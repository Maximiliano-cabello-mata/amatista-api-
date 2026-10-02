"""Da el rol de administrador (o profesor) a una cuenta.

Uso, desde backend/ (usa la base configurada en backend/.env):

    python herramientas/crear_admin.py correo@dominio.com
    python herramientas/crear_admin.py correo@dominio.com --crear --nombre "Ana López"
    python herramientas/crear_admin.py correo@dominio.com --rol profesor

Sin --crear la cuenta debe existir (la persona se registra primero en la
app). Con --crear y un correo nuevo se crea la cuenta pidiendo la contraseña
en la terminal (no queda en el historial). Las cuentas creadas aquí quedan
con el correo confirmado: las crea quien administra el servidor.
"""
import argparse
import getpass
import sys
import uuid
from pathlib import Path
from typing import List, Optional

# Permite ejecutarlo como "python herramientas/crear_admin.py" desde backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from api.auth import problema_correo, problema_password  # noqa: E402
from database.conexion import motor  # noqa: E402
from database.modelos import ROLES, Base, Usuario  # noqa: E402
from seguridad import hash_password, normalizar_email  # noqa: E402


def pedir_password() -> Optional[str]:
    for _ in range(3):
        password = getpass.getpass("Contraseña nueva: ")
        problema = problema_password(password)
        if problema:
            print(problema)
            continue
        if getpass.getpass("Repite la contraseña: ") != password:
            print("Las contraseñas no coinciden.")
            continue
        return password
    return None


def main(argumentos: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Da el rol de administrador a una cuenta de Amatista.")
    parser.add_argument("email", help="correo de la cuenta")
    parser.add_argument("--crear", action="store_true", help="crea la cuenta si no existe (pide la contraseña)")
    parser.add_argument("--nombre", default=None, help="nombre para la cuenta nueva")
    parser.add_argument("--rol", default="admin", choices=ROLES, help="rol que se asigna (por defecto admin)")
    opciones = parser.parse_args(argumentos)

    email = normalizar_email(opciones.email)
    problema = problema_correo(email)
    if problema:
        print(problema, file=sys.stderr)
        return 2

    if motor().dialect.name == "sqlite":
        Base.metadata.create_all(motor())  # en Oracle las tablas las crean los scripts de sql/

    try:
        with Session(motor()) as db:
            usuario = db.scalar(select(Usuario).where(Usuario.email == email))
            if usuario is None:
                if not opciones.crear:
                    print(
                        f"No existe una cuenta con {email}. Regístrala en la app o usa --crear.",
                        file=sys.stderr,
                    )
                    return 1
                password = pedir_password()
                if password is None:
                    print("No se creó la cuenta.", file=sys.stderr)
                    return 1
                usuario = Usuario(
                    id=f"usr-{uuid.uuid4()}",
                    nombre=(opciones.nombre or email.split("@")[0]).strip()[:150],
                    email=email,
                    password_hash=hash_password(password),
                    correo_confirmado=1,
                )
                db.add(usuario)
                accion = "Cuenta creada"
            else:
                if opciones.nombre and not usuario.nombre:
                    usuario.nombre = opciones.nombre.strip()[:150]
                accion = "Cuenta actualizada"
            usuario.rol = opciones.rol
            db.commit()
            print(f"{accion}: {email} (id {usuario.id}) ahora tiene el rol «{opciones.rol}».")
    except SQLAlchemyError as error:
        original = getattr(error, "orig", None) or error
        texto = str(original).strip()
        print(f"Error de base de datos: {texto.splitlines()[0] if texto else type(original).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
