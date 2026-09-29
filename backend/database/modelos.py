"""Tablas de Amatista.

Deben coincidir con backend/sql/001_esquema_amatista.sql, que es el script
que crea las tablas en Oracle. Todos los identificadores de usuario son
texto (VARCHAR2): así caben correos y UUID sin provocar ORA-01722.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import TIMESTAMP, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def ahora() -> datetime:
    """Hora actual en UTC, sin zona (como la guarda TIMESTAMP)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(150))
    creado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class Sesion(Base):
    __tablename__ = "sesiones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"))
    dispositivo: Mapped[Optional[str]] = mapped_column(String(200))
    activa: Mapped[int] = mapped_column(Integer, default=1)
    ultimo_acceso: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class ProgresoLeccion(Base):
    __tablename__ = "progreso_lecciones"

    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"), primary_key=True)
    curso_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    leccion_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    completada: Mapped[int] = mapped_column(Integer, default=0)
    puntaje: Mapped[Optional[int]] = mapped_column(Integer)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
