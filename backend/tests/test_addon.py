"""API del add-on de Blender: vínculo, prácticas, intentos evaluados por el
servidor y descargas armadas al vuelo (api/addon.py, sql/007)."""
import copy
import hashlib
import io
import json
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from contenido import motor
from database import conexion
from database.modelos import (
    AddonVinculo,
    Curso,
    EventoAprendizaje,
    Habilidad,
    HabilidadAlumno,
    Practica,
    ProgresoLeccion,
    ahora,
)

from amatista_engine.models import SceneObject, SceneState  # noqa: E402  (contenido.motor agrega engine/ al path)

API = "/api/addon/v1"
MESA = json.loads((motor.RAIZ / "practices" / "archivo" / "v2" / "mesa.json").read_text(encoding="utf-8"))


def escena_mesa(guardada=True, patas=4, sin_roles=False):
    """Foto de la escena como la manda el add-on (snapshot.scene_to_dict)."""

    def obj(nombre, rol, ubicacion, medidas):
        caja = [[u - m / 2 for u, m in zip(ubicacion, medidas)], [u + m / 2 for u, m in zip(ubicacion, medidas)]]
        return SceneObject(nombre, "MESH", roles=() if sin_roles else (rol,), location=ubicacion,
                           dimensions=medidas, bbox_min=tuple(caja[0]), bbox_max=tuple(caja[1]))

    objetos = [obj("Tabla", "cubierta", (0, 0, 0.8), (2.0, 1.0, 0.1))]
    for i, (x, y) in enumerate(((-0.9, -0.4), (0.9, -0.4), (-0.9, 0.4), (0.9, 0.4))[:patas]):
        objetos.append(obj(f"Pata{i}", "pata", (x, y, 0.375), (0.1, 0.1, 0.75)))
    estado = SceneState(blender_version="5.1.1", file_path="/tmp/mesa.blend", file_saved=guardada,
                        objects=tuple(objetos))
    return motor.snapshot.scene_to_dict(estado)


@pytest.fixture()
def sembrar(monkeypatch, tmp_path):
    """Curso blender, habilidades de la mesa y las prácticas v2 (mesa y podio) publicadas «desde el repositorio».

    Estas pruebas cubren el mecanismo con las prácticas archivadas de la v2;
    test_sincronizar_el_plan_de_estudios_v3 cubre las del plan de estudios.
    """
    for nombre in ("mesa", "podio"):
        carpeta = tmp_path / "blender" / nombre
        carpeta.mkdir(parents=True)
        (carpeta / "practica.json").write_bytes((motor.RAIZ / "practices" / "archivo" / "v2" / f"{nombre}.json").read_bytes())
    monkeypatch.setattr(motor, "CARPETA_PRACTICAS", tmp_path / "blender")

    def hacer(cliente, admin):
        with Session(conexion.motor()) as db:
            if db.get(Curso, "blender") is None:
                db.add(Curso(id="blender", titulo="Blender", estado="publicado"))
                db.flush()
            for orden, habilidad in enumerate(MESA["skills"]):
                if db.get(Habilidad, habilidad) is None:
                    db.add(Habilidad(id=habilidad, curso_id="blender", nombre=habilidad, orden=orden))
            db.commit()
        respuesta = cliente.post(f"{API}/practicas/sincronizar?publicar=true", headers=admin)
        assert respuesta.status_code == 200, respuesta.text
        return respuesta.json()

    return hacer


def vincular(cliente, cabeceras_alumno, dispositivo="blender-addon 0.2.0 · Blender 5.1.1 · Linux"):
    pedido = cliente.post(f"{API}/vinculos", json={"dispositivo": dispositivo}).json()
    confirmado = cliente.post(f"{API}/vinculos/confirmar", json={"codigo": pedido["codigo"].lower().replace("-", " ")},
                              headers=cabeceras_alumno)
    assert confirmado.status_code == 200, confirmado.text
    listo = cliente.post(f"{API}/vinculos/{pedido['vinculo_id']}/estado", json={"secreto": pedido["secreto"]}).json()
    assert listo["estado"] == "listo"
    return {"Authorization": f"Bearer {listo['token']}"}, pedido


# --- Vínculo --------------------------------------------------------------------


def test_vinculo_completo_y_de_un_solo_uso(cliente, crear_cuenta):
    alumno_id, cabeceras = crear_cuenta()
    pedido = cliente.post(f"{API}/vinculos", json={"dispositivo": "Blender 5.1.1"}, headers={"Origin": "http://localhost:5173"})
    assert pedido.status_code == 201
    datos = pedido.json()
    assert len(datos["codigo"]) == 9 and datos["codigo"][4] == "-"
    assert datos["url_vincular"] == f"http://localhost:5173/#/vincular?codigo={datos['codigo']}"

    pendiente = cliente.post(f"{API}/vinculos/{datos['vinculo_id']}/estado", json={"secreto": datos["secreto"]})
    assert pendiente.json()["estado"] == "pendiente"
    malo = cliente.post(f"{API}/vinculos/{datos['vinculo_id']}/estado", json={"secreto": "x" * 20})
    assert malo.status_code == 404

    token, _ = None, None
    cliente.post(f"{API}/vinculos/confirmar", json={"codigo": datos["codigo"]}, headers=cabeceras)
    listo = cliente.post(f"{API}/vinculos/{datos['vinculo_id']}/estado", json={"secreto": datos["secreto"]}).json()
    assert listo["estado"] == "listo" and listo["cuenta"]["id"] == alumno_id
    token = {"Authorization": f"Bearer {listo['token']}"}
    otra_vez = cliente.post(f"{API}/vinculos/{datos['vinculo_id']}/estado", json={"secreto": datos["secreto"]})
    assert otra_vez.json() == {"estado": "canjeado"}

    yo = cliente.get(f"{API}/yo", headers=token)
    assert yo.status_code == 200 and yo.json()["id"] == alumno_id
    # El token del add-on no sirve fuera de /api/addon y /api/blender.
    assert cliente.get("/api/auth/yo", headers=token).status_code == 403

    lista = cliente.get(f"{API}/dispositivos", headers=cabeceras).json()["dispositivos"]
    assert len(lista) == 1 and "Blender 5.1.1" in lista[0]["nombre"]
    assert cliente.delete(f"{API}/dispositivos/{lista[0]['id']}", headers=cabeceras).status_code == 200
    assert cliente.get(f"{API}/yo", headers=token).status_code == 401


def test_codigo_invalido_vencido_o_de_otra_cuenta(cliente, crear_cuenta):
    _, uno = crear_cuenta()
    _, otro = crear_cuenta()
    assert cliente.post(f"{API}/vinculos/confirmar", json={"codigo": "ZZZZ-ZZZZ"}, headers=uno).status_code == 404
    datos = cliente.post(f"{API}/vinculos", json={}).json()
    assert cliente.post(f"{API}/vinculos/confirmar", json={"codigo": datos["codigo"]}).status_code == 401
    assert cliente.post(f"{API}/vinculos/confirmar", json={"codigo": datos["codigo"]}, headers=uno).status_code == 200
    assert cliente.post(f"{API}/vinculos/confirmar", json={"codigo": datos["codigo"]}, headers=otro).status_code == 409
    with Session(conexion.motor()) as db:
        db.get(AddonVinculo, datos["vinculo_id"]).expira_en = ahora() - timedelta(minutes=1)
        db.commit()
    vencido = cliente.post(f"{API}/vinculos/{datos['vinculo_id']}/estado", json={"secreto": datos["secreto"]})
    assert vencido.json() == {"estado": "vencido"}


def test_vinculos_viejos_se_purgan(cliente):
    datos = cliente.post(f"{API}/vinculos", json={}).json()
    with Session(conexion.motor()) as db:
        db.get(AddonVinculo, datos["vinculo_id"]).expira_en = ahora() - timedelta(days=2)
        db.commit()
    cliente.post(f"{API}/vinculos", json={})
    with Session(conexion.motor()) as db:
        assert db.get(AddonVinculo, datos["vinculo_id"]) is None


# --- Prácticas ------------------------------------------------------------------


def test_sincronizar_publica_y_los_alumnos_ven_solo_lo_publicado(cliente, crear_cuenta, sembrar):
    _, admin = crear_cuenta(rol="admin")
    _, profesor = crear_cuenta(rol="profesor")
    _, alumno = crear_cuenta()
    resumen = sembrar(cliente, admin)
    assert resumen["errores"] == []
    assert {p["id"] for p in resumen["practicas"]} == {"blender.n1.mesa", "blender.n1.podio"}
    assert resumen["practicas"][0]["version_publicada"] == 1

    lista = cliente.get(f"{API}/practicas", headers=alumno).json()["practicas"]
    assert {p["id"] for p in lista} == {"blender.n1.mesa", "blender.n1.podio"}
    assert lista[0]["titulo"] == "Construir una mesa" and "estado" not in lista[0]

    # Un desarrollador sube una versión nueva: queda en borrador, el alumno sigue con la 1.
    nueva = copy.deepcopy(MESA)
    nueva["title"] = "Construir una mesa (v2)"
    subida = cliente.post(f"{API}/practicas", json={"definicion": nueva, "nota": "título"}, headers=profesor)
    assert subida.status_code == 201, subida.text
    assert subida.json()["version"] == 2 and subida.json()["version_publicada"] == 1
    repetida = cliente.post(f"{API}/practicas", json={"definicion": nueva}, headers=profesor).json()
    assert repetida["sin_cambios"] is True and repetida["version"] == 2

    detalle = cliente.get(f"{API}/practicas/blender.n1.mesa", headers=alumno).json()
    assert detalle["version"] == 1 and detalle["definicion"]["title"] == "Construir una mesa"
    assert cliente.get(f"{API}/practicas", headers=alumno).json()["practicas"][0]["titulo"] == "Construir una mesa"
    equipo = cliente.get(f"{API}/practicas/blender.n1.mesa", headers=profesor).json()
    assert equipo["version"] == 2 and equipo["estado"] == "publicado"

    historial = cliente.get(f"{API}/practicas/blender.n1.mesa/versiones", headers=profesor).json()
    assert [v["version"] for v in historial["versiones"]] == [2, 1]
    assert cliente.post(f"{API}/practicas/blender.n1.mesa/publicar", json={}, headers=profesor).status_code == 403
    assert cliente.post(f"{API}/practicas/blender.n1.mesa/publicar", json={"version": 2}, headers=admin).json()[
        "version_publicada"] == 2
    assert cliente.get(f"{API}/practicas/blender.n1.mesa", headers=alumno).json()["version"] == 2

    cliente.post(f"{API}/practicas/blender.n1.mesa/archivar", headers=admin)
    restantes = cliente.get(f"{API}/practicas", headers=alumno).json()["practicas"]
    assert {p["id"] for p in restantes} == {"blender.n1.podio"}
    assert cliente.get(f"{API}/practicas/blender.n1.mesa", headers=alumno).status_code == 404


def test_subir_practica_invalida_o_sin_permiso(cliente, crear_cuenta):
    _, profesor = crear_cuenta(rol="profesor")
    _, alumno = crear_cuenta()
    assert cliente.post(f"{API}/practicas", json={"definicion": MESA}, headers=alumno).status_code == 403
    rota = copy.deepcopy(MESA)
    rota["targets"][0]["validator"] = "no.existe"
    respuesta = cliente.post(f"{API}/practicas", json={"definicion": rota}, headers=profesor)
    assert respuesta.status_code == 422
    assert any("no.existe" in e for e in respuesta.json()["errores"])
    sin_curso = cliente.post(f"{API}/practicas", json={"definicion": MESA, "curso_id": "nada"}, headers=profesor)
    assert sin_curso.status_code == 422
    nueva = cliente.post(f"{API}/practicas", json={"definicion": MESA}, headers=profesor).json()
    assert nueva["estado"] == "borrador" and nueva["version_publicada"] is None


# --- Progreso -------------------------------------------------------------------


def test_sincronizar_el_plan_de_estudios_v3(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    resumen = cliente.post(f"{API}/practicas/sincronizar", headers=admin).json()
    assert resumen["errores"] == []
    assert {p["id"] for p in resumen["practicas"]} == {
        "blender.bp.m1.tren", "blender.bp.m2.espada", "blender.bp.m3.nave",
        "blender.bpi.m1.pinta-nave", "blender.bpi.m2.tres-puntos", "blender.bpi.m3.pelota",
    }


def test_intentos_evaluados_por_el_servidor(cliente, crear_cuenta, sembrar):
    _, admin = crear_cuenta(rol="admin")
    alumno_id, alumno = crear_cuenta()
    sembrar(cliente, admin)
    with Session(conexion.motor()) as db:
        practica = db.get(Practica, "blender.n1.mesa")
        practica.curso_id, practica.leccion_id = "blender", "les_103"
        db.commit()
    token, _ = vincular(cliente, alumno)

    abierta = cliente.post(f"{API}/practicas/blender.n1.mesa/abrir", json={"origen": "plataforma"}, headers=alumno)
    assert abierta.status_code == 200 and abierta.json()["progreso"] == 0
    actual = cliente.get(f"{API}/practica-actual", headers=token).json()
    assert actual["definicion"]["id"] == "blender.n1.mesa"

    # Tres patas: el servidor calcula el avance, no el cliente.
    parcial = cliente.post(f"{API}/intentos", json={
        "practica_id": "blender.n1.mesa", "version": 1, "escena": escena_mesa(guardada=False, patas=3),
        "pistas": {"patas": 1}, "version_addon": "0.2.0",
    }, headers={**token, "X-Blender-Version": "5.1.1"})
    assert parcial.status_code == 200, parcial.text
    assert parcial.json()["completada"] is False and parcial.json()["paso_actual"] == "patas"
    assert 0 < parcial.json()["progreso"] < 100

    completa = cliente.post(f"{API}/intentos", json={
        "practica_id": "blender.n1.mesa", "version": 1, "escena": escena_mesa(), "pistas": {"patas": 1},
    }, headers=token).json()
    assert completa["completada"] and completa["nueva_completa"] and completa["progreso"] == 100
    assert completa["autonomia"] == "con_pistas"
    assert set(completa["habilidades"]) == set(MESA["skills"])

    # Un envío peor no baja el mejor resultado ni «descompleta».
    peor = cliente.post(f"{API}/intentos", json={
        "practica_id": "blender.n1.mesa", "escena": escena_mesa(sin_roles=True),
    }, headers=token).json()
    assert peor["progreso"] < 100 and peor["mejor_progreso"] == 100 and not peor["nueva_completa"]

    mio = cliente.get(f"{API}/mi-progreso?practica_id=blender.n1.mesa", headers=alumno).json()["practicas"][0]
    assert mio["completada"] and mio["progreso"] == 100 and mio["intentos"] == 3
    assert mio["version_blender"] == "5.1.1" and "cubierta" in mio["objetivos"]
    assert cliente.get(f"{API}/practica-actual", headers=token).json()["definicion"] is None

    with Session(conexion.motor()) as db:
        leccion = db.get(ProgresoLeccion, (alumno_id, "blender", "les_103"))
        assert leccion is not None and leccion.completada == 1
        habilidad = db.get(HabilidadAlumno, (alumno_id, "bl-transformar"))
        assert habilidad.estado == "con_pistas"
        eventos = db.query(EventoAprendizaje).filter_by(usuario_id=alumno_id, tipo="activity_submitted").all()
        assert len(eventos) == 3 and json.loads(eventos[0].datos)["p"] == "blender.n1.mesa"


def test_autonomia_de_la_habilidad_tiene_tope(cliente, crear_cuenta, sembrar):
    _, admin = crear_cuenta(rol="admin")
    alumno_id, alumno = crear_cuenta()
    sembrar(cliente, admin)
    respuesta = cliente.post(f"{API}/intentos", json={"practica_id": "blender.n1.mesa", "escena": escena_mesa()},
                             headers=alumno).json()
    assert respuesta["autonomia"] == "autonoma"
    with Session(conexion.motor()) as db:
        assert db.get(HabilidadAlumno, (alumno_id, "bl-duplicar")).estado == "con_pistas"


@pytest.mark.parametrize("escena", [
    {"objetos": [{"n": "x", "l": [1, 2]}]},
    {"objetos": [{"n": "x", "l": ["a", 0, 0]}]},
    {"objetos": "muchos"},
    {"objetos": [{"n": str(i)} for i in range(501)]},
])
def test_intento_con_escena_invalida(cliente, crear_cuenta, sembrar, escena):
    _, admin = crear_cuenta(rol="admin")
    _, alumno = crear_cuenta()
    sembrar(cliente, admin)
    respuesta = cliente.post(f"{API}/intentos", json={"practica_id": "blender.n1.mesa", "escena": escena}, headers=alumno)
    assert respuesta.status_code == 422


def test_intento_sin_sesion_o_practica_en_borrador(cliente, crear_cuenta):
    _, profesor = crear_cuenta(rol="profesor")
    _, alumno = crear_cuenta()
    cliente.post(f"{API}/practicas", json={"definicion": MESA}, headers=profesor)
    cuerpo = {"practica_id": "blender.n1.mesa", "escena": escena_mesa()}
    assert cliente.post(f"{API}/intentos", json=cuerpo).status_code == 401
    assert cliente.post(f"{API}/intentos", json=cuerpo, headers=alumno).status_code == 404


# --- Descargas ------------------------------------------------------------------


def test_descarga_publica_y_personal(cliente, crear_cuenta, monkeypatch):
    monkeypatch.setenv("AMATISTA_URL_API", "https://api.amatista.test")
    monkeypatch.setenv("AMATISTA_URL_PWA", "https://amatista.test")
    alumno_id, alumno = crear_cuenta()
    assert cliente.get(f"{API}/descargas/amiga").status_code == 404

    publica = cliente.get(f"{API}/descargas/windows")
    assert publica.status_code == 200 and publica.headers["content-type"] == "application/zip"
    paquete = zipfile.ZipFile(io.BytesIO(publica.content))
    assert "Amatista/Instalar Amatista.bat" in paquete.namelist()
    extension = zipfile.ZipFile(io.BytesIO(paquete.read(f"Amatista/amatista-{motor.VERSION_ADDON}.zip")))
    config = json.loads(extension.read("config.json"))
    assert config["servidor"] == "https://api.amatista.test" and "vinculo" not in config

    personal = cliente.get(f"{API}/descargas/linux", headers=alumno)
    assert personal.headers["cache-control"] == "private, no-store"
    paquete = zipfile.ZipFile(io.BytesIO(personal.content))
    extension = zipfile.ZipFile(io.BytesIO(paquete.read(f"Amatista/amatista-{motor.VERSION_ADDON}.zip")))
    vinculo = json.loads(extension.read("config.json"))["vinculo"]
    # El add-on canjea el vínculo del paquete al abrir Blender: queda conectado sin pasos.
    listo = cliente.post(f"{API}/vinculos/{vinculo['id']}/estado", json={"secreto": vinculo["secreto"]}).json()
    assert listo["estado"] == "listo" and listo["cuenta"]["id"] == alumno_id


def test_repositorio_de_extensiones(cliente, monkeypatch):
    monkeypatch.setenv("AMATISTA_URL_API", "https://api.amatista.test")
    indice = cliente.get(f"{API}/extensiones/index.json").json()
    entrada = indice["data"][0]
    assert entrada["archive_url"] == "https://api.amatista.test/api/addon/v1/extension.zip"
    archivo = cliente.get(f"{API}/extension.zip").content
    assert entrada["archive_hash"] == "sha256:" + hashlib.sha256(archivo).hexdigest()
    estado = cliente.get(f"{API}/estado").json()
    assert estado["blender_minimo"] == "4.2.0" and estado["version_addon"] == motor.VERSION_ADDON


def test_el_plan_v3_enlaza_cada_practica_con_su_leccion(cliente, crear_cuenta):
    from api.contenido import asegurar_cursos_base, importar_modulo

    _, admin = crear_cuenta(rol="admin")
    carpeta = motor.RAIZ / "frontend" / "src" / "data" / "modulos"
    with Session(conexion.motor()) as db:
        asegurar_cursos_base(db)
        for archivo in sorted(carpeta.glob("blender_*-modulo-*.json")):
            importar_modulo(db, json.loads(archivo.read_text(encoding="utf-8")))
        db.commit()
    resumen = cliente.post(f"{API}/practicas/sincronizar?publicar=true", headers=admin).json()
    assert resumen["errores"] == [] and resumen["lecciones_enlazadas"] == 6
    with Session(conexion.motor()) as db:
        enlaces = {p.id: (p.curso_id, p.leccion_id) for p in db.query(Practica).all()}
    assert enlaces["blender.bp.m1.tren"] == ("blender_principiante", "bp1_practica")
    assert enlaces["blender.bpi.m3.pelota"] == ("blender_principiante_intermedio", "bpi3_practica")


def test_importar_el_modulo_2_enlaza_la_practica_con_su_leccion(cliente, crear_cuenta, sembrar):
    from api.contenido import importar_modulo
    from api.niveles import sembrar_niveles_blender

    _, admin = crear_cuenta(rol="admin")
    sembrar(cliente, admin)
    archivo = motor.RAIZ / "frontend" / "src" / "data" / "modulos" / "archivo" / "blender-modulo-2.json"
    with Session(conexion.motor()) as db:
        sembrar_niveles_blender(db)
        resumen = importar_modulo(db, json.loads(archivo.read_text(encoding="utf-8")))
        db.commit()
        assert resumen["estado"] == "archivado"  # curso v2 archivado en el motor v3: no llega a los alumnos
        practica = db.get(Practica, "blender.n1.mesa")
        assert (practica.curso_id, practica.leccion_id) == ("blender", "les_103")
