"""Tipos, constantes y contexto de validación compartidos."""
import math
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

TIPOS_LECCION = ("theory_reading", "theory_interactive", "video_lesson", "code_interactive", "exam")
PASOS_FORMULA = ("gancho", "explora", "practica", "reto", "jefe")
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
    # Motor 6.1 (practices/blender/fichas.json): la ficha de una herramienta con su lugar en la ventana de
    # Blender y el mapa de la ventana para recorrerla o encontrar cada herramienta. Las mismas fichas que
    # presenta Blender al llegar a la práctica.
    "herramienta_blender",
    "blender_mapa",
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

PATRON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PATRON_HUECO = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)

MAX_ID = 50
MAX_TITULO = 200
MAX_DESCRIPCION = 1000
MAX_INSIGNIA = 80
MAX_REEMPLAZA = 250
MAX_OBJETIVO = 300
MAX_HABILIDADES = 8
PATRON_VERSION_BLENDER = re.compile(r"^\d+\.\d+(\.\d+)?$")

_FALTA = object()

PATRON_PRACTICA = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
VISTAS_TALLER = ("taller", "proyecto")
ETAPAS_TALLER = ("exploracion", "practica")
MAX_HABILIDADES_EXPLORACION = 4
MODOS_MAPA = ("explore", "find")
MAX_PREGUNTAS_MAPA = 6

TECLAS_CON_NOMBRE = ("Tab", "Espacio", "Esc", "Enter", "Delete", "Backspace", "F2", "F3", "F12", "Home",
                     "Plus", "Minus", "↑", "↓", "←", "→")
MAX_EJERCICIOS_TECLAS = 8
PROPIEDADES_TRANSFORMAR = ("location", "rotation", "scale")
EJES_TRANSFORMAR = ("X", "Y", "Z")


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
    return (isinstance(valor, (int, float)) and not isinstance(valor, bool)
            and not math.isnan(valor) and not math.isinf(valor))


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
