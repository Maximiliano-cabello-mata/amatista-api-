-- =============================================================================
-- AMATISTA · 015 · REGISTRO DE LOTES MORAL (TRAZABILIDAD DE DESPLIEGUES)
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- Registra cada manifiesto `amatista.moral/1` transmitido desde los cuatro
-- repositorios activos (web, api, motor, g7b-etl) para cruzar la salud del
-- software y los lotes de diseño/color con los eventos de aprendizaje reales.
--
-- Script INCREMENTAL e IDEMPOTENTE:
--   - NO borra tablas ni filas: solo CREATE.
--   - Se puede repetir en Database Actions > SQL (F5) o vía aplicar_015_oracle.py.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
  v_tablas  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST('SISTEMA_LOTES');

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

  FUNCTION existe_indice(p_nombre IN VARCHAR2) RETURN BOOLEAN IS
    v_n PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_n FROM user_indexes WHERE index_name = UPPER(p_nombre);
    RETURN v_n > 0;
  END;

  PROCEDURE crear_tabla(p_tabla IN VARCHAR2, p_ddl IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      ejecutar(p_ddl);
    END IF;
  END;

BEGIN
  -- 1. SISTEMA_LOTES: un registro por repositorio, lote y commit publicado.
  crear_tabla('SISTEMA_LOTES', '
    CREATE TABLE sistema_lotes (
      id               VARCHAR2(64)  NOT NULL,
      lote             VARCHAR2(40)  NOT NULL,
      repo             VARCHAR2(60)  NOT NULL,
      rama             VARCHAR2(60)  NOT NULL,
      commit_sha       VARCHAR2(40)  NOT NULL,
      tag              VARCHAR2(60),
      motor_version    VARCHAR2(40),
      geoda_version    VARCHAR2(40),
      puntaje_moral    NUMBER(3)     DEFAULT 0 NOT NULL,
      contrato_ok      NUMBER(1)     DEFAULT 0 NOT NULL,
      manifiesto       CLOB,
      registrado_en    TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_sistema_lotes PRIMARY KEY (id),
      CONSTRAINT ck_sistema_lotes_puntaje CHECK (puntaje_moral BETWEEN 0 AND 100),
      CONSTRAINT ck_sistema_lotes_ok CHECK (contrato_ok IN (0, 1)),
      CONSTRAINT ck_sistema_lotes_json CHECK (manifiesto IS JSON)
    )');
  IF NOT existe_indice('IX_SISTEMA_LOTES_REPO_FECHA') THEN
    ejecutar('CREATE INDEX ix_sistema_lotes_repo_fecha ON sistema_lotes (repo, registrado_en)');
  END IF;

  -- 2. Si ya se usa AMATISTA_APP (004), recibe permisos sobre SISTEMA_LOTES.
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_tablas.COUNT LOOP
      ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app');
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('015 aplicado. SISTEMA_LOTES lista.');
END;
/

-- 3. Verificar: las 30 tablas deben decir OK (columnas reales = esperadas).
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
        UNION ALL SELECT 'SISTEMA_LOTES', 12 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 4. Conteo inicial de registros MORAL.
SELECT COUNT(*) AS sistema_lotes FROM sistema_lotes;
