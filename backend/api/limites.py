"""Límite de peticiones por IP para las rutas sensibles (en memoria).

Frena el abuso de iniciar-sesion, registro, recuperar y reenviar-codigo
sin depender de Redis ni de otra tabla en Oracle: el servidor ARM corre un
solo proceso de uvicorn, así que basta con un contador por proceso. Si se
reinicia la API los contadores empiezan de cero (aceptable: la cuenta
también se bloquea 15 minutos tras 5 contraseñas incorrectas).

Ventana deslizante: se guardan los instantes de las últimas peticiones de
cada IP+ruta y se descartan los que salieron de la ventana.

Variables:
- AMATISTA_SIN_LIMITES=1 desactiva los límites (pruebas automáticas).
- AMATISTA_PROXY_CONFIABLE=1 toma la IP de X-Forwarded-For. Solo debe
  activarse detrás de Caddy: si la API está expuesta directo, cualquiera
  podría inventar esa cabecera para saltarse el límite.
"""
import hashlib
import math
import os
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Tuple

from fastapi import HTTPException, Request

MENSAJE = "Demasiados intentos seguidos. Espera un momento y vuelve a intentarlo."


class Limitador:
    """Permite como máximo `maximo` peticiones por clave dentro de `ventana` segundos."""

    def __init__(self, maximo: int, ventana: float = 60.0):
        self.maximo = maximo
        self.ventana = ventana
        self._registros: Dict[str, Deque[float]] = {}
        self._candado = threading.Lock()
        self._ultima_limpieza = time.monotonic()

    def intentar(self, clave: str) -> Tuple[bool, int]:
        """Registra una petición. Devuelve (permitida, segundos para reintentar)."""
        momento = time.monotonic()
        limite = momento - self.ventana
        with self._candado:
            self._limpiar(momento, limite)
            registro = self._registros.setdefault(clave, deque())
            while registro and registro[0] <= limite:
                registro.popleft()
            if len(registro) >= self.maximo:
                return False, max(1, math.ceil(registro[0] + self.ventana - momento))
            registro.append(momento)
            return True, 0

    def reiniciar(self) -> None:
        with self._candado:
            self._registros.clear()

    def _limpiar(self, momento: float, limite: float) -> None:
        # Una vez por ventana se borran las IP que ya no tienen peticiones
        # recientes: así la memoria no crece con cada visitante.
        if momento - self._ultima_limpieza < self.ventana:
            return
        self._ultima_limpieza = momento
        for clave in [c for c, r in self._registros.items() if not r or r[-1] <= limite]:
            del self._registros[clave]


_limitadores: List[Limitador] = []


def limites_desactivados() -> bool:
    return os.getenv("AMATISTA_SIN_LIMITES") == "1"


def ip_cliente(request: Request) -> str:
    if os.getenv("AMATISTA_PROXY_CONFIABLE") == "1":
        reenviada = request.headers.get("x-forwarded-for", "")
        # La última entrada es la que agregó nuestro proxy (Caddy); las
        # anteriores las puede escribir el propio cliente.
        partes = [p.strip() for p in reenviada.split(",") if p.strip()]
        if partes:
            return partes[-1]
    return request.client.host if request.client else "desconocida"


def clave_cuenta(request: Request) -> str:
    """La sesión (su hash) si la petición trae token; si no, la IP.

    Un aula entera sale a internet con UNA sola IP: contar por cuenta evita
    que 30 alumnos conectados se frenen entre sí en las rutas con sesión.
    """
    autorizacion = request.headers.get("authorization", "")
    token = autorizacion[7:].strip() if autorizacion.lower().startswith("bearer ") else ""
    token = token or request.headers.get("x-sesion-id", "").strip()
    if token:
        return "t:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    return ip_cliente(request)


def limitar(maximo: int, ventana: float = 60.0, por: str = "ip") -> Callable[[Request], None]:
    """Dependencia de FastAPI: 429 si la IP (o la cuenta) superó `maximo` peticiones en la ventana.

    Uso: @router.post("/ruta", dependencies=[Depends(limitar(10))])
    Cada ruta cuenta por separado (la clave es IP + ruta, o sesión + ruta con
    por="cuenta").
    """
    limitador = Limitador(maximo, ventana)
    _limitadores.append(limitador)
    quien = clave_cuenta if por == "cuenta" else ip_cliente

    def verificar(request: Request) -> None:
        if limites_desactivados():
            return
        permitida, espera = limitador.intentar(f"{quien(request)}|{request.url.path}")
        if not permitida:
            raise HTTPException(status_code=429, detail=MENSAJE, headers={"Retry-After": str(espera)})

    return verificar


def reiniciar_limites() -> None:
    """Vacía todos los contadores (pruebas)."""
    for limitador in _limitadores:
        limitador.reiniciar()
