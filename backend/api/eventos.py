"""Eventos de aprendizaje para medir alumnos activos (plan de lanzamiento, sec. 9 y P10).

La PWA guarda los eventos en una cola y los reenvía hasta que el servidor
confirma. El id lo genera el dispositivo, así un evento reenviado no se
cuenta dos veces. La fecha es la del dispositivo (la actividad offline
conserva su día); si viene del futuro se usa la hora del servidor.
"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import asegurar_usuario, error_bd
from api.dependencias import resolver_alumno, usuario_opcional
from api.limites import limitar
from api.progreso import a_utc, registrar_acceso
from database.conexion import obtener_db
from database.modelos import TIPOS_EVENTO, EventoAprendizaje, Usuario, ahora

# Escrituras sin sesión (alumnos anónimos): frena a quien llene la base a
# propósito. 300 por minuto por cuenta o IP alcanza para un aula que sincroniza.
LIMITE_ESCRITURA = limitar(300, por="cuenta")

router = APIRouter(prefix="/api", tags=["eventos"])

# Relojes de dispositivos adelantados: hasta 5 minutos se respeta la fecha.
TOLERANCIA_FUTURO = timedelta(minutes=5)
# Tamaño de EVENTOS_APRENDIZAJE.DATOS (VARCHAR2(250 CHAR)).
MAX_DATOS = 250


class EventoEntrada(BaseModel):
    id: str = Field(min_length=1, max_length=36)
    tipo: str
    curso_id: Optional[str] = Field(default=None, max_length=50)
    leccion_id: Optional[str] = Field(default=None, max_length=50)
    sesion_aprendizaje: Optional[str] = Field(default=None, max_length=36)
    ocurrido_en: Optional[datetime] = None
    version_app: Optional[str] = Field(default=None, max_length=20)
    # Objeto JSON o texto; si serializado pasa de 250 caracteres se ignora.
    datos: Optional[Any] = None

    @field_validator("tipo")
    @classmethod
    def tipo_conocido(cls, valor: str) -> str:
        if valor not in TIPOS_EVENTO:
            raise ValueError(f"Tipo de evento desconocido. Usa uno de: {', '.join(TIPOS_EVENTO)}.")
        return valor


class SolicitudEventos(BaseModel):
    usuario_id: Optional[str] = Field(default=None, max_length=100)
    eventos: List[EventoEntrada] = Field(min_length=1, max_length=200)


def serializar_datos(valor: Any) -> Optional[str]:
    """Texto compacto para la columna datos; None si está vacío o no cabe."""
    if valor is None or valor == "" or valor == {}:
        return None
    if isinstance(valor, str):
        texto = valor
    elif isinstance(valor, dict):
        try:
            texto = json.dumps(valor, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            return None
    else:
        return None
    return texto if len(texto) <= MAX_DATOS else None


def fecha_evento(fecha: Optional[datetime], momento: datetime) -> datetime:
    fecha = a_utc(fecha) or momento
    return momento if fecha > momento + TOLERANCIA_FUTURO else fecha


def guardar(db: Session, alumno_id: str, unicos: Dict[str, EventoEntrada], momento: datetime) -> int:
    """Inserta los eventos que aún no existen y devuelve cuántos (sin commit)."""
    asegurar_usuario(db, alumno_id)
    usuario = db.get(Usuario, alumno_id)
    registrar_acceso(usuario, momento)
    es_prueba = 1 if usuario.es_prueba else 0

    # Una sola consulta para saber cuáles ya llegaron antes (reenvíos).
    ya_existen = set(
        db.scalars(select(EventoAprendizaje.id).where(EventoAprendizaje.id.in_(list(unicos)))).all()
    )
    nuevos = 0
    for evento_id, evento in unicos.items():
        if evento_id in ya_existen:
            continue
        db.add(
            EventoAprendizaje(
                id=evento_id,
                usuario_id=alumno_id,
                tipo=evento.tipo,
                curso_id=evento.curso_id,
                leccion_id=evento.leccion_id,
                sesion_aprendizaje=evento.sesion_aprendizaje,
                ocurrido_en=fecha_evento(evento.ocurrido_en, momento),
                recibido_en=momento,
                version_app=evento.version_app,
                es_prueba=es_prueba,
                datos=serializar_datos(evento.datos),
            )
        )
        nuevos += 1
    return nuevos


@router.post("/eventos", dependencies=[Depends(LIMITE_ESCRITURA)])
def guardar_eventos(
    datos: SolicitudEventos,
    actual: Optional[Usuario] = Depends(usuario_opcional),
    db: Session = Depends(obtener_db),
):
    # Duplicados dentro del mismo lote: cuenta el primero.
    unicos: Dict[str, EventoEntrada] = {}
    for evento in datos.eventos:
        unicos.setdefault(evento.id.strip(), evento)
    unicos.pop("", None)

    momento = ahora()
    nuevos = 0
    # Dos intentos: si el mismo lote llega dos veces a la vez, la segunda
    # vuelta encuentra los eventos que insertó la otra petición.
    for intento in range(2):
        try:
            alumno_id = resolver_alumno(db, actual, datos.usuario_id)
            nuevos = guardar(db, alumno_id, unicos, momento) if unicos else 0
            db.commit()
            break
        except IntegrityError as error:
            db.rollback()
            if intento:
                raise error_bd(error, "/api/eventos")
        except SQLAlchemyError as error:
            db.rollback()
            raise error_bd(error, "/api/eventos")

    return {"recibidos": len(datos.eventos), "nuevos": nuevos}
