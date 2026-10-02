"""Conexión a la base de datos.

- Producción: Oracle Autonomous Database. Variables en backend/.env:
  DB_USER, DB_PASSWORD y DB_DSN (o DB_HOST + DB_SERVICE). Opcionales:
  DB_ESQUEMA (dueño de las tablas si DB_USER es AMATISTA_APP, ver
  sql/004_usuario_aplicacion.sql), DB_POOL y DB_POOL_EXTRA (tamaño del pool).
- Desarrollo y pruebas sin Oracle: DATABASE_URL, por ejemplo
  sqlite:///./amatista_local.db
"""
import os
import re
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

load_dotenv()

# Identificador de Oracle sin comillas. Se valida porque el nombre se pega
# dentro de ALTER SESSION, que no admite variables enlazadas.
PATRON_ESQUEMA = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")


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


def esquema_oracle() -> Optional[str]:
    """Esquema dueño de las tablas (DB_ESQUEMA), en mayúsculas; None si no se definió."""
    esquema = os.getenv("DB_ESQUEMA", "").strip()
    if not esquema:
        return None
    # fullmatch: "$" también aceptaría un salto de línea al final.
    if not PATRON_ESQUEMA.fullmatch(esquema):
        raise RuntimeError(
            f"DB_ESQUEMA={esquema!r} no es un nombre de esquema válido: usa letras, números, _ $ o #, "
            "empezando con una letra (por ejemplo DB_ESQUEMA=ADMIN)."
        )
    return esquema.upper()


def entero_env(nombre: str, defecto: int, minimo: int) -> int:
    valor = os.getenv(nombre, "").strip()
    if not valor:
        return defecto
    try:
        numero = int(valor)
    except ValueError:
        raise RuntimeError(f"{nombre} debe ser un número entero (es {valor!r}).") from None
    if numero < minimo:
        raise RuntimeError(f"{nombre} debe ser {minimo} o más (es {numero}).")
    return numero


def fijar_esquema(motor: Engine, esquema: str) -> None:
    """Cada conexión nueva del pool trabaja sobre las tablas de `esquema`.

    Así AMATISTA_APP usa las tablas de ADMIN sin sinónimos ni prefijos.
    insert=True: corre antes de que SQLAlchemy lea el esquema por defecto.
    """

    def al_conectar(conexion_dbapi, _registro):
        cursor = conexion_dbapi.cursor()
        try:
            cursor.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {esquema}")
        finally:
            cursor.close()

    event.listen(motor, "connect", al_conectar, insert=True)


def crear_motor() -> Engine:
    url = os.getenv("DATABASE_URL")
    if url:
        argumentos = {"check_same_thread": False} if url.startswith("sqlite") else {}
        return create_engine(url, connect_args=argumentos)

    usuario = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    if not usuario or not password:
        raise RuntimeError("Falta DB_USER o DB_PASSWORD en backend/.env")
    esquema = esquema_oracle()
    motor = create_engine(
        "oracle+oracledb://@",
        connect_args={"user": usuario, "password": password, "dsn": dsn_oracle()},
        # Pool acotado: la VM ARM y el Free Tier admiten pocas sesiones a la
        # vez. Máximo por proceso de uvicorn = DB_POOL + DB_POOL_EXTRA.
        pool_size=entero_env("DB_POOL", 5, minimo=1),
        max_overflow=entero_env("DB_POOL_EXTRA", 5, minimo=0),
        pool_timeout=30,
        pool_pre_ping=True,  # descarta conexiones que Oracle cerró por inactividad
        pool_recycle=1800,
    )
    if esquema:
        fijar_esquema(motor, esquema)
    return motor


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
