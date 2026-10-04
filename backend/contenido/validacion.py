"""Validación de módulos, lecciones y bloques (formato de frontend/src/data/modulos).

La usan la API de contenido (no se publica nada que no valide), el editor
del panel y la CLI (herramientas/contenido.py validar, también en CI).
Cada error dice dónde está el problema, en español:

    lessons[2].contentBlocks[4] (ordering): necesita entre 3 y 8 items (tiene 2)

Tolerante con los campos extra (el contenido futuro no se rompe) y estricta
con los obligatorios: una lección que pasa esta validación se puede mostrar
en la PWA sin errores.
"""
import json
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

TIPOS_LECCION = ("theory_reading", "theory_interactive", "video_lesson", "code_interactive", "exam")
PASOS_FORMULA = ("gancho", "explora", "practica", "reto", "jefe")
# "revision" solo existe en los archivos: en la base un módulo en revisión es un borrador.
ESTADOS_MODULO = ("borrador", "revision", "publicado", "archivado")

BLOQUES_CONTENIDO = (
    "markdown_text",
    "image",
    "concept_cards",
    "timeline",
    "pipeline",
    "layers",
    "callout",
    "code_snippet",
    "video_player",
)
# Llevan id único en la lección y "required" (por defecto true): la lección
# se completa cuando todos los requeridos están resueltos.
BLOQUES_INTERACTIVOS = (
    "quiz_inline",
    "ordering",
    "matching",
    "fill_blanks",
    "hotspots",
    "scene_explorer",
    "code_challenge",
    # Práctica del motor dentro de Blender (docs/motor/): se resuelve cuando el
    # servidor registra la práctica completada desde el add-on.
    "blender_practice",
)
TIPOS_BLOQUE = BLOQUES_CONTENIDO + BLOQUES_INTERACTIVOS

VARIANTES_AVISO = ("dato", "reto")
PRIMITIVAS = ("sphere", "box", "cylinder", "cone", "torus", "icosahedron")
PARAMETROS_ESCENA = ("segments", "color", "wireframe", "metalness", "roughness", "scale", "rotationSpeed")
TIPOS_CONTROL = ("range", "color", "toggle")
# Tipo de control cuando el bloque no lo indica.
CONTROL_POR_PARAMETRO = {"color": "color", "wireframe": "toggle"}
OPERADORES_META = ("<=", ">=", "==", "!=")

# Letras, números, guion y guion bajo: los ids viajan en la URL (#/curso/c/leccion/l)
# y en la clave "curso:leccion" del progreso, así que no admiten ":" ni espacios.
PATRON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PATRON_HUECO = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)

# Tamaños de las columnas de backend/sql/002 (CURSOS, MODULOS, LECCIONES).
MAX_ID = 50
MAX_TITULO = 200
MAX_DESCRIPCION = 1000
MAX_INSIGNIA = 80
MAX_REEMPLAZA = 250
# Ficha de la lección (reestructuración v3, docs/reestructuracion/01_modelo_de_contenido.md).
MAX_OBJETIVO = 300
MAX_HABILIDADES = 8
PATRON_VERSION_BLENDER = re.compile(r"^\d+\.\d+(\.\d+)?$")

_FALTA = object()


class ContenidoInvalido(ValueError):
    """Contenido que no pasa la validación; `errores` trae los mensajes."""

    def __init__(self, errores: List[str]):
        super().__init__(errores[0] if errores else "Contenido inválido.")
        self.errores = errores


def _unir(ruta: str, parte: str) -> str:
    if not ruta or parte.startswith("["):
        return f"{ruta}{parte}"
    return f"{ruta}.{parte}"


def _es_entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _es_numero(valor: Any) -> bool:
    # NaN e infinito no son JSON válido (Oracle los rechaza con IS JSON).
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def _valor(objeto: dict, campo: str) -> Any:
    """Valor del campo; null cuenta como ausente (contratos tolerantes)."""
    valor = objeto.get(campo, _FALTA)
    return _FALTA if valor is None else valor


class _Contexto:
    """Revisa campos de un objeto y anota los errores con su ruta."""

    def __init__(self, errores: List[str], etiqueta: str):
        self.errores = errores
        self.etiqueta = etiqueta

    def error(self, mensaje: str) -> None:
        self.errores.append(f"{self.etiqueta}: {mensaje}" if self.etiqueta else mensaje)

    def texto(
        self,
        objeto: dict,
        campo: str,
        prefijo: str = "",
        obligatorio: bool = True,
        maximo: Optional[int] = None,
        vacio: bool = False,
    ) -> Optional[str]:
        nombre = f"{prefijo}{campo}"
        valor = _valor(objeto, campo)
        if valor is _FALTA:
            if obligatorio:
                self.error(f"falta «{nombre}»")
            return None
        if not isinstance(valor, str):
            self.error(f"«{nombre}» debe ser texto")
            return None
        if not vacio and not valor.strip():
            self.error(f"«{nombre}» está vacío")
            return None
        if maximo is not None and len(valor) > maximo:
            self.error(f"«{nombre}» tiene {len(valor)} caracteres (máximo {maximo})")
        return valor

    def identificador(self, objeto: dict, campo: str = "id", prefijo: str = "", obligatorio: bool = True) -> Optional[str]:
        valor = self.texto(objeto, campo, prefijo, obligatorio=obligatorio, maximo=MAX_ID)
        if valor is not None and not PATRON_ID.match(valor):
            self.error(f"«{prefijo}{campo}» = «{valor}» solo admite letras, números, guion y guion bajo")
        return valor

    def entero(
        self,
        objeto: dict,
        campo: str,
        prefijo: str = "",
        obligatorio: bool = True,
        minimo: Optional[int] = None,
        maximo: Optional[int] = None,
    ) -> Optional[int]:
        nombre = f"{prefijo}{campo}"
        valor = _valor(objeto, campo)
        if valor is _FALTA:
            if obligatorio:
                self.error(f"falta «{nombre}»")
            return None
        if not _es_entero(valor):
            self.error(f"«{nombre}» debe ser un número entero")
            return None
        if (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo):
            if maximo is None:
                self.error(f"«{nombre}» debe ser {minimo} o más (es {valor})")
            else:
                self.error(f"«{nombre}» debe estar entre {minimo} y {maximo} (es {valor})")
            return None
        return valor

    def numero(
        self,
        objeto: dict,
        campo: str,
        prefijo: str = "",
        obligatorio: bool = True,
        minimo: Optional[float] = None,
        maximo: Optional[float] = None,
    ) -> Optional[float]:
        nombre = f"{prefijo}{campo}"
        valor = _valor(objeto, campo)
        if valor is _FALTA:
            if obligatorio:
                self.error(f"falta «{nombre}»")
            return None
        if not _es_numero(valor):
            self.error(f"«{nombre}» debe ser un número")
            return None
        if (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo):
            limites = " y ".join(f"{x:g}" for x in (minimo, maximo) if x is not None)
            self.error(f"«{nombre}» debe estar entre {limites} (es {valor:g})")
            return None
        return valor

    def booleano(self, objeto: dict, campo: str, prefijo: str = "", obligatorio: bool = False) -> Optional[bool]:
        nombre = f"{prefijo}{campo}"
        valor = _valor(objeto, campo)
        if valor is _FALTA:
            if obligatorio:
                self.error(f"falta «{nombre}» (true o false)")
            return None
        if not isinstance(valor, bool):
            self.error(f"«{nombre}» debe ser true o false")
            return None
        return valor

    def opcion(self, objeto: dict, campo: str, opciones: Tuple[str, ...], prefijo: str = "", obligatorio: bool = True) -> Optional[str]:
        nombre = f"{prefijo}{campo}"
        valor = _valor(objeto, campo)
        if valor is _FALTA:
            if obligatorio:
                self.error(f"falta «{nombre}» (usa: {', '.join(opciones)})")
            return None
        if valor not in opciones:
            self.error(f"«{nombre}» = «{valor}» no es válido (usa: {', '.join(opciones)})")
            return None
        return valor

    def objetos(self, objeto: dict, campo: str, minimo: int = 1, maximo: Optional[int] = None) -> List[Tuple[int, dict]]:
        """Lista de objetos JSON con su índice; los elementos que no son objetos se reportan."""
        valor = _valor(objeto, campo)
        if valor is _FALTA:
            self.error(f"falta «{campo}»")
            return []
        if not isinstance(valor, list):
            self.error(f"«{campo}» debe ser una lista")
            return []
        if len(valor) < minimo or (maximo is not None and len(valor) > maximo):
            if maximo is None:
                self.error(f"necesita al menos {minimo} {campo} (tiene {len(valor)})")
            else:
                self.error(f"necesita entre {minimo} y {maximo} {campo} (tiene {len(valor)})")
        elementos = []
        for i, elemento in enumerate(valor):
            if isinstance(elemento, dict):
                elementos.append((i, elemento))
            else:
                self.error(f"«{campo}[{i}]» debe ser un objeto JSON")
        return elementos

    def ids_unicos(self, elementos: List[Tuple[int, dict]], campo: str, clave: str = "id") -> None:
        vistos: Dict[Any, int] = {}
        for i, elemento in elementos:
            valor = elemento.get(clave)
            if not isinstance(valor, str) or not valor.strip():
                continue
            normal = valor.strip().lower() if clave != "id" else valor
            if normal in vistos:
                self.error(f"«{campo}[{i}].{clave}» = «{valor}» se repite (ya está en {campo}[{vistos[normal]}])")
            else:
                vistos[normal] = i


# --- Bloques de contenido ----------------------------------------------------


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


# --- Bloques interactivos ----------------------------------------------------


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


def _cumple(valor: Any, operador: str, meta: Any) -> bool:
    if operador == "<=":
        return valor <= meta
    if operador == ">=":
        return valor >= meta
    if operador == "==":
        return valor == meta
    return valor != meta


def _scene_explorer(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False)
    c.opcion(b, "primitive", PRIMITIVAS)
    controles = c.objetos(b, "controls", 1, len(PARAMETROS_ESCENA))
    tipos: Dict[str, str] = {}
    rangos: Dict[str, Tuple[Optional[float], Optional[float], Any]] = {}
    for i, control in controles:
        p = f"controls[{i}]."
        parametro = c.opcion(control, "param", PARAMETROS_ESCENA, p)
        c.texto(control, "label", p)
        tipo = c.opcion(control, "type", TIPOS_CONTROL, p, obligatorio=False)
        if parametro is None:
            continue
        tipo = tipo or CONTROL_POR_PARAMETRO.get(parametro, "range")
        tipos[parametro] = tipo
        if tipo == "range":
            minimo = c.numero(control, "min", p, obligatorio=False)
            maximo = c.numero(control, "max", p, obligatorio=False)
            paso = c.numero(control, "step", p, obligatorio=False)
            defecto = c.numero(control, "default", p, obligatorio=False)
            if minimo is not None and maximo is not None and minimo >= maximo:
                c.error(f"«{p}min» debe ser menor que «{p}max»")
            if paso is not None and paso <= 0:
                c.error(f"«{p}step» debe ser mayor que 0")
            if defecto is not None and (
                (minimo is not None and defecto < minimo) or (maximo is not None and defecto > maximo)
            ):
                c.error(f"«{p}default» queda fuera del rango min–max")
            rangos[parametro] = (minimo, maximo, defecto)
        elif tipo == "color":
            rangos[parametro] = (None, None, c.texto(control, "default", p, obligatorio=False))
        else:
            rangos[parametro] = (None, None, c.booleano(control, "default", p))
    c.ids_unicos(controles, "controls", clave="param")

    meta = _valor(b, "goal")
    if meta is _FALTA:
        return
    if not isinstance(meta, dict):
        c.error("«goal» debe ser un objeto {param, op, value, text}")
        return
    parametro = c.opcion(meta, "param", PARAMETROS_ESCENA, "goal.")
    operador = c.opcion(meta, "op", OPERADORES_META, "goal.")
    c.texto(meta, "text", "goal.")
    valor = _valor(meta, "value")
    if valor is _FALTA:
        c.error("falta «goal.value»")
        return
    if parametro is None:
        return
    if parametro not in tipos:
        c.error(f"«goal.param» = «{parametro}» no tiene un control en «controls»")
        return
    tipo = tipos[parametro]
    if tipo == "range" and not _es_numero(valor):
        c.error("«goal.value» debe ser un número")
        return
    if tipo == "toggle" and not isinstance(valor, bool):
        c.error("«goal.value» debe ser true o false")
        return
    if tipo == "color" and not isinstance(valor, str):
        c.error("«goal.value» debe ser texto (un color)")
        return
    if operador in ("<=", ">=") and tipo != "range":
        c.error(f"«goal.op» = «{operador}» solo sirve con controles numéricos")
        return
    minimo, maximo, defecto = rangos[parametro]
    if tipo == "range" and operador:
        alcanzable = (
            (operador != "<=" or minimo is None or minimo <= valor)
            and (operador != ">=" or maximo is None or maximo >= valor)
            and (operador != "==" or ((minimo is None or minimo <= valor) and (maximo is None or valor <= maximo)))
        )
        if not alcanzable:
            c.error("«goal.value» no se puede alcanzar con el rango min–max del control")
    comparables = _es_numero(defecto) if tipo == "range" else type(defecto) is type(valor)
    if operador and defecto is not None and comparables and _cumple(defecto, operador, valor):
        c.error("la meta («goal») ya se cumple con el valor inicial («default»): no habría nada que hacer")


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


# Mismo patrón que los ids de práctica del motor (engine/.../practice/schema.py).
PATRON_PRACTICA = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


def _blender_practice(c: _Contexto, b: dict) -> None:
    practica = c.texto(b, "practica", maximo=80)
    if practica is not None and not PATRON_PRACTICA.match(practica):
        c.error(f"«practica» = «{practica}» debe ser un id de práctica como blender.n1.mesa (minúsculas, números, punto, guion)")
    c.texto(b, "title", maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=2000)
    c.entero(b, "minutes", obligatorio=False, minimo=1, maximo=240)
    # Vista previa de los pasos para mostrar sin conexión (el detalle vive en la práctica).
    pasos = _valor(b, "steps")
    if pasos is not _FALTA and (
        not isinstance(pasos, list) or len(pasos) > 30 or not all(isinstance(p, str) and p.strip() for p in pasos)
    ):
        c.error("«steps» debe ser una lista de hasta 30 textos")
    # Permite marcarla como hecha sin el add-on (por ejemplo, en un computador sin Blender).
    c.booleano(b, "allowManual")


REVISORES: Dict[str, Callable[[_Contexto, dict], None]] = {
    "markdown_text": _markdown_text,
    "image": _image,
    "concept_cards": _concept_cards,
    "timeline": _timeline,
    "pipeline": _pipeline,
    "layers": _layers,
    "callout": _callout,
    "code_snippet": _code_snippet,
    "video_player": _video_player,
    "quiz_inline": _quiz_inline,
    "ordering": _ordering,
    "matching": _matching,
    "fill_blanks": _fill_blanks,
    "hotspots": _hotspots,
    "scene_explorer": _scene_explorer,
    "code_challenge": _code_challenge,
    "blender_practice": _blender_practice,
}


def _revisar_bloques(errores: List[str], bloques: List[Tuple[int, dict]], ruta: str) -> None:
    ids: Dict[str, int] = {}
    for i, bloque in bloques:
        ruta_bloque = _unir(ruta, f"contentBlocks[{i}]")
        tipo = bloque.get("type")
        if tipo not in REVISORES:
            _Contexto(errores, ruta_bloque).error(
                f"tipo de bloque desconocido «{tipo}» (usa: {', '.join(TIPOS_BLOQUE)})"
                if tipo
                else "falta «type» del bloque"
            )
            continue
        c = _Contexto(errores, f"{ruta_bloque} ({tipo})")
        if tipo in BLOQUES_INTERACTIVOS:
            c.identificador(bloque, "id")
            c.booleano(bloque, "required")
        REVISORES[tipo](c, bloque)
        bloque_id = bloque.get("id")
        if isinstance(bloque_id, str) and bloque_id:
            if bloque_id in ids:
                c.error(f"el id «{bloque_id}» se repite en la lección (ya lo usa contentBlocks[{ids[bloque_id]}])")
            else:
                ids[bloque_id] = i


def _revisar_examen(errores: List[str], leccion: dict, ruta: str) -> None:
    quiz = _valor(leccion, "quizData")
    if quiz is _FALTA or not isinstance(quiz, dict):
        _Contexto(errores, ruta).error("un examen necesita «quizData» con passingScore y questions")
        return
    ruta_quiz = _unir(ruta, "quizData")
    c = _Contexto(errores, ruta_quiz)
    c.entero(quiz, "passingScore", minimo=0, maximo=100)
    preguntas = c.objetos(quiz, "questions", 1, 50)
    c.ids_unicos(preguntas, "questions")
    for i, pregunta in preguntas:
        cp = _Contexto(errores, f"{ruta_quiz}.questions[{i}]")
        cp.identificador(pregunta, "id")
        cp.texto(pregunta, "questionText")
        # El examen muestra las opciones con las letras A a E.
        _opciones(cp, pregunta, 2, 5)
        cp.texto(pregunta, "feedbackCorrect", obligatorio=False)
        cp.texto(pregunta, "feedbackIncorrect", obligatorio=False)


def _lista_ids(c: _Contexto, objeto: dict, campo: str, maximo: int) -> None:
    valor = _valor(objeto, campo)
    if valor is _FALTA:
        return
    if not isinstance(valor, list) or not all(isinstance(x, str) and PATRON_ID.match(x) for x in valor):
        c.error(f"«ficha.{campo}» debe ser una lista de ids (letras, números, guion y guion bajo)")
    elif len(valor) > maximo:
        c.error(f"«ficha.{campo}» admite como máximo {maximo} elementos (tiene {len(valor)})")


def _revisar_ficha(c: _Contexto, leccion: dict) -> None:
    """Ficha opcional de la lección: objetivo, habilidades, versión de Blender...

    Es opcional para no romper las lecciones anteriores a la v3; el mapa del
    curso (GET /api/contenido/mapa) señala las fichas incompletas.
    """
    ficha = _valor(leccion, "ficha")
    if ficha is _FALTA:
        return
    if not isinstance(ficha, dict):
        c.error("«ficha» debe ser un objeto")
        return
    # Los textos de la ficha pueden quedar vacíos mientras se escribe la lección.
    c.texto(ficha, "objetivo", "ficha.", obligatorio=False, maximo=MAX_OBJETIVO, vacio=True)
    _lista_ids(c, ficha, "habilidades", MAX_HABILIDADES)
    _lista_ids(c, ficha, "prerrequisitos", 20)
    c.texto(ficha, "edicion", "ficha.", obligatorio=False, maximo=20, vacio=True)
    c.booleano(ficha, "offline", "ficha.")
    blender = _valor(ficha, "blender")
    if blender is not _FALTA:
        if not isinstance(blender, dict):
            c.error("«ficha.blender» debe ser un objeto {verificadaEn, notas}")
        else:
            version = c.texto(blender, "verificadaEn", "ficha.blender.", obligatorio=False, maximo=20)
            if version is not None and not PATRON_VERSION_BLENDER.match(version):
                c.error(f"«ficha.blender.verificadaEn» = «{version}» debe ser una versión como 4.2 o 4.2.3")
            c.texto(blender, "notas", "ficha.blender.", obligatorio=False, maximo=500, vacio=True)
    practica = _valor(ficha, "practica")
    if practica is not _FALTA:
        if not isinstance(practica, dict):
            c.error("«ficha.practica» debe ser un objeto {archivo, evidencia}")
        else:
            c.texto(practica, "archivo", "ficha.practica.", obligatorio=False, maximo=300, vacio=True)
            c.texto(practica, "evidencia", "ficha.practica.", obligatorio=False, maximo=300, vacio=True)
    comprobacion = _valor(ficha, "comprobacion")
    if comprobacion is not _FALTA and (
        not isinstance(comprobacion, list) or not all(isinstance(x, str) and x.strip() for x in comprobacion)
    ):
        c.error("«ficha.comprobacion» debe ser una lista de criterios (textos)")


def pendientes_ficha(leccion: Any, requiere_blender: bool = True) -> List[str]:
    """Lo que le falta a la ficha para cumplir la estructura mínima (no son errores).

    requiere_blender=False para cursos que no se trabajan en Blender (A-Frame).
    """
    if not isinstance(leccion, dict):
        return ["ficha"]
    ficha = leccion.get("ficha")
    if not isinstance(ficha, dict):
        return ["ficha"]
    faltan = []
    if not (isinstance(ficha.get("objetivo"), str) and ficha["objetivo"].strip()):
        faltan.append("objetivo")
    if not ficha.get("habilidades"):
        faltan.append("habilidades")
    blender = ficha.get("blender")
    if requiere_blender and not (isinstance(blender, dict) and blender.get("verificadaEn")):
        faltan.append("versión de Blender verificada")
    if not ficha.get("comprobacion"):
        faltan.append("criterios de comprobación")
    return faltan


def _revisar_leccion(errores: List[str], leccion: Any, ruta: str) -> None:
    c = _Contexto(errores, ruta)
    if not isinstance(leccion, dict):
        c.error("la lección debe ser un objeto JSON")
        return
    leccion_id = c.identificador(leccion, "id")
    c.texto(leccion, "title", maximo=MAX_TITULO)
    tipo = c.opcion(leccion, "type", TIPOS_LECCION)
    c.booleano(leccion, "isLocked", obligatorio=True)
    c.entero(leccion, "durationSeconds", obligatorio=False, minimo=1, maximo=86400)
    c.texto(leccion, "slug", obligatorio=False)
    c.opcion(leccion, "formula", PASOS_FORMULA, obligatorio=False)

    portada = _valor(leccion, "cover")
    if portada is not _FALTA:
        if isinstance(portada, dict):
            c.texto(portada, "src", "cover.")
            c.texto(portada, "alt", "cover.")
        else:
            c.error("«cover» debe ser un objeto {src, alt}")

    _revisar_ficha(c, leccion)

    reemplaza = _valor(leccion, "replaces")
    if reemplaza is not _FALTA:
        if not isinstance(reemplaza, list) or not all(isinstance(x, str) and PATRON_ID.match(x) for x in reemplaza):
            c.error("«replaces» debe ser una lista de ids de lecciones")
        elif leccion_id in reemplaza:
            c.error("«replaces» no puede incluir el id de la propia lección")
        elif len(",".join(reemplaza)) > MAX_REEMPLAZA:
            c.error(f"«replaces» es demasiado largo (máximo {MAX_REEMPLAZA} caracteres en total)")

    if tipo == "exam":
        _revisar_examen(errores, leccion, ruta)
        if _valor(leccion, "contentBlocks") is not _FALTA:
            _revisar_bloques(errores, c.objetos(leccion, "contentBlocks", 0), ruta)
    else:
        _revisar_bloques(errores, c.objetos(leccion, "contentBlocks", 1, 60), ruta)

    try:
        json.dumps(leccion, allow_nan=False)
    except (TypeError, ValueError):
        c.error("la lección tiene valores que no son JSON válido (NaN o infinito)")


def validar_leccion(leccion: Any, ruta: str = "") -> List[str]:
    """Errores de una lección (LeccionJSON); lista vacía si es válida."""
    errores: List[str] = []
    _revisar_leccion(errores, leccion, ruta)
    return errores


def modulo_de(datos: Any) -> Any:
    """Acepta el archivo completo ({"module": {...}}) o solo el módulo."""
    if isinstance(datos, dict) and isinstance(datos.get("module"), dict):
        return datos["module"]
    return datos


def validar_modulo(datos: Any) -> List[str]:
    """Errores de un módulo (archivo {"module": ...} o ModuloJSON del catálogo)."""
    errores: List[str] = []
    modulo = modulo_de(datos)
    c = _Contexto(errores, "module")
    if not isinstance(modulo, dict):
        c.error("el módulo debe ser un objeto JSON")
        return errores
    c.identificador(modulo, "id")
    c.texto(modulo, "title", maximo=MAX_TITULO)
    c.texto(modulo, "description", obligatorio=False, maximo=MAX_DESCRIPCION, vacio=True)
    c.entero(modulo, "estimatedTimeMinutes", obligatorio=False, minimo=0, maximo=10000)
    c.entero(modulo, "order", minimo=1, maximo=999)
    c.identificador(modulo, "curso", obligatorio=False)
    c.identificador(modulo, "nivel", obligatorio=False)
    c.texto(modulo, "insignia", obligatorio=False, maximo=MAX_INSIGNIA)
    c.opcion(modulo, "estado", ESTADOS_MODULO, obligatorio=False)

    lecciones = _valor(modulo, "lessons")
    if lecciones is _FALTA or not isinstance(lecciones, list) or not lecciones:
        c.error("«lessons» debe ser una lista con al menos una lección")
        return errores
    ids: Dict[str, int] = {}
    for i, leccion in enumerate(lecciones):
        ruta = f"lessons[{i}]"
        _revisar_leccion(errores, leccion, ruta)
        leccion_id = leccion.get("id") if isinstance(leccion, dict) else None
        if isinstance(leccion_id, str) and leccion_id:
            if leccion_id in ids:
                _Contexto(errores, ruta).error(f"el id «{leccion_id}» se repite (ya lo usa lessons[{ids[leccion_id]}])")
            else:
                ids[leccion_id] = i
    return errores
