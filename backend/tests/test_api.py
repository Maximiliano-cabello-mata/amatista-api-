import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from database import conexion
from database.modelos import Sesion, Usuario
from seguridad import hash_token


def contar(modelo):
    with Session(conexion.motor()) as db:
        return db.scalar(select(func.count()).select_from(modelo))


def test_inicio(cliente):
    assert cliente.get("/").json() == {"estado": "Backend activo"}


def test_salud(cliente):
    respuesta = cliente.get("/api/salud")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok", "motor": "sqlite"}


# --- /api/iniciar-sesion heredado (Laboratorio y versiones viejas de la PWA) ---


def test_iniciar_sesion_crea_usuario_una_vez(cliente):
    datos = {"email": "alumno_prueba@amatista.local", "dispositivo": "Mozilla/5.0"}
    primera = cliente.post("/api/iniciar-sesion", json=datos).json()
    segunda = cliente.post("/api/iniciar-sesion", json=datos).json()

    assert primera["mensaje"] == "Sesión registrada en la base de datos"
    assert primera["usuario_id"] == "alumno_prueba@amatista.local"
    assert primera["sesion_id"] != segunda["sesion_id"]
    assert len(primera["sesion_id"]) == 36
    assert contar(Usuario) == 1
    assert contar(Sesion) == 2
    with Session(conexion.motor()) as db:
        assert db.get(Sesion, hash_token(primera["sesion_id"])).activa == 0  # solo informativa


def test_iniciar_sesion_heredado_no_sirve_como_token(cliente):
    sesion_id = cliente.post("/api/iniciar-sesion", json={"usuario_id": "alumno-viejo-1"}).json()["sesion_id"]
    for cabeceras in ({"Authorization": f"Bearer {sesion_id}"}, {"X-Sesion-Id": sesion_id}):
        assert cliente.get("/api/auth/yo", headers=cabeceras).status_code == 401


def test_iniciar_sesion_heredado_rechaza_cuentas_registradas(cliente, crear_cuenta):
    usuario_id, _ = crear_cuenta(email="ana@amatista.local")
    for cuerpo in (
        {"usuario_id": usuario_id},
        {"email": "ANA@amatista.local"},
        {"usuario_id": "ana@amatista.local"},
        {"usuario_id": "alumno-cualquiera", "email": "ana@amatista.local"},
    ):
        respuesta = cliente.post("/api/iniciar-sesion", json=cuerpo)
        assert respuesta.status_code == 409, cuerpo
        assert respuesta.json()["detail"] == "Esta cuenta usa correo y contraseña: entra desde «Entrar»."
    assert contar(Usuario) == 1


def test_iniciar_sesion_sin_usuario(cliente):
    assert cliente.post("/api/iniciar-sesion", json={}).status_code == 422


# --- Progreso de alumnos anónimos ---


def test_progreso_nunca_retrocede(cliente):
    usuario = "alumno-123"

    def enviar(**evento):
        base = {"curso_id": "blender", "leccion_id": "les_004"}
        respuesta = cliente.post("/api/progreso", json={"usuario_id": usuario, "eventos": [{**base, **evento}]})
        assert respuesta.status_code == 200, respuesta.text

    enviar(completada=False, puntaje=67, intentos=1)
    enviar(completada=True, puntaje=100, intentos=2)
    enviar(completada=False, puntaje=33, intentos=3)  # un intento peor no borra el avance

    lecciones = cliente.get(f"/api/progreso/{usuario}").json()["lecciones"]
    assert len(lecciones) == 1
    assert lecciones[0]["completada"] is True
    assert lecciones[0]["puntaje"] == 100
    assert lecciones[0]["intentos"] == 3


def test_progreso_combina_eventos_repetidos(cliente):
    eventos = [
        {"curso_id": "aframe", "leccion_id": "les_af_001", "completada": True},
        {"curso_id": "aframe", "leccion_id": "les_af_001", "completada": False},
        {"curso_id": "aframe", "leccion_id": "les_af_002", "completada": True},
    ]
    respuesta = cliente.post("/api/progreso", json={"usuario_id": "alumno-1", "eventos": eventos})
    assert respuesta.status_code == 200
    assert respuesta.json()["guardados"] == 2
    lecciones = cliente.get("/api/progreso/alumno-1").json()["lecciones"]
    assert {(f["leccion_id"], f["completada"]) for f in lecciones} == {("les_af_001", True), ("les_af_002", True)}


def test_progreso_valida_datos(cliente):
    evento = {"curso_id": "blender", "leccion_id": "les_004", "puntaje": 150}
    assert cliente.post("/api/progreso", json={"usuario_id": "a", "eventos": [evento]}).status_code == 422
    assert cliente.post("/api/progreso", json={"usuario_id": "a", "eventos": []}).status_code == 422


def test_error_de_base_de_datos_devuelve_mensaje(cliente):
    with conexion.motor().begin() as c:
        c.execute(text("DROP TABLE progreso_lecciones"))
    evento = {"curso_id": "blender", "leccion_id": "les_001", "completada": True}
    respuesta = cliente.post("/api/progreso", json={"usuario_id": "a", "eventos": [evento]})
    assert respuesta.status_code == 500
    assert "no such table" in respuesta.json()["detail"]


def test_cors_permite_localhost_en_cualquier_puerto(cliente):
    respuesta = cliente.options(
        "/api/progreso",
        headers={"Origin": "http://localhost:5176", "Access-Control-Request-Method": "POST"},
    )
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:5176"


def test_cors_rechaza_origen_desconocido(cliente):
    respuesta = cliente.get("/", headers={"Origin": "https://sitio-extrano.example"})
    assert "access-control-allow-origin" not in respuesta.headers


def test_dsn_oracle(monkeypatch):
    monkeypatch.delenv("DB_DSN", raising=False)
    monkeypatch.setenv("DB_HOST", "adb.us-ashburn-1.oraclecloud.com")
    monkeypatch.setenv("DB_SERVICE", "mi_servicio_high.adb.oraclecloud.com")
    monkeypatch.delenv("DB_PORT", raising=False)
    assert conexion.dsn_oracle() == "tcps://adb.us-ashburn-1.oraclecloud.com:1522/mi_servicio_high.adb.oraclecloud.com"

    monkeypatch.setenv("DB_DSN", "(description=(address=(protocol=tcps)))")
    assert conexion.dsn_oracle() == "(description=(address=(protocol=tcps)))"

    monkeypatch.delenv("DB_DSN")
    monkeypatch.delenv("DB_HOST")
    with pytest.raises(RuntimeError):
        conexion.dsn_oracle()
