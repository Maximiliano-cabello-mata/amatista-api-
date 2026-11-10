"""Puente de solo lectura de G7B hacia Geoda, sin migración Oracle.

No transmite información de alumnos. La publicación pública de G7B solo
contiene conteos agregados; NO convierte documentos de Blender en benchmarks.
Falla con perfiles locales vacíos si G7B o la red están fuera de servicio.
"""
import json
import logging
import os
import threading
import time
from urllib.request import urlopen

from fastapi import APIRouter

router = APIRouter(prefix="/api/g7b/v1", tags=["g7b"])
PUBLICACION = ("https://raw.githubusercontent.com/Maximiliano-cabello-mata/"
               "amatista-g7b-etl/main/publicacion/geoda.json")
TTL = 900  # 15 min: el workflow de G7B actualiza la publicación en GitHub.
MAX_BYTES = 65_536
LOG = logging.getLogger("amatista.g7b")
_CANDADO = threading.Lock()
_cache = None
_vence = 0.0


def _numero(n):
    return type(n) is int and n >= 0


def validar(documento):
    """Acepta solo el contrato público de agregados, nunca identificadores."""
    if not isinstance(documento, dict) or documento.get("contrato") != "g7b.geoda.estado/1":
        raise ValueError("contrato de G7B incompatible")
    cubo, conocimiento = documento.get("cubo"), documento.get("conocimiento")
    if not isinstance(cubo, dict) or not isinstance(conocimiento, dict):
        raise ValueError("G7B: faltan cubo y conocimiento")
    version = cubo.get("version")
    if not isinstance(version, str) or not (1 <= len(version) <= 100):
        raise ValueError("G7B: versión inválida")
    if documento.get("version") != version:
        raise ValueError("G7B: versión no coincide")
    total, aceptadas, revision = (cubo.get("mediciones"), cubo.get("aceptadas"), cubo.get("revision"))
    documentos = conocimiento.get("documentos")
    if (not all(_numero(x) for x in (total, aceptadas, revision, documentos))
            or aceptadas + revision != total):
        raise ValueError("G7B: conteos incompatibles")
    if cubo.get("recomendaciones_habilitadas") is not False or documento.get("perfiles") != []:
        raise ValueError("G7B: recomendaciones sin verificación aún no admitidas")
    return {
        "contrato": "g7b.geoda.estado/1",
        "version": version,
        "cubo": {"version": version, "mediciones": total, "aceptadas": aceptadas,
                 "revision": revision, "recomendaciones_habilitadas": False},
        "conocimiento": {"documentos": documentos},
        "perfiles": [],
    }


def _descargar():
    url = os.getenv("G7B_GEODA_URL", PUBLICACION)
    # Configuración de administración, no parámetro del cliente. HTTPS obligatorio.
    if not url.startswith("https://"):
        raise ValueError("G7B_GEODA_URL debe usar HTTPS")
    with urlopen(url, timeout=4) as respuesta:
        datos = respuesta.read(MAX_BYTES + 1)
    if len(datos) > MAX_BYTES:
        raise ValueError("publicación G7B excesiva")
    return validar(json.loads(datos))


def _reiniciar_cache():
    global _cache, _vence
    with _CANDADO:
        _cache, _vence = None, 0.0


def obtener():
    global _cache, _vence
    ahora = time.monotonic()
    with _CANDADO:
        if ahora < _vence:
            if _cache is not None:
                return {**_cache, "estado": "conectado"}
            return {"contrato": "g7b.geoda.estado/1", "estado": "sin_conexion",
                    "cubo": None, "conocimiento": None, "perfiles": []}
        try:
            nuevo = _descargar()
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            # Un proveedor caído no debe disparar una petición externa por cada alumno.
            _vence = ahora + 60
            LOG.warning("G7B no disponible; se mantienen reglas base: %s", type(error).__name__)
            if _cache is not None:
                return {**_cache, "estado": "cache"}
            return {"contrato": "g7b.geoda.estado/1", "estado": "sin_conexion",
                    "cubo": None, "conocimiento": None, "perfiles": []}
        _cache, _vence = nuevo, ahora + TTL
        return {**nuevo, "estado": "conectado"}


@router.get("/estado")
def estado():
    return obtener()
