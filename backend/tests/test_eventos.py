"""/api/eventos: deduplicación por id, validación, fechas, es_prueba e identidad."""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import conexion
from database.modelos import EventoAprendizaje, Usuario, ahora

ANONIMO = "alumno-0a0b0c0d-0000-4000-8000-000000000002"


def evento(tipo="lesson_completed", **campos):
    return {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "curso_id": "blender",
        "leccion_id": "les_001",
        "ocurrido_en": "2026-10-01T15:00:00Z",
        **campos,
    }


def enviar(cliente, eventos, usuario_id=ANONIMO, cabeceras=None):
    cuerpo = {"eventos": eventos}
    if usuario_id is not None:
        cuerpo["usuario_id"] = usuario_id
    return cliente.post("/api/eventos", json=cuerpo, headers=cabeceras or {})


def leer(evento_id):
    with Session(conexion.motor()) as db:
        return db.get(EventoAprendizaje, evento_id)


def contar(*condiciones):
    with Session(conexion.motor()) as db:
        return db.scalar(select(func.count()).select_from(EventoAprendizaje).where(*condiciones))


def test_anonimo_registra_eventos(cliente):
    datos = evento(
        "activity_submitted",
        sesion_aprendizaje=str(uuid.uuid4()),
        version_app="2.2.0",
        datos={"b": "quiz_1", "c": 1},
    )
    respuesta = enviar(cliente, [datos])
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"recibidos": 1, "nuevos": 1}

    guardado = leer(datos["id"])
    assert guardado.usuario_id == ANONIMO
    assert guardado.tipo == "activity_submitted"
    assert (guardado.curso_id, guardado.leccion_id) == ("blender", "les_001")
    assert guardado.sesion_aprendizaje == datos["sesion_aprendizaje"]
    assert guardado.version_app == "2.2.0"
    assert guardado.datos == '{"b":"quiz_1","c":1}'
    assert guardado.ocurrido_en == datetime(2026, 10, 1, 15, 0)  # UTC sin zona
    assert guardado.recibido_en is not None
    assert guardado.es_prueba == 0
    with Session(conexion.motor()) as db:
        assert db.get(Usuario, ANONIMO) is not None  # el anónimo se crea si no existía


def test_reenvio_no_cuenta_dos_veces(cliente):
    a, b = evento(), evento("learning_session_started")
    assert enviar(cliente, [a, b]).json() == {"recibidos": 2, "nuevos": 2}
    # La PWA no recibió la respuesta y reenvía la cola completa, más uno nuevo.
    c = evento("activity_submitted")
    assert enviar(cliente, [a, b, c]).json() == {"recibidos": 3, "nuevos": 1}
    assert enviar(cliente, [a, b, c]).json() == {"recibidos": 3, "nuevos": 0}
    assert contar() == 3


def test_duplicados_dentro_del_mismo_lote(cliente):
    a = evento()
    copia = {**a, "tipo": "activity_submitted"}  # mismo id: cuenta el primero
    assert enviar(cliente, [a, a, copia]).json() == {"recibidos": 3, "nuevos": 1}
    assert contar() == 1
    assert leer(a["id"]).tipo == "lesson_completed"


def test_tipo_invalido_responde_422(cliente):
    respuesta = enviar(cliente, [evento("pagina_vista")])
    assert respuesta.status_code == 422
    assert "Tipo de evento desconocido" in respuesta.text
    assert contar() == 0
    # Un tipo inválido rechaza el lote completo (nada a medias).
    assert enviar(cliente, [evento(), evento("otro")]).status_code == 422
    assert contar() == 0


def test_todos_los_tipos_validos(cliente):
    tipos = ["account_created", "learning_session_started", "lesson_completed", "activity_submitted", "sync_succeeded"]
    assert enviar(cliente, [evento(t) for t in tipos]).json()["nuevos"] == 5


def test_valida_tamanos(cliente):
    assert enviar(cliente, []).status_code == 422
    assert enviar(cliente, [evento() for _ in range(201)]).status_code == 422
    assert enviar(cliente, [evento() for _ in range(200)]).json()["nuevos"] == 200
    assert enviar(cliente, [evento(id="x" * 37)]).status_code == 422
    assert enviar(cliente, [evento(sesion_aprendizaje="x" * 37)]).status_code == 422
    assert enviar(cliente, [evento(version_app="x" * 21)]).status_code == 422
    assert enviar(cliente, [{"tipo": "lesson_completed"}]).status_code == 422  # falta id


def test_fechas_futuras_usan_la_hora_del_servidor(cliente):
    momento = ahora()
    lejano = evento(ocurrido_en=(momento + timedelta(days=2)).isoformat())
    cercano = evento(ocurrido_en=(momento + timedelta(minutes=2)).isoformat())
    pasado = evento(ocurrido_en=(momento - timedelta(days=10)).isoformat())
    sin_fecha = evento()
    del sin_fecha["ocurrido_en"]
    enviar(cliente, [lejano, cercano, pasado, sin_fecha])

    assert momento <= leer(lejano["id"]).ocurrido_en <= ahora()
    assert leer(cercano["id"]).ocurrido_en > ahora()  # dentro de la tolerancia de 5 minutos
    # La actividad offline conserva su fecha (plan, P10).
    assert leer(pasado["id"]).ocurrido_en == datetime.fromisoformat(pasado["ocurrido_en"])
    assert momento <= leer(sin_fecha["id"]).ocurrido_en <= ahora()


def test_datos_se_serializan_y_respetan_250(cliente):
    texto, objeto, largo, largo_texto, lista = (
        evento(datos="texto libre"),
        evento(datos={"a": [1, 2], "b": None}),
        evento(datos={"x": "a" * 300}),
        evento(datos="b" * 251),
        evento(datos=[1, 2]),
    )
    respuesta = enviar(cliente, [texto, objeto, largo, largo_texto, lista])
    assert respuesta.status_code == 200
    assert leer(texto["id"]).datos == "texto libre"
    assert leer(objeto["id"]).datos == '{"a":[1,2],"b":null}'
    assert leer(largo["id"]).datos is None  # no cabe: se ignora el campo, no el evento
    assert leer(largo_texto["id"]).datos is None
    assert leer(lista["id"]).datos is None


def test_es_prueba_se_copia_del_usuario(cliente, crear_cuenta):
    _, prueba = crear_cuenta(es_prueba=1)
    _, real = crear_cuenta()
    a, b = evento(), evento()
    enviar(cliente, [a], usuario_id=None, cabeceras=prueba)
    enviar(cliente, [b], usuario_id=None, cabeceras=real)
    assert leer(a["id"]).es_prueba == 1
    assert leer(b["id"]).es_prueba == 0


def test_identidad_de_los_eventos(cliente, crear_cuenta):
    a_id, a = crear_cuenta()
    b_id, _ = crear_cuenta()
    # Con token se guardan en la cuenta de la sesión, aunque el cuerpo diga otra.
    primero = evento()
    assert enviar(cliente, [primero], usuario_id=b_id, cabeceras=a).status_code == 200
    assert leer(primero["id"]).usuario_id == a_id
    # Sin token no se puede escribir como una cuenta registrada.
    segundo = evento()
    assert enviar(cliente, [segundo], usuario_id=b_id).status_code == 401
    assert leer(segundo["id"]) is None
    # Sin token ni usuario_id.
    assert enviar(cliente, [evento()], usuario_id=None).status_code == 422
    assert contar(EventoAprendizaje.usuario_id == b_id) == 0
