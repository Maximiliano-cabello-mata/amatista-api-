"""Avatares (api/perfil.py) y buzón del creador (api/buzon.py), sql/013."""
import re
from pathlib import Path

from api.perfil import AVATAR_CREADOR, AVATARES

CATALOGO_PWA = Path(__file__).resolve().parents[2] / "frontend" / "src" / "avatares" / "catalogo.js"


def test_los_avatares_son_los_mismos_que_en_la_pwa():
    ids = set(re.findall(r"\{\s*id:\s*'([a-z_]+)'", CATALOGO_PWA.read_text(encoding="utf-8")))
    assert ids == set(AVATARES) | {AVATAR_CREADOR}


# --- Avatar -----------------------------------------------------------------------


def test_un_alumno_elige_y_cambia_su_avatar(cliente, crear_cuenta):
    _, cabeceras = crear_cuenta()
    assert cliente.get("/api/perfil", headers=cabeceras).json() == {"avatar": None, "es_creador": False, "guardado": True}
    assert cliente.put("/api/perfil", json={"avatar": "ajolote"}, headers=cabeceras).json()["avatar"] == "ajolote"
    assert cliente.get("/api/perfil", headers=cabeceras).json()["avatar"] == "ajolote"
    assert cliente.put("/api/perfil", json={"avatar": "zorro"}, headers=cabeceras).status_code == 200
    assert cliente.put("/api/perfil", json={"avatar": "dragon"}, headers=cabeceras).status_code == 422
    assert cliente.get("/api/perfil").status_code == 401


def test_el_pavo_real_es_solo_del_creador(cliente, crear_cuenta, monkeypatch):
    monkeypatch.setenv("AMATISTA_CREADOR", "creador@amatista.local")
    _, alumno = crear_cuenta()
    _, otro_admin = crear_cuenta(rol="admin")
    _, creador = crear_cuenta(rol="admin", email="Creador@amatista.local")
    assert cliente.put("/api/perfil", json={"avatar": "pavo_real"}, headers=alumno).status_code == 403
    assert cliente.put("/api/perfil", json={"avatar": "pavo_real"}, headers=otro_admin).status_code == 403
    # El creador estrena su pavo real sin elegirlo.
    assert cliente.get("/api/perfil", headers=creador).json() == {"avatar": "pavo_real", "es_creador": True, "guardado": True}
    assert cliente.put("/api/perfil", json={"avatar": "pavo_real"}, headers=creador).status_code == 200


def test_sin_amatista_creador_el_creador_es_cualquier_admin(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    assert cliente.get("/api/perfil", headers=admin).json()["es_creador"] is True


# --- Buzón ------------------------------------------------------------------------


def test_conversacion_completa_con_el_creador(cliente, crear_cuenta):
    _, alumno = crear_cuenta(nombre="Ana")
    _, admin = crear_cuenta(rol="admin")
    cliente.put("/api/perfil", json={"avatar": "colibri"}, headers=alumno)

    r = cliente.post("/api/buzon", json={"categoria": "error", "texto": "El botón de A-Frame no carga", "pagina": "#/estudio"},
                     headers=alumno)
    assert r.status_code == 201
    conversacion = r.json()
    assert conversacion["estado"] == "abierta" and len(conversacion["mensajes"]) == 1

    bandeja = cliente.get("/api/buzon/bandeja", headers=admin).json()
    assert bandeja["sin_leer"] == 1
    assert bandeja["conversaciones"][0]["alumno"]["nombre"] == "Ana"
    assert bandeja["conversaciones"][0]["alumno"]["avatar"] == "colibri"

    r = cliente.post(f"/api/buzon/bandeja/{conversacion['id']}/responder", json={"texto": "¡Gracias! Ya lo arreglo."},
                     headers=admin)
    assert r.status_code == 201 and r.json()["estado"] == "respondida"

    mias = cliente.get("/api/buzon", headers=alumno).json()["conversaciones"]
    assert mias[0]["sin_leer"] is True
    assert [m["de_creador"] for m in mias[0]["mensajes"]] == [False, True]
    cliente.post(f"/api/buzon/{conversacion['id']}/leida", headers=alumno)
    assert cliente.get("/api/buzon", headers=alumno).json()["conversaciones"][0]["sin_leer"] is False

    # El alumno contesta: la conversación vuelve a estar abierta para el creador.
    r = cliente.post(f"/api/buzon/{conversacion['id']}/mensajes", json={"texto": "Ya funciona, gracias"}, headers=alumno)
    assert r.json()["estado"] == "abierta"
    r = cliente.post(f"/api/buzon/bandeja/{conversacion['id']}/estado", json={"estado": "resuelta"}, headers=admin)
    assert r.json()["estado"] == "resuelta"


def test_el_buzon_protege_cada_conversacion(cliente, crear_cuenta):
    _, ana = crear_cuenta()
    _, beto = crear_cuenta()
    id_ana = cliente.post("/api/buzon", json={"categoria": "idea", "texto": "Más lecciones de luz"}, headers=ana).json()["id"]
    assert cliente.post(f"/api/buzon/{id_ana}/mensajes", json={"texto": "hola hola"}, headers=beto).status_code == 404
    assert cliente.get("/api/buzon", headers=beto).json()["conversaciones"] == []
    assert cliente.get("/api/buzon/bandeja", headers=ana).status_code == 403
    assert cliente.post(f"/api/buzon/bandeja/{id_ana}/responder", json={"texto": "yo no soy"}, headers=beto).status_code == 403


def test_el_buzon_valida_lo_que_llega(cliente, crear_cuenta):
    _, ana = crear_cuenta()
    assert cliente.post("/api/buzon", json={"categoria": "queja", "texto": "algo"}, headers=ana).status_code == 422
    assert cliente.post("/api/buzon", json={"categoria": "idea", "texto": "  "}, headers=ana).status_code == 422
    assert cliente.post("/api/buzon", json={"categoria": "idea", "texto": "x" * 2001}, headers=ana).status_code == 422
    assert cliente.post("/api/buzon", json={"categoria": "idea", "texto": "Sin cuenta"}).status_code == 401
