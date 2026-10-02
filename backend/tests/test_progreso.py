"""/api/progreso: identidad, upsert monotónico, datos_ligeros, completada_en e insignias."""
import json
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import conexion
from database.modelos import Logro, ProgresoLeccion, Usuario, ahora

ANONIMO = "alumno-0f0e0d0c-0000-4000-8000-000000000001"


# --- Ayudantes ----------------------------------------------------------------


def evento(leccion_id="les_001", curso_id="blender", **campos):
    return {"curso_id": curso_id, "leccion_id": leccion_id, **campos}


def enviar(cliente, eventos, usuario_id=ANONIMO, cabeceras=None, **extra):
    cuerpo = {"eventos": eventos, **extra}
    if usuario_id is not None:
        cuerpo["usuario_id"] = usuario_id
    return cliente.post("/api/progreso", json=cuerpo, headers=cabeceras or {})


def fila(usuario_id, leccion_id="les_001", curso_id="blender"):
    with Session(conexion.motor()) as db:
        return db.get(ProgresoLeccion, (usuario_id, curso_id, leccion_id))


def contar(modelo, *condiciones):
    with Session(conexion.motor()) as db:
        return db.scalar(select(func.count()).select_from(modelo).where(*condiciones))


def lecciones(cliente, usuario_id=ANONIMO, cabeceras=None):
    respuesta = cliente.get(f"/api/progreso/{usuario_id}", headers=cabeceras or {})
    assert respuesta.status_code == 200, respuesta.text
    return {f["leccion_id"]: f for f in respuesta.json()["lecciones"]}


# --- Identidad ------------------------------------------------------------------


def test_anonimo_escribe_y_lee_su_progreso(cliente):
    respuesta = enviar(cliente, [evento(completada=True, puntaje=80, intentos=1)], insignias=["blender:mod_teoria_001"])
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"guardados": 1, "insignias": 1}

    with Session(conexion.motor()) as db:
        usuario = db.get(Usuario, ANONIMO)
        assert usuario is not None and usuario.password_hash is None  # asegurar_usuario lo creó
        assert usuario.ultimo_acceso is not None

    datos = cliente.get(f"/api/progreso/{ANONIMO}").json()
    assert datos["usuario_id"] == ANONIMO
    assert [i["id"] for i in datos["insignias"]] == ["blender:mod_teoria_001"]
    (leccion,) = datos["lecciones"]
    assert leccion["curso_id"] == "blender" and leccion["leccion_id"] == "les_001"
    assert (leccion["completada"], leccion["puntaje"], leccion["intentos"]) == (True, 80, 1)
    assert leccion["datos_ligeros"] is None
    assert leccion["completada_en"] and leccion["actualizado_en"]


def test_progreso_de_anonimo_sin_filas(cliente):
    assert cliente.get("/api/progreso/alumno-que-no-existe").json() == {
        "usuario_id": "alumno-que-no-existe",
        "lecciones": [],
        "insignias": [],
    }


def test_sin_usuario_ni_sesion(cliente):
    assert cliente.post("/api/progreso", json={"eventos": [evento()]}).status_code == 422
    assert enviar(cliente, [evento()], usuario_id="   ").status_code == 422


def test_cuenta_registrada_sin_token_responde_401(cliente, crear_cuenta):
    cuenta_id, _ = crear_cuenta()
    assert enviar(cliente, [evento(completada=True)], usuario_id=cuenta_id).status_code == 401
    assert fila(cuenta_id) is None
    assert cliente.get(f"/api/progreso/{cuenta_id}").status_code == 401


def test_token_invalido_responde_401(cliente):
    cabeceras = {"Authorization": "Bearer token-que-no-existe"}
    assert enviar(cliente, [evento()], cabeceras=cabeceras).status_code == 401
    assert cliente.get("/api/progreso", headers=cabeceras).status_code == 401


def test_con_token_se_escribe_en_la_cuenta_de_la_sesion(cliente, crear_cuenta):
    a_id, a = crear_cuenta()
    b_id, _ = crear_cuenta()
    respuesta = enviar(cliente, [evento(completada=True)], usuario_id=b_id, cabeceras=a)
    assert respuesta.status_code == 200, respuesta.text
    assert fila(a_id).completada == 1
    assert fila(b_id) is None
    # Sin usuario_id también funciona: la identidad sale del token.
    assert enviar(cliente, [evento("les_002")], usuario_id=None, cabeceras=a).status_code == 200
    assert fila(a_id, "les_002") is not None


def test_get_mi_progreso_requiere_sesion(cliente, crear_cuenta):
    assert cliente.get("/api/progreso").status_code == 401
    cuenta_id, cabeceras = crear_cuenta()
    enviar(cliente, [evento(completada=True)], usuario_id=None, cabeceras=cabeceras, insignias=["aframe:mod_af_001"])
    datos = cliente.get("/api/progreso", headers=cabeceras).json()
    assert datos["usuario_id"] == cuenta_id
    assert [f["leccion_id"] for f in datos["lecciones"]] == ["les_001"]
    assert [i["id"] for i in datos["insignias"]] == ["aframe:mod_af_001"]


def test_permisos_para_leer_el_progreso_de_otro(cliente, crear_cuenta):
    alumno_id, alumno = crear_cuenta()
    _, otro = crear_cuenta()
    _, profesor = crear_cuenta(rol="profesor")
    _, admin = crear_cuenta(rol="admin")
    enviar(cliente, [evento(completada=True)], usuario_id=None, cabeceras=alumno)

    assert cliente.get(f"/api/progreso/{alumno_id}", headers=alumno).status_code == 200
    assert cliente.get(f"/api/progreso/{alumno_id}", headers=otro).status_code == 403
    assert cliente.get(f"/api/progreso/{alumno_id}").status_code == 401
    for cabeceras in (profesor, admin):
        respuesta = cliente.get(f"/api/progreso/{alumno_id}", headers=cabeceras)
        assert respuesta.status_code == 200
        assert [f["leccion_id"] for f in respuesta.json()["lecciones"]] == ["les_001"]
    # Un anónimo se puede leer con su id aunque quien pregunta tenga sesión.
    enviar(cliente, [evento()])
    assert cliente.get(f"/api/progreso/{ANONIMO}", headers=otro).status_code == 200


def test_anonimo_fusionado_ya_no_acepta_escrituras(cliente, crear_cuenta):
    cuenta_id, _ = crear_cuenta()
    enviar(cliente, [evento()])
    with Session(conexion.motor()) as db:
        db.get(Usuario, ANONIMO).fusionado_en = cuenta_id
        db.commit()
    assert enviar(cliente, [evento("les_002")]).status_code == 401


# --- Reglas del upsert --------------------------------------------------------------


def test_progreso_nunca_retrocede(cliente):
    for cambios in (
        {"completada": False, "puntaje": 67, "intentos": 1},
        {"completada": True, "puntaje": 100, "intentos": 2},
        {"completada": False, "puntaje": 33, "intentos": 3},  # un intento peor no borra el avance
        {"completada": False, "intentos": 0},
    ):
        assert enviar(cliente, [evento(**cambios)]).status_code == 200
    leccion = lecciones(cliente)["les_001"]
    assert (leccion["completada"], leccion["puntaje"], leccion["intentos"]) == (True, 100, 3)


def test_lote_combina_eventos_de_la_misma_leccion(cliente):
    respuesta = enviar(
        cliente,
        [
            evento(completada=True, puntaje=40, datos_ligeros={"a": 1}),
            evento(completada=False, puntaje=90, intentos=4, datos_ligeros={"p": 2}),
            evento("les_002"),
        ],
    )
    assert respuesta.json()["guardados"] == 2
    leccion = lecciones(cliente)["les_001"]
    assert (leccion["completada"], leccion["puntaje"], leccion["intentos"]) == (True, 90, 4)
    assert leccion["datos_ligeros"] == {"a": 1, "p": 2}


def test_misma_leccion_en_dos_cursos_son_filas_distintas(cliente):
    enviar(cliente, [evento("les_001", "blender", completada=True), evento("les_001", "aframe")])
    assert fila(ANONIMO, "les_001", "blender").completada == 1
    assert fila(ANONIMO, "les_001", "aframe").completada == 0
    enviar(cliente, [evento("les_001", "aframe", puntaje=50)])
    assert fila(ANONIMO, "les_001", "blender").puntaje is None
    assert fila(ANONIMO, "les_001", "aframe").puntaje == 50


def test_lote_grande(cliente):
    eventos = [evento(f"les_{i:03d}", completada=i % 2 == 0) for i in range(500)]
    assert enviar(cliente, eventos).json()["guardados"] == 500
    assert enviar(cliente, eventos).json()["guardados"] == 500  # segunda vez: todas existen
    assert contar(ProgresoLeccion, ProgresoLeccion.usuario_id == ANONIMO) == 500
    assert enviar(cliente, eventos + [evento("les_extra")]).status_code == 422  # máximo 500


def test_valida_datos(cliente):
    assert enviar(cliente, [evento(puntaje=150)]).status_code == 422
    assert enviar(cliente, [evento(intentos=-1)]).status_code == 422
    assert enviar(cliente, [{"curso_id": "blender"}]).status_code == 422
    assert enviar(cliente, [evento("x" * 51)]).status_code == 422
    assert enviar(cliente, []).status_code == 422
    assert enviar(cliente, [evento()], insignias=["x" * 101]).status_code == 422
    assert enviar(cliente, [evento()], insignias=[f"i{n}" for n in range(101)]).status_code == 422


def test_pwa_vieja_sin_campos_nuevos(cliente):
    # Formato exacto de la PWA anterior (incluye actualizado_en con zona).
    cuerpo = {
        "usuario_id": "alumno-pwa-vieja",
        "eventos": [
            {
                "curso_id": "blender",
                "leccion_id": "les_004",
                "completada": True,
                "puntaje": 90,
                "intentos": 2,
                "actualizado_en": "2026-09-30T18:04:05.123Z",
            },
            {"curso_id": "blender", "leccion_id": "les_005", "completada": False, "puntaje": None, "intentos": 0,
             "actualizado_en": None},
        ],
    }
    respuesta = cliente.post("/api/progreso", json=cuerpo)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"guardados": 2, "insignias": 0}
    assert fila("alumno-pwa-vieja", "les_004").completada_en == datetime(2026, 9, 30, 18, 4, 5, 123000)
    # Campos que el servidor no conoce se ignoran (contratos tolerantes).
    cuerpo["eventos"][0]["campo_futuro"] = {"x": 1}
    cuerpo["otro_campo"] = 1
    assert cliente.post("/api/progreso", json=cuerpo).status_code == 200


# --- datos_ligeros -----------------------------------------------------------------


def test_datos_ligeros_se_unen_y_se_guardan_compactos(cliente):
    enviar(cliente, [evento(datos_ligeros={"a": 3, "t": 240})])
    assert fila(ANONIMO).datos_ligeros == '{"a":3,"t":240}'
    # También se acepta un texto JSON; lo nuevo gana en las llaves repetidas.
    enviar(cliente, [evento(datos_ligeros='{"a": 5, "p": 2}')])
    assert json.loads(fila(ANONIMO).datos_ligeros) == {"a": 5, "t": 240, "p": 2}
    assert fila(ANONIMO).datos_ligeros == '{"a":5,"t":240,"p":2}'
    assert lecciones(cliente)["les_001"]["datos_ligeros"] == {"a": 5, "t": 240, "p": 2}
    # Sin datos_ligeros se conserva lo guardado.
    enviar(cliente, [evento(completada=True)])
    assert json.loads(fila(ANONIMO).datos_ligeros) == {"a": 5, "t": 240, "p": 2}


def test_datos_ligeros_limite_de_250(cliente):
    enviar(cliente, [evento(datos_ligeros={"x": "a" * 200})])
    assert json.loads(fila(ANONIMO).datos_ligeros) == {"x": "a" * 200}
    # La unión pasaría de 250: se guarda solo lo nuevo.
    enviar(cliente, [evento(datos_ligeros={"y": "b" * 100})])
    assert json.loads(fila(ANONIMO).datos_ligeros) == {"y": "b" * 100}
    # Lo nuevo solo ya pasa de 250: se ignora el campo (sin 422) y se conserva lo guardado.
    respuesta = enviar(cliente, [evento(completada=True, datos_ligeros={"z": "c" * 300})])
    assert respuesta.status_code == 200
    assert json.loads(fila(ANONIMO).datos_ligeros) == {"y": "b" * 100}
    assert fila(ANONIMO).completada == 1
    # Exactamente 250 caracteres sí cabe.
    justo = {"k": "d" * (250 - len('{"k":""}'))}
    enviar(cliente, [evento("les_002", datos_ligeros=justo)])
    assert len(fila(ANONIMO, "les_002").datos_ligeros) == 250


def test_datos_ligeros_que_no_son_objeto_se_ignoran(cliente):
    for valor in ("no es json", "[1, 2]", "3", [1, 2], 7, "", {}):
        respuesta = enviar(cliente, [evento(datos_ligeros=valor)])
        assert respuesta.status_code == 200, valor
    assert fila(ANONIMO).datos_ligeros is None
    assert lecciones(cliente)["les_001"]["datos_ligeros"] is None


# --- completada_en -------------------------------------------------------------------


def test_completada_en_se_fija_la_primera_vez(cliente):
    enviar(cliente, [evento(completada=False, actualizado_en="2026-09-01T10:00:00")])
    assert fila(ANONIMO).completada_en is None

    enviar(cliente, [evento(completada=True, actualizado_en="2026-09-02T10:00:00")])
    primera = fila(ANONIMO).completada_en
    assert primera == datetime(2026, 9, 2, 10, 0)

    # Reenvíos posteriores (o eventos sin completar) no la cambian.
    enviar(cliente, [evento(completada=True, actualizado_en="2026-09-20T10:00:00")])
    enviar(cliente, [evento(completada=False, actualizado_en="2026-09-21T10:00:00")])
    enviar(cliente, [evento(completada=True)])
    assert fila(ANONIMO).completada_en == primera
    assert lecciones(cliente)["les_001"]["completada_en"] == "2026-09-02T10:00:00"

    # Si llega una fecha más antigua (otro dispositivo offline), se toma la más antigua.
    enviar(cliente, [evento(completada=True, completada_en="2026-09-01T08:00:00-06:00")])
    assert fila(ANONIMO).completada_en == datetime(2026, 9, 1, 14, 0)


def test_completada_en_nunca_en_el_futuro(cliente):
    futuro = (ahora() + timedelta(days=3)).isoformat()
    antes = ahora()
    enviar(cliente, [evento(completada=True, actualizado_en=futuro)])
    assert antes <= fila(ANONIMO).completada_en <= ahora()


def test_completada_en_sin_fecha_del_dispositivo_usa_la_del_servidor(cliente):
    antes = ahora()
    enviar(cliente, [evento(completada=True)])
    assert antes <= fila(ANONIMO).completada_en <= ahora()


# --- Insignias ------------------------------------------------------------------------


def test_insignias_sin_duplicar(cliente):
    respuesta = enviar(cliente, [evento()], insignias=["blender:mod_1", "blender:mod_1", " aframe:mod_1 "])
    assert respuesta.json()["insignias"] == 2
    with Session(conexion.motor()) as db:
        primera = db.get(Logro, (ANONIMO, "blender:mod_1")).obtenido_en

    assert enviar(cliente, [evento()], insignias=["blender:mod_1", "blender:mod_2"]).status_code == 200
    with Session(conexion.motor()) as db:
        ids = set(db.scalars(select(Logro.insignia_id).where(Logro.usuario_id == ANONIMO)).all())
        assert db.get(Logro, (ANONIMO, "blender:mod_1")).obtenido_en == primera  # conserva su fecha
    assert ids == {"blender:mod_1", "blender:mod_2", "aframe:mod_1"}
    datos = cliente.get(f"/api/progreso/{ANONIMO}").json()
    assert {i["id"] for i in datos["insignias"]} == ids
    assert all(i["obtenido_en"] for i in datos["insignias"])


def test_solo_insignias_sin_eventos(cliente):
    respuesta = enviar(cliente, [], insignias=["blender:mod_1"])
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"guardados": 0, "insignias": 1}
    assert contar(Logro, Logro.usuario_id == ANONIMO) == 1
    assert enviar(cliente, [], insignias=[]).status_code == 422
