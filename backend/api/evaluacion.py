"""Panel del profesor evaluador y «Mi grupo» del alumno (sql/014).

El profesor no es alumno, administrador ni desarrollador: evalúa. Arma sus
grupos (los alumnos entran con un código), les deja prácticas de Blender y
revisa lo que entrega cada uno: el progreso que midió el motor y el modelo,
que vuelve a armar en su navegador.

El modelo NUNCA se guarda aquí para siempre. El add-on manda una descripción
ligera (contenido/modelo_ligero.py, unos KB); el servidor la guarda solo
hasta que el profesor la recoge. Desde ese momento vive en la computadora
del profesor (IndexedDB) y aquí se borra. La que nadie recoge se borra a los
DIAS_MODELO días.

Profesor (rol profesor o admin; cada quien ve solo sus grupos):
- GET   /api/evaluacion/grupos ............................. sus grupos con conteos.
- POST  /api/evaluacion/grupos ............................. crea uno {nombre}; trae su código.
- GET   /api/evaluacion/grupos/{id} ........................ alumnos y prácticas del grupo.
- PATCH /api/evaluacion/grupos/{id} ........................ {nombre?, archivado?}.
- DELETE /api/evaluacion/grupos/{id}/alumnos/{alumno} ...... saca a un alumno del grupo.
- GET   /api/evaluacion/practicas .......................... prácticas publicadas para asignar.
- POST  /api/evaluacion/asignaciones ....................... deja una práctica a un grupo.
- PATCH /api/evaluacion/asignaciones/{id} .................. {estado?, indicaciones?, fecha_limite?}.
- GET   /api/evaluacion/asignaciones/{id} .................. una fila por alumno, con su entrega
                                                             o, si no entregó, su avance en vivo.
- GET   /api/evaluacion/asignaciones/{id}/entregas/{alumno}/modelo
- POST  /api/evaluacion/asignaciones/{id}/entregas/{alumno}/modelo/recibido
                                                             el navegador ya lo guardó: se borra aquí.
- POST  /api/evaluacion/asignaciones/{id}/entregas/{alumno}/evaluar
                                                             {estado: evaluada|rehacer, calificacion?, comentario?}.

Alumno (cuenta con correo):
- POST   /api/evaluacion/unirme ............................ {codigo}.
- GET    /api/evaluacion/mis-asignaciones .................. sus grupos y prácticas, con su entrega.
- POST   /api/evaluacion/mis-asignaciones/{id}/vista ....... ya leyó la evaluación.
- DELETE /api/evaluacion/mis-grupos/{id} ................... sale de un grupo.
- POST   /api/evaluacion/entregas .......................... entrega {asignacion_id, modelo?}
                                                             (lo manda el add-on de Blender).

Sin 014 todo responde 503 con un mensaje claro.
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, defer

from api.comun import error_bd
from api.dependencias import es_cuenta_registrada, requiere_rol, usuario_requerido
from api.limites import limitar
from api.perfil import sin_esquema
from contenido import modelo_ligero
from database.conexion import obtener_db
from database.modelos import (
    Asignacion,
    Entrega,
    Grupo,
    GrupoAlumno,
    Practica,
    ProgresoPractica,
    Usuario,
    ahora,
)

router = APIRouter(prefix="/api/evaluacion", tags=["evaluacion"])

profesor_requerido = requiere_rol("profesor", "admin")

SIN_014 = "El panel del profesor todavía no está listo en el servidor (falta sql/014_panel_del_profesor.sql)."
DIAS_MODELO = 30
MAX_GRUPOS = 40
MAX_ALUMNOS = 200
# Sin 0/O ni 1/I/L: el código se dicta en voz alta en el salón.
LETRAS_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

LIMITE_UNIRSE = limitar(10, ventana=600, por="cuenta")
LIMITE_ENTREGAS = limitar(30, ventana=3600, por="cuenta")
LIMITE_PROFESOR = limitar(120, ventana=60, por="cuenta")


def _fallo(db: Session, error: SQLAlchemyError, ruta: str) -> HTTPException:
    db.rollback()
    if sin_esquema(error):
        return HTTPException(status_code=503, detail=SIN_014)
    return error_bd(error, ruta)


def iso(momento: Optional[datetime]) -> Optional[str]:
    return momento.isoformat() if momento else None


def _purgar_modelos(db: Session) -> None:
    """Los modelos que nadie recogió en DIAS_MODELO días se borran (la fila queda)."""
    db.execute(
        update(Entrega)
        .where(Entrega.modelo.is_not(None), Entrega.entregada_en < ahora() - timedelta(days=DIAS_MODELO))
        .values(modelo=None, modelo_bytes=0)
    )


def _nuevo_codigo(db: Session) -> str:
    for _ in range(20):
        codigo = "AMA-" + "".join(secrets.choice(LETRAS_CODIGO) for _ in range(4))
        if db.scalar(select(Grupo.id).where(Grupo.codigo == codigo)) is None:
            return codigo
    raise HTTPException(status_code=503, detail="No se pudo generar un código: inténtalo otra vez.")


def _normalizar_codigo(valor: str) -> str:
    limpio = "".join(c for c in valor.upper() if c.isalnum())
    if limpio.startswith("AMA"):
        limpio = limpio[3:]
    return f"AMA-{limpio}"


def _cuenta_con_correo(usuario: Usuario = Depends(usuario_requerido)) -> Usuario:
    if not es_cuenta_registrada(usuario):
        raise HTTPException(status_code=401, detail="Entra con tu cuenta para unirte al grupo de tu profesor.")
    return usuario


def _grupo_propio(db: Session, grupo_id: str, profesor: Usuario) -> Grupo:
    grupo = db.get(Grupo, grupo_id)
    if grupo is None or grupo.profesor_id != profesor.id:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo entre los tuyos.")
    return grupo


def _asignacion_propia(db: Session, asignacion_id: str, profesor: Usuario) -> Asignacion:
    asignacion = db.get(Asignacion, asignacion_id)
    if asignacion is None or asignacion.profesor_id != profesor.id:
        raise HTTPException(status_code=404, detail="No encontramos esa práctica entre las que dejaste.")
    return asignacion


def _resumen(texto: Optional[str]) -> Optional[dict]:
    if not texto:
        return None
    try:
        return json.loads(texto)
    except ValueError:
        return None


def _entrega(e: Entrega, asignacion: Asignacion) -> dict:
    return {
        "estado": e.estado,
        "progreso": e.progreso,
        "completada": bool(e.completada),
        "intentos": e.intentos,
        "resumen": _resumen(e.resumen),
        "entregada_en": iso(e.entregada_en),
        "tarde": bool(asignacion.fecha_limite and e.entregada_en and e.entregada_en > asignacion.fecha_limite),
        # Sin leer el CLOB: la purga deja modelo_bytes en 0 y recogerlo marca recogido_en.
        "modelo_en_servidor": e.modelo_bytes > 0 and e.recogido_en is None,
        "modelo_bytes": e.modelo_bytes,
        "recogido_en": iso(e.recogido_en),
        "calificacion": e.calificacion,
        "comentario": e.comentario,
        "evaluada_en": iso(e.evaluada_en),
    }


def _asignacion(a: Asignacion, practica: Optional[Practica], extra: Optional[dict] = None) -> dict:
    datos = {
        "id": a.id,
        "grupo_id": a.grupo_id,
        "practica_id": a.practica_id,
        "practica": practica.titulo if practica else a.practica_id,
        "curso_id": practica.curso_id if practica else None,
        "leccion_id": practica.leccion_id if practica else None,
        "nivel": practica.nivel if practica else None,
        "titulo": a.titulo,
        "indicaciones": a.indicaciones,
        "fecha_limite": iso(a.fecha_limite),
        "estado": a.estado,
        "creada_en": iso(a.creada_en),
    }
    if extra:
        datos.update(extra)
    return datos


def _practicas(db: Session, ids) -> Dict[str, Practica]:
    ids = set(ids)
    if not ids:
        return {}
    filas = db.scalars(select(Practica).options(defer(Practica.definicion)).where(Practica.id.in_(ids)))
    return {p.id: p for p in filas}


# --- Solicitudes -------------------------------------------------------------------


class SolicitudGrupo(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)

    @field_validator("nombre")
    @classmethod
    def _limpio(cls, valor: str) -> str:
        valor = " ".join(valor.split())
        if len(valor) < 2:
            raise ValueError("Ponle un nombre al grupo (por ejemplo «3.º B · tarde»).")
        return valor


class CambioGrupo(BaseModel):
    nombre: Optional[str] = Field(default=None, min_length=2, max_length=120)
    archivado: Optional[bool] = None


class SolicitudAsignacion(BaseModel):
    grupo_id: str = Field(max_length=40)
    practica_id: str = Field(max_length=80)
    titulo: Optional[str] = Field(default=None, max_length=200)
    indicaciones: Optional[str] = Field(default=None, max_length=1000)
    fecha_limite: Optional[datetime] = None


class CambioAsignacion(BaseModel):
    estado: Optional[Literal["abierta", "cerrada"]] = None
    indicaciones: Optional[str] = Field(default=None, max_length=1000)
    fecha_limite: Optional[datetime] = None
    quitar_fecha: bool = False


class SolicitudEvaluacion(BaseModel):
    estado: Literal["evaluada", "rehacer"]
    calificacion: Optional[int] = Field(default=None, ge=0, le=10)
    comentario: Optional[str] = Field(default=None, max_length=1000)


class SolicitudUnirse(BaseModel):
    codigo: str = Field(min_length=4, max_length=20)


class SolicitudEntrega(BaseModel):
    asignacion_id: str = Field(max_length=40)
    modelo: Optional[str] = Field(default=None, max_length=modelo_ligero.MAX_BASE64)
    version_addon: Optional[str] = Field(default=None, max_length=20)


def _sin_zona(momento: Optional[datetime]) -> Optional[datetime]:
    if momento is None or momento.tzinfo is None:
        return momento
    return momento.astimezone(timezone.utc).replace(tzinfo=None)


# --- Profesor: grupos -------------------------------------------------------------


@router.get("/grupos", dependencies=[Depends(LIMITE_PROFESOR)])
def mis_grupos(db: Session = Depends(obtener_db), profesor: Usuario = Depends(profesor_requerido)):
    try:
        _purgar_modelos(db)
        db.commit()
        grupos = list(db.scalars(select(Grupo).where(Grupo.profesor_id == profesor.id).order_by(Grupo.creado_en.desc())))
        ids = [g.id for g in grupos]
        alumnos = dict(
            db.execute(
                select(GrupoAlumno.grupo_id, func.count()).where(GrupoAlumno.grupo_id.in_(ids)).group_by(GrupoAlumno.grupo_id)
            ).all()
        ) if ids else {}
        practicas = dict(
            db.execute(
                select(Asignacion.grupo_id, func.count()).where(Asignacion.grupo_id.in_(ids)).group_by(Asignacion.grupo_id)
            ).all()
        ) if ids else {}
        # Entregas que esperan al profesor (aún sin evaluar), por grupo.
        pendientes = dict(
            db.execute(
                select(Asignacion.grupo_id, func.count())
                .join(Entrega, Entrega.asignacion_id == Asignacion.id)
                .where(Asignacion.grupo_id.in_(ids), Entrega.estado == "entregada")
                .group_by(Asignacion.grupo_id)
            ).all()
        ) if ids else {}
    except SQLAlchemyError as error:
        raise _fallo(db, error, "GET /api/evaluacion/grupos")
    return [
        {
            "id": g.id,
            "nombre": g.nombre,
            "codigo": g.codigo,
            "archivado": bool(g.archivado),
            "creado_en": iso(g.creado_en),
            "alumnos": alumnos.get(g.id, 0),
            "practicas": practicas.get(g.id, 0),
            "por_evaluar": pendientes.get(g.id, 0),
        }
        for g in grupos
    ]


@router.post("/grupos", status_code=201, dependencies=[Depends(LIMITE_PROFESOR)])
def crear_grupo(cuerpo: SolicitudGrupo, db: Session = Depends(obtener_db), profesor: Usuario = Depends(profesor_requerido)):
    try:
        total = db.scalar(select(func.count()).select_from(Grupo).where(Grupo.profesor_id == profesor.id, Grupo.archivado == 0))
        if total >= MAX_GRUPOS:
            raise HTTPException(status_code=409, detail=f"Ya tienes {MAX_GRUPOS} grupos activos: archiva alguno antes.")
        grupo = Grupo(id=uuid.uuid4().hex, profesor_id=profesor.id, nombre=cuerpo.nombre, codigo=_nuevo_codigo(db),
                      archivado=0, creado_en=ahora())
        db.add(grupo)
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "POST /api/evaluacion/grupos")
    return {"id": grupo.id, "nombre": grupo.nombre, "codigo": grupo.codigo, "archivado": False,
            "creado_en": iso(grupo.creado_en), "alumnos": 0, "practicas": 0, "por_evaluar": 0}


@router.patch("/grupos/{grupo_id}", dependencies=[Depends(LIMITE_PROFESOR)])
def cambiar_grupo(grupo_id: str, cuerpo: CambioGrupo, db: Session = Depends(obtener_db),
                  profesor: Usuario = Depends(profesor_requerido)):
    try:
        grupo = _grupo_propio(db, grupo_id, profesor)
        if cuerpo.nombre is not None:
            grupo.nombre = SolicitudGrupo(nombre=cuerpo.nombre).nombre
        if cuerpo.archivado is not None:
            grupo.archivado = 1 if cuerpo.archivado else 0
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "PATCH /api/evaluacion/grupos")
    return {"id": grupo.id, "nombre": grupo.nombre, "codigo": grupo.codigo, "archivado": bool(grupo.archivado)}


@router.get("/grupos/{grupo_id}", dependencies=[Depends(LIMITE_PROFESOR)])
def ver_grupo(grupo_id: str, db: Session = Depends(obtener_db), profesor: Usuario = Depends(profesor_requerido)):
    try:
        grupo = _grupo_propio(db, grupo_id, profesor)
        filas = db.execute(
            select(Usuario.id, Usuario.nombre, Usuario.email, GrupoAlumno.unido_en)
            .join(GrupoAlumno, GrupoAlumno.alumno_id == Usuario.id)
            .where(GrupoAlumno.grupo_id == grupo.id)
            .order_by(Usuario.nombre)
        ).all()
        asignaciones = list(
            db.scalars(select(Asignacion).where(Asignacion.grupo_id == grupo.id).order_by(Asignacion.creada_en.desc()))
        )
        ids = [a.id for a in asignaciones]
        conteos: Dict[str, Dict[str, int]] = {i: {"entregada": 0, "evaluada": 0, "rehacer": 0} for i in ids}
        if ids:
            for asignacion_id, estado, cuantas in db.execute(
                select(Entrega.asignacion_id, Entrega.estado, func.count())
                .where(Entrega.asignacion_id.in_(ids))
                .group_by(Entrega.asignacion_id, Entrega.estado)
            ):
                conteos[asignacion_id][estado] = cuantas
        practicas = _practicas(db, (a.practica_id for a in asignaciones))
    except SQLAlchemyError as error:
        raise _fallo(db, error, "GET /api/evaluacion/grupos/{id}")
    return {
        "id": grupo.id,
        "nombre": grupo.nombre,
        "codigo": grupo.codigo,
        "archivado": bool(grupo.archivado),
        "alumnos": [
            {"id": f.id, "nombre": f.nombre or (f.email or "").split("@")[0], "email": f.email, "unido_en": iso(f.unido_en)}
            for f in filas
        ],
        "asignaciones": [
            _asignacion(a, practicas.get(a.practica_id), {"entregas": conteos[a.id]}) for a in asignaciones
        ],
    }


@router.delete("/grupos/{grupo_id}/alumnos/{alumno_id}", dependencies=[Depends(LIMITE_PROFESOR)])
def sacar_alumno(grupo_id: str, alumno_id: str, db: Session = Depends(obtener_db),
                 profesor: Usuario = Depends(profesor_requerido)):
    try:
        grupo = _grupo_propio(db, grupo_id, profesor)
        db.execute(delete(GrupoAlumno).where(GrupoAlumno.grupo_id == grupo.id, GrupoAlumno.alumno_id == alumno_id))
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "DELETE /api/evaluacion/grupos/{id}/alumnos")
    return {"ok": True}


# --- Profesor: prácticas y asignaciones -------------------------------------------


@router.get("/practicas", dependencies=[Depends(LIMITE_PROFESOR)])
def practicas_para_asignar(db: Session = Depends(obtener_db), _: Usuario = Depends(profesor_requerido)):
    filas = db.scalars(
        select(Practica)
        .options(defer(Practica.definicion))
        .where(Practica.estado == "publicado", Practica.version_publicada.is_not(None))
        .order_by(Practica.nivel, Practica.titulo)
    )
    return [
        {"id": p.id, "titulo": p.titulo, "nivel": p.nivel, "curso_id": p.curso_id, "leccion_id": p.leccion_id}
        for p in filas
    ]


@router.post("/asignaciones", status_code=201, dependencies=[Depends(LIMITE_PROFESOR)])
def asignar(cuerpo: SolicitudAsignacion, db: Session = Depends(obtener_db), profesor: Usuario = Depends(profesor_requerido)):
    try:
        grupo = _grupo_propio(db, cuerpo.grupo_id, profesor)
        if grupo.archivado:
            raise HTTPException(status_code=409, detail="Ese grupo está archivado: vuelve a abrirlo para dejarle prácticas.")
        practica = db.get(Practica, cuerpo.practica_id)
        if practica is None or practica.estado != "publicado":
            raise HTTPException(status_code=404, detail="Esa práctica no está publicada: los alumnos no podrían abrirla.")
        asignacion = Asignacion(
            id=uuid.uuid4().hex,
            grupo_id=grupo.id,
            profesor_id=profesor.id,
            practica_id=practica.id,
            titulo=(cuerpo.titulo or "").strip() or practica.titulo,
            indicaciones=(cuerpo.indicaciones or "").strip() or None,
            fecha_limite=_sin_zona(cuerpo.fecha_limite),
            estado="abierta",
            creada_en=ahora(),
        )
        db.add(asignacion)
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "POST /api/evaluacion/asignaciones")
    return _asignacion(asignacion, practica, {"entregas": {"entregada": 0, "evaluada": 0, "rehacer": 0}})


@router.patch("/asignaciones/{asignacion_id}", dependencies=[Depends(LIMITE_PROFESOR)])
def cambiar_asignacion(asignacion_id: str, cuerpo: CambioAsignacion, db: Session = Depends(obtener_db),
                       profesor: Usuario = Depends(profesor_requerido)):
    try:
        asignacion = _asignacion_propia(db, asignacion_id, profesor)
        if cuerpo.estado:
            asignacion.estado = cuerpo.estado
        if cuerpo.indicaciones is not None:
            asignacion.indicaciones = cuerpo.indicaciones.strip() or None
        if cuerpo.quitar_fecha:
            asignacion.fecha_limite = None
        elif cuerpo.fecha_limite is not None:
            asignacion.fecha_limite = _sin_zona(cuerpo.fecha_limite)
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "PATCH /api/evaluacion/asignaciones")
    return _asignacion(asignacion, db.get(Practica, asignacion.practica_id))


@router.get("/asignaciones/{asignacion_id}", dependencies=[Depends(LIMITE_PROFESOR)])
def ver_asignacion(asignacion_id: str, db: Session = Depends(obtener_db), profesor: Usuario = Depends(profesor_requerido)):
    """Una fila por alumno del grupo: su entrega o, si aún no entrega, cómo va en la práctica."""
    try:
        asignacion = _asignacion_propia(db, asignacion_id, profesor)
        alumnos = db.execute(
            select(Usuario.id, Usuario.nombre, Usuario.email)
            .join(GrupoAlumno, GrupoAlumno.alumno_id == Usuario.id)
            .where(GrupoAlumno.grupo_id == asignacion.grupo_id)
            .order_by(Usuario.nombre)
        ).all()
        entregas = {
            e.alumno_id: e
            for e in db.scalars(
                select(Entrega).options(defer(Entrega.modelo)).where(Entrega.asignacion_id == asignacion.id)
            )
        }
        ids = [a.id for a in alumnos]
        avance = {
            p.usuario_id: p
            for p in db.scalars(
                select(ProgresoPractica).where(
                    ProgresoPractica.practica_id == asignacion.practica_id, ProgresoPractica.usuario_id.in_(ids)
                )
            )
        } if ids else {}
        practica = db.get(Practica, asignacion.practica_id)
    except SQLAlchemyError as error:
        raise _fallo(db, error, "GET /api/evaluacion/asignaciones/{id}")
    filas = []
    for a in alumnos:
        entrega = entregas.get(a.id)
        en_vivo = avance.get(a.id)
        filas.append({
            "alumno": {"id": a.id, "nombre": a.nombre or (a.email or "").split("@")[0], "email": a.email},
            "entrega": _entrega(entrega, asignacion) if entrega else None,
            "avance": {
                "progreso": en_vivo.progreso,
                "completada": bool(en_vivo.completada),
                "intentos": en_vivo.intentos,
                "pistas": en_vivo.pistas,
                "autonomia": en_vivo.autonomia,
                "actualizado_en": iso(en_vivo.actualizado_en),
            } if en_vivo else None,
        })
    return {"asignacion": _asignacion(asignacion, practica), "alumnos": filas}


def _entrega_propia(db: Session, asignacion_id: str, alumno_id: str, profesor: Usuario):
    asignacion = _asignacion_propia(db, asignacion_id, profesor)
    entrega = db.get(Entrega, (asignacion.id, alumno_id))
    if entrega is None:
        raise HTTPException(status_code=404, detail="Ese alumno todavía no entrega esta práctica.")
    return asignacion, entrega


@router.get("/asignaciones/{asignacion_id}/entregas/{alumno_id}/modelo", dependencies=[Depends(LIMITE_PROFESOR)])
def recoger_modelo(asignacion_id: str, alumno_id: str, db: Session = Depends(obtener_db),
                   profesor: Usuario = Depends(profesor_requerido)):
    try:
        _, entrega = _entrega_propia(db, asignacion_id, alumno_id, profesor)
    except SQLAlchemyError as error:
        raise _fallo(db, error, "GET /api/evaluacion/.../modelo")
    if entrega.modelo is None:
        detalle = (
            "Ya recogiste este modelo: vive en la computadora donde lo abriste."
            if entrega.recogido_en
            else "Esta entrega no trae modelo (o pasaron más de 30 días y se borró)."
        )
        raise HTTPException(status_code=410, detail=detalle)
    return {"modelo": entrega.modelo, "bytes": entrega.modelo_bytes, "entregada_en": iso(entrega.entregada_en)}


@router.post("/asignaciones/{asignacion_id}/entregas/{alumno_id}/modelo/recibido", dependencies=[Depends(LIMITE_PROFESOR)])
def modelo_recibido(asignacion_id: str, alumno_id: str, db: Session = Depends(obtener_db),
                    profesor: Usuario = Depends(profesor_requerido)):
    """El navegador del profesor ya guardó el modelo: aquí se borra."""
    try:
        _, entrega = _entrega_propia(db, asignacion_id, alumno_id, profesor)
        if entrega.modelo is not None:
            entrega.modelo = None
            entrega.recogido_en = ahora()
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "POST /api/evaluacion/.../modelo/recibido")
    return {"recogido_en": iso(entrega.recogido_en)}


@router.post("/asignaciones/{asignacion_id}/entregas/{alumno_id}/evaluar", dependencies=[Depends(LIMITE_PROFESOR)])
def evaluar(asignacion_id: str, alumno_id: str, cuerpo: SolicitudEvaluacion, db: Session = Depends(obtener_db),
            profesor: Usuario = Depends(profesor_requerido)):
    try:
        asignacion, entrega = _entrega_propia(db, asignacion_id, alumno_id, profesor)
        entrega.estado = cuerpo.estado
        entrega.calificacion = cuerpo.calificacion
        entrega.comentario = (cuerpo.comentario or "").strip() or None
        entrega.evaluada_en = ahora()
        entrega.vista_alumno = 0
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "POST /api/evaluacion/.../evaluar")
    return _entrega(entrega, asignacion)


# --- Alumno -----------------------------------------------------------------------


@router.post("/unirme", dependencies=[Depends(LIMITE_UNIRSE)])
def unirme(cuerpo: SolicitudUnirse, db: Session = Depends(obtener_db), alumno: Usuario = Depends(_cuenta_con_correo)):
    try:
        grupo = db.scalar(select(Grupo).where(Grupo.codigo == _normalizar_codigo(cuerpo.codigo)))
        if grupo is None or grupo.archivado:
            raise HTTPException(status_code=404, detail="Ese código no es de ningún grupo abierto. Revísalo con tu profesor.")
        if grupo.profesor_id == alumno.id:
            raise HTTPException(status_code=409, detail="Es tu propio grupo: lo ves en el panel del profesor.")
        if db.get(GrupoAlumno, (grupo.id, alumno.id)) is None:
            total = db.scalar(select(func.count()).select_from(GrupoAlumno).where(GrupoAlumno.grupo_id == grupo.id))
            if total >= MAX_ALUMNOS:
                raise HTTPException(status_code=409, detail="Ese grupo ya está lleno. Avísale a tu profesor.")
            db.add(GrupoAlumno(grupo_id=grupo.id, alumno_id=alumno.id, unido_en=ahora()))
            db.commit()
        profesor = db.get(Usuario, grupo.profesor_id)
    except SQLAlchemyError as error:
        raise _fallo(db, error, "POST /api/evaluacion/unirme")
    return {"id": grupo.id, "nombre": grupo.nombre, "profesor": profesor.nombre if profesor else None}


@router.get("/mis-asignaciones")
def mis_asignaciones(db: Session = Depends(obtener_db), alumno: Usuario = Depends(_cuenta_con_correo)):
    """Lo que el alumno tiene pendiente con sus profesores. Pocos bytes: lo lee también el add-on."""
    try:
        grupos = db.execute(
            select(Grupo, Usuario.nombre)
            .join(GrupoAlumno, GrupoAlumno.grupo_id == Grupo.id)
            .join(Usuario, Usuario.id == Grupo.profesor_id)
            .where(GrupoAlumno.alumno_id == alumno.id, Grupo.archivado == 0)
            .order_by(Grupo.nombre)
        ).all()
        ids = [g.id for g, _ in grupos]
        asignaciones = list(
            db.scalars(select(Asignacion).where(Asignacion.grupo_id.in_(ids)).order_by(Asignacion.creada_en.desc()))
        ) if ids else []
        entregas = {
            e.asignacion_id: e
            for e in db.scalars(
                select(Entrega).options(defer(Entrega.modelo))
                .where(Entrega.alumno_id == alumno.id, Entrega.asignacion_id.in_([a.id for a in asignaciones]))
            )
        } if asignaciones else {}
        practicas = _practicas(db, (a.practica_id for a in asignaciones))
    except SQLAlchemyError as error:
        raise _fallo(db, error, "GET /api/evaluacion/mis-asignaciones")

    def mia(e: Optional[Entrega]) -> Optional[dict]:
        if e is None:
            return None
        return {
            "estado": e.estado,
            "progreso": e.progreso,
            "entregada_en": iso(e.entregada_en),
            "calificacion": e.calificacion,
            "comentario": e.comentario,
            "evaluada_en": iso(e.evaluada_en),
            "nueva": not e.vista_alumno,
        }

    return {
        "grupos": [{"id": g.id, "nombre": g.nombre, "profesor": nombre} for g, nombre in grupos],
        "asignaciones": [
            _asignacion(a, practicas.get(a.practica_id), {"entrega": mia(entregas.get(a.id))}) for a in asignaciones
        ],
    }


@router.post("/mis-asignaciones/{asignacion_id}/vista")
def marcar_vista(asignacion_id: str, db: Session = Depends(obtener_db), alumno: Usuario = Depends(_cuenta_con_correo)):
    try:
        entrega = db.get(Entrega, (asignacion_id, alumno.id))
        if entrega is not None and not entrega.vista_alumno:
            entrega.vista_alumno = 1
            db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "POST /api/evaluacion/mis-asignaciones/{id}/vista")
    return {"ok": True}


@router.delete("/mis-grupos/{grupo_id}")
def salir_del_grupo(grupo_id: str, db: Session = Depends(obtener_db), alumno: Usuario = Depends(_cuenta_con_correo)):
    try:
        db.execute(delete(GrupoAlumno).where(GrupoAlumno.grupo_id == grupo_id, GrupoAlumno.alumno_id == alumno.id))
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "DELETE /api/evaluacion/mis-grupos/{id}")
    return {"ok": True}


@router.post("/entregas", dependencies=[Depends(LIMITE_ENTREGAS)])
def entregar(cuerpo: SolicitudEntrega, db: Session = Depends(obtener_db), alumno: Usuario = Depends(_cuenta_con_correo)):
    """El alumno entrega: el progreso lo pone el servidor (el que midió el motor), no el add-on."""
    resumen = None
    if cuerpo.modelo:
        try:
            resumen = modelo_ligero.resumen_de(cuerpo.modelo)
        except modelo_ligero.ModeloInvalido as error:
            raise HTTPException(status_code=422, detail=str(error))
    try:
        asignacion = db.get(Asignacion, cuerpo.asignacion_id)
        if asignacion is None or db.get(GrupoAlumno, (asignacion.grupo_id, alumno.id)) is None:
            raise HTTPException(status_code=404, detail="Esa práctica no es de ninguno de tus grupos.")
        if asignacion.estado != "abierta":
            raise HTTPException(status_code=409, detail="Tu profesor ya cerró esta práctica: pídele que la vuelva a abrir.")
        entrega = db.get(Entrega, (asignacion.id, alumno.id))
        if entrega is not None and entrega.estado == "evaluada":
            raise HTTPException(status_code=409, detail="Tu profesor ya evaluó esta entrega. Si te pide rehacerla, podrás volver a entregar.")
        avance = db.get(ProgresoPractica, (alumno.id, asignacion.practica_id))
        momento = ahora()
        if entrega is None:
            entrega = Entrega(asignacion_id=asignacion.id, alumno_id=alumno.id)
            db.add(entrega)
        entrega.estado = "entregada"
        entrega.progreso = avance.progreso if avance else 0
        entrega.completada = avance.completada if avance else 0
        entrega.intentos = avance.intentos if avance else 0
        entrega.resumen = json.dumps(resumen, ensure_ascii=False, separators=(",", ":")) if resumen else None
        entrega.modelo = cuerpo.modelo or None
        entrega.modelo_bytes = len(cuerpo.modelo or "")
        entrega.entregada_en = momento
        entrega.recogido_en = None
        entrega.calificacion = None
        entrega.evaluada_en = None
        entrega.vista_alumno = 1
        _purgar_modelos(db)
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "POST /api/evaluacion/entregas")
    return {
        "asignacion_id": asignacion.id,
        "estado": entrega.estado,
        "progreso": entrega.progreso,
        "completada": bool(entrega.completada),
        "modelo_bytes": entrega.modelo_bytes,
        "resumen": resumen,
    }
