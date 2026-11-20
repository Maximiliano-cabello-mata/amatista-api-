"""Exporta un respaldo de la base y ejecuta una prueba real de restauración en SQLite.

No almacena PII en el archivo de verificación (solo conteos por tabla,
duración y sello temporal). Sirve tanto para el timer diario de producción
(``--destino /home/opc/respaldos``) como para verificar un respaldo manual
existente (``--solo-verificar /ruta/respaldo/base``).

Uso desde ``backend/``:
    python -m herramientas.respaldar_y_probar --destino /home/opc/respaldos --mantener 14
    python -m herramientas.respaldar_y_probar --solo-verificar /ruta/respaldo/base
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

RAIZ_BACKEND = Path(__file__).resolve().parents[1]
if str(RAIZ_BACKEND) not in sys.path:
    sys.path.insert(0, str(RAIZ_BACKEND))

from database.conexion import crear_motor  # noqa: E402
from herramientas import migrar  # noqa: E402

PATRON_SNAPSHOT = re.compile(r"^amatista-\d{8}-\d{6}(?:-\d+)?$")


def probar_restauracion(carpeta_base: Path) -> Dict[str, Any]:
    """Restaura un respaldo ``amatista.export/1`` en SQLite temporal y verifica conteos."""
    inicio = time.monotonic()
    manifiesto = migrar.leer_manifiesto(carpeta_base)

    with tempfile.TemporaryDirectory(prefix="amatista-restore-") as tmpdir:
        db_ruta = Path(tmpdir) / "prueba_restauracion.db"
        motor_prueba = create_engine(f"sqlite:///{db_ruta.as_posix()}")
        try:
            conteos = migrar.importar(motor_prueba, carpeta_base, crear_tablas=True)
        finally:
            motor_prueba.dispose()

    return {
        "restauracion_ok": True,
        "formato": manifiesto["formato"],
        "tablas": len(conteos),
        "filas_totales": sum(conteos.values()),
        "conteos": conteos,
        "verificado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duracion_s": round(time.monotonic() - inicio, 3),
    }


def _siguiente_carpeta(destino: Path, prefijo: str) -> Path:
    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidata = destino / f"{prefijo}{sello}"
    indice = 1
    while candidata.exists():
        candidata = destino / f"{prefijo}{sello}-{indice}"
        indice += 1
    return candidata


def rotar_respaldos(destino: Path, mantener: int) -> List[str]:
    """Elimina snapshots propios más antiguos que excedan ``mantener``."""
    if mantener < 1:
        raise ValueError(f"--mantener debe ser >= 1 (recibido {mantener}).")
    snapshots = sorted(
        (p for p in destino.iterdir() if p.is_dir() and PATRON_SNAPSHOT.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    eliminados: List[str] = []
    for obsoleto in snapshots[mantener:]:
        shutil.rmtree(obsoleto)
        eliminados.append(obsoleto.name)
    return eliminados


def ejecutar_respaldo_verificado(
    motor: Engine,
    destino: Path,
    *,
    mantener: int = 14,
    prefijo: str = "amatista-",
) -> Dict[str, Any]:
    """Exporta la base, prueba la restauración en SQLite temporal y rota copias viejas."""
    umask_anterior = os.umask(0o077) if os.name == "posix" else None
    try:
        destino.mkdir(parents=True, exist_ok=True)
        snapshot = _siguiente_carpeta(destino, prefijo)
        carpeta_base = snapshot / "base"
        migrar.exportar(motor, carpeta_base)
        verificacion = probar_restauracion(carpeta_base)
        (snapshot / "verificacion_restauracion.json").write_text(
            json.dumps(verificacion, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        eliminados = rotar_respaldos(destino, mantener)
    finally:
        if umask_anterior is not None:
            os.umask(umask_anterior)

    return {
        "snapshot": snapshot,
        "verificacion": verificacion,
        "eliminados": eliminados,
    }


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Respaldo verificado de Amatista (exporta y prueba restauración en SQLite temporal)."
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--destino", type=Path, help="Carpeta destino donde crear el snapshot.")
    grupo.add_argument(
        "--solo-verificar",
        type=Path,
        help="Carpeta base/ ya exportada sobre la que ejecutar prueba de restauración.",
    )
    parser.add_argument(
        "--mantener",
        type=int,
        default=14,
        help="Cantidad de snapshots automáticos más recientes a conservar (por defecto 14).",
    )
    parser.add_argument("--url", default=None, help="URL SQLAlchemy de origen opcional.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        if args.solo_verificar is not None:
            carpeta_base = args.solo_verificar.resolve()
            verificacion = probar_restauracion(carpeta_base)
            salida = carpeta_base.parent / "verificacion_restauracion.json"
            salida.write_text(
                json.dumps(verificacion, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"OK restauración: {verificacion['tablas']} tablas, "
                f"{verificacion['filas_totales']} filas en {verificacion['duracion_s']}s "
                f"-> {salida}"
            )
            return 0

        motor = create_engine(args.url) if args.url else crear_motor()
        try:
            resultado = ejecutar_respaldo_verificado(
                motor,
                args.destino,
                mantener=args.mantener,
            )
        finally:
            motor.dispose()

        verif = resultado["verificacion"]
        print(
            f"Respaldo verificado OK en {resultado['snapshot']}: "
            f"{verif['tablas']} tablas, {verif['filas_totales']} filas ({verif['duracion_s']}s). "
            f"Rotados={len(resultado['eliminados'])}."
        )
        return 0
    except (migrar.ErrorMigracion, OSError, ValueError) as exc:
        print(f"ERROR respaldo: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
