"""API de Amatista (FastAPI).

Desarrollo sin Oracle:
    DATABASE_URL=sqlite:///./amatista_local.db uvicorn main:app --reload

Servidor (con backend/.env configurado para Oracle):
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api import progreso, sesiones
from api.comun import error_bd
from database.conexion import motor, obtener_db
from database.modelos import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Con SQLite las tablas se crean solas. En Oracle se crean con
    # backend/sql/001_esquema_amatista.sql (así no se repiten los problemas
    # de tablas viejas que create_all no corrige).
    if motor().dialect.name == "sqlite":
        Base.metadata.create_all(motor())
    yield


app = FastAPI(title="Amatista API", version="0.2.0", lifespan=ciclo_de_vida)

app.add_middleware(
    CORSMiddleware,
    # Orígenes extra (por ejemplo el dominio de Cloudflare Pages) separados por comas.
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()],
    # Cualquier puerto de localhost: Vite usa 5173, 5174, 5176...
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(sesiones.router)
app.include_router(progreso.router)


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
