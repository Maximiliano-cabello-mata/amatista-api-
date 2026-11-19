"""Aplica de forma idempotente el script 015 (tabla SISTEMA_LOTES de MORAL).

Uso desde ``backend/``:
    python -m herramientas.aplicar_015_oracle
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

RAIZ_BACKEND = Path(__file__).resolve().parents[1]
if str(RAIZ_BACKEND) not in sys.path:
    sys.path.insert(0, str(RAIZ_BACKEND))

from database.conexion import crear_motor, esquema_oracle  # noqa: E402
from database.modelos import SistemaLote  # noqa: E402

DDL_TABLA_ORACLE = """
CREATE TABLE sistema_lotes (
  id               VARCHAR2(64)  NOT NULL,
  lote             VARCHAR2(40)  NOT NULL,
  repo             VARCHAR2(60)  NOT NULL,
  rama             VARCHAR2(60)  NOT NULL,
  commit_sha       VARCHAR2(40)  NOT NULL,
  tag              VARCHAR2(60),
  motor_version    VARCHAR2(40),
  geoda_version    VARCHAR2(40),
  puntaje_moral    NUMBER(3)     DEFAULT 0 NOT NULL,
  contrato_ok      NUMBER(1)     DEFAULT 0 NOT NULL,
  manifiesto       CLOB,
  registrado_en    TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_sistema_lotes PRIMARY KEY (id),
  CONSTRAINT ck_sistema_lotes_puntaje CHECK (puntaje_moral BETWEEN 0 AND 100),
  CONSTRAINT ck_sistema_lotes_ok CHECK (contrato_ok IN (0, 1)),
  CONSTRAINT ck_sistema_lotes_json CHECK (manifiesto IS JSON)
)
""".strip()

DDL_INDICE = "CREATE INDEX ix_sistema_lotes_repo_fecha ON sistema_lotes (repo, registrado_en)"


def aplicar_015(motor: Engine) -> dict[str, bool]:
    """Crea SISTEMA_LOTES y su índice compuesto si aún no existen."""
    esquema = esquema_oracle() if motor.dialect.name == "oracle" else None
    inspector = inspect(motor)
    tabla_existia = inspector.has_table("sistema_lotes", schema=esquema)
    indices_antes = (
        {
            (ix.get("name") or "").lower()
            for ix in inspector.get_indexes("sistema_lotes", schema=esquema)
        }
        if tabla_existia
        else set()
    )
    tabla_creada = False

    if not tabla_existia:
        if motor.dialect.name == "oracle":
            with motor.begin() as conn:
                conn.execute(text(DDL_TABLA_ORACLE))
        else:
            SistemaLote.__table__.create(bind=motor, checkfirst=True)
        tabla_creada = True

    inspector = inspect(motor)
    indices_despues = {
        (ix.get("name") or "").lower()
        for ix in inspector.get_indexes("sistema_lotes", schema=esquema)
    }
    if "ix_sistema_lotes_repo_fecha" not in indices_despues:
        with motor.begin() as conn:
            conn.execute(text(DDL_INDICE))
    indice_creado = "ix_sistema_lotes_repo_fecha" not in indices_antes

    if motor.dialect.name == "oracle" and tabla_creada:
        with motor.begin() as conn:
            existe_app = conn.execute(
                text("SELECT COUNT(*) FROM all_users WHERE username = 'AMATISTA_APP'")
            ).scalar_one()
            usuario_actual = (conn.execute(text("SELECT USER FROM dual")).scalar_one() or "").upper()
            if existe_app and usuario_actual != "AMATISTA_APP":
                conn.execute(
                    text("GRANT SELECT, INSERT, UPDATE, DELETE ON sistema_lotes TO amatista_app")
                )

    return {"tabla_creada": tabla_creada, "indice_creado": indice_creado}


def main() -> int:
    motor = crear_motor()
    try:
        resultado = aplicar_015(motor)
    finally:
        motor.dispose()

    estado_tabla = "creada" if resultado["tabla_creada"] else "ya existía"
    estado_indice = "creado" if resultado["indice_creado"] else "ya existía"
    print(f"015 MORAL: SISTEMA_LOTES {estado_tabla}; índice ix_sistema_lotes_repo_fecha {estado_indice}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
