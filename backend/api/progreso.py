"""Progreso de los alumnos: una fila por alumno y lección, e insignias.

Identidad: con sesión se escribe siempre en la cuenta de la sesión; sin
sesión solo en alumnos anónimos (ver resolver_alumno). Contratos tolerantes:
los campos nuevos (datos_ligeros, completada_en, insignias) son opcionales,
así que la PWA vieja que manda {usuario_id, eventos:[...]} sigue funcionando.
"""
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import asegurar_usuario, error_bd
from api.dependencias import (
    REFRESCO_ACCESO,
    puede_ver_alumno,
    resolver_alumno,
    usuario_opcional,
    usuario_requerido,
)
from database.conexion import obtener_db
from database.modelos import Logro, ProgresoLeccion, Usuario, ahora

router = APIRouter(prefix="/api", tags=["progreso"])

# Tamaño de PROGRESO_LECCIONES.DATOS_LIGEROS (VARCHAR2(250 CHAR)).
MAX_DATOS = 250

Insignia = Annotated[str, Field(min_length=1, max_length=100)]


class EventoProgreso(BaseModel):
    curso_id: str = Field(min_length=1, max_length=50)
    leccion_id: str = Field(min_length=1, max_length=50)
    completada: bool = False
    puntaje: Optional[int] = Field(default=None, ge=0, le=100)
    intentos: int = Field(default=0, ge=0, le=99999)
    actualizado_en: Optional[datetime] = None
    # Fecha en que el dispositivo completó la lección (opcional; si falta se
    # usa actualizado_en): la actividad offline conserva su día.
    completada_en: Optional[datetime] = None
    # Objeto JSON o texto con un objeto JSON. Lo que no sea un objeto o no
    # quepa en 250 caracteres se ignora (sin 422: la cola no se atasca).
    datos_ligeros: Optional[Any] = None


class SolicitudProgreso(BaseModel):
    usuario_id: Optional[str] = Field(default=None, max_length=100)
    eventos: List[EventoProgreso] = Field(default_factory=list, max_length=500)
    insignias: Optional[List[Insignia]] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def con_algo_que_guardar(self):
        if not self.eventos and not self.insignias:
            raise ValueError("Envía al menos un evento de progreso o una insignia.")
        return self


# --- Ayudantes (también los usa el panel de administración) -------------------


def a_utc(fecha: Optional[datetime]) -> Optional[datetime]:
    """Fecha en UTC sin zona, como se guarda. Sin zona se asume UTC."""
    if fecha is not None and fecha.tzinfo is not None:
        fecha = fecha.astimezone(timezone.utc).replace(tzinfo=None)
    return fecha


def compactar(objeto: dict) -> str:
    # allow_nan=False: NaN no es JSON válido (Oracle lo rechazaría con IS JSON).
    return json.dumps(objeto, separators=(",", ":"), allow_nan=False)


def leer_objeto(valor: Any) -> Optional[dict]:
    """Objeto JSON a partir de un dict o de un texto JSON; None si no es un objeto."""
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return None
    return valor if isinstance(valor, dict) else None


def datos_validos(valor: Any) -> Optional[dict]:
    """datos_ligeros del dispositivo si es un objeto no vacío que cabe en 250 caracteres."""
    objeto = leer_objeto(valor)
    if not objeto:
        return None
    try:
        return objeto if len(compactar(objeto)) <= MAX_DATOS else None
    except (TypeError, ValueError):
        return None


def unir_datos(guardado: Optional[dict], nuevo: Optional[dict]) -> Optional[dict]:
    """Unión de objetos (lo nuevo gana). Si la unión no cabe, se queda solo lo nuevo."""
    if not nuevo:
        return guardado
    if not guardado:
        return nuevo
    union = {**guardado, **nuevo}
    return union if len(compactar(union)) <= MAX_DATOS else nuevo


def minimo_no_nulo(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    fechas = [f for f in (a, b) if f is not None]
    return min(fechas) if fechas else None


def iso(fecha: Optional[datetime]) -> Optional[str]:
    return fecha.isoformat() if fecha else None


def registrar_acceso(usuario: Optional[Usuario], momento: datetime) -> None:
    """Los alumnos anónimos no pasan por usuario_opcional: su último acceso se
    actualiza al sincronizar, como mucho cada 10 minutos (pocas escrituras)."""
    if usuario is not None and (
        usuario.ultimo_acceso is None or momento - usuario.ultimo_acceso > REFRESCO_ACCESO
    ):
        usuario.ultimo_acceso = momento


def fila_publica(fila: ProgresoLeccion) -> dict:
    return {
        "curso_id": fila.curso_id,
        "leccion_id": fila.leccion_id,
        "completada": bool(fila.completada),
        "puntaje": fila.puntaje,
        "intentos": fila.intentos or 0,
        "datos_ligeros": leer_objeto(fila.datos_ligeros) if fila.datos_ligeros else None,
        "completada_en": iso(fila.completada_en),
        "actualizado_en": iso(fila.actualizado_en),
    }


def progreso_de(db: Session, usuario_id: str) -> dict:
    """Progreso e insignias de un alumno con el formato de GET /api/progreso."""
    filas = db.scalars(
        select(ProgresoLeccion)
        .where(ProgresoLeccion.usuario_id == usuario_id)
        .order_by(ProgresoLeccion.curso_id, ProgresoLeccion.leccion_id)
    ).all()
    logros = db.scalars(
        select(Logro).where(Logro.usuario_id == usuario_id).order_by(Logro.obtenido_en, Logro.insignia_id)
    ).all()
    return {
        "usuario_id": usuario_id,
        "lecciones": [fila_publica(f) for f in filas],
        "insignias": [{"id": logro.insignia_id, "obtenido_en": iso(logro.obtenido_en)} for logro in logros],
    }


# --- Escritura -----------------------------------------------------------------


def avance_de(evento: EventoProgreso, momento: datetime) -> dict:
    """Evento normalizado: fechas en UTC (nunca en el futuro) y datos validados."""
    completada_en = None
    if evento.completada:
        completada_en = a_utc(evento.completada_en) or a_utc(evento.actualizado_en) or momento
        completada_en = min(completada_en, momento)
    return {
        "completada": evento.completada,
        "puntaje": evento.puntaje,
        "intentos": evento.intentos,
        "completada_en": completada_en,
        "datos": datos_validos(evento.datos_ligeros),
    }


def combinar(a: dict, b: dict) -> dict:
    """Une dos eventos de la misma lección sin perder avance."""
    puntajes = [p for p in (a["puntaje"], b["puntaje"]) if p is not None]
    return {
        "completada": a["completada"] or b["completada"],
        "puntaje": max(puntajes) if puntajes else None,
        "intentos": max(a["intentos"], b["intentos"]),
        "completada_en": minimo_no_nulo(a["completada_en"], b["completada_en"]),
        "datos": unir_datos(a["datos"], b["datos"]),
    }


def guardar(
    db: Session,
    alumno_id: str,
    por_leccion: Dict[Tuple[str, str], dict],
    insignias: List[str],
    momento: datetime,
) -> None:
    """Upsert monotónico de las lecciones y de los logros (sin commit)."""
    asegurar_usuario(db, alumno_id)
    registrar_acceso(db.get(Usuario, alumno_id), momento)

    existentes = {}
    if por_leccion:
        # Una sola consulta para todo el lote (en vez de una por lección).
        lecciones = sorted({leccion_id for _, leccion_id in por_leccion})
        filas = db.scalars(
            select(ProgresoLeccion).where(
                ProgresoLeccion.usuario_id == alumno_id, ProgresoLeccion.leccion_id.in_(lecciones)
            )
        ).all()
        existentes = {(f.curso_id, f.leccion_id): f for f in filas}

    for (curso_id, leccion_id), avance in por_leccion.items():
        fila = existentes.get((curso_id, leccion_id))
        if fila is None:
            fila = ProgresoLeccion(
                usuario_id=alumno_id, curso_id=curso_id, leccion_id=leccion_id, completada=0, intentos=0
            )
            db.add(fila)
        # El progreso nunca retrocede: completada OR, mejor puntaje, más intentos.
        fila.completada = 1 if (fila.completada or avance["completada"]) else 0
        if avance["puntaje"] is not None:
            fila.puntaje = max(fila.puntaje or 0, avance["puntaje"])
        fila.intentos = max(fila.intentos or 0, avance["intentos"])
        # completada_en: la primera vez (la fecha más antigua conocida). Un
        # evento sin completar no fecha filas viejas que no la tenían.
        if avance["completada_en"] is not None:
            fila.completada_en = minimo_no_nulo(fila.completada_en, avance["completada_en"])
        datos = unir_datos(leer_objeto(fila.datos_ligeros), avance["datos"])
        if datos:
            fila.datos_ligeros = compactar(datos)
        fila.actualizado_en = momento

    if insignias:
        ya_tiene = set(
            db.scalars(
                select(Logro.insignia_id).where(Logro.usuario_id == alumno_id, Logro.insignia_id.in_(insignias))
            ).all()
        )
        for insignia in insignias:
            if insignia not in ya_tiene:
                db.add(Logro(usuario_id=alumno_id, insignia_id=insignia, obtenido_en=momento))


@router.post("/progreso")
def guardar_progreso(
    datos: SolicitudProgreso,
    actual: Optional[Usuario] = Depends(usuario_opcional),
    db: Session = Depends(obtener_db),
):
    """Guarda el progreso enviado desde el dispositivo del alumno.

    El progreso nunca retrocede: una lección completada sigue completada y
    se conserva el mejor puntaje, aunque lleguen eventos viejos o repetidos.
    """
    momento = ahora()
    por_leccion: Dict[Tuple[str, str], dict] = {}
    for evento in datos.eventos:
        clave = (evento.curso_id, evento.leccion_id)
        avance = avance_de(evento, momento)
        por_leccion[clave] = combinar(por_leccion[clave], avance) if clave in por_leccion else avance
    insignias = list(dict.fromkeys(i.strip() for i in datos.insignias or [] if i.strip()))

    # Dos intentos: si otra petición del mismo alumno insertó la misma fila al
    # mismo tiempo (IntegrityError), la segunda vuelta ya la encuentra.
    for intento in range(2):
        try:
            alumno_id = resolver_alumno(db, actual, datos.usuario_id)
            guardar(db, alumno_id, por_leccion, insignias, momento)
            db.commit()
            break
        except IntegrityError as error:
            db.rollback()
            if intento:
                raise error_bd(error, "/api/progreso")
        except SQLAlchemyError as error:
            db.rollback()
            raise error_bd(error, "/api/progreso")

    return {"guardados": len(por_leccion), "insignias": len(insignias)}


# --- Lectura -------------------------------------------------------------------


@router.get("/progreso")
def leer_mi_progreso(usuario: Usuario = Depends(usuario_requerido), db: Session = Depends(obtener_db)):
    """Progreso de la cuenta de la sesión (la PWA lo combina con el local)."""
    try:
        return progreso_de(db, usuario.id)
    except SQLAlchemyError as error:
        raise error_bd(error, "/api/progreso")


@router.get("/progreso/{usuario_id}")
def leer_progreso(
    usuario_id: str,
    actual: Optional[Usuario] = Depends(usuario_opcional),
    db: Session = Depends(obtener_db),
):
    try:
        if not puede_ver_alumno(db, actual, usuario_id):
            if actual is None:
                raise HTTPException(status_code=401, detail="Inicia sesión para ver este progreso.")
            raise HTTPException(status_code=403, detail="No tienes permiso para ver el progreso de este alumno.")
        return progreso_de(db, usuario_id)
    except SQLAlchemyError as error:
        raise error_bd(error, "/api/progreso")
