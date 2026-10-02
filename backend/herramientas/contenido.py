"""Contenido de los cursos desde la terminal: validar, importar, exportar y crear módulos.

Uso, desde backend/ (importar y exportar usan la base de backend/.env):

    python herramientas/contenido.py validar                 # ../frontend/src/data/modulos/*.json
    python herramientas/contenido.py validar archivo.json otro.json
    python herramientas/contenido.py importar [archivos...]   # upsert; crea los cursos blender y aframe si faltan
    python herramientas/contenido.py exportar mod_teoria_001 [salida.json] [--borradores]
    python herramientas/contenido.py nuevo-modulo blender 2 "Interfaz y navegación" --insignia "Navegante"

validar no necesita base de datos y sale con código 1 si hay errores (lo usa CI).
nuevo-modulo crea ../frontend/src/data/modulos/<curso>-modulo-<n>.json en
borrador con las 5 lecciones de la fórmula (gancho, explora, practica, reto, jefe).
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

from contenido.plantillas import CURSOS_BASE, modulo_esqueleto  # noqa: E402
from contenido.validacion import MAX_TITULO, PATRON_ID, modulo_de, validar_modulo  # noqa: E402

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
            db.commit()
            if creados:
                print(f"Cursos creados: {', '.join(creados)}")
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


def comando_nuevo_modulo(
    curso: str, numero: int, titulo: str, insignia: Optional[str], destino: Optional[str], forzar: bool
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
    datos = modulo_esqueleto(curso, numero, titulo, insignia, modulo_id, ids_existentes)
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
    parser = argparse.ArgumentParser(description="Contenido de Amatista: validar, importar, exportar y crear módulos.")
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

    opciones = parser.parse_args(argumentos)
    if opciones.comando == "validar":
        return comando_validar(opciones.archivos)
    if opciones.comando == "importar":
        return comando_importar(opciones.archivos)
    if opciones.comando == "exportar":
        return comando_exportar(opciones.modulo_id, opciones.salida, opciones.borradores)
    if opciones.comando == "nuevo-modulo":
        return comando_nuevo_modulo(
            opciones.curso, opciones.numero, opciones.titulo, opciones.insignia, opciones.destino, opciones.forzar
        )
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
