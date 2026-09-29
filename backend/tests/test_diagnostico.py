from diagnostico_oracle import ESPERADO, comparar, obsoletas


def esquema_correcto():
    return {tabla: dict(columnas) for tabla, columnas in ESPERADO.items()}


def test_esquema_correcto_no_tiene_problemas():
    real = esquema_correcto()
    real["USUARIOS"]["CREADO_EN"] = "TIMESTAMP(6)"
    assert comparar(real) == []


def test_detecta_tabla_faltante_y_tipo_numerico():
    real = esquema_correcto()
    del real["PROGRESO_LECCIONES"]
    real["SESIONES"]["USUARIO_ID"] = "NUMBER"
    problemas = comparar(real)
    assert "Falta la tabla PROGRESO_LECCIONES." in problemas
    assert any("SESIONES.USUARIO_ID es NUMBER" in p and "ORA-01722" in p for p in problemas)


def test_senala_tablas_obsoletas():
    real = esquema_correcto()
    real["SESIONES_WEB"] = {"ID": "VARCHAR2"}
    assert obsoletas(real) == ["SESIONES_WEB"]
