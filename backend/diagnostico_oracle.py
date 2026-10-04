"""Diagnóstico de la base de datos de Amatista.

Uso, en el servidor y con el entorno virtual activo:

    cd ~/amatista/backend
    source venv/bin/activate
    python diagnostico_oracle.py

Revisa la conexión, muestra con qué usuario y esquema entraste, compara las
tablas reales con las que espera el backend (database/modelos.py), cuenta
filas y muestra el espacio usado contra los 20 GB del Free Tier. Funciona
también con el usuario de aplicación AMATISTA_APP (DB_ESQUEMA=ADMIN): lee
ALL_TAB_COLUMNS del esquema actual. No modifica nada.
"""
import sys

from sqlalchemy import text

from database.conexion import crear_motor

# Tablas, columnas y tipos que espera el backend: database/modelos.py.
# sql/001 crea las tres primeras, sql/002 las cinco siguientes y sql/005 las de
# la reestructuración por niveles (más MODULOS.NIVEL_ID) y sql/007 las del motor
# de prácticas de Blender.
# tests/test_esquema.py comprueba que esta lista no se desalinee.
ESPERADO = {
    "USUARIOS": {
        "ID": "VARCHAR2",
        "NOMBRE": "VARCHAR2",
        "EMAIL": "VARCHAR2",
        "TELEFONO": "VARCHAR2",
        "ROL": "VARCHAR2",
        "PASSWORD_HASH": "VARCHAR2",
        "CORREO_CONFIRMADO": "NUMBER",
        "CODIGO_VERIFICACION": "VARCHAR2",
        "CODIGO_PROPOSITO": "VARCHAR2",
        "CODIGO_EXPIRA": "TIMESTAMP",
        "CODIGO_INTENTOS": "NUMBER",
        "INTENTOS_FALLIDOS": "NUMBER",
        "BLOQUEADO_HASTA": "TIMESTAMP",
        "ES_PRUEBA": "NUMBER",
        "FUSIONADO_EN": "VARCHAR2",
        "CREADO_EN": "TIMESTAMP",
        "ULTIMO_ACCESO": "TIMESTAMP",
    },
    "SESIONES": {
        "ID": "VARCHAR2",
        "USUARIO_ID": "VARCHAR2",
        "DISPOSITIVO": "VARCHAR2",
        "ACTIVA": "NUMBER",
        "CREADO_EN": "TIMESTAMP",
        "ULTIMO_ACCESO": "TIMESTAMP",
        "EXPIRA_EN": "TIMESTAMP",
    },
    "PROGRESO_LECCIONES": {
        "USUARIO_ID": "VARCHAR2",
        "CURSO_ID": "VARCHAR2",
        "LECCION_ID": "VARCHAR2",
        "COMPLETADA": "NUMBER",
        "PUNTAJE": "NUMBER",
        "INTENTOS": "NUMBER",
        "DATOS_LIGEROS": "VARCHAR2",
        "COMPLETADA_EN": "TIMESTAMP",
        "ACTUALIZADO_EN": "TIMESTAMP",
    },
    "LOGROS": {
        "USUARIO_ID": "VARCHAR2",
        "INSIGNIA_ID": "VARCHAR2",
        "OBTENIDO_EN": "TIMESTAMP",
    },
    "EVENTOS_APRENDIZAJE": {
        "ID": "VARCHAR2",
        "USUARIO_ID": "VARCHAR2",
        "TIPO": "VARCHAR2",
        "CURSO_ID": "VARCHAR2",
        "LECCION_ID": "VARCHAR2",
        "SESION_APRENDIZAJE": "VARCHAR2",
        "OCURRIDO_EN": "TIMESTAMP",
        "RECIBIDO_EN": "TIMESTAMP",
        "VERSION_APP": "VARCHAR2",
        "ES_PRUEBA": "NUMBER",
        "DATOS": "VARCHAR2",
    },
    "CURSOS": {
        "ID": "VARCHAR2",
        "NUMERO": "VARCHAR2",
        "TITULO": "VARCHAR2",
        "SUBTITULO": "VARCHAR2",
        "DESCRIPCION": "VARCHAR2",
        "NIVEL": "VARCHAR2",
        "ACENTO": "VARCHAR2",
        "RECURSO_TEXTO": "VARCHAR2",
        "RECURSO_URL": "VARCHAR2",
        "ORDEN": "NUMBER",
        "ESTADO": "VARCHAR2",
        "ACTUALIZADO_EN": "TIMESTAMP",
    },
    "MODULOS": {
        "ID": "VARCHAR2",
        "CURSO_ID": "VARCHAR2",
        "NUMERO": "NUMBER",
        "TITULO": "VARCHAR2",
        "DESCRIPCION": "VARCHAR2",
        "INSIGNIA": "VARCHAR2",
        "MINUTOS": "NUMBER",
        "ESTADO": "VARCHAR2",
        "VERSION": "NUMBER",
        "ACTUALIZADO_EN": "TIMESTAMP",
        "PUBLICADO_EN": "TIMESTAMP",
        "NIVEL_ID": "VARCHAR2",
    },
    "LECCIONES": {
        "CURSO_ID": "VARCHAR2",
        "ID": "VARCHAR2",
        "MODULO_ID": "VARCHAR2",
        "ORDEN": "NUMBER",
        "TITULO": "VARCHAR2",
        "TIPO": "VARCHAR2",
        "DURACION_SEGUNDOS": "NUMBER",
        "BLOQUEADA": "NUMBER",
        "CONTENIDO": "CLOB",
        "REEMPLAZA": "VARCHAR2",
        "ESTADO": "VARCHAR2",
        "VERSION": "NUMBER",
        "ACTUALIZADO_EN": "TIMESTAMP",
        "PUBLICADO_EN": "TIMESTAMP",
    },
    # --- 005: reestructuración por niveles (v3.0.0) ---
    "NIVELES": {
        "ID": "VARCHAR2",
        "CURSO_ID": "VARCHAR2",
        "NUMERO": "NUMBER",
        "RAMA": "VARCHAR2",
        "TITULO": "VARCHAR2",
        "PERFIL": "VARCHAR2",
        "PROYECTO": "VARCHAR2",
        "CRITERIO_SALIDA": "VARCHAR2",
        "ESTADO": "VARCHAR2",
        "ACTUALIZADO_EN": "TIMESTAMP",
    },
    "HABILIDADES": {
        "ID": "VARCHAR2",
        "CURSO_ID": "VARCHAR2",
        "NIVEL_ID": "VARCHAR2",
        "NOMBRE": "VARCHAR2",
        "DESCRIPCION": "VARCHAR2",
        "ORDEN": "NUMBER",
        "ACTUALIZADO_EN": "TIMESTAMP",
    },
    "HABILIDADES_ALUMNO": {
        "USUARIO_ID": "VARCHAR2",
        "HABILIDAD_ID": "VARCHAR2",
        "ESTADO": "VARCHAR2",
        "EVIDENCIA": "VARCHAR2",
        "ACTUALIZADO_EN": "TIMESTAMP",
    },
    "EVALUACIONES_RUBRICA": {
        "USUARIO_ID": "VARCHAR2",
        "NIVEL_ID": "VARCHAR2",
        "CRITERIO": "VARCHAR2",
        "LOGRO": "VARCHAR2",
        "EVIDENCIA": "VARCHAR2",
        "COMENTARIO": "VARCHAR2",
        "REVISADO_POR": "VARCHAR2",
        "ACTUALIZADO_EN": "TIMESTAMP",
    },
    "VERSIONES_BLENDER": {
        "VERSION": "VARCHAR2",
        "CATEGORIA": "VARCHAR2",
        "ES_LTS": "NUMBER",
        "SOPORTE_HASTA": "TIMESTAMP",
        "NOTAS": "VARCHAR2",
        "ACTUALIZADO_EN": "TIMESTAMP",
    },
    "VERIFICACIONES_BLENDER": {
        "ID": "VARCHAR2",
        "CURSO_ID": "VARCHAR2",
        "LECCION_ID": "VARCHAR2",
        "VERSION_BLENDER": "VARCHAR2",
        "SISTEMA": "VARCHAR2",
        "VERSION_LECCION": "NUMBER",
        "VERSION_ADDON": "VARCHAR2",
        "RESULTADO": "VARCHAR2",
        "DIFERENCIAS": "VARCHAR2",
        "EVIDENCIA": "VARCHAR2",
        "RESPONSABLE": "VARCHAR2",
        "VERIFICADO_EN": "TIMESTAMP",
    },
    # 007: motor de prácticas de Blender.
    "ADDON_VINCULOS": {
        "ID": "VARCHAR2",
        "CODIGO": "VARCHAR2",
        "SECRETO_HASH": "VARCHAR2",
        "USUARIO_ID": "VARCHAR2",
        "DISPOSITIVO": "VARCHAR2",
        "ESTADO": "VARCHAR2",
        "CREADO_EN": "TIMESTAMP",
        "EXPIRA_EN": "TIMESTAMP",
    },
    "PRACTICAS": {
        "ID": "VARCHAR2",
        "CURSO_ID": "VARCHAR2",
        "LECCION_ID": "VARCHAR2",
        "TITULO": "VARCHAR2",
        "NIVEL": "NUMBER",
        "VERSION": "NUMBER",
        "VERSION_PUBLICADA": "NUMBER",
        "DEFINICION": "CLOB",
        "ESTADO": "VARCHAR2",
        "ORIGEN": "VARCHAR2",
        "AUTOR_ID": "VARCHAR2",
        "ACTUALIZADO_EN": "TIMESTAMP",
        "PUBLICADO_EN": "TIMESTAMP",
    },
    "PRACTICA_VERSIONES": {
        "PRACTICA_ID": "VARCHAR2",
        "VERSION": "NUMBER",
        "DEFINICION": "CLOB",
        "HUELLA": "VARCHAR2",
        "NOTA": "VARCHAR2",
        "AUTOR_ID": "VARCHAR2",
        "VERSION_ADDON": "VARCHAR2",
        "VERSION_BLENDER": "VARCHAR2",
        "CREADO_EN": "TIMESTAMP",
    },
    "PROGRESO_PRACTICAS": {
        "USUARIO_ID": "VARCHAR2",
        "PRACTICA_ID": "VARCHAR2",
        "VERSION": "NUMBER",
        "PROGRESO": "NUMBER",
        "COMPLETADA": "NUMBER",
        "AUTONOMIA": "VARCHAR2",
        "PISTAS": "NUMBER",
        "CORRECCIONES": "NUMBER",
        "INTENTOS": "NUMBER",
        "PASO_ACTUAL": "VARCHAR2",
        "OBJETIVOS": "VARCHAR2",
        "VERSION_BLENDER": "VARCHAR2",
        "VERSION_ADDON": "VARCHAR2",
        "ABIERTA_EN": "TIMESTAMP",
        "COMPLETADA_EN": "TIMESTAMP",
        "ACTUALIZADO_EN": "TIMESTAMP",
    },
}

# Las tablas que crea 001; las demás llegan con 002, 005 y 007.
TABLAS_BASE = ("USUARIOS", "SESIONES", "PROGRESO_LECCIONES")
# Lo que agrega 005 (reestructuración por niveles): tablas y columnas.
TABLAS_005 = (
    "NIVELES", "HABILIDADES", "HABILIDADES_ALUMNO", "EVALUACIONES_RUBRICA", "VERSIONES_BLENDER",
    "VERIFICACIONES_BLENDER",
)
COLUMNAS_005 = {("MODULOS", "NIVEL_ID")}
# Lo que agrega 007 (motor de prácticas de Blender): solo tablas nuevas.
TABLAS_007 = ("ADDON_VINCULOS", "PRACTICAS", "PRACTICA_VERSIONES", "PROGRESO_PRACTICAS")

# Largos mínimos (en caracteres) que causarían ORA-12899 si fueran menores.
# SESIONES.ID guarda el hash SHA-256 del token: 64 caracteres (001 lo creó con 36).
LONGITUDES_MINIMAS = {
    ("SESIONES", "ID"): 64,
    ("USUARIOS", "ID"): 100,
    ("SESIONES", "USUARIO_ID"): 100,
    ("PROGRESO_LECCIONES", "USUARIO_ID"): 100,
    ("USUARIOS", "PASSWORD_HASH"): 255,
    ("USUARIOS", "CODIGO_VERIFICACION"): 64,
}

# Tablas de versiones anteriores que el script SQL elimina.
OBSOLETAS = {"SESIONES_WEB"}

LIMITE_FREE_TIER_MB = 20 * 1024  # 20 GB de Autonomous Database Free Tier
AVISO_ESPACIO = 70  # % a partir del cual conviene bajar la retención de eventos

SCRIPT_001 = "sql/001_esquema_amatista.sql"
SCRIPT_002 = "sql/002_autenticacion_contenido_eventos.sql"
SCRIPT_003 = "sql/003_mantenimiento.sql"
SCRIPT_005 = "sql/005_niveles_habilidades_versiones.sql"
SCRIPT_006 = "sql/006_herramientas_autor.sql"
SCRIPT_007 = "sql/007_motor_practicas.sql"

# Fragmentos de errores comunes y qué significan.
PISTAS = [
    ("ORA-01017", "Usuario o contraseña incorrectos (DB_USER / DB_PASSWORD)."),
    ("ORA-12506", "Oracle rechazó la conexión por la lista de acceso (ACL): solo acepta la IP del "
                  "servidor ARM. Ejecuta esto desde el servidor o agrega tu IP a la ACL en OCI."),
    ("ORA-12514", "El servicio no existe: revisa DB_SERVICE o copia en DB_DSN la cadena TLS de la consola."),
    ("DPY-6005", "No se pudo abrir la conexión: revisa host, puerto 1522 y la ACL de la base."),
    ("DPY-6000", "No se pudo abrir la conexión: revisa host, puerto 1522 y la ACL de la base."),
    ("timed out", "La conexión tardó demasiado: suele ser la ACL de Oracle o un firewall."),
    ("Connection reset", "Se intentó TCP sin cifrar: Autonomous Database exige tcps en el puerto 1522."),
    ("ORA-28759", "Falla de TLS o wallet: si la base exige mTLS, desactívalo (TLS con ACL) o usa el wallet."),
    ("ORA-01435", "El esquema de DB_ESQUEMA no existe: revisa el nombre (por ejemplo DB_ESQUEMA=ADMIN)."),
    ("ORA-28001", "La contraseña venció: cámbiala en Database Actions (ALTER USER ... IDENTIFIED BY) y en backend/.env."),
    ("DB_ESQUEMA", "Corrige DB_ESQUEMA en backend/.env o déjalo vacío si DB_USER es el dueño de las tablas."),
]

# Solo tablas: ALL_TAB_COLUMNS también lista las columnas de las vistas
# (por ejemplo V_AMATISTA_ESPACIO de sql/003).
SQL_COLUMNAS = text(
    "SELECT c.table_name, c.column_name, c.data_type, c.char_length FROM all_tab_columns c "
    "JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name "
    "WHERE c.owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') "
    "ORDER BY c.table_name, c.column_id"
)

# Restricciones UNIQUE (o PK) que incluyen USUARIOS.EMAIL.
SQL_EMAIL_UNICO = text(
    "SELECT COUNT(*) FROM all_constraints c "
    "JOIN all_cons_columns cc ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name "
    "WHERE c.owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AND c.table_name = 'USUARIOS' "
    "AND c.constraint_type IN ('U', 'P') AND cc.column_name = 'EMAIL'"
)

# Si en el esquema actual no hay tablas, ¿están en otro? (falta DB_ESQUEMA)
SQL_OTRO_ESQUEMA = text(
    "SELECT owner FROM all_tables WHERE table_name = 'USUARIOS' "
    "AND owner <> SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') ORDER BY owner"
)


def comparar(real):
    """Compara las tablas reales con las esperadas.

    real: {"TABLA": {"COLUMNA": "TIPO"}} tal como sale de ALL_TAB_COLUMNS.
    Devuelve la lista de problemas (vacía si todo coincide).
    """
    problemas = []
    for tabla, columnas in ESPERADO.items():
        if tabla not in real:
            problemas.append(f"Falta la tabla {tabla}.")
            continue
        for columna, tipo in columnas.items():
            tipo_real = real[tabla].get(columna)
            if tipo_real is None:
                problemas.append(f"Falta la columna {tabla}.{columna}.")
            elif not tipo_real.startswith(tipo):
                aviso = f"{tabla}.{columna} es {tipo_real} y debería ser {tipo}."
                if tipo == "VARCHAR2" and tipo_real.startswith("NUMBER"):
                    aviso += " Guardar ahí un correo o un UUID provoca ORA-01722 (invalid number)."
                problemas.append(aviso)
    return problemas


def revisar_longitudes(longitudes):
    """longitudes: {("TABLA", "COLUMNA"): largo en caracteres} (CHAR_LENGTH)."""
    problemas = []
    for (tabla, columna), minimo in LONGITUDES_MINIMAS.items():
        largo = longitudes.get((tabla, columna))
        if largo and largo < minimo:
            aviso = f"{tabla}.{columna} admite {largo} caracteres y necesita {minimo}."
            if (tabla, columna) == ("SESIONES", "ID"):
                aviso += " Ahí se guarda el hash SHA-256 del token (64): iniciar sesión fallaría con ORA-12899."
            problemas.append(aviso)
    return problemas


def revisar_email_unico(real, email_unico):
    """La restricción UNIQUE de USUARIOS.EMAIL impide dos cuentas con el mismo correo."""
    if "EMAIL" in real.get("USUARIOS", {}) and not email_unico:
        return [
            "Falta la restricción UNIQUE de USUARIOS.EMAIL (uq_usuarios_email): "
            "dos cuentas podrían registrarse con el mismo correo."
        ]
    return []


def obsoletas(real):
    return sorted(tabla for tabla in real if tabla.upper() in OBSOLETAS)


def ids_numericos(real):
    """Columnas de id que deberían ser texto y son NUMBER (diseño del 27/09)."""
    return [
        f"{tabla}.{columna}"
        for tabla in TABLAS_BASE
        for columna, tipo in ESPERADO[tabla].items()
        if columna.endswith("ID") and tipo == "VARCHAR2"
        and str(real.get(tabla, {}).get(columna, "")).startswith("NUMBER")
    ]


def _completo_salvo(real, tablas, columnas_extra=frozenset()):
    for tabla, columnas in ESPERADO.items():
        if tabla in tablas:
            continue
        if tabla not in real:
            return False
        for columna in columnas:
            if (tabla, columna) not in columnas_extra and columna not in real[tabla]:
                return False
    return True


def solo_falta_005(real):
    """True si lo de 002 está completo y lo único que falta es de 005 (y 007)."""
    return _completo_salvo(real, TABLAS_005 + TABLAS_007, COLUMNAS_005)


def solo_falta_007(real):
    """True si todo hasta 005 está y solo faltan las tablas del motor (007)."""
    return _completo_salvo(real, TABLAS_007)


def solucion(real, otros_esquemas=()):
    """Qué hacer según lo que falta. Nunca recomienda 001 si ya hay tablas con datos."""
    if not any(tabla in real for tabla in TABLAS_BASE):
        if otros_esquemas:
            propietario = otros_esquemas[0]
            return [
                f"Las tablas de Amatista están en el esquema {propietario}, no en el actual.",
                f"Define DB_ESQUEMA={propietario} en backend/.env (o conéctate como {propietario}). "
                "NO ejecutes 001: borraría a los alumnos.",
            ]
        return [
            "Base vacía: en Database Actions > SQL ejecuta con «Ejecutar script» (F5), en orden,",
            f"{SCRIPT_001}, {SCRIPT_002}, {SCRIPT_003}, {SCRIPT_005}, {SCRIPT_006} y {SCRIPT_007} (ver sql/LEEME.txt).",
        ]
    numericos = ids_numericos(real)
    if numericos:
        return [
            f"{', '.join(numericos)} es NUMBER: son tablas del diseño anterior (ids numéricos).",
            "Sin alumnos reales: ejecuta 001 y luego 002 y 003. Con alumnos reales: respalda antes",
            "y sigue sql/LEEME.txt, sección «Base del diseño anterior».",
        ]
    if solo_falta_007(real):
        return [
            f"Falta el motor de prácticas de Blender: ejecuta {SCRIPT_007} en Database Actions > SQL",
            "con «Ejecutar script» (F5). Solo crea tablas nuevas, no borra datos y se puede repetir",
            "(guía: docs/reestructuracion/02_manual_oracle.md, sección 7). NO ejecutes 001: borraría a los alumnos.",
        ]
    if solo_falta_005(real):
        return [
            f"Falta la reestructuración por niveles: ejecuta {SCRIPT_005}, {SCRIPT_006} y {SCRIPT_007}",
            "en Database Actions > SQL con «Ejecutar script» (F5). Solo agregan, no borran datos y se pueden",
            "repetir (guía: docs/reestructuracion/02_manual_oracle.md). NO ejecutes 001: borraría a los alumnos.",
        ]
    return [
        f"Ejecuta {SCRIPT_002} en Database Actions > SQL con «Ejecutar script» (F5).",
        "Solo agrega lo que falta, no borra datos y se puede repetir. NO ejecutes 001: borraría a los alumnos.",
    ]


def resumir_espacio(segmentos):
    """segmentos: [(nombre, bytes)] → {"total_mb", "porcentaje", "mayores": [(nombre, mb)]}."""
    por_segmento = {}
    for nombre, cantidad in segmentos:
        por_segmento[nombre] = por_segmento.get(nombre, 0) + int(cantidad or 0)
    total_mb = sum(por_segmento.values()) / 1048576
    mayores = sorted(por_segmento.items(), key=lambda par: par[1], reverse=True)[:5]
    return {
        "total_mb": round(total_mb, 2),
        "porcentaje": round(total_mb * 100 / LIMITE_FREE_TIER_MB, 3),
        "mayores": [(nombre, round(cantidad / 1048576, 2)) for nombre, cantidad in mayores],
    }


def consultar_opcional(motor, consulta, **parametros):
    """Consulta informativa: si falla (por ejemplo, sin permiso) devuelve None."""
    try:
        with motor.connect() as conexion:
            return conexion.execute(consulta, parametros).all()
    except Exception:  # noqa: BLE001 - lo informativo nunca detiene el diagnóstico
        return None


def leer_espacio(motor, usuario, esquema):
    """Segmentos del esquema. El dueño usa USER_SEGMENTS; otro usuario
    (AMATISTA_APP) necesita DBA_SEGMENTS, que puede no tener permitido."""
    if usuario == esquema:
        return consultar_opcional(motor, text("SELECT segment_name, bytes FROM user_segments"))
    return consultar_opcional(
        motor,
        text("SELECT segment_name, bytes FROM dba_segments WHERE owner = :esquema"),
        esquema=esquema,
    )


def contar_filas(motor, tablas):
    """Filas por tabla. Los nombres salen de ESPERADO, nunca de la entrada del usuario."""
    filas = {}
    for tabla in tablas:
        resultado = consultar_opcional(motor, text(f"SELECT COUNT(*) FROM {tabla}"))
        filas[tabla] = resultado[0][0] if resultado else None
    return filas


def mostrar_mantenimiento(motor, usuario, esquema):
    """Avisos sobre 003 (particiones y job). Solo el dueño puede verlos."""
    particionada = consultar_opcional(
        motor,
        text("SELECT COUNT(*) FROM all_part_tables WHERE owner = :esquema AND table_name = 'EVENTOS_APRENDIZAJE'"),
        esquema=esquema,
    )
    if particionada is not None:
        if particionada[0][0]:
            print("  · EVENTOS_APRENDIZAJE está particionada por mes (la purga quita meses completos).")
        else:
            print("  · EVENTOS_APRENDIZAJE no está particionada: la purga borra con DELETE por lotes.")
    if usuario != esquema:
        return
    job = consultar_opcional(
        motor,
        text("SELECT state, next_run_date FROM user_scheduler_jobs WHERE job_name = 'AMATISTA_PURGA_DIARIA'"),
    )
    if job == []:
        print(f"  · Falta la purga diaria: ejecuta {SCRIPT_003} (no borra alumnos).")
    elif job:
        estado, siguiente = job[0]
        print(f"  · Purga diaria: {estado}, siguiente ejecución {siguiente}.")


def main() -> int:
    try:
        motor = crear_motor()
        if motor.dialect.name != "oracle":
            print(f"Este diagnóstico es para Oracle y la conexión configurada es {motor.dialect.name}.")
            print("Quita DATABASE_URL de backend/.env para usar Oracle.")
            return 1
        with motor.connect() as conexion:
            usuario, esquema = conexion.execute(
                text("SELECT USER, SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL")
            ).one()
            version = getattr(conexion.connection.driver_connection, "version", "?")
            columnas = conexion.execute(SQL_COLUMNAS).all()
    except Exception as error:  # noqa: BLE001 - se muestra cualquier falla de conexión
        print("✗ No se pudo conectar a Oracle.")
        print(f"  {error}")
        for fragmento, pista in PISTAS:
            if fragmento.lower() in str(error).lower():
                print(f"  → {pista}")
        return 1

    print(f"✓ Conectado a Oracle {version} como {usuario} (esquema {esquema}).")

    real, longitudes = {}, {}
    for tabla, columna, tipo, largo in columnas:
        real.setdefault(tabla, {})[columna] = tipo
        longitudes[(tabla, columna)] = largo

    propias = [t for t in real if not t.startswith("DBTOOLS$")]
    print(f"  Tablas en el esquema: {', '.join(sorted(propias)) or '(ninguna)'}")
    for tabla in obsoletas(real):
        print(f"  · {tabla} es de una versión anterior y ya no se usa.")

    otros = ()
    if not any(tabla in real for tabla in TABLAS_BASE):
        otros = tuple(fila[0] for fila in consultar_opcional(motor, SQL_OTRO_ESQUEMA) or [])

    email_unico = bool((consultar_opcional(motor, SQL_EMAIL_UNICO) or [[0]])[0][0])
    problemas = comparar(real) + revisar_longitudes(longitudes) + revisar_email_unico(real, email_unico)

    existentes = [tabla for tabla in ESPERADO if tabla in real]
    if existentes:
        print("  Filas por tabla:")
        for tabla, cantidad in contar_filas(motor, existentes).items():
            print(f"    {tabla:<22} {cantidad if cantidad is not None else '?':>10}")

    segmentos = leer_espacio(motor, usuario, esquema)
    if segmentos is None:
        print(f"  · No se pudo leer el espacio usado (permiso). Ejecuta el diagnóstico como {esquema}")
        print("    o consulta V_AMATISTA_ESPACIO en Database Actions.")
    else:
        espacio = resumir_espacio(segmentos)
        print(
            f"  Espacio del esquema: {espacio['total_mb']} MB de {LIMITE_FREE_TIER_MB // 1024} GB "
            f"({espacio['porcentaje']} %)."
        )
        for nombre, mb in espacio["mayores"]:
            print(f"    {nombre:<30} {mb:>10} MB")
        if espacio["porcentaje"] >= AVISO_ESPACIO:
            print(f"  ⚠ Pasa del {AVISO_ESPACIO} %: baja la retención de eventos (sql/LEEME.txt, sección 7).")

    if "EVENTOS_APRENDIZAJE" in real:
        mostrar_mantenimiento(motor, usuario, esquema)

    if problemas:
        print("✗ Las tablas no coinciden con lo que espera el backend:")
        for problema in problemas:
            print(f"  - {problema}")
        for linea in solucion(real, otros):
            print(f"→ {linea}")
        return 2

    print("✓ Las tablas coinciden con lo que espera el backend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
