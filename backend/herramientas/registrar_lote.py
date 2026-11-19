"""Registra manifiestos ``amatista.moral/1`` en la tabla SISTEMA_LOTES.

Acepta tanto manifiestos individuales como consolidados (``--todos`` de
``moral.py``) e inserta o actualiza de forma idempotente cada repositorio por
su identificador determinista ``{repo}:{lote}:{commit_sha[:12]}``.

Uso desde ``backend/``:
    python -m herramientas.registrar_lote --archivo ruta/moral.json --lote M5
    python -m herramientas.registrar_lote --archivo ruta/moral.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

RAIZ_BACKEND = Path(__file__).resolve().parents[1]
if str(RAIZ_BACKEND) not in sys.path:
    sys.path.insert(0, str(RAIZ_BACKEND))

from database.conexion import crear_motor  # noqa: E402
from database.modelos import SistemaLote, ahora  # noqa: E402

CONTRATO_ESPERADO = "amatista.moral/1"


def _texto_acotado(valor: Any, limite: int) -> Optional[str]:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    return texto[:limite]


def _extraer_items(datos: Any) -> list[dict[str, Any]]:
    if not isinstance(datos, dict):
        raise ValueError("El manifiesto MORAL debe ser un objeto JSON.")
    contrato = datos.get("contrato")
    if contrato != CONTRATO_ESPERADO:
        raise ValueError(
            f"Contrato inválido {contrato!r}; se esperaba {CONTRATO_ESPERADO!r}."
        )

    if "repositorios" in datos:
        repositorios = datos["repositorios"]
        if not isinstance(repositorios, list) or not repositorios:
            raise ValueError("El campo 'repositorios' debe ser una lista no vacía.")
        return repositorios

    return [datos]


def normalizar_registros(
    datos: Any,
    *,
    lote: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Valida el manifiesto MORAL y construye las filas normalizadas para SISTEMA_LOTES."""
    items = _extraer_items(datos)
    fecha_global = datos.get("fecha_utc")
    ecosistema_media = datos.get("ecosistema_media")
    registros: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Cada entrada del manifiesto debe ser un objeto JSON.")
        contrato_item = item.get("contrato", datos.get("contrato"))
        if contrato_item != CONTRATO_ESPERADO:
            raise ValueError(
                f"Contrato inválido en entrada {contrato_item!r}; se esperaba {CONTRATO_ESPERADO!r}."
            )

        repo = _texto_acotado(item.get("repo"), 60)
        rama = _texto_acotado(item.get("rama"), 60)
        commit_sha = _texto_acotado(item.get("commit_sha"), 40)
        if not repo or not rama or not commit_sha:
            raise ValueError("Cada entrada debe incluir 'repo', 'rama' y 'commit_sha' no vacíos.")

        puntaje = item.get("puntaje_total")
        if isinstance(puntaje, bool) or not isinstance(puntaje, int) or not (0 <= puntaje <= 100):
            raise ValueError(
                f"Puntaje inválido para {repo}: {puntaje!r} (debe ser entero entre 0 y 100)."
            )

        lote_efectivo = _texto_acotado(lote or item.get("lote") or "MORAL", 40)
        if not lote_efectivo:
            raise ValueError("El identificador de 'lote' no puede quedar vacío.")

        registro_id = f"{repo}:{lote_efectivo}:{commit_sha[:12]}"
        if len(registro_id) > 64:
            registro_id = registro_id[:64]

        payload = dict(item)
        payload.setdefault("contrato", CONTRATO_ESPERADO)
        if fecha_global is not None:
            payload.setdefault("fecha_utc", fecha_global)
        if ecosistema_media is not None:
            payload.setdefault("ecosistema_media", ecosistema_media)
        payload["lote"] = lote_efectivo

        registros.append(
            {
                "id": registro_id,
                "lote": lote_efectivo,
                "repo": repo,
                "rama": rama,
                "commit_sha": commit_sha,
                "tag": _texto_acotado(item.get("ultimo_tag"), 60),
                "motor_version": _texto_acotado(item.get("motor_version"), 40),
                "geoda_version": _texto_acotado(item.get("geoda_version"), 40),
                "puntaje_moral": puntaje,
                "contrato_ok": 1,
                "manifiesto": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        )

    return registros


def registrar_manifiestos(
    datos: Any,
    *,
    lote: Optional[str] = None,
    motor: Optional[Engine] = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Valida y hace upsert idempotente en SISTEMA_LOTES (o simula con dry_run=True)."""
    registros = normalizar_registros(datos, lote=lote)
    resumen: list[dict[str, Any]] = []

    if dry_run:
        for reg in registros:
            resumen.append(
                {
                    "accion": "DRY-RUN",
                    "id": reg["id"],
                    "repo": reg["repo"],
                    "lote": reg["lote"],
                    "puntaje_moral": reg["puntaje_moral"],
                }
            )
        return resumen

    motor_propio = False
    if motor is None:
        motor = crear_motor()
        motor_propio = True

    try:
        marca_tiempo = ahora()
        with Session(motor) as sesion:
            for reg in registros:
                existente = sesion.get(SistemaLote, reg["id"])
                if existente is None:
                    fila = SistemaLote(**reg, registrado_en=marca_tiempo)
                    sesion.add(fila)
                    accion = "INSERT"
                else:
                    existente.lote = reg["lote"]
                    existente.repo = reg["repo"]
                    existente.rama = reg["rama"]
                    existente.commit_sha = reg["commit_sha"]
                    existente.tag = reg["tag"]
                    existente.motor_version = reg["motor_version"]
                    existente.geoda_version = reg["geoda_version"]
                    existente.puntaje_moral = reg["puntaje_moral"]
                    existente.contrato_ok = reg["contrato_ok"]
                    existente.manifiesto = reg["manifiesto"]
                    existente.registrado_en = marca_tiempo
                    accion = "UPDATE"

                resumen.append(
                    {
                        "accion": accion,
                        "id": reg["id"],
                        "repo": reg["repo"],
                        "lote": reg["lote"],
                        "puntaje_moral": reg["puntaje_moral"],
                    }
                )
            sesion.commit()
    finally:
        if motor_propio:
            motor.dispose()

    return resumen


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Registra manifiestos amatista.moral/1 en SISTEMA_LOTES (Oracle / SQLAlchemy)."
    )
    parser.add_argument(
        "--archivo",
        type=Path,
        default=None,
        help="Ruta al archivo JSON de manifiesto. Si se omite, lee JSON desde stdin.",
    )
    parser.add_argument(
        "--lote",
        default=None,
        help="Identificador de lote a registrar (ej. M5). Sobrescribe el del JSON si se indica.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida el manifiesto y muestra las operaciones sin escribir en la base de datos.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        if args.archivo is not None:
            contenido = args.archivo.read_text(encoding="utf-8-sig")
        else:
            contenido = sys.stdin.read()
        datos = json.loads(contenido)
        resumen = registrar_manifiestos(datos, lote=args.lote, dry_run=args.dry_run)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR MORAL registrar_lote: {exc}", file=sys.stderr)
        return 1

    for fila in resumen:
        print(
            f"{fila['accion']}: id={fila['id']} repo={fila['repo']} "
            f"lote={fila['lote']} puntaje={fila['puntaje_moral']}/100"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
