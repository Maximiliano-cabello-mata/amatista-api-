"""Sentry en el backend: apagado sin DSN y sin datos personales con DSN."""
import observabilidad


def test_sin_dsn_no_se_activa(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert observabilidad.iniciar("0.3.0") is False


def test_el_evento_no_lleva_datos_personales():
    evento = {
        "user": {"ip_address": "1.2.3.4", "email": "alumno@x.cl"},
        "request": {
            "url": "https://api.amatista-3d.tech/api/progreso",
            "cookies": {"sesion": "abc"},
            "data": {"contrasena": "secreta"},
            "query_string": "correo=alumno@x.cl",
            "env": {"REMOTE_ADDR": "1.2.3.4"},
            "headers": {"Authorization": "Bearer t", "X-Sesion-Id": "s", "Cookie": "c", "X-Forwarded-For": "1.2.3.4",
                        "User-Agent": "Firefox"},
        },
    }
    limpio = observabilidad._limpiar(evento)
    assert "user" not in limpio
    peticion = limpio["request"]
    assert {"cookies", "data", "query_string"}.isdisjoint(peticion)
    assert peticion["env"] == {}
    assert peticion["headers"] == {"User-Agent": "Firefox"}
    assert peticion["url"].endswith("/api/progreso")


def test_con_dsn_se_activa_sin_pii(monkeypatch):
    import sentry_sdk

    visto = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **opciones: visto.update(opciones))
    monkeypatch.setenv("SENTRY_DSN", "https://clave@o0.ingest.sentry.io/0")
    monkeypatch.setenv("SENTRY_TRAZAS", "7")
    assert observabilidad.iniciar("0.3.0") is True
    assert visto["send_default_pii"] is False
    assert visto["traces_sample_rate"] == 1.0
    assert visto["release"] == "amatista-api@0.3.0"
    assert visto["before_send"] is observabilidad._limpiar


def test_las_migas_del_log_de_acceso_no_se_envian():
    acceso = {"category": "uvicorn.access", "message": '2806:2f0::1:0 - "GET /api/addon/v1/enlace HTTP/1.1" 500'}
    assert observabilidad._miga(acceso) is None
    consulta = {"category": "query", "message": "SELECT practicas.id FROM practicas"}
    assert observabilidad._miga(consulta) is consulta
