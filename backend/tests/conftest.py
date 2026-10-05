"""Fixtures compartidas: API con SQLite temporal y cuentas con sesión."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database import conexion
from database.modelos import Sesion, Usuario, ahora
from seguridad import DURACION_SESION, hash_password, hash_token, nuevo_token


@pytest.fixture(autouse=True)
def entorno_pruebas(monkeypatch):
    # PBKDF2 rápido en pruebas y sin límites de peticiones por IP.
    monkeypatch.setenv("AMATISTA_PBKDF2_ITER", "1000")
    monkeypatch.setenv("AMATISTA_SIN_LIMITES", "1")
    # Las pruebas parten de una base sin prácticas; test_practicas_al_arrancar la prende.
    monkeypatch.setenv("AMATISTA_SINCRONIZAR_PRACTICAS", "0")
    # Las pruebas cambian la base directo: el catálogo se revisa en cada petición.
    monkeypatch.setenv("AMATISTA_CACHE_CATALOGO_S", "0")
    monkeypatch.setenv("AMATISTA_CACHE_RESUMEN_S", "0")
    monkeypatch.delenv("AMATISTA_ADMINS", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'prueba.db'}")
    conexion.motor.cache_clear()
    from api.contenido import invalidar_catalogo
    from main import app

    invalidar_catalogo()

    with TestClient(app) as c:
        yield c
    conexion.motor.cache_clear()


@pytest.fixture()
def crear_cuenta(cliente):
    """Crea directo en la base una cuenta con sesión y devuelve (id, cabeceras).

    No depende de /api/auth: sirve para probar permisos de otras rutas.
    Uso: usuario_id, cabeceras = crear_cuenta(rol="admin")
    """

    def crear(rol="alumno", email=None, password="clave-segura-123", nombre="Prueba", es_prueba=0):
        usuario_id = f"usr-{uuid.uuid4()}"
        email = email or f"{usuario_id}@amatista.local"
        token = nuevo_token()
        with Session(conexion.motor()) as db:
            db.add(
                Usuario(
                    id=usuario_id,
                    nombre=nombre,
                    email=email.lower(),
                    rol=rol,
                    password_hash=hash_password(password),
                    correo_confirmado=1,
                    es_prueba=es_prueba,
                )
            )
            db.flush()
            db.add(
                Sesion(
                    id=hash_token(token),
                    usuario_id=usuario_id,
                    dispositivo="pytest",
                    activa=1,
                    expira_en=ahora() + DURACION_SESION,
                )
            )
            db.commit()
        return usuario_id, {"Authorization": f"Bearer {token}"}

    return crear
