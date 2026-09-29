import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import asegurar_usuario, error_bd
from database.conexion import obtener_db
from database.modelos import Sesion

router = APIRouter(prefix="/api", tags=["sesiones"])


class SolicitudSesion(BaseModel):
    usuario_id: Optional[str] = Field(default=None, max_length=100)
    email: Optional[str] = Field(default=None, max_length=100)
    nombre: Optional[str] = Field(default=None, max_length=150)
    dispositivo: Optional[str] = Field(default=None, max_length=500)


@router.post("/iniciar-sesion")
def iniciar_sesion(datos: SolicitudSesion, db: Session = Depends(obtener_db)):
    usuario_id = (datos.usuario_id or datos.email or "").strip()
    if not usuario_id:
        raise HTTPException(status_code=422, detail="Envía usuario_id o email")

    sesion_id = str(uuid.uuid4())
    try:
        asegurar_usuario(db, usuario_id, datos.nombre)
        db.add(
            Sesion(
                id=sesion_id,
                usuario_id=usuario_id,
                dispositivo=(datos.dispositivo or "web")[:200],
                activa=1,
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
