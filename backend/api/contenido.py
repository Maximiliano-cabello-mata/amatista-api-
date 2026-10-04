"""Contenido de los cursos: catálogo público y edición desde el panel.

- GET /catalogo (público): cursos publicados con sus módulos y lecciones
  publicadas, en el formato de frontend/src/data/modulos/*.json. Responde
  304 si el navegador ya tiene la versión vigente (ETag). Con las tablas
  vacías devuelve cursos: [] y la PWA usa su catálogo empaquetado.
- Edición: solo administradores; los profesores pueden leer (GET) y validar.
- Reestructuración v3: cada curso tiene niveles (api/niveles.py) y cada
  módulo puede pertenecer a uno (nivel_id). El catálogo los incluye como
  campos nuevos; una PWA que no los conoce los ignora.
- Los ids son inmutables (el progreso de los alumnos se guarda con ellos) y
  lo publicado no se borra: se archiva. Solo se publica lo que pasa
  contenido.validacion; un borrador sí se guarda con errores (el editor los
  muestra) para no perder trabajo a medias.

importar_modulo y exportar_modulo también los usa herramientas/contenido.py.
"""
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, defer

from api.comun import error_bd
from api.dependencias import requiere_rol
from contenido.plantillas import (
    CURSOS_BASE,
    EJEMPLOS_BLOQUES,
    FORMULA,
    LECCIONES_PLANTILLA,
    RITMO,
    generar_esqueleto,
    minutos_estimados,
    siguiente_numeracion,
)
from contenido.validacion import (
    BLOQUES_CONTENIDO,
    BLOQUES_INTERACTIVOS,
    MAX_ID,
    MAX_REEMPLAZA,
    MAX_TITULO,
    PATRON_ID,
    TIPOS_LECCION,
    ContenidoInvalido,
    es_practica_blender,
    modulo_de,
    validar_leccion,
    validar_modulo,
)
from database.conexion import obtener_db
from database.modelos import Curso, Leccion, Modulo, Nivel, Usuario, ahora

router = APIRouter(prefix="/api/contenido", tags=["contenido"])

lector = requiere_rol("profesor", "admin")
editor = requiere_rol("admin")

Estado = Literal["borrador", "publicado", "archivado"]
PATRON_ID_TEXTO = PATRON_ID.pattern
# "Módulo 2: Interfaz y navegación" → "Interfaz y navegación" (título corto del catálogo).
PATRON_PREFIJO_MODULO = re.compile(r"^\s*m[oó]dulo\s+\d+\s*[:.\-–—]\s*", re.IGNORECASE)


# --- Ayudantes ---------------------------------------------------------------


def iso(fecha: Optional[datetime]) -> Optional[str]:
    return fecha.isoformat() if fecha else None


def compactar(objeto: Any) -> str:
    # allow_nan=False: NaN no es JSON válido (Oracle lo rechaza con IS JSON).
    return json.dumps(objeto, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def leer_json(texto: Optional[str]) -> Any:
    try:
        return json.loads(texto) if texto else None
    except ValueError:
        return None


def titulo_corto(titulo: str) -> str:
    return PATRON_PREFIJO_MODULO.sub("", titulo or "").strip() or titulo


def rechazo(estado: int, mensaje: str, errores: List[str]) -> JSONResponse:
    """Error con la lista completa de problemas; detail sigue siendo un texto."""
    detalle = f"{mensaje} {errores[0]}" if errores else mensaje
    if len(errores) > 1:
        detalle += f" (y {len(errores) - 1} más)"
    return JSONResponse(status_code=estado, content={"detail": detalle, "errores": errores})


def confirmar(db: Session, ruta: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Otro cambio usó el mismo id al mismo tiempo. Recarga y vuelve a intentarlo."
        )
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, ruta)


def curso_admin(curso: Curso) -> dict:
    return {
        "id": curso.id,
        "numero": curso.numero,
        "titulo": curso.titulo,
        "subtitulo": curso.subtitulo,
        "descripcion": curso.descripcion,
        "nivel": curso.nivel,
        "acento": curso.acento,
        "recurso": recurso_de(curso),
        "orden": curso.orden,
        "estado": curso.estado,
        "actualizado_en": iso(curso.actualizado_en),
    }


def recurso_de(curso: Curso) -> Optional[dict]:
    if not curso.recurso_url:
        return None
    return {"texto": curso.recurso_texto or curso.recurso_url, "url": curso.recurso_url}


def modulo_meta(modulo: Modulo) -> dict:
    return {
        "id": modulo.id,
        "curso_id": modulo.curso_id,
        "nivel_id": modulo.nivel_id,
        "numero": modulo.numero,
        "titulo": modulo.titulo,
        "descripcion": modulo.descripcion,
        "insignia": modulo.insignia,
        "minutos": modulo.minutos,
        "estado": modulo.estado,
        "version": modulo.version,
        "actualizado_en": iso(modulo.actualizado_en),
        "publicado_en": iso(modulo.publicado_en),
    }


def nivel_meta(nivel: Nivel) -> dict:
    return {
        "id": nivel.id,
        "curso_id": nivel.curso_id,
        "numero": nivel.numero,
        "rama": nivel.rama,
        "titulo": nivel.titulo,
        "perfil": nivel.perfil,
        "proyecto": nivel.proyecto,
        "criterio_salida": nivel.criterio_salida,
        "estado": nivel.estado,
        "actualizado_en": iso(nivel.actualizado_en),
    }


def nivel_del_curso(db: Session, nivel_id: str, curso_id: str) -> Nivel:
    """El nivel debe existir y ser del mismo curso que el módulo."""
    nivel = db.get(Nivel, nivel_id)
    if nivel is None:
        raise HTTPException(status_code=404, detail=f"No existe el nivel «{nivel_id}».")
    if nivel.curso_id != curso_id:
        raise HTTPException(
            status_code=409, detail=f"El nivel «{nivel_id}» es del curso «{nivel.curso_id}», no de «{curso_id}»."
        )
    return nivel


def leccion_meta(leccion: Leccion) -> dict:
    return {
        "curso_id": leccion.curso_id,
        "id": leccion.id,
        "modulo_id": leccion.modulo_id,
        "orden": leccion.orden,
        "titulo": leccion.titulo,
        "tipo": leccion.tipo,
        "duracion_segundos": leccion.duracion_segundos,
        "bloqueada": bool(leccion.bloqueada),
        "reemplaza": leccion.reemplaza.split(",") if leccion.reemplaza else [],
        "estado": leccion.estado,
        "version": leccion.version,
        "actualizado_en": iso(leccion.actualizado_en),
        "publicado_en": iso(leccion.publicado_en),
    }


def respuesta_leccion(fila: Leccion, leccion: Any, errores: List[str]) -> dict:
    return {**leccion_meta(fila), "leccion": leccion, "valida": not errores, "errores": errores}


def columnas_leccion(leccion: dict) -> dict:
    """Columnas de LECCIONES que se derivan del JSON de la lección."""
    duracion = leccion.get("durationSeconds")
    reemplaza = leccion.get("replaces")
    if not (isinstance(reemplaza, list) and reemplaza and all(isinstance(x, str) for x in reemplaza)):
        reemplaza = None
    return {
        "titulo": str(leccion.get("title") or "")[:MAX_TITULO],
        "tipo": str(leccion.get("type") or "")[:30],
        "duracion_segundos": duracion
        if isinstance(duracion, int) and not isinstance(duracion, bool) and 0 < duracion <= 9_999_999
        else None,
        "bloqueada": 0 if leccion.get("isLocked") is False else 1,
        "reemplaza": ",".join(reemplaza)[:MAX_REEMPLAZA] if reemplaza else None,
        "contenido": compactar(leccion),
    }


def problemas_para_guardar(leccion: Any) -> List[str]:
    """Lo mínimo para guardar un borrador: id, título y tipo (llenan columnas NOT NULL)."""
    if not isinstance(leccion, dict):
        return ["La lección debe ser un objeto JSON."]
    problemas = []
    leccion_id = leccion.get("id")
    if not isinstance(leccion_id, str) or len(leccion_id) > MAX_ID or not PATRON_ID.match(leccion_id):
        problemas.append("id: usa solo letras, números, guion y guion bajo (máximo 50).")
    titulo = leccion.get("title")
    if not isinstance(titulo, str) or not titulo.strip() or len(titulo) > MAX_TITULO:
        problemas.append(f"title: escribe un título (máximo {MAX_TITULO} caracteres).")
    tipo = leccion.get("type")
    if tipo not in TIPOS_LECCION:
        problemas.append(f"type: usa uno de {', '.join(TIPOS_LECCION)}.")
    try:
        compactar(leccion)
    except ValueError:
        problemas.append("La lección tiene valores que no son JSON válido (NaN o infinito).")
    return problemas


def lecciones_del_modulo(db: Session, modulo_id: str) -> List[Leccion]:
    consulta = (
        select(Leccion)
        .options(defer(Leccion.contenido))
        .where(Leccion.modulo_id == modulo_id)
        .order_by(Leccion.orden, Leccion.id)
    )
    return list(db.scalars(consulta).all())


def renumerar(filas: List[Leccion], momento: datetime) -> None:
    for orden, fila in enumerate(filas, start=1):
        if fila.orden != orden:
            fila.orden = orden
            fila.actualizado_en = momento


def ids_lecciones(db: Session, curso_id: str) -> List[str]:
    return list(db.scalars(select(Leccion.id).where(Leccion.curso_id == curso_id)).all())


def nueva_leccion(curso_id: str, modulo_id: str, orden: int, leccion: dict, estado: str, momento: datetime) -> Leccion:
    return Leccion(
        curso_id=curso_id,
        id=leccion["id"],
        modulo_id=modulo_id,
        orden=orden,
        estado=estado,
        version=1,
        actualizado_en=momento,
        publicado_en=momento if estado == "publicado" else None,
        **columnas_leccion(leccion),
    )


def obtener_curso(db: Session, curso_id: str) -> Curso:
    curso = db.get(Curso, curso_id)
    if curso is None:
        raise HTTPException(status_code=404, detail=f"No existe el curso «{curso_id}».")
    return curso


def obtener_modulo(db: Session, modulo_id: str) -> Modulo:
    modulo = db.get(Modulo, modulo_id)
    if modulo is None:
        raise HTTPException(status_code=404, detail=f"No existe el módulo «{modulo_id}».")
    return modulo


def obtener_leccion(db: Session, curso_id: str, leccion_id: str) -> Leccion:
    fila = db.get(Leccion, (curso_id, leccion_id))
    if fila is None:
        raise HTTPException(status_code=404, detail=f"No existe la lección «{leccion_id}» en el curso «{curso_id}».")
    return fila


def numero_ocupado(db: Session, curso_id: str, numero: int, excepto: Optional[str] = None) -> bool:
    consulta = select(Modulo.id).where(
        Modulo.curso_id == curso_id, Modulo.numero == numero, Modulo.estado != "archivado"
    )
    if excepto:
        consulta = consulta.where(Modulo.id != excepto)
    return db.scalars(consulta).first() is not None


def mismo_id(cuerpo_id: Optional[str], ruta_id: str, que: str) -> None:
    if cuerpo_id is not None and cuerpo_id != ruta_id:
        raise HTTPException(
            status_code=400,
            detail=f"El id de {que} no se puede cambiar (el progreso de los alumnos se guarda con él).",
        )


# --- Catálogo público ----------------------------------------------------------


def _lecciones_publicadas(*columnas):
    """Lecciones publicadas de módulos publicados de cursos publicados."""
    return (
        select(*columnas)
        .join(Modulo, (Modulo.id == Leccion.modulo_id) & (Modulo.curso_id == Leccion.curso_id))
        .join(Curso, Curso.id == Modulo.curso_id)
        .where(Leccion.estado == "publicado", Modulo.estado == "publicado", Curso.estado == "publicado")
    )


def _modulos_visibles(*columnas):
    return (
        select(*columnas)
        .join(Curso, Curso.id == Modulo.curso_id)
        .where(Curso.estado == "publicado", Modulo.estado != "archivado")
    )


def version_catalogo(db: Session) -> str:
    """Huella estable de lo publicado; solo lee metadatos (nunca el JSON de las lecciones)."""
    huella = hashlib.sha1()

    def agregar(filas) -> None:
        for fila in filas:
            texto = "|".join("" if v is None else (v.isoformat() if isinstance(v, datetime) else str(v)) for v in fila)
            huella.update(texto.encode("utf-8") + b"\n")
        huella.update(b"#")

    agregar(
        db.execute(
            select(Curso.id, Curso.orden, Curso.actualizado_en).where(Curso.estado == "publicado").order_by(Curso.id)
        )
    )
    agregar(
        db.execute(
            _modulos_visibles(
                Modulo.id,
                Modulo.curso_id,
                Modulo.nivel_id,
                Modulo.numero,
                Modulo.estado,
                Modulo.version,
                Modulo.actualizado_en,
            ).order_by(Modulo.id)
        )
    )
    agregar(
        db.execute(
            select(Nivel.id, Nivel.estado, Nivel.actualizado_en).where(Nivel.estado == "publicado").order_by(Nivel.id)
        )
    )
    agregar(
        db.execute(
            _lecciones_publicadas(
                Leccion.curso_id, Leccion.id, Leccion.modulo_id, Leccion.orden, Leccion.version, Leccion.actualizado_en
            ).order_by(Leccion.curso_id, Leccion.id)
        )
    )
    return huella.hexdigest()[:20]


def modulo_json(modulo: Modulo, lecciones: List[dict]) -> dict:
    """ModuloJSON: el "module" de frontend/src/data/modulos/*.json."""
    return {
        "id": modulo.id,
        "title": modulo.titulo,
        "description": modulo.descripcion or "",
        "estimatedTimeMinutes": modulo.minutos if modulo.minutos is not None else minutos_estimados(lecciones),
        "order": modulo.numero,
        "lessons": lecciones,
    }


def cursos_catalogo(db: Session) -> List[dict]:
    cursos = db.scalars(select(Curso).where(Curso.estado == "publicado").order_by(Curso.orden, Curso.id)).all()
    if not cursos:
        return []
    modulos = db.scalars(_modulos_visibles(Modulo).order_by(Modulo.numero, Modulo.id)).all()
    lecciones: Dict[str, List[dict]] = defaultdict(list)
    consulta = _lecciones_publicadas(Leccion.modulo_id, Leccion.contenido).order_by(Leccion.orden, Leccion.id)
    for modulo_id, contenido in db.execute(consulta):
        leccion = leer_json(contenido)
        if isinstance(leccion, dict):
            lecciones[modulo_id].append(leccion)

    niveles: Dict[str, List[dict]] = defaultdict(list)
    for nivel in db.scalars(
        select(Nivel).where(Nivel.estado == "publicado").order_by(Nivel.numero, Nivel.rama, Nivel.id)
    ):
        datos = nivel_meta(nivel)
        for campo in ("curso_id", "estado", "actualizado_en"):
            del datos[campo]
        niveles[nivel.curso_id].append(datos)

    por_curso: Dict[str, List[dict]] = defaultdict(list)
    for modulo in modulos:
        # Un módulo sin publicar (o sin lecciones publicadas) aparece como "Próximamente".
        publicado = modulo.estado == "publicado" and lecciones.get(modulo.id)
        por_curso[modulo.curso_id].append(
            {
                "id": modulo.id,
                "numero": modulo.numero,
                "nivel_id": modulo.nivel_id,
                "titulo": titulo_corto(modulo.titulo),
                "insignia": modulo.insignia,
                "contenido": modulo_json(modulo, lecciones[modulo.id]) if publicado else None,
            }
        )
    return [
        {
            "id": curso.id,
            "numero": curso.numero,
            "titulo": curso.titulo,
            "subtitulo": curso.subtitulo,
            "descripcion": curso.descripcion,
            "estado": "disponible",
            "nivel": curso.nivel,
            "acento": curso.acento,
            "recurso": recurso_de(curso),
            "niveles": niveles[curso.id],
            "modulos": por_curso[curso.id],
        }
        for curso in cursos
    ]


def etag_coincide(cabecera: Optional[str], version: str) -> bool:
    """If-None-Match con o sin comillas (también W/"..." y listas separadas por comas)."""
    if not cabecera:
        return False
    for parte in cabecera.split(","):
        valor = parte.strip()
        if valor == "*":
            return True
        if valor.startswith("W/"):
            valor = valor[2:]
        if valor.strip('"') == version:
            return True
    return False


@router.get("/catalogo")
def catalogo(request: Request, db: Session = Depends(obtener_db)):
    try:
        version = version_catalogo(db)
        # no-cache: el navegador guarda la respuesta pero siempre pregunta (304 si no cambió).
        cabeceras = {"ETag": f'"{version}"', "Cache-Control": "no-cache"}
        if etag_coincide(request.headers.get("if-none-match"), version):
            return Response(status_code=304, headers=cabeceras)
        cursos = cursos_catalogo(db)
    except SQLAlchemyError as error:
        raise error_bd(error, "/api/contenido/catalogo", estado=503)
    return JSONResponse({"version": version, "generado_en": iso(ahora()), "cursos": cursos}, headers=cabeceras)


# --- Importar y exportar (también desde herramientas/contenido.py) -------------


def asegurar_cursos_base(db: Session) -> List[str]:
    """Crea los cursos de frontend/src/data/cursos.js que falten (no toca los existentes)."""
    creados = []
    for datos in CURSOS_BASE.values():
        if db.get(Curso, datos["id"]) is None:
            db.add(Curso(**datos, estado="publicado", actualizado_en=ahora()))
            creados.append(datos["id"])
    db.flush()
    return creados


def _aplicar(fila, valores: dict) -> bool:
    cambio = False
    for campo, valor in valores.items():
        if getattr(fila, campo) != valor:
            setattr(fila, campo, valor)
            cambio = True
    return cambio


def importar_modulo(db: Session, datos: Any, curso_id: Optional[str] = None) -> dict:
    """Upsert de un módulo con el formato de archivo ({"module": {...}}). No hace commit.

    Idempotente: lo que no cambió conserva su versión y su fecha (el ETag del
    catálogo no cambia). Estado: "publicado" publica el módulo y sus
    lecciones; "archivado" archiva el módulo; "borrador"/"revision" dejan lo
    que ya existía como estaba (importar nunca despublica). Las lecciones
    de la base que no vienen en el archivo no se tocan.
    """
    errores = validar_modulo(datos)
    if errores:
        raise ContenidoInvalido(errores)
    modulo = modulo_de(datos)
    curso_id = modulo.get("curso") or curso_id
    if not curso_id:
        raise ContenidoInvalido(["module: falta «curso» (blender, aframe...)"])
    if db.get(Curso, curso_id) is None:
        raise ContenidoInvalido([f"module: no existe el curso «{curso_id}»; créalo antes de importar"])

    estado_archivo = modulo.get("estado") or "borrador"
    momento = ahora()
    valores = {
        "curso_id": curso_id,
        "numero": modulo["order"],
        "titulo": modulo["title"],
        "descripcion": modulo.get("description") or None,
        "insignia": modulo.get("insignia") or None,
        "minutos": modulo.get("estimatedTimeMinutes"),
    }
    # «nivel» es opcional: sin él se conserva el nivel que ya tenga el módulo en la base.
    nivel_id = modulo.get("nivel")
    if nivel_id:
        nivel = db.get(Nivel, nivel_id)
        if nivel is None or nivel.curso_id != curso_id:
            raise ContenidoInvalido(
                [f"module: no existe el nivel «{nivel_id}» en el curso «{curso_id}» (sembrar-niveles lo crea)"]
            )
        valores["nivel_id"] = nivel_id
    fila = db.get(Modulo, modulo["id"])
    if fila is not None and fila.curso_id != curso_id:
        raise ContenidoInvalido([f"module: el módulo «{fila.id}» ya existe en el curso «{fila.curso_id}»"])
    if fila is None:
        estado = estado_archivo if estado_archivo in ("publicado", "archivado") else "borrador"
        fila = Modulo(
            id=modulo["id"],
            estado=estado,
            version=1,
            actualizado_en=momento,
            publicado_en=momento if estado == "publicado" else None,
            **valores,
        )
        db.add(fila)
        resultado_modulo = "creado"
    else:
        cambio = _aplicar(fila, valores)
        if estado_archivo in ("publicado", "archivado") and fila.estado != estado_archivo:
            fila.estado = estado_archivo
            cambio = True
        if fila.estado == "publicado" and fila.publicado_en is None:
            fila.publicado_en = momento
        if cambio:
            fila.version += 1
            fila.actualizado_en = momento
        resultado_modulo = "actualizado" if cambio else "sin cambios"
    db.flush()

    publicar = estado_archivo == "publicado"
    resumen = {"nuevas": 0, "actualizadas": 0, "sin_cambios": 0}
    en_archivo = set()
    for orden, leccion in enumerate(modulo["lessons"], start=1):
        en_archivo.add(leccion["id"])
        existente = db.get(Leccion, (curso_id, leccion["id"]))
        if existente is None:
            db.add(nueva_leccion(curso_id, fila.id, orden, leccion, "publicado" if publicar else "borrador", momento))
            resumen["nuevas"] += 1
            continue
        cambio = _aplicar(existente, {"modulo_id": fila.id, "orden": orden})
        if _aplicar(existente, columnas_leccion(leccion)):
            existente.version += 1
            cambio = True
        if publicar and existente.estado != "publicado":
            existente.estado = "publicado"
            existente.publicado_en = existente.publicado_en or momento
            cambio = True
        if cambio:
            existente.actualizado_en = momento
            resumen["actualizadas"] += 1
        else:
            resumen["sin_cambios"] += 1
    db.flush()

    # Las lecciones que solo están en la base quedan después de las del archivo.
    restantes = [f for f in lecciones_del_modulo(db, fila.id) if f.id not in en_archivo]
    for orden, restante in enumerate(restantes, start=len(en_archivo) + 1):
        if restante.orden != orden:
            restante.orden = orden
            restante.actualizado_en = momento
    db.flush()
    if '"blender_practice"' in json.dumps(modulo.get("lessons") or [], ensure_ascii=False):
        # Solo los módulos con prácticas de Blender tocan PRACTICAS (sql/007).
        from api.addon import enlazar_lecciones

        enlazar_lecciones(db)
    return {
        "modulo_id": fila.id,
        "curso_id": curso_id,
        "estado": fila.estado,
        "modulo": resultado_modulo,
        "lecciones_nuevas": resumen["nuevas"],
        "lecciones_actualizadas": resumen["actualizadas"],
        "lecciones_sin_cambios": resumen["sin_cambios"],
        "solo_en_base": [f.id for f in restantes],
    }


def exportar_modulo(db: Session, modulo_id: str, borradores: bool = False) -> Optional[dict]:
    """Módulo en el formato de archivo de frontend/src/data/modulos ({"module": ...}).

    De un módulo publicado se exportan sus lecciones publicadas (lo que ve el
    alumno); con borradores=True, o si el módulo no está publicado, también
    las lecciones en borrador. Las archivadas nunca.
    """
    modulo = db.get(Modulo, modulo_id)
    if modulo is None:
        return None
    estados = ["publicado"] if modulo.estado == "publicado" and not borradores else ["borrador", "publicado"]
    consulta = (
        select(Leccion.contenido)
        .where(Leccion.modulo_id == modulo.id, Leccion.estado.in_(estados))
        .order_by(Leccion.orden, Leccion.id)
    )
    lecciones = [x for x in (leer_json(c) for c in db.scalars(consulta)) if isinstance(x, dict)]
    base = modulo_json(modulo, lecciones)
    del base["lessons"]
    return {
        "module": {
            **base,
            "curso": modulo.curso_id,
            **({"nivel": modulo.nivel_id} if modulo.nivel_id else {}),
            "insignia": modulo.insignia,
            "estado": modulo.estado,
            "lessons": lecciones,
        }
    }


# --- Administración: lectura ---------------------------------------------------


@router.get("/admin/arbol")
def arbol(db: Session = Depends(obtener_db), _: Usuario = Depends(lector)):
    """Cursos → módulos → lecciones con metadatos y estado (sin el JSON de las lecciones)."""
    cursos = db.scalars(select(Curso).order_by(Curso.orden, Curso.id)).all()
    modulos = db.scalars(select(Modulo).order_by(Modulo.numero, Modulo.id)).all()
    lecciones = db.scalars(
        select(Leccion).options(defer(Leccion.contenido)).order_by(Leccion.orden, Leccion.id)
    ).all()
    # Qué lecciones traen la práctica en Blender (el panel marca el cierre del
    # módulo y avisa si falta). Solo el id y el JSON, sin el resto de la fila.
    con_practica = {
        (curso_id, id_)
        for curso_id, id_, contenido in db.execute(
            select(Leccion.curso_id, Leccion.id, Leccion.contenido).where(Leccion.estado != "archivado")
        )
        if es_practica_blender(leer_json(contenido))
    }
    por_modulo: Dict[str, List[dict]] = defaultdict(list)
    for leccion in lecciones:
        por_modulo[leccion.modulo_id].append({**leccion_meta(leccion), "practica_blender": (leccion.curso_id, leccion.id) in con_practica})
    por_curso: Dict[str, List[dict]] = defaultdict(list)
    for modulo in modulos:
        por_curso[modulo.curso_id].append({**modulo_meta(modulo), "lecciones": por_modulo[modulo.id]})
    niveles: Dict[str, List[dict]] = defaultdict(list)
    for nivel in db.scalars(select(Nivel).order_by(Nivel.numero, Nivel.rama, Nivel.id)):
        niveles[nivel.curso_id].append(nivel_meta(nivel))
    return {
        "cursos": [
            {**curso_admin(curso), "niveles": niveles[curso.id], "modulos": por_curso[curso.id]} for curso in cursos
        ]
    }


@router.get("/plantillas")
def plantillas(_: Usuario = Depends(lector)):
    return {
        "formula": FORMULA,
        "ritmo": RITMO,
        "bloques": EJEMPLOS_BLOQUES,
        "lecciones": LECCIONES_PLANTILLA,
        "tipos": {
            "lecciones": list(TIPOS_LECCION),
            "bloques": list(BLOQUES_CONTENIDO),
            "interactivos": list(BLOQUES_INTERACTIVOS),
        },
    }


class SolicitudValidar(BaseModel):
    leccion: Optional[Any] = None
    modulo: Optional[Any] = None


@router.post("/validar")
def validar(cuerpo: SolicitudValidar, _: Usuario = Depends(lector)):
    """Valida sin guardar una lección (o un módulo completo con «modulo»)."""
    if cuerpo.modulo is not None:
        errores = validar_modulo(cuerpo.modulo)
    elif cuerpo.leccion is not None:
        errores = validar_leccion(cuerpo.leccion)
    else:
        raise HTTPException(status_code=422, detail="Envía «leccion» (o «modulo») para validar.")
    return {"valida": not errores, "errores": errores}


@router.get("/modulos/{modulo_id}/exportar")
def exportar(
    modulo_id: str,
    borradores: bool = False,
    db: Session = Depends(obtener_db),
    _: Usuario = Depends(lector),
):
    datos = exportar_modulo(db, modulo_id, borradores=borradores)
    if datos is None:
        raise HTTPException(status_code=404, detail=f"No existe el módulo «{modulo_id}».")
    return datos


@router.get("/lecciones/{curso_id}/{leccion_id}")
def leer_leccion(curso_id: str, leccion_id: str, db: Session = Depends(obtener_db), _: Usuario = Depends(lector)):
    fila = obtener_leccion(db, curso_id, leccion_id)
    return {**leccion_meta(fila), "leccion": leer_json(fila.contenido)}


# --- Administración: cursos ----------------------------------------------------


class Recurso(BaseModel):
    texto: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=300)


class CursoNuevo(BaseModel):
    id: str = Field(min_length=1, max_length=MAX_ID, pattern=PATRON_ID_TEXTO)
    titulo: str = Field(min_length=1, max_length=100)
    numero: Optional[str] = Field(default=None, max_length=5)
    subtitulo: Optional[str] = Field(default=None, max_length=150)
    descripcion: Optional[str] = Field(default=None, max_length=1000)
    nivel: Optional[str] = Field(default=None, max_length=30)
    acento: Optional[str] = Field(default=None, max_length=20)
    recurso: Optional[Recurso] = None
    orden: Optional[int] = Field(default=None, ge=0, le=99999)
    # Un curso nuevo nace oculto: se publica con PUT {estado: "publicado"}.
    estado: Estado = "borrador"


class CambiosCurso(BaseModel):
    id: Optional[str] = None
    titulo: Optional[str] = Field(default=None, min_length=1, max_length=100)
    numero: Optional[str] = Field(default=None, max_length=5)
    subtitulo: Optional[str] = Field(default=None, max_length=150)
    descripcion: Optional[str] = Field(default=None, max_length=1000)
    nivel: Optional[str] = Field(default=None, max_length=30)
    acento: Optional[str] = Field(default=None, max_length=20)
    recurso: Optional[Recurso] = None
    orden: Optional[int] = Field(default=None, ge=0, le=99999)
    estado: Optional[Estado] = None


def _aplicar_curso(curso: Curso, cambios: dict) -> None:
    for campo, valor in cambios.items():
        if campo == "id" or (campo in ("titulo", "estado", "orden") and valor is None):
            continue
        if campo == "recurso":
            curso.recurso_texto = valor["texto"] if valor else None
            curso.recurso_url = valor["url"] if valor else None
        else:
            setattr(curso, campo, valor)


@router.post("/cursos", status_code=201)
def crear_curso(cuerpo: CursoNuevo, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    if db.get(Curso, cuerpo.id) is not None:
        raise HTTPException(status_code=409, detail=f"Ya existe el curso «{cuerpo.id}».")
    orden = cuerpo.orden
    if orden is None:
        orden = (db.scalar(select(func.max(Curso.orden))) or 0) + 1
    curso = Curso(id=cuerpo.id, titulo=cuerpo.titulo, orden=orden, estado=cuerpo.estado, actualizado_en=ahora())
    _aplicar_curso(curso, cuerpo.model_dump(exclude={"id", "titulo", "orden", "estado"}))
    db.add(curso)
    confirmar(db, "/api/contenido/cursos")
    return curso_admin(curso)


@router.put("/cursos/{curso_id}")
def editar_curso(curso_id: str, cuerpo: CambiosCurso, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    curso = obtener_curso(db, curso_id)
    mismo_id(cuerpo.id, curso_id, "un curso")
    _aplicar_curso(curso, cuerpo.model_dump(exclude_unset=True))
    curso.actualizado_en = ahora()
    confirmar(db, "/api/contenido/cursos")
    return curso_admin(curso)


# --- Administración: módulos ---------------------------------------------------


class ModuloNuevo(BaseModel):
    curso_id: str = Field(min_length=1, max_length=MAX_ID)
    titulo: str = Field(min_length=1, max_length=200)
    descripcion: Optional[str] = Field(default=None, max_length=1000)
    insignia: Optional[str] = Field(default=None, max_length=80)
    minutos: Optional[int] = Field(default=None, ge=0, le=10000)
    id: Optional[str] = Field(default=None, min_length=1, max_length=MAX_ID, pattern=PATRON_ID_TEXTO)
    numero: Optional[int] = Field(default=None, ge=1, le=999)
    nivel_id: Optional[str] = Field(default=None, min_length=1, max_length=MAX_ID)
    generar_esqueleto: bool = False


class CambiosModulo(BaseModel):
    id: Optional[str] = None
    titulo: Optional[str] = Field(default=None, min_length=1, max_length=200)
    descripcion: Optional[str] = Field(default=None, max_length=1000)
    insignia: Optional[str] = Field(default=None, max_length=80)
    minutos: Optional[int] = Field(default=None, ge=0, le=10000)
    numero: Optional[int] = Field(default=None, ge=1, le=999)
    nivel_id: Optional[str] = Field(default=None, max_length=MAX_ID)  # null quita el nivel


def modulo_con_lecciones(db: Session, modulo: Modulo) -> dict:
    return {**modulo_meta(modulo), "lecciones": [leccion_meta(f) for f in lecciones_del_modulo(db, modulo.id)]}


@router.post("/modulos", status_code=201)
def crear_modulo(cuerpo: ModuloNuevo, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    curso = obtener_curso(db, cuerpo.curso_id)
    if cuerpo.nivel_id:
        nivel_del_curso(db, cuerpo.nivel_id, curso.id)
    numero = cuerpo.numero
    if numero is None:
        numero = (db.scalar(select(func.max(Modulo.numero)).where(Modulo.curso_id == curso.id)) or 0) + 1
    elif numero_ocupado(db, curso.id, numero):
        raise HTTPException(status_code=409, detail=f"Ya hay un módulo {numero} en el curso «{curso.id}».")

    modulo_id = cuerpo.id
    if modulo_id is None:
        # mod_<curso>_<NNN>; si ya existe (por ejemplo, archivado) se usa el siguiente libre.
        sufijo = numero
        modulo_id = f"mod_{curso.id}_{sufijo:03d}"
        while db.get(Modulo, modulo_id) is not None:
            sufijo += 1
            modulo_id = f"mod_{curso.id}_{sufijo:03d}"
        if len(modulo_id) > MAX_ID:
            raise HTTPException(status_code=422, detail="El id generado es demasiado largo: envía un «id» más corto.")
    elif db.get(Modulo, modulo_id) is not None:
        raise HTTPException(status_code=409, detail=f"Ya existe el módulo «{modulo_id}». Los ids no se reutilizan.")

    momento = ahora()
    modulo = Modulo(
        id=modulo_id,
        curso_id=curso.id,
        nivel_id=cuerpo.nivel_id,
        numero=numero,
        titulo=cuerpo.titulo,
        descripcion=cuerpo.descripcion,
        insignia=cuerpo.insignia,
        minutos=cuerpo.minutos,
        estado="borrador",
        version=1,
        actualizado_en=momento,
    )
    db.add(modulo)
    db.flush()
    if cuerpo.generar_esqueleto:
        prefijo, inicio, ancho = siguiente_numeracion(ids_lecciones(db, curso.id), curso.id)
        for orden, leccion in enumerate(generar_esqueleto(curso.id, modulo_id, prefijo, inicio, ancho), start=1):
            db.add(nueva_leccion(curso.id, modulo_id, orden, leccion, "borrador", momento))
    confirmar(db, "/api/contenido/modulos")
    return modulo_con_lecciones(db, modulo)


@router.put("/modulos/{modulo_id}")
def editar_modulo(
    modulo_id: str, cuerpo: CambiosModulo, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)
):
    modulo = obtener_modulo(db, modulo_id)
    mismo_id(cuerpo.id, modulo_id, "un módulo")
    cambios = cuerpo.model_dump(exclude_unset=True, exclude={"id"})
    if cambios.get("numero") is not None and numero_ocupado(db, modulo.curso_id, cambios["numero"], excepto=modulo.id):
        raise HTTPException(status_code=409, detail=f"Ya hay un módulo {cambios['numero']} en este curso.")
    if cambios.get("nivel_id"):
        nivel_del_curso(db, cambios["nivel_id"], modulo.curso_id)
    for campo, valor in cambios.items():
        if campo in ("titulo", "numero") and valor is None:
            continue
        setattr(modulo, campo, valor)
    modulo.version += 1
    modulo.actualizado_en = ahora()
    confirmar(db, "/api/contenido/modulos")
    return modulo_con_lecciones(db, modulo)


@router.post("/modulos/{modulo_id}/publicar")
def publicar_modulo(modulo_id: str, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    """Publica el módulo y sus lecciones en borrador que validan; las inválidas se omiten."""
    modulo = obtener_modulo(db, modulo_id)
    filas = db.scalars(
        select(Leccion)
        .where(Leccion.modulo_id == modulo.id, Leccion.estado != "archivado")
        .order_by(Leccion.orden, Leccion.id)
    ).all()
    momento = ahora()
    publicadas: List[str] = []
    omitidas: List[dict] = []
    contenidos: List[Any] = []
    for fila in filas:
        leccion = leer_json(fila.contenido)
        if fila.estado == "borrador":
            errores = validar_leccion(leccion) if leccion is not None else ["el contenido guardado no es JSON válido"]
            if errores:
                omitidas.append({"id": fila.id, "errores": errores})
                continue
            fila.estado = "publicado"
            fila.publicado_en = fila.publicado_en or momento
            fila.actualizado_en = momento
            publicadas.append(fila.id)
        contenidos.append(leccion)

    if not contenidos:
        db.rollback()
        errores = [f"{o['id']}: {e}" for o in omitidas for e in o["errores"]]
        return rechazo(422, "El módulo no tiene lecciones válidas para publicar.", errores or ["agrega al menos una lección"])
    errores = validar_modulo(modulo_json(modulo, contenidos))
    if errores:
        db.rollback()
        return rechazo(422, "El módulo no es válido.", errores)

    modulo.estado = "publicado"
    modulo.publicado_en = modulo.publicado_en or momento
    modulo.version += 1
    modulo.actualizado_en = momento
    confirmar(db, f"/api/contenido/modulos/{modulo_id}/publicar")
    return {**modulo_con_lecciones(db, modulo), "publicadas": publicadas, "omitidas": omitidas}


@router.post("/modulos/{modulo_id}/archivar")
def archivar_modulo(modulo_id: str, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    """Oculta el módulo del catálogo. Sus lecciones y el progreso de los alumnos se conservan."""
    modulo = obtener_modulo(db, modulo_id)
    if modulo.estado != "archivado":
        modulo.estado = "archivado"
        modulo.version += 1
        modulo.actualizado_en = ahora()
        confirmar(db, f"/api/contenido/modulos/{modulo_id}/archivar")
    return modulo_con_lecciones(db, modulo)


# --- Administración: lecciones -------------------------------------------------


class LeccionNueva(BaseModel):
    curso_id: str = Field(min_length=1, max_length=MAX_ID)
    modulo_id: str = Field(min_length=1, max_length=MAX_ID)
    leccion: Dict[str, Any]
    posicion: Optional[int] = Field(default=None, ge=1, le=9999)  # 1 = primera del módulo


class CambiosLeccion(BaseModel):
    leccion: Dict[str, Any]


class Mover(BaseModel):
    orden: int = Field(ge=1, le=9999)  # nueva posición dentro del módulo (1 = primera)


@router.post("/lecciones", status_code=201)
def crear_leccion(cuerpo: LeccionNueva, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    modulo = obtener_modulo(db, cuerpo.modulo_id)
    if modulo.curso_id != cuerpo.curso_id:
        raise HTTPException(status_code=422, detail=f"El módulo «{modulo.id}» es del curso «{modulo.curso_id}».")
    leccion = dict(cuerpo.leccion)
    if not leccion.get("id"):
        prefijo, numero, ancho = siguiente_numeracion(ids_lecciones(db, modulo.curso_id), modulo.curso_id)
        leccion = {"id": f"{prefijo}{numero:0{ancho}d}", **{k: v for k, v in leccion.items() if k != "id"}}
    elif db.get(Leccion, (modulo.curso_id, leccion["id"])) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe la lección «{leccion['id']}» en el curso «{modulo.curso_id}». Los ids no se reutilizan.",
        )
    problemas = problemas_para_guardar(leccion)
    if problemas:
        return rechazo(422, "No se puede guardar la lección:", problemas)
    errores = validar_leccion(leccion)

    momento = ahora()
    filas = lecciones_del_modulo(db, modulo.id)
    fila = nueva_leccion(modulo.curso_id, modulo.id, len(filas) + 1, leccion, "borrador", momento)
    posicion = min(cuerpo.posicion or len(filas) + 1, len(filas) + 1)
    filas.insert(posicion - 1, fila)
    db.add(fila)
    renumerar(filas, momento)
    confirmar(db, "/api/contenido/lecciones")
    return respuesta_leccion(fila, leccion, errores)


@router.put("/lecciones/{curso_id}/{leccion_id}")
def editar_leccion(
    curso_id: str,
    leccion_id: str,
    cuerpo: CambiosLeccion,
    db: Session = Depends(obtener_db),
    _: Usuario = Depends(editor),
):
    fila = obtener_leccion(db, curso_id, leccion_id)
    leccion = dict(cuerpo.leccion)
    if leccion.get("id"):
        mismo_id(leccion["id"], leccion_id, "una lección")
    else:
        leccion = {"id": leccion_id, **{k: v for k, v in leccion.items() if k != "id"}}
    problemas = problemas_para_guardar(leccion)
    if problemas:
        return rechazo(422, "No se puede guardar la lección:", problemas)
    errores = validar_leccion(leccion)
    if errores and fila.estado == "publicado":
        # Lo publicado lo ven los alumnos en este momento: siempre debe validar.
        return rechazo(422, "La lección está publicada y el cambio no es válido:", errores)
    _aplicar(fila, columnas_leccion(leccion))
    fila.version += 1
    fila.actualizado_en = ahora()
    confirmar(db, "/api/contenido/lecciones")
    return respuesta_leccion(fila, leccion, errores)


@router.post("/lecciones/{curso_id}/{leccion_id}/publicar")
def publicar_leccion(curso_id: str, leccion_id: str, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    fila = obtener_leccion(db, curso_id, leccion_id)
    leccion = leer_json(fila.contenido)
    errores = validar_leccion(leccion) if leccion is not None else ["el contenido guardado no es JSON válido"]
    if errores:
        return rechazo(422, "La lección no se puede publicar:", errores)
    if fila.estado != "publicado":
        momento = ahora()
        fila.estado = "publicado"
        fila.publicado_en = fila.publicado_en or momento
        fila.actualizado_en = momento
        confirmar(db, "/api/contenido/lecciones/publicar")
    return respuesta_leccion(fila, leccion, [])


@router.post("/lecciones/{curso_id}/{leccion_id}/archivar")
def archivar_leccion(curso_id: str, leccion_id: str, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)):
    """Saca la lección del catálogo sin borrarla: el progreso de los alumnos se conserva."""
    fila = obtener_leccion(db, curso_id, leccion_id)
    if fila.estado != "archivado":
        fila.estado = "archivado"
        fila.actualizado_en = ahora()
        confirmar(db, "/api/contenido/lecciones/archivar")
    return leccion_meta(fila)


@router.post("/lecciones/{curso_id}/{leccion_id}/mover")
def mover_leccion(
    curso_id: str, leccion_id: str, cuerpo: Mover, db: Session = Depends(obtener_db), _: Usuario = Depends(editor)
):
    fila = obtener_leccion(db, curso_id, leccion_id)
    filas = [f for f in lecciones_del_modulo(db, fila.modulo_id) if f is not fila]
    filas.insert(min(cuerpo.orden, len(filas) + 1) - 1, fila)
    renumerar(filas, ahora())
    confirmar(db, "/api/contenido/lecciones/mover")
    return {"modulo_id": fila.modulo_id, "lecciones": [leccion_meta(f) for f in filas]}
