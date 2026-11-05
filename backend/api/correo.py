"""Envío de códigos de verificación por correo (Brevo API v3 y SMTP con STARTTLS).

Variables en backend/.env:
- BREVO_API_KEY: si está definida, envía a través de la API REST v3 de Brevo
  (https://api.brevo.com/v3/smtp/email). Si falla o no está, usa SMTP.
- SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
  Con el puerto 465 se usa TLS directo (SMTP_SSL) en vez de STARTTLS.
- AMATISTA_MOSTRAR_CODIGOS=1 (solo desarrollo): el código se escribe en la
  consola y la API lo devuelve como `codigo_dev`. Nunca en producción.

Sin BREVO_API_KEY ni SMTP_HOST no se envía nada y se registra una advertencia
SIN el código. Un fallo de envío nunca tumba la petición: se registra y el
alumno puede pedir otro código con "reenviar". Nunca se registran contraseñas ni tokens.
"""
import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Optional

from seguridad import DURACION_CODIGO

log = logging.getLogger("amatista.correo")

ESPERA_SMTP = 10   # segundos: un servidor SMTP lento no deja colgada la API
ESPERA_BREVO = 10  # segundos para la API REST de Brevo

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


def extraer_email_puro(remitente: str) -> str:
    """Extrae el email de 'Amatista 3D <no-responder@amatista.local>'."""
    if "<" in remitente and ">" in remitente:
        return remitente.split("<", 1)[1].split(">", 1)[0].strip()
    return remitente.strip()


def construir_plantilla_html(
    email: str, codigo: str, proposito: str, nombre: Optional[str] = None
) -> str:
    """Plantilla de correo 'Cristal Tallado' con branding de Amatista y Goliat."""
    es_correo = proposito == "correo"
    subtitulo = (
        "VERIFICACIÓN DE CUENTA · ESTUDIO 3D"
        if es_correo
        else "RECUPERACIÓN DE ACCESO · SEGURIDAD"
    )
    motivo = MOTIVOS.get(proposito, "continuar")
    saludo = f"Hola, <strong>{nombre}</strong>:" if nombre else "Hola:"

    if es_correo:
        enlace = f"https://amatista-3d.tech/#/confirmar?email={urllib.parse.quote(email)}&codigo={codigo}"
        boton_html = (
            f'<div style="text-align:center;margin:22px 0 16px 0;">'
            f'<a href="{enlace}" target="_blank" style="display:inline-block;padding:12px 24px;'
            f'background:#00e5ff;color:#0d0a14;font-family:monospace;font-size:12px;font-weight:800;'
            f'letter-spacing:0.12em;text-transform:uppercase;text-decoration:none;border-radius:6px;'
            f'box-shadow:0 0 16px rgba(0,229,255,0.35);">Confirmar mi correo en Amatista ▸</a>'
            f'</div>'
        )
        frase_goliat = (
            f"«¡Bienvenido al taller{f', {nombre}' if nombre else ''}! "
            f"En cuanto confirmes tu correo guardaremos tus insignias y tu avance en todos tus dispositivos.»"
        )
    else:
        enlace = "https://amatista-3d.tech/#/recuperar"
        boton_html = (
            f'<div style="text-align:center;margin:22px 0 16px 0;">'
            f'<a href="{enlace}" target="_blank" style="display:inline-block;padding:12px 24px;'
            f'background:#f5792a;color:#ffffff;font-family:monospace;font-size:12px;font-weight:800;'
            f'letter-spacing:0.12em;text-transform:uppercase;text-decoration:none;border-radius:6px;'
            f'box-shadow:0 0 16px rgba(245,121,42,0.35);">Ir a restablecer contraseña ▸</a>'
            f'</div>'
        )
        frase_goliat = (
            "«Escribe este código de 6 dígitos en la pantalla de recuperación. "
            "Nunca compartas este código con nadie.»"
        )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Amatista 3D</title>
</head>
<body style="margin:0;padding:24px 12px;background:#0d0a14;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#ede8f5;">
  <div style="max-width:540px;margin:0 auto;background:#161224;border:1px solid rgba(155,89,182,0.45);border-radius:10px;overflow:hidden;box-shadow:0 12px 36px rgba(0,0,0,0.6);">
    <div style="height:4px;background:linear-gradient(90deg,#9b59b6 0%,#00e5ff 55%,#f5792a 100%);"></div>
    <div style="padding:20px 24px;border-bottom:1px solid rgba(255,255,255,0.08);background:#120e1e;">
      <div style="font-family:monospace;font-size:10px;font-weight:700;letter-spacing:0.2em;color:#00e5ff;">◆ {subtitulo}</div>
      <div style="font-size:22px;font-weight:800;color:#ffffff;margin-top:4px;letter-spacing:0.04em;">
        AMATISTA <span style="color:#c39bd3;">3D</span>
      </div>
    </div>
    <div style="padding:24px;font-size:14px;line-height:1.6;color:#ede8f5;">
      <p style="margin:0 0 12px 0;color:#ffffff;font-size:15px;">{saludo}</p>
      <p style="margin:0 0 16px 0;color:#c9c2d8;">
        Recibimos una solicitud para <strong>{motivo}</strong> en la plataforma de aprendizaje 3D Amatista.
      </p>
      <div style="margin:20px 0;padding:20px;background:#0d0a14;border:1px solid rgba(0,229,255,0.5);border-radius:8px;text-align:center;">
        <div style="font-family:monospace;font-size:10px;letter-spacing:0.2em;color:#00e5ff;font-weight:700;">
          CÓDIGO DE VERIFICACIÓN (15 MINUTOS DE VALIDEZ)
        </div>
        <div style="font-family:monospace;font-size:32px;font-weight:800;letter-spacing:0.35em;color:#ffffff;margin-top:8px;">
          {codigo}
        </div>
      </div>
      {boton_html}
      <div style="margin-top:22px;padding:14px 16px;background:rgba(155,89,182,0.14);border-left:3px solid #c39bd3;border-radius:6px;">
        <div style="font-family:monospace;font-size:10px;font-weight:700;color:#c39bd3;margin-bottom:4px;letter-spacing:0.1em;">
          🐾 GOLIAT · TU COMPAÑERO DE ESTUDIO
        </div>
        <div style="font-size:12px;color:#ede8f5;line-height:1.5;">{frase_goliat}</div>
      </div>
      <p style="margin:20px 0 0 0;font-size:11px;color:rgba(237,232,245,0.55);line-height:1.4;">
        Si no solicitaste este código, puedes ignorar este mensaje con tranquilidad: tu cuenta sigue segura.
      </p>
    </div>
    <div style="padding:12px 24px;background:#0a0710;border-top:1px solid rgba(255,255,255,0.07);font-family:monospace;font-size:9px;letter-spacing:0.14em;color:rgba(237,232,245,0.45);text-align:center;">
      AMATISTA 3D · BLENDER · A-FRAME WEBXR
    </div>
  </div>
</body>
</html>"""


def armar_mensaje(
    email: str,
    codigo: str,
    proposito: str,
    remitente: str,
    nombre: Optional[str] = None,
) -> EmailMessage:
    minutos = int(DURACION_CODIGO.total_seconds() // 60)
    motivo = MOTIVOS.get(proposito, "continuar")
    mensaje = EmailMessage()
    mensaje["Subject"] = ASUNTOS.get(proposito, "Tu código de Amatista")
    mensaje["From"] = remitente
    mensaje["To"] = email
    mensaje["Date"] = formatdate(localtime=False)
    dominio = remitente.rpartition("@")[2].strip("> ") if "@" in remitente else ""
    mensaje["Message-ID"] = make_msgid(domain=dominio or None)

    saludo_texto = f"Hola, {nombre}:\n\n" if nombre else "Hola:\n\n"
    if proposito == "correo":
        enlace = f"https://amatista-3d.tech/#/confirmar?email={urllib.parse.quote(email)}&codigo={codigo}"
        detalle_enlace = f"O confirma directamente con este enlace:\n{enlace}\n\n"
    else:
        detalle_enlace = ""

    contenido_plano = (
        f"{saludo_texto}"
        f"Tu código para {motivo} en Amatista es:\n\n"
        f"    {codigo}\n\n"
        f"Vence en {minutos} minutos.\n\n"
        f"{detalle_enlace}"
        f"Si no lo pediste, ignora este mensaje: tu cuenta sigue segura.\n\n"
        f"— Amatista 3D\n"
    )
    mensaje.set_content(contenido_plano)
    return mensaje


def enviar_via_brevo(
    api_key: str,
    email: str,
    codigo: str,
    proposito: str,
    remitente: str,
    nombre: Optional[str] = None,
) -> bool:
    """Envía el correo a través de la API REST v3 de Brevo (Sendinblue)."""
    url = "https://api.brevo.com/v3/smtp/email"
    asunto = ASUNTOS.get(proposito, "Tu código de Amatista")
    email_remitente = extraer_email_puro(remitente) or "no-responder@amatista-3d.tech"
    nombre_remitente = "Amatista 3D"

    minutos = int(DURACION_CODIGO.total_seconds() // 60)
    motivo = MOTIVOS.get(proposito, "continuar")
    saludo_texto = f"Hola, {nombre}:\n\n" if nombre else "Hola:\n\n"
    enlace = f"https://amatista-3d.tech/#/confirmar?email={urllib.parse.quote(email)}&codigo={codigo}" if proposito == "correo" else ""
    detalle_enlace = f"O confirma directamente con este enlace:\n{enlace}\n\n" if enlace else ""

    contenido_plano = (
        f"{saludo_texto}"
        f"Tu código para {motivo} en Amatista es:\n\n"
        f"    {codigo}\n\n"
        f"Vence en {minutos} minutos.\n\n"
        f"{detalle_enlace}"
        f"Si no lo pediste, ignora este mensaje: tu cuenta sigue segura.\n\n"
        f"— Amatista 3D\n"
    )
    contenido_html = construir_plantilla_html(email, codigo, proposito, nombre)

    payload = {
        "sender": {"name": nombre_remitente, "email": email_remitente},
        "to": [{"email": email, "name": nombre or email.split("@")[0]}],
        "subject": asunto,
        "htmlContent": contenido_html,
        "textContent": contenido_plano,
        "tags": ["amatista-3d", f"proposito-{proposito}"],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "User-Agent": "Amatista-Backend/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=ESPERA_BREVO) as resp:
            if resp.status in (200, 201, 202):
                log.info("Código de %s enviado a %s con Brevo API.", proposito, ocultar_correo(email))
                return True
    except urllib.error.HTTPError as err:
        log.error(
            "Fallo al enviar correo con Brevo API a %s (HTTP %s): %s",
            ocultar_correo(email),
            err.code,
            err.read().decode("utf-8", errors="ignore")[:200],
        )
    except Exception as error:
        log.error(
            "Error de red o conexión con Brevo API a %s: %s",
            ocultar_correo(email),
            error,
        )
    return False


def enviar_codigo(
    email: str, codigo: str, proposito: str, nombre: Optional[str] = None
) -> bool:
    """Envía el código de 6 dígitos personalizado. Devuelve True si salió por Brevo o SMTP.

    proposito: 'correo' (confirmar el correo) o 'password' (recuperar la cuenta).
    """
    if mostrar_codigos():
        log.warning("[MODO DESARROLLO] Código de %s para %s: %s", proposito, email, codigo)

    # 1. Intentar Brevo API v3 si está configurado BREVO_API_KEY
    brevo_key = os.getenv("BREVO_API_KEY", "").strip()
    remitente_config = os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()
    if brevo_key:
        remitente = remitente_config or "no-responder@amatista-3d.tech"
        if enviar_via_brevo(brevo_key, email, codigo, proposito, remitente, nombre):
            return True
        log.warning("Brevo API falló, reintentando por SMTP...")

    # 2. Enviar por SMTP tradicional
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
    remitente = remitente_config or usuario or f"no-responder@{host}"
    contexto = ssl.create_default_context()

    try:
        mensaje = armar_mensaje(email, codigo, proposito, remitente, nombre)
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
    except Exception as error:  # cualquier fallo de envío se registra; nunca tumba la petición
        # Sin el código ni la contraseña de SMTP: solo el tipo de error.
        log.error(
            "No se pudo enviar el código de %s a %s por SMTP (%s: %s).",
            proposito,
            ocultar_correo(email),
            type(error).__name__,
            error,
        )
        return False

    log.info("Código de %s enviado a %s por SMTP.", proposito, ocultar_correo(email))
    return True
