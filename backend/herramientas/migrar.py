"""Migrar Amatista a otra base de datos: DDL, exportar e importar sin depender de Oracle.

Uso, desde backend/ (el origen es la base de backend/.env, igual que el backend):

    python herramientas/migrar.py ddl --dialecto postgresql [--salida esquema.sql]
    python herramientas/migrar.py exportar respaldo/                      # base de .env -> JSONL
    python herramientas/migrar.py importar respaldo/ --url postgresql+psycopg://u:p@host/amatista --crear-tablas
    python herramientas/migrar.py verificar respaldo/ --url postgresql+psycopg://u:p@host/amatista

- ddl: CREATE TABLE e índices de database/modelos.py (la fuente de verdad)
  para postgresql, mysql, sqlite u oracle, con los CHECK de los valores
  permitidos (roles, estados...). Sin particiones ni jobs: eso es de cada
  motor (docs/base-de-datos/03_migracion.md).
- exportar: un archivo <tabla>.jsonl por tabla (una fila = un objeto JSON,
  fechas en ISO 8601) y manifiesto.json con filas y huella SHA-256 de cada
  archivo. Solo lee: se puede correr con la plataforma en línea.
- importar: verifica el manifiesto, crea las tablas si se pide y copia las
  filas en orden de llaves foráneas. Se niega a escribir en una tabla que
  ya tiene filas (nunca mezcla ni sobrescribe). Al final cuenta y compara.
- verificar: compara filas del destino contra el manifiesto.

El formato (amatista.export/1) no depende de ningún motor: sirve para pasar
de Oracle a PostgreSQL, de vuelta, o a SQLite para pruebas.
"""
import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import TIMESTAMP, CheckConstraint, MetaData, Table, create_engine, func, inspect, select  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from database import modelos  # noqa: E402
from database.modelos import Base, EsquemaMigracion, ahora  # noqa: E402

FORMATO = "amatista.export/1"
LOTE = 500
DIALECTOS = ("postgresql", "mysql", "sqlite", "oracle")
CARPETA_SQL = BACKEND / "sql"

# Valores permitidos (los mismos CHECK que crean los scripts de backend/sql/).
# Así el esquema de otra base rechaza lo mismo que Oracle.
CHECKS = (
    ("usuarios", "rol", modelos.ROLES),
    ("eventos_aprendizaje", "tipo", modelos.TIPOS_EVENTO),
    ("cursos", "estado", modelos.ESTADOS_CONTENIDO),
    ("modulos", "estado", modelos.ESTADOS_CONTENIDO),
    ("lecciones", "estado", modelos.ESTADOS_CONTENIDO),
    ("niveles", "estado", modelos.ESTADOS_CONTENIDO),
    ("practicas", "estado", modelos.ESTADOS_CONTENIDO),
    ("practicas", "origen", modelos.ORIGENES_PRACTICA),
    ("habilidades_alumno", "estado", modelos.ESTADOS_HABILIDAD),
    ("evaluaciones_rubrica", "criterio", modelos.CRITERIOS_RUBRICA),
    ("evaluaciones_rubrica", "logro", modelos.LOGROS_RUBRICA),
    ("versiones_blender", "categoria", modelos.CATEGORIAS_BLENDER),
    ("verificaciones_blender", "resultado", modelos.RESULTADOS_VERIFICACION),
    ("addon_vinculos", "estado", modelos.ESTADOS_VINCULO),
    ("progreso_practicas", "autonomia", tuple(e for e in modelos.ESTADOS_HABILIDAD if e != "sin_practicar")),
    ("esquema_migraciones", "origen", modelos.ORIGENES_MIGRACION),
)


class ErrorMigracion(Exception):
    """Algo impide migrar sin riesgo; el mensaje dice qué hacer."""


# --- DDL ------------------------------------------------------------------------


def _dialecto(nombre: str):
    """Dialecto de SQLAlchemy para generar DDL (postgresql, mysql, sqlite u oracle); ErrorMigracion si no se admite."""
    if nombre == "postgresql":
        from sqlalchemy.dialects import postgresql

        return postgresql.dialect()
    if nombre == "mysql":
        from sqlalchemy.dialects import mysql

        return mysql.dialect()
    if nombre == "sqlite":
        from sqlalchemy.dialects import sqlite

        return sqlite.dialect()
    if nombre == "oracle":
        from sqlalchemy.dialects import oracle

        return oracle.dialect()
    raise ErrorMigracion(f"Dialecto «{nombre}» no soportado. Usa uno de: {', '.join(DIALECTOS)}.")


def metadata_portable() -> MetaData:
    """Copia de las tablas de modelos.py con los CHECK de valores permitidos."""
    copia = MetaData()
    for tabla in Base.metadata.sorted_tables:
        tabla.to_metadata(copia)
    for nombre, columna, valores in CHECKS:
        lista = ", ".join(f"'{v}'" for v in valores)
        copia.tables[nombre].append_constraint(
            CheckConstraint(f"{columna} IN ({lista})", name=f"ck_{nombre}_{columna}"[:30])
        )
    return copia


def _orden_estable(sentencia: str) -> str:
    """SQLAlchemy guarda las restricciones en un set: se ordenan (llaves foráneas,
    luego UNIQUE, luego CHECK) para que el archivo no cambie entre corridas."""
    lineas = sentencia.split("\n")

    def grupo(linea: str) -> int:
        """Grupo de orden de una línea del CREATE TABLE: 0 FOREIGN KEY, 1 UNIQUE, 2 CHECK, -1 el resto (no se mueve)."""
        texto = linea.strip()
        if texto.startswith("FOREIGN KEY"):
            return 0
        if texto.startswith("UNIQUE") or " UNIQUE " in texto:
            return 1
        if " CHECK " in texto or texto.startswith("CHECK"):
            return 2
        return -1

    posiciones = [i for i, linea in enumerate(lineas) if grupo(linea) >= 0]
    if len(posiciones) < 2:
        return sentencia
    ultima_con_coma = lineas[posiciones[-1]].rstrip().endswith(",")
    textos = sorted((lineas[i].rstrip().rstrip(",") for i in posiciones), key=lambda t: (grupo(t), t))
    for numero, (i, texto) in enumerate(zip(posiciones, textos)):
        final = numero == len(posiciones) - 1
        lineas[i] = texto if final and not ultima_con_coma else texto + ", "
    return "\n".join(lineas)


def ddl(dialecto: str) -> str:
    """Script CREATE TABLE + CREATE INDEX de todas las tablas de modelos.py para `dialecto`, en orden estable."""
    motor = _dialecto(dialecto)
    lineas = [
        f"-- Esquema de Amatista para {dialecto}, generado desde backend/database/modelos.py",
        "-- con: python herramientas/migrar.py ddl --dialecto " + dialecto,
        "-- No editar a mano: se regenera. Guía: docs/base-de-datos/03_migracion.md",
        "",
    ]
    for tabla in metadata_portable().sorted_tables:
        lineas.append(_orden_estable(str(CreateTable(tabla).compile(dialect=motor)).strip()) + ";")
        lineas.append("")
        for indice in sorted(tabla.indexes, key=lambda i: i.name or ""):
            lineas.append(str(CreateIndex(indice).compile(dialect=motor)).strip() + ";")
        if tabla.indexes:
            lineas.append("")
    return "\n".join(lineas).rstrip() + "\n"


# --- Exportar -------------------------------------------------------------------


def _a_json(valor):
    """Valor de una columna listo para JSONL (fechas en ISO 8601); ErrorMigracion si aparece un binario."""
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, bytes):
        raise ErrorMigracion("Amatista no guarda binarios; una columna devolvió bytes.")
    return valor


def _huella(archivo: Path) -> str:
    """SHA-256 hexadecimal del contenido de un archivo exportado (se guarda en el manifiesto)."""
    return hashlib.sha256(archivo.read_bytes()).hexdigest()


def exportar(motor: Engine, carpeta: Path) -> dict:
    """Escribe <tabla>.jsonl y manifiesto.json. Solo lee el origen."""
    carpeta.mkdir(parents=True, exist_ok=True)
    existentes = {t.lower() for t in inspect(motor).get_table_names()}
    tablas, faltan = {}, []
    with motor.connect() as conexion:
        for tabla in Base.metadata.sorted_tables:
            if tabla.name not in existentes:
                faltan.append(tabla.name)
                continue
            archivo = carpeta / f"{tabla.name}.jsonl"
            filas = 0
            consulta = select(tabla).order_by(*tabla.primary_key.columns)
            with archivo.open("w", encoding="utf-8") as salida:
                for fila in conexion.execution_options(stream_results=True).execute(consulta).mappings():
                    datos = {columna: _a_json(valor) for columna, valor in fila.items()}
                    salida.write(json.dumps(datos, ensure_ascii=False, separators=(",", ":")) + "\n")
                    filas += 1
            tablas[tabla.name] = {"filas": filas, "sha256": _huella(archivo)}
    manifiesto = {
        "formato": FORMATO,
        "creado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origen": motor.dialect.name,
        "orden": [t.name for t in Base.metadata.sorted_tables if t.name in tablas],
        "tablas": tablas,
        "sin_tabla_en_origen": faltan,
    }
    (carpeta / "manifiesto.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return manifiesto


# --- Importar -------------------------------------------------------------------


def leer_manifiesto(carpeta: Path) -> dict:
    """Lee manifiesto.json de un respaldo y comprueba formato y huella de cada .jsonl.

    Lanza ErrorMigracion si falta, es de otro formato o algún archivo cambió.
    """
    ruta = carpeta / "manifiesto.json"
    if not ruta.is_file():
        raise ErrorMigracion(f"No hay manifiesto.json en {carpeta}: ¿es la carpeta de «exportar»?")
    manifiesto = json.loads(ruta.read_text("utf-8"))
    if manifiesto.get("formato") != FORMATO:
        raise ErrorMigracion(f"Formato {manifiesto.get('formato')!r}: esta herramienta lee {FORMATO}.")
    for nombre, datos in manifiesto["tablas"].items():
        archivo = carpeta / f"{nombre}.jsonl"
        if not archivo.is_file() or _huella(archivo) != datos["sha256"]:
            raise ErrorMigracion(f"{archivo.name} falta o cambió desde que se exportó (huella distinta).")
    return manifiesto


def _filas(archivo: Path, tabla: Table) -> Iterable[dict]:
    """Lee las filas de un .jsonl convirtiendo de nuevo a datetime las columnas TIMESTAMP."""
    fechas = {c.name for c in tabla.columns if isinstance(c.type, TIMESTAMP)}
    with archivo.open(encoding="utf-8") as entrada:
        for linea in entrada:
            fila = json.loads(linea)
            for columna in fechas:
                if fila.get(columna):
                    fila[columna] = datetime.fromisoformat(fila[columna])
            yield fila


def _en_orden_propio(tabla: Table, filas: List[dict]) -> List[dict]:
    """Si la tabla se apunta a sí misma (CURSOS.REQUISITO_ID), los referidos van primero."""
    propias = [fk for fk in tabla.foreign_keys if fk.column.table is tabla]
    if not propias:
        return filas
    pk = tabla.primary_key.columns.values()[0].name
    columnas = [fk.parent.name for fk in propias]
    pendientes, listas, vistos = list(filas), [], set()
    while pendientes:
        avance = [f for f in pendientes if all(f.get(c) in (None, f[pk]) or f.get(c) in vistos for c in columnas)]
        if not avance:
            raise ErrorMigracion(f"{tabla.name}: referencias circulares entre filas; revisa {', '.join(columnas)}.")
        for fila in avance:
            vistos.add(fila[pk])
        listas.extend(avance)
        pendientes = [f for f in pendientes if f[pk] not in vistos]
    return listas


def contar(motor: Engine, nombres: Iterable[str]) -> Dict[str, int]:
    """Número de filas de cada tabla indicada en la base `motor`."""
    tablas = Base.metadata.tables
    with motor.connect() as conexion:
        return {n: conexion.execute(select(func.count()).select_from(tablas[n])).scalar_one() for n in nombres}


def importar(motor: Engine, carpeta: Path, crear_tablas: bool = False) -> Dict[str, int]:
    """Carga un respaldo en una base vacía (todo o nada) y devuelve los conteos verificados.

    Con crear_tablas crea el esquema portable antes. Lanza ErrorMigracion si
    faltan tablas, el destino ya tiene filas o los conteos finales no coinciden.
    """
    manifiesto = leer_manifiesto(carpeta)
    if crear_tablas:
        metadata_portable().create_all(motor)
    existentes = {t.lower() for t in inspect(motor).get_table_names()}
    faltan = [n for n in manifiesto["orden"] if n not in existentes]
    if faltan:
        raise ErrorMigracion(f"El destino no tiene {', '.join(faltan)}: usa --crear-tablas o crea el esquema antes.")
    con_filas = [n for n, cantidad in contar(motor, manifiesto["orden"]).items() if cantidad]
    if con_filas:
        raise ErrorMigracion(
            f"El destino ya tiene filas en {', '.join(con_filas)}. Importar solo escribe en tablas vacías."
        )
    tablas = Base.metadata.tables
    with motor.begin() as conexion:  # todo o nada
        for nombre in manifiesto["orden"]:
            tabla = tablas[nombre]
            filas = list(_filas(carpeta / f"{nombre}.jsonl", tabla))
            filas = _en_orden_propio(tabla, filas)
            for inicio in range(0, len(filas), LOTE):
                conexion.execute(tabla.insert(), filas[inicio : inicio + LOTE])
    return verificar(motor, carpeta, manifiesto)


def verificar(motor: Engine, carpeta: Path, manifiesto: Optional[dict] = None) -> Dict[str, int]:
    """Compara las filas de cada tabla del destino con las del manifiesto; devuelve los conteos o lanza ErrorMigracion."""
    manifiesto = manifiesto or leer_manifiesto(carpeta)
    reales = contar(motor, manifiesto["orden"])
    distintas = [
        f"{n}: {reales[n]} en destino, {manifiesto['tablas'][n]['filas']} exportadas"
        for n in manifiesto["orden"]
        if reales[n] != manifiesto["tablas"][n]["filas"]
    ]
    if distintas:
        raise ErrorMigracion("Las filas no coinciden: " + "; ".join(distintas))
    return reales


# --- Control de migraciones (016) -----------------------------------------------


def scripts_sql(carpeta_sql: Optional[Path] = None) -> List[Dict[str, str]]:
    """Lista ordenada de scripts numerados (``001_*.sql`` .. ``016_*.sql``) y su SHA-256."""
    carpeta = carpeta_sql or CARPETA_SQL
    archivos = sorted(p for p in carpeta.glob("[0-9][0-9][0-9]_*.sql") if p.is_file())
    return [
        {
            "version": p.name[:3],
            "archivo": p.name,
            "sha256": _huella(p),
        }
        for p in archivos
    ]


def estado_migraciones(motor: Engine, carpeta_sql: Optional[Path] = None) -> dict:
    """Compara los scripts de ``sql/`` contra la tabla ``ESQUEMA_MIGRACIONES``."""
    scripts = scripts_sql(carpeta_sql)
    existentes = {t.lower() for t in inspect(motor).get_table_names()}
    if "esquema_migraciones" not in existentes:
        return {
            "tabla_existe": False,
            "total_scripts": len(scripts),
            "aplicadas": [],
            "pendientes": [s["version"] for s in scripts],
            "modificadas": [],
            "extras_en_db": [],
            "detalle": [
                {
                    "version": s["version"],
                    "archivo": s["archivo"],
                    "sha256_disco": s["sha256"],
                    "sha256_db": None,
                    "origen": None,
                    "estado": "pendiente",
                }
                for s in scripts
            ],
        }

    with Session(motor) as sesion:
        filas = sesion.scalars(select(EsquemaMigracion).order_by(EsquemaMigracion.version)).all()
    en_db = {fila.version: fila for fila in filas}

    aplicadas: List[str] = []
    pendientes: List[str] = []
    modificadas: List[str] = []
    detalle: List[Dict[str, Optional[str]]] = []

    for script in scripts:
        ver = script["version"]
        fila = en_db.get(ver)
        if fila is None:
            estado = "pendiente"
            pendientes.append(ver)
        elif fila.sha256 != script["sha256"]:
            estado = "modificada"
            modificadas.append(ver)
        else:
            estado = "aplicada"
            aplicadas.append(ver)
        detalle.append(
            {
                "version": ver,
                "archivo": script["archivo"],
                "sha256_disco": script["sha256"],
                "sha256_db": fila.sha256 if fila else None,
                "origen": fila.origen if fila else None,
                "estado": estado,
            }
        )

    versiones_disco = {s["version"] for s in scripts}
    extras = sorted(ver for ver in en_db if ver not in versiones_disco)
    return {
        "tabla_existe": True,
        "total_scripts": len(scripts),
        "aplicadas": aplicadas,
        "pendientes": pendientes,
        "modificadas": modificadas,
        "extras_en_db": extras,
        "detalle": detalle,
    }


def registrar_linea_base(
    motor: Engine,
    carpeta_sql: Optional[Path] = None,
    notas: str = "Registro inicial en produccion",
) -> List[str]:
    """Registra de forma idempotente los scripts aún ausentes con origen ``linea_base``."""
    scripts = scripts_sql(carpeta_sql)
    registradas: List[str] = []
    marca = ahora()
    nota_corta = notas[:255] if notas else None

    with Session(motor) as sesion:
        existentes = set(sesion.scalars(select(EsquemaMigracion.version)).all())
        for script in scripts:
            if script["version"] in existentes:
                continue
            sesion.add(
                EsquemaMigracion(
                    version=script["version"],
                    archivo=script["archivo"],
                    sha256=script["sha256"],
                    origen="linea_base",
                    notas=nota_corta,
                    aplicada_en=marca,
                )
            )
            registradas.append(script["version"])
        if registradas:
            sesion.commit()
    return registradas


# --- Terminal -------------------------------------------------------------------


def _motor(url: Optional[str]) -> Engine:
    """Engine de `url` si se indica; si no, el de la configuración del backend (backend/.env)."""
    if url:
        return create_engine(url)
    from database import conexion

    return conexion.motor()


def main(argumentos: Optional[Sequence[str]] = None) -> int:
    """CLI de migración: subcomandos ddl, exportar, importar, verificar y estado. Devuelve 0 o 1 ante ErrorMigracion.

    «estado --estricto» también devuelve 1 si hay migraciones pendientes o modificadas.
    """
    parser = argparse.ArgumentParser(description="Migrar Amatista a otra base de datos (formato " + FORMATO + ").")
    comandos = parser.add_subparsers(dest="comando", metavar="comando", required=True)

    p_ddl = comandos.add_parser("ddl", help="esquema para otra base (desde modelos.py)")
    p_ddl.add_argument("--dialecto", choices=DIALECTOS, default="postgresql")
    p_ddl.add_argument("--salida", type=Path, help="archivo .sql (por defecto, la pantalla)")

    p_exp = comandos.add_parser("exportar", help="copia la base a JSONL (solo lee)")
    p_exp.add_argument("carpeta", type=Path)
    p_exp.add_argument("--url", help="URL de SQLAlchemy del origen (por defecto, la de backend/.env)")

    p_imp = comandos.add_parser("importar", help="carga un respaldo JSONL en una base vacía")
    p_imp.add_argument("carpeta", type=Path)
    p_imp.add_argument("--url", required=True, help="URL de SQLAlchemy del destino")
    p_imp.add_argument("--crear-tablas", action="store_true", help="crea las tablas que falten antes de importar")

    p_ver = comandos.add_parser("verificar", help="compara filas del destino con el manifiesto")
    p_ver.add_argument("carpeta", type=Path)
    p_ver.add_argument("--url", required=True)

    p_est = comandos.add_parser("estado", help="compara sql/ contra ESQUEMA_MIGRACIONES")
    p_est.add_argument("--url", help="URL de SQLAlchemy (por defecto, la configuración actual)")
    p_est.add_argument("--json", action="store_true", help="imprime el estado en formato JSON")
    p_est.add_argument("--estricto", action="store_true", help="sale con código 1 si falta la tabla o hay pendientes/modificadas")

    args = parser.parse_args(argumentos)
    try:
        if args.comando == "ddl":
            texto = ddl(args.dialecto)
            if args.salida:
                args.salida.write_text(texto, "utf-8")
                print(f"Esquema para {args.dialecto} en {args.salida}")
            else:
                print(texto, end="")
        elif args.comando == "exportar":
            manifiesto = exportar(_motor(args.url), args.carpeta)
            total = sum(t["filas"] for t in manifiesto["tablas"].values())
            print(f"Exportadas {total} filas de {len(manifiesto['tablas'])} tablas a {args.carpeta}")
            if manifiesto["sin_tabla_en_origen"]:
                print("Sin tabla en el origen (se omiten): " + ", ".join(manifiesto["sin_tabla_en_origen"]))
        elif args.comando == "importar":
            filas = importar(_motor(args.url), args.carpeta, args.crear_tablas)
            print(f"Importadas {sum(filas.values())} filas en {len(filas)} tablas. Conteos verificados.")
        elif args.comando == "verificar":
            filas = verificar(_motor(args.url), args.carpeta)
            print(f"OK: {len(filas)} tablas con las mismas filas que el respaldo.")
        else:
            motor = _motor(args.url)
            try:
                resumen = estado_migraciones(motor)
            finally:
                if args.url:
                    motor.dispose()
            if args.json:
                print(json.dumps(resumen, ensure_ascii=False, indent=2))
            else:
                if not resumen["tabla_existe"]:
                    print("ESQUEMA_MIGRACIONES no existe aún; pendientes:", ", ".join(resumen["pendientes"]))
                else:
                    print(
                        f"Migraciones: {len(resumen['aplicadas'])}/{resumen['total_scripts']} aplicadas | "
                        f"pendientes={len(resumen['pendientes'])} | modificadas={len(resumen['modificadas'])}"
                    )
                    if resumen["pendientes"]:
                        print("  Pendientes:", ", ".join(resumen["pendientes"]))
                    if resumen["modificadas"]:
                        print("  Modificadas (SHA distinto):", ", ".join(resumen["modificadas"]))
                    if resumen["extras_en_db"]:
                        print("  Solo en DB:", ", ".join(resumen["extras_en_db"]))
            if args.estricto and (
                not resumen["tabla_existe"] or resumen["pendientes"] or resumen["modificadas"]
            ):
                return 1
    except ErrorMigracion as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
