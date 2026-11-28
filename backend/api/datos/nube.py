"""Amatista Cloud: una hora de Blender en la nube para cuentas de la UTN (sql/014).

La API es el coordinador. Las sesiones corren en **nodos**: la VM de Oracle y
las computadoras del equipo que ejecutan `nube/nodo/amatista_nodo.py`. Un
nodo nunca recibe conexiones de la API: él llama cada pocos segundos
(«latido») y en la respuesta se lleva lo que tiene que abrir o cerrar. Así
funciona detrás del módem de una casa, sin abrir puertos.

Alumnos (cuenta con correo confirmado de un dominio permitido):
- GET  /api/nube/v1/estado ...................... lugares libres, reglas y si la cuenta puede entrar.
- POST /api/nube/v1/sesiones .................... pide una sesión de 60 minutos. Responde su token
                                                  de acceso, que solo se muestra esta vez.
- GET  /api/nube/v1/sesiones/actual ............. la sesión viva de la cuenta, o null.
- POST /api/nube/v1/sesiones/{id}/token ......... token nuevo para entrar desde otro dispositivo
                                                  (el anterior deja de servir).
- POST /api/nube/v1/sesiones/{id}/terminar ...... la cierra antes de tiempo y libera el lugar.

Nodos (Authorization: Bearer <token del nodo>):
- POST /api/nube/v1/nodo/latido ................. capacidad y sesiones vivas → {abrir, cerrar, tokens}.
- POST /api/nube/v1/nodo/sesiones/{id} .......... {estado: lista | fallida | terminada, motivo}.

Equipo (admin):
- GET  /api/nube/v1/admin ....................... nodos, sesiones vivas y las últimas 50.
- POST /api/nube/v1/admin/sesiones/{id}/terminar

Reglas (variables de entorno):
- AMATISTA_NUBE_DOMINIOS ....... dominios de correo permitidos, separados por comas (utn.mx). También
                                 valen sus subdominios (alumno.utn.mx). Los administradores siempre entran.
- AMATISTA_NUBE_NODOS .......... nombre:sha256_del_token,… (lo genera `amatista_nodo.py nuevo-token`).
- AMATISTA_NUBE_MINUTOS ........ duración de cada sesión y de su token: 60 (nunca menos de 60).
- AMATISTA_NUBE_POR_DIA ........ sesiones por cuenta en 24 horas: 2.

Contra el uso como granja de render: una sesión viva por cuenta, cuota diaria,
token que vence con la sesión, y en el nodo un contenedor sin internet, con
CPU, memoria, procesos y disco limitados que se apaga al vencer
(docs/nube/README.md, «Seguridad»).
"""
import os
import re
import threading
import time
import uuid
from datetime import timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import error_bd
from api.dependencias import requiere_rol, usuario_opcional, usuario_requerido
from api.limites import limitar
from api.plataforma.perfil import sin_esquema
from database.conexion import obtener_db
from database.modelos import NubeSesion, Usuario, ahora
from seguridad import hash_token, nuevo_token

router = APIRouter(prefix="/api/nube/v1", tags=["nube"])

SIN_014 = "Amatista Cloud todavía no está listo en el servidor (falta sql/014_nube.sql). Inténtalo más tarde."
VIVAS = ("asignada", "lista")
NODO_VIVO_S = 30  # sin latido en este tiempo, el nodo no recibe sesiones nuevas
NODO_CAIDO_S = 120  # sin latido en este tiempo, sus sesiones se dan por terminadas
ESPERA_ARRANQUE_S = 180  # si el nodo no abre la sesión en este tiempo, se marca fallida
NOMBRE_NODO = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
LIMITE_PEDIDOS = limitar(6, ventana=3600, por="cuenta")


# --- Configuración ---------------------------------------------------------------------


def dominios_permitidos() -> List[str]:
    """Dominios de correo permitidos (AMATISTA_NUBE_DOMINIOS), en minúsculas y sin '@'."""
    # Producción responde dominios ['utn.edu.mx','utn.mx'] (GET /api/nube/v1/estado, 2026-11-27).
    crudo = os.getenv("AMATISTA_NUBE_DOMINIOS", "utn.mx")
    return [d.strip().lower().lstrip("@") for d in crudo.split(",") if d.strip()]


def minutos_sesion() -> int:
    """Duración de sesión en minutos (AMATISTA_NUBE_MINUTOS, mínimo 60)."""
    try:
        return max(60, int(os.getenv("AMATISTA_NUBE_MINUTOS", "60")))
    except ValueError:
        return 60


def sesiones_por_dia() -> int:
    """Cuota de sesiones por cuenta en 24 h (AMATISTA_NUBE_POR_DIA, mínimo 1)."""
    try:
        return max(1, int(os.getenv("AMATISTA_NUBE_POR_DIA", "2")))
    except ValueError:
        return 2


def nodos_autorizados() -> Dict[str, str]:
    """{sha256 del token: nombre del nodo}."""
    autorizados = {}
    for par in os.getenv("AMATISTA_NUBE_NODOS", "").split(","):
        nombre, _, huella = par.strip().partition(":")
        if NOMBRE_NODO.match(nombre) and re.fullmatch(r"[0-9a-f]{64}", huella):
            autorizados[huella] = nombre
    return autorizados


def correo_permitido(email: Optional[str]) -> bool:
    """True si el dominio del correo es uno permitido o subdominio suyo."""
    dominio = (email or "").rsplit("@", 1)[-1].lower() if "@" in (email or "") else ""
    return bool(dominio) and any(dominio == d or dominio.endswith("." + d) for d in dominios_permitidos())


def motivo_sin_acceso(usuario: Optional[Usuario]) -> Optional[str]:
    """Por qué esta cuenta no puede usar Amatista Cloud, o None si puede."""
    # [REVISION-BUG] Las cuentas creadas con Google no tienen password_hash: quedan fuera de Amatista Cloud aunque sean @utn. Decidir.
    if usuario is None or not usuario.password_hash:
        return "Inicia sesión con tu cuenta de la UTN."
    if usuario.rol == "admin":
        return None
    if not correo_permitido(usuario.email):
        dominios = ", ".join("@" + d for d in dominios_permitidos())
        return f"Amatista Cloud es solo para cuentas de la UTN ({dominios})."
    if not usuario.correo_confirmado:
        return "Confirma tu correo de la UTN para usar Amatista Cloud."
    return None


# --- Estado vivo de los nodos (memoria del proceso) ------------------------------------
#
# La API corre en un solo proceso de uvicorn (ver api/limites.py). Si se reinicia,
# los nodos se vuelven a anunciar en su siguiente latido y las sesiones vivas
# siguen en NUBE_SESIONES.


class Nodo:
    """Estado en memoria de un nodo: último latido, capacidad, sesiones ocupadas y órdenes pendientes."""
    def __init__(self, nombre: str):
        """Crea el registro de un nodo recién anunciado, sin latidos ni sesiones."""
        self.nombre = nombre
        self.ultimo = 0.0
        self.capacidad = 0
        self.ocupadas: set = set()
        self.url = ""
        self.datos: dict = {}
        self.abrir: Dict[str, dict] = {}  # sesiones por abrir, hasta que el nodo las confirme
        self.cerrar: set = set()
        self.tokens: Dict[str, str] = {}  # id → hash de un token nuevo

    def vivo(self, momento: float) -> bool:
        """True si latió hace menos de NODO_VIVO_S segundos (reloj monotónico)."""
        return momento - self.ultimo < NODO_VIVO_S

    def libres(self) -> int:
        """Lugares libres descontando sesiones abiertas y por abrir."""
        return max(0, self.capacidad - len(self.ocupadas | set(self.abrir)))

    def publico(self, momento: float) -> dict:
        """Resumen del nodo para el panel de administración."""
        return {
            "nombre": self.nombre,
            "vivo": self.vivo(momento),
            "hace_s": round(momento - self.ultimo, 1) if self.ultimo else None,
            "capacidad": self.capacidad,
            "ocupadas": len(self.ocupadas | set(self.abrir)),
            "libres": self.libres(),
            "url": self.url,
            **{k: self.datos.get(k) for k in ("arquitectura", "nucleos", "ram_gb", "carga", "ram_libre_gb", "version")},
        }


_nodos: Dict[str, Nodo] = {}
_candado = threading.Lock()


def reiniciar_nube() -> None:
    """Olvida los nodos (pruebas)."""
    with _candado:
        _nodos.clear()


# Debe llamarse con _candado tomado (nodo_actual lo hace).
def _nodo(nombre: str) -> Nodo:
    """Devuelve (o crea) el Nodo con ese nombre."""
    if nombre not in _nodos:
        _nodos[nombre] = Nodo(nombre)
    return _nodos[nombre]


def nodo_actual(request: Request) -> Nodo:
    """Dependencia: identifica al nodo por el SHA-256 de su token Bearer (401 si no está en AMATISTA_NUBE_NODOS)."""
    autorizacion = request.headers.get("authorization", "")
    token = autorizacion[7:].strip() if autorizacion.lower().startswith("bearer ") else ""
    nombre = nodos_autorizados().get(hash_token(token)) if token else None
    if not nombre:
        raise HTTPException(status_code=401, detail="Nodo no autorizado.")
    with _candado:
        return _nodo(nombre)


# --- Mantenimiento: vencer sesiones y liberar lugares ----------------------------------


def _cerrar(db: Session, sesion: NubeSesion, estado: str, motivo: str) -> None:
    """Marca la sesión como terminada/fallida y encola en el nodo la orden de cerrarla (sin commit)."""
    sesion.estado = estado
    sesion.motivo_fin = motivo[:40]
    sesion.terminada_en = ahora()
    with _candado:
        nodo = _nodos.get(sesion.nodo)
        if nodo is not None:
            nodo.abrir.pop(sesion.id, None)
            nodo.tokens.pop(sesion.id, None)
            if estado == "terminada" or sesion.id in nodo.ocupadas:
                nodo.cerrar.add(sesion.id)


def mantener(db: Session) -> None:
    """Vence las sesiones pasadas de hora, las que el nodo no abrió y las de nodos caídos."""
    momento = ahora()
    reloj = time.monotonic()
    vivas = db.scalars(select(NubeSesion).where(NubeSesion.estado.in_(VIVAS))).all()
    cambios = False
    for sesion in vivas:
        nodo = _nodos.get(sesion.nodo)
        if sesion.expira_en <= momento:
            _cerrar(db, sesion, "terminada", "tiempo")
        elif sesion.estado == "asignada" and momento - sesion.creada_en > timedelta(seconds=ESPERA_ARRANQUE_S):
            _cerrar(db, sesion, "fallida", "el nodo no la abrió")
        elif nodo is not None and nodo.ultimo and reloj - nodo.ultimo > NODO_CAIDO_S:
            _cerrar(db, sesion, "terminada", "nodo sin conexión")
        else:
            continue
        cambios = True
    if cambios:
        db.commit()


def _publica(sesion: NubeSesion, token: Optional[str] = None) -> dict:
    """Datos de una sesión para el alumno (URLs de entrada y archivos solo si está lista)."""
    nodo = _nodos.get(sesion.nodo)
    url = nodo.url if nodo else ""
    datos = {
        "id": sesion.id,
        "estado": sesion.estado,
        "nodo": sesion.nodo,
        "creada_en": sesion.creada_en.isoformat() + "Z",
        "expira_en": sesion.expira_en.isoformat() + "Z",
        "segundos_restantes": max(0, int((sesion.expira_en - ahora()).total_seconds())),
        "entrada": f"{url}/entrar" if url and sesion.estado == "lista" else None,
        "archivos": f"{url}/s/{sesion.id}/archivos" if url and sesion.estado == "lista" else None,
    }
    if token:
        datos["token"] = token
    return datos


def _viva_de(db: Session, usuario_id: str) -> Optional[NubeSesion]:
    """Sesión asignada o lista más reciente de la cuenta, o None."""
    return db.scalars(
        select(NubeSesion)
        .where(NubeSesion.usuario_id == usuario_id, NubeSesion.estado.in_(VIVAS))
        .order_by(NubeSesion.creada_en.desc())
    ).first()


def _usadas_hoy(db: Session, usuario_id: str) -> int:
    """Sesiones no fallidas de la cuenta en las últimas 24 h."""
    desde = ahora() - timedelta(hours=24)
    return db.scalar(
        select(func.count()).select_from(NubeSesion).where(
            NubeSesion.usuario_id == usuario_id, NubeSesion.creada_en >= desde, NubeSesion.estado != "fallida"
        )
    ) or 0


def _resumen_nodos() -> dict:
    """Nodos vivos, capacidad total y lugares libres."""
    momento = time.monotonic()
    vivos = [n for n in _nodos.values() if n.vivo(momento)]
    return {"nodos": len(vivos), "capacidad": sum(n.capacidad for n in vivos), "libres": sum(n.libres() for n in vivos)}


# --- Alumnos --------------------------------------------------------------------------


@router.get("/estado")
def estado(usuario: Optional[Usuario] = Depends(usuario_opcional), db: Session = Depends(obtener_db)):
    """GET /estado: lugares libres, reglas y si la cuenta puede pedir sesión."""
    datos = {
        **_resumen_nodos(),
        "minutos": minutos_sesion(),
        "por_dia": sesiones_por_dia(),
        "dominios": dominios_permitidos(),
        "puede": False,
        "motivo": motivo_sin_acceso(usuario),
        "usadas_hoy": 0,
        "listo": True,
    }
    if usuario is None or datos["motivo"]:
        return datos
    try:
        mantener(db)
        datos["usadas_hoy"] = 0 if usuario.rol == "admin" else _usadas_hoy(db, usuario.id)
        if usuario.rol == "admin":
            datos["por_dia"] = 999
    except SQLAlchemyError as error:
        if sin_esquema(error):
            return {**datos, "listo": False, "motivo": SIN_014}
        # [REVISION-BUG] error_bd(error, ruta, estado=500) exige 'ruta': las 5 llamadas error_bd(error) de este archivo lanzan
        # TypeError (500 sin folio, fuera del formato de api/errores.py) ante cualquier error de Oracle que no sea 'falta 014'.
        raise error_bd(error)
    datos["puede"] = True
    return datos


@router.post("/sesiones", status_code=201, dependencies=[Depends(LIMITE_PEDIDOS)])
def pedir_sesion(usuario: Usuario = Depends(usuario_requerido), db: Session = Depends(obtener_db)):
    """POST /sesiones: asigna una sesión al nodo con más lugares libres y devuelve su token (una sola vez)."""
    motivo = motivo_sin_acceso(usuario)
    if motivo:
        raise HTTPException(status_code=403, detail=motivo)
    try:
        mantener(db)
        viva = _viva_de(db, usuario.id)
        if viva is not None:
            raise HTTPException(status_code=409, detail="Ya tienes una sesión abierta. Entra a ella o termínala primero.")
        if usuario.rol != "admin" and _usadas_hoy(db, usuario.id) >= sesiones_por_dia():
            raise HTTPException(status_code=429, detail=f"Ya usaste tus {sesiones_por_dia()} sesiones de hoy. Vuelve mañana.")
        with _candado:
            momento = time.monotonic()
            candidatos = [n for n in _nodos.values() if n.vivo(momento) and n.libres() > 0 and n.url]
            if not candidatos:
                # 409 y no 503: el servidor está bien, lo que falta es lugar (el alumno reintenta).
                raise HTTPException(status_code=409, detail="Todos los lugares de Amatista Cloud están ocupados. Inténtalo en unos minutos.",
                                    headers={"Retry-After": "120"})
            # El nodo con más lugares libres; a igualdad, el de más capacidad.
            nodo = max(candidatos, key=lambda n: (n.libres(), n.capacidad))
            token = nuevo_token()
            sesion = NubeSesion(
                id=f"nube-{uuid.uuid4().hex[:24]}",
                usuario_id=usuario.id,
                nodo=nodo.nombre,
                estado="asignada",
                token_hash=hash_token(token),
                creada_en=ahora(),
                expira_en=ahora() + timedelta(minutes=minutos_sesion()),
            )
            nodo.abrir[sesion.id] = {
                "id": sesion.id,
                "token_hash": sesion.token_hash,
                "expira": time.time() + minutos_sesion() * 60,
                "minutos": minutos_sesion(),
            }
        db.add(sesion)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        if sin_esquema(error):
            raise HTTPException(status_code=503, detail=SIN_014)
        raise error_bd(error)
    return _publica(sesion, token)


@router.get("/sesiones/actual")
def sesion_actual(usuario: Usuario = Depends(usuario_requerido), db: Session = Depends(obtener_db)):
    """GET /sesiones/actual: la sesión viva de la cuenta o null."""
    try:
        mantener(db)
        viva = _viva_de(db, usuario.id)
    except SQLAlchemyError as error:
        if sin_esquema(error):
            raise HTTPException(status_code=503, detail=SIN_014)
        raise error_bd(error)
    return {"sesion": _publica(viva) if viva else None}


def _sesion_propia(db: Session, sesion_id: str, usuario: Usuario) -> NubeSesion:
    """La sesión si es de la cuenta (o si quien pide es admin); 404 si no."""
    sesion = db.get(NubeSesion, sesion_id)
    if sesion is None or (sesion.usuario_id != usuario.id and usuario.rol != "admin"):
        raise HTTPException(status_code=404, detail="No encontramos esa sesión.")
    return sesion


@router.post("/sesiones/{sesion_id}/token")
def nuevo_token_de_sesion(sesion_id: str, usuario: Usuario = Depends(usuario_requerido), db: Session = Depends(obtener_db)):
    """Otro dispositivo: token nuevo para la misma sesión. El anterior deja de servir."""
    sesion = _sesion_propia(db, sesion_id, usuario)
    if sesion.estado not in VIVAS or sesion.expira_en <= ahora():
        raise HTTPException(status_code=409, detail="Esa sesión ya terminó.")
    token = nuevo_token()
    sesion.token_hash = hash_token(token)
    db.commit()
    with _candado:
        nodo = _nodos.get(sesion.nodo)
        if nodo is not None:
            if sesion.id in nodo.abrir:
                nodo.abrir[sesion.id]["token_hash"] = sesion.token_hash
            else:
                nodo.tokens[sesion.id] = sesion.token_hash
    return _publica(sesion, token)


@router.post("/sesiones/{sesion_id}/terminar")
def terminar_sesion(sesion_id: str, usuario: Usuario = Depends(usuario_requerido), db: Session = Depends(obtener_db)):
    """POST /sesiones/{id}/terminar: cierra la sesión y libera el lugar."""
    sesion = _sesion_propia(db, sesion_id, usuario)
    if sesion.estado in VIVAS:
        _cerrar(db, sesion, "terminada", "la cerró el alumno" if sesion.usuario_id == usuario.id else "la cerró el equipo")
        db.commit()
    return _publica(sesion)


# --- Nodos ----------------------------------------------------------------------------


class Latido(BaseModel):
    """Cuerpo del latido de un nodo: URL pública, capacidad, sesiones vivas y métricas."""
    url: str = Field(max_length=200)
    capacidad: int = Field(ge=0, le=64)
    sesiones: List[str] = Field(default_factory=list, max_length=64)  # abiertas y las que está abriendo
    arquitectura: Optional[str] = Field(default=None, max_length=20)
    nucleos: Optional[int] = Field(default=None, ge=0, le=1024)
    ram_gb: Optional[float] = Field(default=None, ge=0)
    ram_libre_gb: Optional[float] = Field(default=None, ge=0)
    carga: Optional[float] = Field(default=None, ge=0)
    version: Optional[str] = Field(default=None, max_length=20)


def url_valida(url: str) -> bool:
    """Solo https://host[:puerto] (o http://localhost en pruebas)."""
    if re.fullmatch(r"https://[a-z0-9.-]+(:\d+)?", url):
        return True
    # http solo para pruebas en la misma máquina.
    return bool(re.fullmatch(r"http://(localhost|127\.0\.0\.1)(:\d+)?", url))


@router.post("/nodo/latido")
def latido(datos: Latido, nodo: Nodo = Depends(nodo_actual), db: Session = Depends(obtener_db)):
    """POST /nodo/latido: actualiza el nodo y responde {abrir, cerrar, tokens, latido_s}."""
    url = datos.url.rstrip("/").lower()
    if not url_valida(url):
        raise HTTPException(status_code=422, detail="La URL pública del nodo debe ser https://host (o http://localhost en pruebas).")
    with _candado:
        nodo.ultimo = time.monotonic()
        nodo.capacidad = datos.capacidad
        nodo.url = url
        nodo.ocupadas = set(datos.sesiones)
        nodo.datos = datos.model_dump(exclude={"url", "capacidad", "sesiones"})
        # Lo que el nodo ya abrió deja de estar «por abrir».
        for sesion_id in nodo.ocupadas:
            nodo.abrir.pop(sesion_id, None)
    try:
        mantener(db)
        if nodo.ocupadas:
            # Sesiones que el nodo tiene abiertas pero aquí ya no están vivas: que las cierre.
            vivas = set(db.scalars(select(NubeSesion.id).where(NubeSesion.id.in_(nodo.ocupadas), NubeSesion.estado.in_(VIVAS))).all())
            with _candado:
                nodo.cerrar |= nodo.ocupadas - vivas
    except SQLAlchemyError as error:
        if sin_esquema(error):
            raise HTTPException(status_code=503, detail=SIN_014)
        raise error_bd(error)
    with _candado:
        # Solo se ordena cerrar lo que el nodo todavía tiene (incluye las que está abriendo).
        nodo.cerrar &= nodo.ocupadas
        respuesta = {
            "abrir": list(nodo.abrir.values()),
            "cerrar": sorted(nodo.cerrar),
            "tokens": [{"id": k, "token_hash": v} for k, v in nodo.tokens.items()],
            "latido_s": 3 if nodo.abrir else 5,
        }
        # «cerrar» se repite en cada latido hasta que el nodo deje de reportar la sesión.
        nodo.tokens.clear()
    return respuesta


class Reporte(BaseModel):
    """Informe de un nodo sobre una sesión: lista, fallida o terminada."""
    estado: str = Field(pattern="^(lista|fallida|terminada)$")
    motivo: Optional[str] = Field(default=None, max_length=40)


@router.post("/nodo/sesiones/{sesion_id}")
def reporte_de_nodo(sesion_id: str, datos: Reporte, nodo: Nodo = Depends(nodo_actual), db: Session = Depends(obtener_db)):
    """POST /nodo/sesiones/{id}: aplica el estado que reporta el nodo."""
    sesion = db.get(NubeSesion, sesion_id)
    if sesion is None or sesion.nodo != nodo.nombre:
        raise HTTPException(status_code=404, detail="Esa sesión no es de este nodo.")
    with _candado:
        nodo.abrir.pop(sesion_id, None)
        if datos.estado == "lista":
            nodo.ocupadas.add(sesion_id)
        else:
            nodo.ocupadas.discard(sesion_id)
            nodo.cerrar.discard(sesion_id)
    if sesion.estado in VIVAS:
        if datos.estado == "lista" and sesion.estado == "asignada":
            sesion.estado = "lista"
        elif datos.estado != "lista":
            _cerrar(db, sesion, datos.estado, datos.motivo or ("error del nodo" if datos.estado == "fallida" else "fin"))
        db.commit()
    return {"estado": sesion.estado}


# --- Equipo ---------------------------------------------------------------------------


@router.get("/admin")
def panel(_: Usuario = Depends(requiere_rol("admin")), db: Session = Depends(obtener_db)):
    """GET /admin: nodos, sesiones vivas y las últimas 50 con el correo de cada cuenta."""
    momento = time.monotonic()
    try:
        mantener(db)
        vivas = db.scalars(select(NubeSesion).where(NubeSesion.estado.in_(VIVAS)).order_by(NubeSesion.creada_en)).all()
        ultimas = db.scalars(select(NubeSesion).order_by(NubeSesion.creada_en.desc()).limit(50)).all()
        correos = dict(db.execute(select(Usuario.id, Usuario.email).where(Usuario.id.in_({s.usuario_id for s in ultimas} | {s.usuario_id for s in vivas})))
                       .all())
    except SQLAlchemyError as error:
        if sin_esquema(error):
            raise HTTPException(status_code=503, detail=SIN_014)
        raise error_bd(error)

    def fila(s: NubeSesion) -> dict:
        """Sesión pública más correo y datos de cierre."""
        return {**_publica(s), "correo": correos.get(s.usuario_id), "motivo_fin": s.motivo_fin,
                "terminada_en": s.terminada_en.isoformat() + "Z" if s.terminada_en else None}

    with _candado:
        nodos = [n.publico(momento) for n in _nodos.values()]
    return {"nodos": nodos, "vivas": [fila(s) for s in vivas], "ultimas": [fila(s) for s in ultimas], "resumen": _resumen_nodos()}


@router.post("/admin/sesiones/{sesion_id}/terminar")
def terminar_como_equipo(sesion_id: str, admin: Usuario = Depends(requiere_rol("admin")), db: Session = Depends(obtener_db)):
    """POST /admin/sesiones/{id}/terminar: un admin cierra cualquier sesión."""
    return terminar_sesion(sesion_id, admin, db)
