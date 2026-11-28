"""Pruebas unitarias e idempotentes para el puente MORAL -> SISTEMA_LOTES (015)."""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from database.modelos import SistemaLote
from herramientas.aplicar_015_oracle import aplicar_015
from herramientas.registrar_lote import main, normalizar_registros, registrar_manifiestos


def _manifiesto_repo(
    repo: str = "amatista-recuperada",
    commit_sha: str = "9e4598b1234567890abcdef1234567890abcdef",
    puntaje: int = 92,
) -> dict:
    """Manifiesto amatista.moral/1 de un repositorio de ejemplo con el puntaje indicado."""
    return {
        "contrato": "amatista.moral/1",
        "fecha_utc": "2026-11-19T18:00:00Z",
        "repo": repo,
        "rama": "main",
        "commit_sha": commit_sha,
        "ultimo_tag": "v3.2.0-moral",
        "motor_version": "2dff340f9daa",
        "geoda_version": "2e4c26d6077c",
        "puntaje_total": puntaje,
        "maximo": 100,
        "reglas": [],
        "lotes": [],
    }


def _motor_sqlite():
    """Base SQLite en memoria con solo la tabla SISTEMA_LOTES."""
    motor = create_engine("sqlite:///:memory:")
    SistemaLote.__table__.create(motor)
    return motor


def test_aplicar_015_en_sqlite_es_idempotente():
    """Comprueba que aplicar 015 dos veces crea la tabla e índice solo la primera vez."""
    motor = create_engine("sqlite:///:memory:")
    primera = aplicar_015(motor)
    segunda = aplicar_015(motor)
    assert primera == {"tabla_creada": True, "indice_creado": True}
    assert segunda == {"tabla_creada": False, "indice_creado": False}


def test_registrar_manifiesto_simple_y_upsert_idempotente():
    """Comprueba que registrar el mismo manifiesto dos veces inserta y luego actualiza la misma fila."""
    motor = _motor_sqlite()
    datos = _manifiesto_repo(puntaje=88)

    res1 = registrar_manifiestos(datos, lote="M5", motor=motor)
    assert res1 == [
        {
            "accion": "INSERT",
            "id": "amatista-recuperada:M5:9e4598b12345",
            "repo": "amatista-recuperada",
            "lote": "M5",
            "puntaje_moral": 88,
        }
    ]

    datos_actualizados = _manifiesto_repo(puntaje=96)
    res2 = registrar_manifiestos(datos_actualizados, lote="M5", motor=motor)
    assert res2[0]["accion"] == "UPDATE"
    assert res2[0]["puntaje_moral"] == 96

    with Session(motor) as sesion:
        filas = sesion.scalars(select(SistemaLote)).all()
        assert len(filas) == 1
        fila = filas[0]
        assert fila.contrato_ok == 1
        assert fila.puntaje_moral == 96
        assert fila.tag == "v3.2.0-moral"
        assert json.loads(fila.manifiesto)["puntaje_total"] == 96


def test_registrar_manifiesto_consolidado_todos_repos():
    """Comprueba que un manifiesto consolidado registra una fila por repositorio."""
    motor = _motor_sqlite()
    consolidado = {
        "contrato": "amatista.moral/1",
        "fecha_utc": "2026-11-19T18:10:00Z",
        "ecosistema_media": 94.5,
        "repositorios": [
            _manifiesto_repo("amatista-recuperada", "111111111111aaaa", 96),
            _manifiesto_repo("amatista-api", "222222222222bbbb", 95),
            _manifiesto_repo("amatista-motor", "2dff340f9daa480a", 94),
            _manifiesto_repo("amatista-g7b-etl", "2e4c26d6077c5d1a", 93),
        ],
    }

    resumen = registrar_manifiestos(consolidado, lote="M5", motor=motor)
    assert len(resumen) == 4
    assert {r["repo"] for r in resumen} == {
        "amatista-recuperada",
        "amatista-api",
        "amatista-motor",
        "amatista-g7b-etl",
    }

    with Session(motor) as sesion:
        filas = sesion.scalars(select(SistemaLote)).all()
        assert len(filas) == 4
        assert all(f.lote == "M5" and f.contrato_ok == 1 for f in filas)


def test_dry_run_no_requiere_db_ni_inserta_filas():
    """Comprueba que --dry-run valida y resume sin conectarse a la base."""
    resumen = registrar_manifiestos(_manifiesto_repo(), lote="M5", dry_run=True)
    assert len(resumen) == 1
    assert resumen[0]["accion"] == "DRY-RUN"


@pytest.mark.parametrize(
    "mutacion",
    [
        {"contrato": "otro.contrato/2"},
        {"puntaje_total": -1},
        {"puntaje_total": 101},
        {"puntaje_total": True},
        {"repo": ""},
        {"commit_sha": ""},
    ],
)
def test_rechaza_manifiestos_invalidos(mutacion):
    """Comprueba que normalizar_registros rechaza manifiestos con campos inválidos (ValueError)."""
    datos = _manifiesto_repo()
    datos.update(mutacion)
    with pytest.raises(ValueError):
        normalizar_registros(datos, lote="M5")


def test_cli_dry_run_y_escritura_en_sqlite_por_env(tmp_path, monkeypatch, capsys):
    """Comprueba la CLI de registrar_lote en modo --dry-run y escribiendo en SQLite configurada por entorno."""
    archivo = tmp_path / "moral.json"
    archivo.write_text(json.dumps(_manifiesto_repo(puntaje=95)), encoding="utf-8")

    codigo = main(["--archivo", str(archivo), "--lote", "M5", "--dry-run"])
    assert codigo == 0
    salida = capsys.readouterr().out
    assert "DRY-RUN: id=amatista-recuperada:M5:9e4598b12345" in salida

    db_ruta = tmp_path / "moral_lotes.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_ruta.as_posix()}")
    motor = create_engine(f"sqlite:///{db_ruta.as_posix()}")
    SistemaLote.__table__.create(motor)
    motor.dispose()

    codigo2 = main(["--archivo", str(archivo), "--lote", "M5"])
    assert codigo2 == 0
    salida2 = capsys.readouterr().out
    assert "INSERT: id=amatista-recuperada:M5:9e4598b12345" in salida2
