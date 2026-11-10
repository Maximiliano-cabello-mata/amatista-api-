"""Dónde están los otros repositorios de Amatista en esta computadora.

La API usa el mismo motor que Blender para volver a evaluar cada intento, y
arma las descargas del complemento al vuelo. Ese código vive en el
repositorio del motor (`engine/`, `addon/` y `practices/`). Las lecciones que
valida `herramientas/contenido.py` viven en el de la web (`frontend/`).

Orden de búsqueda:
  1. Variable de entorno: AMATISTA_MOTOR_DIR o AMATISTA_WEB_DIR (la raíz del
     checkout).
  2. Carpeta hermana de este repositorio (`../amatista-motor-`, `../amatista`...).
  3. Este mismo árbol, si es el monorepo original.

El motor es obligatorio: sin él la API no arranca y el error dice cómo
conseguirlo. La web es opcional: solo la usan pruebas de contrato y la
herramienta de contenido.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]

_MOTOR = ("AMATISTA_MOTOR_DIR", ("amatista-motor-", "amatista-motor", "amatista"), "engine")
_WEB = ("AMATISTA_WEB_DIR", ("amatista-web-", "amatista-web", "amatista"), "frontend")


def _buscar(variable: str, hermanos, marca: str) -> Optional[Path]:
    candidatos = []
    if os.getenv(variable):
        candidatos.append(Path(os.environ[variable]).expanduser())
    candidatos.append(REPO)  # monorepo: todo junto
    candidatos += [REPO.parent / hermano for hermano in hermanos]
    return next((c.resolve() for c in candidatos if (c / marca).is_dir()), None)


def motor() -> Path:
    """Raíz del repositorio del motor (contiene engine/, addon/ y practices/)."""
    raiz = _buscar(*_MOTOR)
    if raiz is None:
        raise RuntimeError(
            "No se encontró el repositorio del motor de Amatista. Clónalo junto a este "
            f"({REPO.parent / 'amatista-motor-'}) o define AMATISTA_MOTOR_DIR con su ruta."
        )
    return raiz


def web() -> Optional[Path]:
    """Raíz del repositorio que contiene frontend/, o None si no está."""
    return _buscar(*_WEB)


def requerir_web() -> Path:
    """Para pruebas de contrato: la carpeta frontend/, o salta la prueba diciendo qué falta."""
    import pytest

    raiz = web()
    if raiz is None:
        pytest.skip(
            "Prueba de contrato: necesita el repositorio que contiene frontend/ "
            "(clónalo junto a este o define AMATISTA_WEB_DIR).",
            allow_module_level=True,
        )
    return raiz / "frontend"
