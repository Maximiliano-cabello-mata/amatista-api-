"""API de Amatista (FastAPI).

Desarrollo sin Oracle:
    DATABASE_URL=sqlite:///./amatista_local.db uvicorn main:app --reload

Servidor (con backend/.env configurado para Oracle):
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api import addon, admin, auth, blender, contenido, enlace, eventos, niveles, progreso, sesiones
from api.comun import error_bd
from database.conexion import motor, obtener_db
from database.modelos import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Con SQLite las tablas se crean solas. En Oracle se crean con los
    # scripts numerados de backend/sql/ (001 y luego 002, 003...): así no se
    # repiten los problemas de tablas viejas que create_all no corrige.
    if motor().dialect.name == "sqlite":
        Base.metadata.create_all(motor())
    registrar_practicas_del_repositorio()
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


def apagado(nombre: str) -> bool:
    return os.getenv(nombre, "").strip().lower() in ("1", "si", "sí", "true")


# AMATISTA_OCULTAR_DOCS=1 no publica /docs, /redoc ni /openapi.json (producción
# sin Caddy delante). Con Caddy ya responden 404 ahí (despliegue/Caddyfile).
_DOCS = not apagado("AMATISTA_OCULTAR_DOCS")
app = FastAPI(
    title="Amatista API",
    version="0.3.0",
    lifespan=ciclo_de_vida,
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)

# Tamaño máximo de un cuerpo (por defecto 2 MB): la escena más grande del
# add-on pesa unos cientos de KB. Lo mismo hace Caddy con request_body.
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
RUTAS_PRIVADAS = ("/api/auth/", "/api/progreso", "/api/admin/", "/api/addon/v1/yo", "/api/addon/v1/mi-progreso")


@app.middleware("http")
async def proteger(request: Request, siguiente):
    largo = request.headers.get("content-length")
    if largo and largo.isdigit() and int(largo) > MAX_CUERPO:
        return JSONResponse({"detail": "La petición es demasiado grande."}, status_code=413)
    respuesta = await siguiente(request)
    for nombre, valor in CABECERAS_SEGURAS.items():
        respuesta.headers.setdefault(nombre, valor)
    if request.url.path.startswith(RUTAS_PRIVADAS):
        respuesta.headers.setdefault("Cache-Control", "no-store")
    return respuesta


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
    allow_headers=["Content-Type", "Authorization", "X-Sesion-Id", "If-None-Match"],
    expose_headers=["ETag"],
)

app.include_router(auth.router)
app.include_router(sesiones.router)  # /api/iniciar-sesion heredado (alumnos anónimos)
app.include_router(progreso.router)
app.include_router(eventos.router)
app.include_router(admin.router)
app.include_router(contenido.router)
app.include_router(niveles.router)  # reestructuración v3: niveles y mapa del curso
app.include_router(blender.router)  # versiones de Blender y matriz de compatibilidad
app.include_router(addon.router)  # add-on de Blender y motor de prácticas (sql/007)
app.include_router(enlace.router)  # enlace en vivo plataforma ↔ Blender (sql/010, motor 3.4)


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
