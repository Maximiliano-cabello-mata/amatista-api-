"""Envío de códigos de verificación por correo (SMTP con STARTTLS).

Variables en backend/.env:
- SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
  Con el puerto 465 se usa TLS directo (SMTP_SSL) en vez de STARTTLS.
- AMATISTA_MOSTRAR_CODIGOS=1 (solo desarrollo): el código se escribe en la
  consola y la API lo devuelve como `codigo_dev`. Nunca en producción.

Sin SMTP_HOST no se envía nada y se registra una advertencia SIN el código.
Un fallo de SMTP nunca tumba la petición: se registra y el alumno puede
pedir otro código con "reenviar". Nunca se registran contraseñas ni tokens.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from seguridad import DURACION_CODIGO

log = logging.getLogger("amatista.correo")

ESPERA_SMTP = 10  # segundos: un servidor SMTP lento no deja colgada la API

ASUNTOS = {
    "correo": "Tu código para confirmar tu correo en Amatista",
    "password": "Tu código para recuperar tu cuenta de Amatista",
}
MOTIVOS = {
    "correo": "confirmar tu correo",
    "password": "restablecer tu contraseña",
}


def mostrar_codigos() -> bool:
    return os.getenv("AMATISTA_MOSTRAR_CODIGOS") == "1"


def ocultar_correo(email: str) -> str:
    """'maria@dominio.com' → 'm***@dominio.com' (para la consola)."""
    usuario, _, dominio = (email or "").partition("@")
    return f"{usuario[:1]}***@{dominio}" if dominio else "***"


def armar_mensaje(email: str, codigo: str, proposito: str, remitente: str) -> EmailMessage:
    minutos = int(DURACION_CODIGO.total_seconds() // 60)
    motivo = MOTIVOS.get(proposito, "continuar")
    mensaje = EmailMessage()
    mensaje["Subject"] = ASUNTOS.get(proposito, "Tu código de Amatista")
    mensaje["From"] = remitente
    mensaje["To"] = email
    mensaje["Date"] = formatdate(localtime=False)
    dominio = remitente.rpartition("@")[2].strip("> ") if "@" in remitente else ""
    mensaje["Message-ID"] = make_msgid(domain=dominio or None)
    mensaje.set_content(
        "Hola:\n\n"
        f"Tu código para {motivo} en Amatista es:\n\n"
        f"    {codigo}\n\n"
        f"Vence en {minutos} minutos. Si no lo pediste, ignora este mensaje: tu cuenta sigue segura.\n\n"
        "— Amatista\n"
    )
    return mensaje


def enviar_codigo(email: str, codigo: str, proposito: str) -> bool:
    """Envía el código de 6 dígitos. Devuelve True si salió por SMTP.

    proposito: 'correo' (confirmar el correo) o 'password' (recuperar la cuenta).
    """
    if mostrar_codigos():
        log.warning("[MODO DESARROLLO] Código de %s para %s: %s", proposito, email, codigo)

    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        log.warning(
            "SMTP_HOST no está configurado: no se envió el código de %s a %s.",
            proposito,
            ocultar_correo(email),
        )
        return False

    try:
        puerto = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        puerto = 587
    usuario = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    remitente = os.getenv("SMTP_FROM", "").strip() or usuario or f"no-responder@{host}"
    contexto = ssl.create_default_context()

    try:
        mensaje = armar_mensaje(email, codigo, proposito, remitente)
        if puerto == 465:
            conexion = smtplib.SMTP_SSL(host, puerto, timeout=ESPERA_SMTP, context=contexto)
        else:
            conexion = smtplib.SMTP(host, puerto, timeout=ESPERA_SMTP)
        with conexion as smtp:
            if puerto != 465:
                smtp.starttls(context=contexto)
            if usuario:
                smtp.login(usuario, password)
            smtp.send_message(mensaje)
    except (smtplib.SMTPException, OSError, ValueError) as error:
        # Sin el código ni la contraseña de SMTP: solo el tipo de error.
        log.error(
            "No se pudo enviar el código de %s a %s por SMTP (%s: %s).",
            proposito,
            ocultar_correo(email),
            type(error).__name__,
            error,
        )
        return False

    log.info("Código de %s enviado a %s.", proposito, ocultar_correo(email))
    return True
