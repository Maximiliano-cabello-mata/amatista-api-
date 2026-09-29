import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database.modelos import Usuario

log = logging.getLogger("amatista")


def asegurar_usuario(db: Session, usuario_id: str, nombre: Optional[str] = None) -> None:
    """Crea el usuario si no existe: las llaves foráneas lo necesitan antes."""
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        db.add(Usuario(id=usuario_id, nombre=nombre))
        db.flush()
    elif nombre and not usuario.nombre:
        usuario.nombre = nombre


def error_bd(error: SQLAlchemyError, ruta: str, estado: int = 500) -> HTTPException:
    """Registra el error completo en la terminal y devuelve un mensaje corto.

    El mensaje incluye el código de Oracle (por ejemplo ORA-00942) para que
    se pueda diagnosticar desde el navegador.
    """
    log.exception("Error de base de datos en %s", ruta)
    original = getattr(error, "orig", None) or error
    texto = str(original).strip()
    primera_linea = texto.splitlines()[0] if texto else type(original).__name__
    return HTTPException(status_code=estado, detail=f"Error de base de datos: {primera_linea}")
