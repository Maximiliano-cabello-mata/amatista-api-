"""Amatista Taller (motor 5) visto desde el backend: el catálogo y la revisión.

El catálogo de habilidades y talleres (practices/blender/habilidades.json,
formato amatista.skill/1) lo lee el MISMO motor que usa el add-on
(engine/amatista_engine/taller). Con él el servidor:

- vuelve a armar cada actividad por su id («conmigo:bl-mover:baja»,
  «proyecto:blender.bp.m1»…) y la práctica que la revisa
  (``practica_de_actividad``), igual que Blender;
- revisa la foto de la escena con esa práctica: el acierto que sube el
  modelo del alumno lo decide el servidor, no el cliente;
- calcula el modelo del alumno y el plan de cada taller (api/taller.py).

Sin base de datos: lo usan la API, la validación de lecciones (bloque
blender_taller) y las pruebas. El catálogo se vuelve a leer solo si el
archivo cambió; si no existe o tiene errores, ``catalogo()`` devuelve None y
la API responde sin talleres (nunca un 500).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from contenido import motor
from amatista_engine.taller.habilidades import Catalogo, ErrorCatalogo, leer_catalogo

log = logging.getLogger("amatista.taller")

# Ruta fija (no depende de motor.CARPETA_PRACTICAS, que las pruebas cambian).
CATALOGO = motor.RAIZ / "practices" / "blender" / "habilidades.json"

_cerrojo = threading.Lock()
_leido: Dict[str, Any] = {"clave": None, "catalogo": None}
_PRACTICAS: Dict[str, Any] = {}  # huella de la definición -> práctica compilada
MAX_PRACTICAS = 500


def catalogo() -> Optional[Catalogo]:
    """El catálogo vigente (se relee si cambió el archivo). None si falta o no se puede usar."""
    ruta = CATALOGO
    try:
        clave = (str(ruta), ruta.stat().st_mtime_ns)
    except OSError:
        return None
    with _cerrojo:
        if _leido["clave"] != clave:
            try:
                _leido["catalogo"] = leer_catalogo(ruta)
            except ErrorCatalogo as error:
                log.warning("El catálogo de habilidades no se pudo leer (%s): %s", ruta.name, error)
                _leido["catalogo"] = None
            _leido["clave"] = clave
        return _leido["catalogo"]


def ids_de_talleres() -> tuple:
    cat = catalogo()
    return tuple(t.id for t in cat.talleres) if cat else ()


def _compilada(definicion: Dict[str, Any]):
    clave = motor.huella(definicion)
    practica = _PRACTICAS.get(clave)
    if practica is None:
        resultado = motor.compilar(definicion)
        if not resultado.ok:
            raise motor.InvalidPracticeError(
                f"La práctica {definicion.get('id')} no compila: {'; '.join(resultado.errors[:3])}"
            )
        practica = resultado.practice
        if len(_PRACTICAS) >= MAX_PRACTICAS:
            _PRACTICAS.clear()
        _PRACTICAS[clave] = practica
    return practica


def revisar_escena(definicion: Dict[str, Any], escena: Dict[str, Any]) -> bool:
    """¿La foto de la escena cumple la práctica? Lanza InvalidPracticeError si la práctica o la escena no sirven.

    Es la misma revisión que hace POST /intentos (contenido.motor.evaluar),
    con la práctica compilada en caché: un envío trae hasta 50 intentos.
    """
    practica = _compilada(definicion)
    try:
        estado = motor.snapshot.scene_from_dict(escena)
    except (TypeError, ValueError, KeyError) as error:
        raise motor.InvalidPracticeError(f"La escena no es válida: {error}") from None
    return bool(motor.MOTOR.evaluate(practica, estado).completed)


__all__ = ["CATALOGO", "catalogo", "ids_de_talleres", "revisar_escena"]
