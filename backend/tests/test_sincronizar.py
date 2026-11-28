"""/api/sincronizar, cuerpos gzip y GET /api/progreso?desde= (escuelas con pocos datos)."""
import gzip
import json
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import conexion
from database.modelos import EventoAprendizaje, Logro, ProgresoLeccion, ahora

ANONIMO = "alumno-0a0b0c0d-0000-4000-8000-000000000009"


def evento(tipo="lesson_completed"):
    """Evento de aprendizaje de ejemplo con id nuevo para la lección les_001."""
    return {"id": str(uuid.uuid4()), "tipo": tipo, "curso_id": "blender", "leccion_id": "les_001"}


def contar(modelo, *condiciones):
    """Cuenta las filas de `modelo` que cumplen las condiciones."""
    with Session(conexion.motor()) as db:
        return db.scalar(select(func.count()).select_from(modelo).where(*condiciones))


def comprimido(cliente, ruta, cuerpo, cabeceras=None):
    """Hace un POST con el cuerpo JSON comprimido en gzip (Content-Encoding: gzip)."""
    datos = gzip.compress(json.dumps(cuerpo).encode())
    return cliente.post(
        ruta,
        content=datos,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip", **(cabeceras or {})},
    )


def test_un_solo_envio_guarda_progreso_insignias_y_eventos(cliente):
    """Comprueba que POST /api/sincronizar guarda progreso, insignias y eventos en una sola petición sin duplicar al reenviar."""
    eventos = [evento(), evento("activity_submitted")]
    cuerpo = {
        "usuario_id": ANONIMO,
        "progreso": [{"curso_id": "blender", "leccion_id": "les_001", "completada": True, "puntaje": 90}],
        "insignias": ["primeros_pasos"],
        "eventos": eventos + [eventos[0]],  # repetido dentro del lote: cuenta una vez
    }
    respuesta = cliente.post("/api/sincronizar", json=cuerpo)
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert (datos["guardados"], datos["insignias"], datos["eventos"], datos["nuevos"]) == (1, 1, 3, 2)
    assert datos["servidor_en"]
    assert contar(ProgresoLeccion, ProgresoLeccion.usuario_id == ANONIMO) == 1
    assert contar(Logro, Logro.usuario_id == ANONIMO) == 1
    assert contar(EventoAprendizaje, EventoAprendizaje.usuario_id == ANONIMO) == 2

    # Reenviar el mismo lote (la respuesta se perdió con poca señal) no duplica nada.
    otra = cliente.post("/api/sincronizar", json=cuerpo)
    assert otra.status_code == 200
    assert otra.json()["nuevos"] == 0
    assert contar(EventoAprendizaje, EventoAprendizaje.usuario_id == ANONIMO) == 2


def test_lote_vacio_se_rechaza(cliente):
    """Comprueba que un lote sin progreso, insignias ni eventos responde 422."""
    assert cliente.post("/api/sincronizar", json={"usuario_id": ANONIMO}).status_code == 422


def test_cuenta_registrada_sin_sesion_pide_iniciar_sesion(cliente, crear_cuenta):
    """Comprueba que sincronizar a una cuenta registrada sin sesión da 401 y con sesión funciona."""
    cuenta_id, cabeceras = crear_cuenta()
    sin_sesion = cliente.post("/api/sincronizar", json={"usuario_id": cuenta_id, "eventos": [evento()]})
    assert sin_sesion.status_code == 401
    con_sesion = cliente.post("/api/sincronizar", json={"eventos": [evento()]}, headers=cabeceras)
    assert con_sesion.status_code == 200
    assert contar(EventoAprendizaje, EventoAprendizaje.usuario_id == cuenta_id) == 1


def test_acepta_cuerpo_gzip(cliente):
    """Comprueba que /api/sincronizar y las rutas de siempre aceptan cuerpos gzip."""
    cuerpo = {"usuario_id": ANONIMO, "eventos": [evento() for _ in range(50)]}
    respuesta = comprimido(cliente, "/api/sincronizar", cuerpo)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["nuevos"] == 50
    # Las rutas de siempre también lo aceptan.
    assert comprimido(cliente, "/api/eventos", {"usuario_id": ANONIMO, "eventos": [evento()]}).status_code == 200


def test_gzip_invalido_o_codificacion_desconocida(cliente):
    """Comprueba que un gzip corrupto da 400 y una codificación distinta de gzip da 415."""
    roto = cliente.post(
        "/api/sincronizar",
        content=b"esto no es gzip",
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert roto.status_code == 400
    otra = cliente.post(
        "/api/sincronizar",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Encoding": "br"},
    )
    assert otra.status_code == 415


def test_bomba_gzip_se_corta(cliente, monkeypatch):
    """Comprueba que un gzip que descomprimido supera MAX_CUERPO se corta con 413."""
    import main

    monkeypatch.setattr(main, "MAX_CUERPO", 10_000)
    bomba = gzip.compress(b'{"eventos":[' + b" " * 200_000 + b"]}")
    assert len(bomba) < 10_000  # comprimida pasa el primer límite...
    respuesta = cliente.post(
        "/api/sincronizar",
        content=bomba,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert respuesta.status_code == 413  # ...pero no se descomprime entera


def test_progreso_desde_devuelve_solo_lo_cambiado(cliente, crear_cuenta):
    """Comprueba que GET /api/progreso?desde= devuelve solo las lecciones cambiadas (con margen)."""
    cuenta_id, cabeceras = crear_cuenta()
    avance = lambda leccion: {"curso_id": "blender", "leccion_id": leccion, "completada": True}  # noqa: E731
    cliente.post("/api/sincronizar", json={"progreso": [avance("les_001"), avance("les_002")]}, headers=cabeceras)
    # les_001 cambió hace una hora.
    with Session(conexion.motor()) as db:
        db.get(ProgresoLeccion, (cuenta_id, "blender", "les_001")).actualizado_en = ahora() - timedelta(hours=1)
        db.commit()

    completo = cliente.get("/api/progreso", headers=cabeceras).json()
    assert completo["parcial"] is False
    assert {f["leccion_id"] for f in completo["lecciones"]} == {"les_001", "les_002"}

    desde = (ahora() - timedelta(minutes=10)).isoformat()
    parcial = cliente.get("/api/progreso", params={"desde": desde}, headers=cabeceras).json()
    assert parcial["parcial"] is True
    assert [f["leccion_id"] for f in parcial["lecciones"]] == ["les_002"]
    assert parcial["servidor_en"]


def test_fusion_marca_las_filas_como_cambiadas(cliente):
    """Comprueba que fusionar un anónimo marca actualizado_en para que la descarga incremental lo vea."""
    from api.fusion import fusionar_alumno
    from database.modelos import Usuario

    cliente.post(
        "/api/sincronizar",
        json={"usuario_id": ANONIMO, "progreso": [{"curso_id": "blender", "leccion_id": "les_009", "completada": True}]},
    )
    with Session(conexion.motor()) as db:
        db.get(ProgresoLeccion, (ANONIMO, "blender", "les_009")).actualizado_en = ahora() - timedelta(days=3)
        db.add(Usuario(id="usr-destino", nombre="Destino", email="d@amatista.local", rol="alumno", password_hash="x"))
        db.commit()
        antes = ahora()
        fusionar_alumno(db, ANONIMO, "usr-destino")
        db.commit()
        fila = db.get(ProgresoLeccion, ("usr-destino", "blender", "les_009"))
        assert fila.actualizado_en >= antes - timedelta(seconds=1)
