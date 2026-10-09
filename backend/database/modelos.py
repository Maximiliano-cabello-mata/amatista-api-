"""Tablas de Amatista.

Deben coincidir con los scripts de backend/sql/ (001 crea la base, 002 la
amplía sin borrar datos, 005 agrega la reestructuración por niveles y 007 el
motor de prácticas de Blender). Todos los identificadores son texto (VARCHAR2):
así caben correos y UUID sin provocar ORA-01722, y las llaves foráneas
tienen el mismo tipo en ambos lados (evita ORA-02267).

Presupuesto de espacio (ver el encabezado de sql/002 y sql/LEEME.txt):
una fila por alumno y lección, eventos con retención limitada y contenido
de lecciones como texto JSON (nunca binarios).
"""
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import TIMESTAMP, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
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
# Reestructuración v3 (docs/reestructuracion/): curso > nivel > módulo > lección.
NUMEROS_NIVEL = (1, 2, 3, 4, 5)
# Sección 9 de la propuesta de contenido: cuatro estados por habilidad.
ESTADOS_HABILIDAD = ("sin_practicar", "con_guia", "con_pistas", "autonoma")
# Rúbrica común de proyecto: criterios A a E, cada uno con tres logros.
CRITERIOS_RUBRICA = ("A", "B", "C", "D", "E")
LOGROS_RUBRICA = ("pendiente", "con_ayuda", "autonomo")
# Política de versiones de Blender (sección 5).
CATEGORIAS_BLENDER = ("principal", "compatible", "sin_verificar", "retirada")
RESULTADOS_VERIFICACION = ("verificada", "con_diferencias", "falla")
# Motor de prácticas (sql/007, docs/motor/): vínculo del add-on con la cuenta.
ESTADOS_VINCULO = ("pendiente", "listo", "canjeado")
# Quién subió la versión vigente de una práctica.
ORIGENES_PRACTICA = ("repositorio", "addon", "panel")


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
    # Motor v3 (sql/008): familia del curso ('blender', 'aframe') y curso que
    # conviene terminar antes. Los cuatro cursos de Blender comparten ruta.
    ruta: Mapped[Optional[str]] = mapped_column(String(30))
    requisito_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("cursos.id"))


class Nivel(Base):
    """Nivel del curso (1 Desde cero … 5 Avanzado por especialidad).

    El nivel 5 tiene una fila por rama ("web", "animacion"...): cada rama
    tiene sus prerrequisitos y su proyecto final. Un nivel en borrador no
    aparece en el catálogo; sus módulos sí, si están publicados (los módulos
    sin nivel siguen funcionando igual que antes de la reestructuración).
    """

    __tablename__ = "niveles"
    # La restricción UNIQUE crea su índice y cubre las búsquedas por curso (regla 2).
    __table_args__ = (UniqueConstraint("curso_id", "numero", "rama", name="uq_niveles_curso_numero_rama"),)

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # 'blender-n1', 'blender-n5-web'
    curso_id: Mapped[str] = mapped_column(String(50), ForeignKey("cursos.id"))
    numero: Mapped[int] = mapped_column(Integer)  # 1 a 5
    rama: Mapped[Optional[str]] = mapped_column(String(50))  # solo en el nivel 5
    titulo: Mapped[str] = mapped_column(String(100))
    perfil: Mapped[Optional[str]] = mapped_column(String(500))
    proyecto: Mapped[Optional[str]] = mapped_column(String(300))
    criterio_salida: Mapped[Optional[str]] = mapped_column(String(500))
    estado: Mapped[str] = mapped_column(String(12), default="borrador")
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class Modulo(Base):
    __tablename__ = "modulos"
    __table_args__ = (Index("ix_modulos_curso", "curso_id", "numero"),)

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # 'mod_teoria_001'
    curso_id: Mapped[str] = mapped_column(String(50), ForeignKey("cursos.id"))
    # NULL = módulo anterior a los niveles (sigue visible en el catálogo).
    nivel_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("niveles.id"))
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


# --- Reestructuración v3: habilidades, rúbrica y versiones de Blender --------


class Habilidad(Base):
    """Habilidad observable ("navegar sin mover objetos"). Las lecciones la
    citan en su ficha (ficha.habilidades) y el alumno la demuestra."""

    __tablename__ = "habilidades"
    __table_args__ = (Index("ix_habilidades_curso", "curso_id", "orden"),)

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # 'bl-navegar-vista'
    curso_id: Mapped[str] = mapped_column(String(50), ForeignKey("cursos.id"))
    nivel_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("niveles.id"))
    nombre: Mapped[str] = mapped_column(String(150))
    descripcion: Mapped[Optional[str]] = mapped_column(String(500))
    orden: Mapped[int] = mapped_column(Integer, default=0)
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class HabilidadAlumno(Base):
    """Estado de una habilidad para un alumno: una fila por par (upsert)."""

    __tablename__ = "habilidades_alumno"

    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"), primary_key=True)
    habilidad_id: Mapped[str] = mapped_column(String(50), ForeignKey("habilidades.id"), primary_key=True)
    estado: Mapped[str] = mapped_column(String(14), default="sin_practicar")
    evidencia: Mapped[Optional[str]] = mapped_column(String(300))  # enlace o nota, nunca un binario
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class EvaluacionRubrica(Base):
    """Rúbrica común del proyecto de un nivel: criterio A a E por alumno.

    Se guarda la evaluación más reciente de cada criterio; el historial queda
    en EVENTOS_APRENDIZAJE (activity_submitted).
    """

    __tablename__ = "evaluaciones_rubrica"

    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"), primary_key=True)
    nivel_id: Mapped[str] = mapped_column(String(50), ForeignKey("niveles.id"), primary_key=True)
    criterio: Mapped[str] = mapped_column(String(1), primary_key=True)
    logro: Mapped[str] = mapped_column(String(12), default="pendiente")
    evidencia: Mapped[Optional[str]] = mapped_column(String(300))
    comentario: Mapped[Optional[str]] = mapped_column(String(500))
    revisado_por: Mapped[Optional[str]] = mapped_column(String(100))
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class VersionBlender(Base):
    """Versión de Blender y su categoría en el curso (principal, compatible...).

    La versión principal (LTS) es una decisión pendiente: la tabla nace vacía
    y se llena cuando se verifica (docs/reestructuracion/).
    """

    __tablename__ = "versiones_blender"

    version: Mapped[str] = mapped_column(String(20), primary_key=True)  # '4.2.3'
    categoria: Mapped[str] = mapped_column(String(14), default="sin_verificar")
    es_lts: Mapped[int] = mapped_column(Integer, default=0)
    soporte_hasta: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    notas: Mapped[Optional[str]] = mapped_column(String(500))
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class VerificacionBlender(Base):
    """Una prueba de una lección en una versión de Blender (matriz de
    compatibilidad de la sección 5): quién, cuándo, dónde y qué cambió."""

    __tablename__ = "verificaciones_blender"
    __table_args__ = (
        ForeignKeyConstraint(["curso_id", "leccion_id"], ["lecciones.curso_id", "lecciones.id"]),
        Index("ix_verificaciones_leccion", "curso_id", "leccion_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    curso_id: Mapped[str] = mapped_column(String(50))
    leccion_id: Mapped[str] = mapped_column(String(50))
    version_blender: Mapped[str] = mapped_column(String(20), ForeignKey("versiones_blender.version"))
    sistema: Mapped[str] = mapped_column(String(60))  # 'Windows 11', 'Ubuntu 24.04'
    version_leccion: Mapped[int] = mapped_column(Integer)  # LECCIONES.VERSION probada
    version_addon: Mapped[Optional[str]] = mapped_column(String(20))
    resultado: Mapped[str] = mapped_column(String(16))
    diferencias: Mapped[Optional[str]] = mapped_column(String(1000))
    evidencia: Mapped[Optional[str]] = mapped_column(String(300))
    responsable: Mapped[Optional[str]] = mapped_column(String(100))
    verificado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


# --- Motor de prácticas de Blender (sql/007, docs/motor/) ---------------------


class AddonVinculo(Base):
    """Vínculo de un Blender con una cuenta (código de dispositivo).

    El add-on pide un vínculo, el alumno escribe el código en la plataforma
    (estado «listo») y el add-on lo canjea una sola vez por una sesión propia
    en SESIONES. Solo se guarda el hash del secreto. Las filas vencidas se
    borran solas al crear vínculos nuevos: la tabla queda siempre pequeña.
    """

    __tablename__ = "addon_vinculos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    codigo: Mapped[str] = mapped_column(String(9), unique=True)  # 'ABCD-2345'
    secreto_hash: Mapped[str] = mapped_column(String(64))
    usuario_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("usuarios.id"))
    dispositivo: Mapped[Optional[str]] = mapped_column(String(200))
    estado: Mapped[str] = mapped_column(String(10), default="pendiente")
    creado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
    expira_en: Mapped[datetime] = mapped_column(TIMESTAMP)


class Practica(Base):
    """Práctica del motor (practice.json, formato amatista.practice/1).

    definicion guarda la última versión subida; version_publicada, la que
    reciben los alumnos (su texto está en PRACTICA_VERSIONES). Subir una
    versión nueva nunca la publica: publicar es un paso del administrador.
    """

    __tablename__ = "practicas"
    __table_args__ = (Index("ix_practicas_leccion", "curso_id", "leccion_id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # 'blender.n1.mesa'
    curso_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("cursos.id"))
    leccion_id: Mapped[Optional[str]] = mapped_column(String(50))  # lección donde vive
    titulo: Mapped[str] = mapped_column(String(200))
    nivel: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    version_publicada: Mapped[Optional[int]] = mapped_column(Integer)
    definicion: Mapped[str] = mapped_column(TextoJSON())  # CLOB con JSON (IS JSON en Oracle)
    estado: Mapped[str] = mapped_column(String(12), default="borrador")
    origen: Mapped[str] = mapped_column(String(12), default="addon")
    autor_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("usuarios.id"))
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
    publicado_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)


class PracticaVersion(Base):
    """Historial: cada versión subida de una práctica, con quién y desde dónde."""

    __tablename__ = "practica_versiones"

    practica_id: Mapped[str] = mapped_column(String(80), ForeignKey("practicas.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    definicion: Mapped[str] = mapped_column(TextoJSON())
    huella: Mapped[str] = mapped_column(String(64))  # SHA-256 del JSON canónico
    nota: Mapped[Optional[str]] = mapped_column(String(500))
    autor_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("usuarios.id"))
    version_addon: Mapped[Optional[str]] = mapped_column(String(20))
    version_blender: Mapped[Optional[str]] = mapped_column(String(20))
    creado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


class ProgresoPractica(Base):
    """Avance de un alumno en una práctica: una fila por par (upsert).

    El servidor vuelve a evaluar la foto de la escena con el motor; progreso
    guarda el mejor resultado y completada nunca vuelve de 1 a 0. El detalle
    de cada envío queda en EVENTOS_APRENDIZAJE (activity_submitted).
    """

    __tablename__ = "progreso_practicas"
    __table_args__ = (Index("ix_prog_practicas_practica", "practica_id"),)

    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"), primary_key=True)
    practica_id: Mapped[str] = mapped_column(String(80), ForeignKey("practicas.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    progreso: Mapped[int] = mapped_column(Integer, default=0)  # 0 a 100, el mejor
    completada: Mapped[int] = mapped_column(Integer, default=0)
    autonomia: Mapped[Optional[str]] = mapped_column(String(14))  # con_guia | con_pistas | autonoma
    pistas: Mapped[int] = mapped_column(Integer, default=0)
    correcciones: Mapped[int] = mapped_column(Integer, default=0)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    paso_actual: Mapped[Optional[str]] = mapped_column(String(80))
    # Ids de los objetivos cumplidos en la última evaluación: ["cubierta","patas"].
    objetivos: Mapped[Optional[str]] = mapped_column(TextoJSONCorto(1000))
    version_blender: Mapped[Optional[str]] = mapped_column(String(20))
    version_addon: Mapped[Optional[str]] = mapped_column(String(20))
    abierta_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    completada_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)


# --- Enlace en vivo plataforma ↔ Blender (sql/010, motor 3.4) -------------------


class AddonEnlace(Base):
    """Un Blender abierto y conectado: su último latido y la orden pendiente.

    Una fila por sesión del add-on (SESIONES.DISPOSITIVO «blender-addon …»).
    El add-on manda un latido cada pocos segundos con la práctica abierta, el
    paso y si está en modo enfocado; la plataforma lo muestra en vivo y deja
    aquí una orden («abrir esta práctica», «enfoca Blender») que el add-on
    recoge en su siguiente latido. visto_en solo se reescribe cada 15 s o al
    cambiar algo: la tabla no crece y Oracle casi no escribe.
    """

    __tablename__ = "addon_enlaces"
    __table_args__ = (Index("ix_addon_enlaces_usuario", "usuario_id"),)

    # ON DELETE CASCADE: la purga de sesiones (sql/003) se lleva su enlace.
    sesion_id: Mapped[str] = mapped_column(String(64), ForeignKey("sesiones.id", ondelete="CASCADE"), primary_key=True)
    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"))
    visto_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
    practica_id: Mapped[Optional[str]] = mapped_column(String(80))
    paso: Mapped[Optional[str]] = mapped_column(String(80))
    progreso: Mapped[Optional[int]] = mapped_column(Integer)
    enfocado: Mapped[int] = mapped_column(Integer, default=0)
    version_addon: Mapped[Optional[str]] = mapped_column(String(20))
    version_blender: Mapped[Optional[str]] = mapped_column(String(20))
    # Orden pendiente para este Blender: {"id", "tipo", "datos"}; se borra al entregarla.
    orden: Mapped[Optional[str]] = mapped_column(TextoJSONCorto(1000))
    orden_en: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    # sql/011: compartido entre procesos; se sobrescribe, no acumula historial.
    detalle: Mapped[Optional[str]] = mapped_column(Text)


class AddonAjustes(Base):
    """Cómo se ve Blender para un alumno, decidido desde la plataforma (Mi Blender).

    datos: {"enfoque": "auto|siempre|nunca", "acompanamiento": "acompanado|tarjeta|silencioso",
    "avisos_herramientas": bool, "tarjeta_3d": bool}. El add-on los aplica en cada latido;
    sus preferencias locales quedan como respaldo sin conexión.
    """

    __tablename__ = "addon_ajustes"

    usuario_id: Mapped[str] = mapped_column(String(100), ForeignKey("usuarios.id"), primary_key=True)
    datos: Mapped[str] = mapped_column(TextoJSONCorto(1000))
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMP, default=ahora)
