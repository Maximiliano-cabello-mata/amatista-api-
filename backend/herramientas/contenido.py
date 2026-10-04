"""Contenido de los cursos desde la terminal: validar, importar, exportar y crear módulos.

Uso, desde backend/ (importar y exportar usan la base de backend/.env):

    python herramientas/contenido.py validar                 # ../frontend/src/data/modulos/*.json
    python herramientas/contenido.py validar archivo.json otro.json
    python herramientas/contenido.py importar [archivos...]   # upsert; crea los cursos blender y aframe si faltan
    python herramientas/contenido.py exportar mod_teoria_001 [salida.json] [--borradores]
    python herramientas/contenido.py nuevo-modulo blender 2 "Interfaz y navegación" --insignia "Navegante" --nivel blender-n1
    python herramientas/contenido.py nueva-leccion ../frontend/src/data/modulos/blender-modulo-2.json les_n1_mesa "Construir una mesa" --objetivo "..."
    python herramientas/contenido.py mapa [blender]          # curso > nivel > módulo > lección desde los archivos
    python herramientas/contenido.py sembrar-niveles          # crea en la base los 5 niveles de Blender que falten
    python herramientas/contenido.py practicas [--publicar]   # registra en la base las prácticas de practices/blender/ (sql/007)

validar y mapa no necesitan base de datos; validar sale con código 1 si hay
errores (lo usa CI). nuevo-modulo crea ../frontend/src/data/modulos/<curso>-modulo-<n>.json
en borrador con las 5 lecciones de la fórmula (gancho, explora, practica, reto,
jefe). nueva-leccion agrega al final de un módulo una lección con la estructura
mínima de 10 pasos y su ficha (reestructuración v3). importar también crea
los niveles de Blender que falten (los módulos pueden decir «nivel»).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
MODULOS = REPO / "frontend" / "src" / "data" / "modulos"

# Permite ejecutarlo como "python herramientas/contenido.py" desde backend/
# (y que "contenido" sea el paquete backend/contenido, no este archivo).
sys.path.insert(0, str(BACKEND))

from contenido.plantillas import CURSOS_BASE, NIVELES_BLENDER, leccion_estructurada, modulo_esqueleto  # noqa: E402
from contenido.validacion import (  # noqa: E402
    MAX_TITULO,
    PATRON_ID,
    modulo_de,
    pendientes_ficha,
    validar_leccion,
    validar_modulo,
)

PATRON_ARCHIVO = re.compile(r"^([A-Za-z0-9_]+)-modulo-(\d+)\.json$")


def nombre_visible(ruta: Path) -> str:
    try:
        relativa = os.path.relpath(ruta)
    except ValueError:  # Windows: otra unidad
        return str(ruta)
    # ../frontend/... se lee mejor relativo; algo fuera del repo, con su ruta completa.
    return str(ruta.resolve()) if relativa.startswith(os.path.join(os.pardir, os.pardir)) else relativa


def leer_archivo(ruta: Path) -> Tuple[Optional[dict], List[str]]:
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError as error:
        return None, [f"no se pudo leer el archivo: {error.strerror or error}"]
    try:
        return json.loads(texto), []
    except json.JSONDecodeError as error:
        return None, [f"JSON mal formado (línea {error.lineno}, columna {error.colno}): {error.msg}"]


def curso_de_archivo(ruta: Path) -> Optional[str]:
    coincide = PATRON_ARCHIVO.match(ruta.name)
    return coincide.group(1) if coincide else None


def revisar_archivos(rutas: Sequence[Path]) -> Dict[Path, List[str]]:
    """Errores por archivo, incluidos los que cruzan archivos (ids repetidos por curso)."""
    resultados: Dict[Path, List[str]] = {}
    modulos_vistos: Dict[str, str] = {}
    lecciones_vistas: Dict[Tuple[str, str], str] = {}
    publicados_por_numero: Dict[Tuple[str, int], str] = {}
    for ruta in rutas:
        datos, errores = leer_archivo(ruta)
        resultados[ruta] = errores
        if datos is None:
            continue
        errores.extend(validar_modulo(datos))
        modulo = modulo_de(datos)
        if not isinstance(modulo, dict):
            continue
        if not (isinstance(datos, dict) and "module" in datos):
            errores.append('el archivo debe tener la forma {"module": {...}}')
        # En los archivos, curso y estado son obligatorios: la PWA arma el catálogo con ellos.
        curso = modulo.get("curso")
        if not curso:
            errores.append("module: falta «curso» (blender, aframe...)")
        if not modulo.get("estado"):
            errores.append("module: falta «estado» (borrador, revision o publicado)")
        coincide = PATRON_ARCHIVO.match(ruta.name)
        if coincide and curso and curso != coincide.group(1):
            errores.append(f"module: «curso» = «{curso}» no coincide con el nombre del archivo")
        if coincide and modulo.get("order") != int(coincide.group(2)):
            errores.append(f"module: «order» = {modulo.get('order')} no coincide con el nombre del archivo")

        nombre = ruta.name
        modulo_id = modulo.get("id")
        if isinstance(modulo_id, str):
            if modulo_id in modulos_vistos:
                errores.append(f"module: el id «{modulo_id}» ya lo usa {modulos_vistos[modulo_id]}")
            else:
                modulos_vistos[modulo_id] = nombre
        if not curso:
            continue
        numero = modulo.get("order")
        if modulo.get("estado") == "publicado" and isinstance(numero, int):
            clave_numero = (curso, numero)
            if clave_numero in publicados_por_numero:
                errores.append(
                    f"module: ya hay un módulo {numero} publicado de «{curso}» en {publicados_por_numero[clave_numero]}"
                )
            else:
                publicados_por_numero[clave_numero] = nombre
        lecciones = modulo.get("lessons") if isinstance(modulo.get("lessons"), list) else []
        for i, leccion in enumerate(lecciones):
            leccion_id = leccion.get("id") if isinstance(leccion, dict) else None
            if not isinstance(leccion_id, str):
                continue
            # El progreso se guarda por curso y lección: el id no se puede repetir en todo el curso.
            clave = (curso, leccion_id)
            if clave in lecciones_vistas and lecciones_vistas[clave] != nombre:
                errores.append(f"lessons[{i}]: el id «{leccion_id}» ya lo usa {lecciones_vistas[clave]} (mismo curso)")
            else:
                lecciones_vistas[clave] = nombre
    return resultados


def rutas_o_defecto(archivos: Sequence[str]) -> List[Path]:
    if archivos:
        return [Path(a) for a in archivos]
    return sorted(MODULOS.glob("*.json"))


def comando_validar(archivos: Sequence[str]) -> int:
    rutas = rutas_o_defecto(archivos)
    if not rutas:
        print(f"No hay archivos que validar en {MODULOS}.", file=sys.stderr)
        return 1
    resultados = revisar_archivos(rutas)
    con_errores = 0
    for ruta, errores in resultados.items():
        if errores:
            con_errores += 1
            print(f"ERROR {nombre_visible(ruta)}")
            for error in errores:
                print(f"      - {error}")
        else:
            datos, _ = leer_archivo(ruta)
            lecciones = len(modulo_de(datos).get("lessons", []))
            print(f"OK    {nombre_visible(ruta)} ({lecciones} lecciones)")
    print(f"{len(rutas)} archivo(s), {con_errores} con errores.")
    return 1 if con_errores else 0


def preparar_base():
    """Motor de la base configurada (crea las tablas solo en SQLite, como main.py)."""
    from database.conexion import motor
    from database.modelos import Base

    if motor().dialect.name == "sqlite":
        Base.metadata.create_all(motor())
    return motor()


def mensaje_bd(error: Exception) -> str:
    original = getattr(error, "orig", None) or error
    texto = str(original).strip()
    return f"Error de base de datos: {texto.splitlines()[0] if texto else type(original).__name__}"


def comando_importar(archivos: Sequence[str]) -> int:
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    from api.contenido import asegurar_cursos_base, importar_modulo
    from api.niveles import sembrar_niveles_blender
    from contenido.validacion import ContenidoInvalido

    rutas = rutas_o_defecto(archivos)
    if not rutas:
        print(f"No hay archivos que importar en {MODULOS}.", file=sys.stderr)
        return 1
    # Primero se validan todos: no se importa nada si un archivo tiene errores.
    resultados = revisar_archivos(rutas)
    if any(resultados.values()):
        for ruta, errores in resultados.items():
            for error in errores:
                print(f"ERROR {nombre_visible(ruta)}: {error}", file=sys.stderr)
        print("No se importó nada: corrige los errores (python herramientas/contenido.py validar).", file=sys.stderr)
        return 1

    try:
        with Session(preparar_base()) as db:
            creados = asegurar_cursos_base(db)
            niveles = sembrar_niveles_blender(db)
            db.commit()
            if creados:
                print(f"Cursos creados: {', '.join(creados)}")
            if niveles:
                print(f"Niveles creados (en borrador): {', '.join(niveles)}")
            codigo = 0
            for ruta in rutas:
                datos, _ = leer_archivo(ruta)
                try:
                    resumen = importar_modulo(db, datos, curso_id=curso_de_archivo(ruta))
                    db.commit()
                except ContenidoInvalido as error:
                    db.rollback()
                    codigo = 1
                    for texto in error.errores:
                        print(f"ERROR {nombre_visible(ruta)}: {texto}", file=sys.stderr)
                    continue
                print(
                    f"OK    {nombre_visible(ruta)}: módulo {resumen['modulo_id']} ({resumen['modulo']}, "
                    f"{resumen['estado']}); lecciones: {resumen['lecciones_nuevas']} nuevas, "
                    f"{resumen['lecciones_actualizadas']} actualizadas, {resumen['lecciones_sin_cambios']} sin cambios"
                )
                if resumen["solo_en_base"]:
                    print(f"      En la base pero no en el archivo (no se tocaron): {', '.join(resumen['solo_en_base'])}")
            return codigo
    except SQLAlchemyError as error:
        print(mensaje_bd(error), file=sys.stderr)
        return 1


def guardar_json(datos: dict, ruta: Path) -> None:
    # Mismo formato que los archivos existentes: 2 espacios y acentos sin escapar.
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def comando_exportar(modulo_id: str, salida: Optional[str], borradores: bool) -> int:
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    from api.contenido import exportar_modulo

    try:
        with Session(preparar_base()) as db:
            datos = exportar_modulo(db, modulo_id, borradores=borradores)
    except SQLAlchemyError as error:
        print(mensaje_bd(error), file=sys.stderr)
        return 1
    if datos is None:
        print(f"No existe el módulo «{modulo_id}».", file=sys.stderr)
        return 1
    if salida:
        guardar_json(datos, Path(salida))
        print(f"Módulo {modulo_id} exportado en {salida} ({len(datos['module']['lessons'])} lecciones).")
    else:
        sys.stdout.write(json.dumps(datos, indent=2, ensure_ascii=False) + "\n")
    return 0


def comando_sembrar_niveles() -> int:
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    from api.contenido import asegurar_cursos_base
    from api.niveles import sembrar_niveles_blender

    try:
        with Session(preparar_base()) as db:
            asegurar_cursos_base(db)
            creados = sembrar_niveles_blender(db)
            db.commit()
    except SQLAlchemyError as error:
        print(mensaje_bd(error), file=sys.stderr)
        return 1
    if creados:
        print(f"Niveles creados en borrador: {', '.join(creados)}. Publícalos en el panel o con amatista_autor.publicar_nivel.")
    else:
        print("Los niveles de Blender ya existían: no se cambió nada.")
    return 0


def comando_practicas(publicar: bool) -> int:
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    from api.addon import sincronizar_practicas

    try:
        with Session(preparar_base()) as db:
            resumen = sincronizar_practicas(db, None, publicar)
            db.commit()
    except SQLAlchemyError as error:
        print(mensaje_bd(error), file=sys.stderr)
        print("¿Ya ejecutaste sql/007_motor_practicas.sql en Oracle?", file=sys.stderr)
        return 1
    for practica in resumen["practicas"]:
        cambio = "sin cambios" if practica["sin_cambios"] else "nueva versión"
        print(f"  {practica['id']}: versión {practica['version']} ({cambio}), {practica['estado']}")
    for error in resumen["errores"]:
        print(f"  ERROR {error}", file=sys.stderr)
    print(f"Lecciones enlazadas: {resumen['lecciones_enlazadas']}.")
    if any(p["estado"] != "publicado" for p in resumen["practicas"]):
        print("Hay prácticas en borrador: publícalas en el panel (Prácticas de Blender) o repite con --publicar.")
    return 1 if resumen["errores"] else 0


def comando_mapa(curso: Optional[str], carpeta: Optional[str]) -> int:
    """Curso > nivel > módulo > lección de los archivos, con lo que falta en cada ficha."""
    rutas = sorted((Path(carpeta) if carpeta else MODULOS).glob("*.json"))
    titulos = {n["id"]: f"Nivel {n['numero']}" + (f" ({n['rama']})" if n["rama"] else "") + f": {n['titulo']}"
               for n in NIVELES_BLENDER}
    por_curso: Dict[str, Dict[str, List[Tuple[dict, Path]]]] = {}
    for ruta in rutas:
        datos, errores = leer_archivo(ruta)
        modulo = modulo_de(datos) if datos is not None else None
        if errores or not isinstance(modulo, dict):
            print(f"ERROR {nombre_visible(ruta)}: no se pudo leer", file=sys.stderr)
            continue
        curso_modulo = modulo.get("curso") or curso_de_archivo(ruta) or "—"
        if curso and curso_modulo != curso:
            continue
        por_curso.setdefault(curso_modulo, {}).setdefault(modulo.get("nivel") or "", []).append((modulo, ruta))
    if not por_curso:
        print("No hay módulos que mostrar.")
        return 0
    total = completas = 0
    for curso_id, niveles in sorted(por_curso.items()):
        print(f"\n{curso_id}")
        for nivel_id in sorted(niveles, key=lambda n: (n == "", n)):
            print(f"  {titulos.get(nivel_id, nivel_id) if nivel_id else 'Sin nivel asignado'}")
            for modulo, ruta in sorted(niveles[nivel_id], key=lambda par: par[0].get("order") or 0):
                print(f"    Módulo {modulo.get('order')} · {modulo.get('title')} [{modulo.get('estado', 'publicado')}]")
                for leccion in modulo.get("lessons") or []:
                    if not isinstance(leccion, dict):
                        continue
                    faltan = pendientes_ficha(leccion, curso_id == "blender")
                    total += 1
                    completas += not faltan
                    marca = "✓" if not faltan else "·"
                    detalle = "" if not faltan else f"  (falta: {', '.join(faltan)})"
                    print(f"      {marca} {leccion.get('id')} {leccion.get('title')}{detalle}")
    print(f"\n{completas} de {total} lecciones con la ficha completa.")
    return 0


def comando_nueva_leccion(archivo: str, leccion_id: str, titulo: str, objetivo: Optional[str]) -> int:
    ruta = Path(archivo)
    datos, errores = leer_archivo(ruta)
    if errores:
        print(f"ERROR {nombre_visible(ruta)}: {errores[0]}", file=sys.stderr)
        return 1
    modulo = modulo_de(datos)
    if not isinstance(modulo, dict) or not isinstance(modulo.get("lessons"), list):
        print(f"ERROR {nombre_visible(ruta)}: no es un archivo de módulo", file=sys.stderr)
        return 1
    if any(isinstance(x, dict) and x.get("id") == leccion_id for x in modulo["lessons"]):
        print(f"Ya existe la lección «{leccion_id}» en este módulo.", file=sys.stderr)
        return 1
    leccion = leccion_estructurada(leccion_id, titulo.strip(), objetivo or "", bloqueada=bool(modulo["lessons"]))
    problemas = validar_leccion(leccion)
    if problemas:
        for problema in problemas:
            print(f"ERROR {problema}", file=sys.stderr)
        return 2
    modulo["lessons"].append(leccion)
    guardar_json(datos, ruta)
    print(
        f"Agregada {leccion_id} al final de {nombre_visible(ruta)} con la estructura de 10 pasos. "
        "Llena la ficha (objetivo, habilidades, versión de Blender, comprobación) y valida."
    )
    return 0


def comando_nuevo_modulo(
    curso: str,
    numero: int,
    titulo: str,
    insignia: Optional[str],
    destino: Optional[str],
    forzar: bool,
    nivel: Optional[str] = None,
) -> int:
    carpeta = Path(destino) if destino else MODULOS
    titulo = titulo.strip()
    if not PATRON_ID.match(curso):
        print("El curso solo admite letras, números, guion y guion bajo (por ejemplo blender).", file=sys.stderr)
        return 2
    if numero < 1 or not titulo or len(f"Módulo {numero}: {titulo}") > MAX_TITULO:
        print(f"Usa un número de módulo 1 o mayor y un título de hasta {MAX_TITULO - 12} caracteres.", file=sys.stderr)
        return 2
    archivo = carpeta / f"{curso}-modulo-{numero}.json"
    if archivo.exists() and not forzar:
        print(f"Ya existe {nombre_visible(archivo)}: no se sobrescribe (usa --forzar).", file=sys.stderr)
        return 1

    # Ids de lecciones que ya usa el curso, para seguir su numeración (les_004 → les_005...).
    ids_existentes: List[str] = []
    ids_modulos = set()
    for otro in sorted(carpeta.glob("*.json")) if carpeta.is_dir() else []:
        if otro.resolve() == archivo.resolve():
            continue
        datos, _ = leer_archivo(otro)
        modulo = modulo_de(datos) if datos is not None else None
        if not isinstance(modulo, dict):
            continue
        ids_modulos.add(modulo.get("id"))
        if (modulo.get("curso") or curso_de_archivo(otro)) == curso:
            ids_existentes += [x["id"] for x in modulo.get("lessons", []) if isinstance(x, dict) and "id" in x]

    modulo_id = f"mod_{curso}_{numero:03d}"
    if modulo_id in ids_modulos:
        print(f"Ya hay un módulo con el id «{modulo_id}» en {nombre_visible(carpeta)}.", file=sys.stderr)
        return 1
    if nivel and not PATRON_ID.match(nivel):
        print("El nivel solo admite letras, números, guion y guion bajo (por ejemplo blender-n1).", file=sys.stderr)
        return 2
    datos = modulo_esqueleto(curso, numero, titulo, insignia, modulo_id, ids_existentes, nivel_id=nivel)
    errores = validar_modulo(datos)
    if errores:  # no debería pasar: las plantillas se prueban
        for error in errores:
            print(f"ERROR {error}", file=sys.stderr)
        return 1
    carpeta.mkdir(parents=True, exist_ok=True)
    guardar_json(datos, archivo)
    lecciones = datos["module"]["lessons"]
    print(
        f"Creado {nombre_visible(archivo)} en borrador con {len(lecciones)} lecciones "
        f"({lecciones[0]['id']} … {lecciones[-1]['id']}). Reemplaza el contenido de ejemplo, valida y cambia "
        "«estado» a «publicado» cuando esté listo."
    )
    if curso not in CURSOS_BASE:
        print(f"Aviso: el curso «{curso}» no está en frontend/src/data/cursos.js.")
    return 0


def main(argumentos: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Contenido de Amatista: validar, importar, exportar, crear módulos y lecciones, y ver el mapa."
    )
    comandos = parser.add_subparsers(dest="comando", metavar="comando")

    validar = comandos.add_parser("validar", help="valida archivos de módulo (por defecto todos los del frontend)")
    validar.add_argument("archivos", nargs="*", help="archivos .json (por defecto ../frontend/src/data/modulos/*.json)")

    importar = comandos.add_parser("importar", help="importa archivos de módulo a la base (upsert)")
    importar.add_argument("archivos", nargs="*", help="archivos .json (por defecto ../frontend/src/data/modulos/*.json)")

    exportar = comandos.add_parser("exportar", help="exporta un módulo de la base en formato de archivo")
    exportar.add_argument("modulo_id", help="id del módulo, por ejemplo mod_teoria_001")
    exportar.add_argument("salida", nargs="?", help="archivo de salida (por defecto se imprime)")
    exportar.add_argument("--borradores", action="store_true", help="incluye las lecciones en borrador")

    nuevo = comandos.add_parser("nuevo-modulo", help="crea un módulo en borrador con la fórmula Amatista")
    nuevo.add_argument("curso", help="id del curso, por ejemplo blender o aframe")
    nuevo.add_argument("numero", type=int, help="número del módulo dentro del curso")
    nuevo.add_argument("titulo", help="título del módulo (sin «Módulo N:»)")
    nuevo.add_argument("--insignia", default=None, help="nombre de la insignia que desbloquea el examen final")
    nuevo.add_argument("--destino", default=None, help="carpeta de salida (por defecto ../frontend/src/data/modulos)")
    nuevo.add_argument("--forzar", action="store_true", help="sobrescribe el archivo si ya existe")
    nuevo.add_argument("--nivel", default=None, help="nivel del módulo, por ejemplo blender-n1")

    leccion = comandos.add_parser("nueva-leccion", help="agrega a un módulo una lección de 10 pasos con su ficha")
    leccion.add_argument("archivo", help="archivo del módulo, por ejemplo ../frontend/src/data/modulos/blender-modulo-2.json")
    leccion.add_argument("leccion_id", help="id nuevo de la lección (no se reutilizan)")
    leccion.add_argument("titulo", help="título de la lección")
    leccion.add_argument("--objetivo", default=None, help="objetivo observable (una frase)")

    mapa = comandos.add_parser("mapa", help="curso > nivel > módulo > lección de los archivos y fichas pendientes")
    mapa.add_argument("curso", nargs="?", help="id del curso (por defecto todos)")
    mapa.add_argument("--carpeta", default=None, help="carpeta de módulos (por defecto ../frontend/src/data/modulos)")

    comandos.add_parser("sembrar-niveles", help="crea en la base los niveles de Blender que falten")
    practicas = comandos.add_parser("practicas", help="registra en la base las prácticas de practices/blender/")
    practicas.add_argument("--publicar", action="store_true", help="publica la versión registrada para los alumnos")

    opciones = parser.parse_args(argumentos)
    if opciones.comando == "validar":
        return comando_validar(opciones.archivos)
    if opciones.comando == "importar":
        return comando_importar(opciones.archivos)
    if opciones.comando == "exportar":
        return comando_exportar(opciones.modulo_id, opciones.salida, opciones.borradores)
    if opciones.comando == "nuevo-modulo":
        return comando_nuevo_modulo(
            opciones.curso,
            opciones.numero,
            opciones.titulo,
            opciones.insignia,
            opciones.destino,
            opciones.forzar,
            opciones.nivel,
        )
    if opciones.comando == "nueva-leccion":
        if not PATRON_ID.match(opciones.leccion_id) or not opciones.titulo.strip():
            print("Usa un id con letras, números, guion y guion bajo, y un título.", file=sys.stderr)
            return 2
        return comando_nueva_leccion(opciones.archivo, opciones.leccion_id, opciones.titulo, opciones.objetivo)
    if opciones.comando == "mapa":
        return comando_mapa(opciones.curso, opciones.carpeta)
    if opciones.comando == "sembrar-niveles":
        return comando_sembrar_niveles()
    if opciones.comando == "practicas":
        return comando_practicas(opciones.publicar)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
