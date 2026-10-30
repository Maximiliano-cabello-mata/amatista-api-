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
    # Herramientas de enseñanza v3.1 (docs/plataforma/04_herramientas_de_ensenanza.md).
    "step_by_step",
    "shortcuts",
    "compare",
    # Herramientas gráficas v3.6 (docs/plataforma/07_herramientas_graficas.md): SVG propio, sin librerías.
    "mesh_viewer",
    "node_graph",
    # Recursos gráficos v3.7 (docs/plataforma/10_primeros_pasos_y_recursos.md): SVG propio.
    "ui_tour",
    "connection_flow",
    "axis_gizmo",
    "param_demo",
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
    # Amatista Taller (motor 5): las habilidades del módulo en Blender
    # (practices/blender/habilidades.json). Se resuelve cuando se abre el
    # proyecto; con «vista»: «proyecto» es la tarjeta del proyecto y la isla.
    "blender_taller",
    # Recursos v3.7: cada tarjeta a su grupo y ¿verdad o mito?
    "classify",
    "true_false",
    # Primeros pasos dentro de Blender (add-on 5.1, recorrido.py): se resuelve cuando
    # Blender avisa por el enlace en vivo que el recorrido terminó.
    "blender_tour",
    # Recursos gráficos v4.2 (docs/plataforma/13_camino_graficos_y_motor_web.md): se aprende
    # haciendo con la figura, no leyendo listas.
    "viewport_sim",
    "image_choice",
    "keyframe_sim",
    # Motor Web de Amatista (frontend/src/motor3d/): un mundo A-Frame con objetos que se mueven y retos.
    "motor_scene",
    # Dinámicas v4.3 (frontend/src/components/leccion/dinamicas.js): teclas de Blender en el
    # navegador, el campo del panel N con su molde y la receta con errores.
    "shortcut_drill",
    "transform_challenge",
    "spot_error",
)
TIPOS_BLOQUE = BLOQUES_CONTENIDO + BLOQUES_INTERACTIVOS

VARIANTES_AVISO = ("dato", "reto", "estudio")
MODOS_COMPARAR = ("columns", "slider")
MAX_TECLAS = 6
# Visor de malla (frontend/src/components/graficas/malla.js).
FORMAS_MALLA = ("cube", "plane", "cylinder", "cone", "uv_sphere", "ico_sphere", "torus", "custom")
MODOS_MALLA = ("vertex", "edge", "face")
MAX_CARAS_MALLA = 600
MAX_VERTICES_MALLA = 400
# Diagrama de nodos (frontend/src/components/graficas/nodos.js).
CLASES_NODO = ("input", "output", "shader", "texture", "color", "vector", "converter", "geometry", "group", "layout")
CONECTORES_NODO = ("float", "int", "boolean", "vector", "color", "shader", "geometry", "string", "object", "material")
MAX_NODOS = 12
MAX_ENLACES = 24
PRIMITIVAS = ("sphere", "box", "cylinder", "cone", "torus", "icosahedron")
PARAMETROS_ESCENA = ("segments", "color", "wireframe", "metalness", "roughness", "scale", "rotationSpeed")
TIPOS_CONTROL = ("range", "color", "toggle")
# Tipo de control cuando el bloque no lo indica.
CONTROL_POR_PARAMETRO = {"color": "color", "wireframe": "toggle"}
OPERADORES_META = ("<=", ">=", "==", "!=")
# Recursos v3.7: las zonas de cada pantalla del recorrido (frontend/src/components/leccion/recursos.js, PANTALLAS).
ZONAS_PANTALLA = {
    "plataforma": ("barra", "avance", "ayuda", "continuar", "cursos", "panel", "blender"),
    "leccion": ("titulo", "quehacer", "teoria", "actividad", "hexagonos", "completar", "mascota"),
    "blender": ("herramientas", "vista3d", "tarjeta", "molde", "barra_n", "outliner", "propiedades"),
}
ICONOS_NODO_FLUJO = ("plataforma", "servidor", "blender", "alumno")
# Recursos gráficos v4.2 y Motor Web (frontend/src/components/leccion/graficos/, frontend/src/motor3d/).
MISIONES_VISTA = ("orbitar", "acercar", "frente", "arriba", "lado", "encuadrar")
FIGURAS_ELEGIR = (
    "cubo", "cubo_bisel", "cubo_suave", "cilindro", "cono", "esfera", "plano", "toro",
    "fila_array", "espejo", "alambre", "extruido",
)
ENTORNOS_MOTOR = ("pradera", "bosque", "isla", "desierto", "nieve", "ciudad", "cristal", "espacio", "vacio")
FORMAS_MOTOR = ("caja", "esfera", "cilindro", "cono", "toro", "cristal", "plano", "modelo")
MODELOS_MOTOR = ("arbol", "casa", "cristal")
PATRONES_MOTOR = ("fila", "rejilla", "circulo", "espiral", "azar")
RETOS_MOTOR = ("en_zona", "todos_en_zona", "tocar", "cerca", "altura")
DEMOS_PARAMETRO = ("array", "bevel", "subdivision", "mirror", "light", "resolution")

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


def _mesh_viewer(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False)
    forma = c.opcion(b, "shape", FORMAS_MALLA)
    c.entero(b, "segments", obligatorio=False, minimo=3, maximo=64)
    c.entero(b, "rings", obligatorio=False, minimo=3, maximo=24)
    c.entero(b, "subdivisions", obligatorio=False, minimo=1, maximo=3)
    c.opcion(b, "mode", MODOS_MALLA, obligatorio=False)
    c.booleano(b, "wireframe")
    c.texto(b, "caption", obligatorio=False)
    if forma != "custom":
        return
    malla = _valor(b, "mesh")
    if not isinstance(malla, dict):
        c.error("«shape» = «custom» necesita «mesh»: {vertices: [[x, y, z], …], faces: [[0, 1, 2], …]}")
        return
    vertices = malla.get("vertices")
    caras = malla.get("faces")
    if not isinstance(vertices, list) or not 3 <= len(vertices) <= MAX_VERTICES_MALLA or not all(
        isinstance(v, list) and len(v) == 3 and all(_es_numero(x) for x in v) for v in vertices
    ):
        c.error(f"«mesh.vertices» debe ser una lista de 3 a {MAX_VERTICES_MALLA} puntos [x, y, z]")
        return
    if not isinstance(caras, list) or not 1 <= len(caras) <= MAX_CARAS_MALLA:
        c.error(f"«mesh.faces» debe ser una lista de 1 a {MAX_CARAS_MALLA} caras")
        return
    for i, cara in enumerate(caras):
        if not isinstance(cara, list) or len(cara) < 3 or not all(
            _es_entero(v) and 0 <= v < len(vertices) for v in cara
        ):
            c.error(f"«mesh.faces[{i}]» debe tener 3 o más índices de «mesh.vertices» (0 a {len(vertices) - 1})")


def _conectores(c: _Contexto, nodo: dict, campo: str, prefijo: str, entradas: bool) -> set:
    valor = _valor(nodo, campo)
    if valor is _FALTA:
        return set()
    if not isinstance(valor, list) or len(valor) > 8:
        c.error(f"«{prefijo}{campo}» debe ser una lista de hasta 8 conectores")
        return set()
    ids = set()
    for i, conector in enumerate(valor):
        p = f"{prefijo}{campo}[{i}]."
        if not isinstance(conector, dict):
            c.error(f"«{prefijo}{campo}[{i}]» debe ser un objeto {{id, label, socket}}")
            continue
        ident = c.identificador(conector, "id", p)
        c.texto(conector, "label", p, maximo=30)
        c.opcion(conector, "socket", CONECTORES_NODO, p)
        if entradas and "value" in conector and conector["value"] is not None:
            v = conector["value"]
            if not (_es_numero(v) or (isinstance(v, str) and len(v) <= 16)):
                c.error(f"«{p}value» debe ser un número o un texto corto")
        if ident in ids:
            c.error(f"«{p}id» = «{ident}» se repite en el nodo")
        if ident:
            ids.add(ident)
    return ids


def _node_graph(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False)
    nodos = c.objetos(b, "nodes", 1, MAX_NODOS)
    salidas: Dict[str, str] = {}
    entradas: Dict[str, str] = {}
    previos: Dict[str, List[str]] = {}
    for i, nodo in nodos:
        p = f"nodes[{i}]."
        ident = c.identificador(nodo, "id", p)
        c.texto(nodo, "title", p, maximo=28)
        c.opcion(nodo, "kind", CLASES_NODO, p)
        c.entero(nodo, "col", p, obligatorio=False, minimo=0, maximo=6)
        c.texto(nodo, "note", p, obligatorio=False, maximo=300)
        nombres_salida = _conectores(c, nodo, "outputs", p, False)
        nombres_entrada = _conectores(c, nodo, "inputs", p, True)
        if not ident:
            continue
        salidas.update({f"{ident}.{s}": ident for s in nombres_salida})
        entradas.update({f"{ident}.{e}": ident for e in nombres_entrada})
        previos[ident] = []
    c.ids_unicos(nodos, "nodes")
    valor = _valor(b, "links")
    if valor is _FALTA:
        enlaces: List[Tuple[int, dict]] = []
    else:
        enlaces = c.objetos(b, "links", 0, MAX_ENLACES)
    usadas: Dict[str, int] = {}
    for i, enlace in enlaces:
        p = f"links[{i}]."
        desde = c.texto(enlace, "from", p)
        hasta = c.texto(enlace, "to", p)
        if desde is not None and desde not in salidas:
            c.error(f"«{p}from» = «{desde}» no es una salida («nodo.salida») de ningún nodo")
        if hasta is not None and hasta not in entradas:
            c.error(f"«{p}to» = «{hasta}» no es una entrada («nodo.entrada») de ningún nodo")
        if hasta in usadas:
            c.error(f"«{p}to» = «{hasta}» ya recibe un enlace (links[{usadas[hasta]}]): una entrada admite uno solo")
        elif hasta is not None:
            usadas[hasta] = i
        if desde in salidas and hasta in entradas:
            previos[entradas[hasta]].append(salidas[desde])
    # Sin ciclos: el diagrama se acomoda de las entradas a la salida.
    estado: Dict[str, int] = {}

    def ciclo(nodo: str) -> bool:
        if estado.get(nodo) == 1:
            return True
        if estado.get(nodo) == 2:
            return False
        estado[nodo] = 1
        if any(ciclo(p) for p in previos.get(nodo, [])):
            return True
        estado[nodo] = 2
        return False

    if any(ciclo(n) for n in previos):
        c.error("los enlaces forman un ciclo: un nodo no puede alimentarse a sí mismo")
    c.texto(b, "caption", obligatorio=False)


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


VISTAS_TALLER = ("taller", "proyecto")  # taller (por defecto): las habilidades; proyecto: la tarjeta de la isla
# Motor 6 (engine/amatista_engine/taller/etapas.py): la guía de aprendizaje pone dos momentos en Blender por
# módulo. «exploracion»: las primeras habilidades, Mira y un Hazlo conmigo, antes de la teoría. «practica» (por
# defecto): el resto del ciclo hasta el proyecto, en la lección de cierre.
ETAPAS_TALLER = ("exploracion", "practica")
MAX_HABILIDADES_EXPLORACION = 4


def _blender_taller(c: _Contexto, b: dict) -> None:
    taller = c.texto(b, "taller", maximo=60)
    if taller is not None and not PATRON_PRACTICA.match(taller):
        c.error(f"«taller» = «{taller}» debe ser un id de taller como blender.bp.m1 (minúsculas, números, punto, guion)")
    elif taller is not None:
        from contenido.taller import ids_de_talleres

        conocidos = ids_de_talleres()
        if conocidos and taller not in conocidos:
            c.error(f"«taller» = «{taller}» no está en practices/blender/habilidades.json (hay: {', '.join(conocidos)})")
    c.opcion(b, "vista", VISTAS_TALLER, obligatorio=False)
    etapa = c.opcion(b, "etapa", ETAPAS_TALLER, obligatorio=False)
    habilidades = b.get("habilidades")
    if habilidades is not None:
        if etapa != "exploracion":
            c.error("«habilidades» solo va con «etapa»: «exploracion» (la práctica es el taller entero)")
        elif (not isinstance(habilidades, list) or not habilidades or len(habilidades) > MAX_HABILIDADES_EXPLORACION
              or not all(isinstance(h, str) for h in habilidades)):
            c.error(f"«habilidades» debe ser una lista de 1 a {MAX_HABILIDADES_EXPLORACION} ids de habilidad")
        elif taller is not None:
            from contenido.taller import catalogo

            cat = catalogo()
            t = cat.taller(taller) if cat else None
            ajenas = [h for h in habilidades if t is not None and h not in t.habilidades]
            if ajenas:
                c.error(f"«habilidades»: {', '.join(ajenas)} no son del taller «{taller}»")
    c.texto(b, "title", maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=2000)


# --- Recursos gráficos v3.7 ----------------------------------------------------------------


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


def _axis_gizmo(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
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


def _blender_tour(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=2000)
    c.booleano(b, "allowManual")


def _viewport_sim(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "caption", obligatorio=False)
    misiones = _valor(b, "missions")
    if misiones is _FALTA:
        return
    if not isinstance(misiones, list) or not 1 <= len(misiones) <= len(MISIONES_VISTA):
        c.error(f"«missions» debe ser una lista de 1 a {len(MISIONES_VISTA)} misiones")
        return
    for i, mision in enumerate(misiones):
        if mision not in MISIONES_VISTA:
            c.error(f"«missions[{i}]» = «{mision}» no es válida (usa: {', '.join(MISIONES_VISTA)})")
    if len(set(m for m in misiones if isinstance(m, str))) != len(misiones):
        c.error("«missions» tiene misiones repetidas")


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


def _keyframe_sim(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "caption", obligatorio=False)
    cuadros = c.entero(b, "frames", obligatorio=False, minimo=12, maximo=120) or 60
    metas = c.objetos(b, "targets", 2, 6)
    vistos = set()
    for i, meta in metas:
        p = f"targets[{i}]."
        cuadro = c.entero(meta, "frame", p, minimo=1, maximo=cuadros)
        c.numero(meta, "height", p, minimo=0, maximo=1)
        if cuadro in vistos:
            c.error(f"«{p}frame» = {cuadro} se repite")
        vistos.add(cuadro)


# Teclas que entiende «Entrena tus teclas» (dinamicas.js › normalizarCombinacion): una letra o número,
# o una tecla con nombre, con Shift, Ctrl o Alt delante.
TECLAS_CON_NOMBRE = ("Tab", "Espacio", "Esc", "Enter", "Delete", "Backspace", "F2", "F3", "F12", "Home",
                     "Plus", "Minus", "↑", "↓", "←", "→")
MAX_EJERCICIOS_TECLAS = 8
PROPIEDADES_TRANSFORMAR = ("location", "rotation", "scale")
EJES_TRANSFORMAR = ("X", "Y", "Z")


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


def _motor_scene(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "instructions", obligatorio=False, maximo=600)
    c.texto(b, "success", obligatorio=False, maximo=300)
    escena = _valor(b, "escena")
    if not isinstance(escena, dict):
        c.error("falta «escena» (un objeto con entorno, objetos, zonas y retos)")
        return
    entorno = escena.get("entorno")
    if entorno is not None:
        if not isinstance(entorno, dict):
            c.error("«escena.entorno» debe ser un objeto o null")
        else:
            c.opcion(entorno, "tipo", ENTORNOS_MOTOR, "escena.entorno.")
            c.numero(entorno, "tamano", "escena.entorno.", obligatorio=False, minimo=10, maximo=120)
            c.numero(entorno, "densidad", "escena.entorno.", obligatorio=False, minimo=0, maximo=1)
    ids = set()
    for lista, maximo in (("objetos", 40), ("zonas", 8), ("generar", 6), ("retos", 8)):
        valor = escena.get(lista, [])
        if not isinstance(valor, list) or len(valor) > maximo:
            c.error(f"«escena.{lista}» debe ser una lista de hasta {maximo}")
    for i, objeto in enumerate(escena.get("objetos") or []):
        p = f"escena.objetos[{i}]."
        if not isinstance(objeto, dict):
            c.error(f"«{p[:-1]}» debe ser un objeto JSON")
            continue
        ident = c.identificador(objeto, "id", p)
        if ident in ids:
            c.error(f"«{p}id» = «{ident}» se repite")
        ids.add(ident)
        forma = c.opcion(objeto, "forma", FORMAS_MOTOR, p, obligatorio=False)
        if forma == "modelo":
            c.opcion(objeto, "modelo", MODELOS_MOTOR, p)
    for i, zona in enumerate(escena.get("zonas") or []):
        p = f"escena.zonas[{i}]."
        if not isinstance(zona, dict):
            c.error(f"«{p[:-1]}» debe ser un objeto JSON")
            continue
        ident = c.identificador(zona, "id", p)
        if ident in ids:
            c.error(f"«{p}id» = «{ident}» ya lo usa otro objeto")
        ids.add(ident)
        c.numero(zona, "radio", p, obligatorio=False, minimo=0.3, maximo=10)
    for i, grupo in enumerate(escena.get("generar") or []):
        if isinstance(grupo, dict):
            c.opcion(grupo, "patron", PATRONES_MOTOR, f"escena.generar[{i}].", obligatorio=False)
    retos = escena.get("retos") or []
    for i, reto in enumerate(retos):
        p = f"escena.retos[{i}]."
        if not isinstance(reto, dict):
            c.error(f"«{p[:-1]}» debe ser un objeto JSON")
            continue
        c.opcion(reto, "tipo", RETOS_MOTOR, p)
        for campo in ("objeto", "zona", "de"):
            ref = reto.get(campo)
            if ref is not None and str(ref).lstrip("#") not in ids:
                c.error(f"«{p}{campo}» = «{ref}» no está en la escena")
        for ref in reto.get("objetos") or []:
            if str(ref).lstrip("#") not in ids:
                c.error(f"«{p}objetos»: «{ref}» no está en la escena")
        c.texto(reto, "texto", p, obligatorio=False, maximo=160)


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
    "step_by_step": _step_by_step,
    "shortcuts": _shortcuts,
    "compare": _compare,
    "mesh_viewer": _mesh_viewer,
    "node_graph": _node_graph,
    "quiz_inline": _quiz_inline,
    "ordering": _ordering,
    "matching": _matching,
    "fill_blanks": _fill_blanks,
    "hotspots": _hotspots,
    "scene_explorer": _scene_explorer,
    "code_challenge": _code_challenge,
    "blender_practice": _blender_practice,
    "blender_taller": _blender_taller,
    "ui_tour": _ui_tour,
    "connection_flow": _connection_flow,
    "axis_gizmo": _axis_gizmo,
    "param_demo": _param_demo,
    "classify": _classify,
    "true_false": _true_false,
    "blender_tour": _blender_tour,
    "viewport_sim": _viewport_sim,
    "image_choice": _image_choice,
    "keyframe_sim": _keyframe_sim,
    "motor_scene": _motor_scene,
    "shortcut_drill": _shortcut_drill,
    "transform_challenge": _transform_challenge,
    "spot_error": _spot_error,
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
    _revisar_orden_practica(errores, lecciones)
    return errores


def _es_taller(bloque: Any) -> bool:
    """Un bloque blender_taller de habilidades (la vista «proyecto» es solo la tarjeta del proyecto)."""
    return isinstance(bloque, dict) and bloque.get("type") == "blender_taller" and bloque.get("vista") != "proyecto"


def es_practica_blender(leccion: Any) -> bool:
    """Lección que se hace en Blender: un bloque blender_practice (el proyecto) o el taller del módulo (motor 5)."""
    bloques = leccion.get("contentBlocks") if isinstance(leccion, dict) else None
    return isinstance(bloques, list) and any(
        (isinstance(b, dict) and b.get("type") == "blender_practice") or _es_taller(b) for b in bloques
    )


def practicas_de_leccion(leccion: Any) -> List[str]:
    """Ids de práctica de los bloques blender_practice de una lección."""
    bloques = leccion.get("contentBlocks") if isinstance(leccion, dict) else None
    return [
        b["practica"] for b in (bloques if isinstance(bloques, list) else [])
        if isinstance(b, dict) and b.get("type") == "blender_practice" and isinstance(b.get("practica"), str)
    ]


def talleres_de_leccion(leccion: Any) -> List[str]:
    """Ids de taller de los bloques blender_taller de habilidades de una lección."""
    bloques = leccion.get("contentBlocks") if isinstance(leccion, dict) else None
    return [b["taller"] for b in (bloques if isinstance(bloques, list) else [])
            if _es_taller(b) and isinstance(b.get("taller"), str)]


def etapas_de_leccion(leccion: Any) -> List[tuple]:
    """(taller, etapa) de los bloques blender_taller de habilidades de una lección (motor 6)."""
    bloques = leccion.get("contentBlocks") if isinstance(leccion, dict) else None
    return [(b["taller"], b.get("etapa") if b.get("etapa") in ETAPAS_TALLER else "practica")
            for b in (bloques if isinstance(bloques, list) else []) if _es_taller(b) and isinstance(b.get("taller"), str)]


def _revisar_orden_practica(errores: List[str], lecciones: List[Any]) -> None:
    """Teoría y Blender se intercalan (v3.2, docs/plataforma/02).

    Un módulo puede tener varias prácticas en Blender en cualquier lugar: una
    exploración corta entre dos lecciones de teoría y la práctica de cierre.
    Lo único que no se permite es repetir la misma práctica en el módulo: el
    motor registra un avance por práctica. Lo mismo con el taller de
    habilidades: una lección por taller y etapa (motor 6), y la exploración
    antes que la práctica (la guía de aprendizaje: tocar antes de estudiar).
    """
    vistas: Dict[str, int] = {}
    talleres: Dict[tuple, int] = {}
    for i, leccion in enumerate(lecciones):
        for practica in practicas_de_leccion(leccion):
            if practica in vistas:
                _Contexto(errores, f"lessons[{i}]").error(
                    f"la práctica «{practica}» ya está en lessons[{vistas[practica]}]: cada práctica va una sola vez por módulo"
                )
            else:
                vistas[practica] = i
        for clave in etapas_de_leccion(leccion):
            taller, etapa = clave
            if clave in talleres:
                _Contexto(errores, f"lessons[{i}]").error(
                    f"el taller «{taller}» (etapa {etapa}) ya está en lessons[{talleres[clave]}]: "
                    "cada etapa del taller va una sola vez por módulo"
                )
                continue
            if etapa == "exploracion" and (taller, "practica") in talleres:
                _Contexto(errores, f"lessons[{i}]").error(
                    f"la exploración del taller «{taller}» va antes que su práctica (lessons[{talleres[(taller, 'practica')]}])"
                )
            talleres[clave] = i
