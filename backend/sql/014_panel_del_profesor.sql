-- =============================================================================
-- AMATISTA · 014 · PANEL DEL PROFESOR EVALUADOR
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- El profesor no es alumno, administrador ni desarrollador: es quien evalúa
-- (docs/plataforma/17_panel_del_profesor.md).
--   - GRUPOS ............... los grupos de cada profesor; sus alumnos entran
--                            con un código corto (CODIGO, único).
--   - GRUPO_ALUMNOS ........ quién está en cada grupo.
--   - ASIGNACIONES ......... una práctica de Blender que el profesor deja a un
--                            grupo, con indicaciones y fecha límite.
--   - ENTREGAS ............. lo que entregó cada alumno: el progreso que midió
--                            el motor, un resumen y, POR POCO TIEMPO, la
--                            descripción ligera de su modelo (MODELO: JSON
--                            comprimido, nunca el .blend). Cuando el profesor
--                            la recoge, vive en su computadora y aquí se
--                            borra (RECOGIDO_EN); si nadie la recoge, el
--                            backend la borra a los 30 días.
--
-- Script INCREMENTAL e IDEMPOTENTE, igual que 010 a 013:
--   - NO borra tablas ni filas: solo CREATE.
--   - Antes de cada cambio consulta el diccionario; se puede repetir.
--   - No toca usuarios, progreso, prácticas ni contenido.
--
-- Orden: ... -> 012 -> 013 -> 014 (necesita PRACTICAS, de 007).
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5).
--
-- Sin 014 el backend arranca igual: el panel del profesor y «Mi grupo» del
-- alumno responden 503 con un mensaje claro.
--
-- ESPACIO (bytes por fila con índices; ver 002 para el método):
--   GRUPOS ~150, GRUPO_ALUMNOS ~120 por alumno, ASIGNACIONES ~600.
--   ENTREGAS ~700 sin modelo. El modelo de una práctica low poly pesa de 2 a
--   40 KB (el add-on lo comprime y el servidor rechaza más de 1 MB) y solo
--   está aquí entre que el alumno entrega y el profesor lo abre. Con 30
--   alumnos y 10 prácticas al año: ~200 KB de filas, más los modelos en
--   tránsito (unos pocos MB como máximo).
--
-- ÍNDICES (regla 2: nada sobre columnas que ya son PRIMARY KEY o UNIQUE):
--   IX_GRUPOS_PROFESOR (los grupos de un profesor), IX_GRUPO_ALUMNOS_ALUMNO
--   (los grupos de un alumno), IX_ASIGNACIONES_GRUPO (las prácticas de un
--   grupo, recientes primero) e IX_ENTREGAS_ALUMNO (lo que entregó un alumno).
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
  v_tablas  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST('GRUPOS', 'GRUPO_ALUMNOS', 'ASIGNACIONES', 'ENTREGAS');

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

  PROCEDURE exigir_tabla(p_tabla IN VARCHAR2, p_script IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      RAISE_APPLICATION_ERROR(-20001, 'Falta la tabla ' || p_tabla || '. Ejecuta antes ' || p_script || '.');
    END IF;
  END;

  PROCEDURE crear_tabla(p_tabla IN VARCHAR2, p_ddl IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      ejecutar(p_ddl);
    END IF;
  END;

BEGIN
  -- 0. Punto de partida: 002 (usuarios) y 007 (prácticas).
  exigir_tabla('USUARIOS', '002_autenticacion_contenido_eventos.sql');
  exigir_tabla('PRACTICAS', '007_motor_practicas.sql');

  -- 1. GRUPOS: los grupos de cada profesor.
  crear_tabla('GRUPOS', '
    CREATE TABLE grupos (
      id               VARCHAR2(40)  NOT NULL,
      profesor_id      VARCHAR2(100) NOT NULL,
      nombre           VARCHAR2(120) NOT NULL,
      codigo           VARCHAR2(12)  NOT NULL,
      archivado        NUMBER(1)     DEFAULT 0 NOT NULL,
      creado_en        TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_grupos PRIMARY KEY (id),
      CONSTRAINT uq_grupos_codigo UNIQUE (codigo),
      CONSTRAINT fk_grupos_profesor FOREIGN KEY (profesor_id) REFERENCES usuarios (id),
      CONSTRAINT ck_grupos_archivado CHECK (archivado IN (0, 1))
    )');
  IF NOT existe_indice('IX_GRUPOS_PROFESOR') THEN
    ejecutar('CREATE INDEX ix_grupos_profesor ON grupos (profesor_id)');
  END IF;

  -- 2. GRUPO_ALUMNOS: quién está en cada grupo.
  crear_tabla('GRUPO_ALUMNOS', '
    CREATE TABLE grupo_alumnos (
      grupo_id         VARCHAR2(40)  NOT NULL,
      alumno_id        VARCHAR2(100) NOT NULL,
      unido_en         TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_grupo_alumnos PRIMARY KEY (grupo_id, alumno_id),
      CONSTRAINT fk_grupo_alumnos_grupo FOREIGN KEY (grupo_id) REFERENCES grupos (id),
      CONSTRAINT fk_grupo_alumnos_alumno FOREIGN KEY (alumno_id) REFERENCES usuarios (id)
    )');
  IF NOT existe_indice('IX_GRUPO_ALUMNOS_ALUMNO') THEN
    ejecutar('CREATE INDEX ix_grupo_alumnos_alumno ON grupo_alumnos (alumno_id)');
  END IF;

  -- 3. ASIGNACIONES: la práctica que el profesor deja a un grupo.
  crear_tabla('ASIGNACIONES', '
    CREATE TABLE asignaciones (
      id               VARCHAR2(40)   NOT NULL,
      grupo_id         VARCHAR2(40)   NOT NULL,
      profesor_id      VARCHAR2(100)  NOT NULL,
      practica_id      VARCHAR2(80)   NOT NULL,
      titulo           VARCHAR2(200)  NOT NULL,
      indicaciones     VARCHAR2(1000),
      fecha_limite     TIMESTAMP,
      estado           VARCHAR2(12)   DEFAULT ''abierta'' NOT NULL,
      creada_en        TIMESTAMP      DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_asignaciones PRIMARY KEY (id),
      CONSTRAINT fk_asignaciones_grupo FOREIGN KEY (grupo_id) REFERENCES grupos (id),
      CONSTRAINT fk_asignaciones_profesor FOREIGN KEY (profesor_id) REFERENCES usuarios (id),
      CONSTRAINT fk_asignaciones_practica FOREIGN KEY (practica_id) REFERENCES practicas (id),
      CONSTRAINT ck_asignaciones_estado CHECK (estado IN (''abierta'', ''cerrada''))
    )');
  IF NOT existe_indice('IX_ASIGNACIONES_GRUPO') THEN
    ejecutar('CREATE INDEX ix_asignaciones_grupo ON asignaciones (grupo_id, creada_en)');
  END IF;

  -- 4. ENTREGAS: lo que entregó cada alumno (el modelo, solo de paso).
  crear_tabla('ENTREGAS', '
    CREATE TABLE entregas (
      asignacion_id    VARCHAR2(40)   NOT NULL,
      alumno_id        VARCHAR2(100)  NOT NULL,
      estado           VARCHAR2(12)   DEFAULT ''entregada'' NOT NULL,
      progreso         NUMBER(3)      DEFAULT 0 NOT NULL,
      completada       NUMBER(1)      DEFAULT 0 NOT NULL,
      intentos         NUMBER(6)      DEFAULT 0 NOT NULL,
      resumen          VARCHAR2(2000),
      modelo           CLOB,
      modelo_bytes     NUMBER(9)      DEFAULT 0 NOT NULL,
      entregada_en     TIMESTAMP      DEFAULT SYSTIMESTAMP NOT NULL,
      recogido_en      TIMESTAMP,
      calificacion     NUMBER(2),
      comentario       VARCHAR2(1000),
      evaluada_en      TIMESTAMP,
      vista_alumno     NUMBER(1)      DEFAULT 1 NOT NULL,
      CONSTRAINT pk_entregas PRIMARY KEY (asignacion_id, alumno_id),
      CONSTRAINT fk_entregas_asignacion FOREIGN KEY (asignacion_id) REFERENCES asignaciones (id),
      CONSTRAINT fk_entregas_alumno FOREIGN KEY (alumno_id) REFERENCES usuarios (id),
      CONSTRAINT ck_entregas_estado CHECK (estado IN (''entregada'', ''evaluada'', ''rehacer'')),
      CONSTRAINT ck_entregas_completada CHECK (completada IN (0, 1)),
      CONSTRAINT ck_entregas_vista CHECK (vista_alumno IN (0, 1)),
      CONSTRAINT ck_entregas_calificacion CHECK (calificacion BETWEEN 0 AND 10),
      CONSTRAINT ck_entregas_resumen CHECK (resumen IS JSON)
    )');
  IF NOT existe_indice('IX_ENTREGAS_ALUMNO') THEN
    ejecutar('CREATE INDEX ix_entregas_alumno ON entregas (alumno_id)');
  END IF;

  -- 5. Si ya se usa AMATISTA_APP (004), recibe permisos sobre las tablas nuevas.
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_tablas.COUNT LOOP
      ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app');
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('014 aplicado. Actualiza el backend y reinicia: sudo systemctl restart amatista-backend');
END;
/

-- 6. Verificar: las 28 tablas deben decir OK (columnas reales = esperadas).
--    Sustituye a la consulta de 013 (se suman GRUPOS, GRUPO_ALUMNOS,
--    ASIGNACIONES y ENTREGAS).
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
        UNION ALL SELECT 'ENTREGAS', 15 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 7. Filas: grupos, alumnos en grupos, asignaciones y entregas (y cuántos
--    modelos siguen en tránsito, sin recoger).
SELECT COUNT(*) AS grupos FROM grupos;

SELECT COUNT(*) AS grupo_alumnos FROM grupo_alumnos;

SELECT COUNT(*) AS asignaciones FROM asignaciones;

SELECT COUNT(*) AS entregas FROM entregas;

SELECT COUNT(*) AS modelos_en_transito, NVL(SUM(modelo_bytes), 0) AS bytes FROM entregas WHERE modelo IS NOT NULL;
