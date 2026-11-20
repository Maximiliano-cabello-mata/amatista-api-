"""Aplica de forma idempotente 016 (ESQUEMA_MIGRACIONES, permisos y línea base).

Uso desde ``backend/``:
    python -m herramientas.aplicar_016_oracle
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

RAIZ_BACKEND = Path(__file__).resolve().parents[1]
if str(RAIZ_BACKEND) not in sys.path:
    sys.path.insert(0, str(RAIZ_BACKEND))

from database.conexion import crear_motor, esquema_oracle  # noqa: E402
from database.modelos import EsquemaMigracion  # noqa: E402
from herramientas import migrar  # noqa: E402

DDL_TABLA_ORACLE = """
CREATE TABLE esquema_migraciones (
  version      VARCHAR2(10)  NOT NULL,
  archivo      VARCHAR2(120) NOT NULL,
  sha256       VARCHAR2(64)  NOT NULL,
  origen       VARCHAR2(40)  DEFAULT 'aplicada' NOT NULL,
  notas        VARCHAR2(255),
  aplicada_en  TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_esquema_migraciones PRIMARY KEY (version),
  CONSTRAINT ck_esquema_migraciones_origen CHECK (origen IN ('aplicada', 'linea_base', 'manual'))
)
""".strip()

TABLAS_PERMISOS_016 = (
    "GRUPOS",
    "GRUPO_ALUMNOS",
    "ASIGNACIONES",
    "ENTREGAS",
    "ESQUEMA_MIGRACIONES",
)


def aplicar_016(
    motor: Engine,
    carpeta_sql: Optional[Path] = None,
) -> Dict[str, Any]:
    """Crea ESQUEMA_MIGRACIONES, completa GRANTs a AMATISTA_APP y registra 001–016."""
    esquema = esquema_oracle() if motor.dialect.name == "oracle" else None
    inspector = inspect(motor)
    tabla_creada = False

    if not inspector.has_table("esquema_migraciones", schema=esquema):
        if motor.dialect.name == "oracle":
            with motor.begin() as conn:
                conn.execute(text(DDL_TABLA_ORACLE))
        else:
            EsquemaMigracion.__table__.create(bind=motor, checkfirst=True)
        tabla_creada = True

    permisos_otorgados: List[str] = []
    if motor.dialect.name == "oracle":
        with motor.begin() as conn:
            existe_app = conn.execute(
                text("SELECT COUNT(*) FROM all_users WHERE username = 'AMATISTA_APP'")
            ).scalar_one()
            usuario_actual = (conn.execute(text("SELECT USER FROM dual")).scalar_one() or "").upper()
            if existe_app and usuario_actual != "AMATISTA_APP":
                existentes = {
                    t.upper()
                    for t in conn.execute(text("SELECT table_name FROM user_tables")).scalars().all()
                }
                for tabla in TABLAS_PERMISOS_016:
                    if tabla in existentes:
                        conn.execute(
                            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabla} TO amatista_app")
                        )
                        permisos_otorgados.append(tabla)

    registradas = migrar.registrar_linea_base(motor, carpeta_sql=carpeta_sql)
    return {
        "tabla_creada": tabla_creada,
        "permisos_otorgados": permisos_otorgados,
        "registradas": registradas,
    }


def main() -> int:
    motor = crear_motor()
    try:
        res = aplicar_016(motor)
    finally:
        motor.dispose()

    estado = "creada" if res["tabla_creada"] else "ya existía"
    grants = len(res["permisos_otorgados"])
    nuevas = len(res["registradas"])
    print(
        f"016 Migraciones: ESQUEMA_MIGRACIONES {estado}; "
        f"GRANTs otorgados={grants}; scripts registrados={nuevas}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
