import logging
import re
import uuid
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


def usuario_publico(usuario: Usuario) -> dict:
    """Datos de una cuenta que se pueden mandar al navegador (sin hashes ni códigos)."""
    return {
        "id": usuario.id,
        "nombre": usuario.nombre,
        "email": usuario.email,
        "telefono": usuario.telefono,
        "rol": usuario.rol or "alumno",
        "correo_confirmado": bool(usuario.correo_confirmado),
        "es_prueba": bool(usuario.es_prueba),
        "creado_en": usuario.creado_en.isoformat() if usuario.creado_en else None,
    }


def error_bd(error: SQLAlchemyError, ruta: str, estado: int = 500) -> HTTPException:
    """Registra el error completo en la terminal y devuelve un mensaje corto.

    Al navegador solo llega el código de Oracle (por ejemplo ORA-00942) y un
    folio para buscar el detalle en el log: el texto completo del error
    puede revelar nombres de tablas, columnas o del esquema.
    """
    folio = uuid.uuid4().hex[:8]
    log.exception("Error de base de datos en %s (folio %s)", ruta, folio)
    original = getattr(error, "orig", None) or error
    codigo = re.search(r"\b(?:ORA|DPI|PLS)-\d{4,5}\b", str(original))
    resumen = codigo.group(0) if codigo else type(original).__name__
    return HTTPException(status_code=estado, detail=f"Error de base de datos ({resumen}, folio {folio}).")
