"""Contraseñas, tokens de sesión y códigos de verificación.

Solo usa la biblioteca estándar (hashlib, hmac, secrets): no agrega
dependencias al servidor ARM.

- Contraseñas: PBKDF2-HMAC-SHA256 con sal aleatoria. Formato guardado:
  "pbkdf2_sha256$<iteraciones>$<sal hex>$<hash hex>". Las iteraciones se
  pueden subir con AMATISTA_PBKDF2_ITER sin invalidar las contraseñas viejas
  (cada hash guarda las suyas).
- Tokens de sesión: 32 bytes aleatorios para el navegador; en la base solo
  se guarda su SHA-256.
- Códigos de 6 dígitos: también se guardan como SHA-256 y caducan.
"""
import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from typing import Optional

ALGORITMO = "pbkdf2_sha256"
ITERACIONES_POR_DEFECTO = 600_000  # recomendación OWASP para PBKDF2-HMAC-SHA256
DURACION_SESION = timedelta(days=30)
DURACION_CODIGO = timedelta(minutes=15)
MAX_INTENTOS_CODIGO = 5


def iteraciones() -> int:
    try:
        return max(1_000, int(os.getenv("AMATISTA_PBKDF2_ITER", ITERACIONES_POR_DEFECTO)))
    except ValueError:
        return ITERACIONES_POR_DEFECTO


def hash_password(password: str) -> str:
    sal = secrets.token_bytes(16)
    n = iteraciones()
    derivada = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal, n)
    return f"{ALGORITMO}${n}${sal.hex()}${derivada.hex()}"


def verificar_password(password: str, guardado: Optional[str]) -> bool:
    """Compara en tiempo constante. Un hash vacío o mal formado nunca coincide."""
    if not guardado:
        return False
    try:
        algoritmo, n, sal_hex, hash_hex = guardado.split("$")
        if algoritmo != ALGORITMO:
            return False
        derivada = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(sal_hex), int(n))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derivada.hex(), hash_hex)


def requiere_rehash(guardado: Optional[str]) -> bool:
    """True si el hash usa menos iteraciones que la configuración actual."""
    try:
        return int(guardado.split("$")[1]) < iteraciones()
    except (AttributeError, IndexError, ValueError):
        return True


# Se usa para igualar el tiempo de respuesta cuando el correo no existe y no
# revelar qué cuentas están registradas.
_HASH_SEÑUELO: Optional[str] = None


def verificar_password_señuelo(password: str) -> None:
    global _HASH_SEÑUELO
    _HASH_SEÑUELO = _HASH_SEÑUELO or hash_password(secrets.token_hex(8))
    verificar_password(password, _HASH_SEÑUELO)


def nuevo_token() -> str:
    """Token opaco para el navegador (43 caracteres URL-safe)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Lo que se guarda en SESIONES.id: 64 caracteres hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def nuevo_codigo() -> str:
    """Código numérico de 6 dígitos para confirmar el correo o recuperar la cuenta."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_codigo(email: str, codigo: str) -> str:
    """Hash del código ligado al correo: el mismo código no sirve para otra cuenta."""
    return hashlib.sha256(f"{email.strip().lower()}:{codigo.strip()}".encode("utf-8")).hexdigest()


def codigos_iguales(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b) and hmac.compare_digest(a, b)


def normalizar_email(email: str) -> str:
    return email.strip().lower()
