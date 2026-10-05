"""El servidor detecta solo los practica.json del repositorio al arrancar."""
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from contenido import motor as motor_practicas
from database import conexion
from database.modelos import Practica


def arrancar(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'arranque.db'}")
    monkeypatch.setenv("AMATISTA_SINCRONIZAR_PRACTICAS", "1")
    conexion.motor.cache_clear()
    from main import app

    with TestClient(app):
        pass
    with Session(conexion.motor()) as db:
        filas = {p.id: (p.estado, p.version, p.version_publicada, p.origen) for p in db.scalars(select(Practica))}
    conexion.motor.cache_clear()
    return filas


def test_al_arrancar_registra_y_publica_todas_las_del_repositorio(tmp_path, monkeypatch):
    del_repo = {datos["id"] for _, datos in motor_practicas.practicas_del_repositorio()}
    filas = arrancar(tmp_path, monkeypatch)
    assert len(del_repo) == 18
    assert set(filas) == del_repo
    assert all(estado == "publicado" and publicada == version and origen == "repositorio"
               for estado, version, publicada, origen in filas.values())


def test_volver_a_arrancar_no_crea_versiones_nuevas(tmp_path, monkeypatch):
    primera = arrancar(tmp_path, monkeypatch)
    segunda = arrancar(tmp_path, monkeypatch)
    assert primera == segunda


def test_respeta_una_practica_que_el_equipo_dejo_en_borrador(tmp_path, monkeypatch):
    arrancar(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'arranque.db'}")
    conexion.motor.cache_clear()
    with Session(conexion.motor()) as db:
        practica = db.get(Practica, "blender.bp.m1.tren")
        practica.estado, practica.version_publicada = "borrador", None
        db.commit()
    conexion.motor.cache_clear()
    filas = arrancar(tmp_path, monkeypatch)
    assert filas["blender.bp.m1.tren"][0] == "borrador"


def test_apagado_no_registra_nada(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'apagado.db'}")
    monkeypatch.setenv("AMATISTA_SINCRONIZAR_PRACTICAS", "0")
    conexion.motor.cache_clear()
    from main import app

    with TestClient(app):
        pass
    with Session(conexion.motor()) as db:
        assert db.scalars(select(Practica)).first() is None
    conexion.motor.cache_clear()


def test_revisar_dice_lo_que_falta(tmp_path, monkeypatch):
    from herramientas.contenido import revisar_practicas

    arrancar(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'arranque.db'}")
    conexion.motor.cache_clear()
    with Session(conexion.motor()) as db:
        assert revisar_practicas(db) == []
        db.delete(db.get(Practica, "blender.bi.m1.puente"))
        db.commit()
        assert revisar_practicas(db) == [("blender.bi.m1.puente", "no está en la base")]
    conexion.motor.cache_clear()
