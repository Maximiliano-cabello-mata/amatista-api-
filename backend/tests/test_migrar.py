"""herramientas/migrar.py: DDL para otras bases y respaldo JSONL de ida y vuelta."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from database.modelos import Base, Curso, ProgresoLeccion, Usuario
from herramientas import migrar

CARPETA_DOCS = migrar.BACKEND.parent / "docs" / "base-de-datos"


def base_con_datos(ruta):
    motor = create_engine(f"sqlite:///{ruta}")
    Base.metadata.create_all(motor)
    with Session(motor) as db:
        # El intermedio se agrega primero: importar debe ordenar el requisito.
        db.add(Curso(id="blender_principiante_intermedio", titulo="PI", ruta="blender",
                     requisito_id="blender_principiante", orden=2))
        db.add(Curso(id="blender_principiante", titulo="P", ruta="blender", orden=1))
        db.add(Usuario(id="alumno-1", nombre="Ana", email="ana@ejemplo.com", rol="alumno"))
        db.flush()
        db.add(ProgresoLeccion(usuario_id="alumno-1", curso_id="blender_principiante", leccion_id="bp1_gancho",
                               completada=1, puntaje=90, datos_ligeros='{"a":3}',
                               completada_en=datetime(2026, 10, 4, 12, 30, 5)))
        db.commit()
    return motor


@pytest.mark.parametrize("dialecto", migrar.DIALECTOS)
def test_ddl_trae_todas_las_tablas_y_los_checks(dialecto):
    texto = migrar.ddl(dialecto)
    for tabla in Base.metadata.tables:
        assert f"CREATE TABLE {tabla}" in texto, tabla
    assert "ck_usuarios_rol CHECK (rol IN ('alumno', 'profesor', 'admin'))" in texto
    assert "CREATE INDEX ix_eventos_usuario_fecha" in texto


def test_cada_check_apunta_a_una_columna_real():
    for tabla, columna, valores in migrar.CHECKS:
        assert columna in Base.metadata.tables[tabla].columns, f"{tabla}.{columna}"
        assert valores


def test_el_esquema_postgresql_del_repositorio_esta_al_dia():
    archivo = CARPETA_DOCS / "esquema_postgresql.sql"
    assert archivo.read_text("utf-8") == migrar.ddl("postgresql"), (
        "Regenera: python herramientas/migrar.py ddl --dialecto postgresql --salida ../docs/base-de-datos/esquema_postgresql.sql"
    )


def test_exportar_e_importar_de_ida_y_vuelta(tmp_path):
    origen = base_con_datos(tmp_path / "origen.db")
    respaldo = tmp_path / "respaldo"
    manifiesto = migrar.exportar(origen, respaldo)
    assert manifiesto["formato"] == migrar.FORMATO
    assert manifiesto["tablas"]["cursos"]["filas"] == 2
    assert manifiesto["tablas"]["progreso_lecciones"]["filas"] == 1
    linea = json.loads((respaldo / "progreso_lecciones.jsonl").read_text("utf-8"))
    assert linea["completada_en"] == "2026-10-04T12:30:05"

    destino = create_engine(f"sqlite:///{tmp_path / 'destino.db'}")
    filas = migrar.importar(destino, respaldo, crear_tablas=True)
    assert filas["cursos"] == 2 and filas["usuarios"] == 1
    with Session(destino) as db:
        progreso = db.scalars(select(ProgresoLeccion)).one()
        assert progreso.completada_en == datetime(2026, 10, 4, 12, 30, 5)
        assert progreso.datos_ligeros == '{"a":3}'
        assert db.get(Curso, "blender_principiante_intermedio").requisito_id == "blender_principiante"


def test_importar_nunca_escribe_sobre_datos(tmp_path):
    origen = base_con_datos(tmp_path / "origen.db")
    migrar.exportar(origen, tmp_path / "respaldo")
    with pytest.raises(migrar.ErrorMigracion, match="ya tiene filas"):
        migrar.importar(origen, tmp_path / "respaldo")


def test_importar_detecta_un_archivo_alterado(tmp_path):
    origen = base_con_datos(tmp_path / "origen.db")
    respaldo = tmp_path / "respaldo"
    migrar.exportar(origen, respaldo)
    with (respaldo / "usuarios.jsonl").open("a", encoding="utf-8") as archivo:
        archivo.write('{"id":"intruso"}\n')
    destino = create_engine(f"sqlite:///{tmp_path / 'destino.db'}")
    with pytest.raises(migrar.ErrorMigracion, match="huella"):
        migrar.importar(destino, respaldo, crear_tablas=True)


def test_terminal(tmp_path, capsys):
    base_con_datos(tmp_path / "origen.db")
    url_origen = f"sqlite:///{tmp_path / 'origen.db'}"
    url_destino = f"sqlite:///{tmp_path / 'destino.db'}"
    respaldo = str(tmp_path / "respaldo")
    assert migrar.main(["exportar", respaldo, "--url", url_origen]) == 0
    assert migrar.main(["importar", respaldo, "--url", url_destino, "--crear-tablas"]) == 0
    assert migrar.main(["verificar", respaldo, "--url", url_destino]) == 0
    assert migrar.main(["importar", respaldo, "--url", url_destino]) == 1
    salida = capsys.readouterr()
    assert "Conteos verificados" in salida.out
    assert "ya tiene filas" in salida.err
