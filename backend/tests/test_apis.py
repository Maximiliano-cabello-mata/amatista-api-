"""Cada API tiene su responsabilidad (api/registro.py) y firma sus errores (api/errores.py)."""
import pytest
from fastapi import APIRouter

import main
from api import errores, registro


@pytest.mark.parametrize("ruta, api", [
    ("/api/addon/v1/estado", "motor"),
    ("/api/addon/v1/practicas/blender.bp.m1.tren", "motor"),
    ("/api/evaluacion/grupos", "aula"),
    ("/api/admin/usuarios", "administracion"),
    ("/api/g7b/v1/estado", "datos"),
    ("/api/auth/iniciar-sesion", "plataforma"),
    ("/api/progreso", "plataforma"),
    ("/api/sincronizar", "plataforma"),
    ("/api/contenido/cursos", "plataforma"),
    ("/api/salud", "sistema"),
    ("/api/apis", "sistema"),
    ("/", "sistema"),
    ("/api/addonx", "plataforma"),  # un prefijo parecido no es del motor
])
def test_cada_ruta_pertenece_a_una_api(ruta, api):
    assert registro.api_de(ruta) == api


def test_toda_ruta_publicada_tiene_api():
    """Ninguna ruta publicada queda fuera de las API declaradas."""
    caminos = [c for c in main.app.openapi()["paths"] if c.startswith("/api/")]
    assert len(caminos) > 50
    for camino in caminos:
        if camino not in ("/api/salud", "/api/apis"):
            assert registro.api_de(camino) != registro.SISTEMA, camino


def test_los_modulos_viven_en_el_paquete_de_su_api():
    for api in registro.APIS:
        for router in api.routers():
            assert isinstance(router, APIRouter)
            modulos = {ruta.endpoint.__module__ for ruta in router.routes}
            assert all(m.startswith(f"api.{api.nombre}.") for m in modulos), (api.nombre, modulos)
            assert all(registro.api_de(ruta.path) == api.nombre for ruta in router.routes), api.nombre


def test_resumen_de_las_apis(cliente):
    datos = cliente.get("/api/apis").json()
    nombres = [a["api"] for a in datos["apis"]]
    assert nombres == ["plataforma", "motor", "aula", "administracion", "datos"]
    assert all(a["operaciones"] > 0 for a in datos["apis"])
    assert datos["motor"]["version_motor"] and datos["motor"]["version_addon"]


def test_un_error_dice_de_que_api_y_operacion_sale(cliente):
    r = cliente.get("/api/addon/v1/yo")  # sin token del complemento
    assert r.status_code == 401
    cuerpo = r.json()
    assert isinstance(cuerpo["detail"], str)  # el mensaje de siempre no cambia
    assert cuerpo["error"]["api"] == "motor"
    assert cuerpo["error"]["estado"] == 401
    assert cuerpo["error"]["codigo"] == f"motor.{cuerpo['error']['operacion']}.sin_sesion"
    assert r.headers[errores.CABECERA]


def test_cada_api_firma_sus_errores(cliente):
    casos = {
        "/api/evaluacion/grupos": "aula",
        "/api/admin/usuarios": "administracion",
        "/api/auth/yo": "plataforma",
    }
    for ruta, api in casos.items():
        r = cliente.get(ruta)
        assert r.status_code in (401, 403), ruta
        assert r.json()["error"]["api"] == api, ruta


def test_validacion_conserva_el_detalle_de_fastapi(cliente):
    r = cliente.post("/api/auth/iniciar-sesion", json={})
    assert r.status_code == 422
    cuerpo = r.json()
    assert isinstance(cuerpo["detail"], list) and cuerpo["detail"][0]["type"] == "missing"
    assert cuerpo["error"]["codigo"].endswith(".datos_invalidos")
    assert cuerpo["error"]["api"] == "plataforma"


def test_ruta_inexistente(cliente):
    r = cliente.get("/api/addon/v1/no-existe")
    assert r.status_code == 404
    assert r.json()["error"] == {"api": "motor", "operacion": None, "codigo": "motor.no_encontrado", "estado": 404}


def test_las_respuestas_correctas_tambien_llevan_identificador(cliente):
    r = cliente.get("/api/salud")
    assert r.status_code == 200 and len(r.headers[errores.CABECERA]) == 12


def test_error_no_controlado_devuelve_el_identificador(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from database import conexion

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'prueba.db'}")
    conexion.motor.cache_clear()

    @main.app.get("/api/addon/v1/_prueba_fallo")
    def _prueba_fallo():
        raise RuntimeError("secreto interno que no debe salir")

    try:
        with TestClient(main.app, raise_server_exceptions=False) as cliente:
            r = cliente.get("/api/addon/v1/_prueba_fallo")
        assert r.status_code == 500
        cuerpo = r.json()
        assert "secreto" not in r.text
        assert cuerpo["error"]["codigo"] == "motor._prueba_fallo.error_interno"
        assert cuerpo["error"]["peticion"] == r.headers[errores.CABECERA]
        assert cuerpo["error"]["peticion"] in cuerpo["detail"]
    finally:
        main.app.router.routes[:] = [r for r in main.app.router.routes if getattr(r, "path", "") != "/api/addon/v1/_prueba_fallo"]
        conexion.motor.cache_clear()
