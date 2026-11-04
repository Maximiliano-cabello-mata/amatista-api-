"""Amatista Cloud (api/nube.py, sql/014): solo UTN, una hora por token, cuota y protocolo de nodos."""
import hashlib
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from api.nube import correo_permitido, minutos_sesion, reiniciar_nube
from database import conexion
from database.modelos import NubeSesion, Usuario, ahora

TOKEN_NODO = "token-del-nodo-casa-para-pruebas-0123456789"
NODO = {"Authorization": f"Bearer {TOKEN_NODO}"}
URL_NODO = "http://localhost:8730"


@pytest.fixture(autouse=True)
def nube(monkeypatch):
    monkeypatch.setenv("AMATISTA_NUBE_NODOS", "casa:" + hashlib.sha256(TOKEN_NODO.encode()).hexdigest())
    monkeypatch.setenv("AMATISTA_NUBE_DOMINIOS", "utn.mx")
    monkeypatch.delenv("AMATISTA_NUBE_MINUTOS", raising=False)
    monkeypatch.delenv("AMATISTA_NUBE_POR_DIA", raising=False)
    reiniciar_nube()
    yield
    reiniciar_nube()


def latir(cliente, capacidad=2, sesiones=(), url=URL_NODO, cabeceras=NODO):
    return cliente.post("/api/nube/v1/nodo/latido", headers=cabeceras,
                        json={"url": url, "capacidad": capacidad, "sesiones": list(sesiones), "arquitectura": "x86_64",
                              "nucleos": 8, "ram_gb": 40})


def test_dominios_permitidos():
    assert correo_permitido("ana@utn.mx")
    assert correo_permitido("ana@alumno.utn.mx")
    assert not correo_permitido("ana@xutn.mx")
    assert not correo_permitido("ana@utn.mx.otro.com")
    assert not correo_permitido("ana@gmail.com")
    assert not correo_permitido(None)


def test_la_sesion_dura_al_menos_una_hora(monkeypatch):
    monkeypatch.setenv("AMATISTA_NUBE_MINUTOS", "10")
    assert minutos_sesion() == 60
    monkeypatch.setenv("AMATISTA_NUBE_MINUTOS", "90")
    assert minutos_sesion() == 90


def test_solo_cuentas_de_la_utn_con_correo_confirmado(cliente, crear_cuenta):
    latir(cliente)
    _, gmail = crear_cuenta(email="ana@gmail.com")
    estado = cliente.get("/api/nube/v1/estado", headers=gmail).json()
    assert estado["puede"] is False and "UTN" in estado["motivo"]
    assert cliente.post("/api/nube/v1/sesiones", headers=gmail).status_code == 403

    usuario_id, utn = crear_cuenta(email="beto@utn.mx")
    with Session(conexion.motor()) as db:
        db.get(Usuario, usuario_id).correo_confirmado = 0
        db.commit()
    r = cliente.post("/api/nube/v1/sesiones", headers=utn)
    assert r.status_code == 403 and "Confirma" in r.json()["detail"]

    assert cliente.post("/api/nube/v1/sesiones").status_code == 401
    anonimo = cliente.get("/api/nube/v1/estado").json()
    assert anonimo["puede"] is False and anonimo["libres"] == 2 and anonimo["minutos"] == 60


def test_sin_nodos_no_hay_lugar(cliente, crear_cuenta):
    _, utn = crear_cuenta(email="ana@utn.mx")
    assert cliente.get("/api/nube/v1/estado", headers=utn).json()["puede"] is True
    r = cliente.post("/api/nube/v1/sesiones", headers=utn)
    assert r.status_code == 409 and "ocupados" in r.json()["detail"] and r.headers["retry-after"] == "120"


def test_el_nodo_necesita_su_token_y_una_url_segura(cliente):
    assert latir(cliente, cabeceras={"Authorization": "Bearer otro"}).status_code == 401
    assert latir(cliente, cabeceras={}).status_code == 401
    assert latir(cliente, url="http://granja.example.com").status_code == 422
    assert latir(cliente, url="https://nodo-casa.amatista-3d.tech").status_code == 200


def test_recorrido_completo_de_una_sesion(cliente, crear_cuenta):
    latir(cliente, capacidad=2)
    _, utn = crear_cuenta(email="ana@utn.mx")

    r = cliente.post("/api/nube/v1/sesiones", headers=utn)
    assert r.status_code == 201
    sesion = r.json()
    assert sesion["estado"] == "asignada" and sesion["token"] and sesion["entrada"] is None
    assert 3590 <= sesion["segundos_restantes"] <= 3600
    # Una sesión viva por cuenta.
    assert cliente.post("/api/nube/v1/sesiones", headers=utn).status_code == 409

    # El nodo se la lleva en su siguiente latido, con el hash del token (nunca el token).
    trabajo = latir(cliente).json()
    assert [a["id"] for a in trabajo["abrir"]] == [sesion["id"]]
    assert trabajo["abrir"][0]["token_hash"] == hashlib.sha256(sesion["token"].encode()).hexdigest()
    assert sesion["token"] not in str(trabajo)
    assert trabajo["abrir"][0]["minutos"] == 60

    assert cliente.post(f"/api/nube/v1/nodo/sesiones/{sesion['id']}", headers=NODO, json={"estado": "lista"}).json()["estado"] == "lista"
    actual = cliente.get("/api/nube/v1/sesiones/actual", headers=utn).json()["sesion"]
    assert actual["estado"] == "lista" and actual["entrada"] == f"{URL_NODO}/entrar" and "token" not in actual
    assert latir(cliente, sesiones=[sesion["id"]]).json()["abrir"] == []
    assert cliente.get("/api/nube/v1/estado").json()["libres"] == 1

    # Otro dispositivo: token nuevo, el nodo recibe el hash nuevo.
    nuevo = cliente.post(f"/api/nube/v1/sesiones/{sesion['id']}/token", headers=utn).json()["token"]
    assert nuevo != sesion["token"]
    tokens = latir(cliente, sesiones=[sesion["id"]]).json()["tokens"]
    assert tokens == [{"id": sesion["id"], "token_hash": hashlib.sha256(nuevo.encode()).hexdigest()}]

    # Terminar: el nodo recibe la orden de cerrarla hasta que la suelte.
    assert cliente.post(f"/api/nube/v1/sesiones/{sesion['id']}/terminar", headers=utn).json()["estado"] == "terminada"
    assert latir(cliente, sesiones=[sesion["id"]]).json()["cerrar"] == [sesion["id"]]
    assert latir(cliente, sesiones=[sesion["id"]]).json()["cerrar"] == [sesion["id"]]
    assert latir(cliente, sesiones=[]).json()["cerrar"] == []
    assert cliente.get("/api/nube/v1/sesiones/actual", headers=utn).json()["sesion"] is None


def test_cuota_diaria(cliente, crear_cuenta, monkeypatch):
    monkeypatch.setenv("AMATISTA_NUBE_POR_DIA", "1")
    latir(cliente)
    _, utn = crear_cuenta(email="ana@utn.mx")
    sesion = cliente.post("/api/nube/v1/sesiones", headers=utn).json()
    cliente.post(f"/api/nube/v1/sesiones/{sesion['id']}/terminar", headers=utn)
    r = cliente.post("/api/nube/v1/sesiones", headers=utn)
    assert r.status_code == 429 and "mañana" in r.json()["detail"]
    # Los administradores prueban sin cuota y sin importar el dominio.
    _, admin = crear_cuenta(rol="admin", email="equipo@amatista.local")
    for _ in range(2):
        s = cliente.post("/api/nube/v1/sesiones", headers=admin).json()
        cliente.post(f"/api/nube/v1/sesiones/{s['id']}/terminar", headers=admin)


def test_al_vencer_la_hora_se_cierra_sola(cliente, crear_cuenta):
    latir(cliente)
    _, utn = crear_cuenta(email="ana@utn.mx")
    sesion = cliente.post("/api/nube/v1/sesiones", headers=utn).json()
    cliente.post(f"/api/nube/v1/nodo/sesiones/{sesion['id']}", headers=NODO, json={"estado": "lista"})
    with Session(conexion.motor()) as db:
        db.get(NubeSesion, sesion["id"]).expira_en = ahora() - timedelta(seconds=1)
        db.commit()
    assert latir(cliente, sesiones=[sesion["id"]]).json()["cerrar"] == [sesion["id"]]
    with Session(conexion.motor()) as db:
        fila = db.get(NubeSesion, sesion["id"])
        assert fila.estado == "terminada" and fila.motivo_fin == "tiempo" and fila.terminada_en is not None
        assert fila.token_hash != sesion["token"]


def test_si_el_nodo_falla_la_sesion_no_cuenta_para_la_cuota(cliente, crear_cuenta, monkeypatch):
    monkeypatch.setenv("AMATISTA_NUBE_POR_DIA", "1")
    latir(cliente)
    _, utn = crear_cuenta(email="ana@utn.mx")
    sesion = cliente.post("/api/nube/v1/sesiones", headers=utn).json()
    cliente.post(f"/api/nube/v1/nodo/sesiones/{sesion['id']}", headers=NODO, json={"estado": "fallida", "motivo": "sin imagen"})
    assert cliente.post("/api/nube/v1/sesiones", headers=utn).status_code == 201


def test_un_nodo_no_toca_sesiones_de_otro(cliente, crear_cuenta, monkeypatch):
    otro = "token-del-nodo-oracle-para-pruebas-987654"
    monkeypatch.setenv("AMATISTA_NUBE_NODOS", ",".join([
        "casa:" + hashlib.sha256(TOKEN_NODO.encode()).hexdigest(),
        "oracle:" + hashlib.sha256(otro.encode()).hexdigest(),
    ]))
    latir(cliente, capacidad=1)
    _, utn = crear_cuenta(email="ana@utn.mx")
    sesion = cliente.post("/api/nube/v1/sesiones", headers=utn).json()
    assert sesion["nodo"] == "casa"
    r = cliente.post(f"/api/nube/v1/nodo/sesiones/{sesion['id']}", headers={"Authorization": f"Bearer {otro}"}, json={"estado": "lista"})
    assert r.status_code == 404


def test_panel_del_equipo(cliente, crear_cuenta):
    latir(cliente, capacidad=4)
    _, utn = crear_cuenta(email="ana@utn.mx")
    _, admin = crear_cuenta(rol="admin")
    sesion = cliente.post("/api/nube/v1/sesiones", headers=utn).json()
    assert cliente.get("/api/nube/v1/admin", headers=utn).status_code == 403
    panel = cliente.get("/api/nube/v1/admin", headers=admin).json()
    assert panel["nodos"][0]["nombre"] == "casa" and panel["nodos"][0]["capacidad"] == 4
    assert panel["vivas"][0]["correo"] == "ana@utn.mx" and "token" not in panel["vivas"][0]
    assert cliente.post(f"/api/nube/v1/admin/sesiones/{sesion['id']}/terminar", headers=admin).json()["estado"] == "terminada"
