"""¿De qué cuenta salió esta copia de Amatista Motor?

Uso, desde backend/ en el servidor (necesita AMATISTA_SECRETO_FIRMA del .env):

    python herramientas/verificar_licencia.py Amatista-Motor-3.5-windows.zip
    python herramientas/verificar_licencia.py "carpeta/del/addon/instalado"

Lee licencia.json (la marca de agua que el servidor firma en cada descarga
con cuenta), comprueba la firma y la integridad de los archivos, y dice qué
cuenta la descargó y cuándo. Sirve si una copia circula fuera de Amatista.
"""
import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from api.addon import licencia_valida  # noqa: E402


def archivos_de(ruta: Path) -> dict:
    """{nombre: bytes} de una carpeta, de la extensión .zip o del paquete con instalador."""
    if ruta.is_dir():
        return {p.relative_to(ruta).as_posix(): p.read_bytes() for p in ruta.rglob("*") if p.is_file()}
    with zipfile.ZipFile(ruta) as zf:
        internos = [n for n in zf.namelist() if n.endswith(".zip") and "/amatista-" in "/" + n]
        if internos:  # paquete con instalador: la extensión va dentro
            return archivos_de_zip(zf.read(internos[0]))
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


def archivos_de_zip(datos: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(datos)) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ruta", type=Path)
    args = parser.parse_args(argv)
    archivos = archivos_de(args.ruta)
    licencia = json.loads(archivos.get("licencia.json", b"{}") or b"{}")
    if not licencia:
        print("Sin marca de agua: es una descarga pública (sin sesión) o la quitaron.")
    else:
        estado = "FIRMA VÁLIDA" if licencia_valida(licencia) else "firma inválida o sin AMATISTA_SECRETO_FIRMA"
        print(f"Cuenta: {licencia.get('cuenta')} · emitida {licencia.get('emitida')} · "
              f"versión {licencia.get('version')} · {estado}")
    integridad = json.loads(archivos.get("integridad.json", b"{}") or b"{}")
    esperados = integridad.get("archivos") or {}
    variables = {"config.json", "licencia.json", "integridad.json"}
    cambios = sorted(
        n for n in (set(esperados) | (set(archivos) - variables))
        if "__pycache__" not in n and esperados.get(n) != (hashlib.sha256(archivos[n]).hexdigest() if n in archivos else None)
    )
    print("Archivos: " + ("todos son los originales." if not cambios else f"{len(cambios)} cambiados: {', '.join(cambios[:10])}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
