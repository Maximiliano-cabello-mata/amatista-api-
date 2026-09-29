"""Conexión a la base de datos.

- Producción: Oracle Autonomous Database. Variables en backend/.env:
  DB_USER, DB_PASSWORD y DB_DSN (o DB_HOST + DB_SERVICE).
- Desarrollo y pruebas sin Oracle: DATABASE_URL, por ejemplo
  sqlite:///./amatista_local.db
"""
import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

load_dotenv()


def dsn_oracle() -> str:
    """Cadena de conexión a Oracle.

    Lo más seguro es copiar en DB_DSN la cadena TLS de la consola de OCI.
    Si no está, se arma con DB_HOST, DB_PORT y DB_SERVICE.
    """
    dsn = os.getenv("DB_DSN")
    if dsn:
        return dsn
    host = os.getenv("DB_HOST")
    servicio = os.getenv("DB_SERVICE")
    puerto = os.getenv("DB_PORT", "1522")
    if not host or not servicio:
        raise RuntimeError(
            "Falta configurar Oracle: define DB_DSN, o DB_HOST y DB_SERVICE, en backend/.env"
        )
    # Autonomous Database solo acepta conexiones cifradas (tcps) en el puerto 1522.
    # Con "tcp" la conexión se corta: "Connection reset by peer" (Errno 104).
    return f"tcps://{host}:{puerto}/{servicio}"


def crear_motor() -> Engine:
    url = os.getenv("DATABASE_URL")
    if url:
        argumentos = {"check_same_thread": False} if url.startswith("sqlite") else {}
        return create_engine(url, connect_args=argumentos)

    usuario = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    if not usuario or not password:
        raise RuntimeError("Falta DB_USER o DB_PASSWORD en backend/.env")
    return create_engine(
        "oracle+oracledb://@",
        connect_args={"user": usuario, "password": password, "dsn": dsn_oracle()},
        pool_pre_ping=True,  # descarta conexiones que Oracle cerró por inactividad
        pool_recycle=1800,
    )


@lru_cache(maxsize=1)
def motor() -> Engine:
    return crear_motor()


def obtener_db():
    """Dependencia de FastAPI: una sesión de base de datos por petición."""
    db = Session(motor())
    try:
        yield db
    finally:
        db.close()
