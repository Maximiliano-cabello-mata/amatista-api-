"""Enlace en vivo plataforma ↔ Blender (api/enlace.py, sql/010, motor 3.4).

El add-on manda latidos; la plataforma ve el Blender en vivo, le deja
órdenes (abrir una práctica, enfocar) y decide cómo se ve Blender.
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from database import conexion
from database.modelos import AddonEnlace, ahora
from tests.test_addon import API, sembrar, vincular  # noqa: F401  (fixture sembrar)


def latir(cliente, blender, **cuerpo):
    respuesta = cliente.post(f"{API}/enlace", json={"version_addon": "3.4.0", "version_blender": "5.0.1", **cuerpo},
                             headers=blender)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_sin_blender_abierto_la_plataforma_lo_sabe(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    datos = cliente.get(f"{API}/enlace", headers=alumno).json()
    assert datos["enlace"] is True and datos["en_linea"] is False and datos["blender"] == []
    assert datos["ajustes"]["enfoque"] == "auto"


def test_el_latido_hace_visible_a_blender(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    respuesta = latir(cliente, blender, practica_id="blender.bp.m1.tren", paso="ruedas", progreso=40, enfocado=True)
    assert respuesta["enlace"] and respuesta["intervalo"] == 5 and respuesta["orden"] is None
    datos = cliente.get(f"{API}/enlace", headers=alumno).json()
    assert datos["en_linea"] is True
    (b,) = datos["blender"]
    assert (b["practica_id"], b["paso"], b["progreso"], b["enfocado"]) == ("blender.bp.m1.tren", "ruedas", 40, True)
    assert b["version_addon"] == "3.4.0"


def test_blender_cerrado_deja_de_estar_en_linea(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender)
    with Session(conexion.motor()) as db:
        fila = db.query(AddonEnlace).one()
        fila.visto_en = ahora() - timedelta(minutes=2)
        db.commit()
    assert cliente.get(f"{API}/enlace", headers=alumno).json()["en_linea"] is False


def test_solo_el_add_on_late(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    assert cliente.post(f"{API}/enlace", json={}, headers=alumno).status_code == 403
    assert cliente.post(f"{API}/enlace", json={}).status_code == 401


def test_abrir_en_blender_llega_al_blender_abierto(cliente, crear_cuenta, sembrar):  # noqa: F811
    _, admin = crear_cuenta(rol="admin")
    sembrar(cliente, admin)
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender)
    abierta = cliente.post(f"{API}/practicas/blender.n1.mesa/abrir", json={"origen": "plataforma"}, headers=alumno)
    assert abierta.status_code == 200 and abierta.json()["abierta_en_blender"] is True
    assert cliente.get(f"{API}/enlace", headers=alumno).json()["blender"][0]["orden_pendiente"] == "abrir_practica"
    orden = latir(cliente, blender)["orden"]
    assert orden["tipo"] == "abrir_practica" and orden["datos"] == {"practica_id": "blender.n1.mesa"}
    # Hasta que Blender diga que la cumplió, la orden se repite (si un latido se pierde, no se pierde la orden).
    assert latir(cliente, blender)["orden"]["id"] == orden["id"]
    assert latir(cliente, blender, orden_hecha=orden["id"], practica_id="blender.n1.mesa")["orden"] is None
    assert cliente.get(f"{API}/enlace", headers=alumno).json()["blender"][0]["practica"] == "Construir una mesa"


def test_abrir_sin_blender_abierto_no_deja_orden(cliente, crear_cuenta, sembrar):  # noqa: F811
    _, admin = crear_cuenta(rol="admin")
    sembrar(cliente, admin)
    _, alumno = crear_cuenta()
    abierta = cliente.post(f"{API}/practicas/blender.n1.mesa/abrir", headers=alumno)
    assert abierta.status_code == 200 and abierta.json()["abierta_en_blender"] is False
    # Desde Blender (el alumno eligió otra práctica ahí) no se manda orden de vuelta.
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender)
    cliente.post(f"{API}/practicas/blender.n1.mesa/abrir", json={"origen": "blender"}, headers=blender)
    assert latir(cliente, blender)["orden"] is None


def test_ordenes_de_la_plataforma(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    assert cliente.post(f"{API}/ordenes", json={"tipo": "enfocar"}, headers=alumno).json()["entregada"] is False
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender)
    assert cliente.post(f"{API}/ordenes", json={"tipo": "ver_todo"}, headers=alumno).json()["entregada"] is True
    assert latir(cliente, blender)["orden"]["tipo"] == "ver_todo"
    assert cliente.post(f"{API}/ordenes", json={"tipo": "borrar_todo"}, headers=alumno).status_code == 422
    falta = cliente.post(f"{API}/ordenes", json={"tipo": "abrir_practica", "practica_id": "no.existe"}, headers=alumno)
    assert falta.status_code == 404


def test_la_orden_vieja_se_descarta(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender)
    cliente.post(f"{API}/ordenes", json={"tipo": "enfocar"}, headers=alumno)
    with Session(conexion.motor()) as db:
        fila = db.query(AddonEnlace).one()
        fila.orden_en = ahora() - timedelta(minutes=30)
        db.commit()
    assert latir(cliente, blender)["orden"] is None


def test_mi_blender_decide_como_se_ve_blender(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender)
    guardado = cliente.put(f"{API}/ajustes", json={"enfoque": "siempre", "acompanamiento": "tarjeta"}, headers=alumno)
    assert guardado.status_code == 200
    assert guardado.json()["ajustes"] == {"enfoque": "siempre", "acompanamiento": "tarjeta",
                                          "avisos_herramientas": True, "tarjeta_3d": True}
    # El Blender abierto recibe los ajustes y una orden de aplicarlos.
    respuesta = latir(cliente, blender)
    assert respuesta["ajustes"]["enfoque"] == "siempre" and respuesta["orden"]["tipo"] == "actualizar"
    # Solo cambia lo que se manda; lo inválido se rechaza; Blender no puede cambiarlos.
    cliente.put(f"{API}/ajustes", json={"tarjeta_3d": False}, headers=alumno)
    assert cliente.get(f"{API}/ajustes", headers=alumno).json()["ajustes"]["enfoque"] == "siempre"
    assert cliente.put(f"{API}/ajustes", json={"enfoque": "a veces"}, headers=alumno).status_code == 422
    assert cliente.put(f"{API}/ajustes", json={"enfoque": "nunca"}, headers=blender).status_code == 403


def test_cada_alumno_ve_solo_sus_blender(cliente, crear_cuenta):
    _, uno = crear_cuenta()
    _, otro = crear_cuenta()
    blender, _ = vincular(cliente, uno)
    latir(cliente, blender)
    assert cliente.get(f"{API}/enlace", headers=otro).json()["blender"] == []
    assert cliente.post(f"{API}/ordenes", json={"tipo": "enfocar"}, headers=otro).json()["entregada"] is False


def test_sin_010_todo_sigue_funcionando(cliente, crear_cuenta, sembrar):  # noqa: F811
    """Producción antes de ejecutar 010: sin enlace en vivo, pero nada se rompe."""
    from sqlalchemy import text

    _, admin = crear_cuenta(rol="admin")
    sembrar(cliente, admin)
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    with conexion.motor().begin() as c:
        c.execute(text("DROP TABLE addon_enlaces"))
        c.execute(text("DROP TABLE addon_ajustes"))
    assert latir(cliente, blender)["enlace"] is False
    assert cliente.get(f"{API}/enlace", headers=alumno).json() == {
        "enlace": False, "en_linea": False, "blender": [], "ajustes": {
            "enfoque": "auto", "acompanamiento": "acompanado", "avisos_herramientas": True, "tarjeta_3d": True}}
    abierta = cliente.post(f"{API}/practicas/blender.n1.mesa/abrir", headers=alumno)
    assert abierta.status_code == 200 and abierta.json()["abierta_en_blender"] is False
    # El avance de la práctica abierta se guarda igual (no se pierde con la tabla que falta).
    progreso = cliente.get(f"{API}/mi-progreso?practica_id=blender.n1.mesa", headers=alumno).json()
    assert progreso["practicas"] and progreso["practicas"][0]["practica_id"] == "blender.n1.mesa"
    assert cliente.put(f"{API}/ajustes", json={"enfoque": "nunca"}, headers=alumno).status_code == 503


# --- Motor 3.5: el instructor en vivo y más órdenes ---------------------------------------

DETALLE = {
    "titulo": "Forja la silueta: guarda, hoja y punta", "mensaje": "Falta la parte «Guarda»: …", "numero": 3,
    "total": 5, "figura": "Forja la silueta", "modo": "EDIT_MESH", "pistas": 2, "accion": "Entrar a Edición conmigo",
    "lista": [{"texto": "Mango", "ok": True, "estado": "Bien"},
              {"texto": "Guarda", "ok": False, "estado": "Falta", "consejo": "Con Ctrl + R haz un corte…"}],
}


def test_la_leccion_ve_lo_que_dice_el_instructor(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender, practica_id="blender.bp.m2.espada", paso="silueta", progreso=25, detalle=DETALLE)
    (b,) = cliente.get(f"{API}/enlace", headers=alumno).json()["blender"]
    assert b["detalle"]["titulo"].startswith("Forja") and b["detalle"]["lista"][1]["estado"] == "Falta"
    assert b["detalle"]["modo"] == "EDIT_MESH" and b["detalle"]["pistas"] == 2
    # Sin práctica abierta (o con un add-on 3.4 que no lo manda) no queda un detalle viejo.
    latir(cliente, blender)
    (b,) = cliente.get(f"{API}/enlace", headers=alumno).json()["blender"]
    assert b["detalle"] is None


def test_el_detalle_tiene_limites(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    largo = {**DETALLE, "mensaje": "x" * 600}
    respuesta = cliente.post(f"{API}/enlace", json={"practica_id": "p", "detalle": largo}, headers=blender)
    assert respuesta.status_code == 422
    muchos = {**DETALLE, "lista": [{"texto": "a"}] * 17}
    assert cliente.post(f"{API}/enlace", json={"practica_id": "p", "detalle": muchos}, headers=blender).status_code == 422


def test_la_plataforma_maneja_la_practica(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender, practica_id="blender.bp.m2.espada")
    for tipo in ("comprobar", "pista", "hazlo_conmigo", "guardar"):
        r = cliente.post(f"{API}/ordenes", json={"tipo": tipo, "practica_id": "blender.bp.m2.espada"}, headers=alumno)
        assert r.status_code == 200 and r.json()["entregada"], r.text
        orden = latir(cliente, blender, practica_id="blender.bp.m2.espada")["orden"]
        assert orden["tipo"] == tipo and orden["datos"] == {"practica_id": "blender.bp.m2.espada"}
        latir(cliente, blender, practica_id="blender.bp.m2.espada", orden_hecha=orden["id"])


def test_empezar_de_nuevo_pide_confirmacion(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender, practica_id="blender.bp.m2.espada")
    assert cliente.post(f"{API}/ordenes", json={"tipo": "reiniciar"}, headers=alumno).status_code == 400
    r = cliente.post(f"{API}/ordenes", json={"tipo": "reiniciar", "confirmar": True}, headers=alumno)
    assert r.status_code == 200 and r.json()["entregada"]
    assert latir(cliente, blender, practica_id="blender.bp.m2.espada")["orden"]["tipo"] == "reiniciar"
    assert cliente.post(f"{API}/ordenes", json={"tipo": "borrar_todo"}, headers=alumno).status_code == 422
