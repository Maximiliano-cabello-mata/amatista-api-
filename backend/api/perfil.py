"""Personajes de la plataforma: el avatar low poly de cada cuenta (sql/013).

- GET /api/perfil ...... {avatar, es_creador, guardado}. Sin 013, «guardado»
                         es false y la PWA usa el avatar guardado en el equipo.
- PUT /api/perfil ...... {avatar}: uno de AVATARES (o null para quitarlo).
                         «pavo_real» es solo del creador.

El creador es la cuenta cuyo correo está en AMATISTA_CREADOR (backend/.env);
si la variable está vacía, cualquier admin. Su personaje es el pavo real y es
quien responde el buzón (api/buzon.py).
"""
import os
from typing import Optional, Set

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import error_bd
from api.dependencias import usuario_requerido
from database.conexion import obtener_db
from database.modelos import Perfil, Usuario, ahora

router = APIRouter(prefix="/api/perfil", tags=["perfil"])

# Los mismos ids que frontend/src/avatares/catalogo.js (test_perfil lo comprueba).
AVATARES = ("zorro", "buho", "ajolote", "colibri", "lobo", "gato", "tortuga", "jaguar")
AVATAR_CREADOR = "pavo_real"


def correos_creador() -> Set[str]:
    return {c.strip().lower() for c in os.getenv("AMATISTA_CREADOR", "").split(",") if c.strip()}


def es_creador(usuario: Optional[Usuario]) -> bool:
    if usuario is None or not usuario.email:
        return False
    correos = correos_creador()
    if correos:
        return usuario.email.lower() in correos
    return usuario.rol == "admin"


def sin_esquema(error: SQLAlchemyError) -> bool:
    """Falta una tabla (ORA-00942 o SQLite): sql/013 todavía no se ejecutó."""
    texto = str(getattr(error, "orig", None) or error).lower()
    return any(marca in texto for marca in ("ora-00942", "ora-00904", "no such table", "no such column"))


class SolicitudAvatar(BaseModel):
    avatar: Optional[str] = Field(default=None, max_length=40)


@router.get("")
def leer_perfil(usuario: Usuario = Depends(usuario_requerido), db: Session = Depends(obtener_db)):
    creador = es_creador(usuario)
    try:
        perfil = db.get(Perfil, usuario.id)
    except SQLAlchemyError as error:
        db.rollback()
        if sin_esquema(error):
            return {"avatar": None, "es_creador": creador, "guardado": False}
        raise error_bd(error, "/api/perfil")
    avatar = perfil.avatar if perfil else None
    # El creador estrena su pavo real sin tener que elegirlo.
    if avatar is None and creador:
        avatar = AVATAR_CREADOR
    return {"avatar": avatar, "es_creador": creador, "guardado": True}


@router.put("")
def guardar_avatar(
    datos: SolicitudAvatar,
    usuario: Usuario = Depends(usuario_requerido),
    db: Session = Depends(obtener_db),
):
    creador = es_creador(usuario)
    avatar = datos.avatar
    if avatar is not None and avatar not in AVATARES and avatar != AVATAR_CREADOR:
        raise HTTPException(status_code=422, detail="Ese avatar no existe.")
    if avatar == AVATAR_CREADOR and not creador:
        raise HTTPException(status_code=403, detail="El pavo real es el personaje del creador de Amatista.")
    try:
        perfil = db.get(Perfil, usuario.id)
        if perfil is None:
            db.add(Perfil(usuario_id=usuario.id, avatar=avatar, actualizado_en=ahora()))
        else:
            perfil.avatar = avatar
            perfil.actualizado_en = ahora()
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        if sin_esquema(error):
            raise HTTPException(status_code=503, detail="Falta ejecutar sql/013_avatares_y_buzon.sql en Oracle: "
                                                        "tu avatar se queda en este equipo por ahora.")
        raise error_bd(error, "/api/perfil")
    return {"avatar": avatar, "es_creador": creador, "guardado": True}
