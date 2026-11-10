"""POST /api/iniciar-sesion heredado (versiones viejas de la PWA y Laboratorio).

Solo registra alumnos anónimos para comprobar que la base de datos responde.
Ya no crea sesiones de autenticación: el sesion_id que devuelve es un UUID
informativo que se guarda como hash con activa=0, así que NO sirve como token
Bearer. Las cuentas con correo y contraseña entran por /api/auth.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import error_bd
from api.dependencias import es_cuenta_registrada
from api.limites import limitar
from database.conexion import obtener_db
from database.modelos import Sesion, Usuario, ahora
from seguridad import hash_token, normalizar_email

router = APIRouter(prefix="/api", tags=["sesiones"])

MENSAJE_CUENTA = "Esta cuenta usa correo y contraseña: entra desde «Entrar»."


class SolicitudSesion(BaseModel):
    usuario_id: Optional[str] = Field(default=None, max_length=100)
    email: Optional[str] = Field(default=None, max_length=100)
    nombre: Optional[str] = Field(default=None, max_length=150)
    dispositivo: Optional[str] = Field(default=None, max_length=500)


def pertenece_a_cuenta(db: Session, usuario_id: str, email: Optional[str]) -> bool:
    """True si el id o el correo son de una cuenta registrada (o de un anónimo ya fusionado)."""
    if es_cuenta_registrada(db.get(Usuario, usuario_id)):
        return True
    correos = {normalizar_email(c) for c in (email, usuario_id) if c and "@" in c}
    if not correos:
        return False
    return db.scalar(select(Usuario.id).where(Usuario.email.in_(correos)).limit(1)) is not None


@router.post("/iniciar-sesion", dependencies=[Depends(limitar(30))])
def iniciar_sesion(datos: SolicitudSesion, db: Session = Depends(obtener_db)):
    usuario_id = (datos.usuario_id or datos.email or "").strip()
    if not usuario_id:
        raise HTTPException(status_code=422, detail="Envía usuario_id o email")

    sesion_id = str(uuid.uuid4())
    try:
        if pertenece_a_cuenta(db, usuario_id, datos.email):
            raise HTTPException(status_code=409, detail=MENSAJE_CUENTA)

        usuario = db.get(Usuario, usuario_id)
        if usuario is None:
            db.add(Usuario(id=usuario_id, nombre=datos.nombre))
            db.flush()  # el usuario debe existir antes que su sesión (FK en Oracle)
        elif datos.nombre and not usuario.nombre:
            usuario.nombre = datos.nombre
        momento = ahora()
        db.add(
            Sesion(
                id=hash_token(sesion_id),
                usuario_id=usuario_id,
                dispositivo=(datos.dispositivo or "web")[:200],
                activa=0,
                creado_en=momento,
                ultimo_acceso=momento,
                expira_en=momento,
            )
        )
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/iniciar-sesion")

    return {
        "mensaje": "Sesión registrada en la base de datos",
        "usuario_id": usuario_id,
        "sesion_id": sesion_id,
    }
