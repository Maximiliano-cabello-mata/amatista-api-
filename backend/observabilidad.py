"""Errores del backend a Sentry, solo si hay SENTRY_DSN.

Sin SENTRY_DSN (o sin el paquete sentry-sdk) no hace nada: la API funciona
igual que siempre. Nunca se envían datos personales: ni IP, ni cookies, ni
cabeceras de sesión, ni el cuerpo de las peticiones (contraseñas, escenas del
add-on). docs/despliegue/2026-10-31_sentry.md.
"""
from __future__ import annotations

import logging
import os

registro = logging.getLogger("amatista.sentry")

# Cabeceras que pueden identificar a una cuenta o a una sesión.
CABECERAS_PRIVADAS = {"authorization", "cookie", "set-cookie", "x-sesion-id", "x-amatista-huella", "x-forwarded-for", "x-real-ip",
                      "cf-connecting-ip"}


def _limpiar(evento, _pista=None):
    """Quita del evento todo lo que pueda identificar a un alumno."""
    peticion = evento.get("request") or {}
    peticion.pop("cookies", None)
    peticion.pop("data", None)
    peticion.pop("query_string", None)
    cabeceras = peticion.get("headers")
    if isinstance(cabeceras, dict):
        peticion["headers"] = {k: v for k, v in cabeceras.items() if k.lower() not in CABECERAS_PRIVADAS}
    if "env" in peticion:
        peticion["env"] = {}
    evento.pop("user", None)
    return evento


def _proporcion(nombre: str) -> float:
    try:
        return min(max(float(os.getenv(nombre, "0") or 0), 0.0), 1.0)
    except ValueError:
        return 0.0


def iniciar(version: str = "") -> bool:
    """Activa Sentry si SENTRY_DSN existe. Devuelve True si quedó activo."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        registro.warning("SENTRY_DSN está definido pero falta el paquete sentry-sdk (pip install -r requirements.txt)")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENTORNO", "produccion"),
        release=os.getenv("SENTRY_RELEASE", "") or (f"amatista-api@{version}" if version else None),
        send_default_pii=False,
        traces_sample_rate=_proporcion("SENTRY_TRAZAS"),
        before_send=_limpiar,
        before_send_transaction=_limpiar,
        max_request_body_size="never",
    )
    registro.info("Sentry activo (entorno %s)", os.getenv("SENTRY_ENTORNO", "produccion"))
    return True


__all__ = ["CABECERAS_PRIVADAS", "iniciar"]
