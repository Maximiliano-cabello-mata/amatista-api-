import diagnostico_oracle
from diagnostico_oracle import (
    ESPERADO,
    comparar,
    obsoletas,
    resumir_espacio,
    revisar_email_unico,
    revisar_longitudes,
    solucion,
)


def esquema_correcto():
    return {tabla: dict(columnas) for tabla, columnas in ESPERADO.items()}


def esquema_001():
    """Lo que deja 001 sin 002: solo las tres tablas base, con sus columnas de entonces."""
    return {
        "USUARIOS": {"ID": "VARCHAR2", "NOMBRE": "VARCHAR2", "CREADO_EN": "TIMESTAMP(6)"},
        "SESIONES": {
            "ID": "VARCHAR2",
            "USUARIO_ID": "VARCHAR2",
            "DISPOSITIVO": "VARCHAR2",
            "ACTIVA": "NUMBER",
            "ULTIMO_ACCESO": "TIMESTAMP(6)",
        },
        "PROGRESO_LECCIONES": {
            "USUARIO_ID": "VARCHAR2",
            "CURSO_ID": "VARCHAR2",
            "LECCION_ID": "VARCHAR2",
            "COMPLETADA": "NUMBER",
            "PUNTAJE": "NUMBER",
            "INTENTOS": "NUMBER",
            "ACTUALIZADO_EN": "TIMESTAMP(6)",
        },
    }


def test_esquema_correcto_no_tiene_problemas():
    real = esquema_correcto()
    real["USUARIOS"]["CREADO_EN"] = "TIMESTAMP(6)"
    assert comparar(real) == []


def test_esperado_incluye_las_veinte_tablas():
    assert set(ESPERADO) == {
        "USUARIOS",
        "SESIONES",
        "PROGRESO_LECCIONES",
        "LOGROS",
        "EVENTOS_APRENDIZAJE",
        "CURSOS",
        "MODULOS",
        "LECCIONES",
        "NIVELES",
        "HABILIDADES",
        "HABILIDADES_ALUMNO",
        "EVALUACIONES_RUBRICA",
        "VERSIONES_BLENDER",
        "VERIFICACIONES_BLENDER",
        "ADDON_VINCULOS",
        "PRACTICAS",
        "PRACTICA_VERSIONES",
        "PROGRESO_PRACTICAS",
        "ADDON_ENLACES",
        "ADDON_AJUSTES",
    }
    assert ESPERADO["LECCIONES"]["CONTENIDO"] == "CLOB"
    assert ESPERADO["PRACTICAS"]["DEFINICION"] == "CLOB"
    assert ESPERADO["MODULOS"]["NIVEL_ID"] == "VARCHAR2"


def test_base_con_002_pide_005_y_006_sin_001():
    """Producción tiene 002 aplicado: falta solo lo de la reestructuración."""
    real = {
        tabla: {c: t for c, t in columnas.items() if (tabla, c) != ("MODULOS", "NIVEL_ID")}
        for tabla, columnas in ESPERADO.items()
        if tabla not in diagnostico_oracle.TABLAS_005
    }
    problemas = comparar(real)
    assert "Falta la tabla NIVELES." in problemas
    assert "Falta la columna MODULOS.NIVEL_ID." in problemas
    recomendacion = " ".join(solucion(real))
    assert "005_niveles_habilidades_versiones.sql" in recomendacion
    assert "006_herramientas_autor.sql" in recomendacion
    assert "NO ejecutes 001" in recomendacion
    # Si además falta algo de 002, primero va 002.
    del real["LOGROS"]
    assert "002_autenticacion_contenido_eventos.sql" in " ".join(solucion(real))


def test_base_con_005_pide_solo_007():
    """Producción con 005 y 006 aplicados: falta solo el motor de prácticas."""
    real = {tabla: dict(columnas) for tabla, columnas in ESPERADO.items() if tabla not in diagnostico_oracle.TABLAS_007}
    assert "Falta la tabla PRACTICAS." in comparar(real)
    recomendacion = " ".join(solucion(real))
    assert "007_motor_practicas.sql" in recomendacion
    assert "005_niveles" not in recomendacion
    assert "NO ejecutes 001" in recomendacion


def sin_columnas_008(real):
    for tabla, columna in diagnostico_oracle.COLUMNAS_008:
        real[tabla].pop(columna, None)
    return real


def test_produccion_actual_pide_007_y_008():
    """Producción del 03/10: 14 tablas (sin 007) y CURSOS sin las columnas de 008."""
    real = sin_columnas_008(
        {tabla: dict(columnas) for tabla, columnas in ESPERADO.items() if tabla not in diagnostico_oracle.TABLAS_007}
    )
    recomendacion = " ".join(solucion(real))
    assert "007_motor_practicas.sql" in recomendacion
    assert "008_cursos_por_ruta.sql" in recomendacion
    assert "002_" not in recomendacion


def test_base_con_007_pide_solo_008():
    real = sin_columnas_008({tabla: dict(columnas) for tabla, columnas in ESPERADO.items()})
    assert any("RUTA" in problema for problema in comparar(real))
    recomendacion = " ".join(solucion(real))
    assert "008_cursos_por_ruta.sql" in recomendacion
    assert "007_motor" not in recomendacion
    assert "NO ejecutes 001" in recomendacion


def test_base_con_009_pide_solo_010():
    """Producción con todo hasta 009: falta solo el enlace en vivo con Blender (motor 3.4)."""
    real = {tabla: dict(columnas) for tabla, columnas in ESPERADO.items() if tabla not in diagnostico_oracle.TABLAS_010}
    assert "Falta la tabla ADDON_ENLACES." in comparar(real)
    recomendacion = " ".join(solucion(real))
    assert "010_enlace_blender.sql" in recomendacion
    assert "007_motor" not in recomendacion and "008_" not in recomendacion
    assert "NO ejecutes 001" in recomendacion


def test_base_sin_005_pide_005_006_y_007():
    real = {
        tabla: dict(columnas)
        for tabla, columnas in ESPERADO.items()
        if tabla not in diagnostico_oracle.TABLAS_005 + diagnostico_oracle.TABLAS_007
    }
    recomendacion = " ".join(solucion(real))
    assert "005_niveles_habilidades_versiones.sql" in recomendacion
    assert "007_motor_practicas.sql" in recomendacion


def test_detecta_tabla_faltante_y_tipo_numerico():
    real = esquema_correcto()
    del real["PROGRESO_LECCIONES"]
    real["SESIONES"]["USUARIO_ID"] = "NUMBER"
    problemas = comparar(real)
    assert "Falta la tabla PROGRESO_LECCIONES." in problemas
    assert any("SESIONES.USUARIO_ID es NUMBER" in p and "ORA-01722" in p for p in problemas)


def test_base_de_001_pide_columnas_y_tablas_de_002():
    problemas = comparar(esquema_001())
    assert "Falta la columna USUARIOS.EMAIL." in problemas
    assert "Falta la columna SESIONES.EXPIRA_EN." in problemas
    assert "Falta la tabla EVENTOS_APRENDIZAJE." in problemas
    recomendacion = " ".join(solucion(esquema_001()))
    assert "002_autenticacion_contenido_eventos.sql" in recomendacion
    assert "NO ejecutes 001" in recomendacion


def test_base_vacia_recomienda_001_y_luego_002():
    recomendacion = " ".join(solucion({"DBTOOLS$EXECUTION_HISTORY": {"ID": "NUMBER"}}))
    assert "001_esquema_amatista.sql" in recomendacion
    assert "002_autenticacion_contenido_eventos.sql" in recomendacion


def test_tablas_en_otro_esquema_piden_db_esquema_y_no_001():
    recomendacion = " ".join(solucion({}, otros_esquemas=("ADMIN",)))
    assert "DB_ESQUEMA=ADMIN" in recomendacion
    assert "NO ejecutes 001" in recomendacion


def test_ids_numericos_del_diseno_anterior():
    real = esquema_001()
    real["USUARIOS"]["ID"] = "NUMBER"
    recomendacion = " ".join(solucion(real))
    assert "USUARIOS.ID" in recomendacion
    assert "diseño anterior" in recomendacion


def test_sesiones_id_de_36_no_alcanza_para_el_hash():
    problemas = revisar_longitudes({("SESIONES", "ID"): 36, ("USUARIOS", "ID"): 100})
    assert len(problemas) == 1
    assert "SESIONES.ID admite 36" in problemas[0] and "64" in problemas[0]
    assert revisar_longitudes({("SESIONES", "ID"): 64}) == []
    # NUMBER, TIMESTAMP y CLOB tienen CHAR_LENGTH 0: no se comparan.
    assert revisar_longitudes({("SESIONES", "ID"): 0}) == []


def test_falta_unique_de_email():
    real = esquema_correcto()
    assert revisar_email_unico(real, email_unico=True) == []
    problemas = revisar_email_unico(real, email_unico=False)
    assert len(problemas) == 1 and "uq_usuarios_email" in problemas[0]
    # Sin la columna aún, lo reporta comparar() como columna faltante.
    assert revisar_email_unico(esquema_001(), email_unico=False) == []


def test_resumen_de_espacio_contra_20_gb():
    mb = 1048576
    espacio = resumir_espacio(
        [("USUARIOS", 2 * mb), ("EVENTOS_APRENDIZAJE", 1024 * mb), ("EVENTOS_APRENDIZAJE", 1024 * mb), ("X", None)]
    )
    assert espacio["total_mb"] == 2050
    assert espacio["porcentaje"] == round(2050 * 100 / (20 * 1024), 3)
    assert espacio["mayores"][0] == ("EVENTOS_APRENDIZAJE", 2048)


def test_senala_tablas_obsoletas():
    real = esquema_correcto()
    real["SESIONES_WEB"] = {"ID": "VARCHAR2"}
    assert obsoletas(real) == ["SESIONES_WEB"]


def test_con_sqlite_avisa_que_es_para_oracle(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'diagnostico.db'}")
    assert diagnostico_oracle.main() == 1
    assert "es para Oracle" in capsys.readouterr().out


def test_base_con_010_pide_011_sin_recrear_tablas():
    real = esquema_correcto()
    del real['ADDON_ENLACES']['DETALLE']
    assert 'Falta la columna ADDON_ENLACES.DETALLE.' in comparar(real)
    assert '011_detalle_instructor.sql' in ' '.join(solucion(real))
    assert '001_esquema' not in ' '.join(solucion(real))
