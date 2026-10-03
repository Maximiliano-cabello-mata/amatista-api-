"""La FÓRMULA AMATISTA ("Ciclo del Cristal") y plantillas de contenido.

- FORMULA: los 5 pasos de cada módulo (gancho, explora, practica, reto, jefe).
- EJEMPLOS_BLOQUES: un bloque válido de cada tipo (paleta del editor).
- leccion_plantilla(paso): una lección de ejemplo por paso, con bloques
  interactivos. Todo el texto dice "Reemplaza este contenido".
- generar_esqueleto(): las lecciones borrador de un módulo nuevo.
- CURSOS_BASE: los cursos de frontend/src/data/cursos.js (para importar).
- NIVELES_BLENDER, ESTRUCTURA_LECCION, leccion_estructurada(): la
  reestructuración v3 (cinco niveles y lección de 10 pasos con ficha).

Todas las plantillas pasan contenido.validacion (lo comprueban las pruebas).
"""
import copy
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from contenido.validacion import PASOS_FORMULA

REEMPLAZA = "Reemplaza este contenido"

FORMULA = [
    {
        "paso": "gancho",
        "nombre": "Gancho",
        "descripcion": "Una pregunta o un reto visual que despierta la curiosidad antes de explicar nada.",
        "duracion": "2–4 min",
        "minutos": [2, 4],
        "bloques": ["hotspots", "quiz_inline", "image"],
    },
    {
        "paso": "explora",
        "nombre": "Explora",
        "descripcion": "Descubrir el concepto manipulándolo: mover, voltear y relacionar antes de leer la definición.",
        "duracion": "5–8 min",
        "minutos": [5, 8],
        "bloques": ["scene_explorer", "concept_cards", "matching"],
    },
    {
        "paso": "practica",
        "nombre": "Práctica",
        "descripcion": "Aplicar lo aprendido con guía y retroalimentación inmediata.",
        "duracion": "6–10 min",
        "minutos": [6, 10],
        "bloques": ["ordering", "fill_blanks", "code_challenge"],
    },
    {
        "paso": "reto",
        "nombre": "Reto",
        "descripcion": (
            "Mini proyecto propio con un criterio de logro claro: un code_challenge abierto o "
            "instrucciones de Blender con evidencia."
        ),
        "duracion": "8–15 min",
        "minutos": [8, 15],
        "bloques": ["code_challenge", "callout", "ordering"],
    },
    {
        "paso": "jefe",
        "nombre": "Jefe final",
        "descripcion": "Examen del módulo (se aprueba con 80) que desbloquea la insignia.",
        "duracion": "5–10 min",
        "minutos": [5, 10],
        "bloques": ["exam"],
    },
]

RITMO = [
    "Una idea por lección y como máximo 10 minutos.",
    "Una interacción cada ~2 bloques de texto.",
    "XP: 100 por lección, más el puntaje del examen y 10 por actividad perfecta.",
]

# --- Un bloque de ejemplo por tipo (paleta del editor) ------------------------

ESCENA_INICIAL = (
    "<a-scene>\n"
    '  <a-box position="0 1 -3" color="gray"></a-box>\n'
    '  <a-sky color="#1a1a2e"></a-sky>\n'
    "</a-scene>"
)

EJEMPLOS_BLOQUES: Dict[str, dict] = {
    "markdown_text": {
        "type": "markdown_text",
        "body": (
            f"### {REEMPLAZA}\nExplica **una sola idea** con frases cortas. "
            "Usa `código` para etiquetas o atajos de teclado."
        ),
    },
    "image": {
        "type": "image",
        "src": "ilustraciones/concepto-malla.svg",
        "alt": f"{REEMPLAZA}: describe la imagen para quien no puede verla.",
        "caption": f"{REEMPLAZA}: pie de imagen (opcional).",
    },
    "concept_cards": {
        "type": "concept_cards",
        "items": [
            {
                "term": "Vértice",
                "definition": f"{REEMPLAZA}: un punto en el espacio 3D. Es la pieza más pequeña de una malla.",
                "image": "ilustraciones/concepto-malla.svg",
                "alt": "Una esfera low poly con sus puntos resaltados.",
            },
            {
                "term": "Cara",
                "definition": f"{REEMPLAZA}: la superficie plana que se forma entre tres o más vértices.",
            },
        ],
    },
    "timeline": {
        "type": "timeline",
        "title": f"{REEMPLAZA}: la historia en dos momentos",
        "items": [
            {"year": "1995", "icon": "estudio", "title": "Primer momento", "text": f"{REEMPLAZA}."},
            {"year": "2002", "icon": "libre", "title": "Segundo momento", "text": f"{REEMPLAZA}."},
        ],
    },
    "pipeline": {
        "type": "pipeline",
        "title": f"{REEMPLAZA}: el flujo de trabajo",
        "steps": [
            {"icon": "modelar", "title": "Modelar", "text": "Dar forma a la malla."},
            {"icon": "texturizar", "title": "Texturizar", "text": "Darle color y material."},
            {"icon": "render", "title": "Mostrar", "text": "Verlo en la web con A-Frame."},
        ],
    },
    "layers": {
        "type": "layers",
        "title": f"{REEMPLAZA}: ¿qué pasa por dentro?",
        "items": [
            {"title": "Tu HTML", "text": "Las etiquetas <a-scene>, <a-box>…"},
            {"title": "A-Frame", "text": "Convierte las etiquetas en objetos 3D."},
        ],
        "footer": f"{REEMPLAZA} (opcional).",
    },
    "callout": {
        "type": "callout",
        "variant": "dato",
        "title": "¿Sabías que…?",
        "body": f"{REEMPLAZA}: un dato curioso en una o dos frases.",
    },
    "code_snippet": {
        "type": "code_snippet",
        "language": "html",
        "code": ESCENA_INICIAL,
        "preview": True,
    },
    "video_player": {"type": "video_player", "url": "videos/reemplaza-este-video.mp4"},
    "quiz_inline": {
        "type": "quiz_inline",
        "id": "quiz_1",
        "question": f"{REEMPLAZA}: ¿de qué está hecha cualquier figura 3D?",
        "options": [
            {"id": "opt_a", "text": "De píxeles sueltos.", "isCorrect": False},
            {"id": "opt_b", "text": "De vértices, aristas y caras.", "isCorrect": True},
            {"id": "opt_c", "text": "De fotografías.", "isCorrect": False},
        ],
        "explanation": "Toda malla 3D se arma con vértices unidos por aristas que forman caras.",
    },
    "ordering": {
        "type": "ordering",
        "id": "orden_1",
        "prompt": f"{REEMPLAZA}: ordena los pasos para llevar un modelo a la web.",
        "items": [
            {"id": "paso_1", "text": "Modelar en Blender"},
            {"id": "paso_2", "text": "Pintar materiales"},
            {"id": "paso_3", "text": "Exportar en GLB"},
            {"id": "paso_4", "text": "Mostrarlo con A-Frame"},
        ],
        "explanation": "Primero la forma, luego el color, después el archivo y al final la web.",
    },
    "matching": {
        "type": "matching",
        "id": "parejas_1",
        "prompt": f"{REEMPLAZA}: une cada término con su significado.",
        "pairs": [
            {"id": "par_1", "left": "Vértice", "right": "Un punto en el espacio"},
            {"id": "par_2", "left": "Arista", "right": "La línea entre dos vértices"},
            {"id": "par_3", "left": "Cara", "right": "La superficie entre aristas"},
        ],
        "explanation": "Vértices, aristas y caras forman la malla.",
    },
    "fill_blanks": {
        "type": "fill_blanks",
        "id": "huecos_1",
        "prompt": f"{REEMPLAZA}: completa el código para crear una caja roja.",
        "template": '<a-[[box]] color="[[red|#ff0000|#f00]]"></a-box>',
        "code": True,
        "explanation": "La primitiva de caja es <a-box> y el color se escribe en el atributo color.",
    },
    "hotspots": {
        "type": "hotspots",
        "id": "puntos_1",
        "src": "ilustraciones/concepto-malla.svg",
        "alt": "Una esfera low poly: la mitad es alambre y la otra mitad tiene caras.",
        "points": [
            {"id": "punto_1", "x": 30, "y": 40, "title": "Vértice", "text": f"{REEMPLAZA}: aquí se unen las aristas."},
            {"id": "punto_2", "x": 55, "y": 30, "title": "Arista", "text": f"{REEMPLAZA}: la línea entre dos vértices."},
            {"id": "punto_3", "x": 70, "y": 60, "title": "Cara", "text": f"{REEMPLAZA}: la superficie que se pinta."},
        ],
    },
    "scene_explorer": {
        "type": "scene_explorer",
        "id": "escena_1",
        "title": f"{REEMPLAZA}: juega con la esfera",
        "primitive": "sphere",
        "controls": [
            {"param": "segments", "label": "Segmentos", "type": "range", "min": 3, "max": 32, "step": 1, "default": 24},
            {"param": "wireframe", "label": "Ver la malla", "type": "toggle", "default": True},
            {"param": "color", "label": "Color", "type": "color", "default": "#9b5de5"},
        ],
        "goal": {"param": "segments", "op": "<=", "value": 8, "text": "Baja los segmentos a 8 o menos: así se ve low poly."},
    },
    "code_challenge": {
        "type": "code_challenge",
        "id": "codigo_1",
        "prompt": f"{REEMPLAZA}: cambia la caja gris por una caja roja.",
        "language": "html",
        "starter": ESCENA_INICIAL,
        "checks": [
            {"selector": "a-box", "min": 1, "text": "Hay al menos una caja <a-box>."},
            {"selector": "a-box", "attr": "color", "equals": "red", "text": 'La caja es roja (color="red").'},
        ],
        "solution": ESCENA_INICIAL.replace('color="gray"', 'color="red"'),
        "preview": True,
    },
}


def ejemplo_bloque(tipo: str) -> dict:
    """Copia independiente del bloque de ejemplo (se puede modificar)."""
    return copy.deepcopy(EJEMPLOS_BLOQUES[tipo])


def _bloque(tipo: str, **cambios) -> dict:
    bloque = ejemplo_bloque(tipo)
    bloque.update(cambios)
    return bloque


# --- Una lección de ejemplo por paso de la fórmula ----------------------------


def _gancho(curso_id: Optional[str]) -> Tuple[str, int, List[dict]]:
    return "theory_interactive", 180, [
        _bloque(
            "markdown_text",
            body=(
                f"### {REEMPLAZA}\nAbre con una pregunta que intrigue: *¿cómo dibuja un videojuego "
                "un mundo entero 60 veces por segundo?* Todavía no la respondas."
            ),
        ),
        _bloque("hotspots"),
        _bloque("quiz_inline"),
    ]


def _explora(curso_id: Optional[str]) -> Tuple[str, int, List[dict]]:
    return "theory_interactive", 390, [
        _bloque("markdown_text", body=f"### {REEMPLAZA}\nInvita a descubrir el concepto moviendo los controles."),
        _bloque("scene_explorer"),
        _bloque("concept_cards"),
        _bloque("matching"),
    ]


def _practica(curso_id: Optional[str]) -> Tuple[str, int, List[dict]]:
    if curso_id == "blender":
        return "theory_interactive", 480, [
            _bloque("markdown_text", body=f"### {REEMPLAZA}\nRepasa el paso a paso antes de abrir Blender."),
            _bloque("ordering"),
            _bloque(
                "fill_blanks",
                prompt=f"{REEMPLAZA}: completa los atajos de Blender.",
                template="Para mover un objeto pulsa [[G]], para rotarlo [[R]] y para escalarlo [[S]].",
                code=False,
                explanation="G de grab (mover), R de rotate (rotar) y S de scale (escalar).",
            ),
            _bloque("quiz_inline", id="quiz_2"),
        ]
    return "code_interactive", 480, [
        _bloque("markdown_text", body=f"### {REEMPLAZA}\nGuía al alumno: primero ordena, luego completa y al final programa."),
        _bloque("ordering"),
        _bloque("fill_blanks"),
        _bloque("code_challenge"),
    ]


def _reto(curso_id: Optional[str]) -> Tuple[str, int, List[dict]]:
    if curso_id == "blender":
        return "theory_interactive", 720, [
            _bloque(
                "markdown_text",
                body=(
                    f"### {REEMPLAZA}\nTu reto: modela un objeto low poly propio.\n"
                    "- Usa como máximo 300 caras.\n- Dale al menos dos materiales.\n- Expórtalo en GLB."
                ),
            ),
            _bloque(
                "callout",
                variant="reto",
                title="Criterio de logro",
                body=f"{REEMPLAZA}: el modelo se abre en el visor GLB y se reconoce qué es.",
            ),
            _bloque(
                "ordering",
                id="evidencia_1",
                prompt=f"{REEMPLAZA}: ordena lo que hiciste para cumplir el reto.",
            ),
            _bloque(
                "quiz_inline",
                id="evidencia_2",
                question="¿En qué formato exportaste tu modelo para la web?",
                options=[
                    {"id": "opt_a", "text": ".blend", "isCorrect": False},
                    {"id": "opt_b", "text": ".glb", "isCorrect": True},
                    {"id": "opt_c", "text": ".png", "isCorrect": False},
                ],
                explanation="GLB guarda malla, materiales y texturas en un solo archivo listo para la web.",
            ),
        ]
    return "code_interactive", 720, [
        _bloque(
            "markdown_text",
            body=(
                f"### {REEMPLAZA}\nTu reto: arma una escena propia.\n"
                "- Al menos tres figuras.\n- Un cielo con color.\n- Algo que se mueva."
            ),
        ),
        _bloque(
            "callout",
            variant="reto",
            title="Criterio de logro",
            body=f"{REEMPLAZA}: la escena cumple las tres condiciones y se ve en la vista previa.",
        ),
        _bloque(
            "code_challenge",
            id="reto_1",
            prompt=f"{REEMPLAZA}: crea tu escena. Usa las primitivas que quieras.",
            starter="<a-scene>\n  \n</a-scene>",
            checks=[
                {
                    "selector": "a-box, a-sphere, a-cylinder, a-cone, a-torus",
                    "min": 3,
                    "text": "Hay al menos tres figuras.",
                },
                {"selector": "a-sky", "min": 1, "text": "Hay un cielo <a-sky>."},
                {"selector": "[animation]", "min": 1, "text": "Algo se mueve (atributo animation)."},
            ],
            solution=(
                "<a-scene>\n"
                '  <a-box position="-1 0.5 -3" color="red"></a-box>\n'
                '  <a-sphere position="0 1.25 -5" radius="1.25" color="blue"\n'
                '    animation="property: rotation; to: 0 360 0; loop: true; dur: 4000"></a-sphere>\n'
                '  <a-cylinder position="1 0.75 -3" radius="0.5" height="1.5" color="yellow"></a-cylinder>\n'
                '  <a-sky color="#1a1a2e"></a-sky>\n'
                "</a-scene>"
            ),
        ),
    ]


def _pregunta(numero: int, texto: str, opciones: List[str], correcta: int) -> dict:
    letras = "abcde"
    return {
        "id": f"q_{numero:02d}",
        "questionText": f"{REEMPLAZA}: {texto}",
        "options": [
            {"id": f"opt_{letras[i]}", "text": opcion, "isCorrect": i == correcta} for i, opcion in enumerate(opciones)
        ],
        "feedbackCorrect": "¡Exacto!",
        "feedbackIncorrect": "Repasa la lección de Explora.",
    }


def _jefe(curso_id: Optional[str]) -> dict:
    return {
        "passingScore": 80,
        "questions": [
            _pregunta(1, "¿qué forma la superficie de una malla?", ["Las caras", "Los vértices", "La cámara"], 0),
            _pregunta(2, "¿qué significa low poly?", ["Muchos detalles", "Pocos polígonos", "Sin color"], 1),
            _pregunta(3, "¿qué formato usamos para la web?", [".blend", ".obj", ".glb"], 2),
        ],
    }


_PASOS = {"gancho": _gancho, "explora": _explora, "practica": _practica, "reto": _reto}


def leccion_plantilla(
    paso: str,
    curso_id: Optional[str] = None,
    leccion_id: Optional[str] = None,
    bloqueada: bool = True,
) -> dict:
    """Lección de ejemplo del paso de la fórmula (copia nueva en cada llamada).

    curso_id="blender" usa práctica y reto con instrucciones de Blender; los
    demás cursos, retos de código con A-Frame.
    """
    if paso not in PASOS_FORMULA:
        raise ValueError(f"Paso desconocido: {paso!r} (usa: {', '.join(PASOS_FORMULA)})")
    nombre = next(p["nombre"] for p in FORMULA if p["paso"] == paso)
    leccion = {
        "id": leccion_id or f"les_plantilla_{paso}",
        "title": f"{nombre}: reemplaza este título",
        "slug": f"plantilla-{paso}",
    }
    if paso == "jefe":
        leccion.update(
            {"type": "exam", "durationSeconds": 300, "isLocked": bloqueada, "formula": paso, "quizData": _jefe(curso_id)}
        )
        return leccion
    tipo, segundos, bloques = _PASOS[paso](curso_id)
    leccion.update(
        {"type": tipo, "durationSeconds": segundos, "isLocked": bloqueada, "formula": paso, "contentBlocks": bloques}
    )
    return leccion


LECCIONES_PLANTILLA: Dict[str, dict] = {paso: leccion_plantilla(paso) for paso in PASOS_FORMULA}

# --- Esqueleto de un módulo nuevo ---------------------------------------------

PATRON_NUMERADO = re.compile(r"^(.*?)(\d+)$")


def siguiente_numeracion(ids: Iterable[str], curso_id: str) -> Tuple[str, int, int]:
    """(prefijo, siguiente número, ancho) para ids nuevos de lecciones de un curso.

    Respeta la numeración que ya usa el curso (les_004 → les_005,
    les_af_003 → les_af_004). Sin lecciones: les_<curso>_001.
    """
    conteo: Counter = Counter()
    maximos: Dict[str, int] = {}
    anchos: Dict[str, int] = {}
    for leccion_id in ids:
        coincide = PATRON_NUMERADO.match(leccion_id or "")
        if not coincide:
            continue
        prefijo, digitos = coincide.groups()
        conteo[prefijo] += 1
        maximos[prefijo] = max(maximos.get(prefijo, 0), int(digitos))
        anchos[prefijo] = max(anchos.get(prefijo, 0), len(digitos))
    if not conteo:
        return f"les_{curso_id}_", 1, 3
    prefijo = conteo.most_common(1)[0][0]
    return prefijo, maximos[prefijo] + 1, anchos[prefijo]


def generar_esqueleto(
    curso_id: str,
    modulo_id: str,
    prefijo_ids: str,
    inicio: int = 1,
    ancho: int = 3,
) -> List[dict]:
    """Las 5 lecciones borrador de la fórmula (gancho, explora, practica, reto, jefe).

    Los ids son prefijo_ids + número consecutivo desde `inicio`
    (por ejemplo les_005 … les_009). Solo la primera queda desbloqueada.
    """
    lecciones = []
    for i, paso in enumerate(PASOS_FORMULA):
        leccion = leccion_plantilla(paso, curso_id, f"{prefijo_ids}{inicio + i:0{ancho}d}", bloqueada=i > 0)
        leccion["slug"] = f"{modulo_id}-{paso}".replace("_", "-").lower()
        lecciones.append(leccion)
    return lecciones


def minutos_estimados(lecciones: Iterable[dict]) -> int:
    segundos = sum(
        leccion.get("durationSeconds") or 0 for leccion in lecciones if isinstance(leccion.get("durationSeconds"), int)
    )
    return max(1, round(segundos / 60))


def modulo_esqueleto(
    curso_id: str,
    numero: int,
    titulo: str,
    insignia: Optional[str] = None,
    modulo_id: Optional[str] = None,
    ids_existentes: Iterable[str] = (),
    nivel_id: Optional[str] = None,
) -> dict:
    """Archivo de módulo nuevo ({"module": ...}) en borrador, armado con la fórmula."""
    modulo_id = modulo_id or f"mod_{curso_id}_{numero:03d}"
    prefijo, inicio, ancho = siguiente_numeracion(ids_existentes, curso_id)
    lecciones = generar_esqueleto(curso_id, modulo_id, prefijo, inicio, ancho)
    modulo = {
        "module": {
            "id": modulo_id,
            "title": f"Módulo {numero}: {titulo}",
            "description": f"{REEMPLAZA}: en dos frases, qué logrará el alumno al terminar el módulo.",
            "estimatedTimeMinutes": minutos_estimados(lecciones),
            "order": numero,
            "curso": curso_id,
            "insignia": insignia or f"Insignia del módulo {numero}",
            "estado": "borrador",
            "lessons": lecciones,
        }
    }
    if nivel_id:
        modulo["module"]["nivel"] = nivel_id
    return modulo


# --- Cursos de frontend/src/data/cursos.js ------------------------------------
# Copia de los textos del catálogo empaquetado: la CLI "importar" crea estos
# cursos en la base si faltan (no sobrescribe lo que se editó en el panel).

CURSOS_BASE: Dict[str, dict] = {
    "blender": {
        "id": "blender",
        "numero": "01",
        "titulo": "Blender",
        "subtitulo": "Modelado 3D low poly",
        "descripcion": (
            "Descubre el mundo 3D, modela objetos low poly, dales color y expórtalos en formato GLB "
            "listos para la web."
        ),
        "nivel": "Principiante",
        "acento": "blender",
        "recurso_texto": "Descarga Blender gratis",
        "recurso_url": "https://www.blender.org/download/",
        "orden": 1,
    },
    "aframe": {
        "id": "aframe",
        "numero": "02",
        "titulo": "A-Frame",
        "subtitulo": "Mundos WebXR en el navegador",
        "descripcion": (
            "Lleva tus modelos a la web: crea escenas 3D con HTML, agrega interacción y visítalas en "
            "realidad virtual o aumentada."
        ),
        "nivel": "Intermedio",
        "acento": "neon",
        "recurso_texto": "Documentación de A-Frame",
        "recurso_url": "https://aframe.io/docs/",
        "orden": 2,
    },
}


# --- Reestructuración v3: niveles y estructura mínima de lección --------------
# Fuente: docs/propuestas/2026-10-03_propuesta_contenido_blender.txt (secciones 3
# y 6). La CLI "sembrar-niveles" crea estos niveles en la base si faltan (no
# sobrescribe lo que se editó después en el panel o en Oracle).

NIVELES_BLENDER: List[dict] = [
    {
        "id": "blender-n1",
        "numero": 1,
        "rama": None,
        "titulo": "Desde cero",
        "perfil": "Nunca ha utilizado Blender o se pierde al abrirlo.",
        "proyecto": "Habitación sencilla construida con primitivas.",
        "criterio_salida": (
            "Organiza una escena simple y conserva su trabajo, explicando qué transforma al mover la vista "
            "y qué transforma al mover un objeto."
        ),
    },
    {
        "id": "blender-n2",
        "numero": 2,
        "rama": None,
        "titulo": "Básico con conocimientos previos",
        "perfil": "Conoce herramientas, pero tiene vacíos o poca consistencia.",
        "proyecto": "Mesa con una lámpara estilizada o un objeto de complejidad equivalente.",
        "criterio_salida": "Construye un objeto sencillo y adapta su forma sin copiar cada acción de un tutorial.",
    },
    {
        "id": "blender-n3",
        "numero": 3,
        "rama": None,
        "titulo": "Consolidación y autonomía",
        "perfil": "Puede seguir tutoriales, pero se bloquea al comenzar por su cuenta.",
        "proyecto": "Rincón de estudio propio a partir de una referencia o boceto.",
        "criterio_salida": (
            "Termina una escena pequeña con ayuda limitada y justifica al menos una decisión de modelado "
            "y una corrección."
        ),
    },
    {
        "id": "blender-n4",
        "numero": 4,
        "rama": None,
        "titulo": "Intermedio",
        "perfil": "Termina proyectos pequeños y necesita un flujo más consistente.",
        "proyecto": "Conjunto de objetos coherentes presentado en una escena (y en el laboratorio web cuando exista).",
        "criterio_salida": "Resuelve un proyecto completo, documenta su entrega y corrige problemas identificados al revisarlo.",
    },
    {
        "id": "blender-n5-web",
        "numero": 5,
        "rama": "web",
        "titulo": "Avanzado: modelos y escenas para web y videojuegos",
        "perfil": "Domina la base y quiere llevar sus modelos a la web y a los videojuegos.",
        "proyecto": "Por definir con la rama (decisión pendiente, sección 13).",
        "criterio_salida": None,
    },
    {
        "id": "blender-n5-animacion",
        "numero": 5,
        "rama": "animacion",
        "titulo": "Avanzado: animación y rigging",
        "perfil": "Domina la base y quiere animar personajes y objetos.",
        "proyecto": "Por definir con la rama (decisión pendiente, sección 13).",
        "criterio_salida": None,
    },
    {
        "id": "blender-n5-producto",
        "numero": 5,
        "rama": "producto",
        "titulo": "Avanzado: visualización de productos",
        "perfil": "Domina la base y quiere presentar productos.",
        "proyecto": "Por definir con la rama (decisión pendiente, sección 13).",
        "criterio_salida": None,
    },
    {
        "id": "blender-n5-procedural",
        "numero": 5,
        "rama": "procedural",
        "titulo": "Avanzado: procedimientos y automatización",
        "perfil": "Domina la base y quiere automatizar con nodos y scripts (ampliación posterior).",
        "proyecto": "Por definir con la rama (decisión pendiente, sección 13).",
        "criterio_salida": None,
    },
]

# Estructura mínima de cada lección (sección 6). 006_herramientas_autor.sql
# arma el mismo esqueleto desde Oracle; tests/test_niveles.py comprueba que
# los títulos coincidan.
ESTRUCTURA_LECCION: List[Tuple[str, str, str]] = [
    ("callout", "Objetivo", "Qué podrá hacer el alumno al terminar, en una frase observable."),
    ("markdown_text", "Antes de empezar", "Prerrequisitos y versión de Blender en la que se verificó la lección."),
    ("markdown_text", "Resultado esperado", "Describe la imagen de referencia del resultado (agrega un bloque image)."),
    ("markdown_text", "Conceptos clave", "Explicación breve de lo necesario, nada más."),
    ("markdown_text", "Práctica guiada", "Pasos numerados, con menús además de atajos."),
    ("callout", "Tu variante", "Reto de transferencia: cambia algo manteniendo el objetivo."),
    ("markdown_text", "Errores frecuentes y pistas", "Problema → pista opcional → cómo recuperarse."),
    ("markdown_text", "Comprueba tu trabajo", "Lista de criterios de comprobación (los mismos de la ficha)."),
    ("markdown_text", "Guarda tu evidencia", "Qué guardar: archivo editable, captura y variación."),
    ("markdown_text", "Repaso", "Dónde se vuelve a usar esta habilidad más adelante."),
]


def ficha_vacia(objetivo: str = "") -> dict:
    """Ficha de lección para llenar: objetivo, habilidades, versión verificada...

    El nivel no va en la ficha: lo da el módulo (MODULOS.NIVEL_ID).
    """
    return {
        "objetivo": objetivo,
        "habilidades": [],
        "prerrequisitos": [],
        "blender": {"verificadaEn": None, "notas": ""},
        "edicion": "1.0",
        "practica": {"archivo": "", "evidencia": "Archivo editable y captura del resultado"},
        "comprobacion": [],
        "offline": False,
    }


def leccion_estructurada(
    leccion_id: str,
    titulo: str,
    objetivo: str = "",
    bloqueada: bool = True,
) -> dict:
    """Lección borrador con los 10 pasos de la estructura mínima y su ficha."""
    bloques = []
    for tipo, encabezado, guia in ESTRUCTURA_LECCION:
        if tipo == "callout":
            variante = "reto" if encabezado == "Tu variante" else "dato"
            texto = objetivo if encabezado == "Objetivo" and objetivo else f"{REEMPLAZA}: {guia}"
            bloques.append({"type": "callout", "variant": variante, "title": encabezado, "body": texto})
        else:
            bloques.append({"type": "markdown_text", "body": f"### {encabezado}\n{REEMPLAZA}: {guia}"})
    return {
        "id": leccion_id,
        "title": titulo,
        "type": "theory_reading",
        "durationSeconds": 900,
        "isLocked": bloqueada,
        "ficha": ficha_vacia(objetivo),
        "contentBlocks": bloques,
    }
