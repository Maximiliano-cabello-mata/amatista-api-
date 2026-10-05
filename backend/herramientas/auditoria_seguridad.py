"""Auditoría de seguridad de la API: inyección SQL, permisos y cabeceras.

Recorre TODAS las rutas que publica la API (su esquema OpenAPI) y las ataca
con cargas típicas de inyección SQL (genéricas y propias de Oracle), XSS y
recorrido de carpetas, en la ruta, en la consulta y en cada campo del cuerpo.
Después revisa la matriz de permisos (qué ruta pide sesión y cuál pide rol)
leyendo las dependencias reales de FastAPI, y las cabeceras de seguridad.

Se usa de dos formas:

1. En las pruebas (tests/test_seguridad.py) contra SQLite, en cada push.
2. Contra una API real (por ejemplo la del contenedor de Oracle) para el
   informe:

       python herramientas/auditoria_seguridad.py --url http://localhost:8000 \\
           --token-admin <token> --token-alumno <token> --salida informe.json

Nunca la corras contra producción sin avisar: manda cientos de peticiones
con cargas de ataque (no borran nada si la API está sana, pero llenan el log).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# --- Cargas de ataque --------------------------------------------------------

INYECCION_SQL = [
    "' OR '1'='1",
    "' OR 1=1 --",
    "admin'--",
    "\" OR \"\"=\"",
    "1 OR 1=1",
    "'; DROP TABLE USUARIOS; --",
    "' UNION SELECT NULL, NULL, NULL FROM DUAL --",
    "' || (SELECT USER FROM DUAL) || '",
    "') OR ('a'='a",
    "1; SELECT SYS_CONTEXT('USERENV','CURRENT_SCHEMA') FROM DUAL",
    "%27%20OR%201%3D1--",
    "' AND 1=CAST((SELECT banner FROM v$version WHERE ROWNUM=1) AS INT) --",
]
# Inyección a ciegas por tiempo: si alguna llega a ejecutarse, la respuesta tarda.
INYECCION_TIEMPO = [
    "' AND 1=DBMS_PIPE.RECEIVE_MESSAGE('a',4) --",
    "'; BEGIN DBMS_SESSION.SLEEP(4); END; --",
    "' OR SLEEP(4) --",
]
XSS = ["<script>alert(1)</script>", "\"><img src=x onerror=alert(1)>", "javascript:alert(1)"]
RECORRIDO = ["../../../../etc/passwd", "..%2F..%2F..%2Fetc%2Fpasswd", "....//....//etc/passwd"]

TODAS = INYECCION_SQL + XSS + RECORRIDO

# Lo que una base de datos o Python dejan ver cuando algo se rompe.
FIRMAS_DE_ERROR = re.compile(
    r"ORA-\d{5}|PLS-\d{5}|sqlite3\.|OperationalError|ProgrammingError|SQLAlchemy|"
    r"syntax error|Traceback \(most recent call last\)|DPI-\d{4}|psycopg",
    re.IGNORECASE,
)
# Contenido de /etc/passwd: si aparece, una ruta leyó archivos del servidor.
FIRMA_PASSWD = re.compile(r"root:.*:0:0:")

CABECERAS_ESPERADAS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": None,
}


@dataclass
class Hallazgo:
    severidad: str  # critica | alta | media | baja | info
    ruta: str
    titulo: str
    detalle: str = ""


@dataclass
class Informe:
    peticiones: int = 0
    rutas: int = 0
    hallazgos: List[Hallazgo] = field(default_factory=list)
    permisos: List[Dict[str, Any]] = field(default_factory=list)
    tiempo_max_ms: float = 0.0
    segundos: float = 0.0

    def agregar(self, *args, **kwargs) -> None:
        self.hallazgos.append(Hallazgo(*args, **kwargs))

    @property
    def graves(self) -> List[Hallazgo]:
        return [h for h in self.hallazgos if h.severidad in ("critica", "alta")]

    def como_dict(self) -> Dict[str, Any]:
        return {
            "peticiones": self.peticiones,
            "rutas": self.rutas,
            "segundos": round(self.segundos, 2),
            "respuesta_mas_lenta_ms": round(self.tiempo_max_ms, 1),
            "hallazgos": [h.__dict__ for h in self.hallazgos],
            "permisos": self.permisos,
        }


# --- Esquema OpenAPI → peticiones ---------------------------------------------


def _resolver(esquema: Dict[str, Any], componentes: Dict[str, Any]) -> Dict[str, Any]:
    while "$ref" in esquema:
        esquema = componentes[esquema["$ref"].split("/")[-1]]
    if "anyOf" in esquema:  # Optional[X] → X
        opciones = [o for o in esquema["anyOf"] if o.get("type") != "null"]
        return _resolver(opciones[0], componentes) if opciones else {"type": "string"}
    if "allOf" in esquema and len(esquema["allOf"]) == 1:
        return _resolver(esquema["allOf"][0], componentes)
    return esquema


def ejemplo(esquema: Dict[str, Any], componentes: Dict[str, Any], carga: str, profundidad: int = 0) -> Any:
    """Un valor que respeta el tipo del campo y lleva `carga` en cada texto."""
    esquema = _resolver(esquema, componentes)
    tipo = esquema.get("type")
    if profundidad > 4:
        return None
    if tipo == "object" or "properties" in esquema:
        props = esquema.get("properties") or {}
        if not props:
            return {"clave": carga}
        return {k: ejemplo(v, componentes, carga, profundidad + 1) for k, v in props.items()}
    if tipo == "array":
        return [ejemplo(esquema.get("items") or {"type": "string"}, componentes, carga, profundidad + 1)]
    if tipo == "integer":
        return 1
    if tipo == "number":
        return 1.0
    if tipo == "boolean":
        return True
    if "enum" in esquema:
        return esquema["enum"][0]
    if esquema.get("format") == "email":
        return "prueba@amatista.local"
    return carga


@dataclass
class Operacion:
    metodo: str
    ruta: str
    parametros_ruta: List[str]
    parametros_consulta: List[str]
    cuerpo: Optional[Dict[str, Any]]


def operaciones(openapi: Dict[str, Any]) -> List[Operacion]:
    componentes = openapi.get("components", {}).get("schemas", {})
    lista = []
    for ruta, metodos in sorted(openapi["paths"].items()):
        for metodo, op in metodos.items():
            params = op.get("parameters", [])
            cuerpo = None
            contenido = (op.get("requestBody") or {}).get("content", {}).get("application/json")
            if contenido:
                cuerpo = _resolver(contenido["schema"], componentes)
            lista.append(
                Operacion(
                    metodo=metodo.upper(),
                    ruta=ruta,
                    parametros_ruta=[p["name"] for p in params if p["in"] == "path"],
                    parametros_consulta=[p["name"] for p in params if p["in"] == "query"],
                    cuerpo=cuerpo,
                )
            )
    return lista


# --- Ataques -------------------------------------------------------------------

# Rutas que cierran la sesión con la que se ataca: se prueban aparte.
CIERRAN_SESION = {"/api/auth/cerrar-sesion", "/api/auth/cerrar-todas", "/api/addon/v1/salir"}


def omitir_por_defecto(op: "Operacion") -> bool:
    return op.ruta in CIERRAN_SESION


def _url(op: Operacion, carga: str) -> str:
    url = op.ruta
    for nombre in op.parametros_ruta:
        url = url.replace("{" + nombre + "}", _codificar(carga))
    return url


def _codificar(texto: str) -> str:
    from urllib.parse import quote

    return quote(texto, safe="")


def _revisar_respuesta(informe: Informe, op: Operacion, carga: str, respuesta, ms: float, lugar: str) -> None:
    informe.peticiones += 1
    informe.tiempo_max_ms = max(informe.tiempo_max_ms, ms)
    texto = respuesta.text if hasattr(respuesta, "text") else ""
    nombre = f"{op.metodo} {op.ruta}"
    if respuesta.status_code >= 500:
        informe.agregar("alta", nombre, f"Error 500 con una carga de ataque en {lugar}", f"carga={carga!r}")
    if FIRMAS_DE_ERROR.search(texto or ""):
        informe.agregar(
            "alta", nombre, f"La respuesta deja ver un error interno de la base ({lugar})",
            f"carga={carga!r}: {FIRMAS_DE_ERROR.search(texto).group(0)}",
        )
    if FIRMA_PASSWD.search(texto or ""):
        informe.agregar("critica", nombre, "La API devolvió el contenido de /etc/passwd", f"carga={carga!r}")


def atacar(cliente, openapi: Dict[str, Any], cabeceras: Dict[str, str], cargas: Iterable[str] = TODAS,
           omitir: Callable[[Operacion], bool] = omitir_por_defecto) -> Informe:
    """Manda cada carga a cada ruta: en la ruta, en la consulta y en el cuerpo."""
    informe = Informe()
    componentes = openapi.get("components", {}).get("schemas", {})
    inicio = time.monotonic()
    ops = [op for op in operaciones(openapi) if not omitir(op)]
    informe.rutas = len(ops)
    for op in ops:
        for carga in cargas:
            consulta = {nombre: carga for nombre in op.parametros_consulta}
            cuerpo = ejemplo(op.cuerpo, componentes, carga) if op.cuerpo else None
            t = time.monotonic()
            respuesta = cliente.request(op.metodo, _url(op, carga), params=consulta or None,
                                        json=cuerpo, headers=cabeceras)
            _revisar_respuesta(informe, op, carga, respuesta, (time.monotonic() - t) * 1000, "ruta/consulta/cuerpo")
    informe.segundos = time.monotonic() - inicio
    return informe


def atacar_por_tiempo(cliente, openapi, cabeceras, umbral_s: float = 3.0,
                      omitir: Callable[[Operacion], bool] = omitir_por_defecto) -> Informe:
    """Inyección a ciegas: ninguna respuesta debe tardar lo que pide la carga."""
    informe = Informe()
    componentes = openapi.get("components", {}).get("schemas", {})
    for op in operaciones(openapi):
        if omitir(op):
            continue
        for carga in INYECCION_TIEMPO:
            cuerpo = ejemplo(op.cuerpo, componentes, carga) if op.cuerpo else None
            consulta = {nombre: carga for nombre in op.parametros_consulta}
            t = time.monotonic()
            respuesta = cliente.request(op.metodo, _url(op, carga), params=consulta or None,
                                        json=cuerpo, headers=cabeceras)
            segundos = time.monotonic() - t
            _revisar_respuesta(informe, op, carga, respuesta, segundos * 1000, "tiempo")
            if segundos >= umbral_s:
                informe.agregar("critica", f"{op.metodo} {op.ruta}", "Posible inyección a ciegas por tiempo",
                                f"carga={carga!r} tardó {segundos:.1f} s")
    return informe


def inicio_de_sesion_con_inyeccion(cliente) -> List[Hallazgo]:
    """Las clásicas para saltarse el login no deben abrir sesión."""
    hallazgos = []
    for carga in INYECCION_SQL:
        for cuerpo in ({"email": carga, "password": carga}, {"email": "admin@amatista.local", "password": carga}):
            r = cliente.post("/api/auth/iniciar-sesion", json=cuerpo)
            if r.status_code == 200 and "token" in (r.text or ""):
                hallazgos.append(Hallazgo("critica", "POST /api/auth/iniciar-sesion",
                                          "Inyección SQL abrió una sesión", f"carga={carga!r}"))
    return hallazgos


def revisar_cabeceras(cliente, ruta: str = "/api/salud") -> List[Hallazgo]:
    r = cliente.get(ruta)
    hallazgos = []
    for nombre, valor in CABECERAS_ESPERADAS.items():
        recibido = r.headers.get(nombre)
        if recibido is None or (valor and recibido.lower() != valor.lower()):
            hallazgos.append(Hallazgo("media", f"GET {ruta}", f"Falta la cabecera {nombre}", f"recibido={recibido!r}"))
    if "server" in r.headers and re.search(r"\d", r.headers["server"]):
        hallazgos.append(Hallazgo("baja", f"GET {ruta}", "La cabecera Server revela versiones", r.headers["server"]))
    return hallazgos


# --- Matriz de permisos (lee las dependencias reales de FastAPI) --------------


def requisito_de(ruta) -> str:
    """'rol' si exige un rol, 'sesion' si exige iniciar sesión, 'publica' si no."""
    pendientes = [ruta.dependant]
    encontrado = "publica"
    while pendientes:
        dep = pendientes.pop()
        nombre = getattr(dep.call, "__qualname__", "") if dep.call else ""
        if nombre.startswith("requiere_rol."):
            return "rol"
        if nombre == "usuario_requerido":
            encontrado = "sesion"
        pendientes.extend(dep.dependencies)
    return encontrado


def rutas_de(rutas):
    """APIRoute de la app, también las de routers incluidos (FastAPI ≥ 0.140 los envuelve)."""
    from fastapi.routing import APIRoute

    for ruta in rutas:
        if isinstance(ruta, APIRoute):
            yield ruta
        elif getattr(ruta, "original_router", None) is not None:
            yield from rutas_de(ruta.original_router.routes)
        elif getattr(ruta, "routes", None):
            yield from rutas_de(ruta.routes)


def matriz_de_permisos(app) -> List[Dict[str, str]]:
    filas = []
    for ruta in rutas_de(app.routes):
        for metodo in sorted(ruta.methods):
            filas.append({"metodo": metodo, "ruta": ruta.path, "requisito": requisito_de(ruta)})
    return filas


def probar_permisos(cliente, app, cabeceras_alumno: Dict[str, str]) -> Tuple[List[Dict[str, Any]], List[Hallazgo]]:
    """Sin sesión → 401 en rutas con sesión; alumno → 403 en rutas con rol."""
    filas, hallazgos = [], []
    for fila in matriz_de_permisos(app):
        if fila["requisito"] == "publica":
            filas.append({**fila, "anonimo": None, "alumno": None})
            continue
        url = re.sub(r"\{[^}]+\}", "x", fila["ruta"])
        anonimo = cliente.request(fila["metodo"], url, json={}).status_code
        # Cerrar sesión con el token del alumno lo invalidaría para el resto.
        alumno = None if fila["ruta"] in CIERRAN_SESION else cliente.request(
            fila["metodo"], url, json={}, headers=cabeceras_alumno).status_code
        filas.append({**fila, "anonimo": anonimo, "alumno": alumno})
        nombre = f"{fila['metodo']} {fila['ruta']}"
        if anonimo not in (401, 403):
            hallazgos.append(Hallazgo("critica", nombre, "Responde sin sesión", f"estado={anonimo}"))
        if fila["requisito"] == "rol" and alumno not in (None, 403):
            hallazgos.append(Hallazgo("critica", nombre, "Un alumno entra a una ruta de rol", f"estado={alumno}"))
    return filas, hallazgos


# --- Uso desde la terminal -------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    import httpx

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--token-admin", default="", help="Token de una cuenta admin (llega más hondo en el código)")
    parser.add_argument("--token-alumno", default="", help="Token de un alumno (matriz de permisos)")
    parser.add_argument("--salida", default="", help="Archivo JSON con el informe")
    parser.add_argument("--sin-tiempo", action="store_true", help="Omite la inyección a ciegas por tiempo")
    args = parser.parse_args(argv)

    cabeceras = {"Authorization": f"Bearer {args.token_admin}"} if args.token_admin else {}
    with httpx.Client(base_url=args.url, timeout=30) as cliente:
        openapi = cliente.get("/openapi.json").json()
        # Las descargas arman un .zip por petición: se prueban aparte para no medir eso.
        informe = atacar(cliente, openapi, cabeceras)
        if not args.sin_tiempo:
            tiempo = atacar_por_tiempo(cliente, openapi, cabeceras)
            informe.hallazgos += tiempo.hallazgos
            informe.peticiones += tiempo.peticiones
        informe.hallazgos += inicio_de_sesion_con_inyeccion(cliente)
        informe.hallazgos += revisar_cabeceras(cliente)
    datos = informe.como_dict()
    texto = json.dumps(datos, ensure_ascii=False, indent=2)
    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
    print(f"{datos['peticiones']} peticiones a {datos['rutas']} rutas en {datos['segundos']} s; "
          f"{len(informe.hallazgos)} hallazgos ({len(informe.graves)} graves).")
    for h in informe.hallazgos:
        print(f"  [{h.severidad}] {h.ruta}: {h.titulo} {h.detalle}")
    return 1 if informe.graves else 0


if __name__ == "__main__":
    sys.exit(main())
