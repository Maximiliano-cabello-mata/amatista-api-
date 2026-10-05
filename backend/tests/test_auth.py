"""Cuentas: registro, sesiones, códigos, bloqueo, fusión del anónimo y límites."""
import logging
import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api import auth, correo, limites
from api.fusion import fusionar_alumno
from database import conexion
from database.modelos import EventoAprendizaje, Logro, ProgresoLeccion, Sesion, Usuario, ahora
from seguridad import hash_token

PASSWORD = "clave-segura-123"
CREDENCIALES = "Correo o contraseña incorrectos."


# --- Ayudantes ----------------------------------------------------------------


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def registrar(cliente, **datos):
    cuerpo = {"nombre": "Ana López", "email": "ana@amatista.local", "password": PASSWORD, **datos}
    return cliente.post("/api/auth/registro", json=cuerpo)


def entrar(cliente, email="ana@amatista.local", password=PASSWORD, **datos):
    return cliente.post("/api/auth/iniciar-sesion", json={"email": email, "password": password, **datos})


def confirmar(cliente, codigo, email="ana@amatista.local"):
    return cliente.post("/api/auth/confirmar-correo", json={"email": email, "codigo": codigo})


def escribir_anonimo(cliente, usuario_id, leccion_id):
    """POST /api/progreso sin sesión, como lo hace la PWA de un alumno anónimo."""
    evento = {"curso_id": "blender", "leccion_id": leccion_id, "completada": True}
    return cliente.post("/api/progreso", json={"usuario_id": usuario_id, "eventos": [evento]})


def leccion(leccion_id, completada, puntaje=None, intentos=0, datos=None, completada_en=None, actualizado_en=None):
    return {
        "leccion_id": leccion_id,
        "completada": completada,
        "puntaje": puntaje,
        "intentos": intentos,
        "datos_ligeros": datos,
        "completada_en": completada_en,
        "actualizado_en": actualizado_en or ahora(),
    }


def leer(modelo, llave):
    # Al cerrar la sesión el objeto queda desligado con sus columnas ya cargadas.
    with Session(conexion.motor()) as db:
        return db.get(modelo, llave)


def modificar_usuario(usuario_id, **campos):
    with Session(conexion.motor()) as db:
        usuario = db.get(Usuario, usuario_id)
        for campo, valor in campos.items():
            setattr(usuario, campo, valor)
        db.commit()


def contar(modelo, *condiciones):
    with Session(conexion.motor()) as db:
        return db.scalar(select(func.count()).select_from(modelo).where(*condiciones))


def sembrar(usuario_id, lecciones=(), logros=(), eventos=0, **campos):
    """Crea (si falta) el usuario y le agrega progreso, logros y eventos directo en la base."""
    with Session(conexion.motor()) as db:
        if db.get(Usuario, usuario_id) is None:
            db.add(Usuario(id=usuario_id, **campos))
            db.flush()
        for leccion in lecciones:
            db.add(ProgresoLeccion(usuario_id=usuario_id, **{"curso_id": "blender", **leccion}))
        for insignia in logros:
            db.add(Logro(usuario_id=usuario_id, insignia_id=insignia, obtenido_en=ahora()))
        for i in range(eventos):
            db.add(
                EventoAprendizaje(
                    id=f"{usuario_id[-20:]}-evento-{i}",
                    usuario_id=usuario_id,
                    tipo="lesson_completed",
                    ocurrido_en=ahora(),
                )
            )
        db.commit()


def progreso_de(usuario_id):
    with Session(conexion.motor()) as db:
        filas = db.scalars(select(ProgresoLeccion).where(ProgresoLeccion.usuario_id == usuario_id)).all()
    return {f.leccion_id: f for f in filas}


@pytest.fixture()
def modo_dev(monkeypatch):
    monkeypatch.setenv("AMATISTA_MOSTRAR_CODIGOS", "1")


# --- Registro -----------------------------------------------------------------


def test_registro_crea_cuenta_con_sesion(cliente):
    respuesta = registrar(
        cliente, email="  Ana@Amatista.LOCAL ", telefono="+52 55 1234 5678", dispositivo="x" * 500
    )
    assert respuesta.status_code == 201, respuesta.text
    datos = respuesta.json()
    usuario = datos["usuario"]
    assert usuario["id"].startswith("usr-")
    assert usuario["email"] == "ana@amatista.local"
    assert usuario["nombre"] == "Ana López"
    assert usuario["telefono"] == "+52 55 1234 5678"
    assert usuario["rol"] == "alumno"
    assert usuario["correo_confirmado"] is False
    assert usuario["es_prueba"] is False
    assert usuario["creado_en"]
    assert datos["fusion"] == {"lecciones": 0}
    assert "codigo_dev" not in datos
    assert set(usuario) == {"id", "nombre", "email", "telefono", "rol", "correo_confirmado", "es_prueba", "creado_en"}

    # En la base: contraseña y código como hash, sesión con el SHA-256 del token.
    fila = leer(Usuario, usuario["id"])
    assert fila.password_hash.startswith("pbkdf2_sha256$")
    assert PASSWORD not in fila.password_hash
    assert len(fila.codigo_verificacion) == 64 and fila.codigo_proposito == "correo"
    assert fila.codigo_expira > ahora()
    sesion = leer(Sesion, hash_token(datos["token"]))
    assert sesion.usuario_id == usuario["id"] and sesion.activa == 1
    assert len(sesion.dispositivo) == 200
    assert timedelta(days=29) < sesion.expira_en - ahora() <= timedelta(days=30)
    creada = (EventoAprendizaje.usuario_id == usuario["id"], EventoAprendizaje.tipo == "account_created")
    assert contar(EventoAprendizaje, *creada) == 1

    yo = cliente.get("/api/auth/yo", headers=bearer(datos["token"]))
    assert yo.status_code == 200 and yo.json() == usuario


def test_registro_correo_duplicado(cliente):
    assert registrar(cliente).status_code == 201
    respuesta = registrar(cliente, email="ANA@amatista.local", nombre="Otra")
    assert respuesta.status_code == 409
    assert "Ya existe una cuenta" in respuesta.json()["detail"]
    assert contar(Usuario) == 1


@pytest.mark.parametrize(
    "cambios, mensaje",
    [
        ({"email": "sin-arroba"}, "correo válido"),
        ({"email": "ana@dominio"}, "correo válido"),
        ({"email": "ana @x.com"}, "correo válido"),
        ({"email": "a" * 95 + "@x.com"}, "correo válido"),
        ({"password": "corta1"}, "entre 8 y 128"),
        ({"password": "a1" * 65}, "entre 8 y 128"),
        ({"password": "solo-letras"}, "una letra y un número"),
        ({"password": "12345678"}, "una letra y un número"),
        ({"nombre": "   "}, "Escribe tu nombre"),
        ({"nombre": "n" * 151}, "150"),
        ({"telefono": "1" * 26}, "teléfono"),
        ({"telefono": "llámame"}, "teléfono"),
        ({"usuario_local_id": "a" * 101}, "100"),
    ],
)
def test_registro_valida_datos(cliente, cambios, mensaje):
    respuesta = registrar(cliente, **cambios)
    assert respuesta.status_code == 422
    assert mensaje in respuesta.json()["detail"][0]["msg"]
    assert contar(Usuario) == 0


def test_registro_requiere_campos(cliente):
    assert cliente.post("/api/auth/registro", json={"email": "ana@amatista.local"}).status_code == 422


def test_registro_convierte_al_anonimo_en_el_lugar(cliente):
    anonimo = "alumno-1b2c3d4e-0000-4000-8000-000000000001"
    sembrar(
        anonimo,
        lecciones=[
            {"leccion_id": "les_001", "completada": 1, "puntaje": 90, "intentos": 2},
            {"leccion_id": "les_002", "completada": 0, "intentos": 1},
        ],
        logros=["blender:mod_teoria_001"],
        eventos=2,
    )
    respuesta = registrar(cliente, usuario_local_id=anonimo)
    assert respuesta.status_code == 201, respuesta.text
    datos = respuesta.json()
    assert datos["usuario"]["id"] == anonimo  # la misma fila, sin copiar nada
    assert datos["fusion"] == {"lecciones": 2}
    assert contar(Usuario) == 1

    progreso = progreso_de(anonimo)
    assert progreso["les_001"].completada == 1 and progreso["les_001"].puntaje == 90
    assert contar(Logro, Logro.usuario_id == anonimo) == 1
    assert contar(EventoAprendizaje, EventoAprendizaje.usuario_id == anonimo) == 3  # 2 + account_created
    fila = leer(Usuario, anonimo)
    assert fila.email == "ana@amatista.local" and fila.password_hash and fila.fusionado_en is None

    # La fila ya es una cuenta: sin sesión no se puede escribir con su id.
    assert escribir_anonimo(cliente, anonimo, "les_003").status_code == 401


def test_registro_con_id_local_que_aun_no_existe(cliente):
    respuesta = registrar(cliente, usuario_local_id="alumno-nuevo-123")
    assert respuesta.status_code == 201
    assert respuesta.json()["usuario"]["id"] == "alumno-nuevo-123"
    assert respuesta.json()["fusion"] == {"lecciones": 0}


def test_registro_ignora_id_local_de_una_cuenta(cliente, crear_cuenta):
    otra_id, _ = crear_cuenta(email="beto@amatista.local")
    sembrar(otra_id, lecciones=[{"leccion_id": "les_001", "completada": 1}])
    respuesta = registrar(cliente, usuario_local_id=otra_id)
    assert respuesta.status_code == 201
    nueva = respuesta.json()["usuario"]
    assert nueva["id"] != otra_id and nueva["id"].startswith("usr-")
    assert respuesta.json()["fusion"] == {"lecciones": 0}
    assert leer(Usuario, otra_id).email == "beto@amatista.local"
    assert set(progreso_de(otra_id)) == {"les_001"}
    assert progreso_de(nueva["id"]) == {}


def test_registro_con_id_local_tipo_correo_fusiona_en_cuenta_nueva(cliente):
    # Versiones viejas usaban el correo como id: el id de la cuenta nunca es un correo.
    viejo = "alumno_prueba@amatista.local"
    sembrar(viejo, lecciones=[{"leccion_id": "les_001", "completada": 1, "puntaje": 70}])
    respuesta = registrar(cliente, usuario_local_id=viejo)
    assert respuesta.status_code == 201
    nueva = respuesta.json()["usuario"]["id"]
    assert nueva.startswith("usr-")
    assert respuesta.json()["fusion"] == {"lecciones": 1}
    assert progreso_de(nueva)["les_001"].puntaje == 70
    assert leer(Usuario, viejo).fusionado_en == nueva


# --- Inicio de sesión ---------------------------------------------------------


def test_iniciar_sesion_correcto(cliente):
    registro = registrar(cliente).json()
    respuesta = entrar(cliente, email="ANA@amatista.local", dispositivo="Firefox en Android")
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["token"] != registro["token"]
    assert datos["usuario"]["id"] == registro["usuario"]["id"]
    assert datos["fusion"] == {"lecciones": 0}
    assert leer(Sesion, hash_token(datos["token"])).dispositivo == "Firefox en Android"
    assert cliente.get("/api/auth/yo", headers=bearer(datos["token"])).status_code == 200
    # El token anterior sigue vigente: cada dispositivo tiene su sesión.
    assert cliente.get("/api/auth/yo", headers=bearer(registro["token"])).status_code == 200


def test_iniciar_sesion_no_revela_si_el_correo_existe(cliente):
    registrar(cliente)
    incorrecta = entrar(cliente, password="otra-clave-999")
    inexistente = entrar(cliente, email="nadie@amatista.local")
    assert incorrecta.status_code == inexistente.status_code == 401
    assert incorrecta.json() == inexistente.json() == {"detail": CREDENCIALES}


def test_iniciar_sesion_anonimo_sin_password_no_entra(cliente):
    sembrar("alumno-sin-cuenta", email="raro@amatista.local")
    assert entrar(cliente, email="raro@amatista.local").json() == {"detail": CREDENCIALES}


def test_bloqueo_tras_cinco_fallos(cliente):
    usuario_id = registrar(cliente).json()["usuario"]["id"]
    for _ in range(4):
        assert entrar(cliente, password="mala-clave-1").status_code == 401
    quinto = entrar(cliente, password="mala-clave-1")
    assert quinto.status_code == 423
    assert "15 minutos" in quinto.json()["detail"]
    # Bloqueada: ni con la contraseña correcta.
    correcta = entrar(cliente)
    assert correcta.status_code == 423
    assert re.search(r"en 1[45] minutos", correcta.json()["detail"])

    # Pasan los 15 minutos.
    modificar_usuario(usuario_id, bloqueado_hasta=ahora() - timedelta(seconds=1))
    assert entrar(cliente).status_code == 200
    fila = leer(Usuario, usuario_id)
    assert fila.intentos_fallidos == 0 and fila.bloqueado_hasta is None


def test_inicio_correcto_reinicia_los_fallos(cliente):
    registrar(cliente)
    for _ in range(4):
        entrar(cliente, password="mala-clave-1")
    assert entrar(cliente).status_code == 200
    for _ in range(4):
        assert entrar(cliente, password="mala-clave-1").status_code == 401  # vuelve a contar desde cero


def test_iniciar_sesion_actualiza_hash_viejo(cliente, monkeypatch):
    usuario_id = registrar(cliente).json()["usuario"]["id"]
    assert leer(Usuario, usuario_id).password_hash.split("$")[1] == "1000"
    monkeypatch.setenv("AMATISTA_PBKDF2_ITER", "2000")
    assert entrar(cliente).status_code == 200
    assert leer(Usuario, usuario_id).password_hash.split("$")[1] == "2000"
    assert entrar(cliente).status_code == 200


def test_token_invalido_o_ausente(cliente):
    token = registrar(cliente).json()["token"]
    assert cliente.get("/api/auth/yo").status_code == 401
    assert cliente.get("/api/auth/yo", headers=bearer("token-inventado")).status_code == 401
    assert cliente.get("/api/auth/yo", headers=bearer("x" * 300)).status_code == 401
    assert cliente.get("/api/auth/yo", headers={"X-Sesion-Id": token}).status_code == 200


def test_sesion_vencida(cliente):
    token = registrar(cliente).json()["token"]
    with Session(conexion.motor()) as db:
        db.get(Sesion, hash_token(token)).expira_en = ahora() - timedelta(minutes=1)
        db.commit()
    assert cliente.get("/api/auth/yo", headers=bearer(token)).status_code == 401


# --- Perfil y cierre de sesión -------------------------------------------------


def test_yo_y_actualizar_perfil(cliente):
    token = registrar(cliente, telefono="5512345678").json()["token"]
    respuesta = cliente.patch(
        "/api/auth/yo",
        headers=bearer(token),
        json={"nombre": "  Ana   María ", "rol": "admin", "email": "otro@x.com"},
    )
    assert respuesta.status_code == 200
    usuario = respuesta.json()
    assert usuario["nombre"] == "Ana María"
    assert usuario["telefono"] == "5512345678"  # no se mandó: no cambia
    assert usuario["rol"] == "alumno" and usuario["email"] == "ana@amatista.local"  # no se pueden cambiar aquí

    assert cliente.patch("/api/auth/yo", headers=bearer(token), json={"telefono": "+52 (55) 8765-4321"}).json()[
        "telefono"
    ] == "+52 (55) 8765-4321"
    assert cliente.patch("/api/auth/yo", headers=bearer(token), json={"telefono": ""}).json()["telefono"] is None
    assert cliente.patch("/api/auth/yo", headers=bearer(token), json={"nombre": ""}).status_code == 422
    assert cliente.patch("/api/auth/yo", headers=bearer(token), json={"telefono": "abc"}).status_code == 422
    assert cliente.patch("/api/auth/yo", json={"nombre": "Sin sesión"}).status_code == 401
    assert cliente.get("/api/auth/yo", headers=bearer(token)).json()["nombre"] == "Ana María"


def test_cerrar_sesion_invalida_el_token(cliente):
    token = registrar(cliente).json()["token"]
    otro = entrar(cliente).json()["token"]
    respuesta = cliente.post("/api/auth/cerrar-sesion", headers=bearer(token))
    assert respuesta.status_code == 200 and respuesta.json() == {"ok": True}
    assert leer(Sesion, hash_token(token)).activa == 0
    assert cliente.get("/api/auth/yo", headers=bearer(token)).status_code == 401
    assert cliente.post("/api/auth/cerrar-sesion", headers=bearer(token)).status_code == 401
    assert cliente.get("/api/auth/yo", headers=bearer(otro)).status_code == 200  # las demás siguen


def test_cerrar_todas(cliente, crear_cuenta):
    tokens = [registrar(cliente).json()["token"], entrar(cliente).json()["token"], entrar(cliente).json()["token"]]
    _, ajena = crear_cuenta()
    respuesta = cliente.post("/api/auth/cerrar-todas", headers=bearer(tokens[1]))
    assert respuesta.status_code == 200 and respuesta.json() == {"cerradas": 3}
    for token in tokens:
        assert cliente.get("/api/auth/yo", headers=bearer(token)).status_code == 401
    assert cliente.get("/api/auth/yo", headers=ajena).status_code == 200  # otra cuenta no se toca
    assert cliente.post("/api/auth/cerrar-todas").status_code == 401


# --- Confirmación de correo -----------------------------------------------------


def test_confirmar_correo_con_codigo_dev(cliente, modo_dev):
    datos = registrar(cliente).json()
    codigo = datos["codigo_dev"]
    assert re.fullmatch(r"\d{6}", codigo)
    usuario_id = datos["usuario"]["id"]
    assert codigo not in leer(Usuario, usuario_id).codigo_verificacion  # solo el hash

    respuesta = confirmar(cliente, f" {codigo} ", email="ANA@amatista.local")
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["ok"] is True
    assert respuesta.json()["usuario"]["correo_confirmado"] is True
    fila = leer(Usuario, usuario_id)
    assert fila.correo_confirmado == 1
    assert fila.codigo_verificacion is None and fila.codigo_proposito is None and fila.codigo_expira is None

    # Un código usado no sirve otra vez.
    assert confirmar(cliente, codigo).status_code == 400
    # Ya confirmado: reenviar no genera código nuevo.
    assert cliente.post("/api/auth/reenviar-codigo", json={"email": "ana@amatista.local"}).json() == {"ok": True}


def test_codigo_incorrecto_se_invalida_tras_cinco_intentos(cliente, modo_dev):
    codigo = registrar(cliente).json()["codigo_dev"]
    incorrecto = "000000" if codigo != "000000" else "111111"
    for _ in range(5):
        respuesta = confirmar(cliente, incorrecto)
        assert respuesta.status_code == 400
        assert respuesta.json()["detail"] == "El código no es válido o ya venció. Pide uno nuevo."
    # Agotó los intentos: ni el correcto sirve.
    assert confirmar(cliente, codigo).status_code == 400

    nuevo = cliente.post("/api/auth/reenviar-codigo", json={"email": "ana@amatista.local"}).json()["codigo_dev"]
    assert confirmar(cliente, nuevo).status_code == 200


def test_codigo_vencido(cliente, modo_dev):
    datos = registrar(cliente).json()
    modificar_usuario(datos["usuario"]["id"], codigo_expira=ahora() - timedelta(seconds=1))
    respuesta = confirmar(cliente, datos["codigo_dev"])
    assert respuesta.status_code == 400
    assert leer(Usuario, datos["usuario"]["id"]).correo_confirmado == 0


def test_codigo_de_otra_cuenta_o_formato_invalido(cliente, modo_dev):
    codigo = registrar(cliente).json()["codigo_dev"]
    registrar(cliente, email="beto@amatista.local")
    assert confirmar(cliente, codigo, "beto@amatista.local").status_code == 400
    assert confirmar(cliente, codigo, "nadie@amatista.local").status_code == 400
    assert confirmar(cliente, "12ab").status_code == 422


def test_reenviar_y_recuperar_no_revelan_cuentas(cliente):
    registrar(cliente)
    for ruta in ("/api/auth/reenviar-codigo", "/api/auth/recuperar"):
        existe = cliente.post(ruta, json={"email": "ana@amatista.local"})
        no_existe = cliente.post(ruta, json={"email": "nadie@amatista.local"})
        assert existe.status_code == no_existe.status_code == 200
        assert existe.json() == no_existe.json() == {"ok": True}  # sin codigo_dev fuera del modo desarrollo


def test_codigo_dev_solo_en_modo_desarrollo(cliente):
    assert "codigo_dev" not in registrar(cliente).json()
    assert "codigo_dev" not in cliente.post("/api/auth/recuperar", json={"email": "ana@amatista.local"}).json()


# --- Recuperación y cambio de contraseña ----------------------------------------


def test_recuperar_y_restablecer(cliente, modo_dev):
    registro = registrar(cliente).json()
    codigo_correo = registro["codigo_dev"]
    otro_token = entrar(cliente).json()["token"]

    codigo = cliente.post("/api/auth/recuperar", json={"email": "ana@amatista.local"}).json()["codigo_dev"]
    nueva = "otra-clave-456"
    base = {"email": "ana@amatista.local", "codigo": codigo}
    assert cliente.post("/api/auth/restablecer", json={**base, "password": "debil"}).status_code == 422
    # El código de confirmación de correo no sirve para restablecer.
    if codigo_correo != codigo:
        con_otro_codigo = {**base, "codigo": codigo_correo, "password": nueva}
        assert cliente.post("/api/auth/restablecer", json=con_otro_codigo).status_code == 400

    respuesta = cliente.post("/api/auth/restablecer", json={**base, "password": nueva})
    assert respuesta.status_code == 200 and respuesta.json() == {"ok": True}
    # Cierra todas las sesiones y confirma el correo.
    for token in (registro["token"], otro_token):
        assert cliente.get("/api/auth/yo", headers=bearer(token)).status_code == 401
    assert entrar(cliente).status_code == 401
    datos = entrar(cliente, password=nueva).json()
    assert datos["usuario"]["correo_confirmado"] is True
    # El código ya se usó.
    assert cliente.post("/api/auth/restablecer", json={**base, "password": "tercera-clave-789"}).status_code == 400


def test_restablecer_desbloquea_la_cuenta(cliente, modo_dev):
    registrar(cliente)
    for _ in range(5):
        entrar(cliente, password="mala-clave-1")
    assert entrar(cliente).status_code == 423
    codigo = cliente.post("/api/auth/recuperar", json={"email": "ana@amatista.local"}).json()["codigo_dev"]
    datos = {"email": "ana@amatista.local", "codigo": codigo, "password": "nueva-clave-1"}
    assert cliente.post("/api/auth/restablecer", json=datos).status_code == 200
    assert entrar(cliente, password="nueva-clave-1").status_code == 200


def test_cambiar_password(cliente):
    actual = registrar(cliente).json()["token"]
    otro = entrar(cliente).json()["token"]
    ruta = "/api/auth/cambiar-password"
    assert cliente.post(ruta, json={"actual": PASSWORD, "nueva": "nueva-clave-1"}).status_code == 401
    mala = cliente.post(ruta, headers=bearer(actual), json={"actual": "no-es-la-clave1", "nueva": "nueva-clave-1"})
    assert mala.status_code == 400 and mala.json()["detail"] == "La contraseña actual no es correcta."
    for nueva, estado in (("debil", 422), (PASSWORD, 400)):
        assert cliente.post(ruta, headers=bearer(actual), json={"actual": PASSWORD, "nueva": nueva}).status_code == estado

    respuesta = cliente.post(ruta, headers=bearer(actual), json={"actual": PASSWORD, "nueva": "nueva-clave-1"})
    assert respuesta.status_code == 200 and respuesta.json() == {"ok": True}
    assert cliente.get("/api/auth/yo", headers=bearer(actual)).status_code == 200  # la sesión actual sigue
    assert cliente.get("/api/auth/yo", headers=bearer(otro)).status_code == 401  # las demás se cierran
    assert entrar(cliente).status_code == 401
    assert entrar(cliente, password="nueva-clave-1").status_code == 200


# --- Roles y configuración --------------------------------------------------------


def test_admins_por_variable_de_entorno(cliente, crear_cuenta, monkeypatch):
    monkeypatch.setenv("AMATISTA_ADMINS", " Jefa@Amatista.local , coordinador@amatista.local")
    assert registrar(cliente, email="jefa@amatista.local").json()["usuario"]["rol"] == "admin"
    assert registrar(cliente, email="alumna@amatista.local").json()["usuario"]["rol"] == "alumno"

    crear_cuenta(email="coordinador@amatista.local", password=PASSWORD)
    assert entrar(cliente, email="coordinador@amatista.local").json()["usuario"]["rol"] == "admin"


def test_registro_no_hereda_rol_de_una_fila_anonima(cliente):
    sembrar("alumno-con-rol", rol="admin")
    assert registrar(cliente, usuario_local_id="alumno-con-rol").json()["usuario"]["rol"] == "alumno"


def test_requiere_confirmacion(cliente, modo_dev, monkeypatch):
    monkeypatch.setenv("AMATISTA_REQUIERE_CONFIRMACION", "1")
    datos = registrar(cliente).json()
    assert datos["token"] is None and datos["requiere_confirmacion"] is True
    assert contar(Sesion) == 0
    sin_confirmar = entrar(cliente)
    assert sin_confirmar.status_code == 403 and "Confirma tu correo" in sin_confirmar.json()["detail"]
    assert entrar(cliente, password="mala-clave-1").status_code == 401  # primero se valida la contraseña

    confirmar(cliente, datos["codigo_dev"])
    assert entrar(cliente).status_code == 200


def test_no_registra_contraseñas_ni_tokens(cliente, caplog, modo_dev):
    caplog.set_level(logging.DEBUG)
    token = registrar(cliente).json()["token"]
    entrar(cliente, password="mala-clave-1")
    otro = entrar(cliente).json()["token"]
    texto = caplog.text
    for secreto in (PASSWORD, "mala-clave-1", token, otro):
        assert secreto not in texto


# --- Fusión del anónimo al iniciar sesión ----------------------------------------


def test_fusion_al_iniciar_sesion_desde_otro_dispositivo(cliente):
    cuenta = registrar(cliente).json()["usuario"]["id"]
    t1, t2, t3 = datetime(2026, 9, 1), datetime(2026, 9, 10), datetime(2026, 9, 20)
    sembrar(
        cuenta,
        lecciones=[
            leccion("les_001", 1, puntaje=50, intentos=1, datos='{"a":1}', completada_en=t2, actualizado_en=t2),
            leccion("les_002", 0, puntaje=80, intentos=4, actualizado_en=t1),
            leccion("les_004", 1, intentos=1, completada_en=t1, actualizado_en=t1),
        ],
        logros=["aframe:mod_af_001"],
    )
    anonimo = "alumno-otro-dispositivo"
    sembrar(
        anonimo,
        lecciones=[
            leccion("les_001", 0, puntaje=90, intentos=3, datos='{"a":5}', actualizado_en=t3),
            leccion("les_002", 1, puntaje=70, intentos=2, datos='{"p":2}', completada_en=t1, actualizado_en=t1),
            leccion("les_003", 1, puntaje=100, intentos=1, completada_en=t3, actualizado_en=t3),
        ],
        logros=["blender:mod_teoria_001", "aframe:mod_af_001"],
        eventos=3,
    )

    respuesta = entrar(cliente, usuario_local_id=anonimo)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["fusion"] == {"lecciones": 3}

    progreso = progreso_de(cuenta)
    assert set(progreso) == {"les_001", "les_002", "les_003", "les_004"}
    l1, l2, l3, l4 = (progreso[k] for k in ("les_001", "les_002", "les_003", "les_004"))
    assert (l1.completada, l1.puntaje, l1.intentos, l1.completada_en) == (1, 90, 3, t2)  # nunca retrocede
    assert l1.datos_ligeros == '{"a":1}'  # se conserva el de la cuenta
    assert l1.actualizado_en == t3
    assert (l2.completada, l2.puntaje, l2.intentos, l2.completada_en) == (1, 80, 4, t1)
    assert l2.datos_ligeros == '{"p":2}'  # la cuenta no tenía: se toma el del anónimo
    assert (l3.completada, l3.puntaje, l3.completada_en) == (1, 100, t3)
    assert (l4.completada, l4.completada_en) == (1, t1)

    with Session(conexion.motor()) as db:
        insignias = set(db.scalars(select(Logro.insignia_id).where(Logro.usuario_id == cuenta)).all())
    assert insignias == {"aframe:mod_af_001", "blender:mod_teoria_001"}
    assert contar(EventoAprendizaje, EventoAprendizaje.usuario_id == cuenta) == 4  # 3 + account_created
    assert contar(EventoAprendizaje, EventoAprendizaje.usuario_id == anonimo) == 0
    # La fila anónima queda marcada y sin filas duplicadas.
    assert leer(Usuario, anonimo).fusionado_en == cuenta
    assert progreso_de(anonimo) == {}
    assert contar(Logro, Logro.usuario_id == anonimo) == 0

    # Repetir el inicio con el mismo id local no vuelve a fusionar.
    assert entrar(cliente, usuario_local_id=anonimo).json()["fusion"] == {"lecciones": 0}

    # Una PWA que siga escribiendo como anónimo con ese id ya no puede.
    assert escribir_anonimo(cliente, anonimo, "les_005").status_code == 401
    assert cliente.post("/api/iniciar-sesion", json={"usuario_id": anonimo}).status_code == 409


def test_fusion_no_toma_progreso_de_otra_cuenta(cliente, crear_cuenta):
    otra, _ = crear_cuenta(email="beto@amatista.local")
    sembrar(otra, lecciones=[{"leccion_id": "les_001", "completada": 1}])
    cuenta = registrar(cliente).json()["usuario"]["id"]
    assert entrar(cliente, usuario_local_id=otra).json()["fusion"] == {"lecciones": 0}
    assert set(progreso_de(otra)) == {"les_001"}
    assert progreso_de(cuenta) == {}
    assert leer(Usuario, otra).fusionado_en is None


def test_fusion_casos_limite(cliente):
    cuenta = registrar(cliente).json()["usuario"]["id"]
    otra = registrar(cliente, email="beto@amatista.local").json()["usuario"]["id"]
    sembrar("alumno-a", lecciones=[{"leccion_id": "les_001", "completada": 1}])
    with Session(conexion.motor()) as db:
        assert fusionar_alumno(db, cuenta, cuenta) == 0
        assert fusionar_alumno(db, "", cuenta) == 0
        assert fusionar_alumno(db, "alumno-que-no-existe", cuenta) == 0
        assert fusionar_alumno(db, otra, cuenta) == 0  # una cuenta registrada nunca es origen
        assert fusionar_alumno(db, "alumno-a", "cuenta-que-no-existe") == 0
        assert fusionar_alumno(db, "alumno-a", cuenta) == 1
        db.commit()
    with Session(conexion.motor()) as db:
        # Ya fusionado: otra cuenta no puede tomarlo después.
        assert fusionar_alumno(db, "alumno-a", otra) == 0
    assert set(progreso_de(cuenta)) == {"les_001"}
    assert progreso_de(otra) == {}
    sembrar("alumno-vacio")
    assert entrar(cliente, usuario_local_id="alumno-vacio").json()["fusion"] == {"lecciones": 0}
    assert leer(Usuario, "alumno-vacio").fusionado_en == cuenta


# --- Aislamiento entre alumnos (plan de lanzamiento, P02) ----------------------------


def test_un_alumno_no_escribe_ni_lee_el_progreso_de_otro(cliente, crear_cuenta):
    a_id, a = crear_cuenta(email="a@amatista.local")
    b_id, b = crear_cuenta(email="b@amatista.local")
    evento = {"curso_id": "blender", "leccion_id": "les_001", "completada": True, "puntaje": 100}
    respuesta = cliente.post("/api/progreso", headers=a, json={"usuario_id": b_id, "eventos": [evento]})
    assert respuesta.status_code == 200, respuesta.text
    assert set(progreso_de(a_id)) == {"les_001"}  # se guardó en A
    assert progreso_de(b_id) == {}
    # Sin sesión tampoco se puede escribir como B.
    assert cliente.post("/api/progreso", json={"usuario_id": b_id, "eventos": [evento]}).status_code == 401
    # Ni leer su progreso.
    assert cliente.get(f"/api/progreso/{b_id}", headers=a).status_code == 403
    assert cliente.get(f"/api/progreso/{b_id}").status_code == 401
    assert cliente.get(f"/api/progreso/{a_id}", headers=a).status_code == 200


# --- Límite por IP ---------------------------------------------------------------------


@pytest.fixture()
def con_limites(monkeypatch):
    monkeypatch.delenv("AMATISTA_SIN_LIMITES", raising=False)
    limites.reiniciar_limites()
    yield monkeypatch
    limites.reiniciar_limites()


def test_limite_por_ip_responde_429(cliente, con_limites):
    for _ in range(10):
        assert cliente.post("/api/auth/recuperar", json={"email": "ana@amatista.local"}).status_code == 200
    bloqueada = cliente.post("/api/auth/recuperar", json={"email": "ana@amatista.local"})
    assert bloqueada.status_code == 429
    assert bloqueada.json()["detail"] == limites.MENSAJE
    assert 1 <= int(bloqueada.headers["retry-after"]) <= 60
    # Otras rutas cuentan por separado.
    assert cliente.post("/api/auth/reenviar-codigo", json={"email": "ana@amatista.local"}).status_code == 200
    # Las peticiones inválidas también cuentan (no se puede esquivar con 422).
    for _ in range(auth.LIMITE_INICIO_MAXIMO):
        cliente.post("/api/auth/iniciar-sesion", json={})
    assert cliente.post("/api/auth/iniciar-sesion", json={}).status_code == 429


def test_limite_desactivado_por_variable(cliente, con_limites):
    con_limites.setenv("AMATISTA_SIN_LIMITES", "1")
    for _ in range(15):
        assert cliente.post("/api/auth/recuperar", json={"email": "ana@amatista.local"}).status_code == 200


def test_limite_respeta_x_forwarded_for_solo_con_proxy_confiable(cliente, con_limites):
    def recuperar(ip):
        cabeceras = {"X-Forwarded-For": ip}
        return cliente.post("/api/auth/recuperar", json={"email": "x@amatista.local"}, headers=cabeceras)

    # Sin proxy confiable la cabecera se ignora: inventarla no sirve para saltarse el límite.
    for i in range(10):
        assert recuperar(f"10.0.0.{i}").status_code == 200
    assert recuperar("10.0.1.1").status_code == 429

    limites.reiniciar_limites()
    con_limites.setenv("AMATISTA_PROXY_CONFIABLE", "1")
    for _ in range(10):
        assert recuperar("spoof, 203.0.113.7").status_code == 200
    assert recuperar("otra, 203.0.113.7").status_code == 429  # cuenta la IP que agregó el proxy
    assert recuperar("203.0.113.8").status_code == 200


def test_limitador_ventana_deslizante(monkeypatch):
    reloj = [1000.0]
    monkeypatch.setattr(limites.time, "monotonic", lambda: reloj[0])
    limitador = limites.Limitador(2, ventana=60)
    assert limitador.intentar("ip")[0] and limitador.intentar("ip")[0]
    permitida, espera = limitador.intentar("ip")
    assert not permitida and espera == 60
    assert limitador.intentar("otra-ip")[0]
    reloj[0] += 30
    assert limitador.intentar("ip") == (False, 30)
    reloj[0] += 31
    assert limitador.intentar("ip")[0]
    # La limpieza periódica borra las claves sin peticiones recientes.
    reloj[0] += 200
    limitador.intentar("nueva")
    assert set(limitador._registros) == {"nueva"}


# --- Correo ----------------------------------------------------------------------------


class SMTPFalso:
    enviados = []

    def __init__(self, host, puerto, timeout=None, context=None):
        self.host, self.puerto, self.timeout, self.contexto = host, puerto, timeout, context
        self.pasos = []
        SMTPFalso.enviados.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.pasos.append("quit")

    def starttls(self, context=None):
        self.pasos.append("starttls")

    def login(self, usuario, password):
        self.pasos.append(("login", usuario, password))

    def send_message(self, mensaje):
        self.mensaje = mensaje
        self.pasos.append("send")


def test_enviar_codigo_por_smtp_con_starttls(monkeypatch):
    SMTPFalso.enviados = []
    monkeypatch.setattr(correo.smtplib, "SMTP", SMTPFalso)
    monkeypatch.setenv("SMTP_HOST", "smtp.ejemplo.com")
    monkeypatch.setenv("SMTP_USER", "amatista@ejemplo.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secreto")
    monkeypatch.setenv("SMTP_FROM", "Amatista <no-responder@ejemplo.com>")
    monkeypatch.delenv("SMTP_PORT", raising=False)

    assert correo.enviar_codigo("ana@amatista.local", "123456", "password") is True
    smtp = SMTPFalso.enviados[0]
    assert (smtp.host, smtp.puerto, smtp.timeout) == ("smtp.ejemplo.com", 587, 10)
    assert smtp.pasos == ["starttls", ("login", "amatista@ejemplo.com", "secreto"), "send", "quit"]
    assert smtp.mensaje["To"] == "ana@amatista.local"
    assert smtp.mensaje["From"] == "Amatista <no-responder@ejemplo.com>"
    assert "recuperar" in smtp.mensaje["Subject"]
    assert "123456" in smtp.mensaje.get_content()


def test_enviar_codigo_por_puerto_465_usa_tls_directo(monkeypatch):
    SMTPFalso.enviados = []
    monkeypatch.setattr(correo.smtplib, "SMTP_SSL", SMTPFalso)
    monkeypatch.setenv("SMTP_HOST", "smtp.ejemplo.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)

    assert correo.enviar_codigo("ana@amatista.local", "123456", "correo") is True
    smtp = SMTPFalso.enviados[0]
    assert smtp.puerto == 465 and smtp.contexto is not None
    assert smtp.pasos == ["send", "quit"]  # sin STARTTLS ni login
    assert smtp.mensaje["From"] == "no-responder@smtp.ejemplo.com"
    assert "confirmar" in smtp.mensaje["Subject"]


def test_sin_smtp_avisa_sin_mostrar_el_codigo(caplog, monkeypatch):
    monkeypatch.delenv("AMATISTA_MOSTRAR_CODIGOS", raising=False)
    caplog.set_level(logging.INFO)
    assert correo.enviar_codigo("ana@amatista.local", "654321", "correo") is False
    assert "SMTP_HOST no está configurado" in caplog.text
    assert "654321" not in caplog.text
    assert "ana@amatista.local" not in caplog.text  # el correo va enmascarado

    monkeypatch.setenv("AMATISTA_MOSTRAR_CODIGOS", "1")
    correo.enviar_codigo("ana@amatista.local", "654321", "correo")
    assert "654321" in caplog.text


def test_fallo_smtp_no_tumba_el_registro(cliente, caplog, monkeypatch):
    def caido(*args, **kwargs):
        raise ConnectionRefusedError("conexión rechazada")

    monkeypatch.setattr(correo.smtplib, "SMTP", caido)
    monkeypatch.setenv("SMTP_HOST", "smtp.caido.local")
    caplog.set_level(logging.INFO)
    respuesta = registrar(cliente)
    assert respuesta.status_code == 201
    assert "No se pudo enviar el código" in caplog.text
    assert cliente.post("/api/auth/reenviar-codigo", json={"email": "ana@amatista.local"}).status_code == 200


# --- Herramienta crear_admin ------------------------------------------------------------


def test_crear_admin_promueve_cuenta_existente(cliente, capsys):
    from herramientas import crear_admin

    usuario_id = registrar(cliente).json()["usuario"]["id"]
    assert crear_admin.main(["ANA@amatista.local"]) == 0
    assert leer(Usuario, usuario_id).rol == "admin"
    assert "admin" in capsys.readouterr().out
    assert crear_admin.main(["ana@amatista.local", "--rol", "profesor"]) == 0
    assert leer(Usuario, usuario_id).rol == "profesor"


def test_crear_admin_sin_cuenta_y_con_crear(cliente, monkeypatch):
    from herramientas import crear_admin

    assert crear_admin.main(["jefa@amatista.local"]) == 1
    assert crear_admin.main(["no-es-correo"]) == 2

    respuestas = iter(["debil", "clave-admin-1", "otra-cosa-1", "clave-admin-1", "clave-admin-1"])
    monkeypatch.setattr(crear_admin.getpass, "getpass", lambda *_: next(respuestas))
    assert crear_admin.main(["jefa@amatista.local", "--crear", "--nombre", "Jefa"]) == 0
    datos = entrar(cliente, email="jefa@amatista.local", password="clave-admin-1").json()
    assert datos["usuario"]["rol"] == "admin"
    assert datos["usuario"]["nombre"] == "Jefa"
    assert datos["usuario"]["correo_confirmado"] is True
    assert datos["usuario"]["id"].startswith("usr-")
