"""Buzón del creador: los alumnos platican con el creador para mejorar Amatista (sql/013).

Para alumnos con cuenta (así reciben la respuesta):
- GET  /api/buzon ........................ sus conversaciones, la más reciente primero,
                                           con sus mensajes y si hay respuesta sin leer.
- POST /api/buzon ........................ abre una conversación {categoria, texto, pagina?}.
- POST /api/buzon/{id}/mensajes .......... suma un mensaje a una conversación suya.
- POST /api/buzon/{id}/leida ............. marca la respuesta como leída.

Para el creador y el equipo (admin):
- GET  /api/buzon/bandeja?estado= ........ todas, abiertas primero, con quién escribe.
- POST /api/buzon/bandeja/{id}/responder . responde como el creador (DE_CREADOR = 1).
- POST /api/buzon/bandeja/{id}/estado .... abierta, respondida o resuelta.

Límites: 10 mensajes por hora y cuenta, 2000 caracteres por mensaje y 40
mensajes por conversación. Sin 013 todo responde 503 con un mensaje claro.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import error_bd
from api.dependencias import es_cuenta_registrada, requiere_rol, usuario_requerido
from api.limites import limitar
from api.perfil import es_creador, sin_esquema
from database.conexion import obtener_db
from database.modelos import CATEGORIAS_SUGERENCIA, ESTADOS_SUGERENCIA, MensajeSugerencia, Perfil, Sugerencia, Usuario, ahora

router = APIRouter(prefix="/api/buzon", tags=["buzon"])

LIMITE_MENSAJES = limitar(10, ventana=3600, por="cuenta")
MAX_MENSAJES = 40
SIN_013 = "El buzón todavía no está listo en el servidor (falta sql/013_avatares_y_buzon.sql). Inténtalo más tarde."


def _texto(valor: str) -> str:
    valor = valor.strip()
    if len(valor) < 3:
        raise ValueError("Escribe al menos unas palabras.")
    return valor


class SolicitudSugerencia(BaseModel):
    categoria: str
    texto: str = Field(max_length=2000)
    pagina: Optional[str] = Field(default=None, max_length=200)

    @field_validator("categoria")
    @classmethod
    def _categoria(cls, valor: str) -> str:
        if valor not in CATEGORIAS_SUGERENCIA:
            raise ValueError("Elige de qué trata: error, idea, contenido u otra.")
        return valor

    @field_validator("texto")
    @classmethod
    def _limpio(cls, valor: str) -> str:
        return _texto(valor)


class SolicitudMensaje(BaseModel):
    texto: str = Field(max_length=2000)

    @field_validator("texto")
    @classmethod
    def _limpio(cls, valor: str) -> str:
        return _texto(valor)


class SolicitudEstado(BaseModel):
    estado: str

    @field_validator("estado")
    @classmethod
    def _estado(cls, valor: str) -> str:
        if valor not in ESTADOS_SUGERENCIA:
            raise ValueError("Estado no válido.")
        return valor


def _fallo(db: Session, error: SQLAlchemyError, ruta: str) -> HTTPException:
    db.rollback()
    if sin_esquema(error):
        return HTTPException(status_code=503, detail=SIN_013)
    return error_bd(error, ruta)


def _mensajes(db: Session, ids: List[str]) -> dict:
    por_conversacion = {i: [] for i in ids}
    if not ids:
        return por_conversacion
    filas = db.scalars(
        select(MensajeSugerencia).where(MensajeSugerencia.sugerencia_id.in_(ids)).order_by(MensajeSugerencia.creado_en)
    )
    for m in filas:
        por_conversacion[m.sugerencia_id].append(
            {"id": m.id, "de_creador": bool(m.de_creador), "texto": m.texto, "creado_en": m.creado_en.isoformat()}
        )
    return por_conversacion


def _conversacion(s: Sugerencia, mensajes: list, extra: Optional[dict] = None) -> dict:
    datos = {
        "id": s.id,
        "categoria": s.categoria,
        "pagina": s.pagina,
        "estado": s.estado,
        "sin_leer": bool(s.sin_leer_alumno),
        "sin_leer_creador": bool(s.sin_leer_creador),
        "creada_en": s.creada_en.isoformat(),
        "actualizada_en": s.actualizada_en.isoformat(),
        "mensajes": mensajes,
    }
    if extra:
        datos.update(extra)
    return datos


def _cuenta_con_correo(usuario: Usuario = Depends(usuario_requerido)) -> Usuario:
    if not es_cuenta_registrada(usuario):
        raise HTTPException(status_code=401, detail="Entra con tu cuenta para escribirle al creador y recibir su respuesta.")
    return usuario


def _propia(db: Session, sugerencia_id: str, usuario: Usuario) -> Sugerencia:
    s = db.get(Sugerencia, sugerencia_id)
    if s is None or s.usuario_id != usuario.id:
        raise HTTPException(status_code=404, detail="No encontramos esa conversación.")
    return s


def _agregar(db: Session, s: Sugerencia, autor: Usuario, texto: str, de_creador: bool) -> None:
    total = db.scalar(select(func.count()).select_from(MensajeSugerencia).where(MensajeSugerencia.sugerencia_id == s.id))
    if total >= MAX_MENSAJES:
        raise HTTPException(status_code=409, detail="Esta conversación ya es muy larga: abre una nueva.")
    momento = ahora()
    db.add(MensajeSugerencia(id=uuid.uuid4().hex, sugerencia_id=s.id, autor_id=autor.id,
                             de_creador=1 if de_creador else 0, texto=texto, creado_en=momento))
    s.actualizada_en = momento
    if de_creador:
        s.sin_leer_alumno = 1
        s.sin_leer_creador = 0
        if s.estado == "abierta":
            s.estado = "respondida"
    else:
        s.sin_leer_creador = 1
        if s.estado != "abierta":
            s.estado = "abierta"


# --- Alumno -------------------------------------------------------------------


@router.get("")
def mis_conversaciones(usuario: Usuario = Depends(_cuenta_con_correo), db: Session = Depends(obtener_db)):
    try:
        lista = list(db.scalars(
            select(Sugerencia).where(Sugerencia.usuario_id == usuario.id).order_by(Sugerencia.actualizada_en.desc()).limit(30)
        ))
        mensajes = _mensajes(db, [s.id for s in lista])
    except SQLAlchemyError as error:
        raise _fallo(db, error, "/api/buzon")
    return {"conversaciones": [_conversacion(s, mensajes[s.id]) for s in lista]}


@router.post("", status_code=201, dependencies=[Depends(LIMITE_MENSAJES)])
def abrir(datos: SolicitudSugerencia, usuario: Usuario = Depends(_cuenta_con_correo), db: Session = Depends(obtener_db)):
    momento = ahora()
    s = Sugerencia(id=uuid.uuid4().hex, usuario_id=usuario.id, categoria=datos.categoria, pagina=datos.pagina,
                   estado="abierta", sin_leer_alumno=0, sin_leer_creador=1, creada_en=momento, actualizada_en=momento)
    try:
        db.add(s)
        db.flush()
        _agregar(db, s, usuario, datos.texto, de_creador=False)
        db.commit()
        mensajes = _mensajes(db, [s.id])
    except SQLAlchemyError as error:
        raise _fallo(db, error, "/api/buzon")
    return _conversacion(s, mensajes[s.id])


@router.post("/{sugerencia_id}/mensajes", status_code=201, dependencies=[Depends(LIMITE_MENSAJES)])
def escribir(sugerencia_id: str, datos: SolicitudMensaje, usuario: Usuario = Depends(_cuenta_con_correo),
             db: Session = Depends(obtener_db)):
    try:
        s = _propia(db, sugerencia_id, usuario)
        _agregar(db, s, usuario, datos.texto, de_creador=False)
        db.commit()
        mensajes = _mensajes(db, [s.id])
    except SQLAlchemyError as error:
        raise _fallo(db, error, "/api/buzon/mensajes")
    return _conversacion(s, mensajes[s.id])


@router.post("/{sugerencia_id}/leida")
def marcar_leida(sugerencia_id: str, usuario: Usuario = Depends(_cuenta_con_correo), db: Session = Depends(obtener_db)):
    try:
        s = _propia(db, sugerencia_id, usuario)
        s.sin_leer_alumno = 0
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "/api/buzon/leida")
    return {"ok": True}


# --- Creador y equipo -----------------------------------------------------------

solo_admin = requiere_rol("admin")


@router.get("/bandeja")
def bandeja(estado: Optional[str] = None, usuario: Usuario = Depends(solo_admin), db: Session = Depends(obtener_db)):
    if estado is not None and estado not in ESTADOS_SUGERENCIA:
        raise HTTPException(status_code=422, detail="Estado no válido.")
    try:
        consulta = select(Sugerencia, Usuario.nombre, Usuario.email, Perfil.avatar).join(
            Usuario, Usuario.id == Sugerencia.usuario_id
        ).outerjoin(Perfil, Perfil.usuario_id == Sugerencia.usuario_id)
        if estado:
            consulta = consulta.where(Sugerencia.estado == estado)
        filas = list(db.execute(consulta.order_by(Sugerencia.sin_leer_creador.desc(), Sugerencia.actualizada_en.desc()).limit(100)))
        mensajes = _mensajes(db, [f[0].id for f in filas])
        sin_leer = db.scalar(select(func.count()).select_from(Sugerencia).where(Sugerencia.sin_leer_creador == 1))
    except SQLAlchemyError as error:
        raise _fallo(db, error, "/api/buzon/bandeja")
    return {
        "sin_leer": sin_leer or 0,
        "es_creador": es_creador(usuario),
        "conversaciones": [
            _conversacion(s, mensajes[s.id], {"alumno": {"nombre": nombre, "email": email, "avatar": avatar}})
            for s, nombre, email, avatar in filas
        ],
    }


@router.post("/bandeja/{sugerencia_id}/responder", status_code=201)
def responder(sugerencia_id: str, datos: SolicitudMensaje, usuario: Usuario = Depends(solo_admin),
              db: Session = Depends(obtener_db)):
    try:
        s = db.get(Sugerencia, sugerencia_id)
        if s is None:
            raise HTTPException(status_code=404, detail="No encontramos esa conversación.")
        _agregar(db, s, usuario, datos.texto, de_creador=True)
        db.commit()
        mensajes = _mensajes(db, [s.id])
    except SQLAlchemyError as error:
        raise _fallo(db, error, "/api/buzon/responder")
    return _conversacion(s, mensajes[s.id])


@router.post("/bandeja/{sugerencia_id}/estado")
def cambiar_estado(sugerencia_id: str, datos: SolicitudEstado, usuario: Usuario = Depends(solo_admin),
                   db: Session = Depends(obtener_db)):
    try:
        s = db.get(Sugerencia, sugerencia_id)
        if s is None:
            raise HTTPException(status_code=404, detail="No encontramos esa conversación.")
        s.estado = datos.estado
        s.sin_leer_creador = 0
        db.commit()
    except SQLAlchemyError as error:
        raise _fallo(db, error, "/api/buzon/estado")
    return {"id": s.id, "estado": s.estado}
