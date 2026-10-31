"""Amatista Taller en el servidor (motor 5, add-on 5.0, sql/012).

Con el taller la unidad es la habilidad, no la práctica. El add-on calcula
el modelo del alumno sin conexión (engine/amatista_engine/taller/modelo.py) y
lo sincroniza aquí; el servidor lo vuelve a calcular con el mismo motor a
partir de los intentos, así que lo que queda en Oracle lo decide él:

- GET /habilidades .............. el modelo del alumno {habilidad: Dominio},
                                  lo que ya terminó (proyectos de los talleres
                                  y «reto:<taller>») y la hora del servidor.
- POST /habilidades/intentos .... hasta 50 intentos (también los guardados sin
                                  conexión). Cada uno se vuelve a armar por su
                                  id; si trae la foto de la escena, el servidor
                                  la revisa con la práctica de la actividad y
                                  ese resultado es el que cuenta. Los repetidos
                                  (un reenvío) no cuentan dos veces.
- GET /talleres ................. un resumen por taller (curso, isla).
- GET /talleres/{taller_id} ..... el plan del taller: qué toca ahora, cada
                                  habilidad con su fase y sus próximos repasos.

Estado en HABILIDADES_ALUMNO (columnas de 012) y TALLERES_ALUMNO; el avance
del proyecto sigue en PROGRESO_PRACTICAS (POST /intentos). La columna ESTADO
de HABILIDADES_ALUMNO se sigue llenando a partir de la fase, para las
pantallas de antes. Sin 012 las lecturas responden un estado vacío
(«guardado»: false) y POST /habilidades/intentos responde 503: el add-on
guarda los intentos y los vuelve a mandar.

Al arrancar, sincronizar_habilidades() registra en HABILIDADES las
habilidades de practices/blender/habilidades.json (HABILIDADES_ALUMNO tiene
una llave foránea hacia ella).
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api.addon import copia_verificada, definicion_de, es_equipo, politica_copias, version_para, visible
from api.comun import error_bd
from api.dependencias import sin_progreso, usuario_requerido
from api.limites import limitar
from contenido import motor
from contenido.taller import catalogo, revisar_escena
from database.conexion import obtener_db
from database.modelos import (
    Curso,
    EventoAprendizaje,
    Habilidad,
    HabilidadAlumno,
    Practica,
    ProgresoPractica,
    TallerAlumno,
    Usuario,
    ahora,
)

from amatista_engine.taller.actividades import (
    CONMIGO,
    MIRA,
    PROYECTO,
    PRUEBA,
    REPASO,
    RETO,
    TU,
    actividad_por_id,
    planear,
    practica_de_actividad,
)
from amatista_engine.taller.habilidades import Catalogo
from amatista_engine.taller.modelo import (
    ACOMPANADA,
    DOMINADA,
    NUEVA,
    SOLA,
    Dominio,
    estado_a_dict,
    estado_desde_dict,
    fase,
    marcar_demo,
    registrar_intento,
)
from amatista_engine.taller.repaso import proximas

router = APIRouter(prefix="/api/addon/v1", tags=["taller"])
log = logging.getLogger("amatista.taller")

TIPOS = (MIRA, CONMIGO, TU, PRUEBA, REPASO, PROYECTO, RETO)
MAX_INTENTOS = 50  # por envío
REPETIDO = 0.0005  # s: un intento con «en» menor o igual que el último guardado (± redondeo) es un reenvío
PASADO_MAXIMO = 400 * 86400.0  # s: lo que guarda EVENTOS_APRENDIZAJE (003); un intento más viejo no es válido
MAX_SEGUNDOS = 86400.0
MAX_ISLA = 1000  # caracteres del JSON de la isla de un taller
LARGO_HABILIDAD = 50  # HABILIDADES.ID es VARCHAR2(50)
# La columna ESTADO de antes (sql/005) a partir de la fase del modelo del alumno.
ESTADO_POR_FASE = {NUEVA: "con_guia", ACOMPANADA: "con_guia", SOLA: "con_pistas", DOMINADA: "autonoma"}
# Columnas de 012 y su tope (NUMBER(6), NUMBER(4)…): el número nunca desborda la columna.
TOPES = {"intentos": 999_999, "aciertos": 999_999, "sin_ayuda": 999_999, "caja": 99, "pruebas": 9_999}
RESUMEN = ("taller", "titulo", "curso", "modulo", "listas", "total", "proyecto", "proyecto_abierto", "proyecto_hecho",
           "reto_hecho", "completo", "repasos")

LIMITE_INTENTOS = limitar(60, por="cuenta")  # como POST /intentos
LIMITE_CONSULTAS = limitar(120, por="cuenta")


# --- Ayudantes ----------------------------------------------------------------------


def sin_esquema(error: SQLAlchemyError) -> bool:
    """Falta una tabla o una columna (ORA-00942, ORA-00904 o SQLite): sql/012 todavía no se ejecutó."""
    texto = str(getattr(error, "orig", None) or error).lower()
    return any(marca in texto for marca in ("ora-00942", "ora-00904", "no such table", "no such column",
                                            "has no column named"))


def _compactar(datos: Any) -> str:
    return json.dumps(datos, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _leer_json(texto: Any) -> Any:
    if isinstance(texto, (dict, list)):
        return texto
    try:
        return json.loads(texto) if texto else None
    except (TypeError, ValueError):
        return None


def _fecha(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def estado_legado(d: Dominio) -> str:
    """sin_practicar (sin intentos), con_guia (nueva o acompañada), con_pistas (sola) o autonoma (dominada)."""
    return "sin_practicar" if not d.intentos else ESTADO_POR_FASE[fase(d.p)]


def _dominio_de_fila(fila: HabilidadAlumno) -> Dict[str, Any]:
    return {
        "p": fila.dominio,
        "intentos": int(fila.intentos or 0),
        "aciertos": int(fila.aciertos or 0),
        "sin_ayuda": int(fila.sin_ayuda or 0),
        "ultimo": float(fila.ultimo or 0.0),
        "caja": int(fila.caja or 0),
        "proximo": float(fila.proximo or 0.0),
        "pruebas": int(fila.pruebas or 0),
        "vio_demo": bool(fila.vio_demo),
        "ayuda_max": int(fila.ayuda_max or 0),
    }


def _volcar(fila: HabilidadAlumno, d: Dominio) -> None:
    fila.dominio = round(min(1.0, max(0.0, float(d.p))), 8)
    for campo, tope in TOPES.items():
        setattr(fila, campo, max(0, min(int(getattr(d, campo)), tope)))
    fila.ultimo = round(float(d.ultimo), 3)
    fila.proximo = round(float(d.proximo), 3)
    fila.vio_demo = int(bool(d.vio_demo))
    fila.ayuda_max = max(0, min(int(d.ayuda_max), 4))
    fila.estado = estado_legado(d)


def _parametros(cat: Optional[Catalogo]):
    return {hid: h.bkt for hid, h in cat.habilidades.items()} if cat else {}


# --- Lectura del estado del alumno -------------------------------------------------------


def filas_habilidades(db: Session, usuario_id: str, bloquear: bool = False) -> Dict[str, HabilidadAlumno]:
    """Las filas de HABILIDADES_ALUMNO del alumno (con las columnas de 012: falla sin ellas)."""
    consulta = select(HabilidadAlumno).where(HabilidadAlumno.usuario_id == usuario_id)
    if bloquear:
        consulta = consulta.with_for_update()  # dos envíos a la vez no cuentan el mismo intento dos veces
    return {f.habilidad_id: f for f in db.scalars(consulta)}


def estado_de_filas(filas: Dict[str, HabilidadAlumno], cat: Optional[Catalogo]) -> Dict[str, Dominio]:
    """{habilidad: Dominio} de las filas que ya tienen modelo (DOMINIO no nulo)."""
    crudo = {hid: _dominio_de_fila(f) for hid, f in filas.items() if f.dominio is not None}
    return estado_desde_dict(crudo, _parametros(cat))


def filas_talleres(db: Session, usuario_id: str) -> Dict[str, TallerAlumno]:
    return {f.taller_id: f for f in db.scalars(select(TallerAlumno).where(TallerAlumno.usuario_id == usuario_id))}


def proyectos_hechos(db: Session, usuario_id: str, cat: Optional[Catalogo]) -> List[str]:
    """Los proyectos de los talleres que el alumno ya completó (PROGRESO_PRACTICAS, lo llena POST /intentos)."""
    proyectos = sorted({t.proyecto for t in cat.talleres if t.proyecto}) if cat else []
    if not proyectos:
        return []
    return sorted(db.scalars(
        select(ProgresoPractica.practica_id).where(
            ProgresoPractica.usuario_id == usuario_id,
            ProgresoPractica.completada == 1,
            ProgresoPractica.practica_id.in_(proyectos),
        )
    ))


def completadas_de(proyectos: Iterable[str], talleres: Dict[str, TallerAlumno]) -> List[str]:
    return list(proyectos) + [f"reto:{tid}" for tid, fila in sorted(talleres.items()) if fila.reto_hecho]


@dataclass
class Lectura:
    estado: Dict[str, Dominio] = field(default_factory=dict)
    habilidades: Dict[str, HabilidadAlumno] = field(default_factory=dict)
    talleres: Dict[str, TallerAlumno] = field(default_factory=dict)
    proyectos: List[str] = field(default_factory=list)
    guardado: bool = True  # False: falta sql/012 (el estado es el vacío)

    @property
    def completadas(self) -> List[str]:
        return completadas_de(self.proyectos, self.talleres)

    def isla(self, taller_id: str) -> Dict[str, Any]:
        fila = self.talleres.get(taller_id)
        valor = _leer_json(fila.isla) if fila is not None else None
        return valor if isinstance(valor, dict) else {}


def leer_de(db: Session, usuario: Usuario, cat: Optional[Catalogo], ruta: str) -> Lectura:
    """leer() para quien llama; un administrador (motor 7) siempre empieza de cero."""
    if sin_progreso(usuario):
        return Lectura()
    return leer(db, usuario.id, cat, ruta)


def leer(db: Session, usuario_id: str, cat: Optional[Catalogo], ruta: str) -> Lectura:
    """Todo lo que el taller sabe del alumno. Lo que falta en la base (012, 007) se lee como vacío."""
    lectura = Lectura()
    try:
        lectura.habilidades = filas_habilidades(db, usuario_id)
        lectura.estado = estado_de_filas(lectura.habilidades, cat)
        lectura.talleres = filas_talleres(db, usuario_id)
    except SQLAlchemyError as error:
        db.rollback()
        if not sin_esquema(error):
            raise error_bd(error, ruta)
        lectura = Lectura(guardado=False)
    try:
        lectura.proyectos = proyectos_hechos(db, usuario_id, cat)
    except SQLAlchemyError as error:
        db.rollback()
        if not sin_esquema(error):
            raise error_bd(error, ruta)
    return lectura


def respuesta_estado(lectura: Lectura, momento: float) -> Dict[str, Any]:
    return {
        "estado": estado_a_dict(lectura.estado),
        "completadas": lectura.completadas,
        "ahora": round(momento, 3),
        "guardado": lectura.guardado,
    }


# --- HABILIDADES (el catálogo en la base) ---------------------------------------------------


def _cursos_de_habilidades(cat: Catalogo) -> Dict[str, str]:
    """Cada habilidad pertenece al curso del primer taller que la enseña (o, si no, del que la repasa)."""
    cursos: Dict[str, str] = {}
    for taller in cat.talleres:
        for hid in taller.habilidades:
            cursos.setdefault(hid, taller.curso)
    for taller in cat.talleres:
        for hid in taller.repasa:
            cursos.setdefault(hid, taller.curso)
    return cursos


def sincronizar_habilidades(db: Session, solo_faltantes: bool = False) -> Dict[str, Any]:
    """Registra en HABILIDADES las habilidades del catálogo (sin commit). Devuelve las que quedaron.

    Upsert: nombre = título, descripción = «qué es», orden = el del catálogo
    y el curso del primer taller que la enseña. Con solo_faltantes no toca
    las que ya están (lo usa cada envío de intentos). Una habilidad cuyo
    curso todavía no existe en CURSOS (base sin el contenido importado) se
    salta: HABILIDADES.CURSO_ID es llave foránea.
    """
    resumen: Dict[str, Any] = {"nuevas": [], "actualizadas": [], "sin_curso": [], "registradas": set()}
    cat = catalogo()
    if cat is None:
        return resumen
    cursos = _cursos_de_habilidades(cat)
    por_defecto = cat.talleres[0].curso if cat.talleres else ""
    ids = [hid for hid in cat.habilidades if len(hid) <= LARGO_HABILIDAD]
    largos = [hid for hid in cat.habilidades if len(hid) > LARGO_HABILIDAD]
    if largos:
        log.warning("Habilidades con id de más de %d caracteres (no caben en HABILIDADES): %s",
                    LARGO_HABILIDAD, ", ".join(largos))
    existentes = {f.id: f for f in db.scalars(select(Habilidad).where(Habilidad.id.in_(ids)))} if ids else {}
    if solo_faltantes and len(existentes) == len(ids):
        resumen["registradas"] = set(existentes)
        return resumen
    necesarios = {cursos.get(hid, por_defecto) for hid in ids} - {""}
    cursos_en_base = set(db.scalars(select(Curso.id).where(Curso.id.in_(necesarios)))) if necesarios else set()
    momento = ahora()
    for orden, hid in enumerate(cat.habilidades):
        if hid not in ids:
            continue
        h = cat.habilidades[hid]
        fila = existentes.get(hid)
        if fila is not None and solo_faltantes:
            resumen["registradas"].add(hid)
            continue
        curso = cursos.get(hid, por_defecto)
        if curso not in cursos_en_base:
            if fila is not None:
                resumen["registradas"].add(hid)  # ya estaba (de otro curso): se conserva como está
            else:
                resumen["sin_curso"].append(hid)
            continue
        valores = {"curso_id": curso, "nombre": (h.titulo or hid)[:150], "descripcion": (h.que or None) and h.que[:500],
                   "orden": orden}
        if fila is None:
            db.add(Habilidad(id=hid, actualizado_en=momento, **valores))
            resumen["nuevas"].append(hid)
        elif any(getattr(fila, k) != v for k, v in valores.items()):
            for campo, valor in valores.items():
                setattr(fila, campo, valor)
            fila.actualizado_en = momento
            resumen["actualizadas"].append(hid)
        resumen["registradas"].add(hid)
    db.flush()
    return resumen


def sincronizar_al_arrancar(db: Session) -> Dict[str, Any]:
    """Al arrancar: las habilidades del catálogo en HABILIDADES (con commit)."""
    resumen = sincronizar_habilidades(db)
    db.commit()
    return resumen


# --- Lecturas -------------------------------------------------------------------------------


@router.get("/habilidades", dependencies=[Depends(LIMITE_CONSULTAS)])
def mis_habilidades(db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    """El modelo del alumno: {estado: {habilidad: Dominio}, completadas, ahora, guardado}."""
    momento = time.time()
    lectura = leer_de(db, usuario, catalogo(), "/api/addon/v1/habilidades")
    return respuesta_estado(lectura, momento)


# --- Los intentos -----------------------------------------------------------------------------


class IntentoTaller(BaseModel):
    """Un intento de una actividad del taller, como lo guarda el add-on (también sin conexión).

    Los números se acotan en el servidor (nunca rechazan el envío completo); los
    textos tienen tope. tipo: mira | conmigo | tu | prueba | repaso | proyecto | reto.
    """

    habilidad: str = Field(default="", max_length=60)
    actividad: str = Field(default="", max_length=120)  # «conmigo:bl-mover:baja», «proyecto:blender.bp.m1»
    taller: str = Field(default="", max_length=60)
    tipo: str = Field(default="", max_length=12)
    correcto: bool = False
    ayuda: int = 0  # 0 sin ayuda · 1 qué falta · 2 con qué · 3 cómo (teclas) · 4 mírame
    molde: bool = False
    segundos: float = 0.0
    en: float = 0.0  # epoch del intento (reloj del add-on)
    escena: Optional[Dict[str, Any]] = None  # la foto de la escena, igual que en POST /intentos
    isla: Optional[Dict[str, Any]] = Field(default=None, max_length=12)  # lo que eligió para su pieza de la isla


class IntentosTaller(BaseModel):
    intentos: List[IntentoTaller] = Field(default_factory=list, max_length=MAX_INTENTOS)
    version_addon: Optional[str] = Field(default=None, max_length=20)


@dataclass
class Proceso:
    """Lo que dejó un envío de intentos (todavía sin escribir)."""

    cambios: Dict[str, str] = field(default_factory=dict)  # habilidad -> evidencia
    retos: Set[str] = field(default_factory=set)
    islas: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    eventos: List[EventoAprendizaje] = field(default_factory=list)
    aceptados: int = 0
    repetidos: int = 0
    rechazados: List[Dict[str, Any]] = field(default_factory=list)

    def rechazar(self, indice: int, intento: IntentoTaller, motivo: str) -> None:
        self.rechazados.append({"indice": indice, "actividad": intento.actividad, "motivo": motivo})


_REPOSITORIO: Dict[str, Dict[str, Dict[str, Any]]] = {}


def _definicion_proyecto(db: Session, practica_id: str, usuario: Usuario) -> Optional[Dict[str, Any]]:
    """La práctica del proyecto: la versión que le toca al alumno en PRACTICAS o, si no está, la del repositorio."""
    if not practica_id:
        return None
    try:
        practica = db.get(Practica, practica_id)
        if visible(practica, usuario):
            return definicion_de(db, practica, version_para(practica, usuario))
    except (SQLAlchemyError, HTTPException):
        pass
    carpeta = str(motor.CARPETA_PRACTICAS)
    if carpeta not in _REPOSITORIO:
        _REPOSITORIO[carpeta] = {d.get("id"): d for _, d in motor.practicas_del_repositorio()}
    return _REPOSITORIO[carpeta].get(practica_id)


def _ultimo(d: Optional[Dominio]) -> float:
    return float(d.ultimo) if d is not None and d.ultimo else 0.0


def _evento(usuario: Usuario, taller, actividad, correcto: bool, ayuda: int, molde: bool, segundos: float,
            en: float, revisado: bool, verificada: bool, version_addon: Optional[str], momento: datetime):
    datos = _compactar({"t": taller.id[:40], "a": actividad.id[:80], "k": actividad.tipo, "c": int(correcto),
                        "y": ayuda, "m": int(molde), "s": round(segundos, 1), "r": int(revisado),
                        "ok": int(verificada)})
    return EventoAprendizaje(
        id=str(uuid.uuid4()),
        usuario_id=usuario.id,
        tipo="activity_submitted",
        curso_id=(taller.curso or None) and taller.curso[:50],
        leccion_id=(taller.leccion or None) and taller.leccion[:50],
        ocurrido_en=_fecha(en),
        recibido_en=momento,
        version_app=f"addon {version_addon or ''}".strip()[:20],
        es_prueba=usuario.es_prueba or 0,
        datos=datos if len(datos) <= 250 else None,
    )


def procesar(db: Session, usuario: Usuario, cat: Catalogo, estado: Dict[str, Dominio], cuerpo: IntentosTaller,
             momento: float, registradas: Set[str], verificada: bool) -> Proceso:
    """Aplica los intentos al modelo del alumno, en el orden en que ocurrieron (cambia `estado`)."""
    p = Proceso()
    fecha = ahora()

    def cuando(par):
        en = par[1].en
        return (en if isinstance(en, (int, float)) and math.isfinite(en) else momento, par[0])

    for indice, intento in sorted(enumerate(cuerpo.intentos), key=cuando):
        en = intento.en
        if not math.isfinite(en) or en <= 0 or en < momento - PASADO_MAXIMO:
            p.rechazar(indice, intento, "La fecha del intento («en») no es válida.")
            continue
        en = round(min(en, momento), 3)  # un reloj adelantado no escribe en el futuro
        taller = cat.taller(intento.taller)
        if taller is None:
            p.rechazar(indice, intento, f"No existe el taller «{intento.taller}».")
            continue
        if intento.habilidad and cat.habilidad(intento.habilidad) is None:
            p.rechazar(indice, intento, f"No existe la habilidad «{intento.habilidad}».")
            continue
        if intento.tipo not in TIPOS:
            p.rechazar(indice, intento, f"El tipo «{intento.tipo}» no existe (usa: {', '.join(TIPOS)}).")
            continue
        actividad = actividad_por_id(cat, taller.id, intento.actividad, estado, en)
        if actividad is None:
            p.rechazar(indice, intento, f"No existe la actividad «{intento.actividad}» en el taller «{taller.id}».")
            continue
        if actividad.tipo != intento.tipo:
            p.rechazar(indice, intento, f"La actividad «{actividad.id}» es de tipo «{actividad.tipo}», no «{intento.tipo}».")
            continue
        if intento.habilidad and actividad.habilidad and intento.habilidad != actividad.habilidad:
            p.rechazar(indice, intento, f"La actividad «{actividad.id}» no es de la habilidad «{intento.habilidad}».")
            continue
        ayuda = max(0, min(int(intento.ayuda), 4))
        segundos = max(0.0, min(float(intento.segundos), MAX_SEGUNDOS)) if math.isfinite(intento.segundos) else 0.0
        evidencia = f"Taller {taller.id}: {actividad.id}"[:300]

        if actividad.tipo == MIRA:
            hid = actividad.habilidad
            if hid not in registradas:
                p.rechazar(indice, intento, f"La habilidad «{hid}» no está registrada en el servidor.")
                continue
            if estado.get(hid) is not None and estado[hid].vio_demo:
                p.repetidos += 1
                continue
            estado[hid] = marcar_demo(estado.get(hid), cat.habilidades[hid].bkt)
            p.cambios[hid] = evidencia
            p.aceptados += 1
            p.eventos.append(_evento(usuario, taller, actividad, False, 0, False, segundos, en, False, verificada,
                                     cuerpo.version_addon, fecha))
            continue

        ids = [h for h in actividad.habilidades if h in cat.habilidades and h in registradas]
        if not ids:
            p.rechazar(indice, intento, "Las habilidades de esta actividad no están registradas en el servidor.")
            continue
        nuevas = [h for h in ids if en > _ultimo(estado.get(h)) + REPETIDO]
        if not nuevas:
            p.repetidos += 1  # un reenvío: ya se contó
            continue
        correcto, revisado = bool(intento.correcto), False
        if intento.escena is not None:
            definicion = (_definicion_proyecto(db, taller.proyecto, usuario) if actividad.tipo == PROYECTO
                          else practica_de_actividad(cat, actividad))
            if definicion is not None:
                try:
                    correcto, revisado = revisar_escena(definicion, intento.escena), True
                except (motor.InvalidPracticeError, ValueError, TypeError) as error:
                    p.rechazar(indice, intento, str(error)[:200] or "La escena no se pudo revisar.")
                    continue
        # El molde que dibuja la actividad cuenta aunque el add-on no lo diga (acertar con molde dice menos).
        molde = bool(intento.molde) or actividad.dibujo == "molde"
        for hid in nuevas:
            estado[hid] = registrar_intento(estado.get(hid), cat.habilidades[hid].bkt, correcto, ayuda, en,
                                            molde=molde, tipo=actividad.tipo)
            p.cambios[hid] = evidencia
        if actividad.tipo == RETO and correcto:
            p.retos.add(taller.id)
        if intento.isla and correcto and actividad.tipo in (PROYECTO, RETO):
            p.islas[taller.id] = {**p.islas.get(taller.id, {}), **intento.isla}
        p.aceptados += 1
        p.eventos.append(_evento(usuario, taller, actividad, correcto, ayuda, molde, segundos, en, revisado,
                                 verificada, cuerpo.version_addon, fecha))
    return p


def escribir(db: Session, usuario_id: str, estado: Dict[str, Dominio], lectura: Lectura, p: Proceso) -> None:
    """Guarda lo que cambió (sin commit): HABILIDADES_ALUMNO, TALLERES_ALUMNO y los eventos."""
    momento = ahora()
    for hid, evidencia in p.cambios.items():
        fila = lectura.habilidades.get(hid)
        if fila is None:
            fila = HabilidadAlumno(usuario_id=usuario_id, habilidad_id=hid)
            db.add(fila)
            lectura.habilidades[hid] = fila
        _volcar(fila, estado[hid])
        fila.evidencia = evidencia
        fila.actualizado_en = momento
    for tid in sorted(p.retos | set(p.islas)):
        fila = lectura.talleres.get(tid)
        if fila is None:
            fila = TallerAlumno(usuario_id=usuario_id, taller_id=tid, reto_hecho=0)
            db.add(fila)
            lectura.talleres[tid] = fila
        if tid in p.retos:
            fila.reto_hecho = 1
        if tid in p.islas:
            anterior = _leer_json(fila.isla)
            isla = {**(anterior if isinstance(anterior, dict) else {}), **p.islas[tid]}
            try:
                texto = _compactar(isla)
            except (TypeError, ValueError):
                texto = ""
            if texto and len(texto) <= MAX_ISLA:
                fila.isla = texto
        fila.actualizado_en = momento
    for evento in p.eventos:
        db.add(evento)


@router.post("/habilidades/intentos", dependencies=[Depends(LIMITE_INTENTOS)])
def registrar_intentos(cuerpo: IntentosTaller, request: Request, db: Session = Depends(obtener_db),
                       usuario: Usuario = Depends(usuario_requerido)):
    """Los intentos del taller (hasta 50 por envío). Responde el estado como GET /habilidades más el resumen del envío.

    Un 200 quiere decir que todos los intentos enviados quedaron resueltos
    (aceptados, repetidos o rechazados con su motivo): el add-on los puede
    borrar de su cola. Un 503 (falta sql/012) o un 409 (dos envíos a la vez):
    los guarda y los vuelve a mandar.
    """
    ruta = "/api/addon/v1/habilidades/intentos"
    verificada = copia_verificada(request)
    if not verificada and politica_copias() == "exigir" and not es_equipo(usuario):
        raise HTTPException(
            status_code=403,
            detail="Esta copia de Amatista no es la oficial. Descárgala de nuevo desde la plataforma (Mi Blender).",
        )
    cat = catalogo()
    if cat is None:
        raise HTTPException(status_code=503, detail="El servidor no tiene el catálogo de habilidades del taller.")
    momento = time.time()
    if sin_progreso(usuario):
        # Motor 7: el administrador practica sin progreso; el add-on puede borrar su cola.
        return {**respuesta_estado(Lectura(), momento), "aceptados": 0, "repetidos": 0, "rechazados": [],
                "sin_progreso": True}
    lectura = Lectura()
    try:
        registradas = sincronizar_habilidades(db, solo_faltantes=True)["registradas"]
        lectura.habilidades = filas_habilidades(db, usuario.id, bloquear=True)
        lectura.estado = estado_de_filas(lectura.habilidades, cat)
        lectura.talleres = filas_talleres(db, usuario.id)
    except SQLAlchemyError as error:
        db.rollback()
        if sin_esquema(error):
            raise HTTPException(status_code=503, detail="Falta ejecutar sql/012_taller.sql en Oracle: guarda los "
                                                        "intentos y vuelve a mandarlos más tarde.")
        raise error_bd(error, ruta)
    estado = dict(lectura.estado)
    proceso = procesar(db, usuario, cat, estado, cuerpo, momento, registradas, verificada)
    try:
        escribir(db, usuario.id, estado, lectura, proceso)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Llegaron dos envíos a la vez. Vuelve a mandar los intentos.")
    except SQLAlchemyError as error:
        db.rollback()
        if sin_esquema(error):
            raise HTTPException(status_code=503, detail="Falta ejecutar sql/012_taller.sql en Oracle.")
        raise error_bd(error, ruta)
    lectura.estado = estado
    try:
        lectura.proyectos = proyectos_hechos(db, usuario.id, cat)
    except SQLAlchemyError as error:
        db.rollback()
        if not sin_esquema(error):
            raise error_bd(error, ruta)
    return {
        **respuesta_estado(lectura, momento),
        "aceptados": proceso.aceptados,
        "repetidos": proceso.repetidos,
        "rechazados": proceso.rechazados,
    }


# --- Los talleres -------------------------------------------------------------------------------


def _resumen(plan, isla: Dict[str, Any]) -> Dict[str, Any]:
    datos = plan.a_dict()
    salida = {clave: datos[clave] for clave in RESUMEN}
    salida["leccion"] = plan.taller.leccion
    salida["isla"] = {**datos["isla"], **isla}
    return salida


@router.get("/talleres", dependencies=[Depends(LIMITE_CONSULTAS)])
def mis_talleres(curso: Optional[str] = None, db: Session = Depends(obtener_db),
                 usuario: Usuario = Depends(usuario_requerido)):
    """Un resumen por taller (la página del curso y la isla). ?curso= filtra por curso."""
    momento = time.time()
    cat = catalogo()
    if cat is None:
        return {"talleres": [], "ahora": round(momento, 3), "guardado": False}
    lectura = leer_de(db, usuario, cat, "/api/addon/v1/talleres")
    completadas = lectura.completadas
    talleres = []
    for taller in cat.talleres:
        if curso and taller.curso != curso:
            continue
        plan = planear(cat, taller.id, lectura.estado, momento, completadas)
        if plan is not None:
            talleres.append(_resumen(plan, lectura.isla(taller.id)))
    return {"talleres": talleres, "ahora": round(momento, 3), "guardado": lectura.guardado}


@router.get("/talleres/{taller_id}", dependencies=[Depends(LIMITE_CONSULTAS)])
def un_taller(taller_id: str, elegida: Optional[str] = None, db: Session = Depends(obtener_db),
              usuario: Usuario = Depends(usuario_requerido)):
    """El plan del taller para el alumno (Plan.a_dict() del motor), con próximos repasos y las tarjetas de cada habilidad.

    ?elegida=<habilidad>: la que el alumno eligió practicar (autonomía).
    """
    momento = time.time()
    cat = catalogo()
    taller = cat.taller(taller_id) if cat else None
    if taller is None:
        raise HTTPException(status_code=404, detail=f"No existe el taller «{taller_id[:60]}».")
    lectura = leer_de(db, usuario, cat, f"/api/addon/v1/talleres/{taller.id}")
    plan = planear(cat, taller.id, lectura.estado, momento, lectura.completadas, elegida=(elegida or "")[:60])
    datos = plan.a_dict()
    for tarjeta in datos["habilidades"]:
        h = cat.habilidades.get(tarjeta["id"])
        if h is not None:
            tarjeta.update(que=h.que, teclas=list(h.teclas), herramientas=list(h.herramientas))
    del_taller = set(taller.habilidades) | set(taller.repasa)
    datos["proximos"] = proximas({h: d for h, d in lectura.estado.items() if h in del_taller}, momento)
    datos["isla"] = {**datos["isla"], **lectura.isla(taller.id)}
    datos["leccion"] = taller.leccion
    datos["ahora"] = round(momento, 3)
    datos["guardado"] = lectura.guardado
    return datos
