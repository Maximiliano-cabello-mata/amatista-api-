"""Enlace en vivo plataforma ↔ Blender (motor 3.4, sql/010).

Antes, «Abrir en Blender» solo marcaba la práctica en el servidor: si
Blender ya estaba abierto no pasaba nada y la plataforma no sabía si había
un Blender conectado. Ahora:

- El add-on manda un **latido** (POST /enlace) cada pocos segundos con la
  práctica abierta, el paso, el progreso y si está en modo enfocado. La
  respuesta trae la **orden pendiente** y los **ajustes** del alumno.
- La plataforma ve sus Blender en vivo (GET /enlace) y les deja órdenes
  (POST /ordenes): abrir una práctica, enfocar Blender o mostrar todo y,
  desde el motor 3.5, comprobar, pedir pista, «Hazlo conmigo», guardar,
  empezar de nuevo y ver el ejemplo resuelto o volver de él. Las órdenes
  esperan en una cola corta (una orden nueva no pisa la pendiente). El
  latido trae también lo que muestra el instructor (paso, mensaje y la
  lista de la figura): la lección lo muestra en vivo.
- Motor 5 (Amatista Taller): la lección maneja el taller en Blender
  (taller_abrir, taller_continuar, taller_pausa, taller_pista,
  taller_mirame, taller_probar, taller_repaso e isla) y el latido trae
  detalle.taller: la actividad de ahora, la pieza del molde, la pista y la
  fase de cada habilidad.
- Add-on 5.2: «Conoce Amatista Motor», tres prácticas que explican el add-on
  (en el 5.1 era un solo recorrido). El latido trae siempre detalle.recorrido
  (cuál va, en qué parada y cuáles se terminaron) y la página Blender de la
  plataforma deja la orden «recorrido» con su acción (empezar, siguiente,
  anterior, salir) y la práctica (pestana, vista, taller). Con un recorrido el
  detalle se guarda aunque no haya práctica abierta.
- Motor 6.1: la lección abierta en la plataforma se lo dice a Blender con la
  orden «leccion» (curso, lección, título, clase y su lugar en el módulo) al
  abrirse y cada poco mientras está a la vista. Una orden «leccion» nueva
  reemplaza a la que esperaba (solo importa la última). El latido trae
  detalle.taller.ficha: la herramienta nueva que Blender está presentando.
- «Mi Blender» guarda cómo se ve Blender para el alumno (GET/PUT /ajustes):
  modo enfocado, acompañamiento, avisos. El add-on aplica solo los que el
  alumno eligió; lo que nunca tocó sigue como esté en Blender.

La fila del latido solo se reescribe si cambió algo o cada 15 s, para que
un aula entera no escriba en Oracle cada 5 s. Sin las tablas de 010 todo
responde «sin enlace» y la plataforma sigue como antes.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.addon import obtener_practica
from api.comun import error_bd
from api.contenido import iso
from api.dependencias import PREFIJO_ADDON, usuario_requerido
from api.limites import limitar
from database.conexion import obtener_db
from database.modelos import AddonAjustes, AddonEnlace, Practica, Sesion, Usuario, ahora

router = APIRouter(prefix="/api/addon/v1", tags=["addon"])

INTERVALO = 5  # segundos entre latidos que pide el servidor
EN_LINEA = timedelta(seconds=25)  # sin latido en este tiempo, Blender se da por cerrado
REESCRIBIR = timedelta(seconds=15)  # el latido sin cambios solo actualiza visto_en cada 15 s
VIDA_ORDEN = timedelta(minutes=10)  # una orden que nadie recogió se descarta
MAX_ORDENES = 5  # órdenes en espera por Blender (ADDON_ENLACES.ORDEN es VARCHAR2(1000))
LARGO_ORDEN = 1000
OLVIDAR = timedelta(days=2)  # enlaces sin latido en 2 días se borran

ENFOQUES = ("auto", "siempre", "nunca")
ACOMPANAMIENTOS = ("acompanado", "tarjeta", "silencioso")
AJUSTES_POR_DEFECTO: Dict[str, Any] = {
    "enfoque": "auto",  # auto = enfocado en los niveles 1 y 2
    "acompanamiento": "acompanado",
    "avisos_herramientas": True,
    "tarjeta_3d": True,
}
ORDENES = ("abrir_practica", "enfocar", "ver_todo", "actualizar",
           # Motor 3.5: la plataforma maneja la práctica en Blender.
           "comprobar", "pista", "hazlo_conmigo", "guardar", "reiniciar", "ver_ejemplo", "volver_practica",
           # Motor 5: la plataforma maneja el taller (addon/amatista_blender/taller/sesion.py, ORDENES).
           "taller_abrir", "taller_continuar", "taller_pausa", "taller_pista", "taller_mirame", "taller_probar",
           "taller_repaso", "isla",
           # Add-on 5.1: los primeros pasos en Blender (addon/amatista_blender/recorrido.py).
           "recorrido",
           # Motor 6.1: en qué lección va el alumno en la plataforma (addon/amatista_blender/plataforma.py).
           "leccion")
CLASES_LECCION = ("teoria", "blender", "examen", "proyecto")
# Las órdenes del taller que necesitan saber de qué taller (y de qué habilidad) hablan.
ORDENES_CON_TALLER = ("taller_abrir", "taller_probar", "taller_repaso")
ORDENES_CON_HABILIDAD = ("taller_probar", "taller_repaso")
LIMITE_LATIDO = limitar(40, por="cuenta")  # 12 por minuto con el intervalo normal
LIMITE_ORDENES = limitar(30, por="cuenta")


def _sesion_addon(request: Request, db: Session, usuario: Usuario) -> Sesion:
    sesion = db.get(Sesion, getattr(request.state, "sesion_id", ""))
    if sesion is None or sesion.usuario_id != usuario.id or not (sesion.dispositivo or "").startswith(PREFIJO_ADDON):
        raise HTTPException(status_code=403, detail="El latido solo lo manda el add-on de Blender.")
    return sesion


def _leer(texto: Optional[str]) -> Any:
    if not texto:
        return None
    try:
        return json.loads(texto)
    except ValueError:
        return None


def _compactar(datos: Any) -> str:
    return json.dumps(datos, ensure_ascii=False, separators=(",", ":"))


def ajustes_guardados(db: Session, usuario_id: str) -> Dict[str, Any]:
    """Solo lo que el alumno eligió en «Mi Blender» (sin los valores por defecto)."""
    fila = db.get(AddonAjustes, usuario_id)
    guardados = _leer(fila.datos) if fila else None
    if not isinstance(guardados, dict):
        return {}
    return {k: v for k, v in guardados.items() if k in AJUSTES_POR_DEFECTO}


def ajustes_de(db: Session, usuario_id: str) -> Dict[str, Any]:
    """Lo que muestra la plataforma: lo elegido y, en lo demás, los valores por defecto."""
    return {**AJUSTES_POR_DEFECTO, **ajustes_guardados(db, usuario_id)}


def _sin_tablas(error: SQLAlchemyError) -> bool:
    """ORA-00942 (o «no such table» en SQLite): 010 todavía no se ejecutó."""
    texto = str(getattr(error, "orig", None) or error).lower()
    return "ora-00942" in texto or "no such table" in texto


def _enlace_publico(fila: AddonEnlace, momento, titulos: Dict[str, str]) -> Dict[str, Any]:
    detalle = _leer(fila.detalle) if momento - fila.visto_en <= EN_LINEA else None
    return {
        "id": fila.sesion_id[:16],
        "en_linea": momento - fila.visto_en <= EN_LINEA,
        "visto_en": iso(fila.visto_en),
        "practica_id": fila.practica_id,
        "practica": titulos.get(fila.practica_id or "", fila.practica_id),
        "paso": fila.paso,
        "progreso": fila.progreso,
        "enfocado": bool(fila.enfocado),
        "version_addon": fila.version_addon,
        "version_blender": fila.version_blender,
        "orden_pendiente": (cola_de(fila)[:1] or [{}])[0].get("tipo"),
        "detalle": detalle,
    }


def enlaces_de(db: Session, usuario_id: str) -> List[AddonEnlace]:
    return list(db.scalars(
        select(AddonEnlace).where(AddonEnlace.usuario_id == usuario_id).order_by(AddonEnlace.visto_en.desc())
    ).all())


def cola_de(fila: AddonEnlace) -> List[Dict[str, Any]]:
    """Las órdenes en espera de un Blender, la primera es la que se entrega.

    La columna guarda una lista JSON; una fila de antes (una sola orden) se lee como lista de una.
    """
    valor = _leer(fila.orden)
    if isinstance(valor, dict):
        valor = [valor]
    return [o for o in valor if isinstance(o, dict) and o.get("id")] if isinstance(valor, list) else []


def _guardar_cola(fila: AddonEnlace, cola: List[Dict[str, Any]], momento, cambio_primera: bool) -> None:
    if not cola:
        fila.orden, fila.orden_en = None, None
        return
    fila.orden = _compactar(cola)
    if cambio_primera or fila.orden_en is None:
        fila.orden_en = momento  # la vida de la orden cuenta desde que es la que se entrega


def _encolar(cola: List[Dict[str, Any]], orden: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Agrega la orden sin perder las pendientes.

    «actualizar» no se repite si ya hay uno esperando (releer los ajustes dos veces es lo mismo).
    Si no cabe, se descarta la más vieja que no sea «abrir_practica» (abrir manda sobre lo demás).
    """
    if orden["tipo"] == "actualizar" and any(o.get("tipo") == "actualizar" for o in cola):
        return cola
    if orden["tipo"] == "leccion":  # solo importa la lección de ahora: la nueva reemplaza a la que esperaba
        cola = [o for o in cola if o.get("tipo") != "leccion"]
    cola = cola + [orden]
    while len(cola) > MAX_ORDENES or len(_compactar(cola)) > LARGO_ORDEN:
        sobra = next((i for i, o in enumerate(cola[:-1]) if o.get("tipo") != "abrir_practica"), 0)
        cola.pop(sobra)
    return cola


def dejar_orden(db: Session, usuario_id: str, tipo: str, datos: Optional[Dict[str, Any]] = None) -> int:
    """Deja la orden en cada Blender en línea del alumno. Devuelve a cuántos llegó (0 = ninguno abierto)."""
    momento = ahora()
    orden = {"id": uuid.uuid4().hex[:12], "tipo": tipo, "datos": datos or {}}
    entregadas = 0
    for fila in enlaces_de(db, usuario_id):
        if momento - fila.visto_en <= EN_LINEA:
            antes = cola_de(fila)
            cola = _encolar(antes, orden)
            _guardar_cola(fila, cola, momento, (antes[:1] or [None])[0] != cola[0])
            entregadas += 1
    return entregadas


def ordenar_abrir(db: Session, usuario_id: str, practica_id: str) -> int:
    """«Abrir en Blender» de la lección: si hay un Blender abierto, la práctica se abre ahí sola.

    No falla si 010 no se ejecutó todavía: entonces Blender la abre al arrancar, como antes.
    """
    try:
        return dejar_orden(db, usuario_id, "abrir_practica", {"practica_id": practica_id})
    except SQLAlchemyError as error:
        if _sin_tablas(error):
            db.rollback()
            return 0
        raise


# --- El add-on -------------------------------------------------------------------


class ItemLista(BaseModel):
    texto: str = Field(max_length=60)
    ok: bool = False
    estado: str = Field(default="", max_length=20)
    consejo: str = Field(default="", max_length=400)
    aspecto: str = Field(default="", max_length=40)  # la figura, materiales, luces… (el ejemplo resuelto)


class PasoMision(BaseModel):
    """Un micro paso de la misión de ahora (motor 4): texto y sus teclas."""

    texto: str = Field(default="", max_length=160)
    teclas: List[str] = Field(default_factory=list, max_length=6)


class ActividadVivo(BaseModel):
    """La actividad del taller que el alumno tiene abierta (motor 5)."""

    id: str = Field(default="", max_length=120)  # «conmigo:bl-mover:baja»
    tipo: str = Field(default="", max_length=12)  # mira | conmigo | tu | prueba | repaso | proyecto | reto
    nombre_tipo: str = Field(default="", max_length=30)  # «Hazlo conmigo»
    titulo: str = Field(default="", max_length=80)
    pide: str = Field(default="", max_length=300)
    habilidad: str = Field(default="", max_length=60)
    numero: int = Field(default=0, ge=0, le=99)
    de: int = Field(default=0, ge=0, le=99)
    dibujo: str = Field(default="", max_length=10)  # molde | marca | libre
    molde: bool = False


class PiezaVivo(BaseModel):
    nombre: str = Field(default="", max_length=60)
    estado: str = Field(default="", max_length=12)  # falta | cerca | lista
    mensaje: str = Field(default="", max_length=200)


class PistaVivo(BaseModel):
    nivel: int = Field(default=0, ge=0, le=9)  # 1 qué falta · 2 con qué · 3 cómo · 4 mírame
    titulo: str = Field(default="", max_length=40)
    texto: str = Field(default="", max_length=300)
    teclas: List[str] = Field(default_factory=list, max_length=6)


class HabilidadVivo(BaseModel):
    id: str = Field(default="", max_length=60)
    titulo: str = Field(default="", max_length=60)
    p: float = Field(default=0.0, ge=0.0, le=1.0)  # dominio
    fase: str = Field(default="", max_length=12)  # nueva | acompanada | sola | dominada


class FichaVivo(BaseModel):
    id: str = Field(default="", max_length=40)
    nombre: str = Field(default="", max_length=60)


class TallerVivo(BaseModel):
    """detalle.taller del latido (motor 5): lo que la lección muestra del taller en vivo."""

    taller: str = Field(default="", max_length=60)
    actividad: ActividadVivo = Field(default_factory=ActividadVivo)
    pieza: Optional[PiezaVivo] = None  # la pieza del molde que toca
    llenas: int = Field(default=0, ge=0, le=99)
    total: int = Field(default=0, ge=0, le=99)
    mensaje: str = Field(default="", max_length=300)
    pista: Optional[PistaVivo] = None
    estado: str = Field(default="", max_length=12)  # mirando | trabajando | lograda | pausa | presentando
    habilidades: List[HabilidadVivo] = Field(default_factory=list, max_length=12)
    proyecto_abierto: bool = False
    repasos: List[str] = Field(default_factory=list, max_length=12)
    # Motor 6: la etapa que mandó la lección y si ya se cumplió (la lección se da por hecha).
    etapa: Literal["", "exploracion", "practica"] = ""
    etapa_cumplida: bool = False
    # Motor 6.1: la herramienta nueva que Blender está presentando con su ficha.
    ficha: Optional[FichaVivo] = None


class ParadaVivo(BaseModel):
    titulo: str = Field(default="", max_length=60)
    ok: bool = False


RECORRIDOS = ("pestana", "vista", "taller")  # addon/amatista_blender/recorrido_pasos.py › ORDEN


class RecorridoVivo(BaseModel):
    """detalle.recorrido del latido: desde el add-on 5.2, las tres prácticas de «Conoce Amatista Motor»
    (cuál va, en qué parada y cuáles se terminaron). El 5.1 mandaba un solo recorrido sin id."""

    id: str = Field(default="", max_length=20)
    nombre: str = Field(default="", max_length=60)
    hechos: List[Literal["pestana", "vista", "taller"]] = Field(default_factory=list, max_length=3)
    activo: bool = False
    hecho: bool = False
    paso: int = Field(default=0, ge=0, le=20)
    total: int = Field(default=0, ge=0, le=20)
    titulo: str = Field(default="", max_length=80)
    texto: str = Field(default="", max_length=300)
    hacer: str = Field(default="", max_length=160)
    teclas: List[str] = Field(default_factory=list, max_length=6)
    pasos: List[ParadaVivo] = Field(default_factory=list, max_length=12)


class Detalle(BaseModel):
    """Lo que el instructor muestra en Blender ahora (motor 3.5; motor 4: la misión de la ruta; motor 5: el taller)."""

    titulo: str = Field(default="", max_length=120)  # el paso actual
    mensaje: str = Field(default="", max_length=500)  # lo que dice el instructor
    numero: int = Field(default=0, ge=0, le=99)
    total: int = Field(default=0, ge=0, le=99)
    figura: str = Field(default="", max_length=120)  # título de la lista («Forja la silueta…»)
    lista: List[ItemLista] = Field(default_factory=list, max_length=16)
    modo: str = Field(default="", max_length=20)  # OBJECT, EDIT_MESH…
    pistas: int = Field(default=0, ge=0, le=9)  # pistas que quedan en el paso
    accion: str = Field(default="", max_length=80)  # el texto del «Hazlo conmigo» si hay
    completada: bool = False
    # Motor 4: la ruta del alumno (la misma misión que ve en Blender).
    parte: str = Field(default="", max_length=60)  # «Las ruedas»
    hechas: int = Field(default=0, ge=0, le=99)  # misiones cumplidas
    objetivo: str = Field(default="", max_length=300)  # qué hacer, en una frase
    pasos: List[PasoMision] = Field(default_factory=list, max_length=3)
    taller: Optional[TallerVivo] = None  # motor 5: la actividad del taller (sin práctica de antes)
    recorrido: Optional[RecorridoVivo] = None  # add-on 5.1: los primeros pasos


class Latido(BaseModel):
    practica_id: Optional[str] = Field(default=None, max_length=80)
    paso: Optional[str] = Field(default=None, max_length=80)
    progreso: Optional[int] = Field(default=None, ge=0, le=100)
    enfocado: bool = False
    version_addon: Optional[str] = Field(default=None, max_length=20)
    version_blender: Optional[str] = Field(default=None, max_length=20)
    orden_hecha: Optional[str] = Field(default=None, max_length=12)  # id de la orden que ya cumplió
    detalle: Optional[Detalle] = None  # motor 3.5: lo que muestra el instructor


@router.post("/enlace", dependencies=[Depends(LIMITE_LATIDO)])
def latido(cuerpo: Latido, request: Request, db: Session = Depends(obtener_db),
           usuario: Usuario = Depends(usuario_requerido)):
    """El latido del add-on: «sigo abierto, voy aquí». Responde la orden pendiente y los ajustes."""
    sesion = _sesion_addon(request, db, usuario)
    momento = ahora()
    try:
        fila = db.get(AddonEnlace, sesion.id)
        nuevo = fila is None
        if nuevo:
            fila = AddonEnlace(sesion_id=sesion.id, usuario_id=usuario.id, visto_en=momento)
            db.add(fila)
        estado = (cuerpo.practica_id, cuerpo.paso, cuerpo.progreso, int(cuerpo.enfocado), cuerpo.version_addon,
                  cuerpo.version_blender)
        antes = (fila.practica_id, fila.paso, fila.progreso, fila.enfocado, fila.version_addon, fila.version_blender)
        cola = cola_de(fila)
        if cola and fila.orden_en and momento - fila.orden_en > VIDA_ORDEN:
            cola = []  # nadie las recogió en 10 minutos: se descartan
        elif cuerpo.orden_hecha:
            cola = [o for o in cola if o.get("id") != cuerpo.orden_hecha]
        if cola != cola_de(fila):
            _guardar_cola(fila, cola, momento, True)
        orden = cola[0] if cola else None
        guardar = cuerpo.detalle is not None and (cuerpo.practica_id or cuerpo.detalle.recorrido is not None)
        detalle = _compactar(cuerpo.detalle.model_dump()) if guardar else None
        if fila.detalle != detalle:
            fila.detalle = detalle
        cambio = nuevo or estado != antes or db.is_modified(fila)
        if cambio or momento - fila.visto_en > REESCRIBIR:
            (fila.practica_id, fila.paso, fila.progreso, fila.enfocado, fila.version_addon,
             fila.version_blender) = estado
            fila.visto_en = momento
            db.commit()
        # Solo lo que el alumno eligió en «Mi Blender»: lo demás lo deciden las preferencias de Blender.
        ajustes = ajustes_guardados(db, usuario.id)
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            return {"enlace": False, "intervalo": 60, "orden": None, "ajustes": {}}
        raise error_bd(error, "/api/addon/v1/enlace")
    return {"enlace": True, "intervalo": INTERVALO, "orden": orden, "ajustes": ajustes}


# --- La plataforma ------------------------------------------------------------------


@router.get("/enlace")
def ver_enlace(db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    """Los Blender del alumno: si están abiertos, qué practican y en qué paso van."""
    momento = ahora()
    try:
        filas = enlaces_de(db, usuario.id)
        viejas = [f for f in filas if momento - f.visto_en > OLVIDAR]
        for fila in viejas:
            db.delete(fila)
        if viejas:
            db.commit()
        filas = [f for f in filas if f not in viejas]
        ids = {f.practica_id for f in filas if f.practica_id}
        titulos = {p.id: p.titulo for p in db.scalars(select(Practica).where(Practica.id.in_(ids))).all()} if ids else {}
        blender = [_enlace_publico(f, momento, titulos) for f in filas]
        ajustes = ajustes_de(db, usuario.id)
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            return {"enlace": False, "en_linea": False, "blender": [], "ajustes": dict(AJUSTES_POR_DEFECTO)}
        raise error_bd(error, "/api/addon/v1/enlace")
    return {"enlace": True, "en_linea": any(b["en_linea"] for b in blender), "blender": blender, "ajustes": ajustes}


class Orden(BaseModel):
    tipo: Literal["abrir_practica", "enfocar", "ver_todo", "actualizar", "comprobar", "pista", "hazlo_conmigo",
                  "guardar", "reiniciar", "ver_ejemplo", "volver_practica",
                  "taller_abrir", "taller_continuar", "taller_pausa", "taller_pista", "taller_mirame",
                  "taller_probar", "taller_repaso", "isla", "recorrido", "leccion"]
    practica_id: Optional[str] = Field(default=None, max_length=80)
    confirmar: bool = False  # «reiniciar» lo exige: la plataforma pregunta antes
    # Motor 5: de qué taller, actividad o habilidad habla una orden del taller.
    taller: Optional[str] = Field(default=None, max_length=60)
    actividad: Optional[str] = Field(default=None, max_length=120)
    habilidad: Optional[str] = Field(default=None, max_length=60)
    # Add-on 5.1: qué hacer con los primeros pasos (orden «recorrido»); desde el 5.2, de qué práctica de
    # «Conoce Amatista Motor» habla (sin ella, «empezar» abre la primera sin terminar).
    accion: Optional[Literal["empezar", "siguiente", "anterior", "salir"]] = None
    recorrido: Optional[Literal["pestana", "vista", "taller"]] = None
    # Motor 6: la lección marca la etapa del taller y Blender no sale de ella
    # (engine/amatista_engine/taller/etapas.py). leccion: a dónde vuelve «Volver a la plataforma».
    etapa: Optional[Literal["exploracion", "practica"]] = None
    habilidades: Optional[List[str]] = Field(default=None, max_length=4)
    leccion: Optional[str] = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    # Motor 6.1 (orden «leccion»): la lección abierta en la plataforma, para que Blender sepa dónde va el alumno.
    curso: Optional[str] = Field(default=None, max_length=60, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
    titulo: Optional[str] = Field(default=None, max_length=120)
    clase: Optional[Literal["teoria", "blender", "examen", "proyecto"]] = None
    numero: Optional[int] = Field(default=None, ge=0, le=99)
    total: Optional[int] = Field(default=None, ge=0, le=99)


def _datos_taller(cuerpo: Orden) -> Dict[str, Any]:
    """Los datos de una orden del taller, revisados contra el catálogo (practices/blender/habilidades.json)."""
    from contenido.taller import catalogo

    datos: Dict[str, Any] = {}
    if cuerpo.tipo not in ORDENES_CON_TALLER:
        return datos
    cat = catalogo()
    taller = cat.taller(cuerpo.taller or "") if cat else None
    if taller is None:
        raise HTTPException(status_code=404, detail=f"No existe el taller «{(cuerpo.taller or '')[:60]}».")
    datos["taller"] = taller.id
    if cuerpo.tipo in ORDENES_CON_HABILIDAD:
        visibles = set(taller.habilidades) | set(taller.repasa)
        if (cuerpo.habilidad or "") not in visibles:
            raise HTTPException(status_code=404,
                                detail=f"La habilidad «{(cuerpo.habilidad or '')[:60]}» no es de este taller.")
        datos["habilidad"] = cuerpo.habilidad
    if cuerpo.tipo == "taller_abrir" and cuerpo.actividad:
        datos["actividad"] = cuerpo.actividad  # Blender la vuelve a armar y, si no existe, abre lo que toca
    if cuerpo.tipo == "taller_abrir" and cuerpo.etapa:
        datos["etapa"] = cuerpo.etapa
        if cuerpo.etapa == "exploracion":
            # Solo habilidades de este taller: Blender vuelve a revisarlas (etapas.etapa_de).
            datos["habilidades"] = [h for h in (cuerpo.habilidades or []) if h in taller.habilidades][:4]
    if cuerpo.tipo == "taller_abrir" and cuerpo.leccion:
        datos["leccion"] = cuerpo.leccion
    return datos


def _rechazar_addon(request: Request, db: Session, mensaje: str) -> None:
    """Las órdenes y los ajustes salen de la plataforma, nunca de un token del add-on."""
    sesion = db.get(Sesion, getattr(request.state, "sesion_id", ""))
    if sesion is not None and (sesion.dispositivo or "").startswith(PREFIJO_ADDON):
        raise HTTPException(status_code=403, detail=mensaje)


@router.post("/ordenes", dependencies=[Depends(LIMITE_ORDENES)])
def nueva_orden(cuerpo: Orden, request: Request, db: Session = Depends(obtener_db),
                usuario: Usuario = Depends(usuario_requerido)):
    """La plataforma le pide algo al Blender abierto del alumno."""
    _rechazar_addon(request, db, "Las órdenes a Blender se dan desde la plataforma.")
    datos: Dict[str, Any] = {}
    if cuerpo.tipo == "reiniciar" and not cuerpo.confirmar:
        raise HTTPException(status_code=400, detail="Empezar de nuevo necesita confirmación.")
    if cuerpo.practica_id and cuerpo.tipo != "abrir_practica":
        datos["practica_id"] = cuerpo.practica_id  # Blender solo la cumple si sigue en esa práctica
    if cuerpo.tipo == "abrir_practica":
        # Como al descargarla: un alumno solo puede abrir prácticas publicadas.
        practica = obtener_practica(db, cuerpo.practica_id or "", usuario)
        datos["practica_id"] = practica.id
    datos.update(_datos_taller(cuerpo))
    if cuerpo.tipo == "leccion":
        if not cuerpo.leccion or not cuerpo.curso:
            raise HTTPException(status_code=400, detail="La orden «leccion» necesita «curso» y «leccion».")
        datos = {"curso": cuerpo.curso, "leccion": cuerpo.leccion, "titulo": (cuerpo.titulo or "").strip()[:120],
                 "clase": cuerpo.clase or "teoria", "numero": cuerpo.numero or 0, "total": cuerpo.total or 0}
    if cuerpo.tipo == "recorrido":
        datos["accion"] = cuerpo.accion or "empezar"
        if cuerpo.recorrido:
            datos["recorrido"] = cuerpo.recorrido
    try:
        entregadas = dejar_orden(db, usuario.id, cuerpo.tipo, datos)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            return {"enlace": False, "entregada": False}
        raise error_bd(error, "/api/addon/v1/ordenes")
    return {"enlace": True, "entregada": entregadas > 0, "blender": entregadas}


class Ajustes(BaseModel):
    enfoque: Optional[Literal["auto", "siempre", "nunca"]] = None
    acompanamiento: Optional[Literal["acompanado", "tarjeta", "silencioso"]] = None
    avisos_herramientas: Optional[bool] = None
    tarjeta_3d: Optional[bool] = None


@router.get("/ajustes")
def leer_ajustes(db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    try:
        return {"enlace": True, "ajustes": ajustes_de(db, usuario.id)}
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            return {"enlace": False, "ajustes": dict(AJUSTES_POR_DEFECTO)}
        raise error_bd(error, "/api/addon/v1/ajustes")


@router.put("/ajustes", dependencies=[Depends(LIMITE_ORDENES)])
def guardar_ajustes(cuerpo: Ajustes, request: Request, db: Session = Depends(obtener_db),
                    usuario: Usuario = Depends(usuario_requerido)):
    """«Mi Blender»: cómo se ve Blender para este alumno. Llega al add-on en su siguiente latido."""
    _rechazar_addon(request, db, "Los ajustes de Blender se cambian desde la plataforma.")
    try:
        # Se guarda solo lo elegido: lo que el alumno nunca tocó no pisa las preferencias de Blender.
        elegidos = {**ajustes_guardados(db, usuario.id), **cuerpo.model_dump(exclude_none=True)}
        nuevos = {**AJUSTES_POR_DEFECTO, **elegidos}
        fila = db.get(AddonAjustes, usuario.id)
        if fila is None:
            db.add(AddonAjustes(usuario_id=usuario.id, datos=_compactar(elegidos), actualizado_en=ahora()))
        else:
            fila.datos = _compactar(elegidos)
            fila.actualizado_en = ahora()
        dejar_orden(db, usuario.id, "actualizar")
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            raise HTTPException(status_code=503, detail="Falta ejecutar sql/010_enlace_blender.sql en Oracle.")
        raise error_bd(error, "/api/addon/v1/ajustes")
    return {"enlace": True, "ajustes": nuevos}
