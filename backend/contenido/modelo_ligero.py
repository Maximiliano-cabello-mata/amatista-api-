"""Modelo ligero de una entrega (formato amatista.modelo/1).

El add-on no sube el .blend: describe la escena en un JSON compacto (cada
malla en triángulos, con sus vértices cuantizados a enteros de 16 bits),
lo comprime con zlib y lo manda en base64. Una práctica low poly pesa de 2
a 40 KB. El panel del profesor lo descomprime y lo vuelve a armar en su
navegador (frontend/src/profesor/modelo.js); el servidor solo lo revisa y
lo guarda de paso, hasta que el profesor lo recoge.

    {
      "formato": "amatista.modelo/1",
      "blender": "4.2.3",
      "centro": [x, y, z], "escala": 2.5,      # vértice real = centro + v / 32767 * escala
      "materiales": [{"nombre": "Madera", "color": [0.6, 0.4, 0.2]}],
      "objetos": [
        {"nombre": "Mesa", "tipo": "MESH", "material": 0, "v": [...], "t": [...],
         "p": [x, y, z], "r": [x, y, z], "s": [x, y, z], "mods": ["BEVEL"]},
        {"nombre": "Luz", "tipo": "LIGHT", "p": [x, y, z]}
      ],
      "resumido": false                        # true si el add-on recortó mallas enormes
    }

Las coordenadas son las de Blender (Z hacia arriba) y ya traen la posición
del objeto aplicada (espacio del mundo, con modificadores evaluados).
"""
from __future__ import annotations

import base64
import binascii
import json
import zlib

FORMATO = "amatista.modelo/1"
MAX_BASE64 = 1_400_000  # ~1 MB comprimido
MAX_DESCOMPRIMIDO = 12 * 1024 * 1024
MAX_OBJETOS = 400
MAX_MATERIALES = 64
MAX_TRIANGULOS = 150_000
COORDENADA = 32767
TIPOS = ("MESH", "LIGHT", "CAMERA", "EMPTY", "CURVE", "OTRO")


class ModeloInvalido(ValueError):
    pass


def _numeros(valor, cantidad: int) -> bool:
    return isinstance(valor, list) and len(valor) == cantidad and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) for x in valor
    )


def descomprimir(paquete: str) -> dict:
    if not isinstance(paquete, str) or not paquete:
        raise ModeloInvalido("El modelo llegó vacío.")
    if len(paquete) > MAX_BASE64:
        raise ModeloInvalido("El modelo pesa demasiado (más de 1 MB comprimido). Simplifica la escena y vuelve a entregar.")
    try:
        crudo = base64.b64decode(paquete, validate=True)
    except (binascii.Error, ValueError):
        raise ModeloInvalido("El modelo no viene en base64.")
    descompresor = zlib.decompressobj()
    try:
        texto = descompresor.decompress(crudo, MAX_DESCOMPRIMIDO)
    except zlib.error:
        raise ModeloInvalido("El modelo no está comprimido con zlib.")
    if descompresor.unconsumed_tail:
        raise ModeloInvalido("El modelo descomprimido es demasiado grande.")
    try:
        datos = json.loads(texto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ModeloInvalido("El modelo no es un JSON válido.")
    if not isinstance(datos, dict):
        raise ModeloInvalido("El modelo no es un JSON válido.")
    return datos


def revisar(datos: dict) -> dict:
    """Revisa la forma del modelo y devuelve su resumen (unos cientos de bytes)."""
    if datos.get("formato") != FORMATO:
        raise ModeloInvalido(f"Formato desconocido: se esperaba {FORMATO}.")
    if not _numeros(datos.get("centro"), 3) or not isinstance(datos.get("escala"), (int, float)) or datos["escala"] <= 0:
        raise ModeloInvalido("Al modelo le falta su centro o su escala.")
    materiales = datos.get("materiales") or []
    if not isinstance(materiales, list) or len(materiales) > MAX_MATERIALES:
        raise ModeloInvalido(f"El modelo tiene más de {MAX_MATERIALES} materiales.")
    for material in materiales:
        if not isinstance(material, dict) or not _numeros(material.get("color"), 3):
            raise ModeloInvalido("Un material no trae su color.")
    objetos = datos.get("objetos")
    if not isinstance(objetos, list) or not objetos:
        raise ModeloInvalido("El modelo no tiene objetos.")
    if len(objetos) > MAX_OBJETOS:
        raise ModeloInvalido(f"El modelo tiene más de {MAX_OBJETOS} objetos.")
    vertices = triangulos = 0
    nombres = []
    for objeto in objetos:
        if not isinstance(objeto, dict) or not isinstance(objeto.get("nombre"), str) or objeto.get("tipo") not in TIPOS:
            raise ModeloInvalido("Un objeto no trae su nombre o su tipo.")
        material = objeto.get("material", -1)
        if not isinstance(material, int) or not -1 <= material < len(materiales):
            raise ModeloInvalido(f"«{objeto['nombre'][:40]}» usa un material que no existe.")
        v, t = objeto.get("v"), objeto.get("t")
        if v is not None or t is not None:
            if not isinstance(v, list) or not isinstance(t, list) or len(v) % 3 or len(t) % 3:
                raise ModeloInvalido(f"La malla de «{objeto['nombre'][:40]}» está incompleta.")
            if not all(isinstance(x, int) and -COORDENADA <= x <= COORDENADA for x in v):
                raise ModeloInvalido(f"La malla de «{objeto['nombre'][:40]}» tiene coordenadas fuera de rango.")
            cuantos = len(v) // 3
            if not all(isinstance(i, int) and 0 <= i < cuantos for i in t):
                raise ModeloInvalido(f"La malla de «{objeto['nombre'][:40]}» apunta a vértices que no existen.")
            vertices += cuantos
            triangulos += len(t) // 3
            if triangulos > MAX_TRIANGULOS:
                raise ModeloInvalido(f"El modelo tiene más de {MAX_TRIANGULOS} triángulos.")
        if len(nombres) < 20:
            nombres.append(objeto["nombre"][:40])
    resumen = {
        "objetos": len(objetos),
        "vertices": vertices,
        "triangulos": triangulos,
        "materiales": len(materiales),
        "nombres": nombres,
        "resumido": bool(datos.get("resumido")),
    }
    if isinstance(datos.get("blender"), str):
        resumen["blender"] = datos["blender"][:20]
    return resumen


def resumen_de(paquete: str) -> dict:
    return revisar(descomprimir(paquete))


def empaquetar(datos: dict) -> str:
    """Lo contrario de descomprimir (lo usan las pruebas; el add-on hace lo mismo)."""
    texto = json.dumps(datos, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(zlib.compress(texto, 9)).decode("ascii")
