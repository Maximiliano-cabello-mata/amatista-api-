-- =============================================================================
-- AMATISTA · 016 · REGISTRO DE MIGRACIONES Y PERMISOS COMPLETOS AMATISTA_APP
-- (Oracle Autonomous Database ATP · Free Tier de 20 GB)
--
-- Crea la tabla ESQUEMA_MIGRACIONES para llevar control con SHA-256 de los
-- scripts aplicados (diagnostico + migrar.py estado) y completa los permisos
-- pendientes de AMATISTA_APP sobre las tablas de 014 y esta tabla.
--
-- Script INCREMENTAL e IDEMPOTENTE:
--   - NO borra tablas ni filas: solo CREATE y GRANT.
--   - Se puede repetir en Database Actions > SQL (F5) o vía aplicar_016_oracle.py.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta      PLS_INTEGER;
  v_pendientes  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST(
                  'GRUPOS', 'GRUPO_ALUMNOS', 'ASIGNACIONES', 'ENTREGAS',
                  'ESQUEMA_MIGRACIONES');

  PROCEDURE ejecutar(p_sql IN VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
    DBMS_OUTPUT.PUT_LINE('OK: ' || SUBSTR(REGEXP_REPLACE(p_sql, '\s+', ' '), 1, 200));
  END;

  FUNCTION existe_tabla(p_tabla IN VARCHAR2) RETURN BOOLEAN IS
    v_n PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_n FROM user_tables WHERE table_name = UPPER(p_tabla);
    RETURN v_n > 0;
  END;

  PROCEDURE crear_tabla(p_tabla IN VARCHAR2, p_ddl IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      ejecutar(p_ddl);
    END IF;
  END;

BEGIN
  -- 1. ESQUEMA_MIGRACIONES: una fila por script SQL aplicado y su huella SHA-256.
  crear_tabla('ESQUEMA_MIGRACIONES', '
    CREATE TABLE esquema_migraciones (
      version      VARCHAR2(10)  NOT NULL,
      archivo      VARCHAR2(120) NOT NULL,
      sha256       VARCHAR2(64)  NOT NULL,
      origen       VARCHAR2(40)  DEFAULT ''aplicada'' NOT NULL,
      notas        VARCHAR2(255),
      aplicada_en  TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_esquema_migraciones PRIMARY KEY (version),
      CONSTRAINT ck_esquema_migraciones_origen CHECK (origen IN (''aplicada'', ''linea_base'', ''manual''))
    )');

  -- 2. Completar permisos de AMATISTA_APP (incluyendo tablas de 014 sin GRANT previo).
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_pendientes.COUNT LOOP
      IF existe_tabla(v_pendientes(i)) THEN
        ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_pendientes(i) || ' TO amatista_app');
      END IF;
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('016 aplicado. ESQUEMA_MIGRACIONES lista.');
END;
/

-- 3. Verificar: las 31 tablas deben decir OK (columnas reales = esperadas).
SELECT e.tabla,
       NVL(c.columnas, 0) AS columnas,
       e.esperadas,
       CASE WHEN NVL(c.columnas, 0) = e.esperadas THEN 'OK' ELSE 'REVISAR' END AS estado
  FROM (SELECT 'USUARIOS' AS tabla, 17 AS esperadas FROM dual
        UNION ALL SELECT 'SESIONES', 7 FROM dual
        UNION ALL SELECT 'PROGRESO_LECCIONES', 9 FROM dual
        UNION ALL SELECT 'LOGROS', 3 FROM dual
        UNION ALL SELECT 'EVENTOS_APRENDIZAJE', 11 FROM dual
        UNION ALL SELECT 'CURSOS', 14 FROM dual
        UNION ALL SELECT 'MODULOS', 12 FROM dual
        UNION ALL SELECT 'LECCIONES', 14 FROM dual
        UNION ALL SELECT 'NIVELES', 10 FROM dual
        UNION ALL SELECT 'HABILIDADES', 7 FROM dual
        UNION ALL SELECT 'HABILIDADES_ALUMNO', 15 FROM dual
        UNION ALL SELECT 'EVALUACIONES_RUBRICA', 8 FROM dual
        UNION ALL SELECT 'VERSIONES_BLENDER', 6 FROM dual
        UNION ALL SELECT 'VERIFICACIONES_BLENDER', 12 FROM dual
        UNION ALL SELECT 'ADDON_VINCULOS', 8 FROM dual
        UNION ALL SELECT 'PRACTICAS', 13 FROM dual
        UNION ALL SELECT 'PRACTICA_VERSIONES', 9 FROM dual
        UNION ALL SELECT 'PROGRESO_PRACTICAS', 16 FROM dual
        UNION ALL SELECT 'ADDON_ENLACES', 12 FROM dual
        UNION ALL SELECT 'ADDON_AJUSTES', 3 FROM dual
        UNION ALL SELECT 'TALLERES_ALUMNO', 5 FROM dual
        UNION ALL SELECT 'PERFILES', 3 FROM dual
        UNION ALL SELECT 'SUGERENCIAS', 9 FROM dual
        UNION ALL SELECT 'SUGERENCIA_MENSAJES', 6 FROM dual
        UNION ALL SELECT 'GRUPOS', 6 FROM dual
        UNION ALL SELECT 'GRUPO_ALUMNOS', 3 FROM dual
        UNION ALL SELECT 'ASIGNACIONES', 9 FROM dual
        UNION ALL SELECT 'ENTREGAS', 15 FROM dual
        UNION ALL SELECT 'NUBE_SESIONES', 9 FROM dual
        UNION ALL SELECT 'SISTEMA_LOTES', 12 FROM dual
        UNION ALL SELECT 'ESQUEMA_MIGRACIONES', 6 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 4. Conteo inicial de registros de migraciones.
SELECT COUNT(*) AS esquema_migraciones FROM esquema_migraciones;
