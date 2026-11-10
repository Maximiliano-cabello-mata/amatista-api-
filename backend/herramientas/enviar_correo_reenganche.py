#!/usr/bin/env python3
"""Script para enviar correos personalizados de reenganche / avance a los usuarios de Amatista 3D.

Uso (desde backend/):
  python herramientas/enviar_correo_reenganche.py --dry-run
  python herramientas/enviar_correo_reenganche.py --probar <tu-correo>
  python herramientas/enviar_correo_reenganche.py --enviar
"""
import argparse
import logging
import os
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Optional

# Añadir el directorio backend al path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

# Cargar variables de entorno
from dotenv import load_dotenv
env_sistema = Path("/etc/amatista/amatista-api.env")
if env_sistema.exists():
    load_dotenv(env_sistema)
load_dotenv(backend_dir / ".env")

from database.conexion import motor
from database.modelos import Base, ProgresoLeccion, Usuario
from sqlalchemy import func, select
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("amatista.reenganche")


def primer_nombre(nombre_completo: Optional[str], email: str) -> str:
    if nombre_completo:
        limpio = " ".join(nombre_completo.split()).strip()
        if limpio and "@" not in limpio:
            return limpio.split()[0].capitalize()
    usuario, _, _ = email.partition("@")
    return usuario.capitalize()


def construir_correo_reenganche(
    email: str,
    nombre: str,
    lecciones: int,
    remitente: str,
) -> EmailMessage:
    asunto = f"¡Hola, {nombre}! Tu taller 3D en Amatista te espera 🚀"
    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = remitente
    mensaje["To"] = email
    mensaje["Date"] = formatdate(localtime=False)
    dominio = remitente.rpartition("@")[2].strip("> ") if "@" in remitente else ""
    mensaje["Message-ID"] = make_msgid(domain=dominio or None)

    url_pwa = os.getenv("AMATISTA_URL_PWA", "https://amatista-3dt.tech").strip().rstrip("/")
    enlace_taller = f"{url_pwa}/#/"

    if lecciones > 0:
        texto_avance = f"Llevas {lecciones} lecciones completadas en tu cuenta. ¡Tu avance sigue intacto y guardado en la nube!"
        frase_goliat = f"«¡Qué gusto verte de nuevo, {nombre}! Tu mesa de trabajo sigue lista con tus {lecciones} lecciones guardadas. Con solo 5 minutos hoy puedes seguir avanzando.»"
    else:
        texto_avance = "Tu mesa de trabajo está preparada para comenzar tu primera práctica de 5 minutos."
        frase_goliat = f"«¡Bienvenido de vuelta, {nombre}! Todo el taller de Blender y WebXR está listo para que comiences paso a paso sin complicaciones.»"

    contenido_plano = (
        f"Hola, {nombre}:\n\n"
        f"Te escribimos desde Amatista 3D para invitarte a continuar con tu aprendizaje en modelado y desarrollo 3D.\n\n"
        f"{texto_avance}\n\n"
        f"Novedades en la plataforma:\n"
        f"- Inicio de sesión rápido y seguro con Google.\n"
        f"- Tutor de Inteligencia Artificial para atajos y resolución de dudas en 3D.\n"
        f"- Sistema de rachas e insignias personalizadas.\n\n"
        f"Continúa tu taller aquí:\n"
        f"{enlace_taller}\n\n"
        f"Goliat:\n"
        f"{frase_goliat}\n\n"
        f"— El equipo de Amatista 3D\n"
    )
    mensaje.set_content(contenido_plano)

    contenido_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Amatista 3D</title>
</head>
<body style="margin:0;padding:24px 12px;background:#0d0a14;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#ede8f5;">
  <div style="max-width:560px;margin:0 auto;background:#161224;border:1px solid rgba(155,89,182,0.45);border-radius:12px;overflow:hidden;box-shadow:0 12px 36px rgba(0,0,0,0.6);">
    <div style="height:4px;background:linear-gradient(90deg,#9b59b6 0%,#00e5ff 55%,#f5792a 100%);"></div>
    
    <div style="padding:22px 26px;border-bottom:1px solid rgba(255,255,255,0.08);background:#120e1e;">
      <div style="font-family:monospace;font-size:10px;font-weight:700;letter-spacing:0.2em;color:#00e5ff;">◆ TU TALLER 3D PERSONALIZADO</div>
      <div style="font-size:24px;font-weight:800;color:#ffffff;margin-top:4px;letter-spacing:0.04em;">
        AMATISTA <span style="color:#c39bd3;">3D</span>
      </div>
    </div>

    <div style="padding:26px;font-size:14px;line-height:1.6;color:#ede8f5;">
      <p style="margin:0 0 14px 0;color:#ffffff;font-size:16px;">
        ¡Hola, <strong>{nombre}</strong>!
      </p>
      
      <p style="margin:0 0 16px 0;color:#c9c2d8;">
        Te extrañamos en el taller de <strong>Amatista 3D</strong>. Queremos invitarte a retomar tu ruta de aprendizaje en modelado 3D, Blender y WebXR.
      </p>

      <div style="margin:20px 0;padding:18px 20px;background:#0d0a14;border:1px solid rgba(0,229,255,0.4);border-radius:8px;">
        <div style="font-family:monospace;font-size:11px;letter-spacing:0.18em;color:#00e5ff;font-weight:700;">
          ESTADO DE TU AVANCE
        </div>
        <div style="font-size:18px;font-weight:800;color:#ffffff;margin-top:6px;">
          {f"🔥 {lecciones} lecciones completadas" if lecciones > 0 else "⚡ Listo para tu primera lección"}
        </div>
        <p style="margin:6px 0 0 0;font-size:12px;color:#a89fc0;">
          {texto_avance}
        </p>
      </div>

      <div style="margin:20px 0;padding:16px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;">
        <div style="font-weight:700;color:#ffffff;margin-bottom:8px;font-size:13px;">
          ✨ Novedades que ya puedes disfrutar:
        </div>
        <ul style="margin:0;padding-left:18px;font-size:13px;color:#c9c2d8;line-height:1.7;">
          <li><strong>Inicio de sesión con Google:</strong> entra directamente en 1 clic.</li>
          <li><strong>Tutor de Inteligencia Artificial:</strong> resuelve dudas de Blender y herramientas 3D al instante.</li>
          <li><strong>Rachas y desafíos diarios:</strong> gana XP y sube de nivel con sesiones cortas de 5 a 10 minutos.</li>
        </ul>
      </div>

      <div style="text-align:center;margin:26px 0 18px 0;">
        <a href="{enlace_taller}" target="_blank" style="display:inline-block;padding:14px 28px;background:#00e5ff;color:#0d0a14;font-family:monospace;font-size:12px;font-weight:800;letter-spacing:0.12em;text-transform:uppercase;text-decoration:none;border-radius:6px;box-shadow:0 0 20px rgba(0,229,255,0.4);">
          Continuar mi taller en Amatista ▸
        </a>
      </div>

      <div style="margin-top:22px;padding:14px 16px;background:rgba(155,89,182,0.14);border-left:3px solid #c39bd3;border-radius:6px;">
        <div style="font-family:monospace;font-size:10px;font-weight:700;color:#c39bd3;margin-bottom:4px;letter-spacing:0.1em;">
          🐾 GOLIAT · GUARDIÁN DEL TALLER
        </div>
        <div style="font-size:12px;color:#ede8f5;line-height:1.5;">
          {frase_goliat}
        </div>
      </div>
    </div>

    <div style="padding:14px 24px;background:#0a0710;border-top:1px solid rgba(255,255,255,0.07);font-family:monospace;font-size:9px;letter-spacing:0.14em;color:rgba(237,232,245,0.45);text-align:center;">
      AMATISTA 3D · PLATAFORMA DE APRENDIZAJE 3D LIBRE Y UNIVERSAL
    </div>
  </div>
</body>
</html>"""

    mensaje.add_alternative(contenido_html, subtype="html")
    return mensaje


def enviar_correo_smtp(mensaje: EmailMessage) -> bool:
    host = os.getenv("SMTP_HOST", "smtp-relay.brevo.com").strip()
    try:
        puerto = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        puerto = 587
    usuario = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    contexto = ssl.create_default_context()

    try:
        if puerto == 465:
            conexion = smtplib.SMTP_SSL(host, puerto, timeout=12, context=contexto)
        else:
            conexion = smtplib.SMTP(host, puerto, timeout=12)
        with conexion as smtp:
            if puerto != 465:
                smtp.starttls(context=contexto)
            if usuario:
                smtp.login(usuario, password)
            smtp.send_message(mensaje)
        return True
    except Exception as error:
        log.error("Fallo al enviar a %s: %s", mensaje["To"], error)
        return False


def main():
    parser = argparse.ArgumentParser(description="Envío masivo de correos de reenganche personalizados.")
    parser.add_argument("--dry-run", action="store_true", help="Simula el envío sin mandar correos.")
    parser.add_argument("--probar", type=str, help="Envía un solo correo de prueba a esta dirección.")
    parser.add_argument("--enviar", action="store_true", help="Envía los correos a todos los usuarios reales.")
    args = parser.parse_args()

    if not args.dry_run and not args.probar and not args.enviar:
        parser.print_help()
        print("\nDebes especificar una acción: --dry-run, --probar <correo> o --enviar")
        sys.exit(1)

    remitente = os.getenv("SMTP_FROM", "").strip() or "Amatista 3D <cabellomaximiliano86@gmail.com>"
    eng = motor()
    if eng.dialect.name == "sqlite":
        Base.metadata.create_all(eng)

    with Session(eng) as db:
        if args.probar:
            email_prueba = args.probar.strip().lower()
            usuario = db.scalar(select(Usuario).where(Usuario.email == email_prueba))
            nombre = primer_nombre(usuario.nombre if usuario else None, email_prueba)
            lecciones = (
                db.scalar(
                    select(func.count())
                    .select_from(ProgresoLeccion)
                    .where(ProgresoLeccion.usuario_id == usuario.id)
                )
                if usuario
                else 0
            )
            print(f"Enviando correo de prueba a: {email_prueba} (Nombre: {nombre}, Lecciones: {lecciones})...")
            msg = construir_correo_reenganche(email_prueba, nombre, lecciones, remitente)
            ok = enviar_correo_smtp(msg)
            if ok:
                print(f"✓ Correo de prueba enviado con éxito a {email_prueba}!")
            else:
                print("✗ Falló el envío del correo de prueba.")
            return

        usuarios = db.scalars(select(Usuario).where(Usuario.email.is_not(None))).all()
        destinatarios = []
        for u in usuarios:
            correo = (u.email or "").strip().lower()
            if not correo or "@" not in correo or correo.endswith(".local") or correo == "alumno.prueba@gmail.com":
                continue
            lecciones = db.scalar(
                select(func.count())
                .select_from(ProgresoLeccion)
                .where(ProgresoLeccion.usuario_id == u.id)
            ) or 0
            nombre = primer_nombre(u.nombre, correo)
            destinatarios.append((correo, nombre, lecciones, u.id))

        print("\n=======================================================")
        print("  AMATISTA 3D · CORREO PERSONALIZADO DE REENGANCHE")
        print("=======================================================")
        print(f"Total usuarios registrados con correo válido: {len(destinatarios)}")
        print(f"Remitente configurado: {remitente}")
        print("-------------------------------------------------------")

        for idx, (correo, nombre, lecciones, uid) in enumerate(destinatarios, 1):
            print(f"[{idx:02d}/{len(destinatarios):02d}] {correo:<35} | Alumno: {nombre:<15} | Lecciones: {lecciones}")

        if args.dry_run:
            print("\n[MODO SIMULACIÓN --dry-run]")
            print("No se envió ningún correo. Para enviar una prueba a tu cuenta usa:")
            print("  python herramientas/enviar_correo_reenganche.py --probar cabellomaximiliano86@gmail.com")
            print("Para enviar a todos usa:")
            print("  python herramientas/enviar_correo_reenganche.py --enviar")
            return

        if args.enviar:
            print("\nIniciando envío a todos los destinatarios...")
            exitos = 0
            fallos = 0
            for idx, (correo, nombre, lecciones, uid) in enumerate(destinatarios, 1):
                msg = construir_correo_reenganche(correo, nombre, lecciones, remitente)
                ok = enviar_correo_smtp(msg)
                if ok:
                    print(f"  [{idx:02d}/{len(destinatarios):02d}] ✓ Enviado a {correo} ({nombre})")
                    exitos += 1
                else:
                    print(f"  [{idx:02d}/{len(destinatarios):02d}] ✗ Fallo en {correo}")
                    fallos += 1
                time.sleep(1.2)

            print("\n=======================================================")
            print(f"Envío completado: {exitos} exitosos, {fallos} fallidos.")
            print("=======================================================\n")


if __name__ == "__main__":
    main()
