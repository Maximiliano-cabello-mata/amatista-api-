"""API de Amatista (FastAPI).

Desarrollo sin Oracle:
    DATABASE_URL=sqlite:///./amatista_local.db uvicorn main:app --reload

Servidor (con backend/.env configurado para Oracle):
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
import logging
import os
import zlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api import (
    addon, admin, auth, blender, buzon, contenido, enlace, eventos, niveles, perfil, progreso, sesiones, sincronizar, taller,
)
from api.comun import error_bd
from database.conexion import motor, obtener_db
from database.modelos import Base
import observabilidad

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Con SQLite las tablas se crean solas. En Oracle se crean con los
    # scripts numerados de backend/sql/ (001 y luego 002, 003...): así no se
    # repiten los problemas de tablas viejas que create_all no corrige.
    if motor().dialect.name == "sqlite":
        Base.metadata.create_all(motor())
    registrar_practicas_del_repositorio()
    registrar_habilidades_del_taller()
    yield


def registrar_practicas_del_repositorio():
    """Detecta los practica.json de practices/blender/ y los registra en la base.

    Si falta la tabla PRACTICAS (sql/007 sin aplicar) o la base no responde,
    solo avisa en el log: la API arranca igual.
    """
    if os.getenv("AMATISTA_SINCRONIZAR_PRACTICAS", "1").strip().lower() in ("0", "no", "false"):
        return
    registro = logging.getLogger("amatista.practicas")
    with Session(motor()) as db:
        try:
            resumen = addon.sincronizar_al_arrancar(db)
        except SQLAlchemyError as error:
            db.rollback()
            registro.warning("No se registraron las prácticas del repositorio (¿falta sql/007?): %s", error.__class__.__name__)
            return
    registro.info(
        "Prácticas del repositorio: %d nuevas, %d actualizadas, %d lecciones enlazadas",
        len(resumen["nuevas"]), len(resumen["actualizadas"]), resumen["lecciones_enlazadas"],
    )
    for error in resumen["errores"]:
        registro.warning("Práctica sin registrar: %s", error)


def registrar_habilidades_del_taller():
    """Registra en HABILIDADES las habilidades de practices/blender/habilidades.json (Amatista Taller).

    HABILIDADES_ALUMNO tiene una llave foránea hacia HABILIDADES. Si la base
    no responde o le faltan tablas, solo avisa en el log: la API arranca igual
    y POST /api/addon/v1/habilidades/intentos vuelve a intentarlo.
    """
    if os.getenv("AMATISTA_SINCRONIZAR_PRACTICAS", "1").strip().lower() in ("0", "no", "false"):
        return
    registro = logging.getLogger("amatista.taller")
    with Session(motor()) as db:
        try:
            resumen = taller.sincronizar_al_arrancar(db)
        except SQLAlchemyError as error:
            db.rollback()
            registro.warning("No se registraron las habilidades del taller: %s", error.__class__.__name__)
            return
    registro.info("Habilidades del taller: %d nuevas, %d actualizadas, %d sin su curso en la base",
                  len(resumen["nuevas"]), len(resumen["actualizadas"]), len(resumen["sin_curso"]))


def apagado(nombre: str) -> bool:
    return os.getenv(nombre, "").strip().lower() in ("1", "si", "sí", "true")


# AMATISTA_OCULTAR_DOCS=1 no publica /docs, /redoc ni /openapi.json (producción
# sin Caddy delante). Con Caddy ya responden 404 ahí (despliegue/Caddyfile).
_DOCS = not apagado("AMATISTA_OCULTAR_DOCS")
VERSION_API = "0.3.0"
# Errores a Sentry solo con SENTRY_DSN en backend/.env (observabilidad.py).
observabilidad.iniciar(VERSION_API)
app = FastAPI(
    title="Amatista API",
    version=VERSION_API,
    lifespan=ciclo_de_vida,
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)

# Tamaño máximo de un cuerpo (por defecto 2 MB): la escena más grande del
# add-on pesa unos cientos de KB. Caddy también limita con request_body (1 MB),
# pero la API puede quedar expuesta sin proxy: el límite se cumple aquí también.
MAX_CUERPO = int(os.getenv("AMATISTA_MAX_CUERPO", 2 * 1024 * 1024))

# Cabeceras de seguridad (las mismas que pone Caddy): si la API queda expuesta
# sin proxy, como hoy en el puerto 8000 de la VM, siguen llegando al navegador.
CABECERAS_SEGURAS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "cross-origin",
}
# Respuestas con datos de una cuenta: nunca en cachés compartidas.
RUTAS_PRIVADAS = ("/api/auth/", "/api/progreso", "/api/admin/", "/api/addon/v1/yo", "/api/addon/v1/mi-progreso",
                  "/api/perfil", "/api/buzon")


@app.middleware("http")
async def proteger(request: Request, siguiente):
    respuesta = await siguiente(request)
    for nombre, valor in CABECERAS_SEGURAS.items():
        respuesta.headers.setdefault(nombre, valor)
    if request.url.path.startswith(RUTAS_PRIVADAS):
        respuesta.headers.setdefault("Cache-Control", "no-store")
    return respuesta


class LimitarCuerpo:
    """Rechaza con 413 los cuerpos de más de MAX_CUERPO bytes.

    Con Content-Length se decide antes de leer nada. Sin él (Transfer-Encoding:
    chunked) el cuerpo se lee contando bytes y se corta en cuanto pasa el
    límite: nunca se carga ni se valida un cuerpo enorme.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        largo = dict(scope.get("headers") or ()).get(b"content-length")
        if largo is not None:
            if not largo.isdigit() or int(largo) > MAX_CUERPO:
                await self._demasiado(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        partes, total = [], 0
        while True:
            mensaje = await receive()
            if mensaje["type"] != "http.request":
                return  # el cliente se desconectó
            partes.append(mensaje.get("body", b""))
            total += len(partes[-1])
            if total > MAX_CUERPO:
                await self._demasiado(scope, receive, send)
                return
            if not mensaje.get("more_body"):
                break
        entregado = False

        async def recibir():
            nonlocal entregado
            if not entregado:
                entregado = True
                return {"type": "http.request", "body": b"".join(partes), "more_body": False}
            return await receive()

        await self.app(scope, recibir, send)

    @staticmethod
    async def _demasiado(scope, receive, send):
        await JSONResponse({"detail": "La petición es demasiado grande."}, status_code=413)(scope, receive, send)


class DescomprimirCuerpo:
    """Acepta cuerpos con Content-Encoding: gzip (la PWA comprime los lotes de sincronización).

    Con datos móviles limitados, un lote de progreso y eventos pesa entre 5 y
    10 veces menos comprimido. Va por dentro de LimitarCuerpo (que mide lo
    comprimido) y vuelve a medir al descomprimir: una «bomba» de gzip se
    corta en MAX_CUERPO sin llegar a cargarse entera en memoria.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cabeceras = list(scope.get("headers") or ())
        codificacion = dict(cabeceras).get(b"content-encoding", b"").strip().lower()
        if not codificacion or codificacion == b"identity":
            await self.app(scope, receive, send)
            return
        if codificacion != b"gzip":
            await JSONResponse({"detail": "Solo se acepta Content-Encoding: gzip."}, status_code=415)(scope, receive, send)
            return
        partes = []
        while True:
            mensaje = await receive()
            if mensaje["type"] != "http.request":
                return  # el cliente se desconectó
            partes.append(mensaje.get("body", b""))
            if not mensaje.get("more_body"):
                break
        descompresor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            cuerpo = descompresor.decompress(b"".join(partes), MAX_CUERPO + 1)
        except zlib.error:
            await JSONResponse({"detail": "El cuerpo comprimido no es válido."}, status_code=400)(scope, receive, send)
            return
        if len(cuerpo) > MAX_CUERPO or descompresor.unconsumed_tail:
            await LimitarCuerpo._demasiado(scope, receive, send)
            return
        nuevas = [(k, v) for k, v in cabeceras if k not in (b"content-encoding", b"content-length")]
        nuevas.append((b"content-length", str(len(cuerpo)).encode()))
        entregado = False

        async def recibir():
            nonlocal entregado
            if not entregado:
                entregado = True
                return {"type": "http.request", "body": cuerpo, "more_body": False}
            return await receive()

        await self.app({**scope, "headers": nuevas}, recibir, send)


# El último que se agrega queda por fuera: LimitarCuerpo mide lo que llega y
# después DescomprimirCuerpo mide lo descomprimido.
app.add_middleware(DescomprimirCuerpo)
app.add_middleware(LimitarCuerpo)


class ComprimirJSON(GZipMiddleware):
    """Comprime JSON grandes (catálogo, prácticas); los .zip ya vienen comprimidos.

    Caddy también comprime, pero no vuelve a comprimir lo que ya trae
    Content-Encoding: las dos capas no se pisan.
    """

    async def __call__(self, scope, receive, send):
        ruta = scope.get("path", "")
        if ruta.endswith(".zip") or "/descargas/" in ruta:
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(ComprimirJSON, minimum_size=1024, compresslevel=5)

app.add_middleware(
    CORSMiddleware,
    # Orígenes extra (por ejemplo el dominio de Cloudflare Pages) separados por comas.
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()],
    # Cualquier puerto de localhost: Vite usa 5173, 5174, 5176...
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Content-Encoding", "Authorization", "X-Sesion-Id", "If-None-Match"],
    expose_headers=["ETag"],
)

app.include_router(auth.router)
app.include_router(sesiones.router)  # /api/iniciar-sesion heredado (alumnos anónimos)
app.include_router(progreso.router)
app.include_router(eventos.router)
app.include_router(sincronizar.router)  # progreso + eventos en una petición comprimida (poca conectividad)
app.include_router(admin.router)
app.include_router(contenido.router)
app.include_router(niveles.router)  # reestructuración v3: niveles y mapa del curso
app.include_router(blender.router)  # versiones de Blender y matriz de compatibilidad
app.include_router(addon.router)  # add-on de Blender y motor de prácticas (sql/007)
app.include_router(enlace.router)  # enlace en vivo plataforma ↔ Blender (sql/010, motor 3.4)
app.include_router(taller.router)  # Amatista Taller: el modelo del alumno por habilidad (sql/012, motor 5)
app.include_router(perfil.router)  # avatares low poly de cada cuenta (sql/013)
app.include_router(buzon.router)  # buzón del creador: sugerencias de los alumnos (sql/013)


@app.get("/")
def inicio():
    return {"estado": "Backend activo"}


@app.get("/api/salud")
def salud(db: Session = Depends(obtener_db)):
    """Comprueba que la base de datos responde."""
    try:
        db.execute(select(literal(1)))
    except SQLAlchemyError as error:
        raise error_bd(error, "/api/salud", estado=503)
    return {"estado": "ok", "motor": motor().dialect.name}
