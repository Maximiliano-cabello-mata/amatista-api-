"""Los pasos sin conexión de cada lección (`steps` del bloque blender_practice en
frontend/src/data/modulos/*.json) son los mismos que entrega el servidor con pasos_de().

Si cambia una práctica, regenera los `steps` con los títulos de pasos_de(definición).
"""
import json
from pathlib import Path

import pytest

from api.addon import pasos_de
from contenido.motor import practicas_del_repositorio

RAIZ = Path(__file__).resolve().parents[2]
MODULOS = sorted((RAIZ / "frontend" / "src" / "data" / "modulos").glob("*.json"))


def bloques_practica():
    for ruta in MODULOS:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        for leccion in datos.get("module", {}).get("lessons", []):
            for bloque in leccion.get("contentBlocks", []):
                if bloque.get("type") == "blender_practice":
                    yield pytest.param(bloque, id=f"{ruta.name}:{bloque.get('practica')}")


DEFINICIONES = {definicion["id"]: definicion for _, definicion in practicas_del_repositorio()}


def test_hay_practicas_en_las_lecciones():
    assert len(list(bloques_practica())) >= 18


@pytest.mark.parametrize("bloque", list(bloques_practica()))
def test_pasos_de_la_leccion_coinciden_con_el_servidor(bloque):
    assert bloque["practica"] in DEFINICIONES, f"«{bloque['practica']}» no existe en practices/blender"
    esperados = [paso["titulo"] for paso in pasos_de(DEFINICIONES[bloque["practica"]])]
    assert bloque.get("steps") == esperados
