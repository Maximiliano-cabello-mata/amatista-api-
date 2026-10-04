"""Reestructuración v3: niveles, ficha de lección, mapa, versiones de Blender y CLI."""
import json
import re
import shutil
from pathlib import Path

import pytest

from api.niveles import id_nivel
from contenido.plantillas import ESTRUCTURA_LECCION, NIVELES_BLENDER, leccion_estructurada, modulo_esqueleto
from contenido.validacion import pendientes_ficha, validar_leccion, validar_modulo
from herramientas import contenido as cli

BACKEND = Path(__file__).resolve().parent.parent
MODULOS = BACKEND.parent / "frontend" / "src" / "data" / "modulos"
def _como_publicado(ruta: Path) -> Path:
    """El módulo 1 del curso v2 quedó archivado (motor v3); estas pruebas lo usan publicado como ejemplo."""
    import tempfile

    copia = Path(tempfile.mkdtemp()) / ruta.name
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["module"]["estado"] = "publicado"
    copia.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    return copia


BLENDER = _como_publicado(MODULOS / "archivo" / "blender-modulo-1.json")
AFRAME = MODULOS / "aframe-modulo-1.json"
SQL_006 = BACKEND / "sql" / "006_herramientas_autor.sql"
API = "/api/contenido"
CATALOGO = "/api/contenido/catalogo"


def con_modulos(catalogo: dict) -> list:
    """Cursos del catálogo que ya tienen módulos (los cursos base sin módulos también se listan)."""
    return [c for c in catalogo["cursos"] if c["modulos"]]


def leer(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def importar_reales() -> None:
    assert cli.main(["importar", str(BLENDER), str(AFRAME)]) == 0


def ficha_completa(**cambios) -> dict:
    ficha = {
        "objetivo": "Crear una mesa con proporciones coherentes.",
        "habilidades": ["bl-extruir"],
        "blender": {"verificadaEn": "4.2", "notas": ""},
        "comprobacion": ["Cuatro patas", "Archivo guardado"],
    }
    ficha.update(cambios)
    return ficha


# --- Plantillas y ficha -------------------------------------------------------


def test_niveles_de_la_propuesta():
    assert [n["numero"] for n in NIVELES_BLENDER] == [1, 2, 3, 4, 5, 5, 5, 5]
    for nivel in NIVELES_BLENDER:
        assert nivel["id"] == id_nivel("blender", nivel["numero"], nivel["rama"])
        assert (nivel["rama"] is None) == (nivel["numero"] < 5)


def test_leccion_estructurada_valida_y_con_los_10_pasos():
    leccion = leccion_estructurada("les_n1_mesa", "Construir una mesa", "Crear una mesa estable.")
    assert validar_leccion(leccion) == []
    assert len(leccion["contentBlocks"]) == len(ESTRUCTURA_LECCION) == 10
    assert leccion["contentBlocks"][0]["body"] == "Crear una mesa estable."
    assert pendientes_ficha(leccion) == ["habilidades", "versión de Blender verificada", "criterios de comprobación"]
    assert pendientes_ficha(leccion, requiere_blender=False) == ["habilidades", "criterios de comprobación"]


def test_006_arma_la_misma_estructura_que_las_plantillas():
    """El paquete de Oracle y la CLI deben crear las mismas lecciones."""
    pasos = re.findall(r"paso\(v_bloques, '(\w+)', '([^']+)', '([^']+)'", SQL_006.read_text(encoding="utf-8"))
    assert pasos == ESTRUCTURA_LECCION


@pytest.mark.parametrize(
    "ficha, esperado",
    [
        ("texto", "«ficha» debe ser un objeto"),
        (ficha_completa(habilidades="bl-extruir"), "«ficha.habilidades» debe ser una lista de ids"),
        (ficha_completa(habilidades=["a b"]), "«ficha.habilidades» debe ser una lista de ids"),
        (ficha_completa(habilidades=[f"h{i}" for i in range(9)]), "admite como máximo 8"),
        (ficha_completa(blender={"verificadaEn": "4.x"}), "debe ser una versión como 4.2"),
        (ficha_completa(blender="4.2"), "«ficha.blender» debe ser un objeto"),
        (ficha_completa(comprobacion=["", "ok"]), "«ficha.comprobacion» debe ser una lista"),
        (ficha_completa(offline="si"), "«ficha.offline» debe ser true o false"),
        (ficha_completa(objetivo="x" * 301), "ficha.objetivo"),
    ],
)
def test_errores_de_la_ficha(ficha, esperado):
    leccion = leccion_estructurada("les_x", "X")
    leccion["ficha"] = ficha
    errores = validar_leccion(leccion)
    assert any(esperado in e for e in errores), errores


def test_ficha_completa_sin_pendientes_y_lecciones_viejas_siguen_validando():
    leccion = leccion_estructurada("les_x", "X")
    leccion["ficha"] = ficha_completa()
    assert validar_leccion(leccion) == [] and pendientes_ficha(leccion) == []
    viejo = leer(BLENDER)
    assert validar_modulo(viejo) == []
    assert all(pendientes_ficha(x) == ["ficha"] for x in viejo["module"]["lessons"])


def test_modulo_con_nivel_invalido():
    datos = modulo_esqueleto("blender", 2, "Interfaz", nivel_id="blender n1")
    assert any("nivel" in e for e in validar_modulo(datos))
    assert validar_modulo(modulo_esqueleto("blender", 2, "Interfaz", nivel_id="blender-n1")) == []


# --- API de niveles y mapa ----------------------------------------------------


def test_importar_crea_niveles_y_asigna_el_modulo(cliente, crear_cuenta):
    importar_reales()
    _, admin = crear_cuenta(rol="admin")
    niveles = cliente.get(f"{API}/niveles?curso_id=blender", headers=admin).json()["niveles"]
    # Ordenados por número; las ramas del nivel 5, por nombre.
    assert [n["id"] for n in niveles] == [n["id"] for n in sorted(NIVELES_BLENDER, key=lambda n: (n["numero"], n["rama"] or ""))]
    assert {n["estado"] for n in niveles} == {"borrador"}

    # En el catálogo: el módulo dice su nivel; los niveles en borrador no aparecen.
    catalogo = cliente.get(CATALOGO).json()
    blender = con_modulos(catalogo)[0]
    assert blender["niveles"] == []
    assert blender["modulos"][0]["nivel_id"] == "blender-n1"
    assert con_modulos(catalogo)[1]["modulos"][0]["nivel_id"] is None

    # Publicar el nivel cambia la versión del catálogo y lo muestra.
    respuesta = cliente.put(f"{API}/niveles/blender-n1", json={"estado": "publicado"}, headers=admin)
    assert respuesta.status_code == 200
    despues = cliente.get(CATALOGO, headers={"If-None-Match": catalogo["version"]})
    assert despues.status_code == 200
    nivel = con_modulos(despues.json())[0]["niveles"][0]
    assert (nivel["id"], nivel["numero"], nivel["titulo"]) == ("blender-n1", 1, "Desde cero")
    assert "estado" not in nivel and "curso_id" not in nivel

    # Exportar conserva el nivel del módulo.
    exportado = cliente.get(f"{API}/modulos/mod_teoria_001/exportar", headers=admin).json()
    assert exportado["module"]["nivel"] == "blender-n1"

    # Sembrar otra vez no cambia nada.
    assert cliente.post(f"{API}/niveles/sembrar", headers=admin).json() == {"creados": []}


def test_crear_editar_y_asignar_niveles(cliente, crear_cuenta):
    importar_reales()
    _, admin = crear_cuenta(rol="admin")
    nuevo = {"curso_id": "aframe", "numero": 1, "titulo": "Primeros pasos en WebXR"}
    respuesta = cliente.post(f"{API}/niveles", json=nuevo, headers=admin)
    assert respuesta.status_code == 201 and respuesta.json()["id"] == "aframe-n1"
    assert cliente.post(f"{API}/niveles", json=nuevo, headers=admin).status_code == 409
    rama_mala = {**nuevo, "numero": 2, "rama": "web"}
    assert cliente.post(f"{API}/niveles", json=rama_mala, headers=admin).status_code == 422
    rama = cliente.post(f"{API}/niveles", json={**nuevo, "numero": 5, "rama": "juegos"}, headers=admin)
    assert rama.json()["id"] == "aframe-n5-juegos"
    assert cliente.post(f"{API}/niveles", json={**nuevo, "numero": 6}, headers=admin).status_code == 422

    # Un módulo solo puede ir a un nivel de su curso; null lo saca del nivel.
    mover = cliente.put(f"{API}/modulos/mod_aframe_001", json={"nivel_id": "blender-n2"}, headers=admin)
    assert mover.status_code == 409
    assert cliente.put(f"{API}/modulos/mod_aframe_001", json={"nivel_id": "no-existe"}, headers=admin).status_code == 404
    mover = cliente.put(f"{API}/modulos/mod_aframe_001", json={"nivel_id": "aframe-n1"}, headers=admin)
    assert mover.status_code == 200 and mover.json()["nivel_id"] == "aframe-n1"
    quitar = cliente.put(f"{API}/modulos/mod_aframe_001", json={"nivel_id": None}, headers=admin)
    assert quitar.json()["nivel_id"] is None

    # Crear un módulo directamente en un nivel.
    creado = cliente.post(
        f"{API}/modulos",
        json={"curso_id": "blender", "titulo": "Navegación", "nivel_id": "blender-n1", "generar_esqueleto": True},
        headers=admin,
    )
    assert creado.status_code == 201 and creado.json()["nivel_id"] == "blender-n1"
    arbol = {c["id"]: c for c in cliente.get(f"{API}/admin/arbol", headers=admin).json()["cursos"]}
    assert [n["id"] for n in arbol["blender"]["niveles"]][:2] == ["blender-n1", "blender-n2"]


def test_mapa_del_curso(cliente, crear_cuenta):
    importar_reales()
    _, admin = crear_cuenta(rol="admin")
    leccion = leccion_estructurada("les_n1_mesa", "Construir una mesa", "Crear una mesa.")
    leccion["ficha"] = ficha_completa()
    assert cliente.post(
        f"{API}/lecciones", json={"curso_id": "blender", "modulo_id": "mod_teoria_001", "leccion": leccion}, headers=admin
    ).status_code == 201
    mapa = cliente.get(f"{API}/mapa/blender", headers=admin).json()
    assert mapa["resumen"] == {"lecciones": 5, "fichas_completas": 1}
    nivel1 = mapa["niveles"][0]
    assert nivel1["id"] == "blender-n1" and nivel1["modulos"][0]["id"] == "mod_teoria_001"
    mesa = nivel1["modulos"][0]["lecciones"][-1]
    assert (mesa["id"], mesa["pendientes"], mesa["blender"]) == ("les_n1_mesa", [], "4.2")
    assert mapa["sin_nivel"] == []
    aframe = cliente.get(f"{API}/mapa/aframe", headers=admin).json()
    assert aframe["niveles"] == [] and aframe["sin_nivel"][0]["id"] == "mod_aframe_001"
    assert cliente.get(f"{API}/mapa/no-existe", headers=admin).status_code == 404


def test_importar_con_nivel_inexistente_falla(cliente, tmp_path):
    datos = leer(BLENDER)
    datos["module"]["nivel"] = "blender-n9"
    archivo = tmp_path / "blender-modulo-1.json"
    archivo.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["importar", str(archivo)]) == 1


def test_permisos_de_niveles_y_blender(cliente, crear_cuenta):
    importar_reales()
    _, alumno = crear_cuenta(rol="alumno")
    _, profesor = crear_cuenta(rol="profesor")
    assert cliente.get(f"{API}/niveles", headers=alumno).status_code == 403
    assert cliente.get(f"{API}/mapa/blender", headers=alumno).status_code == 403
    assert cliente.get(f"{API}/mapa/blender", headers=profesor).status_code == 200
    assert cliente.post(f"{API}/niveles/sembrar", headers=profesor).status_code == 403
    assert cliente.put("/api/blender/versiones/4.2", json={}, headers=profesor).status_code == 403
    assert cliente.get("/api/blender/compatibilidad", headers=alumno).status_code == 403
    assert cliente.get("/api/blender/versiones").status_code == 200  # público


# --- Versiones de Blender y matriz --------------------------------------------


def test_versiones_y_matriz_de_compatibilidad(cliente, crear_cuenta):
    importar_reales()
    _, admin = crear_cuenta(rol="admin")
    _, profesor = crear_cuenta(rol="profesor", nombre="Profe")
    assert cliente.get("/api/blender/versiones").json() == {"principal": None, "versiones": []}

    assert cliente.put("/api/blender/versiones/4.x", json={}, headers=admin).status_code == 422
    uno = cliente.put("/api/blender/versiones/4.2.9", json={"categoria": "principal", "es_lts": True,
                                                           "soporte_hasta": "2026-07-01"}, headers=admin)
    assert uno.status_code == 200 and uno.json()["soporte_hasta"] == "2026-07-01"
    cliente.put("/api/blender/versiones/4.2.10", json={"categoria": "principal"}, headers=admin)
    versiones = cliente.get("/api/blender/versiones").json()
    # Orden numérico y una sola principal.
    assert versiones["principal"] == "4.2.10"
    assert [(v["version"], v["categoria"]) for v in versiones["versiones"]] == [
        ("4.2.10", "principal"), ("4.2.9", "compatible")]

    prueba = {"curso_id": "blender", "leccion_id": "les_002", "version_blender": "4.2.10",
              "sistema": "Windows 11", "resultado": "verificada"}
    assert cliente.post("/api/blender/verificaciones", json={**prueba, "version_blender": "9.9"},
                        headers=profesor).status_code == 409
    assert cliente.post("/api/blender/verificaciones", json={**prueba, "leccion_id": "les_x"},
                        headers=profesor).status_code == 404
    hecha = cliente.post("/api/blender/verificaciones", json=prueba, headers=profesor)
    assert hecha.status_code == 201
    assert (hecha.json()["responsable"], hecha.json()["version_leccion"]) == ("Profe", 1)

    matriz = cliente.get("/api/blender/compatibilidad", headers=profesor).json()
    fila = next(x for x in matriz["lecciones"] if x["leccion_id"] == "les_002")
    assert fila["versiones"]["4.2.10"]["estado"] == "vigente"
    assert fila["versiones"]["4.2.9"]["estado"] == "sin verificar"

    # Si la lección cambia, la prueba hay que repetirla.
    leccion = cliente.get(f"{API}/lecciones/blender/les_002", headers=admin).json()["leccion"]
    leccion["title"] = "Diccionario 3D (revisado)"
    assert cliente.put(f"{API}/lecciones/blender/les_002", json={"leccion": leccion}, headers=admin).status_code == 200
    matriz = cliente.get("/api/blender/compatibilidad", headers=profesor).json()
    fila = next(x for x in matriz["lecciones"] if x["leccion_id"] == "les_002")
    assert fila["versiones"]["4.2.10"]["estado"] == "repetir"

    # Una versión retirada sale de la matriz.
    cliente.put("/api/blender/versiones/4.2.9", json={"categoria": "retirada"}, headers=admin)
    matriz = cliente.get("/api/blender/compatibilidad", headers=profesor).json()
    assert [v["version"] for v in matriz["versiones"]] == ["4.2.10"]


# --- CLI ----------------------------------------------------------------------


def test_cli_nueva_leccion_mapa_y_nuevo_modulo_con_nivel(tmp_path, capsys):
    shutil.copy(BLENDER, tmp_path / BLENDER.name)
    assert cli.main(["nuevo-modulo", "blender", "2", "Interfaz y navegación", "--nivel", "blender-n1",
                     "--destino", str(tmp_path)]) == 0
    archivo = tmp_path / "blender-modulo-2.json"
    assert leer(archivo)["module"]["nivel"] == "blender-n1"

    assert cli.main(["nueva-leccion", str(archivo), "les_n1_mesa", "Construir una mesa",
                     "--objetivo", "Crear una mesa estable."]) == 0
    modulo = leer(archivo)["module"]
    assert modulo["lessons"][-1]["id"] == "les_n1_mesa"
    assert modulo["lessons"][-1]["isLocked"] is True
    assert validar_modulo({"module": modulo}) == []
    assert cli.main(["nueva-leccion", str(archivo), "les_n1_mesa", "Otra"]) == 1
    assert cli.main(["nueva-leccion", str(archivo), "id malo", "Otra"]) == 2

    capsys.readouterr()
    assert cli.main(["mapa", "blender", "--carpeta", str(tmp_path)]) == 0
    salida = capsys.readouterr().out
    assert "Nivel 1: Desde cero" in salida
    assert "les_n1_mesa Construir una mesa  (falta: habilidades" in salida
    assert "de 10 lecciones con la ficha completa" in salida
    assert cli.main(["nuevo-modulo", "blender", "3", "X", "--nivel", "mal nivel", "--destino", str(tmp_path)]) == 2


def test_cli_sembrar_niveles(cliente, capsys):
    assert cli.main(["sembrar-niveles"]) == 0
    assert "blender-n1" in capsys.readouterr().out
    assert cli.main(["sembrar-niveles"]) == 0
    assert "ya existían" in capsys.readouterr().out
