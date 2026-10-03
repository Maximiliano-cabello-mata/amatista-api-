"""Versiones de Blender y matriz de compatibilidad (política de la sección 5).

- GET /api/blender/versiones (público): versiones y su categoría. La PWA
  muestra «Verificada en…» y el futuro add-on compara la versión del
  alumno con esta lista.
- PUT /api/blender/versiones/{version} (admin): crea o cambia una versión.
  Solo hay una «principal»: al marcar otra, la anterior pasa a «compatible».
- POST /api/blender/verificaciones (profesor o admin): anota una prueba de
  una lección en una versión y un sistema operativo.
- GET /api/blender/compatibilidad?curso_id=blender (profesor o admin): la
  matriz lección x versión con la prueba más reciente de cada par.

La versión principal todavía no está decidida (docs/reestructuracion/,
decisiones pendientes): la tabla nace vacía y nada aquí la inventa.
"""
import re
import uuid
from datetime import date, datetime
from typing import Dict, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from api.contenido import confirmar, iso, obtener_leccion
from api.dependencias import requiere_rol
from database.conexion import obtener_db
from database.modelos import Leccion, Usuario, VerificacionBlender, VersionBlender, ahora

router = APIRouter(prefix="/api/blender", tags=["blender"])

lector = requiere_rol("profesor", "admin")
editor = requiere_rol("admin")

PATRON_VERSION = re.compile(r"^\d+\.\d+(\.\d+)?$")
Categoria = Literal["principal", "compatible", "sin_verificar", "retirada"]
Resultado = Literal["verificada", "con_diferencias", "falla"]


def clave_version(version: str) -> Tuple[int, ...]:
    """4.2.10 va después de 4.2.9 (orden numérico, no de texto)."""
    return tuple(int(parte) for parte in version.split("."))


def version_meta(fila: VersionBlender) -> dict:
    return {
        "version": fila.version,
        "categoria": fila.categoria,
        "es_lts": bool(fila.es_lts),
        "soporte_hasta": fila.soporte_hasta.date().isoformat() if fila.soporte_hasta else None,
        "notas": fila.notas,
        "actualizado_en": iso(fila.actualizado_en),
    }


def verificacion_meta(fila: VerificacionBlender) -> dict:
    return {
        "id": fila.id,
        "curso_id": fila.curso_id,
        "leccion_id": fila.leccion_id,
        "version_blender": fila.version_blender,
        "sistema": fila.sistema,
        "version_leccion": fila.version_leccion,
        "version_addon": fila.version_addon,
        "resultado": fila.resultado,
        "diferencias": fila.diferencias,
        "evidencia": fila.evidencia,
        "responsable": fila.responsable,
        "verificado_en": iso(fila.verificado_en),
    }


@router.get("/versiones")
def versiones(db: Session = Depends(obtener_db)):
    filas = sorted(db.scalars(select(VersionBlender)), key=lambda f: clave_version(f.version), reverse=True)
    principal = next((f.version for f in filas if f.categoria == "principal"), None)
    return {"principal": principal, "versiones": [version_meta(f) for f in filas]}


class CambiosVersion(BaseModel):
    categoria: Categoria = "sin_verificar"
    es_lts: bool = False
    soporte_hasta: Optional[date] = None
    notas: Optional[str] = Field(default=None, max_length=500)


@router.put("/versiones/{version}")
def guardar_version(
    version: str, cuerpo: CambiosVersion, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)
):
    if len(version) > 20 or not PATRON_VERSION.match(version):
        raise HTTPException(status_code=422, detail="La versión debe ser como 4.2 o 4.2.3.")
    momento = ahora()
    if cuerpo.categoria == "principal":
        for otra in db.scalars(
            select(VersionBlender).where(VersionBlender.categoria == "principal", VersionBlender.version != version)
        ):
            otra.categoria = "compatible"
            otra.actualizado_en = momento
    fila = db.get(VersionBlender, version)
    if fila is None:
        fila = VersionBlender(version=version)
        db.add(fila)
    fila.categoria = cuerpo.categoria
    fila.es_lts = int(cuerpo.es_lts)
    fila.soporte_hasta = datetime.combine(cuerpo.soporte_hasta, datetime.min.time()) if cuerpo.soporte_hasta else None
    fila.notas = cuerpo.notas
    fila.actualizado_en = momento
    confirmar(db, f"/api/blender/versiones/{version}")
    return version_meta(fila)


class VerificacionNueva(BaseModel):
    curso_id: str = Field(min_length=1, max_length=50)
    leccion_id: str = Field(min_length=1, max_length=50)
    version_blender: str = Field(min_length=1, max_length=20)
    sistema: str = Field(min_length=1, max_length=60)
    resultado: Resultado
    version_addon: Optional[str] = Field(default=None, max_length=20)
    diferencias: Optional[str] = Field(default=None, max_length=1000)
    evidencia: Optional[str] = Field(default=None, max_length=300)


@router.post("/verificaciones", status_code=201)
def registrar_verificacion(
    cuerpo: VerificacionNueva, db: Session = Depends(obtener_db), usuario: Usuario = Depends(lector)
):
    leccion = obtener_leccion(db, cuerpo.curso_id, cuerpo.leccion_id)
    if db.get(VersionBlender, cuerpo.version_blender) is None:
        raise HTTPException(
            status_code=409, detail=f"Registra antes la versión {cuerpo.version_blender} (PUT /api/blender/versiones)."
        )
    fila = VerificacionBlender(
        id=str(uuid.uuid4()),
        version_leccion=leccion.version,
        responsable=(usuario.nombre or usuario.email or usuario.id)[:100],
        verificado_en=ahora(),
        **cuerpo.model_dump(),
    )
    db.add(fila)
    confirmar(db, "/api/blender/verificaciones")
    return verificacion_meta(fila)


@router.get("/compatibilidad")
def compatibilidad(curso_id: str = "blender", db: Session = Depends(obtener_db), _: Usuario = Depends(lector)):
    """Para cada lección y versión vigente: la prueba más reciente y si sigue valiendo.

    estado: «sin verificar», «repetir» (la lección cambió después de la
    prueba) o «vigente».
    """
    versiones_vigentes = sorted(
        (f for f in db.scalars(select(VersionBlender)) if f.categoria != "retirada"),
        key=lambda f: clave_version(f.version),
        reverse=True,
    )
    lecciones = list(
        db.scalars(
            select(Leccion)
            .options(defer(Leccion.contenido))
            .where(Leccion.curso_id == curso_id, Leccion.estado != "archivado")
            .order_by(Leccion.modulo_id, Leccion.orden)
        )
    )
    ultimas: Dict[Tuple[str, str], VerificacionBlender] = {}
    for fila in db.scalars(
        select(VerificacionBlender)
        .where(VerificacionBlender.curso_id == curso_id)
        .order_by(VerificacionBlender.verificado_en, VerificacionBlender.id)
    ):
        ultimas[(fila.leccion_id, fila.version_blender)] = fila

    filas = []
    for leccion in lecciones:
        celdas = {}
        for version in versiones_vigentes:
            prueba = ultimas.get((leccion.id, version.version))
            if prueba is None:
                estado = "sin verificar"
            elif prueba.version_leccion < leccion.version:
                estado = "repetir"
            else:
                estado = "vigente"
            celdas[version.version] = {
                "estado": estado,
                "resultado": prueba.resultado if prueba else None,
                "sistema": prueba.sistema if prueba else None,
                "verificado_en": iso(prueba.verificado_en) if prueba else None,
            }
        filas.append(
            {
                "leccion_id": leccion.id,
                "modulo_id": leccion.modulo_id,
                "titulo": leccion.titulo,
                "version": leccion.version,
                "versiones": celdas,
            }
        )
    return {
        "curso_id": curso_id,
        "versiones": [version_meta(v) for v in versiones_vigentes],
        "lecciones": filas,
    }
