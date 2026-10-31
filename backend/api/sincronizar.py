"""POST /api/sincronizar: progreso, insignias y eventos en una sola petición.

Pensado para escuelas con datos móviles limitados: antes la PWA hacía dos
peticiones (/api/progreso y /api/eventos), cada una con su conexión, sus
cabeceras y su espera. Ahora todo viaja junto, comprimido con gzip
(Content-Encoding: gzip, ver DescomprimirCuerpo en main.py) y se guarda en
una sola transacción: o llega todo o no llega nada, y la PWA reenvía el
lote completo sin duplicar (progreso monotónico, eventos con id propio).

Las PWA anteriores siguen usando /api/progreso y /api/eventos.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api import eventos as api_eventos
from api import progreso as api_progreso
from api.comun import error_bd
from api.dependencias import resolver_alumno, usuario_opcional
from api.limites import limitar
from database.conexion import obtener_db
from database.modelos import Usuario, ahora

# Igual que /api/progreso y /api/eventos: alcanza para un aula que sincroniza.
LIMITE_ESCRITURA = limitar(300, por="cuenta")

router = APIRouter(prefix="/api", tags=["progreso"])


class SolicitudSincronizar(BaseModel):
    usuario_id: Optional[str] = Field(default=None, max_length=100)
    progreso: List[api_progreso.EventoProgreso] = Field(default_factory=list, max_length=500)
    insignias: List[api_progreso.Insignia] = Field(default_factory=list, max_length=100)
    eventos: List[api_eventos.EventoEntrada] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def con_algo_que_guardar(self):
        if not (self.progreso or self.insignias or self.eventos):
            raise ValueError("Envía al menos un avance, una insignia o un evento.")
        return self


@router.post("/sincronizar", dependencies=[Depends(LIMITE_ESCRITURA)])
def sincronizar(
    datos: SolicitudSincronizar,
    actual: Optional[Usuario] = Depends(usuario_opcional),
    db: Session = Depends(obtener_db),
):
    momento = ahora()
    por_leccion, insignias = api_progreso.agrupar(datos.progreso, datos.insignias, momento)
    unicos = api_eventos.sin_repetir(datos.eventos)
    nuevos = 0
    # Dos intentos, como en /api/progreso: si el mismo lote llega dos veces a
    # la vez (reintento con poca señal), la segunda vuelta encuentra lo insertado.
    for intento in range(2):
        try:
            alumno_id = resolver_alumno(db, actual, datos.usuario_id)
            if por_leccion or insignias:
                api_progreso.guardar(db, alumno_id, por_leccion, insignias, momento)
            nuevos = api_eventos.guardar(db, alumno_id, unicos, momento) if unicos else 0
            db.commit()
            break
        except IntegrityError as error:
            db.rollback()
            if intento:
                raise error_bd(error, "/api/sincronizar")
        except SQLAlchemyError as error:
            db.rollback()
            raise error_bd(error, "/api/sincronizar")

    return {
        "guardados": len(por_leccion),
        "insignias": len(insignias),
        "eventos": len(datos.eventos),
        "nuevos": nuevos,
        "servidor_en": api_progreso.iso(momento),
    }
