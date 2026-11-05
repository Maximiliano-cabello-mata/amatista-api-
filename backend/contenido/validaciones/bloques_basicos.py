"""Validación de bloques de contenido básicos (texto, imágenes, listas, comparaciones)."""
from typing import Callable, Dict

from .comun import (
    MAX_TECLAS,
    MODOS_COMPARAR,
    VARIANTES_AVISO,
    _Contexto,
    _FALTA,
    _valor,
)


def _markdown_text(c: _Contexto, b: dict) -> None:
    c.texto(b, "body")


def _image(c: _Contexto, b: dict) -> None:
    c.texto(b, "src")
    c.texto(b, "alt")
    c.texto(b, "caption", obligatorio=False)


def _concept_cards(c: _Contexto, b: dict) -> None:
    items = c.objetos(b, "items", 1, 12)
    for i, item in items:
        p = f"items[{i}]."
        c.texto(item, "term", p)
        c.texto(item, "definition", p)
        imagen = c.texto(item, "image", p, obligatorio=False)
        c.texto(item, "alt", p, obligatorio=imagen is not None)
    # La PWA identifica cada tarjeta por su término.
    c.ids_unicos(items, "items", clave="term")


def _lista_con_titulo(c: _Contexto, b: dict, campo: str) -> None:
    c.texto(b, "title", obligatorio=False)
    for i, item in c.objetos(b, campo, 1, 20):
        p = f"{campo}[{i}]."
        c.texto(item, "title", p)
        c.texto(item, "text", p)
        c.texto(item, "icon", p, obligatorio=False)
        if "year" in item and item["year"] is not None and not isinstance(item["year"], (str, int)):
            c.error(f"«{p}year» debe ser texto")


def _timeline(c: _Contexto, b: dict) -> None:
    _lista_con_titulo(c, b, "items")


def _pipeline(c: _Contexto, b: dict) -> None:
    _lista_con_titulo(c, b, "steps")


def _layers(c: _Contexto, b: dict) -> None:
    _lista_con_titulo(c, b, "items")
    c.texto(b, "footer", obligatorio=False)


def _callout(c: _Contexto, b: dict) -> None:
    c.opcion(b, "variant", VARIANTES_AVISO, obligatorio=False)
    c.texto(b, "title", obligatorio=False)
    c.texto(b, "body")


def _code_snippet(c: _Contexto, b: dict) -> None:
    c.texto(b, "code")
    c.texto(b, "language", obligatorio=False)
    c.booleano(b, "preview")


def _video_player(c: _Contexto, b: dict) -> None:
    c.texto(b, "url")


def _teclas(c: _Contexto, objeto: dict, prefijo: str, obligatorio: bool, campo: str = "keys") -> None:
    """keys: combinación que se pulsa a la vez (["Shift", "D"]); then: la que
    va después (S y luego Z = {"keys": ["S"], "then": ["Z"]})."""
    teclas = _valor(objeto, campo)
    if teclas is _FALTA:
        if obligatorio:
            c.error(f"falta «{prefijo}{campo}»")
        return
    if (
        not isinstance(teclas, list)
        or not 1 <= len(teclas) <= MAX_TECLAS
        or not all(isinstance(t, str) and t.strip() and len(t) <= 20 for t in teclas)
    ):
        c.error(f"«{prefijo}{campo}» debe ser una lista de 1 a {MAX_TECLAS} teclas («Shift», «D»)")
    if campo == "keys":
        _teclas(c, objeto, prefijo, False, "then")


def _step_by_step(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False)
    for i, paso in c.objetos(b, "steps", 1, 12):
        p = f"steps[{i}]."
        c.texto(paso, "title", p)
        c.texto(paso, "text", p, obligatorio=False)
        _teclas(c, paso, p, obligatorio=False)
        imagen = c.texto(paso, "image", p, obligatorio=False)
        c.texto(paso, "alt", p, obligatorio=imagen is not None)


def _shortcuts(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False)
    for i, atajo in c.objetos(b, "items", 1, 24):
        p = f"items[{i}]."
        _teclas(c, atajo, p, obligatorio=True)
        c.texto(atajo, "action", p)
    c.booleano(b, "practice")


def _lado(c: _Contexto, b: dict, campo: str) -> None:
    lado = _valor(b, campo)
    if not isinstance(lado, dict):
        c.error(f"«{campo}» debe ser un objeto {{label, image, text}}")
        return
    p = f"{campo}."
    c.texto(lado, "label", p)
    imagen = c.texto(lado, "image", p, obligatorio=False)
    c.texto(lado, "alt", p, obligatorio=imagen is not None)
    texto = c.texto(lado, "text", p, obligatorio=False)
    if imagen is None and texto is None:
        c.error(f"«{campo}» necesita «image» o «text»")


def _compare(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False)
    _lado(c, b, "before")
    _lado(c, b, "after")
    modo = c.opcion(b, "mode", MODOS_COMPARAR, obligatorio=False)
    if modo == "slider":
        for campo in ("before", "after"):
            lado = b.get(campo)
            if isinstance(lado, dict) and not lado.get("image"):
                c.error(f"«mode» = «slider» necesita «{campo}.image»")
    c.texto(b, "caption", obligatorio=False)


REVISORES_BASICOS: Dict[str, Callable[[_Contexto, dict], None]] = {
    "markdown_text": _markdown_text,
    "image": _image,
    "concept_cards": _concept_cards,
    "timeline": _timeline,
    "pipeline": _pipeline,
    "layers": _layers,
    "callout": _callout,
    "code_snippet": _code_snippet,
    "video_player": _video_player,
    "step_by_step": _step_by_step,
    "shortcuts": _shortcuts,
    "compare": _compare,
}
