"""Amatista Engine y el constructor del add-on, vistos desde el backend.

El servidor usa el MISMO motor que Blender (engine/amatista_engine, sin bpy)
para compilar las prácticas que suben los desarrolladores y para volver a
evaluar la foto de la escena que manda el add-on: el progreso que se guarda
en Oracle lo calcula el servidor, no el cliente.

El constructor (addon/herramientas/construir.py) arma las descargas al vuelo
desde el código del repositorio: no hay binarios guardados.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

RAIZ = Path(__file__).resolve().parents[2]
CARPETA_PRACTICAS = RAIZ / "practices" / "blender"

for _carpeta in (RAIZ / "engine", RAIZ / "addon" / "herramientas"):
    if str(_carpeta) not in sys.path:
        sys.path.insert(0, str(_carpeta))

import amatista_engine  # noqa: E402
import construir  # noqa: E402
from amatista_engine import pedagogy, snapshot  # noqa: E402
from amatista_engine.errors import InvalidPracticeError  # noqa: E402
from amatista_engine.practice import compile_practice, parse_practice  # noqa: E402

MOTOR = amatista_engine.create_default_engine()
VERSION_MOTOR = amatista_engine.__version__
VERSION_ADDON = construir.VERSION
BLENDER_MINIMO = construir.leer_manifiesto()["blender_version_min"]

__all__ = [
    "MOTOR", "VERSION_MOTOR", "VERSION_ADDON", "BLENDER_MINIMO", "InvalidPracticeError",
    "compilar", "huella", "evaluar", "ejemplo_de", "practicas_del_repositorio", "construir", "pedagogy",
]


def canonico(definicion: Dict[str, Any]) -> str:
    return json.dumps(definicion, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def huella(definicion: Dict[str, Any]) -> str:
    """SHA-256 del JSON canónico sin el número de versión (que pone el servidor)."""
    sin_version = {k: v for k, v in definicion.items() if k != "version"}
    return hashlib.sha256(canonico(sin_version).encode("utf-8")).hexdigest()


def compilar(definicion: Any):
    """CompileResult del motor (errores que impiden guardar y avisos)."""
    return compile_practice(definicion, MOTOR.registry, MOTOR.tools)


def evaluar(definicion: Dict[str, Any], escena: Dict[str, Any], pistas: Dict[str, int], correcciones: int):
    """Evalúa la foto de la escena. Lanza InvalidPracticeError si la foto no sirve."""
    practica = parse_practice(definicion)
    try:
        estado = snapshot.scene_from_dict(escena)
    except (TypeError, ValueError, KeyError) as error:
        raise InvalidPracticeError(f"La escena no es válida: {error}") from None
    reporte = MOTOR.evaluate(practica, estado)
    autonomia = pedagogy.classify(reporte.completed, pistas, correcciones)
    return practica, reporte, autonomia


_EJEMPLOS: Dict[str, Any] = {}


def ejemplo_de(definicion: Dict[str, Any]):
    """El ejemplo resuelto para la lección (motor 3.5): pasos en palabras, en código y qué revisa. None si no hay."""
    if not isinstance(definicion, dict) or not isinstance(definicion.get("example"), dict):
        return None
    clave = huella(definicion)
    if clave in _EJEMPLOS:
        return _EJEMPLOS[clave]
    from amatista_engine.ejemplo import ASPECTOS, aspectos_del_ejemplo, describir, escena_esperada, lo_que_pide
    from amatista_engine.practice.loader import pieza_como_dict

    try:
        practica = parse_practice(definicion)
    except InvalidPracticeError:
        return None
    if practica.example is None:
        return None
    pasos = list(practica.example.steps)
    partes = [pieza_como_dict(p) for p in practica.reference.compared] if practica.reference else []
    aspectos = list(practica.example.check) or aspectos_del_ejemplo(escena_esperada(pasos, partes), lo_que_pide(pasos))
    salida = {
        "titulo": practica.example.title or practica.title,
        "descripcion": practica.example.description,
        "pasos": describir(pasos, partes),
        "revisa": [ASPECTOS[a] for a in aspectos],
        "codigo": pasos,
    }
    if len(_EJEMPLOS) > 200:
        _EJEMPLOS.clear()
    _EJEMPLOS[clave] = salida
    return salida


def practicas_del_repositorio() -> List[Tuple[Path, Dict[str, Any]]]:
    """Las prácticas del plan de estudios: practices/blender/<curso>/<módulo>/practica.json.

    cursos.json y los pruebas.json viven en las mismas carpetas pero no son
    prácticas; practices/archivo/ (las prácticas v2) no se publica.
    """
    encontradas = []
    for ruta in sorted(CARPETA_PRACTICAS.rglob("*.json")):
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(datos, dict) and str(datos.get("schema", "")).startswith("amatista.practice/"):
            encontradas.append((ruta, datos))
    return encontradas
