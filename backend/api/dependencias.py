"""Identidad de quien llama: sesiones, roles y alumnos anónimos.

El navegador manda el token en "Authorization: Bearer <token>" (también se
acepta la cabecera X-Sesion-Id del informe del 1 de octubre). El servidor
obtiene la identidad SOLO de la sesión validada: un alumno no puede leer ni
modificar el progreso de otro cambiando usuario_id en el cuerpo.

Alumnos anónimos (offline-first): sin sesión se aceptan escrituras para un
usuario_id que NO pertenece a una cuenta registrada ni fue fusionado. Así la
PWA funciona sin cuenta y las versiones viejas en caché siguen sincronizando.
"""
from datetime import timedelta
from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database.conexion import obtener_db
from database.modelos import Sesion, Usuario, ahora
from seguridad import DURACION_SESION, hash_token

# ultimo_acceso se escribe como mucho cada 10 minutos por sesión: evita una
# escritura en Oracle por cada petición.
REFRESCO_ACCESO = timedelta(minutes=10)
# Sesiones creadas por el add-on de Blender (api/addon.py) y lo único que pueden usar.
PREFIJO_ADDON = "blender-addon"
RUTAS_ADDON = ("/api/addon/", "/api/blender/")


def extraer_token(request: Request) -> Optional[str]:
    autorizacion = request.headers.get("authorization", "")
    if autorizacion.lower().startswith("bearer "):
        token = autorizacion[7:].strip()
        if token:
            return token
    token = request.headers.get("x-sesion-id", "").strip()
    return token or None


def sesion_de_token(db: Session, token: str) -> Optional[Sesion]:
    """Sesión activa y vigente para ese token, o None."""
    if not token or len(token) > 200:
        return None
    sesion = db.get(Sesion, hash_token(token))
    if sesion is None or not sesion.activa:
        return None
    momento = ahora()
    if sesion.expira_en is not None and sesion.expira_en <= momento:
        return None
    return sesion


def usuario_opcional(request: Request, db: Session = Depends(obtener_db)) -> Optional[Usuario]:
    """Usuario de la sesión, o None si no se mandó token.

    Un token inválido o vencido responde 401 (en vez de tratarse como
    anónimo) para que la PWA sepa que debe volver a iniciar sesión.
    """
    token = extraer_token(request)
    if token is None:
        return None
    sesion = sesion_de_token(db, token)
    usuario = db.get(Usuario, sesion.usuario_id) if sesion else None
    if usuario is None:
        raise HTTPException(status_code=401, detail="Tu sesión venció. Vuelve a iniciar sesión.")
    if (sesion.dispositivo or "").startswith(PREFIJO_ADDON) and not request.url.path.startswith(RUTAS_ADDON):
        # La sesión de Blender solo sirve para el add-on: si alguien copia ese
        # token no puede cambiar la contraseña ni entrar al panel.
        raise HTTPException(status_code=403, detail="Esta sesión es del add-on de Blender y no sirve aquí.")

    momento = ahora()
    if sesion.ultimo_acceso is None or momento - sesion.ultimo_acceso > REFRESCO_ACCESO:
        sesion.ultimo_acceso = momento
        sesion.expira_en = momento + DURACION_SESION  # expiración deslizante
        usuario.ultimo_acceso = momento
        db.commit()
    request.state.sesion_id = sesion.id
    return usuario


def usuario_requerido(usuario: Optional[Usuario] = Depends(usuario_opcional)) -> Usuario:
    if usuario is None:
        raise HTTPException(status_code=401, detail="Inicia sesión para continuar.")
    return usuario


def requiere_rol(*roles: str) -> Callable[..., Usuario]:
    """Dependencia que exige uno de los roles indicados (403 si no)."""

    def verificar(usuario: Usuario = Depends(usuario_requerido)) -> Usuario:
        if usuario.rol not in roles:
            raise HTTPException(status_code=403, detail="No tienes permiso para esta acción.")
        return usuario

    return verificar


def es_cuenta_registrada(usuario: Optional[Usuario]) -> bool:
    return usuario is not None and (bool(usuario.password_hash) or bool(usuario.fusionado_en))


def resolver_alumno(db: Session, actual: Optional[Usuario], usuario_id: Optional[str]) -> str:
    """Id del alumno sobre el que se escribe (progreso, eventos).

    - Con sesión: siempre el usuario de la sesión (se ignora el del cuerpo).
    - Sin sesión: el usuario_id del cuerpo, solo si es un alumno anónimo.
    """
    if actual is not None:
        return actual.id
    usuario_id = (usuario_id or "").strip()
    if not usuario_id:
        raise HTTPException(status_code=422, detail="Envía usuario_id o inicia sesión.")
    if es_cuenta_registrada(db.get(Usuario, usuario_id)):
        raise HTTPException(status_code=401, detail="Inicia sesión para sincronizar el progreso de esta cuenta.")
    return usuario_id


def puede_ver_alumno(db: Session, actual: Optional[Usuario], usuario_id: str) -> bool:
    """Lectura de progreso: el propio alumno, profesores y administradores.

    El progreso de un alumno anónimo se puede leer sin sesión con su id
    (el id es un UUID que solo conoce su dispositivo).
    """
    if actual is not None and (actual.id == usuario_id or actual.rol in ("profesor", "admin")):
        return True
    return not es_cuenta_registrada(db.get(Usuario, usuario_id))
