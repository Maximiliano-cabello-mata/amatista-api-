"""Validación de bloques 3D (malla, nodos, motor web, simulador de viewport y keyframes)."""
from typing import Any, Callable, Dict, List, Optional, Tuple

from .comun import (
    CLASES_NODO,
    CONECTORES_NODO,
    CONTROL_POR_PARAMETRO,
    ENTORNOS_MOTOR,
    FORMAS_MALLA,
    FORMAS_MOTOR,
    MAX_CARAS_MALLA,
    MAX_ENLACES,
    MAX_NODOS,
    MAX_VERTICES_MALLA,
    MISIONES_VISTA,
    MODELOS_MOTOR,
    MODOS_MALLA,
    OPERADORES_META,
    PARAMETROS_ESCENA,
    PATRONES_MOTOR,
    PRIMITIVAS,
    RETOS_MOTOR,
    TIPOS_CONTROL,
    _Contexto,
    _FALTA,
    _es_entero,
    _es_numero,
    _valor,
)


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


def _axis_gizmo(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "caption", obligatorio=False)


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


REVISORES_3D: Dict[str, Callable[[_Contexto, dict], None]] = {
    "mesh_viewer": _mesh_viewer,
    "node_graph": _node_graph,
    "axis_gizmo": _axis_gizmo,
    "scene_explorer": _scene_explorer,
    "viewport_sim": _viewport_sim,
    "keyframe_sim": _keyframe_sim,
    "motor_scene": _motor_scene,
}
