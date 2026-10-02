"""/api/admin: métricas del plan de lanzamiento, usuarios, permisos y mantenimiento."""
import uuid
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import conexion
from database.modelos import (
    Curso,
    EventoAprendizaje,
    Leccion,
    Logro,
    Modulo,
    ProgresoLeccion,
    Sesion,
    Usuario,
    ahora,
)
from seguridad import hash_token, nuevo_token

ZONA = ZoneInfo("America/Mexico_City")


# --- Ayudantes ----------------------------------------------------------------


def crear_usuario(usuario_id, creado_en, registrado=True, confirmado=True, **campos):
    """Usuario directo en la base, con fecha de alta controlada."""
    with Session(conexion.motor()) as db:
        db.add(
            Usuario(
                id=usuario_id,
                nombre=campos.pop("nombre", usuario_id),
                email=campos.pop("email", f"{usuario_id}@amatista.local" if registrado else None),
                password_hash="pbkdf2_sha256$1000$sal$hash" if registrado else None,
                correo_confirmado=1 if registrado and confirmado else 0,
                creado_en=creado_en,
                **campos,
            )
        )
        db.commit()
    return usuario_id


def agregar_eventos(usuario_id, *eventos, es_prueba=0):
    """eventos: (tipo, ocurrido_en)."""
    with Session(conexion.motor()) as db:
        for tipo, ocurrido_en in eventos:
            db.add(
                EventoAprendizaje(
                    id=str(uuid.uuid4()),
                    usuario_id=usuario_id,
                    tipo=tipo,
                    curso_id="blender",
                    leccion_id="les_001",
                    ocurrido_en=ocurrido_en,
                    es_prueba=es_prueba,
                )
            )
        db.commit()


def agregar_progreso(usuario_id, curso_id, leccion_id, completada_en=None, puntaje=None):
    with Session(conexion.motor()) as db:
        db.add(
            ProgresoLeccion(
                usuario_id=usuario_id,
                curso_id=curso_id,
                leccion_id=leccion_id,
                completada=1 if completada_en else 0,
                puntaje=puntaje,
                intentos=1,
                completada_en=completada_en,
                actualizado_en=completada_en or ahora(),
            )
        )
        db.commit()


def agregar_sesion(usuario_id, activa=1, expira_en=None, ultimo_acceso=None):
    token = nuevo_token()
    with Session(conexion.motor()) as db:
        db.add(
            Sesion(
                id=hash_token(token),
                usuario_id=usuario_id,
                activa=activa,
                expira_en=expira_en or ahora() + timedelta(days=30),
                ultimo_acceso=ultimo_acceso or ahora(),
            )
        )
        db.commit()
    return {"Authorization": f"Bearer {token}"}


def leer(modelo, llave):
    with Session(conexion.motor()) as db:
        return db.get(modelo, llave)


def contar(modelo, *condiciones):
    with Session(conexion.motor()) as db:
        return db.scalar(select(func.count()).select_from(modelo).where(*condiciones))


def dia(fecha):
    return fecha.replace(tzinfo=timezone.utc).astimezone(ZONA).date().isoformat()


# --- Permisos -----------------------------------------------------------------


LECTURAS = ("/api/admin/resumen", "/api/admin/usuarios")


def test_permisos_de_lectura(cliente, crear_cuenta):
    _, alumno = crear_cuenta()
    _, profesor = crear_cuenta(rol="profesor")
    _, admin = crear_cuenta(rol="admin")
    for ruta in LECTURAS:
        assert cliente.get(ruta).status_code == 401, ruta
        assert cliente.get(ruta, headers=alumno).status_code == 403, ruta
        assert cliente.get(ruta, headers=profesor).status_code == 200, ruta
        assert cliente.get(ruta, headers=admin).status_code == 200, ruta


def test_permisos_de_escritura(cliente, crear_cuenta):
    alumno_id, alumno = crear_cuenta()
    _, profesor = crear_cuenta(rol="profesor")
    for cabeceras, estado in (({}, 401), (alumno, 403), (profesor, 403)):
        ruta = f"/api/admin/usuarios/{alumno_id}"
        assert cliente.patch(ruta, json={"rol": "admin"}, headers=cabeceras).status_code == estado
        assert cliente.post("/api/admin/mantenimiento/purgar", json={}, headers=cabeceras).status_code == estado
        assert cliente.get("/api/admin/salud-detallada", headers=cabeceras).status_code == estado
    assert leer(Usuario, alumno_id).rol == "alumno"


# --- Resumen: escenario con fechas controladas ----------------------------------


@pytest.fixture()
def escenario(cliente, crear_cuenta):
    """Alumnos reales, de prueba, personal y fusionados con actividad en días conocidos."""
    momento = ahora()

    def hace(dias, horas=0):
        return momento - timedelta(days=dias, hours=horas)

    _, profesor = crear_cuenta(rol="profesor")

    # A: activado hace 5 días (lección completada) y activo (3 días distintos).
    crear_usuario("alumno-a", hace(10))
    agregar_eventos("alumno-a", ("learning_session_started", hace(3)), ("lesson_completed", hace(1)))
    agregar_progreso("alumno-a", "blender", "les_001", completada_en=hace(5), puntaje=90)
    # B: registrado hace 2 días, todo en un solo día: activado pero no activo semanal.
    crear_usuario("alumno-b", hace(2))
    agregar_eventos("alumno-b", ("learning_session_started", hace(1)), ("lesson_completed", hace(1)))
    # C: dos días de sesiones sin completar nada: ni activado ni activo.
    crear_usuario("alumno-c", hace(40), confirmado=False)
    agregar_eventos("alumno-c", ("learning_session_started", hace(2)), ("learning_session_started", hace(4)))
    agregar_progreso("alumno-c", "aframe", "les_af_001")
    # D (cuenta de prueba), E (profesor) y F (anónimo fusionado): mismo patrón que A, no cuentan.
    crear_usuario("alumno-d", hace(1), es_prueba=1)
    crear_usuario("profe-e", hace(1), rol="profesor")
    crear_usuario("alumno-f", hace(3), registrado=False, fusionado_en="alumno-a")
    for excluido in ("alumno-d", "profe-e", "alumno-f"):
        agregar_eventos(
            excluido,
            ("learning_session_started", hace(3)),
            ("lesson_completed", hace(1)),
            ("activity_submitted", hace(1)),
        )
        agregar_progreso(excluido, "blender", "les_009", completada_en=hace(1))
    # G: anónimo (sin cuenta) que estudia: activado y activo, pero no es registro.
    crear_usuario("alumno-g", hace(6), registrado=False)
    agregar_eventos("alumno-g", ("learning_session_started", hace(4)), ("activity_submitted", hace(2)))
    # H: alumno antiguo, solo cuenta en el total de lecciones y por curso.
    crear_usuario("alumno-h", hace(60))
    agregar_progreso("alumno-h", "blender", "les_001", completada_en=hace(50))
    agregar_progreso("alumno-h", "aframe", "les_af_001", completada_en=hace(50))
    agregar_eventos("alumno-h", ("lesson_completed", hace(50)))
    # I y J: cohorte de retención (activados hace 19 y 18 días); solo I vuelve en los días 8 a 14.
    crear_usuario("alumno-i", hace(20))
    agregar_eventos("alumno-i", ("activity_submitted", hace(19)), ("lesson_completed", hace(10)))
    crear_usuario("alumno-j", hace(20))
    agregar_eventos("alumno-j", ("lesson_completed", hace(18)), ("learning_session_started", hace(1)))
    # K: anónimo de hace un mes que se registró hace 3 días (evento account_created).
    crear_usuario("alumno-k", hace(30))
    agregar_eventos("alumno-k", ("account_created", hace(3)))

    return {"momento": momento, "hace": hace, "profesor": profesor}


def test_resumen_metricas_del_plan(cliente, escenario):
    respuesta = cliente.get("/api/admin/resumen", headers=escenario["profesor"])
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()

    assert datos["usuarios"] == {
        "total": 11,  # sin la fila fusionada; incluye al profesor que consulta
        "registrados": 10,
        "anonimos": 1,
        "confirmados": 9,
        "por_rol": {"alumno": 9, "profesor": 2, "admin": 0},
        "de_prueba": 1,
    }
    assert datos["registros_periodo"] == 2  # B (creado_en) y K (account_created)
    assert datos["activaciones_periodo"] == 3  # A, B y G
    assert datos["activos_semanales"] == 2  # A y G
    assert datos["lecciones_completadas"] == {"total": 3, "periodo": 1}
    assert datos["actividades_periodo"] == 1  # G
    assert datos["retencion_semana2"] == {"numerador": 1, "denominador": 2, "porcentaje": 50.0}
    assert datos["embudo"] == {"registros": 2, "activaciones": 3, "activos": 2}
    assert datos["generado_en"] and datos["periodo"]["hasta"] > datos["periodo"]["desde"]


def test_resumen_serie_diaria(cliente, escenario):
    hace = escenario["hace"]
    serie = cliente.get("/api/admin/resumen", headers=escenario["profesor"]).json()["serie_diaria"]
    assert len(serie) == 30
    assert serie[-1]["fecha"] == dia(ahora())  # hoy en hora de Ciudad de México
    assert [s["fecha"] for s in serie] == sorted(s["fecha"] for s in serie)
    por_fecha = {s["fecha"]: s for s in serie}

    assert por_fecha[dia(hace(1))]["activos"] == 3  # A, B y J (D, E y F no cuentan)
    assert por_fecha[dia(hace(2))]["activos"] == 2  # C y G
    assert por_fecha[dia(hace(5))]["completadas"] == 1  # A
    assert sum(s["completadas"] for s in serie) == 1
    assert por_fecha[dia(hace(2))]["registros"] == 1  # B
    assert por_fecha[dia(hace(3))]["registros"] == 1  # K
    assert sum(s["registros"] for s in serie) == 5  # A, B, I, J y K


def test_resumen_por_curso(cliente, escenario):
    profesor = escenario["profesor"]
    por_curso = cliente.get("/api/admin/resumen", headers=profesor).json()["por_curso"]
    assert por_curso == [
        {"curso_id": "aframe", "alumnos": 2, "completadas": 1, "lecciones_publicadas": None},
        {"curso_id": "blender", "alumnos": 2, "completadas": 2, "lecciones_publicadas": None},
    ]

    # Con contenido en la base: lecciones publicadas por curso (y orden del curso).
    with Session(conexion.motor()) as db:
        db.add_all([Curso(id="blender", titulo="Blender", orden=1), Curso(id="aframe", titulo="A-Frame", orden=2)])
        db.add(Modulo(id="mod_teoria_001", curso_id="blender", numero=1, titulo="Teoría"))
        db.flush()
        for leccion_id, estado in (("les_001", "publicado"), ("les_002", "publicado"), ("les_003", "borrador")):
            db.add(
                Leccion(
                    curso_id="blender",
                    id=leccion_id,
                    modulo_id="mod_teoria_001",
                    orden=1,
                    titulo=leccion_id,
                    tipo="theory_reading",
                    contenido="{}",
                    estado=estado,
                )
            )
        db.commit()
    por_curso = cliente.get("/api/admin/resumen", headers=profesor).json()["por_curso"]
    assert [(c["curso_id"], c["lecciones_publicadas"]) for c in por_curso] == [("blender", 2), ("aframe", 0)]


def test_resumen_con_ventana_de_30_dias(cliente, escenario):
    datos = cliente.get("/api/admin/resumen?dias=30", headers=escenario["profesor"]).json()
    assert datos["activos_semanales"] == 4  # A, G, I y J
    assert datos["registros_periodo"] == 5  # A, B, I, J y K
    assert datos["activaciones_periodo"] == 5  # A, B, G, I y J
    assert cliente.get("/api/admin/resumen?dias=0", headers=escenario["profesor"]).status_code == 422
    assert cliente.get("/api/admin/resumen?dias=91", headers=escenario["profesor"]).status_code == 422


def test_resumen_activacion_fuera_de_7_dias_no_cuenta(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    momento = ahora()
    # Alta hace 12 días, primera lección hace 2: no es activación (más de 7 días).
    crear_usuario("alumno-tarde", momento - timedelta(days=12))
    agregar_eventos("alumno-tarde", ("lesson_completed", momento - timedelta(days=2)))
    datos = cliente.get("/api/admin/resumen", headers=admin).json()
    assert datos["activaciones_periodo"] == 0
    assert datos["retencion_semana2"] == {"numerador": 0, "denominador": 0, "porcentaje": None}


def test_resumen_sin_datos(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    datos = cliente.get("/api/admin/resumen", headers=admin).json()
    assert datos["activos_semanales"] == 0
    assert datos["por_curso"] == []
    assert len(datos["serie_diaria"]) == 30
    assert datos["usuarios"]["por_rol"] == {"alumno": 0, "profesor": 0, "admin": 1}


def test_marcar_cuenta_de_prueba_la_saca_de_las_metricas(cliente, escenario, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    antes = cliente.get("/api/admin/resumen", headers=admin).json()
    respuesta = cliente.patch("/api/admin/usuarios/alumno-a", json={"es_prueba": True}, headers=admin)
    assert respuesta.status_code == 200 and respuesta.json()["es_prueba"] is True
    # La copia de es_prueba en los eventos también se actualiza.
    assert contar(EventoAprendizaje, EventoAprendizaje.usuario_id == "alumno-a", EventoAprendizaje.es_prueba == 0) == 0
    despues = cliente.get("/api/admin/resumen", headers=admin).json()
    assert despues["activos_semanales"] == antes["activos_semanales"] - 1
    assert despues["activaciones_periodo"] == antes["activaciones_periodo"] - 1
    assert despues["usuarios"]["de_prueba"] == antes["usuarios"]["de_prueba"] + 1


# --- Usuarios -------------------------------------------------------------------


@pytest.fixture()
def usuarios(cliente, crear_cuenta):
    admin_id, admin = crear_cuenta(rol="admin", nombre="Prueba")
    momento = ahora()
    crear_usuario(
        "usr-1",
        momento - timedelta(days=3),
        nombre="Ana López",
        email="ana@amatista.local",
        ultimo_acceso=momento - timedelta(days=2),
    )
    crear_usuario(
        "usr-2",
        momento - timedelta(days=2),
        nombre="Beto Ruiz",
        email="beto@amatista.local",
        rol="profesor",
        ultimo_acceso=momento - timedelta(hours=1),
    )
    crear_usuario("alumno-xyz-anonimo", momento - timedelta(days=1), registrado=False, nombre=None)
    crear_usuario("alumno-fusionado", momento - timedelta(days=4), registrado=False, fusionado_en="usr-1")
    agregar_progreso("usr-1", "blender", "les_001", completada_en=momento, puntaje=80)
    agregar_progreso("usr-1", "blender", "les_002", completada_en=momento)
    with Session(conexion.motor()) as db:
        db.add(ProgresoLeccion(usuario_id="usr-1", curso_id="blender", leccion_id="les_003", completada=0, puntaje=50))
        db.commit()
    return {"admin_id": admin_id, "admin": admin}


def listar(cliente, cabeceras, **params):
    respuesta = cliente.get("/api/admin/usuarios", params=params, headers=cabeceras)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_usuarios_por_defecto(cliente, usuarios):
    datos = listar(cliente, usuarios["admin"])
    assert (datos["total"], datos["pagina"], datos["por_pagina"]) == (4, 1, 25)
    # Más reciente primero; la fila fusionada se oculta.
    assert [u["id"] for u in datos["usuarios"]] == [usuarios["admin_id"], "alumno-xyz-anonimo", "usr-2", "usr-1"]
    ana = datos["usuarios"][-1]
    assert ana["nombre"] == "Ana López" and ana["email"] == "ana@amatista.local"
    assert (ana["lecciones_completadas"], ana["xp"]) == (2, 280)  # 2 × 100 + 80 (el puntaje sin completar no suma)
    assert ana["anonimo"] is False and ana["fusionado_en"] is None
    assert ana["ultimo_acceso"] and ana["creado_en"] and ana["rol"] == "alumno"
    assert ana["correo_confirmado"] is True and ana["es_prueba"] is False
    anonimo = datos["usuarios"][1]
    assert anonimo["anonimo"] is True and (anonimo["lecciones_completadas"], anonimo["xp"]) == (0, 0)
    assert "password_hash" not in ana and "codigo_verificacion" not in ana


def test_usuarios_busqueda_sin_distinguir_mayusculas(cliente, usuarios):
    admin = usuarios["admin"]
    assert [u["id"] for u in listar(cliente, admin, buscar="ANA")["usuarios"]] == ["usr-1"]  # nombre y correo
    assert [u["id"] for u in listar(cliente, admin, buscar="lópez")["usuarios"]] == ["usr-1"]
    assert [u["id"] for u in listar(cliente, admin, buscar="BETO@")["usuarios"]] == ["usr-2"]  # correo
    assert [u["id"] for u in listar(cliente, admin, buscar="Xyz")["usuarios"]] == ["alumno-xyz-anonimo"]  # id
    assert listar(cliente, admin, buscar="%")["total"] == 0  # los comodines se escapan
    assert listar(cliente, admin, buscar="_")["total"] == 0
    assert listar(cliente, admin, buscar="   ")["total"] == 4
    assert listar(cliente, admin, buscar="fusionado")["total"] == 0
    assert listar(cliente, admin, buscar="fusionado", incluir_fusionados=True)["total"] == 1


def test_usuarios_filtro_por_rol(cliente, usuarios):
    admin = usuarios["admin"]
    assert [u["id"] for u in listar(cliente, admin, rol="profesor")["usuarios"]] == ["usr-2"]
    assert [u["id"] for u in listar(cliente, admin, rol="admin")["usuarios"]] == [usuarios["admin_id"]]
    assert {u["id"] for u in listar(cliente, admin, rol="alumno")["usuarios"]} == {"usr-1", "alumno-xyz-anonimo"}
    assert listar(cliente, admin, rol="alumno", incluir_fusionados=True)["total"] == 3
    assert listar(cliente, admin, rol="")["total"] == 4
    respuesta = cliente.get("/api/admin/usuarios", params={"rol": "director"}, headers=admin)
    assert respuesta.status_code == 422 and "Rol inválido" in respuesta.json()["detail"]


def test_usuarios_paginacion(cliente, usuarios):
    admin = usuarios["admin"]
    paginas = [listar(cliente, admin, por_pagina=3, pagina=n) for n in (1, 2, 3)]
    assert [len(p["usuarios"]) for p in paginas] == [3, 1, 0]
    assert all(p["total"] == 4 and p["por_pagina"] == 3 for p in paginas)
    ids = [u["id"] for p in paginas for u in p["usuarios"]]
    assert len(set(ids)) == 4
    assert listar(cliente, admin, por_pagina=500)["por_pagina"] == 100
    assert cliente.get("/api/admin/usuarios?pagina=0", headers=admin).status_code == 422
    assert cliente.get("/api/admin/usuarios?por_pagina=0", headers=admin).status_code == 422


def test_usuarios_orden(cliente, usuarios):
    admin = usuarios["admin"]
    por_nombre = [u["nombre"] for u in listar(cliente, admin, orden="nombre")["usuarios"]]
    assert por_nombre == ["Ana López", "Beto Ruiz", "Prueba", None]  # sin nombre al final
    por_actividad = [u["id"] for u in listar(cliente, admin, orden="actividad")["usuarios"]]
    assert por_actividad[:2] == ["usr-2", "usr-1"]  # los que nunca entraron, al final
    respuesta = cliente.get("/api/admin/usuarios?orden=azar", headers=admin)
    assert respuesta.status_code == 422


def test_detalle_de_usuario(cliente, usuarios):
    admin = usuarios["admin"]
    momento = ahora()
    with Session(conexion.motor()) as db:
        db.add(Logro(usuario_id="usr-1", insignia_id="blender:mod_teoria_001", obtenido_en=momento))
        db.commit()
    agregar_sesion("usr-1")
    agregar_sesion("usr-1", activa=0)
    agregar_sesion("usr-1", expira_en=momento - timedelta(minutes=1))
    agregar_eventos("usr-1", *[("learning_session_started", momento - timedelta(hours=h)) for h in range(25)])

    respuesta = cliente.get("/api/admin/usuarios/usr-1", headers=admin)
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["usuario"]["id"] == "usr-1"
    assert (datos["usuario"]["lecciones_completadas"], datos["usuario"]["xp"]) == (2, 280)
    assert [f["leccion_id"] for f in datos["progreso"]] == ["les_001", "les_002", "les_003"]
    assert datos["progreso"][0]["completada"] is True and datos["progreso"][0]["puntaje"] == 80
    assert [i["id"] for i in datos["insignias"]] == ["blender:mod_teoria_001"]
    assert datos["sesiones_activas"] == 1
    recientes = datos["eventos_recientes"]
    assert len(recientes) == 20
    assert recientes[0]["ocurrido_en"] > recientes[-1]["ocurrido_en"]  # más reciente primero
    assert set(recientes[0]) == {"tipo", "curso_id", "leccion_id", "ocurrido_en"}

    assert cliente.get("/api/admin/usuarios/no-existe", headers=admin).status_code == 404


def test_profesor_ve_el_detalle(cliente, usuarios, crear_cuenta):
    _, profesor = crear_cuenta(rol="profesor")
    _, alumno = crear_cuenta()
    assert cliente.get("/api/admin/usuarios/usr-1", headers=profesor).status_code == 200
    assert cliente.get("/api/admin/usuarios/usr-1", headers=alumno).status_code == 403


# --- PATCH /usuarios/{id} ----------------------------------------------------------------


def test_admin_cambia_rol_y_aplica_de_inmediato(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    alumno_id, alumno = crear_cuenta()
    assert cliente.get("/api/admin/resumen", headers=alumno).status_code == 403

    respuesta = cliente.patch(f"/api/admin/usuarios/{alumno_id}", json={"rol": "profesor"}, headers=admin)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["rol"] == "profesor"
    assert set(respuesta.json()) == {
        "id", "nombre", "email", "telefono", "rol", "correo_confirmado", "es_prueba", "creado_en",
    }
    # El rol se lee en cada petición: no hace falta volver a iniciar sesión.
    assert cliente.get("/api/admin/resumen", headers=alumno).status_code == 200
    assert cliente.patch(f"/api/admin/usuarios/{alumno_id}", json={"rol": "admin"}, headers=alumno).status_code == 403


def test_admin_no_se_quita_admin_a_si_mismo(cliente, crear_cuenta):
    admin_id, admin = crear_cuenta(rol="admin")
    for rol in ("profesor", "alumno"):
        respuesta = cliente.patch(f"/api/admin/usuarios/{admin_id}", json={"rol": rol}, headers=admin)
        assert respuesta.status_code == 400
        assert "No puedes quitarte el rol de administrador" in respuesta.json()["detail"]
    assert leer(Usuario, admin_id).rol == "admin"
    # Sin cambio de rol sí puede editarse.
    ruta = f"/api/admin/usuarios/{admin_id}"
    respuesta = cliente.patch(ruta, json={"rol": "admin", "es_prueba": True}, headers=admin)
    assert respuesta.status_code == 200 and respuesta.json()["es_prueba"] is True
    # Otro administrador sí puede degradarlo.
    _, otro_admin = crear_cuenta(rol="admin")
    assert cliente.patch(ruta, json={"rol": "alumno"}, headers=otro_admin).status_code == 200
    assert leer(Usuario, admin_id).rol == "alumno"


def test_patch_valida(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    alumno_id, _ = crear_cuenta()
    respuesta = cliente.patch(f"/api/admin/usuarios/{alumno_id}", json={"rol": "director"}, headers=admin)
    assert respuesta.status_code == 422 and "Rol inválido" in respuesta.json()["detail"]
    assert cliente.patch("/api/admin/usuarios/no-existe", json={"rol": "alumno"}, headers=admin).status_code == 404
    # Un alumno anónimo no puede ser profesor ni admin (no tiene con qué iniciar sesión).
    crear_usuario("alumno-anonimo", ahora(), registrado=False)
    respuesta = cliente.patch("/api/admin/usuarios/alumno-anonimo", json={"rol": "profesor"}, headers=admin)
    assert respuesta.status_code == 400
    assert leer(Usuario, "alumno-anonimo").rol == "alumno"


def test_patch_correo_confirmado(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    alumno_id, _ = crear_cuenta()
    with Session(conexion.motor()) as db:
        usuario = db.get(Usuario, alumno_id)
        usuario.correo_confirmado = 0
        usuario.codigo_verificacion, usuario.codigo_proposito, usuario.codigo_intentos = "a" * 64, "correo", 2
        db.commit()
    respuesta = cliente.patch(f"/api/admin/usuarios/{alumno_id}", json={"correo_confirmado": True}, headers=admin)
    assert respuesta.status_code == 200 and respuesta.json()["correo_confirmado"] is True
    usuario = leer(Usuario, alumno_id)
    assert usuario.codigo_verificacion is None and usuario.codigo_proposito is None
    respuesta = cliente.patch(f"/api/admin/usuarios/{alumno_id}", json={"correo_confirmado": False}, headers=admin)
    assert respuesta.json()["correo_confirmado"] is False
    # Cuerpo vacío: no cambia nada.
    assert cliente.patch(f"/api/admin/usuarios/{alumno_id}", json={}, headers=admin).status_code == 200


# --- Mantenimiento ----------------------------------------------------------------------


def test_purgar_sesiones_y_eventos(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    alumno_id, propia = crear_cuenta()
    momento = ahora()
    agregar_sesion(alumno_id, activa=0)  # cerrada
    agregar_sesion(alumno_id, expira_en=momento - timedelta(days=1))  # vencida
    agregar_sesion(alumno_id, ultimo_acceso=momento - timedelta(days=100))  # sin acceso en 90 días
    vigente = agregar_sesion(alumno_id)
    agregar_eventos(
        alumno_id,
        ("lesson_completed", momento - timedelta(days=500)),
        ("lesson_completed", momento - timedelta(days=10)),
    )

    respuesta = cliente.post("/api/admin/mantenimiento/purgar", headers=admin)  # sin cuerpo: valores por defecto
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"sesiones": 3, "eventos": 1}
    assert contar(Sesion, Sesion.usuario_id == alumno_id) == 2  # la de crear_cuenta y la vigente
    for cabeceras in (vigente, propia):
        assert cliente.get("/api/auth/yo", headers=cabeceras).status_code == 200
    assert cliente.get("/api/admin/resumen", headers=admin).status_code == 200  # la sesión del admin sigue

    respuesta = cliente.post(
        "/api/admin/mantenimiento/purgar", json={"dias_sesiones": 90, "dias_eventos": 5}, headers=admin
    )
    assert respuesta.json() == {"sesiones": 0, "eventos": 1}
    assert contar(EventoAprendizaje) == 0
    # Nunca toca usuarios ni progreso.
    assert leer(Usuario, alumno_id) is not None


def test_purgar_valida(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    for cuerpo in ({"dias_sesiones": 0}, {"dias_eventos": -1}):
        assert cliente.post("/api/admin/mantenimiento/purgar", json=cuerpo, headers=admin).status_code == 422


def test_salud_detallada(cliente, crear_cuenta):
    _, admin = crear_cuenta(rol="admin")
    crear_usuario("alumno-1", ahora(), registrado=False)
    respuesta = cliente.get("/api/admin/salud-detallada", headers=admin)
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["motor"] == "sqlite"
    assert datos["version_api"] == cliente.app.version
    assert datos["tablas"]["usuarios"] == 2
    assert datos["tablas"]["sesiones"] == 1
    assert set(datos["tablas"]) >= {
        "usuarios", "sesiones", "progreso_lecciones", "logros", "eventos_aprendizaje", "cursos", "modulos", "lecciones",
    }
