"""Pruebas de rendimiento de la API de Amatista.

Tres pasos, desde backend/ (usa la base de backend/.env o DATABASE_URL):

    python herramientas/rendimiento.py sembrar --alumnos 2000 --eventos 60000
    python herramientas/rendimiento.py medir --url http://localhost:8000 --salida rendimiento.json
    python herramientas/rendimiento.py limpiar

- sembrar: crea alumnos de prueba (id «rend-…», es_prueba = 1) con progreso,
  eventos y prácticas, más una cuenta admin y una alumna con sesión. Guarda
  sus tokens en rendimiento_tokens.json (no lo subas: son sesiones reales).
- medir: golpea cada ruta importante con 1, 10 y 40 usuarios a la vez y
  anota latencia (p50, p95, p99), peticiones por segundo y errores.
- limpiar: borra todo lo que creó «sembrar» (solo filas «rend-…»).

NUNCA contra producción: «sembrar» escribe miles de filas y «medir» manda
miles de peticiones. Se corre contra una copia (el contenedor de Oracle
de docs/desarrollador/04_pruebas_y_ci.md) o contra SQLite.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
RAIZ = Path(__file__).resolve().parents[2]
TOKENS = Path("rendimiento_tokens.json")
PREFIJO = "rend-"


# --- sembrar / limpiar ---------------------------------------------------------


def sembrar(alumnos: int, eventos: int) -> Dict[str, str]:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from database.conexion import motor
    from database.modelos import (
        Base, EventoAprendizaje, Leccion, ProgresoLeccion, Sesion, Usuario, ahora,
    )
    from seguridad import DURACION_SESION, hash_password, hash_token, nuevo_token

    m = motor()
    if m.dialect.name == "sqlite":
        Base.metadata.create_all(m)
    momento = ahora()
    azar = random.Random(7)
    with Session(m) as db:
        lecciones = db.execute(select(Leccion.curso_id, Leccion.id).where(Leccion.estado == "publicado")).all()
        if not lecciones:
            lecciones = [("blender_principiante", f"bp1_{i}") for i in range(10)]
        hash_comun = hash_password("clave-de-rendimiento-123")
        usuarios, progreso, lista_eventos = [], [], []
        for i in range(alumnos):
            uid = f"{PREFIJO}{i:06d}"
            creado = momento - timedelta(days=azar.randint(0, 60))
            usuarios.append(dict(id=uid, nombre=f"Alumno {i}", email=f"{uid}@rendimiento.local", rol="alumno",
                                 password_hash=hash_comun, correo_confirmado=1, es_prueba=1, creado_en=creado,
                                 ultimo_acceso=creado, intentos_fallidos=0, codigo_intentos=0))
            for curso, leccion in azar.sample(lecciones, min(len(lecciones), azar.randint(1, 12))):
                completa = azar.random() < 0.7
                progreso.append(dict(usuario_id=uid, curso_id=curso, leccion_id=leccion, completada=int(completa),
                                     puntaje=azar.randint(50, 100) if completa else None, intentos=azar.randint(1, 4),
                                     completada_en=creado if completa else None, actualizado_en=creado))
        for _ in range(eventos):
            uid = f"{PREFIJO}{azar.randrange(alumnos):06d}"
            curso, leccion = azar.choice(lecciones)
            ocurrido = momento - timedelta(minutes=azar.randint(0, 60 * 24 * 30))
            lista_eventos.append(dict(id=str(uuid.uuid4()), usuario_id=uid,
                                      tipo=azar.choice(["learning_session_started", "lesson_completed", "activity_submitted"]),
                                      curso_id=curso, leccion_id=leccion, ocurrido_en=ocurrido, recibido_en=ocurrido,
                                      version_app="rend", es_prueba=1))
        for tabla, filas in ((Usuario, usuarios), (ProgresoLeccion, progreso), (EventoAprendizaje, lista_eventos)):
            for inicio in range(0, len(filas), 2000):
                db.execute(tabla.__table__.insert(), filas[inicio:inicio + 2000])
            db.commit()
        tokens = {}
        for rol in ("admin", "alumno"):
            uid = f"{PREFIJO}{rol}-{uuid.uuid4().hex[:8]}"
            token = nuevo_token()
            db.add(Usuario(id=uid, nombre=f"Rendimiento {rol}", email=f"{uid}@rendimiento.local", rol=rol,
                           password_hash=hash_password("clave-de-rendimiento-123"), correo_confirmado=1, es_prueba=1))
            db.flush()
            db.add(Sesion(id=hash_token(token), usuario_id=uid, dispositivo="rendimiento", activa=1,
                          ultimo_acceso=momento, expira_en=momento + DURACION_SESION))
            tokens[rol] = token
            tokens[f"{rol}_email"] = f"{uid}@rendimiento.local"
        db.commit()
    TOKENS.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")
    print(f"Sembrados {alumnos} alumnos, {len(progreso)} lecciones de progreso y {eventos} eventos. "
          f"Tokens en {TOKENS}.")
    return tokens


def limpiar() -> None:
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from database.conexion import motor
    from database.modelos import (
        AddonAjustes, AddonEnlace, AddonVinculo, EventoAprendizaje, HabilidadAlumno, Logro, ProgresoLeccion,
        ProgresoPractica, Sesion, Usuario,
    )

    with Session(motor()) as db:
        for tabla in (EventoAprendizaje, ProgresoLeccion, ProgresoPractica, HabilidadAlumno, Logro, AddonEnlace,
                      AddonAjustes, Sesion):
            db.execute(delete(tabla).where(tabla.usuario_id.like(f"{PREFIJO}%")))
        db.execute(delete(AddonVinculo).where(AddonVinculo.usuario_id.like(f"{PREFIJO}%")))
        db.execute(delete(Usuario).where(Usuario.id.like(f"{PREFIJO}%")))
        db.commit()
    print("Datos de rendimiento borrados.")


# --- medir -----------------------------------------------------------------------


def escena_de_solucion(practica: Path) -> Dict[str, Any]:
    """La escena del caso «Solución» de pruebas.json, como la manda el add-on."""
    sys.path.insert(0, str(RAIZ / "engine"))
    from amatista_engine.practice.loader import parse_practice
    from amatista_engine.snapshot import scene_to_dict
    from amatista_engine.testing import escena_de_caso

    casos = json.loads((practica / "pruebas.json").read_text(encoding="utf-8"))["casos"]
    caso = next((c for c in casos if c["nombre"].lower().startswith("solución")), casos[-1])
    definicion = parse_practice(json.loads((practica / "practica.json").read_text(encoding="utf-8")))
    return scene_to_dict(escena_de_caso(caso, definicion))


def escenarios(tokens: Dict[str, str]) -> List[Dict[str, Any]]:
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    alumno = {"Authorization": f"Bearer {tokens['alumno']}"}
    escena_tren = escena_de_solucion(RAIZ / "practices/blender/principiante/m1-tren")
    anonimo = lambda: f"anon-{uuid.uuid4()}"  # noqa: E731
    return [
        {"nombre": "Salud (base de datos)", "metodo": "GET", "ruta": "/api/salud"},
        {"nombre": "Catálogo de cursos", "metodo": "GET", "ruta": "/api/contenido/catalogo"},
        {"nombre": "Catálogo (caché ETag)", "metodo": "GET", "ruta": "/api/contenido/catalogo", "etag": True},
        {"nombre": "Iniciar sesión (PBKDF2)", "metodo": "POST", "ruta": "/api/auth/iniciar-sesion",
         "cuerpo": lambda: {"email": tokens["alumno_email"], "password": "clave-de-rendimiento-123"},
         "max_concurrencia": 10},
        {"nombre": "Mi perfil", "metodo": "GET", "ruta": "/api/auth/yo", "cabeceras": alumno},
        {"nombre": "Mi progreso", "metodo": "GET", "ruta": "/api/progreso", "cabeceras": alumno},
        {"nombre": "Guardar progreso (anónimo)", "metodo": "POST", "ruta": "/api/progreso",
         "cuerpo": lambda: {"usuario_id": anonimo(), "eventos": [
             {"curso_id": "blender_principiante", "leccion_id": "bp1_teoria", "completada": True, "puntaje": 90}]}},
        {"nombre": "Eventos de aprendizaje", "metodo": "POST", "ruta": "/api/eventos",
         "cuerpo": lambda: {"usuario_id": anonimo(), "eventos": [
             {"id": str(uuid.uuid4()), "tipo": "learning_session_started", "curso_id": "blender_principiante",
              "leccion_id": "bp1_teoria", "ocurrido_en": "2026-10-05T03:00:00Z"}]}},
        {"nombre": "Prácticas del add-on", "metodo": "GET", "ruta": "/api/addon/v1/practicas", "cabeceras": alumno},
        {"nombre": "Una práctica", "metodo": "GET", "ruta": "/api/addon/v1/practicas/blender.bp.m1.tren",
         "cabeceras": alumno},
        {"nombre": "Calificar intento (motor)", "metodo": "POST", "ruta": "/api/addon/v1/intentos",
         "cabeceras": alumno, "cuerpo": lambda: {"practica_id": "blender.bp.m1.tren", "escena": escena_tren}},
        {"nombre": "Descarga del add-on (pública)", "metodo": "GET", "ruta": "/api/addon/v1/descargas/windows",
         "max_concurrencia": 10},
        {"nombre": "Descarga del add-on (con cuenta)", "metodo": "GET", "ruta": "/api/addon/v1/descargas/windows",
         "cabeceras": alumno, "max_concurrencia": 10},
        {"nombre": "Panel admin: resumen", "metodo": "GET", "ruta": "/api/admin/resumen", "cabeceras": admin,
         "max_concurrencia": 10},
        {"nombre": "Panel admin: usuarios", "metodo": "GET", "ruta": "/api/admin/usuarios", "cabeceras": admin,
         "max_concurrencia": 10},
    ]


def percentil(valores: List[float], p: float) -> float:
    if not valores:
        return 0.0
    orden = sorted(valores)
    k = (len(orden) - 1) * p
    f, c = int(k), min(int(k) + 1, len(orden) - 1)
    return orden[f] + (orden[c] - orden[f]) * (k - f)


def medir_escenario(cliente_fabrica: Callable[[], Any], esc: Dict[str, Any], concurrencia: int,
                    peticiones: int) -> Dict[str, Any]:
    tiempos: List[float] = []
    errores: Dict[str, int] = {}
    bytes_total = 0
    candado = threading.Lock()
    locales = threading.local()
    etag: Optional[str] = None
    if esc.get("etag"):
        with cliente_fabrica() as c:
            etag = c.request(esc["metodo"], esc["ruta"]).headers.get("etag")

    def una(_):
        nonlocal bytes_total
        if not hasattr(locales, "cliente"):
            locales.cliente = cliente_fabrica()
        cabeceras = dict(esc.get("cabeceras") or {})
        if etag:
            cabeceras["If-None-Match"] = etag
        cuerpo = esc["cuerpo"]() if esc.get("cuerpo") else None
        t = time.perf_counter()
        try:
            r = locales.cliente.request(esc["metodo"], esc["ruta"], json=cuerpo, headers=cabeceras)
            ms = (time.perf_counter() - t) * 1000
            with candado:
                tiempos.append(ms)
                bytes_total += len(r.content)
                if r.status_code >= 400:
                    errores[str(r.status_code)] = errores.get(str(r.status_code), 0) + 1
        except Exception as error:  # noqa: BLE001
            with candado:
                errores[type(error).__name__] = errores.get(type(error).__name__, 0) + 1

    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrencia) as grupo:
        list(grupo.map(una, range(peticiones)))
    total = time.perf_counter() - inicio
    return {
        "concurrencia": concurrencia,
        "peticiones": peticiones,
        "segundos": round(total, 3),
        "rps": round(peticiones / total, 1) if total else 0,
        "p50_ms": round(percentil(tiempos, 0.50), 1),
        "p95_ms": round(percentil(tiempos, 0.95), 1),
        "p99_ms": round(percentil(tiempos, 0.99), 1),
        "media_ms": round(statistics.fmean(tiempos), 1) if tiempos else 0,
        "max_ms": round(max(tiempos), 1) if tiempos else 0,
        "errores": errores,
        "kb_por_respuesta": round(bytes_total / max(1, len(tiempos)) / 1024, 1),
    }


def medir(url: str, concurrencias: List[int], peticiones: int, solo: Optional[str] = None) -> Dict[str, Any]:
    import httpx

    tokens = json.loads(TOKENS.read_text(encoding="utf-8"))
    fabrica = lambda: httpx.Client(base_url=url, timeout=60)  # noqa: E731
    resultados = []
    with fabrica() as c:  # calentamiento: carga perezosa de módulos y cachés
        for esc in escenarios(tokens):
            c.request(esc["metodo"], esc["ruta"], json=esc["cuerpo"]() if esc.get("cuerpo") else None,
                      headers=esc.get("cabeceras") or {})
    for esc in escenarios(tokens):
        if solo and solo.lower() not in esc["nombre"].lower():
            continue
        filas = []
        for n in concurrencias:
            n = min(n, esc.get("max_concurrencia", n))
            if any(f["concurrencia"] == n for f in filas):
                continue
            total = peticiones if n > 1 else max(20, peticiones // 4)
            if esc.get("max_concurrencia"):
                total = min(total, 60)
            filas.append(medir_escenario(fabrica, esc, n, total))
            print(f"  {esc['nombre']:<36} x{n:<3} p50 {filas[-1]['p50_ms']:>8} ms  p95 {filas[-1]['p95_ms']:>8} ms  "
                  f"{filas[-1]['rps']:>7} rps  errores {filas[-1]['errores'] or '-'}")
        resultados.append({"nombre": esc["nombre"], "metodo": esc["metodo"], "ruta": esc["ruta"], "mediciones": filas})
    return {"url": url, "fecha": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "escenarios": resultados}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="orden", required=True)
    s = sub.add_parser("sembrar")
    s.add_argument("--alumnos", type=int, default=2000)
    s.add_argument("--eventos", type=int, default=60000)
    m = sub.add_parser("medir")
    m.add_argument("--url", default="http://localhost:8000")
    m.add_argument("--concurrencia", default="1,10,40")
    m.add_argument("--peticiones", type=int, default=200)
    m.add_argument("--solo", help="Solo los escenarios cuyo nombre contiene este texto")
    m.add_argument("--salida", default="rendimiento.json")
    sub.add_parser("limpiar")
    args = parser.parse_args(argv)
    if args.orden == "sembrar":
        sembrar(args.alumnos, args.eventos)
    elif args.orden == "limpiar":
        limpiar()
    else:
        datos = medir(args.url, [int(x) for x in args.concurrencia.split(",")], args.peticiones, args.solo)
        Path(args.salida).write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Resultados en {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
