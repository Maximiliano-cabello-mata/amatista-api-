"""Validación de bloques asociados al add-on y herramientas de Blender."""
from typing import Callable, Dict

from .comun import (
    ETAPAS_TALLER,
    MAX_HABILIDADES_EXPLORACION,
    MAX_PREGUNTAS_MAPA,
    MODOS_MAPA,
    PATRON_PRACTICA,
    VISTAS_TALLER,
    _Contexto,
    _FALTA,
    _valor,
)


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


def _fichas():
    from contenido.taller import fichas

    return fichas()


def _herramienta_blender(c: _Contexto, b: dict) -> None:
    herramienta = c.texto(b, "tool", maximo=40)
    if herramienta is not None:
        f = _fichas()
        if len(f) and herramienta not in f:
            c.error(f"«tool» = «{herramienta}» no tiene ficha en practices/blender/fichas.json (hay: {', '.join(f.ids())})")
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=600)
    c.booleano(b, "check")  # por defecto sí: encontrarla en el mapa de Blender para darla por vista


def _blender_mapa(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", obligatorio=False, maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=600)
    modo = c.opcion(b, "mode", MODOS_MAPA)
    f = _fichas()
    zonas = {z.id for z in f.zonas()}
    lista = _valor(b, "zones")
    if lista is not _FALTA:
        if modo != "explore":
            c.error("«zones» solo va con «mode»: «explore»")
        elif not isinstance(lista, list) or not 2 <= len(lista) <= max(2, len(zonas) or 9) or \
                not all(isinstance(z, str) for z in lista):
            c.error("«zones» debe ser una lista de 2 o más zonas")
        else:
            for z in lista:
                if zonas and z not in zonas:
                    c.error(f"«zones»: «{z}» no es una zona de Blender (hay: {', '.join(sorted(zonas))})")
            if len(set(lista)) != len(lista):
                c.error("«zones» tiene zonas repetidas")
    if modo == "find":
        for i, pregunta in c.objetos(b, "questions", 1, MAX_PREGUNTAS_MAPA):
            p = f"questions[{i}]."
            herramienta = _valor(pregunta, "tool")
            if herramienta is not _FALTA:
                if not isinstance(herramienta, str) or (len(f) and herramienta not in f):
                    c.error(f"«{p}tool» = «{herramienta}» no tiene ficha")
                if _valor(pregunta, "zone") is not _FALTA:
                    c.error(f"«{p}» lleva «tool» o «zone», no los dos")
                continue
            zona = c.texto(pregunta, "zone", p, maximo=40)
            if zona is not None and zonas and zona not in zonas:
                c.error(f"«{p}zone» = «{zona}» no es una zona de Blender")
            c.texto(pregunta, "prompt", p, maximo=200)
    elif _valor(b, "questions") is not _FALTA:
        c.error("«questions» solo va con «mode»: «find»")


def _blender_tour(c: _Contexto, b: dict) -> None:
    c.texto(b, "title", maximo=200)
    c.texto(b, "text", obligatorio=False, maximo=2000)
    c.booleano(b, "allowManual")


REVISORES_BLENDER: Dict[str, Callable[[_Contexto, dict], None]] = {
    "blender_practice": _blender_practice,
    "blender_taller": _blender_taller,
    "herramienta_blender": _herramienta_blender,
    "blender_mapa": _blender_mapa,
    "blender_tour": _blender_tour,
}
