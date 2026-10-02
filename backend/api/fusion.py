"""Fusión del progreso de un alumno anónimo con una cuenta registrada.

Caso: el alumno estudió sin cuenta en un dispositivo (id local
"alumno-<uuid>") y después inicia sesión con su cuenta. Lo que hizo en ese
dispositivo se une a la cuenta sin perder nada (reglas monotónicas, igual
que /api/progreso):

- completada: OR (una lección completada nunca vuelve a pendiente).
- puntaje e intentos: el máximo.
- completada_en: la fecha más antigua que no sea nula.
- datos_ligeros: se conserva el de la cuenta salvo que esté vacío.
- logros: se copian (los que la cuenta ya tenía conservan su fecha).
- eventos de aprendizaje: se reasignan a la cuenta (las métricas siguen
  contando a la misma persona).

Las filas del anónimo se mueven (no se duplican): una sola fila por alumno y
lección cuida el presupuesto de 20 GB y evita contar dos veces en las
métricas. La fila del anónimo queda marcada con fusionado_en y desde ese
momento resolver_alumno rechaza escrituras sin sesión para ese id (401).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from database.modelos import EventoAprendizaje, Logro, ProgresoLeccion, Sesion, Usuario


def es_anonimo_fusionable(usuario: Optional[Usuario]) -> bool:
    """Alumno anónimo (sin contraseña ni correo) que no se ha fusionado."""
    return (
        usuario is not None
        and not usuario.password_hash
        and not usuario.email
        and not usuario.fusionado_en
    )


def _minimo_no_nulo(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    fechas = [f for f in (a, b) if f is not None]
    return min(fechas) if fechas else None


def _maximo_no_nulo(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    fechas = [f for f in (a, b) if f is not None]
    return max(fechas) if fechas else None


def fusionar_alumno(db: Session, origen_id: str, destino_id: str) -> int:
    """Une el progreso del anónimo `origen_id` con la cuenta `destino_id`.

    Devuelve cuántas lecciones del anónimo se fusionaron. Si el origen no
    existe, es una cuenta registrada o ya se fusionó, no hace nada (0).
    No hace commit: quien llama confirma la transacción completa.
    """
    origen_id = (origen_id or "").strip()
    if not origen_id or origen_id == destino_id:
        return 0
    origen = db.get(Usuario, origen_id)
    destino = db.get(Usuario, destino_id)
    if destino is None or not es_anonimo_fusionable(origen):
        return 0

    filas_origen = db.scalars(select(ProgresoLeccion).where(ProgresoLeccion.usuario_id == origen_id)).all()
    filas_destino = {
        (f.curso_id, f.leccion_id): f
        for f in db.scalars(select(ProgresoLeccion).where(ProgresoLeccion.usuario_id == destino_id)).all()
    }

    for fila in filas_origen:
        propia = filas_destino.get((fila.curso_id, fila.leccion_id))
        if propia is None:
            db.add(
                ProgresoLeccion(
                    usuario_id=destino_id,
                    curso_id=fila.curso_id,
                    leccion_id=fila.leccion_id,
                    completada=1 if fila.completada else 0,
                    puntaje=fila.puntaje,
                    intentos=fila.intentos or 0,
                    datos_ligeros=fila.datos_ligeros,
                    completada_en=fila.completada_en,
                    actualizado_en=fila.actualizado_en,
                )
            )
            continue
        propia.completada = 1 if (propia.completada or fila.completada) else 0
        puntajes = [p for p in (propia.puntaje, fila.puntaje) if p is not None]
        propia.puntaje = max(puntajes) if puntajes else None
        propia.intentos = max(propia.intentos or 0, fila.intentos or 0)
        propia.completada_en = _minimo_no_nulo(propia.completada_en, fila.completada_en)
        if not propia.datos_ligeros:
            propia.datos_ligeros = fila.datos_ligeros
        propia.actualizado_en = _maximo_no_nulo(propia.actualizado_en, fila.actualizado_en)

    logros_destino = set(db.scalars(select(Logro.insignia_id).where(Logro.usuario_id == destino_id)).all())
    for logro in db.scalars(select(Logro).where(Logro.usuario_id == origen_id)).all():
        if logro.insignia_id not in logros_destino:
            db.add(Logro(usuario_id=destino_id, insignia_id=logro.insignia_id, obtenido_en=logro.obtenido_en))

    # Primero se insertan las filas de la cuenta y después se borran las del
    # anónimo: dentro de la misma transacción, si algo falla no se pierde nada.
    db.flush()
    db.execute(
        delete(ProgresoLeccion).where(ProgresoLeccion.usuario_id == origen_id),
        execution_options={"synchronize_session": False},
    )
    db.execute(
        delete(Logro).where(Logro.usuario_id == origen_id),
        execution_options={"synchronize_session": False},
    )
    db.execute(
        update(EventoAprendizaje)
        .where(EventoAprendizaje.usuario_id == origen_id)
        .values(usuario_id=destino_id, es_prueba=destino.es_prueba or 0),
        execution_options={"synchronize_session": False},
    )
    # Sesiones del endpoint heredado: el id anónimo ya no representa a nadie.
    db.execute(
        update(Sesion).where(Sesion.usuario_id == origen_id, Sesion.activa == 1).values(activa=0),
        execution_options={"synchronize_session": False},
    )
    origen.fusionado_en = destino_id
    db.flush()
    return len(filas_origen)
