"""Panel del profesor evaluador y «Mi grupo» del alumno (api/evaluacion.py, sql/014)."""
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from contenido import modelo_ligero
from database import conexion
from database.modelos import Entrega, Practica, ProgresoPractica, ahora

API = "/api/evaluacion"


def modelo_mesa(**extra):
    datos = {
        "formato": modelo_ligero.FORMATO,
        "blender": "4.2.3",
        "centro": [0, 0, 0.5],
        "escala": 1.2,
        "materiales": [{"nombre": "Madera", "color": [0.6, 0.4, 0.2]}],
        "objetos": [
            {"nombre": "Cubierta", "tipo": "MESH", "material": 0,
             "v": [-100, -100, 0, 100, -100, 0, 100, 100, 0, -100, 100, 0], "t": [0, 1, 2, 0, 2, 3]},
            {"nombre": "Luz", "tipo": "LIGHT", "p": [2, 2, 3]},
        ],
    }
    datos.update(extra)
    return modelo_ligero.empaquetar(datos)


@pytest.fixture()
def practica(cliente):
    with Session(conexion.motor()) as db:
        db.add(Practica(id="blender.n1.mesa", titulo="Construir una mesa", nivel=1, version=1, version_publicada=1,
                        definicion="{}", estado="publicado", origen="repositorio"))
        db.add(Practica(id="blender.borrador", titulo="Borrador", nivel=1, version=1, definicion="{}",
                        estado="borrador", origen="repositorio"))
        db.commit()
    return "blender.n1.mesa"


@pytest.fixture()
def salon(cliente, crear_cuenta, practica):
    """Un profesor con un grupo, dos alumnos dentro y la mesa asignada."""
    profesor_id, profesor = crear_cuenta(rol="profesor", nombre="Profe Ana")
    grupo = cliente.post(f"{API}/grupos", json={"nombre": "3.º B"}, headers=profesor).json()
    assert grupo["codigo"].startswith("AMA-") and len(grupo["codigo"]) == 8
    alumnos = []
    for nombre in ("Luis", "Marta"):
        alumno_id, cabeceras = crear_cuenta(nombre=nombre)
        # El código se acepta como lo dicte el profesor: minúsculas, sin guion.
        codigo = grupo["codigo"].replace("-", "").lower()
        assert cliente.post(f"{API}/unirme", json={"codigo": codigo}, headers=cabeceras).json()["profesor"] == "Profe Ana"
        alumnos.append((alumno_id, cabeceras))
    asignacion = cliente.post(
        f"{API}/asignaciones",
        json={"grupo_id": grupo["id"], "practica_id": practica, "indicaciones": "Cuatro patas iguales."},
        headers=profesor,
    ).json()
    assert asignacion["titulo"] == "Construir una mesa"
    return {"profesor": profesor, "profesor_id": profesor_id, "grupo": grupo, "alumnos": alumnos, "asignacion": asignacion}


def test_el_modelo_ligero_se_revisa_y_resume():
    resumen = modelo_ligero.resumen_de(modelo_mesa())
    assert resumen == {"objetos": 2, "vertices": 4, "triangulos": 2, "materiales": 1,
                       "nombres": ["Cubierta", "Luz"], "resumido": False, "blender": "4.2.3"}
    for malo in (
        "no-es-base64!",
        modelo_ligero.empaquetar({"formato": "otro"}),
        modelo_mesa(objetos=[{"nombre": "X", "tipo": "MESH", "v": [0, 0, 0], "t": [0, 0, 1]}]),
        modelo_mesa(objetos=[{"nombre": "X", "tipo": "MESH", "v": [0, 0, 99999], "t": [0, 0, 0]}]),
        modelo_mesa(objetos=[{"nombre": "X", "tipo": "MESH", "material": 3}]),
    ):
        with pytest.raises(modelo_ligero.ModeloInvalido):
            modelo_ligero.resumen_de(malo)


def test_una_bomba_de_zip_no_pasa():
    import base64
    import zlib

    bomba = base64.b64encode(zlib.compress(b" " * (modelo_ligero.MAX_DESCOMPRIMIDO + 10), 9)).decode()
    with pytest.raises(modelo_ligero.ModeloInvalido, match="demasiado grande"):
        modelo_ligero.descomprimir(bomba)


def test_solo_profesores_y_admins_usan_el_panel(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    _, admin = crear_cuenta(rol="admin")
    assert cliente.get(f"{API}/grupos", headers=alumno).status_code == 403
    assert cliente.get(f"{API}/grupos").status_code == 401
    assert cliente.get(f"{API}/grupos", headers=admin).json() == []


def test_flujo_completo_entregar_recoger_y_evaluar(cliente, salon):
    profesor, asignacion = salon["profesor"], salon["asignacion"]
    (luis_id, luis), (marta_id, _) = salon["alumnos"]

    # El alumno ve la práctica que le dejaron.
    mias = cliente.get(f"{API}/mis-asignaciones", headers=luis).json()
    assert mias["grupos"][0]["profesor"] == "Profe Ana"
    assert mias["asignaciones"][0]["practica_id"] == "blender.n1.mesa"
    assert mias["asignaciones"][0]["entrega"] is None

    # El progreso de la entrega lo pone el servidor, desde lo que midió el motor.
    with Session(conexion.motor()) as db:
        db.add(ProgresoPractica(usuario_id=luis_id, practica_id="blender.n1.mesa", progreso=100, completada=1,
                                intentos=4, pistas=1))
        db.commit()
    entregada = cliente.post(f"{API}/entregas", json={"asignacion_id": asignacion["id"], "modelo": modelo_mesa()},
                             headers=luis).json()
    assert entregada["progreso"] == 100 and entregada["completada"] and entregada["resumen"]["triangulos"] == 2

    # El profesor ve una fila por alumno: Luis entregó; Marta aún no.
    vista = cliente.get(f"{API}/asignaciones/{asignacion['id']}", headers=profesor).json()
    filas = {f["alumno"]["id"]: f for f in vista["alumnos"]}
    assert filas[luis_id]["entrega"]["modelo_en_servidor"] is True
    assert filas[luis_id]["avance"]["intentos"] == 4
    assert filas[marta_id]["entrega"] is None
    assert cliente.get(f"{API}/grupos", headers=profesor).json()[0]["por_evaluar"] == 1

    # Recoge el modelo; al confirmar que lo guardó, el servidor lo borra.
    ruta = f"{API}/asignaciones/{asignacion['id']}/entregas/{luis_id}/modelo"
    assert modelo_ligero.resumen_de(cliente.get(ruta, headers=profesor).json()["modelo"])["objetos"] == 2
    assert cliente.post(f"{ruta}/recibido", headers=profesor).json()["recogido_en"]
    with Session(conexion.motor()) as db:
        assert db.get(Entrega, (asignacion["id"], luis_id)).modelo is None
    assert cliente.get(ruta, headers=profesor).status_code == 410
    fila = cliente.get(f"{API}/asignaciones/{asignacion['id']}", headers=profesor).json()["alumnos"]
    assert next(f for f in fila if f["alumno"]["id"] == luis_id)["entrega"]["modelo_en_servidor"] is False

    # Evalúa: el alumno ve la nota como nueva hasta que la abre.
    evaluada = cliente.post(f"{API}/asignaciones/{asignacion['id']}/entregas/{luis_id}/evaluar",
                            json={"estado": "evaluada", "calificacion": 9, "comentario": "¡Muy pareja!"},
                            headers=profesor).json()
    assert evaluada["calificacion"] == 9
    mia = cliente.get(f"{API}/mis-asignaciones", headers=luis).json()["asignaciones"][0]["entrega"]
    assert mia["nueva"] and mia["comentario"] == "¡Muy pareja!"
    cliente.post(f"{API}/mis-asignaciones/{asignacion['id']}/vista", headers=luis)
    assert not cliente.get(f"{API}/mis-asignaciones", headers=luis).json()["asignaciones"][0]["entrega"]["nueva"]

    # Evaluada no se reentrega; «rehacer» sí lo permite.
    assert cliente.post(f"{API}/entregas", json={"asignacion_id": asignacion["id"]}, headers=luis).status_code == 409
    cliente.post(f"{API}/asignaciones/{asignacion['id']}/entregas/{luis_id}/evaluar",
                 json={"estado": "rehacer", "comentario": "Falta una pata."}, headers=profesor)
    otra = cliente.post(f"{API}/entregas", json={"asignacion_id": asignacion["id"], "modelo": modelo_mesa()}, headers=luis)
    assert otra.status_code == 200 and otra.json()["estado"] == "entregada"


def test_nadie_ve_lo_de_otro_profesor_ni_entrega_fuera_de_su_grupo(cliente, crear_cuenta, salon):
    _, otro_profesor = crear_cuenta(rol="profesor")
    _, intruso = crear_cuenta()
    asignacion, grupo = salon["asignacion"], salon["grupo"]
    luis_id = salon["alumnos"][0][0]
    assert cliente.get(f"{API}/grupos/{grupo['id']}", headers=otro_profesor).status_code == 404
    assert cliente.get(f"{API}/asignaciones/{asignacion['id']}", headers=otro_profesor).status_code == 404
    assert cliente.get(f"{API}/asignaciones/{asignacion['id']}/entregas/{luis_id}/modelo",
                       headers=otro_profesor).status_code == 404
    assert cliente.post(f"{API}/entregas", json={"asignacion_id": asignacion["id"]}, headers=intruso).status_code == 404


def test_reglas_de_grupos_y_asignaciones(cliente, crear_cuenta, salon):
    profesor, grupo, asignacion = salon["profesor"], salon["grupo"], salon["asignacion"]
    luis_id, luis = salon["alumnos"][0]
    # Un borrador no se puede asignar (los alumnos no lo verían).
    assert cliente.post(f"{API}/asignaciones", json={"grupo_id": grupo["id"], "practica_id": "blender.borrador"},
                        headers=profesor).status_code == 404
    assert [p["id"] for p in cliente.get(f"{API}/practicas", headers=profesor).json()] == ["blender.n1.mesa"]
    # Cerrada: no se entrega.
    cliente.patch(f"{API}/asignaciones/{asignacion['id']}", json={"estado": "cerrada"}, headers=profesor)
    assert cliente.post(f"{API}/entregas", json={"asignacion_id": asignacion["id"]}, headers=luis).status_code == 409
    # Un modelo mal formado se rechaza con un mensaje en español.
    cliente.patch(f"{API}/asignaciones/{asignacion['id']}", json={"estado": "abierta"}, headers=profesor)
    malo = cliente.post(f"{API}/entregas", json={"asignacion_id": asignacion["id"], "modelo": "@@@"}, headers=luis)
    assert malo.status_code == 422 and "base64" in malo.json()["detail"]
    # Archivado: el código deja de servir; el profesor puede sacar a un alumno.
    cliente.patch(f"{API}/grupos/{grupo['id']}", json={"archivado": True}, headers=profesor)
    _, nuevo = crear_cuenta()
    assert cliente.post(f"{API}/unirme", json={"codigo": grupo["codigo"]}, headers=nuevo).status_code == 404
    cliente.delete(f"{API}/grupos/{grupo['id']}/alumnos/{luis_id}", headers=profesor)
    assert len(cliente.get(f"{API}/grupos/{grupo['id']}", headers=profesor).json()["alumnos"]) == 1
    # El profesor no se une a su propio grupo.
    cliente.patch(f"{API}/grupos/{grupo['id']}", json={"archivado": False}, headers=profesor)
    assert cliente.post(f"{API}/unirme", json={"codigo": grupo["codigo"]}, headers=profesor).status_code == 409


def test_los_modelos_sin_recoger_se_borran_a_los_30_dias(cliente, salon):
    asignacion = salon["asignacion"]
    luis_id, luis = salon["alumnos"][0]
    cliente.post(f"{API}/entregas", json={"asignacion_id": asignacion["id"], "modelo": modelo_mesa()}, headers=luis)
    with Session(conexion.motor()) as db:
        entrega = db.get(Entrega, (asignacion["id"], luis_id))
        entrega.entregada_en = ahora() - timedelta(days=31)
        db.commit()
    cliente.get(f"{API}/grupos", headers=salon["profesor"])
    with Session(conexion.motor()) as db:
        entrega = db.get(Entrega, (asignacion["id"], luis_id))
        assert entrega.modelo is None and entrega.modelo_bytes == 0


def test_un_alumno_anonimo_no_se_une(cliente, practica):
    respuesta = cliente.post(f"{API}/unirme", json={"codigo": "AMA-XXXX"}, headers={"X-Usuario-Id": "alumno-1"})
    assert respuesta.status_code == 401
