"""Tablas de Amatista.

Deben coincidir con los scripts de backend/sql/ (001 crea la base y 002 la
amplía sin borrar datos). Todos los identificadores son texto (VARCHAR2):
así caben correos y UUID sin provocar ORA-01722, y las llaves foráneas
tienen el mismo tipo en ambos lados (evita ORA-02267).

Presupuesto de espacio (ver el encabezado de sql/002 y sql/LEEME.txt):
una fila por alumno y lección, eventos con retención limitada y contenido
de lecciones como texto JSON (nunca binarios).
"""
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import TIMESTAMP, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("alumno", "profesor", "admin")
ESTADOS_CONTENIDO = ("borrador", "publicado", "archivado")
TIPOS_EVENTO = (
    "account_created",
    "learning_session_started",
    "lesson_completed",
    "activity_submitted",
    "sync_succeeded",
)


def ahora() -> datetime:
    """Hora actual en UTC, sin zona (como la guarda TIMESTAMP)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TextoJSON(TypeDecorator):
    """Texto con JSON que siempre se lee como texto.

    python-oracledb entrega ya convertidas (dict o list) las columnas con
    CHECK (... IS JSON); SQLite entrega el texto. Así el resto del código ve
    siempre lo mismo: el texto JSON compacto.
    """

    impl = Text
    cache_ok = True

    def process_result_value(self, valor: Any, dialect) -> Optional[str]:
        if valor is None or isinstance(valor, str):
            return valor
        return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))


class TextoJSONCorto(TextoJSON):
    impl = String
    cache_ok = True


class Base(DeclarativeBase):
    pass


# --- Identidad --------------------------------------------------------------


class Usuario(Base):
    """Alumno, profesor o administrador.

    Una fila sin password_hash es un alumno anónimo: el identificador local
    que la PWA genera en el dispositivo ("alumno-<uuid>"). Al registrarse,
    esa misma fila recibe correo y contraseña y conserva su progreso. Si el
    alumno inicia sesión desde otro dispositivo, el progreso anónimo de ese
    dispositivo se fusiona con su cuenta y la fila anónima queda marcada en
    fusionado_en (ya no acepta escrituras sin sesión).
    """

    __tablename__ = "usuarios"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(150))
    # Siempre en minúsculas. UNIQUE admite varios NULL (alumnos anónimos).
    email: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(25))
    rol: Mapped[str] = mapped_column(String(20), default="alumno")
    # "pbkdf2_sha256$<iteraciones>$<sal>$<hash>"; NULL = cuenta anónima.
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    correo_confirmado: Mapped[int] = mapped_column(Integer, default=0)
    # Hash SHA-256 del código de 6 dígitos (nunca el código en claro).
    codigo_verificacion: Mapped[Optional[str]] = mapped_column(String(64))
    codigo_proposito: Mapped[Optional[str]] = mapped_column(String(12))  # 'correo' | 'password'
    codigo_expira: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    codigo_intentos: Mapped[int] = mapped_column(Integer, default=0)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_hasta: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    # 1 = cuenta de prueba o del equipo: no cuenta en las métricas de alumnos.
    es_prueba: Mapped[int] = mapped_column(Integer, default=0)
    # Cuenta con la que se fusionó este alumno anónimo.
    fusionado_en: Mapped[Optional[str]] = mapped_column(String(100))
    creado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
    ultimo_acceso: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)


class Sesion(Base):
    """Sesión iniciada con correo y contraseña.

    id guarda el hash SHA-256 (64 caracteres hex) del token que tiene el
    navegador: si alguien lee la tabla, no puede usar las sesiones.
    """

    __tablename__ = "sesiones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"), index=True)
    dispositivo: Mapped[Optional[str]] = mapped_column(String(200))
    activa: Mapped[int] = mapped_column(Integer, default=1)
    creado_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, default=ahora)
    ultimo_acceso: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
    expira_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)


# --- Progreso ---------------------------------------------------------------


class ProgresoLeccion(Base):
    """Estado consolidado: una sola fila por alumno y lección (upsert)."""

    __tablename__ = "progreso_lecciones"

    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"), primary_key=True)
    curso_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    leccion_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    completada: Mapped[int] = mapped_column(Integer, default=0)  # nunca vuelve de 1 a 0
    puntaje: Mapped[Optional[int]] = mapped_column(Integer)  # se conserva el máximo
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    # JSON plano compacto con llaves cortas, por ejemplo {"a":3,"p":2,"t":240}.
    datos_ligeros: Mapped[Optional[str]] = mapped_column(TextoJSONCorto(250))
    completada_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class Logro(Base):
    """Insignias ganadas. Nunca se retiran, aunque el módulo crezca después."""

    __tablename__ = "logros"

    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"), primary_key=True)
    # "<curso_id>:<modulo_id>", por ejemplo "blender:mod_teoria_001".
    insignia_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    obtenido_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class EventoAprendizaje(Base):
    """Eventos mínimos para medir alumnos activos (plan de lanzamiento, sec. 11).

    El id lo genera el dispositivo: un evento reenviado no se cuenta dos veces.
    En Oracle la tabla se particiona por mes para purgar meses completos.
    """

    __tablename__ = "eventos_aprendizaje"
    __table_args__ = (
        Index("ix_eventos_usuario_fecha", "usuario_id", "ocurrido_en"),
        Index("ix_eventos_fecha", "ocurrido_en"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"))
    tipo: Mapped[str] = mapped_column(String(30))
    curso_id: Mapped[Optional[str]] = mapped_column(String(50))
    leccion_id: Mapped[Optional[str]] = mapped_column(String(50))
    sesion_aprendizaje: Mapped[Optional[str]] = mapped_column(String(36))
    ocurrido_en: Mapped[datetime] = mapped_column(TIMESTAMP)
    recibido_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
    version_app: Mapped[Optional[str]] = mapped_column(String(20))
    es_prueba: Mapped[int] = mapped_column(Integer, default=0)
    datos: Mapped[Optional[str]] = mapped_column(String(250))


# --- Contenido (lo administra el panel de administración) -------------------


class Curso(Base):
    __tablename__ = "cursos"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # 'blender', 'aframe'
    numero: Mapped[Optional[str]] = mapped_column(String(5))  # '01'
    titulo: Mapped[str] = mapped_column(String(100))
    subtitulo: Mapped[Optional[str]] = mapped_column(String(150))
    descripcion: Mapped[Optional[str]] = mapped_column(String(1000))
    nivel: Mapped[Optional[str]] = mapped_column(String(30))
    acento: Mapped[Optional[str]] = mapped_column(String(20))
    recurso_texto: Mapped[Optional[str]] = mapped_column(String(100))
    recurso_url: Mapped[Optional[str]] = mapped_column(String(300))
    orden: Mapped[int] = mapped_column(Integer, default=0)
    estado: Mapped[str] = mapped_column(String(12), default="publicado")
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class Modulo(Base):
    __tablename__ = "modulos"
    __table_args__ = (Index("ix_modulos_curso", "curso_id", "numero"),)

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # 'mod_teoria_001'
    curso_id: Mapped[str] = mapped_column(String(50), ForeignKey("cursos.id"))
    numero: Mapped[int] = mapped_column(Integer)  # posición dentro del curso (1, 2, ...)
    titulo: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[Optional[str]] = mapped_column(String(1000))
    insignia: Mapped[Optional[str]] = mapped_column(String(80))
    minutos: Mapped[Optional[int]] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(12), default="borrador")
    version: Mapped[int] = mapped_column(Integer, default=1)
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
    publicado_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)


class Leccion(Base):
    """Una lección. contenido guarda el JSON completo de la lección, con el
    mismo formato que frontend/src/data/modulos/*.json (contentBlocks,
    quizData, cover...). El id nunca cambia una vez publicada: el progreso
    de los alumnos se guarda con él. Para sustituir una lección se crea otra
    con "replaces": ["id_viejo"] y el progreso se acopla solo.
    """

    __tablename__ = "lecciones"
    __table_args__ = (
        ForeignKeyConstraint(["curso_id"], ["cursos.id"]),
        Index("ix_lecciones_modulo", "modulo_id", "orden"),
    )

    curso_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    modulo_id: Mapped[str] = mapped_column(String(50), ForeignKey("modulos.id"))
    orden: Mapped[int] = mapped_column(Integer)
    titulo: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(30))
    duracion_segundos: Mapped[Optional[int]] = mapped_column(Integer)
    bloqueada: Mapped[int] = mapped_column(Integer, default=1)
    contenido: Mapped[str] = mapped_column(TextoJSON())  # CLOB con JSON (IS JSON en Oracle)
    reemplaza: Mapped[Optional[str]] = mapped_column(String(250))  # ids viejos separados por comas
    estado: Mapped[str] = mapped_column(String(12), default="borrador")
    version: Mapped[int] = mapped_column(Integer, default=1)
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
    publicado_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
