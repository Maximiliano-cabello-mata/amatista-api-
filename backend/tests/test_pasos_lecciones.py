"""Los pasos sin conexión de cada lección (`steps` del bloque blender_practice en
frontend/src/data/modulos/*.json) son los mismos que entrega el servidor con pasos_de().

Si cambia una práctica, regenera los `steps` con los títulos de pasos_de(definición).
"""
import json
from pathlib import Path

import pytest
import vecinos

from api.motor.addon import pasos_de
from contenido.motor import practicas_del_repositorio

RAIZ = Path(__file__).resolve().parents[2]
MODULOS = sorted((vecinos.requerir_web() / "src" / "data" / "modulos").glob("*.json"))


def bloques_practica():
    """Genera un parámetro de pytest por cada bloque blender_practice de los módulos del frontend."""
    for ruta in MODULOS:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        for leccion in datos.get("module", {}).get("lessons", []):
            for bloque in leccion.get("contentBlocks", []):
                if bloque.get("type") == "blender_practice":
                    yield pytest.param(bloque, id=f"{ruta.name}:{bloque.get('practica')}")


DEFINICIONES = {definicion["id"]: definicion for _, definicion in practicas_del_repositorio()}


def test_hay_practicas_en_las_lecciones():
    """Comprueba que los módulos del frontend enlazan al menos 9 prácticas de Blender."""
    assert len(list(bloques_practica())) >= 9  # el proyecto de cada módulo (motor 5: el taller va aparte)


@pytest.mark.parametrize("bloque", list(bloques_practica()))
def test_pasos_de_la_leccion_coinciden_con_el_servidor(bloque):
    """Comprueba que los «steps» del bloque coinciden con los pasos que calcula el servidor para esa práctica."""
    assert bloque["practica"] in DEFINICIONES, f"«{bloque['practica']}» no existe en practices/blender"
    esperados = [paso["titulo"] for paso in pasos_de(DEFINICIONES[bloque["practica"]])]
    assert bloque.get("steps") == esperados
