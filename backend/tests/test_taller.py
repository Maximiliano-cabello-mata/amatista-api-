"""Amatista Taller en el servidor (api/taller.py, sql/012, motor 5).

El add-on manda los intentos de cada habilidad (también los guardados sin
conexión); el servidor vuelve a calcular el modelo del alumno con el mismo
motor y, si llega la foto de la escena, la revisa él: ese es el acierto que
cuenta. La plataforma lee el plan de cada taller y maneja el taller en
Blender por el enlace en vivo.
"""
import time

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.contenido import asegurar_cursos_base
from contenido import motor
from contenido.validacion import validar_leccion
from database import conexion
from database.modelos import Habilidad, HabilidadAlumno
from tests.test_addon import API, vincular
from tests.test_enlace import latir

from amatista_engine.testing import Escena

TALLER = "blender.bp.m1"


@pytest.fixture()
def con_cursos(cliente):
    with Session(conexion.motor()) as db:
        asegurar_cursos_base(db)
        db.commit()


def escena_cubo(primitiva="cube"):
    return motor.snapshot.scene_to_dict(Escena().malla(primitiva, "Cubo", dims=(2, 2, 2), loc=(0, 0, 0)).construir())


def escena_vacia():
    return motor.snapshot.scene_to_dict(Escena().construir())


def intento(actividad, tipo, correcto=False, en=None, habilidad="bl-agregar-primitivas", **extra):
    return {"habilidad": habilidad, "actividad": actividad, "taller": TALLER, "tipo": tipo, "correcto": correcto,
            "ayuda": 0, "segundos": 30, "en": en or time.time() - 60, **extra}


def enviar(cliente, cabeceras, *intentos):
    return cliente.post(f"{API}/habilidades/intentos", json={"intentos": list(intentos), "version_addon": "5.0.0"},
                        headers=cabeceras)


def test_sin_intentos_el_alumno_empieza_de_cero(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    datos = cliente.get(f"{API}/habilidades", headers=alumno).json()
    assert datos["estado"] == {} and datos["completadas"] == [] and datos["guardado"] is True
    assert cliente.get(f"{API}/habilidades").status_code == 401


def test_al_arrancar_registra_las_habilidades_del_catalogo(cliente, con_cursos, monkeypatch):
    from api.taller import sincronizar_al_arrancar
    from contenido.taller import catalogo

    with Session(conexion.motor()) as db:
        resumen = sincronizar_al_arrancar(db)
        assert resumen["sin_curso"] == []
        filas = {h.id: h for h in db.query(Habilidad).all()}
    assert set(catalogo().habilidades) <= set(filas)
    assert filas["bl-agregar-primitivas"].curso_id == "blender_principiante"
    assert filas["bl-agregar-primitivas"].nombre == "Agregar formas"
    # Repetirlo no cambia nada.
    with Session(conexion.motor()) as db:
        otra = sincronizar_al_arrancar(db)
    assert otra["nuevas"] == [] and otra["actualizadas"] == []


def test_el_servidor_revisa_la_escena_y_su_resultado_cuenta(cliente, crear_cuenta, con_cursos):
    alumno_id, alumno = crear_cuenta()
    ahora = time.time()
    respuesta = enviar(
        cliente, alumno,
        intento("mira:bl-agregar-primitivas", "mira", en=ahora - 300),
        # El add-on dice que no, pero la escena tiene el cubo: el servidor la revisa y cuenta el acierto.
        intento("conmigo:bl-agregar-primitivas:caja", "conmigo", correcto=False, en=ahora - 200, escena=escena_cubo()),
        # El add-on dice que sí con una escena vacía: no cuenta.
        intento("tu:bl-agregar-primitivas:cono", "tu", correcto=True, en=ahora - 100, escena=escena_vacia()),
    )
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert (datos["aceptados"], datos["repetidos"], datos["rechazados"]) == (3, 0, [])
    d = datos["estado"]["bl-agregar-primitivas"]
    assert d["vio_demo"] is True and d["intentos"] == 2 and d["aciertos"] == 1
    with Session(conexion.motor()) as db:
        fila = db.get(HabilidadAlumno, (alumno_id, "bl-agregar-primitivas"))
        assert fila.dominio is not None and fila.estado in ("con_guia", "con_pistas") and "conmigo" not in fila.evidencia
    # GET devuelve lo mismo que quedó guardado.
    assert cliente.get(f"{API}/habilidades", headers=alumno).json()["estado"]["bl-agregar-primitivas"] == d


def test_un_reenvio_no_cuenta_dos_veces(cliente, crear_cuenta, con_cursos):
    _, alumno = crear_cuenta()
    uno = intento("conmigo:bl-agregar-primitivas:caja", "conmigo", correcto=True, en=time.time() - 30)
    assert enviar(cliente, alumno, uno).json()["aceptados"] == 1
    otra_vez = enviar(cliente, alumno, uno).json()
    assert otra_vez["aceptados"] == 0 and otra_vez["repetidos"] == 1
    assert otra_vez["estado"]["bl-agregar-primitivas"]["intentos"] == 1


def test_los_intentos_que_no_existen_se_rechazan_con_su_motivo(cliente, crear_cuenta, con_cursos):
    _, alumno = crear_cuenta()
    ahora = time.time()
    datos = enviar(
        cliente, alumno,
        {**intento("conmigo:bl-agregar-primitivas:caja", "conmigo"), "taller": "no.existe"},
        intento("conmigo:bl-agregar-primitivas:caja", "tu", en=ahora - 50),
        intento("conmigo:bl-agregar-primitivas:nada", "conmigo", en=ahora - 40),
        intento("conmigo:bl-agregar-primitivas:caja", "conmigo", en=ahora - 400 * 86400 - 10),
        intento("conmigo:bl-agregar-primitivas:caja", "conmigo", en=ahora - 20, habilidad="bl-mover"),
    ).json()
    motivos = [r["motivo"] for r in sorted(datos["rechazados"], key=lambda r: r["indice"])]
    assert datos["aceptados"] == 0 and len(motivos) == 5, motivos
    assert "no.existe" in motivos[0] and "tipo" in motivos[1] and "nada" in motivos[2]
    assert "fecha" in motivos[3] and "bl-mover" in motivos[4]


def test_los_talleres_y_el_plan(cliente, crear_cuenta, con_cursos):
    _, alumno = crear_cuenta()
    lista = cliente.get(f"{API}/talleres", headers=alumno).json()
    assert len(lista["talleres"]) == 9 and lista["guardado"] is True
    primero = lista["talleres"][0]
    assert primero["taller"] == TALLER and primero["leccion"] == "bp1_blender" and primero["listas"] == 0
    assert primero["isla"]["pieza"] == "La estación y el tren"
    assert [t["taller"] for t in cliente.get(f"{API}/talleres?curso=blender_intermedio", headers=alumno).json()["talleres"]] == [
        "blender.bi.m1", "blender.bi.m2", "blender.bi.m3"]

    plan = cliente.get(f"{API}/talleres/{TALLER}", headers=alumno).json()
    assert plan["actual"]["tipo"] == "mira" and plan["proyecto_abierto"] is False
    tarjeta = next(h for h in plan["habilidades"] if h["id"] == "bl-agregar-primitivas")
    assert tarjeta["que"].startswith("Todo en 3D") and "Shift+A" in tarjeta["teclas"]
    elegida = cliente.get(f"{API}/talleres/{TALLER}?elegida=bl-rotar", headers=alumno).json()
    assert elegida["actual"]["habilidad"] == "bl-rotar"
    assert cliente.get(f"{API}/talleres/no.existe", headers=alumno).status_code == 404


def test_sin_012_se_lee_vacio_y_los_intentos_esperan(cliente, crear_cuenta, con_cursos):
    _, alumno = crear_cuenta()
    with Session(conexion.motor()) as db:
        db.execute(text("DROP TABLE talleres_alumno"))
        db.commit()
    assert cliente.get(f"{API}/habilidades", headers=alumno).json()["guardado"] is False
    assert cliente.get(f"{API}/talleres", headers=alumno).json()["guardado"] is False
    respuesta = enviar(cliente, alumno, intento("conmigo:bl-agregar-primitivas:caja", "conmigo"))
    assert respuesta.status_code == 503 and "012" in respuesta.json()["detail"]


def test_la_plataforma_maneja_el_taller_en_blender(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender)
    ordenar = lambda **cuerpo: cliente.post(f"{API}/ordenes", json=cuerpo, headers=alumno)  # noqa: E731
    assert ordenar(tipo="taller_abrir", taller=TALLER, actividad="mira:bl-mover").json()["entregada"] is True
    orden = latir(cliente, blender)["orden"]
    assert orden["tipo"] == "taller_abrir" and orden["datos"] == {"taller": TALLER, "actividad": "mira:bl-mover"}
    latir(cliente, blender, orden_hecha=orden["id"])
    for tipo in ("taller_continuar", "taller_pausa", "taller_pista", "taller_mirame", "isla"):
        assert ordenar(tipo=tipo).status_code == 200, tipo
    assert ordenar(tipo="taller_abrir", taller="no.existe").status_code == 404
    assert ordenar(tipo="taller_probar", taller=TALLER, habilidad="bl-luces").status_code == 404
    assert ordenar(tipo="taller_repaso", taller=TALLER, habilidad="bl-mover").status_code == 200


def test_la_leccion_manda_su_etapa_y_blender_no_sale_de_ella(cliente, crear_cuenta):
    """Motor 6: la exploración lleva sus habilidades (solo las del taller) y la lección a la que se vuelve."""
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    latir(cliente, blender)
    ordenar = lambda **cuerpo: cliente.post(f"{API}/ordenes", json=cuerpo, headers=alumno)  # noqa: E731
    r = ordenar(tipo="taller_abrir", taller=TALLER, etapa="exploracion",
                habilidades=["bl-agregar-primitivas", "bl-luces", "bl-mover"], leccion="bp1_blender")
    assert r.status_code == 200 and r.json()["entregada"] is True
    orden = latir(cliente, blender)["orden"]
    assert orden["datos"] == {"taller": TALLER, "etapa": "exploracion",
                              "habilidades": ["bl-agregar-primitivas", "bl-mover"], "leccion": "bp1_blender"}
    latir(cliente, blender, orden_hecha=orden["id"])
    assert ordenar(tipo="taller_abrir", taller=TALLER, etapa="todo").status_code == 422
    assert ordenar(tipo="taller_abrir", taller=TALLER, leccion="../../etc").status_code == 422
    assert ordenar(tipo="taller_abrir", taller=TALLER, etapa="exploracion",
                   habilidades=["a", "b", "c", "d", "e"]).status_code == 422


def test_la_leccion_ve_el_taller_en_vivo(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    blender, _ = vincular(cliente, alumno)
    vivo = {
        "taller": TALLER,
        "actividad": {"id": "conmigo:bl-mover:baja", "tipo": "conmigo", "nombre_tipo": "Hazlo conmigo",
                      "titulo": "Mover", "pide": "Baja el cubo al suelo.", "habilidad": "bl-mover", "numero": 1,
                      "de": 3, "dibujo": "molde", "molde": True},
        "pieza": {"nombre": "Cubo", "estado": "cerca", "mensaje": "Un poco más abajo."},
        "llenas": 0, "total": 1, "mensaje": "Un poco más abajo.",
        "pista": {"nivel": 3, "titulo": "Cómo", "texto": "G, Z y mueve el ratón.", "teclas": ["G", "Z"]},
        "estado": "trabajando",
        "habilidades": [{"id": "bl-mover", "titulo": "Mover", "p": 0.42, "fase": "acompanada"}],
        "proyecto_abierto": False, "repasos": [],
    }
    latir(cliente, blender, practica_id="taller.bl-mover.conmigo", paso="conmigo:bl-mover:baja", progreso=0,
          detalle={"titulo": "Mover", "mensaje": "Un poco más abajo.", "taller": vivo})
    (b,) = cliente.get(f"{API}/enlace", headers=alumno).json()["blender"]
    assert b["detalle"]["taller"]["actividad"]["pide"] == "Baja el cubo al suelo."
    assert b["detalle"]["taller"]["pista"]["teclas"] == ["G", "Z"]
    # Un detalle de taller fuera de sus límites no se guarda (y el latido lo rechaza entero).
    malo = {**vivo, "habilidades": [{"id": "x", "p": 3}]}
    respuesta = cliente.post(f"{API}/enlace", json={"practica_id": "taller.x", "detalle": {"taller": malo}}, headers=blender)
    assert respuesta.status_code == 422


def test_el_bloque_blender_taller_se_valida():
    from tests.test_contenido import leccion_con as leccion

    bien = {"type": "blender_taller", "id": "taller_1", "taller": TALLER, "title": "Tu taller"}
    assert validar_leccion(leccion(bien)) == []
    assert validar_leccion(leccion({**bien, "vista": "proyecto"})) == []
    assert any("no está en practices" in e for e in validar_leccion(leccion({**bien, "taller": "blender.zz.m9"})))
    assert any("vista" in e for e in validar_leccion(leccion({**bien, "vista": "otra"})))
    assert any("id" in e for e in validar_leccion(leccion({k: v for k, v in bien.items() if k != "id"})))


def test_la_etapa_del_taller_se_valida():
    """Motor 6: exploración (con habilidades del taller) antes que la práctica, una vez cada una por módulo."""
    from contenido.validacion import _revisar_orden_practica
    from tests.test_contenido import leccion_con as leccion

    explora = {"type": "blender_taller", "id": "t1", "taller": TALLER, "title": "Explora", "etapa": "exploracion",
               "habilidades": ["bl-agregar-primitivas", "bl-mover"]}
    assert validar_leccion(leccion(explora)) == []
    assert any("no es válido" in e for e in validar_leccion(leccion({**explora, "etapa": "todo"})))
    assert any("no son del taller" in e for e in validar_leccion(leccion({**explora, "habilidades": ["bl-luces"]})))
    assert any("solo va con" in e for e in validar_leccion(leccion({**explora, "etapa": "practica"})))
    assert any("lista de 1 a 4" in e for e in validar_leccion(leccion({**explora, "habilidades": list("abcde")})))
    practica = {"type": "blender_taller", "id": "t2", "taller": TALLER, "title": "Taller", "etapa": "practica"}
    con = lambda bloque: {"contentBlocks": [bloque]}  # noqa: E731
    errores = []
    _revisar_orden_practica(errores, [con(explora), con(practica)])
    assert errores == []
    _revisar_orden_practica(errores, [con(practica), con(explora)])
    assert any("va antes que su práctica" in e for e in errores)
    errores = []
    _revisar_orden_practica(errores, [con(explora), con(explora)])
    assert any("cada etapa del taller va una sola vez" in e for e in errores)
