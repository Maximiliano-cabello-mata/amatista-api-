"""Modelos, scripts SQL y diagnóstico describen las mismas tablas.

database/modelos.py es la fuente de verdad. Una columna nueva va también en
un script numerado de backend/sql/ y en ESPERADO de diagnostico_oracle.py
(ver sql/LEEME.txt): estas pruebas fallan si alguno queda atrás. No hay
Oracle en las pruebas, así que los scripts se leen como texto.
"""
import re
from pathlib import Path

import pytest
from sqlalchemy import TIMESTAMP, Integer, String, Text
from sqlalchemy.types import TypeDecorator

from database import conexion
from database.modelos import (
    CATEGORIAS_BLENDER,
    CRITERIOS_RUBRICA,
    ESTADOS_CONTENIDO,
    ESTADOS_HABILIDAD,
    ESTADOS_VINCULO,
    LOGROS_RUBRICA,
    ORIGENES_PRACTICA,
    RESULTADOS_VERIFICACION,
    ROLES,
    TIPOS_EVENTO,
    Base,
)
from diagnostico_oracle import ESPERADO, LONGITUDES_MINIMAS

CARPETA_SQL = Path(__file__).resolve().parent.parent / "sql"
SCRIPTS = sorted(CARPETA_SQL.glob("[0-9][0-9][0-9]_*.sql"))
TABLAS = {tabla.name.upper(): tabla for tabla in Base.metadata.sorted_tables}

# Un literal entre comillas simples de PL/SQL ('' es una comilla dentro).
LITERAL = r"'((?:[^']|'')*)'"
PATRON_TIPO = re.compile(r"^\s*(VARCHAR2|NUMBER|TIMESTAMP|CLOB)\b\s*(?:\(\s*(\d+))?", re.IGNORECASE)
PALABRAS_RESTRICCION = {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}


# --- Lectura de los scripts -------------------------------------------------


def texto(script: Path) -> str:
    return script.read_text(encoding="utf-8")


def sin_comentarios(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def script(prefijo: str) -> str:
    coincidencias = [s for s in SCRIPTS if s.name.startswith(prefijo)]
    assert len(coincidencias) == 1, f"Debe existir un solo script {prefijo}_*.sql"
    return texto(coincidencias[0])


def entre_parentesis(sql: str, inicio: int) -> str:
    """Contenido del paréntesis que abre en `inicio`, respetando anidados."""
    nivel = 0
    for i in range(inicio, len(sql)):
        if sql[i] == "(":
            nivel += 1
        elif sql[i] == ")":
            nivel -= 1
            if nivel == 0:
                return sql[inicio + 1 : i]
    raise AssertionError("Paréntesis sin cerrar en un CREATE TABLE")


def partes_nivel_superior(cuerpo: str):
    """Separa por comas que no estén dentro de paréntesis."""
    partes, nivel, actual = [], 0, []
    for caracter in cuerpo:
        if caracter == "(":
            nivel += 1
        elif caracter == ")":
            nivel -= 1
        if caracter == "," and nivel == 0:
            partes.append("".join(actual).strip())
            actual = []
        else:
            actual.append(caracter)
    partes.append("".join(actual).strip())
    return [p for p in partes if p]


def definicion_columna(definicion: str) -> dict:
    tipo = PATRON_TIPO.match(definicion)
    assert tipo, f"Tipo no reconocido: {definicion!r}"
    return {
        "tipo": tipo.group(1).upper(),
        "largo": int(tipo.group(2)) if tipo.group(2) else None,
        "not_null": "NOT NULL" in definicion.upper(),
    }


def esquema_de_scripts():
    """Estado final de 001, 002, 003... aplicados en orden.

    Devuelve (columnas, restricciones, indices):
      columnas: {"TABLA": {"COLUMNA": {"tipo", "largo", "not_null"}}}
      restricciones: [("TABLA", "NOMBRE", "definición")]
      indices: [("TABLA", ("COL1", "COL2"))]
    """
    columnas, restricciones, indices = {}, [], []
    for archivo in SCRIPTS:
        sql = sin_comentarios(texto(archivo))
        for creacion in re.finditer(r"CREATE\s+TABLE\s+(\w+)\s*\(", sql, re.IGNORECASE):
            tabla = creacion.group(1).upper()
            cuerpo = entre_parentesis(sql, creacion.end() - 1)
            for parte in partes_nivel_superior(cuerpo):
                palabra = parte.split()[0].upper()
                if palabra in PALABRAS_RESTRICCION:
                    nombre = re.match(r"CONSTRAINT\s+(\w+)\s+(.*)", parte, re.IGNORECASE | re.DOTALL)
                    if nombre:
                        restricciones.append((tabla, nombre.group(1).upper(), nombre.group(2)))
                    else:
                        restricciones.append((tabla, "", parte))
                else:
                    columna, definicion = parte.split(None, 1)
                    columnas.setdefault(tabla, {})[columna.upper()] = definicion_columna(definicion)
        for tabla, columna, definicion in re.findall(
            rf"agregar_columna\(\s*'(\w+)'\s*,\s*'(\w+)'\s*,\s*{LITERAL}\s*\)", sql
        ):
            columnas.setdefault(tabla.upper(), {})[columna.upper()] = definicion_columna(definicion)
        for tabla, columna, tipo in re.findall(rf"ampliar_columna\(\s*'(\w+)'\s*,\s*'(\w+)'\s*,\s*{LITERAL}\s*\)", sql):
            nuevo = definicion_columna(tipo)
            columnas[tabla.upper()][columna.upper()].update(tipo=nuevo["tipo"], largo=nuevo["largo"])
        for tabla, nombre, definicion in re.findall(
            rf"agregar_restriccion\(\s*'(\w+)'\s*,\s*'(\w+)'\s*,\s*{LITERAL}\s*\)", sql
        ):
            restricciones.append((tabla.upper(), nombre.upper(), definicion))
        for tabla, cols in re.findall(r"CREATE\s+INDEX\s+\w+\s+ON\s+(\w+)\s*\(([^)]*)\)", sql, re.IGNORECASE):
            indices.append((tabla.upper(), tuple(c.strip().upper() for c in cols.split(","))))
        for tabla, cols in re.findall(r"crear_indice\(\s*'\w+'\s*,\s*'(\w+)'\s*,\s*'([^']*)'", sql):
            indices.append((tabla.upper(), tuple(c.strip().upper() for c in cols.split(","))))
    return columnas, restricciones, indices


def columnas_de(definicion: str):
    return tuple(c.strip().upper() for c in definicion.split(","))


def tipo_oracle(columna) -> str:
    # Los tipos propios (TextoJSON) se guardan como su tipo base.
    tipo = columna.type.impl if isinstance(columna.type, TypeDecorator) else columna.type
    # Text hereda de String: se revisa primero.
    if isinstance(tipo, Text):
        return "CLOB"
    if isinstance(tipo, String):
        return "VARCHAR2"
    if isinstance(tipo, Integer):
        return "NUMBER"
    if isinstance(tipo, TIMESTAMP):
        return "TIMESTAMP"
    raise AssertionError(f"Tipo sin equivalente en Oracle: {columna.type!r}")


COLUMNAS_SQL, RESTRICCIONES_SQL, INDICES_SQL = esquema_de_scripts()


# --- Diagnóstico == modelos ---------------------------------------------------


def test_esperado_del_diagnostico_coincide_con_los_modelos():
    assert set(ESPERADO) == set(TABLAS)
    for nombre, tabla in TABLAS.items():
        modelo = {c.name.upper(): tipo_oracle(c) for c in tabla.columns}
        assert ESPERADO[nombre] == modelo, f"ESPERADO['{nombre}'] no coincide con modelos.py"


def test_longitudes_minimas_del_diagnostico_coinciden_con_los_modelos():
    for (tabla, columna), minimo in LONGITUDES_MINIMAS.items():
        assert TABLAS[tabla].columns[columna.lower()].type.length == minimo, f"{tabla}.{columna}"


# --- Scripts SQL == modelos ---------------------------------------------------


def test_los_scripts_crean_todas_las_tablas_y_columnas_de_los_modelos():
    assert set(COLUMNAS_SQL) == set(TABLAS)
    for nombre, tabla in TABLAS.items():
        modelo = {c.name.upper() for c in tabla.columns}
        assert set(COLUMNAS_SQL[nombre]) == modelo, f"Columnas de {nombre} en sql/ vs modelos.py"


def test_tipos_largos_y_nulos_coinciden_con_los_modelos():
    for nombre, tabla in TABLAS.items():
        for columna in tabla.columns:
            sql = COLUMNAS_SQL[nombre][columna.name.upper()]
            donde = f"{nombre}.{columna.name.upper()}"
            assert sql["tipo"] == tipo_oracle(columna), donde
            if sql["tipo"] == "VARCHAR2":
                assert sql["largo"] == columna.type.length, f"{donde}: largo"
            assert sql["not_null"] == (not columna.nullable), f"{donde}: NOT NULL"


def test_llaves_primarias_y_foraneas_coinciden_con_los_modelos():
    primarias = {
        tabla: columnas_de(re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", definicion, re.I).group(1))
        for tabla, _, definicion in RESTRICCIONES_SQL
        if re.search(r"PRIMARY\s+KEY", definicion, re.I)
    }
    # También llaves compuestas: FOREIGN KEY (curso_id, leccion_id) REFERENCES lecciones (...).
    foraneas = {
        (tabla, columna, referida.upper())
        for tabla, _, definicion in RESTRICCIONES_SQL
        for columnas, referida in re.findall(r"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)", definicion, re.I)
        for columna in columnas_de(columnas)
    }
    for nombre, tabla in TABLAS.items():
        assert primarias[nombre] == tuple(c.name.upper() for c in tabla.primary_key.columns), nombre
    modelo = {
        (nombre, fk.parent.name.upper(), fk.column.table.name.upper())
        for nombre, tabla in TABLAS.items()
        for fk in tabla.foreign_keys
    }
    assert foraneas == modelo


def test_email_unico_y_checks_con_las_constantes_de_los_modelos():
    definiciones = {nombre: definicion for _, nombre, definicion in RESTRICCIONES_SQL if nombre}
    assert re.fullmatch(r"UNIQUE\s*\(\s*email\s*\)", definiciones["UQ_USUARIOS_EMAIL"], re.I)

    def valores(nombre):
        return set(re.findall(r"'+(\w+)'+", definiciones[nombre]))

    assert valores("CK_USUARIOS_ROL") == set(ROLES)
    assert valores("CK_EVENTOS_TIPO") == set(TIPOS_EVENTO)
    for nombre in (
        "CK_CURSOS_ESTADO", "CK_MODULOS_ESTADO", "CK_LECCIONES_ESTADO", "CK_NIVELES_ESTADO", "CK_PRACTICAS_ESTADO",
    ):
        assert valores(nombre) == set(ESTADOS_CONTENIDO), nombre
    assert valores("CK_HAB_ALUMNO_ESTADO") == set(ESTADOS_HABILIDAD)
    assert valores("CK_RUBRICA_CRITERIO") == set(CRITERIOS_RUBRICA)
    assert valores("CK_RUBRICA_LOGRO") == set(LOGROS_RUBRICA)
    assert valores("CK_VERSIONES_CATEGORIA") == set(CATEGORIAS_BLENDER)
    assert valores("CK_VERIFICACIONES_RESULTADO") == set(RESULTADOS_VERIFICACION)
    assert valores("CK_VINCULOS_ESTADO") == set(ESTADOS_VINCULO)
    assert valores("CK_PRACTICAS_ORIGEN") == set(ORIGENES_PRACTICA)
    # La autonomía de una práctica es un estado de habilidad ya practicada.
    assert valores("CK_PROG_PRACTICAS_AUTONOMIA") == set(ESTADOS_HABILIDAD) - {"sin_practicar"}
    for nombre in ("CK_PRACTICAS_DEFINICION", "CK_PRAC_VERSIONES_DEFINICION", "CK_PROG_PRACTICAS_OBJETIVOS"):
        assert "IS JSON" in definiciones[nombre].upper(), nombre
    assert "IS JSON" in definiciones["CK_PROGRESO_DATOS_JSON"].upper()
    assert "IS JSON" in definiciones["CK_LECCIONES_CONTENIDO"].upper()


def test_indices_de_los_modelos_existen_y_ninguno_repite_pk_o_unique():
    indices = set(INDICES_SQL)
    for nombre, tabla in TABLAS.items():
        for indice in tabla.indexes:
            columnas = tuple(c.name.upper() for c in indice.columns)
            assert (nombre, columnas) in indices, f"Falta el índice {nombre}{columnas} en sql/"
    # Regla 2 del informe: Oracle ya indexa PRIMARY KEY y UNIQUE.
    for nombre, columnas in INDICES_SQL:
        assert columnas != tuple(c.name.upper() for c in TABLAS[nombre].primary_key.columns), nombre
        unicas = {(c.name.upper(),) for c in TABLAS[nombre].columns if c.unique}
        assert columnas not in unicas, f"Índice manual sobre una columna UNIQUE de {nombre}"


def test_la_verificacion_del_ultimo_script_espera_el_numero_correcto_de_columnas():
    """007 trae la verificación vigente de todas las tablas (sustituye a las de 002 y 005)."""
    esperadas = dict(
        re.findall(r"SELECT\s+'(\w+)'(?:\s+AS\s+tabla)?\s*,\s*(\d+)(?:\s+AS\s+esperadas)?\s+FROM\s+dual", script("007"), re.I)
    )
    assert {tabla: int(n) for tabla, n in esperadas.items()} == {
        nombre: len(tabla.columns) for nombre, tabla in TABLAS.items()
    }


def test_003_o_su_script_cuentan_filas_y_004_da_permisos_sobre_todas_las_tablas():
    # 003 ya se ejecutó en producción: las tablas nuevas se cuentan en la
    # verificación del script que las crea (005 o 007), sin editar 003.
    mantenimiento = sin_comentarios(script("003"))
    conteos = mantenimiento + sin_comentarios(script("005")) + sin_comentarios(script("007"))
    for nombre in TABLAS:
        assert re.search(rf"COUNT\(\*\)(\s+AS\s+\w+)?\s+FROM\s+{nombre}\b", conteos, re.I), nombre
    lista = re.search(r"ODCIVARCHAR2LIST\(([^)]*)\)", sin_comentarios(script("004")), re.I).group(1)
    assert set(re.findall(r"'(\w+)'", lista)) == set(TABLAS)
    assert re.search(r"CREATE\s+OR\s+REPLACE\s+PROCEDURE\s+amatista_purgar", mantenimiento, re.I)
    assert re.search(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+v_amatista_espacio", mantenimiento, re.I)


# --- Sintaxis y reglas de los scripts ----------------------------------------


@pytest.mark.parametrize("prefijo", ["002", "005", "006", "007"])
def test_los_scripts_incrementales_no_borran_datos(prefijo):
    codigo = sin_comentarios(script(prefijo))
    for patron in (r"\bDROP\s+(TABLE|COLUMN|PARTITION|INDEX|CONSTRAINT)\b", r"\bDELETE\s+FROM\b", r"\bTRUNCATE\b"):
        assert not re.search(patron, codigo, re.I), f"{prefijo} debe ser incremental: contiene {patron}"


@pytest.mark.parametrize("archivo", SCRIPTS, ids=lambda s: s.name)
def test_bloques_plsql_terminan_con_barra(archivo):
    """Cada bloque PL/SQL termina en END; seguido de una línea "/". Una "/"
    suelta fuera de un bloque repetiría la sentencia anterior."""
    dentro, anterior = False, ""
    inicio = re.compile(r"^(DECLARE|BEGIN|CREATE\s+OR\s+REPLACE\s+(PROCEDURE|FUNCTION|PACKAGE|TRIGGER))\b", re.I)
    for numero, linea in enumerate(sin_comentarios(texto(archivo)).splitlines(), start=1):
        if not dentro and inicio.match(linea):
            dentro = True
        if linea.strip() == "/":
            assert dentro, f"{archivo.name}:{numero}: '/' fuera de un bloque PL/SQL"
            assert re.fullmatch(r"END(\s+\w+)?;", anterior, re.I), f"{archivo.name}:{numero}: falta END; antes de '/'"
            dentro = False
        if linea.strip():
            anterior = linea.strip()
    assert not dentro, f"{archivo.name}: el último bloque PL/SQL no termina con '/'"


@pytest.mark.parametrize("archivo", SCRIPTS, ids=lambda s: s.name)
def test_comillas_y_sql_dinamico(archivo):
    codigo = sin_comentarios(texto(archivo))
    assert codigo.count("'") % 2 == 0, f"{archivo.name}: comillas simples sin cerrar"
    assert "&" not in texto(archivo), f"{archivo.name}: '&' abre variables de sustitución en Database Actions"
    for literal in re.findall(rf"EXECUTE\s+IMMEDIATE\s+{LITERAL}", codigo, re.I):
        assert not literal.rstrip().endswith(";"), f"{archivo.name}: EXECUTE IMMEDIATE con ';' (ORA-00911)"
    if archivo.name.startswith(("002", "005", "007")):
        # En 002, 005 y 007 todos los literales son DDL: ninguno puede terminar en ';'.
        assert not re.search(r";\s*'", codigo), f"{archivo.name}: un DDL termina con ';' dentro del literal"


# --- Conexión: DB_ESQUEMA y pool ------------------------------------------------


@pytest.fixture()
def entorno_oracle(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for variable in ("DB_ESQUEMA", "DB_POOL", "DB_POOL_EXTRA"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("DB_USER", "AMATISTA_APP")
    monkeypatch.setenv("DB_PASSWORD", "x")
    monkeypatch.setenv("DB_DSN", "tcps://adb.ejemplo.com:1522/servicio")
    return monkeypatch


@pytest.mark.parametrize("valor, esperado", [("", None), ("admin", "ADMIN"), (" ADMIN ", "ADMIN"), ("A$#_9", "A$#_9")])
def test_db_esquema_valido(entorno_oracle, valor, esperado):
    entorno_oracle.setenv("DB_ESQUEMA", valor)
    assert conexion.esquema_oracle() == esperado


@pytest.mark.parametrize("valor", ["ADMIN; DROP USER X", "1ADMIN", "AD MIN", '"ADMIN"', "A" * 129, "ADMIN\nX"])
def test_db_esquema_invalido_no_llega_a_oracle(entorno_oracle, valor):
    entorno_oracle.setenv("DB_ESQUEMA", valor)
    with pytest.raises(RuntimeError, match="DB_ESQUEMA"):
        conexion.esquema_oracle()


def test_pool_acotado_por_defecto_y_configurable(entorno_oracle):
    motor = conexion.crear_motor()
    assert (motor.pool.size(), motor.pool._max_overflow) == (5, 5)
    entorno_oracle.setenv("DB_POOL", "3")
    entorno_oracle.setenv("DB_POOL_EXTRA", "0")
    motor = conexion.crear_motor()
    assert (motor.pool.size(), motor.pool._max_overflow) == (3, 0)


@pytest.mark.parametrize("variable, valor", [("DB_POOL", "0"), ("DB_POOL", "muchas"), ("DB_POOL_EXTRA", "-1")])
def test_pool_invalido(entorno_oracle, variable, valor):
    entorno_oracle.setenv(variable, valor)
    with pytest.raises(RuntimeError, match=variable):
        conexion.crear_motor()


def test_cada_conexion_fija_el_esquema_dueno(entorno_oracle):
    class Cursor:
        def __init__(self, registro):
            self.registro = registro

        def execute(self, sql):
            self.registro.append(sql)

        def close(self):
            self.registro.append("cerrado")

    class ConexionFalsa:
        registro = []

        def cursor(self):
            return Cursor(self.registro)

    entorno_oracle.setenv("DB_ESQUEMA", "admin")
    motor = conexion.crear_motor()
    # insert=True: el primero en correr, antes de que SQLAlchemy lea el esquema.
    primero = list(motor.pool.dispatch.connect)[0]
    falsa = ConexionFalsa()
    primero(falsa, None)
    assert falsa.registro == ["ALTER SESSION SET CURRENT_SCHEMA = ADMIN", "cerrado"]

    entorno_oracle.delenv("DB_ESQUEMA")
    sin_esquema = conexion.crear_motor()
    assert all(getattr(f, "__name__", "") != "al_conectar" for f in sin_esquema.pool.dispatch.connect)
