"""Validación de bloques interactivos (quizzes, ordenar, asociar, dinámicas, tours)."""
from typing import Any, Callable, Dict, List

from .comun import (
    DEMOS_PARAMETRO,
    EJES_TRANSFORMAR,
    FIGURAS_ELEGIR,
    ICONOS_NODO_FLUJO,
    MAX_EJERCICIOS_TECLAS,
    MAX_TECLAS,
    PATRON_HUECO,
    PROPIEDADES_TRANSFORMAR,
    TECLAS_CON_NOMBRE,
    ZONAS_PANTALLA,
    _Contexto,
    _FALTA,
    _es_numero,
    _valor,
)


def _opciones(c: _Contexto, b: dict, minimo: int, maximo: int, campo: str = "options") -> None:
    opciones = c.objetos(b, campo, minimo, maximo)
    for i, opcion in opciones:
        p = f"{campo}[{i}]."
        c.identificador(opcion, "id", p)
        c.texto(opcion, "text", p)
        c.booleano(opcion, "isCorrect", p, obligatorio=True)
    c.ids_unicos(opciones, campo)
    if opciones and not any(opcion.get("isCorrect") is True for _, opcion in opciones):
        c.error("necesita al menos una opción con «isCorrect»: true")


def _quiz_inline(c: _Contexto, b: dict) -> None:
    c.texto(b, "question")
    _opciones(c, b, 2, 6)
    c.texto(b, "explanation", obligatorio=False)


def _ordering(c: _Contexto, b: dict) -> None:
    c.texto(b, "prompt")
    items = c.objetos(b, "items", 3, 8)
    for i, item in items:
        c.identificador(item, "id", f"items[{i}].")
        c.texto(item, "text", f"items[{i}].")
    c.ids_unicos(items, "items")
    # Dos textos iguales harían ambiguo el orden correcto.
    c.ids_unicos(items, "items", clave="text")
    c.texto(b, "explanation", obligatorio=False)


def _matching(c: _Contexto, b: dict) -> None:
    c.texto(b, "prompt")
    pares = c.objetos(b, "pairs", 2, 8)
    for i, par in pares:
        p = f"pairs[{i}]."
        c.identificador(par, "id", p)
        c.texto(par, "left", p)
        c.texto(par, "right", p)
    c.ids_unicos(pares, "pairs")
    c.ids_unicos(pares, "pairs", clave="left")
    c.ids_unicos(pares, "pairs", clave="right")
    c.texto(b, "explanation", obligatorio=False)


def huecos_de(plantilla: str) -> List[List[str]]:
    """Respuestas aceptadas de cada hueco [[respuesta|alternativa]] de fill_blanks."""
    return [[opcion.strip() for opcion in hueco.split("|")] for hueco in PATRON_HUECO.findall(plantilla)]


def _fill_blanks(c: _Contexto, b: dict) -> None:
    c.texto(b, "prompt")
    plantilla = c.texto(b, "template")
    if plantilla is not None:
        huecos = huecos_de(plantilla)
        if not 1 <= len(huecos) <= 10:
            c.error(f"«template» necesita entre 1 y 10 huecos [[respuesta]] (tiene {len(huecos)})")
        for i, respuestas in enumerate(huecos, start=1):
            if not all(respuestas):
                c.error(f"el hueco {i} de «template» tiene una respuesta vacía (usa [[respuesta|alternativa]])")
        resto = PATRON_HUECO.sub("", plantilla)
        if "[[" in resto or "]]" in resto:
            c.error("«template» tiene corchetes [[ ]] sin cerrar")
    c.booleano(b, "code")
    c.texto(b, "explanation", obligatorio=False)


def _hotspots(c: _Contexto, b: dict) -> None:
    c.texto(b, "src")
    c.texto(b, "alt")
    puntos = c.objetos(b, "points", 1, 10)
    for i, punto in puntos:
        p = f"points[{i}]."
        c.identificador(punto, "id", p)
        c.numero(punto, "x", p, minimo=0, maximo=100)
        c.numero(punto, "y", p, minimo=0, maximo=100)
        c.texto(punto, "title", p)
        c.texto(punto, "text", p)
    c.ids_unicos(puntos, "points")


def _code_challenge(c: _Contexto, b: dict) -> None:
    c.texto(b, "prompt")
    c.opcion(b, "language", ("html",))
    # El código inicial puede estar vacío en un reto abierto.
    c.texto(b, "starter", vacio=True)
    for i, revision in c.objetos(b, "checks", 1, 20):
        p = f"checks[{i}]."
        c.texto(revision, "selector", p)
        c.texto(revision, "attr", p, obligatorio=False)
        c.texto(revision, "contains", p, obligatorio=False)
        c.entero(revision, "min", p, obligatorio=False, minimo=0)
        igual = _valor(revision, "equals")
        if igual is not _FALTA and not (isinstance(igual, (str, bool)) or _es_numero(igual)):
            c.error(f"«{p}equals» debe ser texto, número o true/false")
        c.texto(revision, "text", p)
    c.texto(b, "solution", obligatorio=False)
    c.booleano(b, "preview")


def _ui_tour(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    pantalla = c.opcion(b, "screen", tuple(ZONAS_PANTALLA))
    for i, parada in c.objetos(b, "stops", 2, 10):
        p = f"stops[{i}]."
        if pantalla:
            c.opcion(parada, "area", ZONAS_PANTALLA[pantalla], p)
        c.texto(parada, "title", p, maximo=120)
        c.texto(parada, "text", p, maximo=600)


def _connection_flow(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    nodos = c.objetos(b, "nodes", 2, 4)
    c.ids_unicos(nodos, "nodes")
    ids = set()
    for i, nodo in nodos:
        p = f"nodes[{i}]."
        ident = c.identificador(nodo, "id", p)
        c.texto(nodo, "label", p, maximo=24)
        c.opcion(nodo, "icon", ICONOS_NODO_FLUJO, p, obligatorio=False)
        if ident:
            ids.add(ident)
    for i, mensaje in c.objetos(b, "messages", 1, 10):
        p = f"messages[{i}]."
        desde = c.texto(mensaje, "from", p)
        hasta = c.texto(mensaje, "to", p)
        for campo, valor in (("from", desde), ("to", hasta)):
            if valor is not None and ids and valor not in ids:
                c.error(f"«{p}{campo}» = «{valor}» no es un nodo (hay: {', '.join(sorted(ids))})")
        if desde is not None and desde == hasta:
            c.error(f"«{p}from» y «{p}to» son el mismo nodo")
        c.texto(mensaje, "label", p, maximo=60)
        c.texto(mensaje, "text", p, maximo=600)
    c.texto(b, "caption", obligatorio=False)


def _param_demo(c: _Contexto, b: dict) -> None:
    c.opcion(b, "demo", DEMOS_PARAMETRO)
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "caption", obligatorio=False)
    c.numero(b, "value", obligatorio=False)


def _classify(c: _Contexto, b: dict) -> None:
    c.texto(b, "question", maximo=300)
    grupos = c.objetos(b, "groups", 2, 4)
    c.ids_unicos(grupos, "groups")
    ids = set()
    for i, grupo in grupos:
        p = f"groups[{i}]."
        ident = c.identificador(grupo, "id", p)
        c.texto(grupo, "label", p, maximo=24)
        if ident:
            ids.add(ident)
    items = c.objetos(b, "items", 3, 10)
    c.ids_unicos(items, "items", clave="text")
    usados = set()
    for i, item in items:
        p = f"items[{i}]."
        c.texto(item, "text", p, maximo=160)
        grupo = c.texto(item, "group", p)
        if grupo is not None and ids and grupo not in ids:
            c.error(f"«{p}group» = «{grupo}» no es un grupo (hay: {', '.join(sorted(ids))})")
        usados.add(grupo)
    vacios = sorted(ids - usados)
    if items and vacios:
        c.error(f"el grupo «{vacios[0]}» no tiene ninguna tarjeta")
    c.texto(b, "explanation", obligatorio=False)


def _true_false(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    afirmaciones = c.objetos(b, "statements", 2, 8)
    c.ids_unicos(afirmaciones, "statements", clave="text")
    for i, afirmacion in afirmaciones:
        p = f"statements[{i}]."
        c.texto(afirmacion, "text", p, maximo=300)
        c.booleano(afirmacion, "answer", p, obligatorio=True)
        c.texto(afirmacion, "explanation", p, obligatorio=False, maximo=600)
    respuestas = {a.get("answer") for _, a in afirmaciones if isinstance(a.get("answer"), bool)}
    if len(afirmaciones) >= 3 and len(respuestas) == 1:
        c.error("todas las afirmaciones tienen la misma respuesta: mezcla verdades y mitos")


def _image_choice(c: _Contexto, b: dict) -> None:
    c.texto(b, "question", maximo=300)
    opciones = c.objetos(b, "options", 2, 4)
    c.ids_unicos(opciones, "options")
    correctas = 0
    for i, opcion in opciones:
        p = f"options[{i}]."
        c.identificador(opcion, "id", p)
        c.opcion(opcion, "figure", FIGURAS_ELEGIR, p)
        c.texto(opcion, "label", p, obligatorio=False, maximo=40)
        if c.booleano(opcion, "isCorrect", p, obligatorio=True):
            correctas += 1
    if opciones and correctas != 1:
        c.error(f"debe haber exactamente una opción correcta (hay {correctas})")
    c.texto(b, "explanation", obligatorio=False)


def _tecla_valida(texto: Any) -> bool:
    if not isinstance(texto, str) or not texto.strip():
        return False
    *mods, tecla = [p.strip() for p in texto.split("+")]
    if any(m.lower() not in ("shift", "ctrl", "control", "alt") for m in mods):
        return False
    return (len(tecla) == 1 and tecla.isalnum()) or tecla.lower() in {t.lower() for t in TECLAS_CON_NOMBRE}


def _shortcut_drill(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=600)
    ejercicios = c.objetos(b, "drills", 1, MAX_EJERCICIOS_TECLAS)
    for i, ejercicio in ejercicios:
        p = f"drills[{i}]."
        c.texto(ejercicio, "prompt", p, maximo=200)
        c.texto(ejercicio, "explanation", p, obligatorio=False, maximo=400)
        teclas = ejercicio.get("keys")
        if not isinstance(teclas, list) or not 1 <= len(teclas) <= MAX_TECLAS:
            c.error(f"«{p}keys» debe ser una lista de 1 a {MAX_TECLAS} teclas")
            continue
        for tecla in teclas:
            if not _tecla_valida(tecla):
                c.error(f"«{p}keys» tiene una tecla que no se puede pulsar: {tecla!r}")


def _transform_challenge(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=600)
    c.texto(b, "explanation", obligatorio=False, maximo=400)
    c.texto(b, "unit", obligatorio=False, maximo=4)
    propiedad = c.opcion(b, "property", PROPIEDADES_TRANSFORMAR)
    c.opcion(b, "axis", EJES_TRANSFORMAR)
    meta = c.numero(b, "target")
    minimo = c.numero(b, "min", obligatorio=False)
    maximo = c.numero(b, "max", obligatorio=False)
    paso = c.numero(b, "step", obligatorio=False, minimo=0.001)
    tolerancia = c.numero(b, "tolerance", obligatorio=False, minimo=0)
    limites = {"location": (-3, 3), "rotation": (-180, 180), "scale": (0.2, 3)}.get(propiedad, (None, None))
    bajo = minimo if minimo is not None else limites[0]
    alto = maximo if maximo is not None else limites[1]
    if None not in (meta, bajo, alto) and not bajo <= meta <= alto:
        c.error(f"«target» = {meta:g} queda fuera del campo ({bajo:g} a {alto:g})")
    inicial = {"location": 0, "rotation": 0, "scale": 1}.get(propiedad)
    margen = tolerancia if tolerancia is not None else (paso or {"location": 0.1, "rotation": 5, "scale": 0.1}.get(propiedad, 0)) / 2
    if meta is not None and inicial is not None and abs(meta - inicial) <= margen:
        c.error("«target» es el valor inicial: el alumno no tendría nada que ajustar")


def _spot_error(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=600)
    pasos = c.objetos(b, "steps", 3, 10)
    c.ids_unicos(pasos, "steps", clave="text")
    for i, paso in pasos:
        p = f"steps[{i}]."
        c.texto(paso, "text", p, maximo=300)
        c.booleano(paso, "wrong", p)
        c.texto(paso, "explanation", p, obligatorio=bool(paso.get("wrong")), maximo=400)
    malos = sum(1 for _, paso in pasos if paso.get("wrong") is True)
    if pasos and not 1 <= malos < len(pasos):
        c.error("marca con «wrong»: true al menos un paso, y deja al menos uno bien hecho")


REVISORES_INTERACTIVOS: Dict[str, Callable[[_Contexto, dict], None]] = {
    "quiz_inline": _quiz_inline,
    "ordering": _ordering,
    "matching": _matching,
    "fill_blanks": _fill_blanks,
    "hotspots": _hotspots,
    "code_challenge": _code_challenge,
    "ui_tour": _ui_tour,
    "connection_flow": _connection_flow,
    "param_demo": _param_demo,
    "classify": _classify,
    "true_false": _true_false,
    "image_choice": _image_choice,
    "shortcut_drill": _shortcut_drill,
    "transform_challenge": _transform_challenge,
    "spot_error": _spot_error,
}
