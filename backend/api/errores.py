"""Errores con origen: cada respuesta de error dice qué API y qué operación falló.

Formato (el campo `detail` no cambia; la web y el complemento lo siguen leyendo):

    {
      "detail": "No existe esa práctica.",
      "error": {
        "api": "motor",                                   # api/registro.py
        "operacion": "registrar_intento",                 # la función de la ruta
        "codigo": "motor.registrar_intento.no_encontrado",
        "estado": 404
      }
    }

Toda respuesta lleva además la cabecera X-Peticion-Id. Con ese identificador se
busca la petición en los registros (`amatista registros`). En un error interno
(500) el identificador también va en el cuerpo, como `error.peticion`.

El bloque `error` no lleva nada propio de la petición salvo en los 500: dos
respuestas al mismo fallo son idénticas (no revela, por ejemplo, si un correo
existe).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.registro import api_de

log = logging.getLogger("amatista.errores")
CABECERA = "X-Peticion-Id"

# Nombre estable de cada estado dentro del código de error.
MOTIVOS = {
    400: "peticion_invalida", 401: "sin_sesion", 403: "sin_permiso", 404: "no_encontrado",
    405: "metodo_no_permitido", 409: "conflicto", 410: "ya_no_existe", 413: "demasiado_grande",
    415: "formato_no_admitido", 422: "datos_invalidos", 423: "bloqueado", 429: "demasiados_intentos",
    500: "error_interno", 502: "servicio_externo", 503: "no_disponible", 504: "sin_respuesta",
}


def id_de(request: Request) -> str:
    """Identificador de la petición (lo pone el middleware de abajo)."""
    return getattr(request.state, "peticion", "") or ""


def origen(request: Request, estado: int) -> dict:
    """El bloque `error`: API, operación, código estable y estado."""
    api = api_de(request.url.path)
    ruta = request.scope.get("route")
    operacion: Optional[str] = getattr(ruta, "name", None)
    partes = [api] + ([operacion] if operacion else []) + [MOTIVOS.get(estado, f"estado_{estado}")]
    return {"api": api, "operacion": operacion, "codigo": ".".join(partes), "estado": estado}


def _respuesta(request: Request, estado: int, detalle: Any, cabeceras: Optional[dict] = None) -> JSONResponse:
    error = origen(request, estado)
    if estado >= 500:
        error["peticion"] = id_de(request)
        log.error("%s (%s %s, petición %s)", error["codigo"], request.method, request.url.path, id_de(request))
    return JSONResponse({"detail": detalle, "error": error}, status_code=estado, headers=cabeceras)


def instalar(app: FastAPI) -> None:
    """Middleware del identificador de petición y manejadores de error."""

    @app.middleware("http")
    async def identificar(request: Request, siguiente):
        request.state.peticion = uuid.uuid4().hex[:12]
        respuesta = await siguiente(request)
        respuesta.headers.setdefault(CABECERA, request.state.peticion)
        return respuesta

    @app.exception_handler(StarletteHTTPException)
    async def error_http(request: Request, error: StarletteHTTPException):
        return _respuesta(request, error.status_code, error.detail, getattr(error, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def error_validacion(request: Request, error: RequestValidationError):
        return _respuesta(request, 422, jsonable_encoder(error.errors()))

    @app.exception_handler(Exception)
    async def error_inesperado(request: Request, error: Exception):
        # El detalle completo queda en el registro; al cliente solo le llega el identificador.
        log.exception("Error no controlado en %s %s (petición %s)", request.method, request.url.path, id_de(request))
        respuesta = _respuesta(request, 500, f"Error interno (petición {id_de(request)}).")
        respuesta.headers[CABECERA] = id_de(request)
        return respuesta
