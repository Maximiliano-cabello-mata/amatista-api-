"""Cuentas: registro, inicio de sesión, confirmación de correo y recuperación.

- El token de sesión solo lo conoce el navegador; en SESIONES se guarda su
  SHA-256 (seguridad.hash_token) con expiración deslizante de 30 días.
- Los mensajes de iniciar-sesion, reenviar-codigo y recuperar no revelan si
  un correo está registrado (y cuando no existe se gasta el mismo tiempo en
  verificar una contraseña señuelo).
- 5 contraseñas incorrectas seguidas bloquean la cuenta 15 minutos (423).
- Registro con el id local de un alumno anónimo: esa misma fila se convierte
  en la cuenta y conserva su progreso. Inicio de sesión con el id local de un
  anónimo: su progreso se fusiona con la cuenta (api/fusion.py).

Variables: AMATISTA_ADMINS="correo1,correo2" (rol admin al registrarse o
entrar), AMATISTA_REQUIERE_CONFIRMACION=1 (no se entra sin confirmar el
correo), AMATISTA_MOSTRAR_CODIGOS=1 (solo desarrollo: devuelve codigo_dev).
"""
import logging
import math
import os
import re
import uuid
from datetime import timedelta
from typing import Annotated, Optional, Set

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel
from pydantic_core import PydanticCustomError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api.comun import error_bd, usuario_publico
from api.correo import enviar_codigo, mostrar_codigos
from api.dependencias import usuario_requerido
from api.fusion import es_anonimo_fusionable, fusionar_alumno
from api.limites import limitar
from database.conexion import obtener_db
from database.modelos import EventoAprendizaje, ProgresoLeccion, Sesion, Usuario, ahora
from seguridad import (
    DURACION_CODIGO,
    DURACION_SESION,
    MAX_INTENTOS_CODIGO,
    codigos_iguales,
    hash_codigo,
    hash_password,
    hash_token,
    normalizar_email,
    nuevo_codigo,
    nuevo_token,
    requiere_rehash,
    verificar_password,
    verificar_password_señuelo,
)

router = APIRouter(prefix="/api/auth", tags=["autenticacion"])
log = logging.getLogger("amatista.auth")

MAX_FALLOS = 5
DURACION_BLOQUEO = timedelta(minutes=15)

MENSAJE_CREDENCIALES = "Correo o contraseña incorrectos."
MENSAJE_CODIGO = "El código no es válido o ya venció. Pide uno nuevo."
MENSAJE_DUPLICADO = "Ya existe una cuenta con ese correo. Inicia sesión o recupera tu contraseña."
MENSAJE_SIN_CONFIRMAR = (
    "Confirma tu correo antes de iniciar sesión: escribe el código que te enviamos o pide uno nuevo."
)

# Peticiones por minuto y por IP. Generosos porque un salón de clases sale a
# internet con una sola IP; el bloqueo por cuenta frena el abuso dirigido.
# Un aula entera comparte una sola IP pública: los límites de registro e
# inicio alcanzan para un grupo de 40 a la vez. Contra adivinar contraseñas
# protege además el bloqueo de la cuenta tras 5 fallos (MAX_FALLOS).
LIMITE_REGISTRO = limitar(60)
LIMITE_INICIO_MAXIMO = 60
LIMITE_INICIO = limitar(LIMITE_INICIO_MAXIMO)
LIMITE_CORREOS = limitar(10)  # recuperar y reenviar-codigo mandan correos
LIMITE_CODIGOS = limitar(20)
LIMITE_CAMBIO = limitar(10)

PATRON_CORREO = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{1,59})$"
)
PATRON_TELEFONO = re.compile(r"^[0-9+()\-.\s]+$")
# Ids que se pueden adoptar como id de cuenta ("alumno-<uuid>"). Nunca un correo.
PATRON_ID_LOCAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")


# --- Reglas de validación (también las usa herramientas/crear_admin.py) ------


def problema_correo(email: str) -> Optional[str]:
    correo = normalizar_email(email or "")
    if len(correo) > 100 or not PATRON_CORREO.match(correo):
        return "Escribe un correo válido, por ejemplo nombre@dominio.com."
    return None


def problema_password(password: str) -> Optional[str]:
    if not 8 <= len(password or "") <= 128:
        return "La contraseña debe tener entre 8 y 128 caracteres."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "La contraseña debe incluir al menos una letra y un número."
    return None


def _error(tipo: str, mensaje: str) -> PydanticCustomError:
    # PydanticCustomError deja el mensaje tal cual (sin "Value error, ...").
    return PydanticCustomError(tipo, mensaje)


def _correo(valor: str) -> str:
    problema = problema_correo(valor)
    if problema:
        raise _error("correo_invalido", problema)
    return normalizar_email(valor)


def _password_nueva(valor: str) -> str:
    problema = problema_password(valor)
    if problema:
        raise _error("password_invalida", problema)
    return valor


def _password_escrita(valor: str) -> str:
    # Al iniciar sesión no se aplican las reglas nuevas (cuentas viejas),
    # solo un tope para no derivar textos enormes.
    if not valor:
        raise _error("password_vacia", "Escribe tu contraseña.")
    if len(valor) > 1024:
        raise _error("password_larga", "La contraseña es demasiado larga.")
    return valor


def _nombre(valor: str) -> str:
    valor = " ".join(valor.split())
    if not valor:
        raise _error("nombre_vacio", "Escribe tu nombre.")
    if len(valor) > 150:
        raise _error("nombre_largo", "El nombre admite como máximo 150 caracteres.")
    return valor


def _telefono(valor: Optional[str]) -> Optional[str]:
    valor = (valor or "").strip()
    if not valor:
        return None
    if len(valor) > 25 or not PATRON_TELEFONO.match(valor) or not any(c.isdigit() for c in valor):
        raise _error("telefono_invalido", "El teléfono admite hasta 25 caracteres: números, espacios, +, -, ( y ).")
    return valor


def _id_local(valor: Optional[str]) -> Optional[str]:
    valor = (valor or "").strip()
    if len(valor) > 100:
        raise _error("id_local_largo", "El identificador local admite como máximo 100 caracteres.")
    return valor or None


def _dispositivo(valor: Optional[str]) -> Optional[str]:
    valor = " ".join((valor or "").split())
    return valor[:200] or None


def _codigo(valor: str) -> str:
    valor = re.sub(r"[\s-]", "", valor or "")
    if not re.fullmatch(r"\d{6}", valor):
        raise _error("codigo_invalido", "El código tiene 6 dígitos.")
    return valor


Correo = Annotated[str, AfterValidator(_correo)]
PasswordNueva = Annotated[str, AfterValidator(_password_nueva)]
PasswordEscrita = Annotated[str, AfterValidator(_password_escrita)]
Nombre = Annotated[str, AfterValidator(_nombre)]
Telefono = Annotated[Optional[str], AfterValidator(_telefono)]
IdLocal = Annotated[Optional[str], AfterValidator(_id_local)]
Dispositivo = Annotated[Optional[str], AfterValidator(_dispositivo)]
Codigo = Annotated[str, AfterValidator(_codigo)]


class SolicitudRegistro(BaseModel):
    nombre: Nombre
    email: Correo
    password: PasswordNueva
    telefono: Telefono = None
    usuario_local_id: IdLocal = None
    dispositivo: Dispositivo = None


class SolicitudInicio(BaseModel):
    email: Correo
    password: PasswordEscrita
    usuario_local_id: IdLocal = None
    dispositivo: Dispositivo = None


class SolicitudPerfil(BaseModel):
    nombre: Optional[Nombre] = None
    telefono: Telefono = None


class SolicitudCorreo(BaseModel):
    email: Correo


class SolicitudConfirmacion(BaseModel):
    email: Correo
    codigo: Codigo


class SolicitudRestablecer(BaseModel):
    email: Correo
    codigo: Codigo
    password: PasswordNueva


class SolicitudCambioPassword(BaseModel):
    actual: PasswordEscrita
    nueva: PasswordNueva


# --- Utilidades ---------------------------------------------------------------


def correos_admin() -> Set[str]:
    return {normalizar_email(c) for c in os.getenv("AMATISTA_ADMINS", "").split(",") if c.strip()}


def aplicar_admin_inicial(usuario: Usuario) -> None:
    if usuario.email and usuario.email in correos_admin() and usuario.rol != "admin":
        usuario.rol = "admin"
        log.info("Cuenta %s promovida a admin por AMATISTA_ADMINS.", usuario.id)


def requiere_confirmacion() -> bool:
    return os.getenv("AMATISTA_REQUIERE_CONFIRMACION") == "1"


def buscar_por_correo(db: Session, email: str) -> Optional[Usuario]:
    return db.scalar(select(Usuario).where(Usuario.email == email))


def abrir_sesion(db: Session, usuario: Usuario, dispositivo: Optional[str], request: Request) -> str:
    """Crea la sesión y devuelve el token (lo único que ve el navegador)."""
    token = nuevo_token()
    momento = ahora()
    descripcion = dispositivo or _dispositivo(request.headers.get("user-agent")) or "web"
    db.add(
        Sesion(
            id=hash_token(token),
            usuario_id=usuario.id,
            dispositivo=descripcion[:200],
            activa=1,
            creado_en=momento,
            ultimo_acceso=momento,
            expira_en=momento + DURACION_SESION,
        )
    )
    usuario.ultimo_acceso = momento
    return token


def generar_codigo(usuario: Usuario, proposito: str) -> str:
    """Nuevo código de 6 dígitos (reemplaza al anterior). Se guarda solo su hash."""
    codigo = nuevo_codigo()
    usuario.codigo_verificacion = hash_codigo(usuario.email, codigo)
    usuario.codigo_proposito = proposito
    usuario.codigo_expira = ahora() + DURACION_CODIGO
    usuario.codigo_intentos = 0
    return codigo


def limpiar_codigo(usuario: Usuario) -> None:
    usuario.codigo_verificacion = None
    usuario.codigo_proposito = None
    usuario.codigo_expira = None
    usuario.codigo_intentos = 0


def codigo_correcto(usuario: Optional[Usuario], codigo: str, proposito: str) -> bool:
    """Comprueba el código. Cada fallo cuenta; al llegar al máximo se invalida."""
    if usuario is None or not usuario.codigo_verificacion or usuario.codigo_proposito != proposito:
        return False
    if usuario.codigo_expira is None or usuario.codigo_expira <= ahora():
        limpiar_codigo(usuario)
        return False
    if codigos_iguales(hash_codigo(usuario.email, codigo), usuario.codigo_verificacion):
        return True
    usuario.codigo_intentos = (usuario.codigo_intentos or 0) + 1
    if usuario.codigo_intentos >= MAX_INTENTOS_CODIGO:
        limpiar_codigo(usuario)
    return False


def cerrar_sesiones(db: Session, usuario_id: str, excepto: Optional[str] = None) -> int:
    condiciones = [Sesion.usuario_id == usuario_id, Sesion.activa == 1]
    if excepto:
        condiciones.append(Sesion.id != excepto)
    resultado = db.execute(
        update(Sesion).where(*condiciones).values(activa=0),
        execution_options={"synchronize_session": False},
    )
    return resultado.rowcount or 0


def con_codigo_dev(respuesta: dict, codigo: Optional[str]) -> dict:
    if codigo and mostrar_codigos():
        respuesta["codigo_dev"] = codigo
    return respuesta


def minutos_restantes(usuario: Usuario) -> int:
    return max(1, math.ceil((usuario.bloqueado_hasta - ahora()).total_seconds() / 60))


def error_bloqueo(usuario: Usuario) -> HTTPException:
    minutos = minutos_restantes(usuario)
    unidad = "minuto" if minutos == 1 else "minutos"
    return HTTPException(
        status_code=423,
        detail=(
            "Por seguridad, tu cuenta está bloqueada por varios intentos fallidos. "
            f"Intenta de nuevo en {minutos} {unidad}."
        ),
        headers={"Retry-After": str(minutos * 60)},
    )


# --- Rutas --------------------------------------------------------------------


@router.post("/registro", status_code=201, dependencies=[Depends(LIMITE_REGISTRO)])
def registro(
    datos: SolicitudRegistro,
    request: Request,
    tareas: BackgroundTasks,
    db: Session = Depends(obtener_db),
):
    try:
        if buscar_por_correo(db, datos.email) is not None:
            raise HTTPException(status_code=409, detail=MENSAJE_DUPLICADO)

        local_id = datos.usuario_local_id
        local = db.get(Usuario, local_id) if local_id else None
        convertir = bool(local_id and PATRON_ID_LOCAL.match(local_id)) and (
            local is None or es_anonimo_fusionable(local)
        )
        if convertir:
            # La fila del alumno anónimo se convierte en la cuenta: conserva su progreso.
            usuario = local or Usuario(id=local_id)
        else:
            usuario = Usuario(id=f"usr-{uuid.uuid4()}")
        if usuario not in db:
            db.add(usuario)

        momento = ahora()
        usuario.nombre = datos.nombre
        usuario.email = datos.email
        usuario.telefono = datos.telefono
        usuario.password_hash = hash_password(datos.password)
        usuario.rol = "alumno"
        usuario.correo_confirmado = 0
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        usuario.es_prueba = usuario.es_prueba or 0
        usuario.creado_en = momento  # fecha de la cuenta (cuenta para "registros")
        aplicar_admin_inicial(usuario)
        codigo = generar_codigo(usuario, "correo")
        db.flush()  # la cuenta debe existir antes que su sesión y sus eventos (FK en Oracle)

        if convertir:
            lecciones = db.scalar(
                select(func.count()).select_from(ProgresoLeccion).where(ProgresoLeccion.usuario_id == usuario.id)
            )
        else:
            # Id local que no se puede adoptar (por ejemplo un correo de la
            # versión vieja): su progreso anónimo se fusiona con la cuenta nueva.
            lecciones = fusionar_alumno(db, local_id, usuario.id) if local is not None else 0

        token = None
        if not requiere_confirmacion():
            token = abrir_sesion(db, usuario, datos.dispositivo, request)
        db.add(
            EventoAprendizaje(
                id=str(uuid.uuid4()),
                usuario_id=usuario.id,
                tipo="account_created",
                ocurrido_en=momento,
                es_prueba=usuario.es_prueba or 0,
            )
        )
        db.commit()
    except IntegrityError:
        # Dos registros simultáneos con el mismo correo.
        db.rollback()
        raise HTTPException(status_code=409, detail=MENSAJE_DUPLICADO)
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/registro")

    # El correo sale después de responder: un SMTP lento o caído no frena el registro.
    tareas.add_task(enviar_codigo, usuario.email, codigo, "correo")
    respuesta = {"token": token, "usuario": usuario_publico(usuario), "fusion": {"lecciones": lecciones or 0}}
    if token is None:
        respuesta["requiere_confirmacion"] = True
    return con_codigo_dev(respuesta, codigo)


@router.post("/iniciar-sesion", dependencies=[Depends(LIMITE_INICIO)])
def iniciar_sesion(datos: SolicitudInicio, request: Request, db: Session = Depends(obtener_db)):
    try:
        usuario = buscar_por_correo(db, datos.email)
        if usuario is None or not usuario.password_hash:
            verificar_password_señuelo(datos.password)  # mismo tiempo que con una cuenta real
            raise HTTPException(status_code=401, detail=MENSAJE_CREDENCIALES)

        if usuario.bloqueado_hasta is not None and usuario.bloqueado_hasta > ahora():
            raise error_bloqueo(usuario)

        if not verificar_password(datos.password, usuario.password_hash):
            usuario.intentos_fallidos = (usuario.intentos_fallidos or 0) + 1
            bloqueada = usuario.intentos_fallidos >= MAX_FALLOS
            if bloqueada:
                usuario.bloqueado_hasta = ahora() + DURACION_BLOQUEO
                usuario.intentos_fallidos = 0
                log.warning("Cuenta %s bloqueada 15 minutos por contraseñas incorrectas.", usuario.id)
            db.commit()
            if bloqueada:
                raise error_bloqueo(usuario)
            raise HTTPException(status_code=401, detail=MENSAJE_CREDENCIALES)

        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        if requiere_rehash(usuario.password_hash):
            usuario.password_hash = hash_password(datos.password)
        aplicar_admin_inicial(usuario)
        if requiere_confirmacion() and not usuario.correo_confirmado:
            db.commit()
            raise HTTPException(status_code=403, detail=MENSAJE_SIN_CONFIRMAR)

        lecciones = fusionar_alumno(db, datos.usuario_local_id, usuario.id) if datos.usuario_local_id else 0
        token = abrir_sesion(db, usuario, datos.dispositivo, request)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/iniciar-sesion")

    return {"token": token, "usuario": usuario_publico(usuario), "fusion": {"lecciones": lecciones}}


@router.post("/cerrar-sesion")
def cerrar_sesion(
    request: Request,
    usuario: Usuario = Depends(usuario_requerido),
    db: Session = Depends(obtener_db),
):
    try:
        sesion = db.get(Sesion, request.state.sesion_id)
        if sesion is not None:
            sesion.activa = 0
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/cerrar-sesion")
    return {"ok": True}


@router.post("/cerrar-todas")
def cerrar_todas(usuario: Usuario = Depends(usuario_requerido), db: Session = Depends(obtener_db)):
    try:
        cerradas = cerrar_sesiones(db, usuario.id)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/cerrar-todas")
    return {"cerradas": cerradas}


@router.get("/yo")
def yo(usuario: Usuario = Depends(usuario_requerido)):
    return usuario_publico(usuario)


@router.patch("/yo")
def actualizar_yo(
    datos: SolicitudPerfil,
    usuario: Usuario = Depends(usuario_requerido),
    db: Session = Depends(obtener_db),
):
    try:
        if datos.nombre is not None:
            usuario.nombre = datos.nombre
        if "telefono" in datos.model_fields_set:
            usuario.telefono = datos.telefono  # vacío o null lo borra
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/yo")
    return usuario_publico(usuario)


@router.post("/confirmar-correo", dependencies=[Depends(LIMITE_CODIGOS)])
def confirmar_correo(datos: SolicitudConfirmacion, db: Session = Depends(obtener_db)):
    try:
        usuario = buscar_por_correo(db, datos.email)
        if not codigo_correcto(usuario, datos.codigo, "correo"):
            db.commit()  # guarda el intento fallido
            raise HTTPException(status_code=400, detail=MENSAJE_CODIGO)
        usuario.correo_confirmado = 1
        limpiar_codigo(usuario)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/confirmar-correo")
    return {"ok": True, "usuario": usuario_publico(usuario)}


@router.post("/reenviar-codigo", dependencies=[Depends(LIMITE_CORREOS)])
def reenviar_codigo(datos: SolicitudCorreo, tareas: BackgroundTasks, db: Session = Depends(obtener_db)):
    """Siempre responde lo mismo: no revela si el correo está registrado."""
    codigo = None
    try:
        usuario = buscar_por_correo(db, datos.email)
        if usuario is not None and usuario.password_hash and not usuario.correo_confirmado:
            codigo = generar_codigo(usuario, "correo")
            db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/reenviar-codigo")
    if codigo:
        tareas.add_task(enviar_codigo, datos.email, codigo, "correo")
    return con_codigo_dev({"ok": True}, codigo)


@router.post("/recuperar", dependencies=[Depends(LIMITE_CORREOS)])
def recuperar(datos: SolicitudCorreo, tareas: BackgroundTasks, db: Session = Depends(obtener_db)):
    """Envía un código para restablecer la contraseña. No revela si el correo existe."""
    codigo = None
    try:
        usuario = buscar_por_correo(db, datos.email)
        if usuario is not None and usuario.password_hash:
            codigo = generar_codigo(usuario, "password")
            db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/recuperar")
    if codigo:
        tareas.add_task(enviar_codigo, datos.email, codigo, "password")
    return con_codigo_dev({"ok": True}, codigo)


@router.post("/restablecer", dependencies=[Depends(LIMITE_CODIGOS)])
def restablecer(datos: SolicitudRestablecer, db: Session = Depends(obtener_db)):
    try:
        usuario = buscar_por_correo(db, datos.email)
        if not codigo_correcto(usuario, datos.codigo, "password"):
            db.commit()
            raise HTTPException(status_code=400, detail=MENSAJE_CODIGO)
        usuario.password_hash = hash_password(datos.password)
        usuario.correo_confirmado = 1  # demostró que tiene acceso al buzón
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        limpiar_codigo(usuario)
        cerrar_sesiones(db, usuario.id)  # quien tuviera la contraseña vieja queda fuera
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/restablecer")
    return {"ok": True}


@router.post("/cambiar-password", dependencies=[Depends(LIMITE_CAMBIO)])
def cambiar_password(
    datos: SolicitudCambioPassword,
    request: Request,
    usuario: Usuario = Depends(usuario_requerido),
    db: Session = Depends(obtener_db),
):
    if not verificar_password(datos.actual, usuario.password_hash):
        raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
    if datos.nueva == datos.actual:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe ser distinta de la actual.")
    try:
        usuario.password_hash = hash_password(datos.nueva)
        cerrar_sesiones(db, usuario.id, excepto=request.state.sesion_id)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise error_bd(error, "/api/auth/cambiar-password")
    return {"ok": True}
