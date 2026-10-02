"""Panel de administración: métricas del lanzamiento, usuarios y mantenimiento.

Profesores y administradores leen; solo los administradores modifican.

Métricas (plan de lanzamiento, sec. 9). Cuentan solo alumnos reales: se
excluyen las cuentas de prueba (es_prueba = 1), los roles profesor y admin y
las filas anónimas ya fusionadas con una cuenta (serían la misma persona dos
veces). Los alumnos anónimos que estudian sin cuenta sí cuentan.

- Registro: cuenta con correo y contraseña; su fecha es la del evento
  account_created (si falta, creado_en).
- Activación: primera lección completada (completada_en o lesson_completed)
  o primera activity_submitted, dentro de 7 días desde creado_en.
- Activo semanal: actividad de aprendizaje (learning_session_started,
  lesson_completed, activity_submitted o una lección completada) en 2 o más
  días distintos de la ventana, y al menos una lección completada o actividad
  entregada en la ventana.
- Retención de segunda semana: de los activados en la ventana desplazada 14
  días atrás (ya transcurrieron sus días 8 a 14), cuántos tuvieron actividad
  de aprendizaje entre los días 8 y 14 posteriores a su activación.

Se calcula en Python sobre consultas acotadas por fecha (portátil entre
SQLite y Oracle). Los días se cortan en hora de Ciudad de México, como los
cortes del plan.
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import error_bd, usuario_publico
from api.dependencias import requiere_rol
from api.progreso import iso, progreso_de
from database.conexion import obtener_db
from database.modelos import (
    ROLES,
    Base,
    Curso,
    EventoAprendizaje,
    Leccion,
    ProgresoLeccion,
    Sesion,
    Usuario,
    ahora,
)

log = logging.getLogger("amatista")

router = APIRouter(prefix="/api/admin", tags=["administracion"])

lectores = requiere_rol("profesor", "admin")
administradores = requiere_rol("admin")

TIPOS_APRENDIZAJE = ("learning_session_started", "lesson_completed", "activity_submitted")
# Lección completada o actividad válida (requisito del activo semanal y de la activación).
TIPOS_LOGRO = ("lesson_completed", "activity_submitted")
DIAS_ACTIVACION = timedelta(days=7)
DIAS_SERIE = 30
ORDENES = ("reciente", "nombre", "actividad")


def zona_metricas():
    """Hora de Ciudad de México (sin horario de verano desde 2022)."""
    try:
        return ZoneInfo("America/Mexico_City")
    except (KeyError, ValueError):  # sin base de zonas horarias (p. ej. Windows sin tzdata)
        return timezone(timedelta(hours=-6))


def dia_local(fecha: datetime, zona) -> date:
    return fecha.replace(tzinfo=timezone.utc).astimezone(zona).date()


def alumnos_reales():
    """Condición SQL: alumno real (sin pruebas, sin personal, sin filas fusionadas)."""
    return and_(
        func.coalesce(Usuario.rol, "alumno") == "alumno",
        func.coalesce(Usuario.es_prueba, 0) == 0,
        Usuario.fusionado_en.is_(None),
    )


def suma_si(condicion, valor=1):
    return func.sum(case((condicion, valor), else_=0))


# --- Resumen -------------------------------------------------------------------


def contar_usuarios(db: Session) -> dict:
    """Totales de la tabla de usuarios (sin las filas fusionadas)."""
    total, registrados, confirmados, de_prueba = db.execute(
        select(
            func.count(),
            suma_si(Usuario.password_hash.is_not(None)),
            suma_si(Usuario.correo_confirmado == 1),
            suma_si(Usuario.es_prueba == 1),
        ).where(Usuario.fusionado_en.is_(None))
    ).one()
    por_rol = {rol: 0 for rol in ROLES}
    # Se agrupa por la columna sola: en Oracle, GROUP BY con una expresión con
    # variables enlazadas falla (ORA-00979).
    for rol, cantidad in db.execute(
        select(Usuario.rol, func.count()).where(Usuario.fusionado_en.is_(None)).group_by(Usuario.rol)
    ).all():
        clave = rol or "alumno"
        por_rol[clave] = por_rol.get(clave, 0) + cantidad
    total, registrados = total or 0, registrados or 0
    return {
        "total": total,
        "registrados": registrados,
        "anonimos": total - registrados,
        "confirmados": confirmados or 0,
        "por_rol": por_rol,
        "de_prueba": de_prueba or 0,
    }


def por_curso(db: Session) -> List[dict]:
    filas = db.execute(
        select(
            ProgresoLeccion.curso_id,
            func.count(func.distinct(ProgresoLeccion.usuario_id)),
            suma_si(ProgresoLeccion.completada == 1),
        )
        .join(Usuario, Usuario.id == ProgresoLeccion.usuario_id)
        .where(alumnos_reales())
        .group_by(ProgresoLeccion.curso_id)
    ).all()
    alumnos = {curso_id: (cantidad, completadas or 0) for curso_id, cantidad, completadas in filas}

    # Lecciones publicadas por curso, solo si la tabla de contenido tiene filas
    # (antes de importar el contenido el dato no existe: null).
    publicadas: Optional[Dict[str, int]] = None
    if db.scalar(select(func.count()).select_from(Leccion)):
        publicadas = dict(
            db.execute(
                select(Leccion.curso_id, func.count()).where(Leccion.estado == "publicado").group_by(Leccion.curso_id)
            ).all()
        )
    orden = dict(db.execute(select(Curso.id, Curso.orden)).all())
    cursos = set(alumnos) | set(publicadas or {})
    return [
        {
            "curso_id": curso_id,
            "alumnos": alumnos.get(curso_id, (0, 0))[0],
            "completadas": alumnos.get(curso_id, (0, 0))[1],
            "lecciones_publicadas": None if publicadas is None else publicadas.get(curso_id, 0),
        }
        for curso_id in sorted(cursos, key=lambda c: (orden.get(c) is None, orden.get(c) or 0, c))
    ]


@router.get("/resumen")
def resumen(
    dias: int = Query(7, ge=1, le=90),
    _: Usuario = Depends(lectores),
    db: Session = Depends(obtener_db),
):
    momento = ahora()
    zona = zona_metricas()
    desde = momento - timedelta(days=dias)
    hoy = dia_local(momento, zona)
    dias_serie = [hoy - timedelta(days=i) for i in range(DIAS_SERIE - 1, -1, -1)]
    # Retención: cohorte activada en la ventana desplazada 14 días atrás.
    cohorte_desde, cohorte_hasta = desde - timedelta(days=14), momento - timedelta(days=14)
    # Límite de todas las consultas: la serie diaria y la búsqueda de la
    # activación de esa cohorte (hasta 7 días después de su alta).
    inicio = min(cohorte_desde - DIAS_ACTIVACION, momento - timedelta(days=DIAS_SERIE + 1))

    try:
        usuarios = contar_usuarios(db)

        # Alumnos reales dados de alta desde el inicio: candidatos a activación.
        altas = {
            usuario_id: (creado_en, bool(registrado))
            for usuario_id, creado_en, registrado in db.execute(
                select(Usuario.id, Usuario.creado_en, case((Usuario.password_hash.is_(None), 0), else_=1)).where(
                    alumnos_reales(), Usuario.creado_en >= inicio
                )
            ).all()
        }
        eventos = db.execute(
            select(
                EventoAprendizaje.usuario_id,
                EventoAprendizaje.tipo,
                EventoAprendizaje.ocurrido_en,
                case((Usuario.password_hash.is_(None), 0), else_=1),
            )
            .join(Usuario, Usuario.id == EventoAprendizaje.usuario_id)
            .where(alumnos_reales(), EventoAprendizaje.ocurrido_en >= inicio)
        ).all()
        completadas = db.execute(
            select(ProgresoLeccion.usuario_id, ProgresoLeccion.completada_en)
            .join(Usuario, Usuario.id == ProgresoLeccion.usuario_id)
            .where(
                alumnos_reales(),
                ProgresoLeccion.completada == 1,
                ProgresoLeccion.completada_en >= inicio,
            )
        ).all()
        completadas_total = db.scalar(
            select(func.count())
            .select_from(ProgresoLeccion)
            .join(Usuario, Usuario.id == ProgresoLeccion.usuario_id)
            .where(alumnos_reales(), ProgresoLeccion.completada == 1)
        )
        cursos = por_curso(db)
    except SQLAlchemyError as error:
        raise error_bd(error, "/api/admin/resumen")

    # Actividad de aprendizaje por alumno: (fecha, es lección completada o actividad).
    actividad: Dict[str, List[tuple]] = defaultdict(list)
    registro: Dict[str, datetime] = {}
    actividades_periodo = 0
    for usuario_id, tipo, ocurrido_en, registrado in eventos:
        if tipo in TIPOS_APRENDIZAJE:
            actividad[usuario_id].append((ocurrido_en, tipo in TIPOS_LOGRO))
            if tipo == "activity_submitted" and ocurrido_en >= desde:
                actividades_periodo += 1
        elif tipo == "account_created" and registrado:
            registro[usuario_id] = min(registro.get(usuario_id, ocurrido_en), ocurrido_en)
    for usuario_id, completada_en in completadas:
        actividad[usuario_id].append((completada_en, True))
    # Cuentas sin evento account_created (creadas antes de los eventos o por
    # herramientas): la fecha de registro es creado_en.
    for usuario_id, (creado_en, registrado) in altas.items():
        if registrado and usuario_id not in registro and creado_en is not None:
            registro[usuario_id] = creado_en

    # Activos semanales.
    activos_semanales = 0
    for fechas in actividad.values():
        en_ventana = [(f, logro) for f, logro in fechas if f >= desde]
        if len({dia_local(f, zona) for f, _ in en_ventana}) >= 2 and any(logro for _, logro in en_ventana):
            activos_semanales += 1

    # Activaciones: primera lección o actividad dentro de 7 días desde el alta.
    activacion: Dict[str, datetime] = {}
    for usuario_id, (creado_en, _) in altas.items():
        logros = [f for f, logro in actividad.get(usuario_id, ()) if logro]
        if logros and creado_en is not None and min(logros) <= creado_en + DIAS_ACTIVACION:
            activacion[usuario_id] = min(logros)
    activaciones_periodo = sum(1 for f in activacion.values() if f >= desde)

    # Retención de segunda semana.
    cohorte = [u for u, f in activacion.items() if cohorte_desde <= f <= cohorte_hasta]
    retenidos = 0
    for usuario_id in cohorte:
        dia_activacion = dia_local(activacion[usuario_id], zona)
        if any(8 <= (dia_local(f, zona) - dia_activacion).days <= 14 for f, _ in actividad[usuario_id]):
            retenidos += 1

    # Serie diaria de los últimos 30 días.
    activos_por_dia: Dict[date, Set[str]] = defaultdict(set)
    for usuario_id, fechas in actividad.items():
        for f, _ in fechas:
            activos_por_dia[dia_local(f, zona)].add(usuario_id)
    completadas_por_dia: Dict[date, int] = defaultdict(int)
    for _, completada_en in completadas:
        completadas_por_dia[dia_local(completada_en, zona)] += 1
    registros_por_dia: Dict[date, int] = defaultdict(int)
    for fecha in registro.values():
        registros_por_dia[dia_local(fecha, zona)] += 1
    registros_periodo = sum(1 for f in registro.values() if f >= desde)

    return {
        "generado_en": iso(momento),
        "periodo": {"desde": iso(desde), "hasta": iso(momento)},
        "usuarios": usuarios,
        "registros_periodo": registros_periodo,
        "activaciones_periodo": activaciones_periodo,
        "activos_semanales": activos_semanales,
        "lecciones_completadas": {
            "total": completadas_total or 0,
            "periodo": sum(1 for _, f in completadas if f >= desde),
        },
        "actividades_periodo": actividades_periodo,
        "retencion_semana2": {
            "numerador": retenidos,
            "denominador": len(cohorte),
            "porcentaje": round(retenidos * 100 / len(cohorte), 1) if cohorte else None,
        },
        "serie_diaria": [
            {
                "fecha": dia.isoformat(),
                "activos": len(activos_por_dia.get(dia, ())),
                "completadas": completadas_por_dia.get(dia, 0),
                "registros": registros_por_dia.get(dia, 0),
            }
            for dia in dias_serie
        ],
        "por_curso": cursos,
        "embudo": {
            "registros": registros_periodo,
            "activaciones": activaciones_periodo,
            "activos": activos_semanales,
        },
    }


# --- Usuarios ------------------------------------------------------------------


def ficha(usuario: Usuario, completadas: int = 0, xp: int = 0) -> dict:
    """UsuarioPublico más los datos que solo ve el panel."""
    return {
        **usuario_publico(usuario),
        "ultimo_acceso": iso(usuario.ultimo_acceso),
        "lecciones_completadas": completadas,
        "xp": xp,
        "anonimo": not usuario.password_hash,
        "fusionado_en": usuario.fusionado_en,
    }


def escapar_like(texto: str) -> str:
    return texto.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def orden_sql(orden: str) -> list:
    # Los nulos van al final en los dos motores (Oracle y SQLite los ordenan distinto).
    if orden == "nombre":
        return [case((Usuario.nombre.is_(None), 1), else_=0), func.lower(Usuario.nombre), Usuario.id]
    if orden == "actividad":
        return [
            case((Usuario.ultimo_acceso.is_(None), 1), else_=0),
            Usuario.ultimo_acceso.desc(),
            Usuario.creado_en.desc(),
            Usuario.id,
        ]
    return [case((Usuario.creado_en.is_(None), 1), else_=0), Usuario.creado_en.desc(), Usuario.id]


@router.get("/usuarios")
def listar_usuarios(
    buscar: Optional[str] = Query(None, max_length=100),
    rol: Optional[str] = Query(None),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(25, ge=1),
    orden: str = Query("reciente"),
    incluir_fusionados: bool = Query(False),
    _: Usuario = Depends(lectores),
    db: Session = Depends(obtener_db),
):
    if orden not in ORDENES:
        raise HTTPException(status_code=422, detail="Orden inválido: usa reciente, nombre o actividad.")
    rol = (rol or "").strip() or None
    if rol is not None and rol not in ROLES:
        raise HTTPException(status_code=422, detail="Rol inválido: usa alumno, profesor o admin.")
    por_pagina = min(por_pagina, 100)

    condiciones = []
    if not incluir_fusionados:
        condiciones.append(Usuario.fusionado_en.is_(None))
    if rol == "alumno":
        condiciones.append(or_(Usuario.rol == "alumno", Usuario.rol.is_(None)))
    elif rol:
        condiciones.append(Usuario.rol == rol)
    buscar = (buscar or "").strip().lower()
    if buscar:
        patron = f"%{escapar_like(buscar)}%"
        condiciones.append(
            or_(
                func.lower(Usuario.nombre).like(patron, escape="!"),
                func.lower(Usuario.email).like(patron, escape="!"),
                func.lower(Usuario.id).like(patron, escape="!"),
            )
        )

    try:
        total = db.scalar(select(func.count()).select_from(Usuario).where(*condiciones))
        usuarios = db.scalars(
            select(Usuario)
            .where(*condiciones)
            .order_by(*orden_sql(orden))
            .offset((pagina - 1) * por_pagina)
            .limit(por_pagina)
        ).all()
        estadisticas = {}
        if usuarios:
            estadisticas = {
                usuario_id: (completadas or 0, xp or 0)
                for usuario_id, completadas, xp in db.execute(
                    select(
                        ProgresoLeccion.usuario_id,
                        suma_si(ProgresoLeccion.completada == 1),
                        # xp = 100 por lección completada + su puntaje.
                        suma_si(ProgresoLeccion.completada == 1, 100 + func.coalesce(ProgresoLeccion.puntaje, 0)),
                    )
                    .where(ProgresoLeccion.usuario_id.in_([u.id for u in usuarios]))
                    .group_by(ProgresoLeccion.usuario_id)
                ).all()
            }
    except SQLAlchemyError as error:
        raise error_bd(error, "/api/admin/usuarios")

    return {
        "total": total or 0,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "usuarios": [ficha(u, *estadisticas.get(u.id, (0, 0))) for u in usuarios],
    }


@router.get("/usuarios/{usuario_id}")
def detalle_usuario(usuario_id: str, _: Usuario = Depends(lectores), db: Session = Depends(obtener_db)):
    try:
        usuario = db.get(Usuario, usuario_id)
        if usuario is None:
            raise HTTPException(status_code=404, detail="No existe ese usuario.")
        progreso = progreso_de(db, usuario_id)
        momento = ahora()
        sesiones_activas = db.scalar(
            select(func.count())
            .select_from(Sesion)
            .where(
                Sesion.usuario_id == usuario_id,
                Sesion.activa == 1,
                or_(Sesion.expira_en.is_(None), Sesion.expira_en > momento),
            )
        )
        eventos = db.scalars(
            select(EventoAprendizaje)
            .where(EventoAprendizaje.usuario_id == usuario_id)
            .order_by(EventoAprendizaje.ocurrido_en.desc(), EventoAprendizaje.id)
            .limit(20)
        ).all()
    except SQLAlchemyError as error:
        raise error_bd(error, "/api/admin/usuarios")

    completadas = [f for f in progreso["lecciones"] if f["completada"]]
    xp = sum(100 + (f["puntaje"] or 0) for f in completadas)
    return {
        "usuario": ficha(usuario, len(completadas), xp),
        "progreso": progreso["lecciones"],
        "insignias": progreso["insignias"],
        "sesiones_activas": sesiones_activas or 0,
        "eventos_recientes": [
            {"tipo": e.tipo, "curso_id": e.curso_id, "leccion_id": e.leccion_id, "ocurrido_en": iso(e.ocurrido_en)}
            for e in eventos
        ],
    }


class CambioUsuario(BaseModel):
    rol: Optional[str] = None
    es_prueba: Optional[bool] = None
    correo_confirmado: Optional[bool] = None


@router.patch("/usuarios/{usuario_id}")
def modificar_usuario(
    usuario_id: str,
    datos: CambioUsuario,
    actual: Usuario = Depends(administradores),
    db: Session = Depends(obtener_db),
):
    # El rol se lee en cada petición: cambiarlo no requiere cerrar sesiones.
    if datos.rol is not None and datos.rol not in ROLES:
        raise HTTPException(status_code=422, detail="Rol inválido: usa alumno, profesor o admin.")
    try:
        usuario = db.get(Usuario, usuario_id)
        if usuario is None:
            raise HTTPException(status_code=404, detail="No existe ese usuario.")
        if datos.rol is not None and datos.rol != (usuario.rol or "alumno"):
            if usuario.id == actual.id:
                raise HTTPException(
                    status_code=400,
                    detail="No puedes quitarte el rol de administrador: pídeselo a otro administrador.",
                )
            if datos.rol != "alumno" and not usuario.password_hash:
                raise HTTPException(
                    status_code=400, detail="Solo una cuenta con correo y contraseña puede ser profesor o admin."
                )
            usuario.rol = datos.rol
        if datos.es_prueba is not None and int(datos.es_prueba) != (usuario.es_prueba or 0):
            usuario.es_prueba = int(datos.es_prueba)
            # Los eventos llevan una copia de es_prueba (consultas sin JOIN).
            db.execute(
                update(EventoAprendizaje)
                .where(EventoAprendizaje.usuario_id == usuario.id)
                .values(es_prueba=usuario.es_prueba),
                execution_options={"synchronize_session": False},
            )
        if datos.correo_confirmado is not None:
            usuario.correo_confirmado = int(datos.correo_confirmado)
            if datos.correo_confirmado and usuario.codigo_proposito == "correo":
                usuario.codigo_verificacion = usuario.codigo_proposito = usuario.codigo_expira = None
                usuario.codigo_intentos = 0
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/admin/usuarios")
    return usuario_publico(usuario)


# --- Mantenimiento -------------------------------------------------------------


class SolicitudPurga(BaseModel):
    dias_sesiones: int = Field(default=90, ge=1, le=3650)
    dias_eventos: int = Field(default=400, ge=1, le=3650)


@router.post("/mantenimiento/purgar")
def purgar(
    datos: Optional[SolicitudPurga] = Body(default=None),
    _: Usuario = Depends(administradores),
    db: Session = Depends(obtener_db),
):
    """Lo mismo que AMATISTA_PURGAR (sql/003), con DELETE y sin quitar particiones.

    Nunca toca usuarios, progreso, logros ni contenido.
    """
    datos = datos or SolicitudPurga()
    momento = ahora()
    try:
        sesiones = db.execute(
            delete(Sesion).where(
                or_(
                    Sesion.activa == 0,
                    Sesion.expira_en < momento,
                    Sesion.ultimo_acceso < momento - timedelta(days=datos.dias_sesiones),
                )
            ),
            execution_options={"synchronize_session": False},
        ).rowcount
        eventos = db.execute(
            delete(EventoAprendizaje).where(
                EventoAprendizaje.ocurrido_en < momento - timedelta(days=datos.dias_eventos)
            ),
            execution_options={"synchronize_session": False},
        ).rowcount
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/admin/mantenimiento/purgar")
    log.info("Purga: %s sesiones y %s eventos borrados", sesiones, eventos)
    return {"sesiones": sesiones or 0, "eventos": eventos or 0}


@router.get("/salud-detallada")
def salud_detallada(request: Request, _: Usuario = Depends(administradores), db: Session = Depends(obtener_db)):
    """Motor y filas por tabla. Una tabla que falta (migración pendiente) da null."""
    tablas = {}
    for tabla in Base.metadata.sorted_tables:
        try:
            tablas[tabla.name] = db.scalar(select(func.count()).select_from(tabla))
        except SQLAlchemyError:
            log.exception("No se pudo contar la tabla %s", tabla.name)
            db.rollback()
            tablas[tabla.name] = None
    return {"motor": db.get_bind().dialect.name, "tablas": tablas, "version_api": request.app.version}
