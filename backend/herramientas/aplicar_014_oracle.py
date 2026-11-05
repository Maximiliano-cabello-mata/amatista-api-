"""Aplica de forma idempotente las tablas de sql/014_panel_del_profesor.sql en Oracle."""
import sys
from pathlib import Path

# Asegurar que backend/ está en sys.path
RAIZ_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_BACKEND))

from sqlalchemy import text
from database.conexion import crear_motor

DDL_STATEMENTS = [
    # 1. GRUPOS
    """
    CREATE TABLE grupos (
      id               VARCHAR2(40)  NOT NULL,
      profesor_id      VARCHAR2(100) NOT NULL,
      nombre           VARCHAR2(120) NOT NULL,
      codigo           VARCHAR2(12)  NOT NULL,
      archivado        NUMBER(1)     DEFAULT 0 NOT NULL,
      creado_en        TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_grupos PRIMARY KEY (id),
      CONSTRAINT fk_grupos_profesor FOREIGN KEY (profesor_id) REFERENCES usuarios (id),
      CONSTRAINT uq_grupos_codigo UNIQUE (codigo),
      CONSTRAINT ck_grupos_archivado CHECK (archivado IN (0, 1))
    )
    """,
    "CREATE INDEX ix_grupos_profesor ON grupos (profesor_id)",

    # 2. GRUPO_ALUMNOS
    """
    CREATE TABLE grupo_alumnos (
      grupo_id         VARCHAR2(40)  NOT NULL,
      alumno_id        VARCHAR2(100) NOT NULL,
      unido_en         TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_grupo_alumnos PRIMARY KEY (grupo_id, alumno_id),
      CONSTRAINT fk_grupo_alumnos_grupo FOREIGN KEY (grupo_id) REFERENCES grupos (id),
      CONSTRAINT fk_grupo_alumnos_alumno FOREIGN KEY (alumno_id) REFERENCES usuarios (id)
    )
    """,
    "CREATE INDEX ix_grupo_alumnos_alumno ON grupo_alumnos (alumno_id)",

    # 3. ASIGNACIONES
    """
    CREATE TABLE asignaciones (
      id               VARCHAR2(40)   NOT NULL,
      grupo_id         VARCHAR2(40)   NOT NULL,
      profesor_id      VARCHAR2(100)  NOT NULL,
      practica_id      VARCHAR2(80)   NOT NULL,
      titulo           VARCHAR2(200)  NOT NULL,
      indicaciones     VARCHAR2(1000),
      fecha_limite     TIMESTAMP,
      estado           VARCHAR2(12)   DEFAULT 'abierta' NOT NULL,
      creada_en        TIMESTAMP      DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_asignaciones PRIMARY KEY (id),
      CONSTRAINT fk_asignaciones_grupo FOREIGN KEY (grupo_id) REFERENCES grupos (id),
      CONSTRAINT fk_asignaciones_profesor FOREIGN KEY (profesor_id) REFERENCES usuarios (id),
      CONSTRAINT fk_asignaciones_practica FOREIGN KEY (practica_id) REFERENCES practicas (id),
      CONSTRAINT ck_asignaciones_estado CHECK (estado IN ('abierta', 'cerrada'))
    )
    """,
    "CREATE INDEX ix_asignaciones_grupo ON asignaciones (grupo_id, creada_en)",

    # 4. ENTREGAS
    """
    CREATE TABLE entregas (
      asignacion_id    VARCHAR2(40)   NOT NULL,
      alumno_id        VARCHAR2(100)  NOT NULL,
      estado           VARCHAR2(12)   DEFAULT 'entregada' NOT NULL,
      progreso         NUMBER(3)      DEFAULT 0 NOT NULL,
      completada       NUMBER(1)      DEFAULT 0 NOT NULL,
      intentos         NUMBER(6)      DEFAULT 0 NOT NULL,
      resumen          VARCHAR2(2000),
      modelo           CLOB,
      modelo_bytes     NUMBER(9)      DEFAULT 0 NOT NULL,
      entregada_en     TIMESTAMP      DEFAULT SYSTIMESTAMP NOT NULL,
      recogido_en      TIMESTAMP,
      calificacion     NUMBER(2),
      comentario       VARCHAR2(1000),
      evaluada_en      TIMESTAMP,
      vista_alumno     NUMBER(1)      DEFAULT 1 NOT NULL,
      CONSTRAINT pk_entregas PRIMARY KEY (asignacion_id, alumno_id),
      CONSTRAINT fk_entregas_asignacion FOREIGN KEY (asignacion_id) REFERENCES asignaciones (id),
      CONSTRAINT fk_entregas_alumno FOREIGN KEY (alumno_id) REFERENCES usuarios (id),
      CONSTRAINT ck_entregas_estado CHECK (estado IN ('entregada', 'evaluada', 'rehacer')),
      CONSTRAINT ck_entregas_completada CHECK (completada IN (0, 1)),
      CONSTRAINT ck_entregas_vista CHECK (vista_alumno IN (0, 1)),
      CONSTRAINT ck_entregas_calificacion CHECK (calificacion BETWEEN 0 AND 10),
      CONSTRAINT ck_entregas_resumen CHECK (resumen IS JSON)
    )
    """,
    "CREATE INDEX ix_entregas_alumno ON entregas (alumno_id)"
]

def tabla_existe(con, tabla):
    r = con.execute(
        text("SELECT COUNT(*) FROM all_tables WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AND table_name = :t"),
        {"t": tabla.upper()}
    ).scalar()
    return (r or 0) > 0

def indice_existe(con, idx):
    r = con.execute(
        text("SELECT COUNT(*) FROM all_indexes WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AND index_name = :i"),
        {"i": idx.upper()}
    ).scalar()
    return (r or 0) > 0

def main():
    print("Conectando a la base de datos...")
    motor = crear_motor()
    with motor.begin() as con:
        usuario, esquema = con.execute(
            text("SELECT USER, SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL")
        ).one()
        print(f"Conectado como usuario '{usuario}', esquema activo '{esquema}'.")

        # 1. GRUPOS
        if not tabla_existe(con, "grupos"):
            print("Creando tabla GRUPOS...")
            con.execute(text(DDL_STATEMENTS[0]))
            print("OK")
        else:
            print("Tabla GRUPOS ya existe.")

        if not indice_existe(con, "ix_grupos_profesor"):
            con.execute(text(DDL_STATEMENTS[1]))
            print("Indice ix_grupos_profesor creado.")

        # 2. GRUPO_ALUMNOS
        if not tabla_existe(con, "grupo_alumnos"):
            print("Creando tabla GRUPO_ALUMNOS...")
            con.execute(text(DDL_STATEMENTS[2]))
            print("OK")
        else:
            print("Tabla GRUPO_ALUMNOS ya existe.")

        if not indice_existe(con, "ix_grupo_alumnos_alumno"):
            con.execute(text(DDL_STATEMENTS[3]))
            print("Indice ix_grupo_alumnos_alumno creado.")

        # 3. ASIGNACIONES
        if not tabla_existe(con, "asignaciones"):
            print("Creando tabla ASIGNACIONES...")
            con.execute(text(DDL_STATEMENTS[4]))
            print("OK")
        else:
            print("Tabla ASIGNACIONES ya existe.")

        if not indice_existe(con, "ix_asignaciones_grupo"):
            con.execute(text(DDL_STATEMENTS[5]))
            print("Indice ix_asignaciones_grupo creado.")

        # 4. ENTREGAS
        if not tabla_existe(con, "entregas"):
            print("Creando tabla ENTREGAS...")
            con.execute(text(DDL_STATEMENTS[6]))
            print("OK")
        else:
            print("Tabla ENTREGAS ya existe.")

        if not indice_existe(con, "ix_entregas_alumno"):
            con.execute(text(DDL_STATEMENTS[7]))
            print("Indice ix_entregas_alumno creado.")

    print("\n✓ Todas las tablas e índices de 014 aplicados correctamente en Oracle.")

if __name__ == "__main__":
    main()
