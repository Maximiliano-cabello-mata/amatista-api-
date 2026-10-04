"""API del add-on de Blender y del motor de prácticas (/api/addon/v1).

Vínculo (conectar Blender con una cuenta, como en un televisor):
- POST /vinculos .................. el add-on pide un código (sin sesión).
- POST /vinculos/confirmar ........ el alumno escribe el código en la PWA.
- POST /vinculos/{id}/estado ...... el add-on pregunta; cuando está «listo»
                                    recibe una sesión propia (una sola vez).
- GET /yo, POST /salir, GET/DELETE /dispositivos.

Prácticas (tabla PRACTICAS, historial en PRACTICA_VERSIONES):
- GET /practicas, GET /practicas/{id}: los alumnos ven solo la versión
  publicada; profesores y administradores, también los borradores.
- POST /practicas (profesor o admin, desde Amatista Author): compila con el
  motor y registra una versión nueva. NUNCA publica.
- POST /practicas/{id}/publicar y /archivar (admin).
- POST /practicas/sincronizar (admin): registra las de practices/blender/.

Progreso (PROGRESO_PRACTICAS):
- POST /practicas/{id}/abrir: la plataforma (o Blender) marca la práctica
  como la actual; el add-on la abre sola con GET /practica-actual.
- POST /intentos: el add-on manda la foto de la escena; el servidor la
  evalúa con el mismo motor y guarda el mejor resultado. Al completarla
  marca la lección, sube las habilidades (como mucho a «con_pistas»: la
  autonomía la confirma el proyecto del nivel) y anota activity_submitted.
- GET /mi-progreso.

Descargas (sin binarios en el repositorio: se arman al vuelo):
- GET /descargas/{windows|macos|linux}: paquete con instalador. Con sesión
  trae un vínculo de un solo uso: el add-on queda conectado al abrir Blender.
- GET /extension.zip y GET /extensiones/index.json (repositorio remoto de
  extensiones de Blender, con avisos de actualización).
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api import progreso as api_progreso
from api.auth import abrir_sesion
from api.comun import error_bd, usuario_publico
from api.contenido import confirmar, iso, rechazo
from api.dependencias import PREFIJO_ADDON, requiere_rol, usuario_opcional, usuario_requerido
from api.limites import limitar
from contenido import motor
from database.conexion import obtener_db
from database.modelos import (
    AddonVinculo,
    Curso,
    EventoAprendizaje,
    Habilidad,
    HabilidadAlumno,
    Leccion,
    Practica,
    PracticaVersion,
    ProgresoPractica,
    Sesion,
    Usuario,
    ahora,
)
from seguridad import hash_token

router = APIRouter(prefix="/api/addon/v1", tags=["addon"])

desarrollador = requiere_rol("profesor", "admin")
editor = requiere_rol("admin")

# Las sesiones del add-on se reconocen por su dispositivo y solo sirven aquí
# y en /api/blender (ver api/dependencias.py).
PREFIJO_DISPOSITIVO = PREFIJO_ADDON
DURACION_VINCULO = timedelta(minutes=10)
DURACION_VINCULO_PAQUETE = timedelta(days=7)
# Sin I, L, O, 0 ni 1: se dicta y se copia sin confusiones.
ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PATRON_CODIGO = re.compile(r"^[A-Z0-9]{4}-?[A-Z0-9]{4}$")
SISTEMAS = ("windows", "macos", "linux")
MAX_DEFINICION = 200_000  # caracteres de una practice.json
ORDEN_AUTONOMIA = {"sin_practicar": 0, "con_guia": 1, "con_pistas": 2, "autonoma": 3}
# Una práctica sola no demuestra autonomía: eso lo confirma el proyecto del nivel.
TOPE_HABILIDAD = "con_pistas"
MAX_OBJETIVOS_TEXTO = 1000

LIMITE_VINCULOS = limitar(20)
LIMITE_CONSULTAS = limitar(60)
LIMITE_CONFIRMAR = limitar(10)
LIMITE_INTENTOS = limitar(120)
LIMITE_DESCARGAS = limitar(20)


# --- Ayudantes -------------------------------------------------------------------


def es_equipo(usuario: Optional[Usuario]) -> bool:
    return usuario is not None and usuario.rol in ("profesor", "admin")


def compactar(objeto: Any) -> str:
    return json.dumps(objeto, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def leer(texto: Optional[str]) -> Any:
    try:
        return json.loads(texto) if texto else None
    except ValueError:
        return None


def url_api(request: Request) -> str:
    """Dirección pública de esta API (AMATISTA_URL_API detrás de Caddy)."""
    return (os.getenv("AMATISTA_URL_API") or str(request.base_url)).rstrip("/")


def origen_permitido(origen: str) -> bool:
    if re.fullmatch(r"https?://(localhost|127\.0\.0\.1)(:\d+)?", origen):
        return True
    return origen in [o.strip().rstrip("/") for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]


def url_pwa(request: Request) -> str:
    """Dirección de la PWA: AMATISTA_URL_PWA o el origen (solo si está en CORS)."""
    configurada = os.getenv("AMATISTA_URL_PWA", "").strip().rstrip("/")
    if configurada:
        return configurada
    origen = request.headers.get("origin", "").rstrip("/")
    return origen if origen and origen_permitido(origen) else ""


def nuevo_codigo() -> str:
    letras = "".join(secrets.choice(ALFABETO) for _ in range(8))
    return f"{letras[:4]}-{letras[4:]}"


def normalizar_codigo(codigo: str) -> Optional[str]:
    limpio = re.sub(r"[\s-]", "", codigo or "").upper()
    if not PATRON_CODIGO.match(limpio):
        return None
    return f"{limpio[:4]}-{limpio[4:]}"


def purgar_vinculos(db: Session, momento: datetime) -> None:
    """Los vínculos vencidos o canjeados hace más de un día no sirven para nada."""
    db.execute(delete(AddonVinculo).where(AddonVinculo.expira_en < momento - timedelta(days=1)))


def crear_vinculo(db: Session, dispositivo: str, momento: datetime, duracion: timedelta,
                  usuario: Optional[Usuario] = None) -> tuple:
    """(fila, secreto en claro). El secreto solo existe en la respuesta."""
    purgar_vinculos(db, momento)
    for _ in range(5):
        codigo = nuevo_codigo()
        if db.scalar(select(AddonVinculo.id).where(AddonVinculo.codigo == codigo)) is None:
            break
    secreto = secrets.token_urlsafe(24)
    fila = AddonVinculo(
        id=str(uuid.uuid4()),
        codigo=codigo,
        secreto_hash=hash_token(secreto),
        usuario_id=usuario.id if usuario else None,
        dispositivo=dispositivo[:200],
        estado="listo" if usuario else "pendiente",
        creado_en=momento,
        expira_en=momento + duracion,
    )
    db.add(fila)
    return fila, secreto


def dispositivo_addon(texto: Optional[str]) -> str:
    texto = " ".join((texto or "").split())[:180]
    if not texto.startswith(PREFIJO_DISPOSITIVO):
        texto = f"{PREFIJO_DISPOSITIVO} {texto}".strip()
    return texto[:200]


def visible(practica: Optional[Practica], usuario: Optional[Usuario]) -> bool:
    if practica is None:
        return False
    if es_equipo(usuario):
        return True
    return practica.estado == "publicado" and practica.version_publicada is not None


def obtener_practica(db: Session, practica_id: str, usuario: Optional[Usuario]) -> Practica:
    practica = db.get(Practica, practica_id)
    if not visible(practica, usuario):
        raise HTTPException(status_code=404, detail="No existe esa práctica o todavía no está publicada.")
    return practica


def version_para(practica: Practica, usuario: Optional[Usuario]) -> int:
    """Los alumnos usan la versión publicada; el equipo, la última subida."""
    if es_equipo(usuario) or practica.version_publicada is None:
        return practica.version
    return practica.version_publicada


def definicion_de(db: Session, practica: Practica, version: int) -> Dict[str, Any]:
    if version == practica.version:
        return leer(practica.definicion)
    fila = db.get(PracticaVersion, (practica.id, version))
    if fila is None:
        raise HTTPException(status_code=404, detail=f"No existe la versión {version} de la práctica.")
    return leer(fila.definicion)


def progreso_meta(fila: Optional[ProgresoPractica]) -> Optional[dict]:
    if fila is None:
        return None
    return {
        "practica_id": fila.practica_id,
        "version": fila.version,
        "progreso": fila.progreso,
        "completada": bool(fila.completada),
        "autonomia": fila.autonomia,
        "pistas": fila.pistas,
        "correcciones": fila.correcciones,
        "intentos": fila.intentos,
        "paso_actual": fila.paso_actual,
        "objetivos": leer(fila.objetivos) or [],
        "version_blender": fila.version_blender,
        "abierta_en": iso(fila.abierta_en),
        "completada_en": iso(fila.completada_en),
        "actualizado_en": iso(fila.actualizado_en),
    }


def practica_meta(practica: Practica, definicion: Dict[str, Any], version: int,
                  progreso: Optional[ProgresoPractica] = None, equipo: bool = False) -> dict:
    meta = {
        "id": practica.id,
        "titulo": definicion.get("title") or practica.titulo,
        "descripcion": definicion.get("description") or "",
        "nivel": definicion.get("level") or practica.nivel,
        "minutos": definicion.get("estimatedMinutes"),
        "version": version,
        "curso_id": practica.curso_id,
        "leccion_id": practica.leccion_id,
        "pasos": [{"id": t.get("id"), "titulo": t.get("title")} for t in definicion.get("targets", [])
                  if isinstance(t, dict) and not t.get("optional")],
        "mi_progreso": progreso_meta(progreso),
    }
    if equipo:
        meta.update(
            estado=practica.estado,
            version_ultima=practica.version,
            version_publicada=practica.version_publicada,
            origen=practica.origen,
            actualizado_en=iso(practica.actualizado_en),
            publicado_en=iso(practica.publicado_en),
        )
    return meta


def cerrar(db: Session, ruta: str) -> None:
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, ruta, estado=503)


# --- Estado y vínculo ------------------------------------------------------------


@router.get("/estado")
def estado(request: Request):
    """Lo que el add-on necesita saber antes de nada (público)."""
    servidor = url_api(request)
    return {
        "api": "v1",
        "version_addon": motor.VERSION_ADDON,
        "version_motor": motor.VERSION_MOTOR,
        "blender_minimo": motor.BLENDER_MINIMO,
        "descargas": {s: f"{servidor}/api/addon/v1/descargas/{s}" for s in SISTEMAS},
        "repositorio": f"{servidor}/api/addon/v1/extensiones/index.json",
    }


class VinculoNuevo(BaseModel):
    dispositivo: Optional[str] = Field(default=None, max_length=200)


@router.post("/vinculos", status_code=201, dependencies=[Depends(LIMITE_VINCULOS)])
def pedir_vinculo(cuerpo: VinculoNuevo, request: Request, db: Session = Depends(obtener_db)):
    momento = ahora()
    fila, secreto = crear_vinculo(db, dispositivo_addon(cuerpo.dispositivo), momento, DURACION_VINCULO)
    cerrar(db, "/api/addon/v1/vinculos")
    pwa = url_pwa(request)
    return {
        "vinculo_id": fila.id,
        "codigo": fila.codigo,
        "secreto": secreto,
        "expira_en_segundos": int(DURACION_VINCULO.total_seconds()),
        "url_vincular": f"{pwa}/#/vincular?codigo={fila.codigo}" if pwa else "",
    }


class Confirmacion(BaseModel):
    codigo: str = Field(min_length=8, max_length=12)


@router.post("/vinculos/confirmar", dependencies=[Depends(LIMITE_CONFIRMAR)])
def confirmar_vinculo(cuerpo: Confirmacion, db: Session = Depends(obtener_db),
                      usuario: Usuario = Depends(usuario_requerido)):
    codigo = normalizar_codigo(cuerpo.codigo)
    fila = db.scalar(select(AddonVinculo).where(AddonVinculo.codigo == codigo)) if codigo else None
    if fila is None or fila.expira_en <= ahora() or fila.estado == "canjeado":
        raise HTTPException(status_code=404, detail="Ese código no existe o ya venció. Pide uno nuevo en Blender.")
    if fila.estado == "listo" and fila.usuario_id != usuario.id:
        raise HTTPException(status_code=409, detail="Ese código ya lo usó otra cuenta.")
    fila.usuario_id = usuario.id
    fila.estado = "listo"
    confirmar(db, "/api/addon/v1/vinculos/confirmar")
    return {"estado": "listo", "dispositivo": fila.dispositivo}


class ConsultaVinculo(BaseModel):
    secreto: str = Field(min_length=10, max_length=100)
    dispositivo: Optional[str] = Field(default=None, max_length=200)


@router.post("/vinculos/{vinculo_id}/estado", dependencies=[Depends(LIMITE_CONSULTAS)])
def estado_vinculo(vinculo_id: str, cuerpo: ConsultaVinculo, request: Request, db: Session = Depends(obtener_db)):
    fila = db.get(AddonVinculo, vinculo_id[:36])
    if fila is None or not hmac.compare_digest(fila.secreto_hash, hash_token(cuerpo.secreto)):
        raise HTTPException(status_code=404, detail="Este vínculo no existe. Pide un código nuevo.")
    momento = ahora()
    if fila.estado == "canjeado":
        return {"estado": "canjeado"}
    if fila.expira_en <= momento:
        return {"estado": "vencido"}
    if fila.estado != "listo" or not fila.usuario_id:
        return {"estado": "pendiente", "expira_en_segundos": int((fila.expira_en - momento).total_seconds())}
    usuario = db.get(Usuario, fila.usuario_id)
    if usuario is None:
        return {"estado": "vencido"}
    descripcion = dispositivo_addon(cuerpo.dispositivo or fila.dispositivo)
    token = abrir_sesion(db, usuario, descripcion, request)
    fila.estado = "canjeado"
    fila.dispositivo = descripcion
    cerrar(db, "/api/addon/v1/vinculos/estado")
    return {"estado": "listo", "token": token, "cuenta": usuario_publico(usuario)}


@router.get("/yo")
def yo(db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    filas = db.scalars(select(ProgresoPractica).where(ProgresoPractica.usuario_id == usuario.id)).all()
    return {
        **usuario_publico(usuario),
        "practicas": {"iniciadas": len(filas), "completadas": sum(1 for f in filas if f.completada)},
    }


@router.post("/salir")
def salir(request: Request, db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    sesion = db.get(Sesion, getattr(request.state, "sesion_id", ""))
    if sesion is not None and sesion.usuario_id == usuario.id:
        sesion.activa = 0
        cerrar(db, "/api/addon/v1/salir")
    return {"ok": True}


@router.get("/dispositivos")
def dispositivos(db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    """Los Blender conectados a la cuenta (para desconectarlos desde la PWA)."""
    momento = ahora()
    filas = db.scalars(
        select(Sesion)
        .where(Sesion.usuario_id == usuario.id, Sesion.activa == 1, Sesion.dispositivo.like(f"{PREFIJO_DISPOSITIVO}%"))
        .order_by(Sesion.ultimo_acceso.desc())
    ).all()
    return {
        "dispositivos": [
            {
                "id": f.id[:16],
                "nombre": f.dispositivo[len(PREFIJO_DISPOSITIVO):].strip(" ·") or "Blender",
                "conectado_en": iso(f.creado_en),
                "ultimo_uso": iso(f.ultimo_acceso),
            }
            for f in filas
            if f.expira_en is None or f.expira_en > momento
        ]
    }


@router.delete("/dispositivos/{dispositivo_id}")
def desconectar(dispositivo_id: str, db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    if len(dispositivo_id) != 16:
        raise HTTPException(status_code=404, detail="No existe ese dispositivo.")
    filas = db.scalars(
        select(Sesion).where(
            Sesion.usuario_id == usuario.id,
            Sesion.id.like(f"{dispositivo_id}%"),
            Sesion.dispositivo.like(f"{PREFIJO_DISPOSITIVO}%"),
        )
    ).all()
    if not filas:
        raise HTTPException(status_code=404, detail="No existe ese dispositivo.")
    for fila in filas:
        fila.activa = 0
    confirmar(db, "/api/addon/v1/dispositivos")
    return {"ok": True}


# --- Prácticas -------------------------------------------------------------------


@router.get("/practicas")
def listar_practicas(curso_id: Optional[str] = None, db: Session = Depends(obtener_db),
                     usuario: Optional[Usuario] = Depends(usuario_opcional)):
    equipo = es_equipo(usuario)
    consulta = select(Practica).order_by(Practica.nivel, Practica.id)
    if curso_id:
        consulta = consulta.where(Practica.curso_id == curso_id)
    if not equipo:
        consulta = consulta.where(Practica.estado == "publicado", Practica.version_publicada.is_not(None))
    practicas = db.scalars(consulta).all()
    progresos = {}
    if usuario is not None:
        progresos = {
            f.practica_id: f
            for f in db.scalars(select(ProgresoPractica).where(ProgresoPractica.usuario_id == usuario.id))
        }
    publicadas = {}
    if not equipo and practicas:
        claves = [(p.id, p.version_publicada) for p in practicas if p.version_publicada != p.version]
        for p_id, version in claves:
            fila = db.get(PracticaVersion, (p_id, version))
            if fila is not None:
                publicadas[p_id] = leer(fila.definicion)
    resultado = []
    for practica in practicas:
        version = version_para(practica, usuario)
        definicion = publicadas.get(practica.id) or leer(practica.definicion) or {}
        resultado.append(practica_meta(practica, definicion, version, progresos.get(practica.id), equipo))
    if equipo:
        estadisticas = {
            fila.practica_id: (fila.alumnos, fila.completadas)
            for fila in db.execute(
                select(
                    ProgresoPractica.practica_id,
                    func.count().label("alumnos"),
                    func.sum(ProgresoPractica.completada).label("completadas"),
                ).group_by(ProgresoPractica.practica_id)
            )
        }
        for meta in resultado:
            alumnos, completadas = estadisticas.get(meta["id"], (0, 0))
            meta["alumnos"] = int(alumnos or 0)
            meta["completadas"] = int(completadas or 0)
    return {"practicas": resultado}


@router.get("/practicas/{practica_id}")
def leer_practica(practica_id: str, version: Optional[int] = None, db: Session = Depends(obtener_db),
                  usuario: Optional[Usuario] = Depends(usuario_opcional)):
    practica = obtener_practica(db, practica_id, usuario)
    elegida = version if (version and es_equipo(usuario)) else version_para(practica, usuario)
    definicion = definicion_de(db, practica, elegida)
    progreso = db.get(ProgresoPractica, (usuario.id, practica.id)) if usuario else None
    return {
        **practica_meta(practica, definicion, elegida, progreso, es_equipo(usuario)),
        "definicion": definicion,
    }


class PracticaSubida(BaseModel):
    definicion: Dict[str, Any]
    curso_id: Optional[str] = Field(default=None, max_length=50)
    leccion_id: Optional[str] = Field(default=None, max_length=50)
    nota: Optional[str] = Field(default=None, max_length=500)
    version_addon: Optional[str] = Field(default=None, max_length=20)
    version_blender: Optional[str] = Field(default=None, max_length=20)


def registrar_version(db: Session, cuerpo: PracticaSubida, autor: Optional[Usuario], origen: str,
                      momento: datetime) -> dict:
    """Compila y guarda una versión nueva (sin commit). Lanza HTTPException 422/409."""
    definicion = dict(cuerpo.definicion)
    try:
        texto = motor.canonico(definicion)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="La práctica no es JSON válido.")
    if len(texto) > MAX_DEFINICION:
        raise HTTPException(status_code=422, detail="La práctica es demasiado grande (máximo 200 KB).")
    resultado = motor.compilar(definicion)
    if not resultado.ok:
        raise HTTPException(status_code=422, detail={"mensaje": "La práctica no compila.", "errores": resultado.errors})
    practica_def = resultado.practice
    if cuerpo.curso_id and db.get(Curso, cuerpo.curso_id) is None:
        raise HTTPException(status_code=422, detail=f"No existe el curso «{cuerpo.curso_id}».")

    huella = motor.huella(definicion)
    practica = db.get(Practica, practica_def.id)
    if practica is not None:
        ultima = db.get(PracticaVersion, (practica.id, practica.version))
        if ultima is not None and ultima.huella == huella:
            if cuerpo.curso_id:
                practica.curso_id = cuerpo.curso_id
            if cuerpo.leccion_id:
                practica.leccion_id = cuerpo.leccion_id
            return {"id": practica.id, "version": practica.version, "estado": practica.estado,
                    "version_publicada": practica.version_publicada, "sin_cambios": True,
                    "avisos": resultado.warnings}
        version = practica.version + 1
    else:
        version = 1
        practica = Practica(id=practica_def.id, version=0, estado="borrador", origen=origen)
        db.add(practica)

    definicion["version"] = version
    texto = motor.canonico(definicion)
    practica.titulo = practica_def.title[:200]
    practica.nivel = min(max(int(practica_def.level), 1), 5)
    practica.version = version
    practica.definicion = texto
    practica.origen = origen
    practica.autor_id = autor.id if autor else practica.autor_id
    practica.actualizado_en = momento
    if cuerpo.curso_id:
        practica.curso_id = cuerpo.curso_id
    if cuerpo.leccion_id:
        practica.leccion_id = cuerpo.leccion_id
    if practica.estado == "archivado":
        practica.estado = "borrador"
    db.flush()
    db.add(
        PracticaVersion(
            practica_id=practica.id,
            version=version,
            definicion=texto,
            huella=huella,
            nota=cuerpo.nota,
            autor_id=autor.id if autor else None,
            version_addon=cuerpo.version_addon,
            version_blender=cuerpo.version_blender,
            creado_en=momento,
        )
    )
    return {"id": practica.id, "version": version, "estado": practica.estado,
            "version_publicada": practica.version_publicada, "sin_cambios": False, "avisos": resultado.warnings}


@router.post("/practicas", status_code=201)
def subir_practica(cuerpo: PracticaSubida, db: Session = Depends(obtener_db), usuario: Usuario = Depends(desarrollador)):
    try:
        respuesta = registrar_version(db, cuerpo, usuario, "addon", ahora())
    except HTTPException as error:
        db.rollback()
        if isinstance(error.detail, dict):
            return rechazo(422, error.detail["mensaje"], error.detail["errores"])
        raise
    confirmar(db, "/api/addon/v1/practicas")
    return respuesta


class Publicacion(BaseModel):
    version: Optional[int] = Field(default=None, ge=1)


@router.post("/practicas/{practica_id}/publicar")
def publicar_practica(practica_id: str, cuerpo: Optional[Publicacion] = None, db: Session = Depends(obtener_db),
                      usuario: Usuario = Depends(editor)):
    practica = obtener_practica(db, practica_id, usuario)
    version = (cuerpo.version if cuerpo else None) or practica.version
    if version > practica.version or db.get(PracticaVersion, (practica.id, version)) is None:
        raise HTTPException(status_code=404, detail=f"No existe la versión {version}.")
    momento = ahora()
    practica.version_publicada = version
    practica.estado = "publicado"
    practica.publicado_en = momento
    practica.actualizado_en = momento
    confirmar(db, f"/api/addon/v1/practicas/{practica_id}/publicar")
    return {"id": practica.id, "estado": practica.estado, "version_publicada": version}


@router.post("/practicas/{practica_id}/archivar")
def archivar_practica(practica_id: str, db: Session = Depends(obtener_db), usuario: Usuario = Depends(editor)):
    practica = obtener_practica(db, practica_id, usuario)
    practica.estado = "archivado"
    practica.actualizado_en = ahora()
    confirmar(db, f"/api/addon/v1/practicas/{practica_id}/archivar")
    return {"id": practica.id, "estado": practica.estado}


@router.get("/practicas/{practica_id}/versiones")
def versiones_practica(practica_id: str, db: Session = Depends(obtener_db),
                       usuario: Usuario = Depends(desarrollador)):
    practica = obtener_practica(db, practica_id, usuario)
    filas = db.scalars(
        select(PracticaVersion).where(PracticaVersion.practica_id == practica.id).order_by(PracticaVersion.version.desc())
    ).all()
    autores = {u.id: u for u in db.scalars(select(Usuario).where(Usuario.id.in_({f.autor_id for f in filas if f.autor_id})))}
    return {
        "id": practica.id,
        "version_publicada": practica.version_publicada,
        "versiones": [
            {
                "version": f.version,
                "nota": f.nota,
                "autor": (autores[f.autor_id].nombre or autores[f.autor_id].email) if f.autor_id in autores else None,
                "version_addon": f.version_addon,
                "version_blender": f.version_blender,
                "creado_en": iso(f.creado_en),
                "publicada": f.version == practica.version_publicada,
            }
            for f in filas
        ],
    }


def practicas_en_lecciones(db: Session) -> Dict[str, tuple]:
    """{practica_id: (curso_id, leccion_id)} según los bloques blender_practice."""
    vinculos = {}
    for leccion in db.scalars(select(Leccion).where(Leccion.estado != "archivado").order_by(Leccion.curso_id, Leccion.id)):
        contenido = leer(leccion.contenido) if isinstance(leccion.contenido, str) else leccion.contenido
        for bloque in (contenido or {}).get("contentBlocks") or []:
            if isinstance(bloque, dict) and bloque.get("type") == "blender_practice" and bloque.get("practica"):
                vinculos.setdefault(bloque["practica"], (leccion.curso_id, leccion.id))
    return vinculos


def enlazar_lecciones(db: Session) -> int:
    """Anota en PRACTICAS la lección donde vive cada práctica (sin commit)."""
    cambios = 0
    for practica_id, (curso_id, leccion_id) in practicas_en_lecciones(db).items():
        practica = db.get(Practica, practica_id)
        if practica is not None and (practica.curso_id, practica.leccion_id) != (curso_id, leccion_id):
            practica.curso_id, practica.leccion_id = curso_id, leccion_id
            cambios += 1
    return cambios


def sincronizar_practicas(db: Session, autor: Optional[Usuario], publicar: bool) -> dict:
    """Registra las prácticas de practices/blender/ (origen «repositorio»). Sin commit.

    También la usa «python herramientas/contenido.py practicas».
    """
    momento = ahora()
    registradas, errores = [], []
    for ruta, datos in motor.practicas_del_repositorio():
        # registrar_version revisa todo antes de tocar la base: si falla, no deja nada a medias.
        try:
            respuesta = registrar_version(
                db, PracticaSubida(definicion=datos, nota=f"Repositorio: {ruta.parent.name}/{ruta.name}"), autor, "repositorio", momento
            )
        except HTTPException as error:
            detalle = error.detail if isinstance(error.detail, str) else "; ".join(error.detail["errores"][:3])
            errores.append(f"{ruta.parent.name}/{ruta.name}: {detalle}")
            continue
        if publicar:
            practica = db.get(Practica, respuesta["id"])
            practica.version_publicada = practica.version
            practica.estado = "publicado"
            practica.publicado_en = momento
            respuesta.update(estado="publicado", version_publicada=practica.version)
        registradas.append(respuesta)
    db.flush()
    return {"practicas": registradas, "errores": errores, "lecciones_enlazadas": enlazar_lecciones(db)}


@router.post("/practicas/sincronizar")
def sincronizar_repositorio(publicar: bool = False, db: Session = Depends(obtener_db), usuario: Usuario = Depends(editor)):
    """Registra en Oracle las prácticas de practices/blender/ (botón del panel)."""
    resumen = sincronizar_practicas(db, usuario, publicar)
    confirmar(db, "/api/addon/v1/practicas/sincronizar")
    return resumen


# --- Progreso -----------------------------------------------------------------


class Apertura(BaseModel):
    origen: Literal["plataforma", "blender"] = "plataforma"


@router.post("/practicas/{practica_id}/abrir")
def abrir_practica(practica_id: str, cuerpo: Optional[Apertura] = None, db: Session = Depends(obtener_db),
                   usuario: Usuario = Depends(usuario_requerido)):
    """La práctica pasa a ser la «actual»: Blender la abre sola al conectarse."""
    practica = obtener_practica(db, practica_id, usuario)
    momento = ahora()
    fila = db.get(ProgresoPractica, (usuario.id, practica.id))
    if fila is None:
        fila = ProgresoPractica(
            usuario_id=usuario.id, practica_id=practica.id, version=version_para(practica, usuario),
            progreso=0, completada=0, pistas=0, correcciones=0, intentos=0, abierta_en=momento,
        )
        db.add(fila)
    fila.abierta_en = fila.abierta_en or momento
    fila.actualizado_en = momento
    confirmar(db, f"/api/addon/v1/practicas/{practica_id}/abrir")
    return progreso_meta(fila)


@router.get("/practica-actual")
def practica_actual(db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    """La última práctica que el alumno abrió o trabajó y aún no termina."""
    filas = db.scalars(
        select(ProgresoPractica)
        .where(ProgresoPractica.usuario_id == usuario.id, ProgresoPractica.completada == 0)
        .order_by(ProgresoPractica.actualizado_en.desc())
        .limit(5)
    ).all()
    for fila in filas:
        practica = db.get(Practica, fila.practica_id)
        if visible(practica, usuario):
            version = version_para(practica, usuario)
            return {"definicion": definicion_de(db, practica, version), "progreso": progreso_meta(fila)}
    return {"definicion": None, "progreso": None}


class Intento(BaseModel):
    practica_id: str = Field(min_length=1, max_length=80)
    version: Optional[int] = Field(default=None, ge=1)
    escena: Dict[str, Any]
    pistas: Dict[str, int] = Field(default_factory=dict, max_length=60)
    correcciones: int = Field(default=0, ge=0, le=99999)
    version_addon: Optional[str] = Field(default=None, max_length=20)
    version_motor: Optional[str] = Field(default=None, max_length=20)
    sistema: Optional[str] = Field(default=None, max_length=60)
    modo: Optional[str] = Field(default=None, max_length=20)


def subir_habilidades(db: Session, usuario: Usuario, habilidades, autonomia: str, practica_id: str,
                      version: int, momento: datetime) -> List[str]:
    """Sube cada habilidad de la práctica (nunca la baja), con tope en con_pistas."""
    tope = min(ORDEN_AUTONOMIA[autonomia], ORDEN_AUTONOMIA[TOPE_HABILIDAD])
    estado = next(e for e, n in ORDEN_AUTONOMIA.items() if n == tope)
    subidas = []
    existentes = set(db.scalars(select(Habilidad.id).where(Habilidad.id.in_(list(habilidades))))) if habilidades else set()
    for habilidad_id in habilidades:
        if habilidad_id not in existentes:
            continue
        fila = db.get(HabilidadAlumno, (usuario.id, habilidad_id))
        if fila is None:
            fila = HabilidadAlumno(usuario_id=usuario.id, habilidad_id=habilidad_id, estado="sin_practicar")
            db.add(fila)
        if ORDEN_AUTONOMIA.get(fila.estado, 0) < tope:
            fila.estado = estado
            fila.evidencia = f"Práctica {practica_id} v{version} (Blender)"[:300]
            fila.actualizado_en = momento
            subidas.append(habilidad_id)
    return subidas


@router.post("/intentos", dependencies=[Depends(LIMITE_INTENTOS)])
def registrar_intento(cuerpo: Intento, request: Request, db: Session = Depends(obtener_db),
                      usuario: Usuario = Depends(usuario_requerido)):
    practica = obtener_practica(db, cuerpo.practica_id, usuario)
    version = version_para(practica, usuario)
    if cuerpo.version and cuerpo.version != version:
        # Una versión anterior que el alumno tenía en caché sigue valiendo.
        if cuerpo.version <= practica.version and db.get(PracticaVersion, (practica.id, cuerpo.version)) is not None:
            version = cuerpo.version
    definicion = definicion_de(db, practica, version)
    pistas = {str(k)[:80]: max(0, min(int(v), 20)) for k, v in cuerpo.pistas.items()}
    try:
        practica_def, reporte, autonomia = motor.evaluar(definicion, cuerpo.escena, pistas, cuerpo.correcciones)
    except motor.InvalidPracticeError as error:
        raise HTTPException(status_code=422, detail=str(error))

    momento = ahora()
    progreso = int(round(reporte.progress))
    cumplidos = [r.target_id for r in reporte.results if r.passed is True]
    while cumplidos and len(compactar(cumplidos)) > MAX_OBJETIVOS_TEXTO:
        cumplidos.pop()
    version_blender = (request.headers.get("x-blender-version") or cuerpo.escena.get("blender") or "")[:20] or None

    fila = db.get(ProgresoPractica, (usuario.id, practica.id))
    nueva_completa = False
    if fila is None:
        fila = ProgresoPractica(usuario_id=usuario.id, practica_id=practica.id, abierta_en=momento,
                                progreso=0, completada=0, intentos=0, pistas=0, correcciones=0)
        db.add(fila)
    fila.version = version
    fila.intentos = (fila.intentos or 0) + 1
    if progreso >= (fila.progreso or 0):
        # Los pasos acompañan al mejor resultado: la plataforma nunca muestra
        # 100 % con objetivos pendientes porque el alumno siguió probando.
        fila.paso_actual = reporte.current_target_id
        fila.objetivos = compactar(cumplidos)
    fila.progreso = max(fila.progreso or 0, progreso)
    fila.pistas = sum(pistas.values())
    fila.correcciones = cuerpo.correcciones
    fila.version_blender = version_blender
    fila.version_addon = cuerpo.version_addon
    fila.abierta_en = fila.abierta_en or momento
    fila.actualizado_en = momento
    if reporte.completed:
        if not fila.completada:
            nueva_completa = True
            fila.completada = 1
            fila.completada_en = momento
        if autonomia and ORDEN_AUTONOMIA[autonomia] > ORDEN_AUTONOMIA.get(fila.autonomia or "sin_practicar", 0):
            fila.autonomia = autonomia

    habilidades = []
    if reporte.completed and autonomia:
        habilidades = subir_habilidades(db, usuario, practica_def.skills, autonomia, practica.id, version, momento)
    if nueva_completa and practica.curso_id and practica.leccion_id:
        api_progreso.guardar(
            db,
            usuario.id,
            {(practica.curso_id, practica.leccion_id): {
                "completada": True, "puntaje": progreso, "intentos": 1, "completada_en": momento, "datos": None,
            }},
            [],
            momento,
        )
    datos = compactar({"p": practica.id[:60], "v": version, "pr": progreso, "c": int(reporte.completed),
                       "h": fila.pistas, "a": (autonomia or "")[:10]})
    db.add(
        EventoAprendizaje(
            id=str(uuid.uuid4()),
            usuario_id=usuario.id,
            tipo="activity_submitted",
            curso_id=practica.curso_id,
            leccion_id=practica.leccion_id,
            ocurrido_en=momento,
            recibido_en=momento,
            version_app=f"addon {cuerpo.version_addon or ''}".strip()[:20],
            es_prueba=usuario.es_prueba or 0,
            datos=datos if len(datos) <= 250 else None,
        )
    )
    confirmar(db, "/api/addon/v1/intentos")
    return {
        "practica_id": practica.id,
        "version": version,
        "progreso": progreso,
        "mejor_progreso": fila.progreso,
        "completada": reporte.completed,
        "nueva_completa": nueva_completa,
        "paso_actual": reporte.current_target_id,
        "autonomia": autonomia,
        "habilidades": habilidades,
        "pasos": [{"id": s.target_id, "estado": s.status} for s in reporte.steps],
    }


@router.get("/mi-progreso")
def mi_progreso(practica_id: Optional[str] = None, db: Session = Depends(obtener_db),
                usuario: Usuario = Depends(usuario_requerido)):
    consulta = select(ProgresoPractica).where(ProgresoPractica.usuario_id == usuario.id)
    if practica_id:
        consulta = consulta.where(ProgresoPractica.practica_id == practica_id)
    filas = db.scalars(consulta.order_by(ProgresoPractica.actualizado_en.desc())).all()
    return {"practicas": [progreso_meta(f) for f in filas]}


# --- Descargas -------------------------------------------------------------------


@lru_cache(maxsize=16)
def _paquete_publico(sistema: str, servidor: str, plataforma: str) -> bytes:
    if sistema == "extension":
        return motor.construir.construir_extension(servidor=servidor, plataforma=plataforma)
    return motor.construir.construir_paquete(sistema, servidor=servidor, plataforma=plataforma)


def _zip(contenido: bytes, nombre: str, privado: bool) -> Response:
    return Response(
        content=contenido,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "Cache-Control": "private, no-store" if privado else "public, max-age=300",
        },
    )


@router.get("/descargas/{sistema}", dependencies=[Depends(LIMITE_DESCARGAS)])
def descargar(sistema: str, request: Request, db: Session = Depends(obtener_db),
              usuario: Optional[Usuario] = Depends(usuario_opcional)):
    """Paquete con instalador. Con sesión trae un vínculo de un solo uso."""
    if sistema not in SISTEMAS:
        raise HTTPException(status_code=404, detail=f"Elige uno de: {', '.join(SISTEMAS)}.")
    servidor, plataforma = url_api(request), url_pwa(request)
    nombre = f"Amatista-{motor.VERSION_ADDON}-{sistema}.zip"
    if usuario is None:
        return _zip(_paquete_publico(sistema, servidor, plataforma), nombre, privado=False)
    fila, secreto = crear_vinculo(db, f"{PREFIJO_DISPOSITIVO} · paquete {sistema}", ahora(),
                                  DURACION_VINCULO_PAQUETE, usuario)
    cerrar(db, "/api/addon/v1/descargas")
    contenido = motor.construir.construir_paquete(
        sistema, servidor=servidor, plataforma=plataforma, vinculo={"id": fila.id, "secreto": secreto}
    )
    return _zip(contenido, nombre, privado=True)


@router.get("/extension.zip")
def extension(request: Request):
    return _zip(_paquete_publico("extension", url_api(request), url_pwa(request)),
                motor.construir.nombre_extension(), privado=False)


@router.get("/extensiones/index.json")
def indice_extensiones(request: Request):
    servidor = url_api(request)
    datos = _paquete_publico("extension", servidor, url_pwa(request))
    return motor.construir.indice_repositorio(datos, f"{servidor}/api/addon/v1/extension.zip")
