"""Validación de lecciones, exámenes, fichas y módulos didácticos."""
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from .bloques_3d import REVISORES_3D
from .bloques_basicos import REVISORES_BASICOS
from .bloques_blender import REVISORES_BLENDER
from .bloques_interactivos import REVISORES_INTERACTIVOS, _opciones
from .comun import (
    BLOQUES_INTERACTIVOS,
    ESTADOS_MODULO,
    ETAPAS_TALLER,
    MAX_DESCRIPCION,
    MAX_HABILIDADES,
    MAX_ID,
    MAX_INSIGNIA,
    MAX_OBJETIVO,
    MAX_REEMPLAZA,
    MAX_TITULO,
    PASOS_FORMULA,
    PATRON_ID,
    PATRON_VERSION_BLENDER,
    TIPOS_BLOQUE,
    TIPOS_LECCION,
    _Contexto,
    _FALTA,
    _unir,
    _valor,
)

REVISORES: Dict[str, Callable[[_Contexto, dict], None]] = {
    **REVISORES_BASICOS,
    **REVISORES_INTERACTIVOS,
    **REVISORES_3D,
    **REVISORES_BLENDER,
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
