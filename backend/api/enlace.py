"""Enlace en vivo plataforma ↔ Blender (motor 3.4, sql/010).

Antes, «Abrir en Blender» solo marcaba la práctica en el servidor: si
Blender ya estaba abierto no pasaba nada y la plataforma no sabía si había
un Blender conectado. Ahora:

- El add-on manda un **latido** (POST /enlace) cada pocos segundos con la
  práctica abierta, el paso, el progreso y si está en modo enfocado. La
  respuesta trae la **orden pendiente** y los **ajustes** del alumno.
- La plataforma ve sus Blender en vivo (GET /enlace) y les deja órdenes
  (POST /ordenes): abrir una práctica, enfocar Blender o mostrar todo y,
  desde el motor 3.5, comprobar, pedir pista, «Hazlo conmigo», guardar y
  empezar de nuevo. El latido trae también lo que muestra el instructor
  (paso, mensaje y la lista de la figura): la lección lo muestra en vivo.
- «Mi Blender» guarda cómo se ve Blender para el alumno (GET/PUT /ajustes):
  modo enfocado, acompañamiento, avisos. El add-on los aplica solo.

La fila del latido solo se reescribe si cambió algo o cada 15 s, para que
un aula entera no escriba en Oracle cada 5 s. Sin las tablas de 010 todo
responde «sin enlace» y la plataforma sigue como antes.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import error_bd
from api.contenido import iso
from api.dependencias import PREFIJO_ADDON, usuario_requerido
from api.limites import limitar
from database.conexion import obtener_db
from database.modelos import AddonAjustes, AddonEnlace, Practica, Sesion, Usuario, ahora

router = APIRouter(prefix="/api/addon/v1", tags=["addon"])

INTERVALO = 5  # segundos entre latidos que pide el servidor
EN_LINEA = timedelta(seconds=25)  # sin latido en este tiempo, Blender se da por cerrado
REESCRIBIR = timedelta(seconds=15)  # el latido sin cambios solo actualiza visto_en cada 15 s
VIDA_ORDEN = timedelta(minutes=10)  # una orden que nadie recogió se descarta
OLVIDAR = timedelta(days=2)  # enlaces sin latido en 2 días se borran

ENFOQUES = ("auto", "siempre", "nunca")
ACOMPANAMIENTOS = ("acompanado", "tarjeta", "silencioso")
AJUSTES_POR_DEFECTO: Dict[str, Any] = {
    "enfoque": "auto",  # auto = enfocado en los niveles 1 y 2
    "acompanamiento": "acompanado",
    "avisos_herramientas": True,
    "tarjeta_3d": True,
}
ORDENES = ("abrir_practica", "enfocar", "ver_todo", "actualizar",
           # Motor 3.5: la plataforma maneja la práctica en Blender.
           "comprobar", "pista", "hazlo_conmigo", "guardar", "reiniciar", "ver_ejemplo", "volver_practica")
# Lo que Blender está mostrando ahora (paso, mensaje del instructor, lista de la figura). Vive en la
# memoria del proceso: cambia cada pocos segundos y no vale la pena escribirlo en Oracle. Con dos
# procesos de uvicorn (T-081) habría que compartirlo; hoy el servicio corre con uno.
VIVO: Dict[str, Dict[str, Any]] = {}
MAX_VIVO = 2000

LIMITE_LATIDO = limitar(40, por="cuenta")  # 12 por minuto con el intervalo normal
LIMITE_ORDENES = limitar(30, por="cuenta")


def _sesion_addon(request: Request, db: Session, usuario: Usuario) -> Sesion:
    sesion = db.get(Sesion, getattr(request.state, "sesion_id", ""))
    if sesion is None or sesion.usuario_id != usuario.id or not (sesion.dispositivo or "").startswith(PREFIJO_ADDON):
        raise HTTPException(status_code=403, detail="El latido solo lo manda el add-on de Blender.")
    return sesion


def _leer(texto: Optional[str]) -> Any:
    if not texto:
        return None
    try:
        return json.loads(texto)
    except ValueError:
        return None


def _compactar(datos: Any) -> str:
    return json.dumps(datos, ensure_ascii=False, separators=(",", ":"))


def ajustes_de(db: Session, usuario_id: str) -> Dict[str, Any]:
    fila = db.get(AddonAjustes, usuario_id)
    guardados = _leer(fila.datos) if fila else None
    return {**AJUSTES_POR_DEFECTO, **(guardados if isinstance(guardados, dict) else {})}


def _sin_tablas(error: SQLAlchemyError) -> bool:
    """ORA-00942 (o «no such table» en SQLite): 010 todavía no se ejecutó."""
    texto = str(getattr(error, "orig", None) or error).lower()
    return "ora-00942" in texto or "no such table" in texto


def _enlace_publico(fila: AddonEnlace, momento, titulos: Dict[str, str]) -> Dict[str, Any]:
    vivo = VIVO.get(fila.sesion_id)
    detalle = None
    if vivo and momento - vivo["en"] <= EN_LINEA and vivo.get("practica_id") == fila.practica_id:
        detalle = vivo["detalle"]
    return {
        "id": fila.sesion_id[:16],
        "en_linea": momento - fila.visto_en <= EN_LINEA,
        "visto_en": iso(fila.visto_en),
        "practica_id": fila.practica_id,
        "practica": titulos.get(fila.practica_id or "", fila.practica_id),
        "paso": fila.paso,
        "progreso": fila.progreso,
        "enfocado": bool(fila.enfocado),
        "version_addon": fila.version_addon,
        "version_blender": fila.version_blender,
        "orden_pendiente": (_leer(fila.orden) or {}).get("tipo"),
        "detalle": detalle,
    }


def enlaces_de(db: Session, usuario_id: str) -> List[AddonEnlace]:
    return list(db.scalars(
        select(AddonEnlace).where(AddonEnlace.usuario_id == usuario_id).order_by(AddonEnlace.visto_en.desc())
    ).all())


def dejar_orden(db: Session, usuario_id: str, tipo: str, datos: Optional[Dict[str, Any]] = None) -> int:
    """Deja la orden en cada Blender en línea del alumno. Devuelve a cuántos llegó (0 = ninguno abierto)."""
    momento = ahora()
    orden = {"id": uuid.uuid4().hex[:12], "tipo": tipo, "datos": datos or {}}
    entregadas = 0
    for fila in enlaces_de(db, usuario_id):
        if momento - fila.visto_en <= EN_LINEA:
            fila.orden = _compactar(orden)
            fila.orden_en = momento
            entregadas += 1
    return entregadas


def ordenar_abrir(db: Session, usuario_id: str, practica_id: str) -> int:
    """«Abrir en Blender» de la lección: si hay un Blender abierto, la práctica se abre ahí sola.

    No falla si 010 no se ejecutó todavía: entonces Blender la abre al arrancar, como antes.
    """
    try:
        return dejar_orden(db, usuario_id, "abrir_practica", {"practica_id": practica_id})
    except SQLAlchemyError as error:
        if _sin_tablas(error):
            db.rollback()
            return 0
        raise


# --- El add-on -------------------------------------------------------------------


class ItemLista(BaseModel):
    texto: str = Field(max_length=60)
    ok: bool = False
    estado: str = Field(default="", max_length=20)
    consejo: str = Field(default="", max_length=400)
    aspecto: str = Field(default="", max_length=40)  # la figura, materiales, luces… (el ejemplo resuelto)


class Detalle(BaseModel):
    """Lo que el instructor muestra en Blender ahora (motor 3.5)."""

    titulo: str = Field(default="", max_length=120)  # el paso actual
    mensaje: str = Field(default="", max_length=500)  # lo que dice el instructor
    numero: int = Field(default=0, ge=0, le=99)
    total: int = Field(default=0, ge=0, le=99)
    figura: str = Field(default="", max_length=120)  # título de la lista («Forja la silueta…»)
    lista: List[ItemLista] = Field(default_factory=list, max_length=16)
    modo: str = Field(default="", max_length=20)  # OBJECT, EDIT_MESH…
    pistas: int = Field(default=0, ge=0, le=9)  # pistas que quedan en el paso
    accion: str = Field(default="", max_length=80)  # el texto del «Hazlo conmigo» si hay
    completada: bool = False


class Latido(BaseModel):
    practica_id: Optional[str] = Field(default=None, max_length=80)
    paso: Optional[str] = Field(default=None, max_length=80)
    progreso: Optional[int] = Field(default=None, ge=0, le=100)
    enfocado: bool = False
    version_addon: Optional[str] = Field(default=None, max_length=20)
    version_blender: Optional[str] = Field(default=None, max_length=20)
    orden_hecha: Optional[str] = Field(default=None, max_length=12)  # id de la orden que ya cumplió
    detalle: Optional[Detalle] = None  # motor 3.5: lo que muestra el instructor


@router.post("/enlace", dependencies=[Depends(LIMITE_LATIDO)])
def latido(cuerpo: Latido, request: Request, db: Session = Depends(obtener_db),
           usuario: Usuario = Depends(usuario_requerido)):
    """El latido del add-on: «sigo abierto, voy aquí». Responde la orden pendiente y los ajustes."""
    sesion = _sesion_addon(request, db, usuario)
    momento = ahora()
    try:
        fila = db.get(AddonEnlace, sesion.id)
        nuevo = fila is None
        if nuevo:
            fila = AddonEnlace(sesion_id=sesion.id, usuario_id=usuario.id, visto_en=momento)
            db.add(fila)
        estado = (cuerpo.practica_id, cuerpo.paso, cuerpo.progreso, int(cuerpo.enfocado), cuerpo.version_addon,
                  cuerpo.version_blender)
        antes = (fila.practica_id, fila.paso, fila.progreso, fila.enfocado, fila.version_addon, fila.version_blender)
        orden = _leer(fila.orden)
        if orden and cuerpo.orden_hecha and orden.get("id") == cuerpo.orden_hecha:
            fila.orden, fila.orden_en, orden = None, None, None
        elif orden and fila.orden_en and momento - fila.orden_en > VIDA_ORDEN:
            fila.orden, fila.orden_en, orden = None, None, None
        cambio = nuevo or estado != antes or db.is_modified(fila)
        if cambio or momento - fila.visto_en > REESCRIBIR:
            (fila.practica_id, fila.paso, fila.progreso, fila.enfocado, fila.version_addon,
             fila.version_blender) = estado
            fila.visto_en = momento
            db.commit()
        ajustes = ajustes_de(db, usuario.id)
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            return {"enlace": False, "intervalo": 60, "orden": None, "ajustes": dict(AJUSTES_POR_DEFECTO)}
        raise error_bd(error, "/api/addon/v1/enlace")
    _guardar_vivo(sesion.id, cuerpo, momento)
    return {"enlace": True, "intervalo": INTERVALO, "orden": orden, "ajustes": ajustes}


def _guardar_vivo(sesion_id: str, cuerpo: Latido, momento) -> None:
    if cuerpo.detalle is None or not cuerpo.practica_id:
        VIVO.pop(sesion_id, None)
        return
    if sesion_id not in VIVO and len(VIVO) >= MAX_VIVO:  # nunca crece sin límite: se van los más viejos
        for clave in sorted(VIVO, key=lambda k: VIVO[k]["en"])[: MAX_VIVO // 10]:
            VIVO.pop(clave, None)
    VIVO[sesion_id] = {"en": momento, "practica_id": cuerpo.practica_id, "detalle": cuerpo.detalle.model_dump()}


# --- La plataforma ------------------------------------------------------------------


@router.get("/enlace")
def ver_enlace(db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    """Los Blender del alumno: si están abiertos, qué practican y en qué paso van."""
    momento = ahora()
    try:
        filas = enlaces_de(db, usuario.id)
        viejas = [f for f in filas if momento - f.visto_en > OLVIDAR]
        for fila in viejas:
            db.delete(fila)
        if viejas:
            db.commit()
        filas = [f for f in filas if f not in viejas]
        ids = {f.practica_id for f in filas if f.practica_id}
        titulos = {p.id: p.titulo for p in db.scalars(select(Practica).where(Practica.id.in_(ids))).all()} if ids else {}
        blender = [_enlace_publico(f, momento, titulos) for f in filas]
        ajustes = ajustes_de(db, usuario.id)
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            return {"enlace": False, "en_linea": False, "blender": [], "ajustes": dict(AJUSTES_POR_DEFECTO)}
        raise error_bd(error, "/api/addon/v1/enlace")
    return {"enlace": True, "en_linea": any(b["en_linea"] for b in blender), "blender": blender, "ajustes": ajustes}


class Orden(BaseModel):
    tipo: Literal["abrir_practica", "enfocar", "ver_todo", "actualizar", "comprobar", "pista", "hazlo_conmigo",
                  "guardar", "reiniciar", "ver_ejemplo", "volver_practica"]
    practica_id: Optional[str] = Field(default=None, max_length=80)
    confirmar: bool = False  # «reiniciar» lo exige: la plataforma pregunta antes


@router.post("/ordenes", dependencies=[Depends(LIMITE_ORDENES)])
def nueva_orden(cuerpo: Orden, db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    """La plataforma le pide algo al Blender abierto del alumno."""
    datos: Dict[str, Any] = {}
    if cuerpo.tipo == "reiniciar" and not cuerpo.confirmar:
        raise HTTPException(status_code=400, detail="Empezar de nuevo necesita confirmación.")
    if cuerpo.practica_id and cuerpo.tipo != "abrir_practica":
        datos["practica_id"] = cuerpo.practica_id  # Blender solo la cumple si sigue en esa práctica
    if cuerpo.tipo == "abrir_practica":
        practica = db.get(Practica, cuerpo.practica_id or "")
        if practica is None:
            raise HTTPException(status_code=404, detail="No existe esa práctica.")
        datos["practica_id"] = practica.id
    try:
        entregadas = dejar_orden(db, usuario.id, cuerpo.tipo, datos)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            return {"enlace": False, "entregada": False}
        raise error_bd(error, "/api/addon/v1/ordenes")
    return {"enlace": True, "entregada": entregadas > 0, "blender": entregadas}


class Ajustes(BaseModel):
    enfoque: Optional[Literal["auto", "siempre", "nunca"]] = None
    acompanamiento: Optional[Literal["acompanado", "tarjeta", "silencioso"]] = None
    avisos_herramientas: Optional[bool] = None
    tarjeta_3d: Optional[bool] = None


@router.get("/ajustes")
def leer_ajustes(db: Session = Depends(obtener_db), usuario: Usuario = Depends(usuario_requerido)):
    try:
        return {"enlace": True, "ajustes": ajustes_de(db, usuario.id)}
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            return {"enlace": False, "ajustes": dict(AJUSTES_POR_DEFECTO)}
        raise error_bd(error, "/api/addon/v1/ajustes")


@router.put("/ajustes", dependencies=[Depends(LIMITE_ORDENES)])
def guardar_ajustes(cuerpo: Ajustes, request: Request, db: Session = Depends(obtener_db),
                    usuario: Usuario = Depends(usuario_requerido)):
    """«Mi Blender»: cómo se ve Blender para este alumno. Llega al add-on en su siguiente latido."""
    sesion = db.get(Sesion, getattr(request.state, "sesion_id", ""))
    if sesion is not None and (sesion.dispositivo or "").startswith(PREFIJO_ADDON):
        raise HTTPException(status_code=403, detail="Los ajustes de Blender se cambian desde la plataforma.")
    try:
        actuales = ajustes_de(db, usuario.id)
        nuevos = {**actuales, **cuerpo.model_dump(exclude_none=True)}
        fila = db.get(AddonAjustes, usuario.id)
        if fila is None:
            db.add(AddonAjustes(usuario_id=usuario.id, datos=_compactar(nuevos), actualizado_en=ahora()))
        else:
            fila.datos = _compactar(nuevos)
            fila.actualizado_en = ahora()
        dejar_orden(db, usuario.id, "actualizar")
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        if _sin_tablas(error):
            raise HTTPException(status_code=503, detail="Falta ejecutar sql/010_enlace_blender.sql en Oracle.")
        raise error_bd(error, "/api/addon/v1/ajustes")
    return {"enlace": True, "ajustes": nuevos}
