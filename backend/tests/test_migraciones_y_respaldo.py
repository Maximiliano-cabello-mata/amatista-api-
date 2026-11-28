"""Pruebas de 016 (ESQUEMA_MIGRACIONES), migrar estado y respaldo con restauración."""
import json
from datetime import datetime
# [REVISION-MUERTO] Path y select (abajo) se importan y no se usan.
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from database.modelos import Base, Curso, EsquemaMigracion, SistemaLote, Usuario
from herramientas import migrar, respaldar_y_probar
from herramientas.aplicar_016_oracle import aplicar_016


def test_aplicar_016_idempotente_y_estado_completo_en_sqlite(tmp_path):
    """Comprueba que aplicar 016 dos veces en SQLite deja ESQUEMA_MIGRACIONES completa y sin pendientes."""
    db_url = f"sqlite:///{(tmp_path / 'mig_016.db').as_posix()}"
    motor = create_engine(db_url)
    try:
        antes = migrar.estado_migraciones(motor)
        assert antes["tabla_existe"] is False
        assert "016" in antes["pendientes"]

        primera = aplicar_016(motor)
        segunda = aplicar_016(motor)
        assert primera["tabla_creada"] is True
        assert "016" in primera["registradas"]
        assert segunda["tabla_creada"] is False
        assert segunda["registradas"] == []

        despues = migrar.estado_migraciones(motor)
        assert despues["tabla_existe"] is True
        assert despues["pendientes"] == []
        assert despues["modificadas"] == []
        assert len(despues["aplicadas"]) == len(migrar.scripts_sql())

        assert migrar.main(["estado", "--url", db_url, "--estricto"]) == 0
    finally:
        motor.dispose()


def test_estado_migraciones_detecta_pendiente_y_cambio_sha(tmp_path):
    """Comprueba que estado_migraciones detecta un script pendiente y uno cuyo SHA cambió."""
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    s1 = sql_dir / "001_init.sql"
    s2 = sql_dir / "002_extra.sql"
    s1.write_text("SELECT 1;\n", encoding="utf-8")
    s2.write_text("SELECT 2;\n", encoding="utf-8")

    db_url = f"sqlite:///{(tmp_path / 'estado.db').as_posix()}"
    motor = create_engine(db_url)
    try:
        # Sin tabla: --estricto falla.
        assert migrar.main(["estado", "--url", db_url, "--estricto"]) == 1

        EsquemaMigracion.__table__.create(motor)
        registradas = migrar.registrar_linea_base(motor, carpeta_sql=sql_dir)
        assert registradas == ["001", "002"]

        # Añadir script nuevo sin registrar -> pendiente.
        s3 = sql_dir / "003_nuevo.sql"
        s3.write_text("SELECT 3;\n", encoding="utf-8")
        # Modificar script 001 -> SHA distinto.
        s1.write_text("SELECT 999;\n", encoding="utf-8")

        est = migrar.estado_migraciones(motor, carpeta_sql=sql_dir)
        assert est["aplicadas"] == ["002"]
        assert est["pendientes"] == ["003"]
        assert est["modificadas"] == ["001"]
    finally:
        motor.dispose()


def test_respaldar_y_probar_exporta_restaura_verifica_y_rota(tmp_path):
    """Comprueba que el respaldo verificado exporta, restaura en SQLite, escribe la verificación y rota copias viejas."""
    origen_ruta = tmp_path / "origen.db"
    db_url = f"sqlite:///{origen_ruta.as_posix()}"
    motor = create_engine(db_url)
    Base.metadata.create_all(motor)
    with Session(motor) as db:
        db.add(Curso(id="blender_principiante", titulo="P", ruta="blender", orden=1))
        db.add(Usuario(id="u-1", nombre="Ana", email="ana@ejemplo.com", rol="alumno"))
        db.add(
            SistemaLote(
                id="amatista-api:M5:abc",
                lote="M5",
                repo="amatista-api",
                rama="main",
                commit_sha="abc123",
                puntaje_moral=96,
                contrato_ok=1,
                manifiesto='{"ok":true}',
                registrado_en=datetime(2026, 11, 19, 12, 0, 0),
            )
        )
        db.add(
            EsquemaMigracion(
                version="016",
                archivo="016_migraciones.sql",
                sha256="a" * 64,
                origen="linea_base",
                aplicada_en=datetime(2026, 11, 19, 12, 0, 0),
            )
        )
        db.commit()

    destino = tmp_path / "respaldos"
    destino.mkdir()
    ajeno = destino / "copia-manual-no-tocar"
    ajeno.mkdir()
    viejo_1 = destino / "amatista-20261101-010000"
    viejo_2 = destino / "amatista-20261102-010000"
    viejo_1.mkdir()
    viejo_2.mkdir()

    try:
        rc = respaldar_y_probar.main(
            ["--destino", str(destino), "--mantener", "2", "--url", db_url]
        )
        assert rc == 0
    finally:
        motor.dispose()

    # Mantiene la carpeta ajena + 2 snapshots más recientes (viejo_2 + nuevo).
    assert ajeno.is_dir()
    assert not viejo_1.exists()
    assert viejo_2.is_dir()
    snaps = sorted(
        p for p in destino.iterdir()
        if p.is_dir() and respaldar_y_probar.PATRON_SNAPSHOT.match(p.name)
    )
    assert len(snaps) == 2
    nuevo = snaps[-1]
    verif_archivo = nuevo / "verificacion_restauracion.json"
    assert verif_archivo.is_file()
    verif = json.loads(verif_archivo.read_text(encoding="utf-8"))
    assert verif["restauracion_ok"] is True
    assert verif["tablas"] == len(Base.metadata.tables)
    assert verif["filas_totales"] == 4
    # Sin PII en verificacion_restauracion.json.
    assert "ana@ejemplo.com" not in verif_archivo.read_text(encoding="utf-8")

    # También funciona --solo-verificar sobre un snapshot existente.
    verif_archivo.unlink()
    assert respaldar_y_probar.main(["--solo-verificar", str(nuevo / "base")]) == 0
    assert verif_archivo.is_file()
