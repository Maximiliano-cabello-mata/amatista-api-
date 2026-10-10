"""Contenido: validación, plantillas de la fórmula, catálogo con ETag, edición y CLI."""
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from contenido.plantillas import (
    CURSOS_BASE,
    EJEMPLOS_BLOQUES,
    FORMULA,
    LECCIONES_PLANTILLA,
    es_curso_blender,
    generar_esqueleto,
    modulo_esqueleto,
    siguiente_numeracion,
)
from contenido.validacion import (
    BLOQUES_INTERACTIVOS,
    PASOS_FORMULA,
    TIPOS_BLOQUE,
    es_practica_blender,
    validar_leccion,
    validar_modulo,
)
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
CATALOGO = "/api/contenido/catalogo"
CAMPOS_MODULO = ("id", "title", "description", "estimatedTimeMinutes", "order")


def con_modulos(catalogo: dict) -> list:
    """Cursos del catálogo que ya tienen módulos (los cursos base sin módulos también se listan)."""
    return [c for c in catalogo["cursos"] if c["modulos"]]


def leer(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def leccion_con(*bloques, **cambios) -> dict:
    leccion = {
        "id": "les_prueba",
        "title": "Prueba",
        "type": "theory_interactive",
        "isLocked": False,
        "contentBlocks": list(bloques) or [{"type": "markdown_text", "body": "Hola"}],
    }
    leccion.update(cambios)
    return leccion


def importar_reales() -> None:
    assert cli.main(["importar", str(BLENDER), str(AFRAME)]) == 0


# --- Validación ---------------------------------------------------------------


@pytest.mark.parametrize("ruta", sorted(MODULOS.glob("*.json")), ids=lambda r: r.name)
def test_json_del_frontend_validan(ruta):
    assert validar_modulo(leer(ruta)) == []


def test_campos_nuevos_de_los_modulos_reales():
    blender, aframe = leer(BLENDER)["module"], leer(AFRAME)["module"]
    assert (blender["curso"], blender["insignia"], blender["estado"]) == ("blender", "Explorador 3D", "publicado")
    assert (aframe["curso"], aframe["insignia"], aframe["estado"]) == ("aframe", "Arquitecto WebXR", "publicado")


def test_errores_dicen_donde_esta_el_problema():
    modulo = modulo_esqueleto("aframe", 2, "Prueba")
    lecciones = modulo["module"]["lessons"]
    ordenar = next(b for b in lecciones[2]["contentBlocks"] if b["type"] == "ordering")
    ordenar["items"] = ordenar["items"][:2]
    indice_orden = lecciones[2]["contentBlocks"].index(ordenar)
    quiz = next(b for b in lecciones[0]["contentBlocks"] if b["type"] == "quiz_inline")
    for opcion in quiz["options"]:
        opcion["isCorrect"] = False
    lecciones[1]["contentBlocks"][-1]["id"] = lecciones[1]["contentBlocks"][1]["id"]  # id interactivo repetido
    lecciones[3]["id"] = lecciones[0]["id"]  # id de lección repetido
    examen = lecciones[4]["quizData"]
    examen["passingScore"] = 101
    for opcion in examen["questions"][0]["options"]:
        opcion["isCorrect"] = False
    del lecciones[0]["title"]

    errores = validar_modulo(modulo)
    texto = "\n".join(errores)
    assert f"lessons[2].contentBlocks[{indice_orden}] (ordering): necesita entre 3 y 8 items (tiene 2)" in errores
    assert "(quiz_inline): necesita al menos una opción con «isCorrect»: true" in texto
    assert "se repite en la lección" in texto
    assert "lessons[3]: el id «les_aframe_001» se repite (ya lo usa lessons[0])" in errores
    assert "lessons[4].quizData: «passingScore» debe estar entre 0 y 100 (es 101)" in errores
    assert "lessons[4].quizData.questions[0]: necesita al menos una opción" in texto
    assert "lessons[0]: falta «title»" in errores


@pytest.mark.parametrize(
    "bloque, esperado",
    [
        ({"type": "parrafo", "body": "x"}, "tipo de bloque desconocido «parrafo»"),
        ({**EJEMPLOS_BLOQUES["quiz_inline"], "id": None}, "falta «id»"),
        ({**EJEMPLOS_BLOQUES["matching"], "pairs": EJEMPLOS_BLOQUES["matching"]["pairs"][:1]}, "entre 2 y 8 pairs"),
        ({**EJEMPLOS_BLOQUES["fill_blanks"], "template": "Sin huecos"}, "entre 1 y 10 huecos"),
        ({**EJEMPLOS_BLOQUES["fill_blanks"], "template": "<a-[[box]] color=[[]]>"}, "respuesta vacía"),
        (
            {**EJEMPLOS_BLOQUES["hotspots"], "points": [{**EJEMPLOS_BLOQUES["hotspots"]["points"][0], "x": 150}]},
            "«points[0].x» debe estar entre 0 y 100",
        ),
        ({**EJEMPLOS_BLOQUES["scene_explorer"], "primitive": "teapot"}, "«primitive» = «teapot» no es válido"),
        (
            {**EJEMPLOS_BLOQUES["scene_explorer"], "goal": {"param": "scale", "op": ">=", "value": 2, "text": "x"}},
            "no tiene un control",
        ),
        ({**EJEMPLOS_BLOQUES["code_challenge"], "language": "python"}, "«language» = «python» no es válido"),
        ({**EJEMPLOS_BLOQUES["code_challenge"], "checks": []}, "entre 1 y 20 checks"),
        ({**EJEMPLOS_BLOQUES["callout"], "variant": "alerta"}, "«variant» = «alerta» no es válido"),
        ({**EJEMPLOS_BLOQUES["step_by_step"], "steps": []}, "entre 1 y 12 steps"),
        (
            {**EJEMPLOS_BLOQUES["step_by_step"], "steps": [{"title": "x", "keys": "S"}]},
            "«steps[0].keys» debe ser una lista",
        ),
        ({**EJEMPLOS_BLOQUES["shortcuts"], "items": [{"action": "Mover"}]}, "falta «items[0].keys»"),
        ({**EJEMPLOS_BLOQUES["compare"], "mode": "slider"}, "necesita «before.image»"),
        ({**EJEMPLOS_BLOQUES["compare"], "after": {"label": "Después"}}, "«after» necesita «image» o «text»"),
        ({**EJEMPLOS_BLOQUES["mesh_viewer"], "shape": "monkey"}, "«shape» = «monkey» no es válido"),
        ({**EJEMPLOS_BLOQUES["mesh_viewer"], "segments": 200}, "«segments» debe estar entre 3 y 64"),
        ({**EJEMPLOS_BLOQUES["mesh_viewer"], "shape": "custom"}, "necesita «mesh»"),
        (
            {
                **EJEMPLOS_BLOQUES["mesh_viewer"],
                "shape": "custom",
                "mesh": {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 7]]},
            },
            "«mesh.faces[0]» debe tener 3 o más índices",
        ),
        (
            {**EJEMPLOS_BLOQUES["node_graph"], "links": [{"from": "ruido.color", "to": "bsdf.nada"}]},
            "«links[0].to» = «bsdf.nada» no es una entrada",
        ),
        (
            {
                **EJEMPLOS_BLOQUES["node_graph"],
                "links": [{"from": "ruido.color", "to": "bsdf.base"}, {"from": "ruido.color", "to": "bsdf.base"}],
            },
            "ya recibe un enlace",
        ),
        (
            {
                **EJEMPLOS_BLOQUES["node_graph"],
                "nodes": [
                    {
                        "id": "a",
                        "kind": "converter",
                        "title": "Math",
                        "inputs": [{"id": "x", "label": "Valor", "socket": "float"}],
                        "outputs": [{"id": "y", "label": "Valor", "socket": "float"}],
                    }
                ],
                "links": [{"from": "a.y", "to": "a.x"}],
            },
            "forman un ciclo",
        ),
        ({**EJEMPLOS_BLOQUES["node_graph"], "nodes": [{"id": "a", "kind": "rara", "title": "X"}], "links": []}, "«nodes[0].kind»"),
    ],
)
def test_errores_de_bloques(bloque, esperado):
    errores = validar_leccion(leccion_con(bloque))
    assert any(esperado in error for error in errores), errores
    assert all(error.startswith("contentBlocks[0]") for error in errores), errores


def test_herramientas_nuevas_validan():
    for tipo in ("step_by_step", "shortcuts", "compare", "mesh_viewer", "node_graph"):
        assert tipo in TIPOS_BLOQUE and tipo not in BLOQUES_INTERACTIVOS
        assert validar_leccion(leccion_con(EJEMPLOS_BLOQUES[tipo])) == [], tipo


def test_teoria_y_blender_se_intercalan():
    modulo = leer(MODULOS / "blender_principiante-modulo-1.json")
    assert validar_modulo(modulo) == []
    lecciones = modulo["module"]["lessons"]
    tipos = ["blender" if es_practica_blender(l) else l["type"] for l in lecciones]
    # Teoría · Blender · teoría · Blender · examen.
    assert tipos == ["theory_interactive", "blender", "theory_interactive", "blender", "exam"]
    # Una práctica antes de toda la teoría también es válida…
    lecciones.insert(0, lecciones.pop(1))
    assert validar_modulo(modulo) == []
    # …pero la misma práctica dos veces en el módulo no.
    lecciones.append({**lecciones[0], "id": "bp1_repetida"})
    errores = validar_modulo(modulo)
    assert any("ya está en lessons[0]" in e for e in errores), errores


def test_tolerante_con_campos_extra_y_estricto_con_obligatorios():
    bloque = {**EJEMPLOS_BLOQUES["ordering"], "pista": "campo de una versión futura"}
    assert validar_leccion(leccion_con(bloque, autor={"nombre": "Ana"})) == []
    errores = validar_leccion({"id": "les 1", "type": "lectura"})
    assert "«id» = «les 1» solo admite letras, números, guion y guion bajo" in errores
    assert "falta «title»" in errores
    assert any(error.startswith("«type» = «lectura» no es válido") for error in errores)
    assert "falta «isLocked» (true o false)" in errores
    assert validar_leccion([]) == ["la lección debe ser un objeto JSON"]


# --- Plantillas y fórmula --------------------------------------------------------


def test_un_ejemplo_valido_por_tipo_de_bloque():
    assert set(EJEMPLOS_BLOQUES) == set(TIPOS_BLOQUE)
    for tipo, bloque in EJEMPLOS_BLOQUES.items():
        assert bloque["type"] == tipo
        assert validar_leccion(leccion_con(bloque)) == [], tipo


def test_una_leccion_plantilla_por_paso():
    assert [paso["paso"] for paso in FORMULA] == list(PASOS_FORMULA)
    assert all(paso["nombre"] and paso["descripcion"] and paso["duracion"] for paso in FORMULA)
    for paso in PASOS_FORMULA:
        leccion = LECCIONES_PLANTILLA[paso]
        assert leccion["formula"] == paso
        assert validar_leccion(leccion) == [], paso
        assert "Reemplaza este contenido" in json.dumps(leccion, ensure_ascii=False)
        if paso == "jefe":
            assert leccion["type"] == "exam" and leccion["quizData"]["passingScore"] == 80
        else:
            assert any(b["type"] in BLOQUES_INTERACTIVOS for b in leccion["contentBlocks"]), paso


@pytest.mark.parametrize("curso", ["blender", "aframe", "otro"])
def test_generar_esqueleto(curso):
    lecciones = generar_esqueleto(curso, f"mod_{curso}_002", "les_", 5)
    assert [x["id"] for x in lecciones] == ["les_005", "les_006", "les_007", "les_008", "les_009"]
    assert [x["formula"] for x in lecciones] == list(PASOS_FORMULA)
    assert [x["isLocked"] for x in lecciones] == [False, True, True, True, True]
    assert all(validar_leccion(x) == [] for x in lecciones)
    assert validar_modulo(modulo_esqueleto(curso, 2, "Prueba")) == []
    # Cada llamada devuelve copias: editar una no cambia las plantillas.
    lecciones[0]["contentBlocks"][0]["body"] = "cambiado"
    assert LECCIONES_PLANTILLA["gancho"]["contentBlocks"][0]["body"] != "cambiado"


@pytest.mark.parametrize("curso", ["blender_principiante", "blender_principiante_intermedio", "blender_intermedio", "blender"])
def test_plantillas_de_blender_para_los_cursos_vigentes(curso):
    # Los cursos v3 (blender_*) usan la práctica y el reto de Blender, no los retos de código de A-Frame.
    assert es_curso_blender(curso)
    tipos = {
        x["formula"]: (x["type"], [b["type"] for b in x.get("contentBlocks", [])])
        for x in generar_esqueleto(curso, f"mod_{curso}_004", "les_", 1)
    }
    for paso in ("practica", "reto"):
        tipo, bloques = tipos[paso]
        assert tipo == "theory_interactive", (curso, paso)
        assert "code_challenge" not in bloques and "code_interactive" not in bloques
    assert not es_curso_blender("aframe") and not es_curso_blender(None)
    assert generar_esqueleto("aframe", "mod_aframe_004", "les_", 1)[2]["type"] == "code_interactive"


def test_siguiente_numeracion_respeta_prefijos():
    blender = [x["id"] for x in leer(BLENDER)["module"]["lessons"]]
    aframe = [x["id"] for x in leer(AFRAME)["module"]["lessons"]]
    assert siguiente_numeracion(blender, "blender") == ("les_", 5, 3)
    assert siguiente_numeracion(aframe, "aframe") == ("les_af_", 4, 3)
    assert siguiente_numeracion([], "nuevo") == ("les_nuevo_", 1, 3)


# --- Catálogo público --------------------------------------------------------------


def test_catalogo_vacio(cliente):
    respuesta = cliente.get(CATALOGO)
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["cursos"] == []
    assert datos["version"] and datos["generado_en"]
    assert respuesta.headers["etag"] == f'"{datos["version"]}"'


def test_importar_json_reales_y_catalogo_con_el_mismo_formato(cliente, capsys):
    importar_reales()
    datos = cliente.get(CATALOGO).json()
    assert [c["id"] for c in con_modulos(datos)] == ["blender", "aframe"]
    for curso, ruta in zip(con_modulos(datos), (BLENDER, AFRAME)):
        base = CURSOS_BASE[curso["id"]]
        assert (curso["titulo"], curso["numero"], curso["estado"]) == (base["titulo"], base["numero"], "disponible")
        assert curso["recurso"] == {"texto": base["recurso_texto"], "url": base["recurso_url"]}
        archivo = leer(ruta)["module"]
        assert len(curso["modulos"]) == 1
        modulo = curso["modulos"][0]
        assert (modulo["id"], modulo["insignia"], modulo["numero"]) == (archivo["id"], archivo["insignia"], 1)
        contenido = modulo["contenido"]
        assert set(contenido) == {*CAMPOS_MODULO, "lessons"}
        assert {c: contenido[c] for c in CAMPOS_MODULO} == {c: archivo[c] for c in CAMPOS_MODULO}
        # Las lecciones viajan exactamente como están en el archivo (ids, orden y bloques).
        assert contenido["lessons"] == archivo["lessons"]

    # Importar otra vez no cambia nada (ni la versión del catálogo).
    capsys.readouterr()
    importar_reales()
    assert "0 nuevas, 0 actualizadas" in capsys.readouterr().out
    assert cliente.get(CATALOGO).json()["version"] == datos["version"]


def test_catalogo_etag_y_304(cliente, crear_cuenta):
    importar_reales()
    primera = cliente.get(CATALOGO)
    version = primera.json()["version"]
    for cabecera in (version, f'"{version}"', f'W/"{version}"', f'"otra", "{version}"'):
        respuesta = cliente.get(CATALOGO, headers={"If-None-Match": cabecera})
        assert respuesta.status_code == 304, cabecera
        assert respuesta.content == b""
        assert respuesta.headers["etag"] == f'"{version}"'
    assert cliente.get(CATALOGO, headers={"If-None-Match": '"otra"'}).status_code == 200

    # Un cambio publicado cambia la versión.
    _, admin = crear_cuenta(rol="admin")
    assert cliente.post("/api/contenido/lecciones/blender/les_003/archivar", headers=admin).status_code == 200
    despues = cliente.get(CATALOGO, headers={"If-None-Match": version})
    assert despues.status_code == 200
    assert despues.json()["version"] != version
    lecciones = con_modulos(despues.json())[0]["modulos"][0]["contenido"]["lessons"]
    assert [x["id"] for x in lecciones] == ["les_001", "les_002", "les_004"]


# --- Edición desde el panel ----------------------------------------------------------


def test_flujo_completo_de_un_modulo(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    api = "/api/contenido"
    curso = {"id": "blender", "titulo": "Blender", "numero": "01", "estado": "publicado"}
    assert cliente.post(f"{api}/cursos", json=curso, headers=admin).status_code == 201
    assert cliente.post(f"{api}/cursos", json=curso, headers=admin).status_code == 409

    # Módulo nuevo con el esqueleto de la fórmula: todo en borrador.
    respuesta = cliente.post(
        f"{api}/modulos",
        json={"curso_id": "blender", "titulo": "Módulo 2: Interfaz y navegación", "numero": 2,
              "insignia": "Navegante", "generar_esqueleto": True},
        headers=admin,
    )
    assert respuesta.status_code == 201
    modulo = respuesta.json()
    assert (modulo["id"], modulo["estado"]) == ("mod_blender_002", "borrador")
    ids = [x["id"] for x in modulo["lecciones"]]
    assert ids == [f"les_blender_{n:03d}" for n in range(1, 6)]
    assert {x["estado"] for x in modulo["lecciones"]} == {"borrador"}

    # Sin publicar aparece como "Próximamente".
    catalogo = cliente.get(CATALOGO).json()
    assert catalogo["cursos"][0]["modulos"] == [
        {"id": "mod_blender_002", "numero": 2, "nivel_id": None, "titulo": "Interfaz y navegación",
         "insignia": "Navegante", "contenido": None}
    ]

    # Editar una lección: valida y sube la versión.
    gancho = cliente.get(f"{api}/lecciones/blender/les_blender_001", headers=admin).json()
    assert gancho["leccion"]["formula"] == "gancho"
    gancho["leccion"]["title"] = "¿Cómo ve el mundo una computadora?"
    respuesta = cliente.put(f"{api}/lecciones/blender/les_blender_001", json={"leccion": gancho["leccion"]}, headers=admin)
    assert respuesta.status_code == 200
    assert (respuesta.json()["version"], respuesta.json()["valida"], respuesta.json()["titulo"]) == (
        2, True, "¿Cómo ve el mundo una computadora?")

    # Un borrador se guarda aunque tenga errores, pero no se publica.
    practica = cliente.get(f"{api}/lecciones/blender/les_blender_003", headers=admin).json()["leccion"]
    rota = copy.deepcopy(practica)
    ordenar = next(b for b in rota["contentBlocks"] if b["type"] == "ordering")
    ordenar["items"] = ordenar["items"][:2]
    respuesta = cliente.put(f"{api}/lecciones/blender/les_blender_003", json={"leccion": rota}, headers=admin)
    assert respuesta.status_code == 200
    assert respuesta.json()["valida"] is False
    assert any("(ordering): necesita entre 3 y 8 items" in e for e in respuesta.json()["errores"])
    validacion = cliente.post(f"{api}/validar", json={"leccion": rota}, headers=admin).json()
    assert validacion["valida"] is False and validacion["errores"]
    respuesta = cliente.post(f"{api}/lecciones/blender/les_blender_003/publicar", headers=admin)
    assert respuesta.status_code == 422
    assert respuesta.json()["errores"] and isinstance(respuesta.json()["detail"], str)

    # Publicar el módulo publica las lecciones válidas y omite la rota.
    respuesta = cliente.post(f"{api}/modulos/mod_blender_002/publicar", headers=admin)
    assert respuesta.status_code == 200
    assert respuesta.json()["publicadas"] == ["les_blender_001", "les_blender_002", "les_blender_004", "les_blender_005"]
    assert [o["id"] for o in respuesta.json()["omitidas"]] == ["les_blender_003"]
    contenido = cliente.get(CATALOGO).json()["cursos"][0]["modulos"][0]["contenido"]
    assert contenido["title"] == "Módulo 2: Interfaz y navegación" and contenido["order"] == 2
    assert [x["id"] for x in contenido["lessons"]] == ["les_blender_001", "les_blender_002", "les_blender_004", "les_blender_005"]
    assert contenido["lessons"][0]["title"] == "¿Cómo ve el mundo una computadora?"

    # Al corregirla se publica y aparece en su lugar.
    assert cliente.put(f"{api}/lecciones/blender/les_blender_003", json={"leccion": practica}, headers=admin).json()["valida"]
    assert cliente.post(f"{api}/lecciones/blender/les_blender_003/publicar", headers=admin).status_code == 200
    contenido = cliente.get(CATALOGO).json()["cursos"][0]["modulos"][0]["contenido"]
    assert [x["id"] for x in contenido["lessons"]] == ids

    # Lo publicado no acepta cambios inválidos.
    respuesta = cliente.put(f"{api}/lecciones/blender/les_blender_003", json={"leccion": rota}, headers=admin)
    assert respuesta.status_code == 422 and respuesta.json()["errores"]

    # El id es inmutable.
    respuesta = cliente.put(
        f"{api}/lecciones/blender/les_blender_003", json={"leccion": {**practica, "id": "les_otro"}}, headers=admin
    )
    assert respuesta.status_code == 400
    assert cliente.get(f"{api}/lecciones/blender/les_otro", headers=admin).status_code == 404
    assert cliente.put(f"{api}/modulos/mod_blender_002", json={"id": "mod_otro"}, headers=admin).status_code == 400
    assert cliente.put(f"{api}/cursos/blender", json={"id": "otro"}, headers=admin).status_code == 400
    duplicada = {"curso_id": "blender", "modulo_id": "mod_blender_002", "leccion": practica}
    assert cliente.post(f"{api}/lecciones", json=duplicada, headers=admin).status_code == 409

    # Lección nueva sin id: toma el siguiente libre y se inserta en la posición pedida.
    nueva = {k: v for k, v in LECCIONES_PLANTILLA["explora"].items() if k != "id"}
    respuesta = cliente.post(
        f"{api}/lecciones",
        json={"curso_id": "blender", "modulo_id": "mod_blender_002", "leccion": nueva, "posicion": 1},
        headers=admin,
    )
    assert respuesta.status_code == 201
    assert (respuesta.json()["id"], respuesta.json()["orden"], respuesta.json()["estado"]) == ("les_blender_006", 1, "borrador")
    assert respuesta.json()["leccion"]["id"] == "les_blender_006"

    # Mover reordena todo el módulo.
    respuesta = cliente.post(f"{api}/lecciones/blender/les_blender_006/mover", json={"orden": 99}, headers=admin)
    assert [x["id"] for x in respuesta.json()["lecciones"]] == [*ids, "les_blender_006"]
    assert [x["orden"] for x in respuesta.json()["lecciones"]] == [1, 2, 3, 4, 5, 6]
    respuesta = cliente.post(f"{api}/lecciones/blender/les_blender_005/mover", json={"orden": 1}, headers=admin)
    assert [x["id"] for x in respuesta.json()["lecciones"]][:2] == ["les_blender_005", "les_blender_001"]

    # Archivar una lección la saca del catálogo, pero sigue en el árbol del panel.
    assert cliente.post(f"{api}/lecciones/blender/les_blender_002/archivar", headers=admin).json()["estado"] == "archivado"
    contenido = cliente.get(CATALOGO).json()["cursos"][0]["modulos"][0]["contenido"]
    assert [x["id"] for x in contenido["lessons"]] == ["les_blender_005", "les_blender_001", "les_blender_003", "les_blender_004"]
    arbol = cliente.get(f"{api}/admin/arbol", headers=admin).json()
    estados = {x["id"]: x["estado"] for x in arbol["cursos"][0]["modulos"][0]["lecciones"]}
    assert estados["les_blender_002"] == "archivado" and estados["les_blender_006"] == "borrador"
    assert "leccion" not in arbol["cursos"][0]["modulos"][0]["lecciones"][0]
    assert arbol["cursos"][0]["modulos"][0]["lecciones"][0]["practica_blender"] is False

    # Exportar da el formato de archivo del frontend (solo lo publicado).
    exportado = cliente.get(f"{api}/modulos/mod_blender_002/exportar", headers=admin).json()
    assert validar_modulo(exportado) == []
    assert (exportado["module"]["curso"], exportado["module"]["insignia"], exportado["module"]["estado"]) == (
        "blender", "Navegante", "publicado")
    assert [x["id"] for x in exportado["module"]["lessons"]] == [x["id"] for x in contenido["lessons"]]
    con_borradores = cliente.get(f"{api}/modulos/mod_blender_002/exportar?borradores=true", headers=admin).json()
    assert "les_blender_006" in [x["id"] for x in con_borradores["module"]["lessons"]]

    # Archivar el módulo lo saca del catálogo (nada se borra).
    assert cliente.post(f"{api}/modulos/mod_blender_002/archivar", headers=admin).json()["estado"] == "archivado"
    assert cliente.get(CATALOGO).json()["cursos"][0]["modulos"] == []
    assert cliente.get(f"{api}/lecciones/blender/les_blender_001", headers=admin).status_code == 200


def test_publicar_modulo_sin_lecciones_validas(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    api = "/api/contenido"
    cliente.post(f"{api}/cursos", json={"id": "aframe", "titulo": "A-Frame", "estado": "publicado"}, headers=admin)
    modulo = cliente.post(f"{api}/modulos", json={"curso_id": "aframe", "titulo": "Vacío"}, headers=admin).json()
    assert modulo["id"] == "mod_aframe_001" and modulo["lecciones"] == []
    respuesta = cliente.post(f"{api}/modulos/{modulo['id']}/publicar", headers=admin)
    assert respuesta.status_code == 422
    assert cliente.get(CATALOGO).json()["cursos"][0]["modulos"][0]["contenido"] is None


def test_permisos(cliente, crear_cuenta):
    api = "/api/contenido"
    _, alumno = crear_cuenta(rol="alumno")
    _, profesor = crear_cuenta(rol="profesor")
    modulo = {"curso_id": "blender", "titulo": "Nuevo"}

    assert cliente.get(CATALOGO).status_code == 200  # público
    assert cliente.get(f"{api}/admin/arbol").status_code == 401
    for metodo, ruta, cuerpo in [
        ("get", "/admin/arbol", None),
        ("get", "/plantillas", None),
        ("post", "/validar", {"leccion": leccion_con()}),
        ("post", "/modulos", modulo),
    ]:
        respuesta = cliente.request(metodo.upper(), f"{api}{ruta}", json=cuerpo, headers=alumno)
        assert respuesta.status_code == 403, ruta

    # El profesor lee y valida, pero no modifica.
    assert cliente.get(f"{api}/admin/arbol", headers=profesor).json() == {"cursos": []}
    plantillas = cliente.get(f"{api}/plantillas", headers=profesor).json()
    assert [p["paso"] for p in plantillas["formula"]] == list(PASOS_FORMULA)
    assert set(plantillas["bloques"]) == set(TIPOS_BLOQUE) and set(plantillas["lecciones"]) == set(PASOS_FORMULA)
    assert cliente.post(f"{api}/validar", json={"leccion": leccion_con()}, headers=profesor).json() == {
        "valida": True, "errores": []}
    for metodo, ruta, cuerpo in [
        ("post", "/cursos", {"id": "nuevo", "titulo": "Nuevo"}),
        ("put", "/cursos/blender", {"titulo": "Otro"}),
        ("post", "/modulos", modulo),
        ("post", "/modulos/mod_teoria_001/publicar", None),
        ("post", "/lecciones", {"curso_id": "blender", "modulo_id": "x", "leccion": leccion_con()}),
        ("put", "/lecciones/blender/les_001", {"leccion": leccion_con()}),
        ("post", "/lecciones/blender/les_001/archivar", None),
    ]:
        respuesta = cliente.request(metodo.upper(), f"{api}{ruta}", json=cuerpo, headers=profesor)
        assert respuesta.status_code == 403, ruta


# --- CLI -------------------------------------------------------------------------


def test_cli_validar_json_reales(capsys):
    assert cli.main(["validar"]) == 0
    assert " 0 con errores." in capsys.readouterr().out


def test_cli_validar_json_roto(tmp_path, capsys):
    mal_formado = tmp_path / "blender-modulo-3.json"
    mal_formado.write_text('{"module": {', encoding="utf-8")
    assert cli.main(["validar", str(mal_formado)]) == 1
    assert "JSON mal formado" in capsys.readouterr().out

    invalido = tmp_path / "blender-modulo-2.json"
    datos = leer(BLENDER)
    datos["module"]["order"] = 2
    datos["module"]["lessons"][3]["quizData"]["passingScore"] = 150
    invalido.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["validar", str(invalido)]) == 1
    assert "lessons[3].quizData: «passingScore» debe estar entre 0 y 100" in capsys.readouterr().out

    # Entre archivos: los ids de lecciones no se repiten dentro del curso.
    datos["module"]["lessons"][3]["quizData"]["passingScore"] = 80
    datos["module"]["id"] = "mod_teoria_002"
    invalido.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["validar", str(BLENDER), str(invalido)]) == 1
    assert "ya lo usa blender-modulo-1.json (mismo curso)" in capsys.readouterr().out


def test_cli_validar_como_script():
    resultado = subprocess.run(
        [sys.executable, "herramientas/contenido.py", "validar"], cwd=BACKEND, capture_output=True, text=True
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def test_cli_nuevo_modulo(tmp_path, capsys):
    shutil.copy(BLENDER, tmp_path / BLENDER.name)
    argumentos = ["nuevo-modulo", "blender", "2", "Interfaz y navegación", "--insignia", "Navegante",
                  "--destino", str(tmp_path)]
    assert cli.main(argumentos) == 0
    archivo = tmp_path / "blender-modulo-2.json"
    datos = leer(archivo)
    assert validar_modulo(datos) == []
    modulo = datos["module"]
    assert (modulo["id"], modulo["title"], modulo["order"]) == ("mod_blender_002", "Módulo 2: Interfaz y navegación", 2)
    assert (modulo["curso"], modulo["insignia"], modulo["estado"]) == ("blender", "Navegante", "borrador")
    assert [x["id"] for x in modulo["lessons"]] == ["les_005", "les_006", "les_007", "les_008", "les_009"]
    assert [x["formula"] for x in modulo["lessons"]] == list(PASOS_FORMULA)
    assert cli.main(["validar", str(tmp_path / BLENDER.name), str(archivo)]) == 0
    # No sobrescribe sin --forzar.
    assert cli.main(argumentos) == 1
    assert cli.main([*argumentos, "--forzar"]) == 0


def test_cli_exportar(cliente, tmp_path):
    importar_reales()
    salida = tmp_path / "exportado.json"
    assert cli.main(["exportar", "mod_teoria_001", str(salida)]) == 0
    exportado, original = leer(salida)["module"], leer(BLENDER)["module"]
    assert validar_modulo({"module": exportado}) == []
    for campo in (*CAMPOS_MODULO, "curso", "insignia", "estado", "lessons"):
        assert exportado[campo] == original[campo], campo
    assert cli.main(["exportar", "mod_que_no_existe"]) == 1


def test_cli_importar_rechaza_archivos_con_errores(cliente, tmp_path):
    roto = tmp_path / "aframe-modulo-1.json"
    datos = leer(AFRAME)
    datos["module"]["lessons"][0]["contentBlocks"] = []
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["importar", str(roto)]) == 1
    assert cliente.get(CATALOGO).json()["cursos"] == []


def test_texto_json_lee_igual_el_dict_de_oracle_y_el_texto_de_sqlite():
    # python-oracledb entrega como dict las columnas con CHECK (... IS JSON).
    from database.modelos import TextoJSON

    tipo = TextoJSON()
    assert tipo.process_result_value({"id": "les_001", "title": "Ñandú"}, None) == '{"id":"les_001","title":"Ñandú"}'
    assert tipo.process_result_value([1, 2], None) == "[1,2]"
    assert tipo.process_result_value('{"a":1}', None) == '{"a":1}'
    assert tipo.process_result_value(None, None) is None


def test_arbol_marca_la_practica_en_blender(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    assert cli.main(["importar", str(BLENDER), str(MODULOS / "archivo" / "blender-modulo-2.json")]) == 0
    arbol = cliente.get("/api/contenido/admin/arbol", headers=admin).json()
    lecciones = {
        x["id"]: x["practica_blender"]
        for curso in arbol["cursos"]
        for modulo in curso["modulos"]
        for x in modulo["lecciones"]
    }
    practicas = [i for i, v in lecciones.items() if v]
    assert len(practicas) == 1 and len(lecciones) > 1
