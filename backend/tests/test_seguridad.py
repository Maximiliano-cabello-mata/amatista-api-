"""Auditoría de seguridad automática (herramientas/auditoria_seguridad.py).

Corre en cada push: recorre todas las rutas de la API con cargas de
inyección SQL (genéricas y de Oracle), XSS y recorrido de carpetas, revisa
la matriz de permisos y las cabeceras. Si alguien agrega una ruta nueva sin
dependencia de sesión o rol, o una consulta armada con texto del usuario,
esta prueba falla.
"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import conexion
from database.modelos import Usuario
from herramientas import auditoria_seguridad as auditoria


@pytest.fixture()
def app():
    """Fixture: la aplicación FastAPI de main.py (para leer su OpenAPI y sus dependencias)."""
    from main import app

    return app


def _aislar_google_tokeninfo(monkeypatch):
    """Configura GOOGLE_CLIENT_ID y simula rechazo 400 de Google sin salir a internet durante el fuzzing."""
    import urllib.error
    from api.plataforma import auth

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "amatista-seguridad-test.apps.googleusercontent.com")

    def _rechazar_token_invalido(req, timeout=6):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(auth.urllib.request, "urlopen", _rechazar_token_invalido)


def test_ninguna_ruta_cae_con_inyeccion_sql_xss_o_recorrido(cliente, crear_cuenta, app, monkeypatch):
    """Comprueba que ninguna ruta responde 500 ni filtra errores internos con cargas de inyección, XSS o recorrido."""
    _aislar_google_tokeninfo(monkeypatch)
    _, admin = crear_cuenta(rol="admin")
    with Session(conexion.motor()) as db:
        usuarios_antes = db.scalar(select(func.count()).select_from(Usuario))
    informe = auditoria.atacar(cliente, app.openapi(), admin)
    assert informe.rutas >= 60
    assert informe.peticiones >= informe.rutas * len(auditoria.TODAS)
    assert [h.__dict__ for h in informe.graves] == []
    # Ninguna carga borró tablas ni filas: la base sigue respondiendo igual.
    with Session(conexion.motor()) as db:
        assert db.scalar(select(func.count()).select_from(Usuario)) >= usuarios_antes
    assert cliente.get("/api/salud").status_code == 200


def test_inyeccion_a_ciegas_por_tiempo(cliente, crear_cuenta, app, monkeypatch):
    """Comprueba que ninguna carga de inyección por tiempo hace tardar la respuesta."""
    _aislar_google_tokeninfo(monkeypatch)
    _, admin = crear_cuenta(rol="admin")
    informe = auditoria.atacar_por_tiempo(cliente, app.openapi(), admin, umbral_s=3.0)
    assert [h.__dict__ for h in informe.graves] == []


def test_inyeccion_en_el_inicio_de_sesion(cliente, crear_cuenta):
    """Comprueba que las inyecciones clásicas no abren sesión en /api/auth/iniciar-sesion."""
    crear_cuenta(rol="admin", email="admin@amatista.local")
    assert auditoria.inicio_de_sesion_con_inyeccion(cliente) == []


def test_matriz_de_permisos(cliente, crear_cuenta, app):
    """Comprueba la matriz de permisos: sin sesión 401/403 y un alumno 403 en rutas de rol."""
    _, alumno = crear_cuenta(rol="alumno")
    filas, hallazgos = auditoria.probar_permisos(cliente, app, alumno)
    assert [h.__dict__ for h in hallazgos] == []
    requisitos = {(f["metodo"], f["ruta"]): f["requisito"] for f in filas}
    # Todo lo de administración y autoría exige rol; nada de eso es público.
    for (metodo, ruta), requisito in requisitos.items():
        if ruta.startswith("/api/admin/") or (ruta.startswith("/api/contenido/") and ruta != "/api/contenido/catalogo"):
            assert requisito == "rol", f"{metodo} {ruta} debería exigir rol"
    # Las únicas rutas públicas son estas: una ruta pública nueva debe agregarse aquí a propósito.
    publicas = sorted(f"{m} {r}" for (m, r), q in requisitos.items() if q == "publica")
    esperadas = [
        "GET /", "GET /api/salud", "GET /api/apis", "GET /api/g7b/v1/estado",
        "POST /api/auth/registro", "POST /api/auth/iniciar-sesion", "POST /api/auth/google", "POST /api/auth/confirmar-correo",
        "POST /api/auth/reenviar-codigo", "POST /api/auth/recuperar", "POST /api/auth/restablecer",
        "POST /api/iniciar-sesion", "POST /api/progreso", "GET /api/progreso/{usuario_id}", "POST /api/eventos",
        "POST /api/sincronizar",
        "GET /api/contenido/catalogo", "GET /api/blender/versiones",
        "GET /api/addon/v1/estado", "POST /api/addon/v1/vinculos", "POST /api/addon/v1/vinculos/{vinculo_id}/estado",
        "GET /api/addon/v1/practicas", "GET /api/addon/v1/practicas/{practica_id}",
        "GET /api/addon/v1/descargas/{sistema}", "GET /api/addon/v1/extension.zip",
        "GET /api/addon/v1/extensiones/index.json",
        "GET /api/addon/v1/instaladores/{sistema}",
    ]
    if any(r.startswith("/api/nube/") for (_, r) in requisitos):
        esperadas.append("GET /api/nube/v1/estado")
    assert publicas == sorted(esperadas)


def test_un_alumno_no_lee_el_progreso_de_otro(cliente, crear_cuenta):
    """Comprueba que un alumno no lee el progreso de otra cuenta (403) y sin sesión pide iniciar (401)."""
    otro, _ = crear_cuenta(rol="alumno")
    _, alumno = crear_cuenta(rol="alumno")
    assert cliente.get(f"/api/progreso/{otro}", headers=alumno).status_code == 403
    assert cliente.get(f"/api/progreso/{otro}").status_code == 401


def test_cabeceras_de_seguridad(cliente):
    """Comprueba las cabeceras de seguridad y Cache-Control: no-store en rutas privadas."""
    assert auditoria.revisar_cabeceras(cliente) == []
    privada = cliente.post("/api/auth/iniciar-sesion", json={"email": "x@amatista.local", "password": "x"})
    assert privada.headers["cache-control"] == "no-store"


def test_cuerpo_demasiado_grande(cliente):
    """Comprueba que un cuerpo de más de 2 MB responde 413."""
    enorme = b"{" + b" " * (3 * 1024 * 1024) + b"}"
    respuesta = cliente.post("/api/eventos", content=enorme, headers={"Content-Type": "application/json"})
    assert respuesta.status_code == 413


def test_documentacion_oculta_en_produccion(monkeypatch, tmp_path):
    """Comprueba que con AMATISTA_OCULTAR_DOCS=1 no se publican /docs ni /openapi.json."""
    import importlib

    import main

    monkeypatch.setenv("AMATISTA_OCULTAR_DOCS", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'docs.db'}")
    conexion.motor.cache_clear()
    from fastapi.testclient import TestClient

    oculto = importlib.reload(main)
    try:
        with TestClient(oculto.app) as c:
            assert c.get("/docs").status_code == 404
            assert c.get("/openapi.json").status_code == 404
            assert c.get("/api/salud").status_code == 200
    finally:
        monkeypatch.delenv("AMATISTA_OCULTAR_DOCS")
        importlib.reload(main)
        conexion.motor.cache_clear()


def test_limite_por_cuenta_no_frena_a_un_aula(cliente, crear_cuenta, monkeypatch):
    """Dos alumnos detrás de la misma IP tienen cada uno su cupo de intentos."""
    from api import limites

    monkeypatch.delenv("AMATISTA_SIN_LIMITES", raising=False)
    limites.reiniciar_limites()
    try:
        _, ana = crear_cuenta()
        _, luis = crear_cuenta()
        verificar = limites.limitar(2, por="cuenta")

        class Peticion:
            """Petición mínima falsa (cabeceras, IP compartida y ruta) para llamar al limitador directamente."""
            def __init__(self, cabeceras):
                """Arma la petición con las cabeceras de la cuenta y la misma IP de aula para todas."""
                self.headers = {k.lower(): v for k, v in cabeceras.items()}
                self.client = type("C", (), {"host": "10.0.0.1"})()
                self.url = type("U", (), {"path": "/api/addon/v1/intentos"})()

        verificar(Peticion(ana))
        verificar(Peticion(ana))
        with pytest.raises(Exception) as error:
            verificar(Peticion(ana))
        assert getattr(error.value, "status_code", None) == 429
        verificar(Peticion(luis))  # misma IP, otra cuenta: pasa
    finally:
        limites.reiniciar_limites()


def test_cuerpo_demasiado_grande_sin_content_length(cliente):
    """Un cuerpo «chunked» (sin Content-Length) también se corta al pasar el límite, sin leerlo entero."""
    enorme = b'{"dispositivo":"' + b"a" * (3 * 1024 * 1024) + b'"}'

    def partes():
        """Entrega el cuerpo enorme en trozos de 64 KiB (sin Content-Length)."""
        for i in range(0, len(enorme), 64 * 1024):
            yield enorme[i:i + 64 * 1024]

    respuesta = cliente.post("/api/addon/v1/vinculos", content=partes(), headers={"Content-Type": "application/json"})
    assert respuesta.status_code == 413

    def chico():
        """Entrega un cuerpo pequeño en dos trozos (sin Content-Length)."""
        yield b'{"dispositivo":'
        yield b'"Blender"}'

    assert cliente.post("/api/addon/v1/vinculos", content=chico(),
                        headers={"Content-Type": "application/json"}).status_code == 201
