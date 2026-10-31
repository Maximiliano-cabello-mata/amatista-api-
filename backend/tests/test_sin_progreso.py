"""Motor 7: una cuenta de administrador practica sin llevar progreso.

Cada vez que entra, todo empieza de cero: no se guardan lecciones, insignias,
eventos ni intentos del add-on, y las lecturas vuelven vacías. Un alumno
normal sigue guardando todo (para que la excepción no se cuele).
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import conexion
from database.modelos import EventoAprendizaje, Logro, ProgresoLeccion, ProgresoPractica
from tests.test_addon import API, escena_mesa, sembrar, vincular  # noqa: F401  (sembrar es fixture)


def contar(modelo, *condiciones):
    with Session(conexion.motor()) as db:
        return db.scalar(select(func.count()).select_from(modelo).where(*condiciones))


def avance(**campos):
    return {"curso_id": "blender", "leccion_id": "les_001", "completada": True, "puntaje": 90, **campos}


def test_el_administrador_no_guarda_ni_lee_progreso(cliente, crear_cuenta):
    admin_id, admin = crear_cuenta(rol="admin")
    guardado = cliente.post("/api/progreso", json={"eventos": [avance()], "insignias": ["primeros_pasos"]},
                            headers=admin)
    assert guardado.status_code == 200, guardado.text
    assert guardado.json() == {"guardados": 0, "insignias": 0, "sin_progreso": True}

    sincronizado = cliente.post("/api/sincronizar", json={
        "progreso": [avance()], "insignias": ["primeros_pasos"],
        "eventos": [{"id": "6c1d4c4e-0000-4000-8000-000000000001", "tipo": "lesson_completed",
                     "curso_id": "blender", "leccion_id": "les_001"}],
    }, headers=admin)
    assert sincronizado.status_code == 200, sincronizado.text
    assert sincronizado.json()["sin_progreso"] is True and sincronizado.json()["guardados"] == 0

    leido = cliente.get("/api/progreso", headers=admin).json()
    assert leido["lecciones"] == [] and leido["insignias"] == [] and leido["sin_progreso"] is True
    assert contar(ProgresoLeccion, ProgresoLeccion.usuario_id == admin_id) == 0
    assert contar(Logro, Logro.usuario_id == admin_id) == 0
    assert contar(EventoAprendizaje, EventoAprendizaje.usuario_id == admin_id) == 0


def test_el_alumno_si_guarda_su_progreso(cliente, crear_cuenta):
    alumno_id, alumno = crear_cuenta()
    guardado = cliente.post("/api/progreso", json={"eventos": [avance()]}, headers=alumno).json()
    assert guardado["guardados"] == 1 and "sin_progreso" not in guardado
    assert [f["leccion_id"] for f in cliente.get("/api/progreso", headers=alumno).json()["lecciones"]] == ["les_001"]


def test_el_administrador_practica_en_blender_sin_dejar_rastro(cliente, crear_cuenta, sembrar):
    admin_id, admin = crear_cuenta(rol="admin")
    sembrar(cliente, admin)
    token, _ = vincular(cliente, admin)

    intento = cliente.post(f"{API}/intentos", json={
        "practica_id": "blender.n1.mesa", "version": 1, "escena": escena_mesa(),
    }, headers=token)
    assert intento.status_code == 200, intento.text
    cuerpo = intento.json()
    assert cuerpo["completada"] is True and cuerpo["sin_progreso"] is True  # se evalúa igual, para probar
    assert contar(ProgresoPractica, ProgresoPractica.usuario_id == admin_id) == 0
    assert cliente.get(f"{API}/mi-progreso", headers=admin).json() == {"practicas": [], "sin_progreso": True}


def test_el_taller_del_administrador_empieza_de_cero(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    habilidades = cliente.get(f"{API}/habilidades", headers=admin)
    assert habilidades.status_code == 200, habilidades.text
    assert habilidades.json()["estado"] == {} and habilidades.json()["completadas"] == []
