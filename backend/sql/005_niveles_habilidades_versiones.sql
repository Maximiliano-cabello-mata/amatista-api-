-- =============================================================================
-- AMATISTA · 005 · REESTRUCTURACIÓN POR NIVELES (v3.0.0)
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- Agrega lo que necesita la ruta curso > nivel > módulo > lección > actividad
-- (docs/reestructuracion/01_modelo_de_contenido.md):
--   - NIVELES ............... los cinco niveles de cada curso (el 5, por rama).
--   - MODULOS.NIVEL_ID ...... a qué nivel pertenece cada módulo (NULL = módulo
--                             anterior a los niveles; sigue visible).
--   - HABILIDADES ........... habilidades observables que citan las lecciones.
--   - HABILIDADES_ALUMNO .... estado de cada habilidad por alumno.
--   - EVALUACIONES_RUBRICA .. rúbrica A-E del proyecto de cada nivel.
--   - VERSIONES_BLENDER ..... versiones y su categoría (principal, compatible,
--                             sin verificar, retirada). Nace vacía: la versión
--                             principal es una decisión pendiente.
--   - VERIFICACIONES_BLENDER  matriz de compatibilidad: una prueba de una
--                             lección en una versión y un sistema operativo.
--
-- Script INCREMENTAL e IDEMPOTENTE, igual que 002:
--   - NO borra tablas ni filas: solo ALTER TABLE ... ADD y CREATE.
--   - Antes de cada cambio consulta el diccionario; se puede repetir.
--   - Los usuarios, el progreso, las insignias y el contenido no se tocan.
--
-- Orden: 001 -> 002 -> 003 -> (004) -> 005 -> 006.
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5). Guía completa:
-- docs/reestructuracion/02_manual_oracle.md
--
-- IMPORTANTE: ejecútalo ANTES de actualizar el backend a la v3.0.0. El código
-- nuevo lee MODULOS.NIVEL_ID: sin esta columna el catálogo responde 503.
--
-- ESPACIO (bytes por fila con índices; ver 002 para el método):
--   NIVELES ~300 x ~10 por curso; HABILIDADES ~250 x ~80 por curso: < 1 MB.
--   HABILIDADES_ALUMNO (IOT) ~80 x ~80 por alumno ............. ~6 KB/alumno
--   EVALUACIONES_RUBRICA ~150 x 5 criterios x 5 niveles . ~4 KB/alumno
--   VERIFICACIONES_BLENDER ~500 x lecciones x versiones ........ < 5 MB
--   Total permanente por alumno pasa de ~12 KB a ~22 KB: 5 GB siguen
--   alcanzando para más de 200,000 alumnos.
--
-- ÍNDICES (regla 2: nada sobre columnas que ya son PRIMARY KEY o UNIQUE):
--   - uq_niveles_curso_numero_rama: el UNIQUE crea su índice y cubre el mapa
--     del curso ordenado por número.
--   - ix_habilidades_curso (curso_id, orden): habilidades de un curso en orden.
--   - ix_verificaciones_leccion (curso_id, leccion_id): matriz por lección y
--     llave foránea compuesta hacia LECCIONES.
--   - HABILIDADES_ALUMNO es IOT (la fila vive en la llave primaria) y
--     EVALUACIONES_RUBRICA, tabla normal; en ambas la llave primaria empieza
--     por usuario_id y ya cubre las consultas por alumno.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
  v_tablas  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST(
              'NIVELES', 'HABILIDADES', 'HABILIDADES_ALUMNO', 'EVALUACIONES_RUBRICA',
              'VERSIONES_BLENDER', 'VERIFICACIONES_BLENDER');

  -- Ejecuta un DDL o DML dinámico y lo deja anotado en la salida del script.
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

  FUNCTION existe_columna(p_tabla IN VARCHAR2, p_columna IN VARCHAR2) RETURN BOOLEAN IS
    v_n PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_n
      FROM user_tab_columns
     WHERE table_name = UPPER(p_tabla) AND column_name = UPPER(p_columna);
    RETURN v_n > 0;
  END;

  FUNCTION existe_restriccion(p_nombre IN VARCHAR2) RETURN BOOLEAN IS
    v_n PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_n FROM user_constraints WHERE constraint_name = UPPER(p_nombre);
    RETURN v_n > 0;
  END;

  FUNCTION existe_indice(p_nombre IN VARCHAR2) RETURN BOOLEAN IS
    v_n PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_n FROM user_indexes WHERE index_name = UPPER(p_nombre);
    RETURN v_n > 0;
  END;

  PROCEDURE exigir_columna(p_tabla IN VARCHAR2, p_columna IN VARCHAR2) IS
  BEGIN
    IF NOT existe_columna(p_tabla, p_columna) THEN
      RAISE_APPLICATION_ERROR(-20001, 'Falta ' || p_tabla || '.' || p_columna
        || '. Ejecuta antes 002_autenticacion_contenido_eventos.sql (y 001 si la base está vacía).');
    END IF;
  END;

  PROCEDURE agregar_columna(p_tabla IN VARCHAR2, p_columna IN VARCHAR2, p_definicion IN VARCHAR2) IS
  BEGIN
    IF NOT existe_columna(p_tabla, p_columna) THEN
      ejecutar('ALTER TABLE ' || p_tabla || ' ADD (' || p_columna || ' ' || p_definicion || ')');
    END IF;
  END;

  PROCEDURE agregar_restriccion(p_tabla IN VARCHAR2, p_nombre IN VARCHAR2, p_definicion IN VARCHAR2) IS
  BEGIN
    IF NOT existe_restriccion(p_nombre) THEN
      ejecutar('ALTER TABLE ' || p_tabla || ' ADD CONSTRAINT ' || p_nombre || ' ' || p_definicion);
    END IF;
  EXCEPTION
    WHEN OTHERS THEN
      -- ORA-02261 / ORA-02275: ya hay una restricción igual con otro nombre; se conserva.
      IF SQLCODE IN (-2261, -2275) THEN
        DBMS_OUTPUT.PUT_LINE('Se conserva la restricción equivalente que ya existe: ' || p_nombre);
      ELSE
        RAISE;
      END IF;
  END;

  PROCEDURE crear_tabla(p_tabla IN VARCHAR2, p_ddl IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      ejecutar(p_ddl);
    END IF;
  END;

  PROCEDURE crear_indice(p_nombre IN VARCHAR2, p_tabla IN VARCHAR2, p_columnas IN VARCHAR2) IS
  BEGIN
    IF NOT existe_indice(p_nombre) THEN
      ejecutar('CREATE INDEX ' || p_nombre || ' ON ' || p_tabla || ' (' || p_columnas || ')');
    END IF;
  EXCEPTION
    WHEN OTHERS THEN
      -- ORA-01408: esas columnas ya tienen un índice con otro nombre; no se duplica.
      IF SQLCODE = -1408 THEN
        DBMS_OUTPUT.PUT_LINE('Se conserva el índice que ya existe sobre ' || p_tabla || ' (' || p_columnas || ')');
      ELSE
        RAISE;
      END IF;
  END;

BEGIN
  -- 0. Punto de partida: el contenido de 002 (CURSOS, MODULOS, LECCIONES).
  exigir_columna('CURSOS', 'ESTADO');
  exigir_columna('MODULOS', 'CURSO_ID');
  exigir_columna('LECCIONES', 'CONTENIDO');
  exigir_columna('USUARIOS', 'ROL');

  -- 1. NIVELES: el 5 lleva una fila por rama (web, animacion, producto...).
  --    UNIQUE (curso_id, numero, rama): Oracle rechaza dos niveles 1 del
  --    mismo curso aunque rama sea NULL, y su índice sirve para el mapa.
  crear_tabla('NIVELES', '
    CREATE TABLE niveles (
      id               VARCHAR2(50)        NOT NULL,
      curso_id         VARCHAR2(50)        NOT NULL,
      numero           NUMBER(1)           NOT NULL,
      rama             VARCHAR2(50),
      titulo           VARCHAR2(100 CHAR)  NOT NULL,
      perfil           VARCHAR2(500 CHAR),
      proyecto         VARCHAR2(300 CHAR),
      criterio_salida  VARCHAR2(500 CHAR),
      estado           VARCHAR2(12) DEFAULT ''borrador'' NOT NULL,
      actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_niveles PRIMARY KEY (id),
      CONSTRAINT fk_niveles_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
      CONSTRAINT uq_niveles_curso_numero_rama UNIQUE (curso_id, numero, rama),
      CONSTRAINT ck_niveles_numero CHECK (numero IN (1, 2, 3, 4, 5)),
      CONSTRAINT ck_niveles_rama CHECK (numero = 5 OR rama IS NULL),
      CONSTRAINT ck_niveles_estado CHECK (estado IN (''borrador'', ''publicado'', ''archivado''))
    )');

  -- 2. MODULOS.NIVEL_ID: anulable, así los módulos de antes no cambian.
  --    Es una columna nueva sin DEFAULT: Oracle solo cambia el diccionario.
  agregar_columna('MODULOS', 'NIVEL_ID', 'VARCHAR2(50)');
  agregar_restriccion('MODULOS', 'FK_MODULOS_NIVEL', 'FOREIGN KEY (nivel_id) REFERENCES niveles (id)');

  -- 3. HABILIDADES: catálogo de habilidades observables del curso.
  crear_tabla('HABILIDADES', '
    CREATE TABLE habilidades (
      id              VARCHAR2(50)        NOT NULL,
      curso_id        VARCHAR2(50)        NOT NULL,
      nivel_id        VARCHAR2(50),
      nombre          VARCHAR2(150 CHAR)  NOT NULL,
      descripcion     VARCHAR2(500 CHAR),
      orden           NUMBER(5) DEFAULT 0 NOT NULL,
      actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_habilidades PRIMARY KEY (id),
      CONSTRAINT fk_habilidades_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
      CONSTRAINT fk_habilidades_nivel FOREIGN KEY (nivel_id) REFERENCES niveles (id)
    )');
  crear_indice('IX_HABILIDADES_CURSO', 'HABILIDADES', 'curso_id, orden');

  -- 4. HABILIDADES_ALUMNO: sin practicar -> con guía -> con pistas -> autónoma.
  --    IOT como LOGROS: la fila vive dentro de la llave primaria.
  crear_tabla('HABILIDADES_ALUMNO', '
    CREATE TABLE habilidades_alumno (
      usuario_id      VARCHAR2(100)       NOT NULL,
      habilidad_id    VARCHAR2(50)        NOT NULL,
      estado          VARCHAR2(14) DEFAULT ''sin_practicar'' NOT NULL,
      evidencia       VARCHAR2(300 CHAR),
      actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_habilidades_alumno PRIMARY KEY (usuario_id, habilidad_id),
      CONSTRAINT fk_hab_alumno_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT fk_hab_alumno_habilidad FOREIGN KEY (habilidad_id) REFERENCES habilidades (id),
      CONSTRAINT ck_hab_alumno_estado CHECK (estado IN (''sin_practicar'', ''con_guia'', ''con_pistas'', ''autonoma''))
    ) ORGANIZATION INDEX');

  -- 5. EVALUACIONES_RUBRICA: criterio A-E del proyecto de cada nivel.
  --    Tabla normal (no IOT): evidencia y comentario pueden pasar del límite
  --    de fila de una IOT sin segmento de desbordamiento (ORA-01429).
  crear_tabla('EVALUACIONES_RUBRICA', '
    CREATE TABLE evaluaciones_rubrica (
      usuario_id      VARCHAR2(100)       NOT NULL,
      nivel_id        VARCHAR2(50)        NOT NULL,
      criterio        VARCHAR2(1)         NOT NULL,
      logro           VARCHAR2(12) DEFAULT ''pendiente'' NOT NULL,
      evidencia       VARCHAR2(300 CHAR),
      comentario      VARCHAR2(500 CHAR),
      revisado_por    VARCHAR2(100),
      actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_evaluaciones_rubrica PRIMARY KEY (usuario_id, nivel_id, criterio),
      CONSTRAINT fk_rubrica_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT fk_rubrica_nivel FOREIGN KEY (nivel_id) REFERENCES niveles (id),
      CONSTRAINT ck_rubrica_criterio CHECK (criterio IN (''A'', ''B'', ''C'', ''D'', ''E'')),
      CONSTRAINT ck_rubrica_logro CHECK (logro IN (''pendiente'', ''con_ayuda'', ''autonomo''))
    )');

  -- 6. VERSIONES_BLENDER: nace vacía (la versión principal está pendiente).
  crear_tabla('VERSIONES_BLENDER', '
    CREATE TABLE versiones_blender (
      version         VARCHAR2(20)        NOT NULL,
      categoria       VARCHAR2(14) DEFAULT ''sin_verificar'' NOT NULL,
      es_lts          NUMBER(1) DEFAULT 0 NOT NULL,
      soporte_hasta   TIMESTAMP,
      notas           VARCHAR2(500 CHAR),
      actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_versiones_blender PRIMARY KEY (version),
      CONSTRAINT ck_versiones_categoria CHECK (categoria IN (''principal'', ''compatible'', ''sin_verificar'', ''retirada'')),
      CONSTRAINT ck_versiones_es_lts CHECK (es_lts IN (0, 1))
    )');

  -- 7. VERIFICACIONES_BLENDER: la matriz de compatibilidad.
  crear_tabla('VERIFICACIONES_BLENDER', '
    CREATE TABLE verificaciones_blender (
      id               VARCHAR2(36)        NOT NULL,
      curso_id         VARCHAR2(50)        NOT NULL,
      leccion_id       VARCHAR2(50)        NOT NULL,
      version_blender  VARCHAR2(20)        NOT NULL,
      sistema          VARCHAR2(60 CHAR)   NOT NULL,
      version_leccion  NUMBER(10)          NOT NULL,
      version_addon    VARCHAR2(20),
      resultado        VARCHAR2(16)        NOT NULL,
      diferencias      VARCHAR2(1000 CHAR),
      evidencia        VARCHAR2(300 CHAR),
      responsable      VARCHAR2(100 CHAR),
      verificado_en    TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_verificaciones_blender PRIMARY KEY (id),
      CONSTRAINT fk_verificaciones_leccion FOREIGN KEY (curso_id, leccion_id) REFERENCES lecciones (curso_id, id),
      CONSTRAINT fk_verificaciones_version FOREIGN KEY (version_blender) REFERENCES versiones_blender (version),
      CONSTRAINT ck_verificaciones_resultado CHECK (resultado IN (''verificada'', ''con_diferencias'', ''falla''))
    )');
  crear_indice('IX_VERIFICACIONES_LECCION', 'VERIFICACIONES_BLENDER', 'curso_id, leccion_id');

  -- 8. Si ya se usa AMATISTA_APP (004), recibe permisos sobre las tablas
  --    nuevas. Volver a ejecutar 004 hace lo mismo.
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_tablas.COUNT LOOP
      ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app');
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('005 aplicado. Sigue con 006_herramientas_autor.sql.');
END;
/

-- 9. Verificar: las 14 tablas deben decir OK (columnas reales = esperadas).
--    Esta consulta sustituye a la de 002 (MODULOS ahora tiene 12 columnas).
SELECT e.tabla,
       NVL(c.columnas, 0) AS columnas,
       e.esperadas,
       CASE WHEN NVL(c.columnas, 0) = e.esperadas THEN 'OK' ELSE 'REVISAR' END AS estado
  FROM (SELECT 'USUARIOS' AS tabla, 17 AS esperadas FROM dual
        UNION ALL SELECT 'SESIONES', 7 FROM dual
        UNION ALL SELECT 'PROGRESO_LECCIONES', 9 FROM dual
        UNION ALL SELECT 'LOGROS', 3 FROM dual
        UNION ALL SELECT 'EVENTOS_APRENDIZAJE', 11 FROM dual
        UNION ALL SELECT 'CURSOS', 12 FROM dual
        UNION ALL SELECT 'MODULOS', 12 FROM dual
        UNION ALL SELECT 'LECCIONES', 14 FROM dual
        UNION ALL SELECT 'NIVELES', 10 FROM dual
        UNION ALL SELECT 'HABILIDADES', 7 FROM dual
        UNION ALL SELECT 'HABILIDADES_ALUMNO', 5 FROM dual
        UNION ALL SELECT 'EVALUACIONES_RUBRICA', 8 FROM dual
        UNION ALL SELECT 'VERSIONES_BLENDER', 6 FROM dual
        UNION ALL SELECT 'VERIFICACIONES_BLENDER', 12 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 10. Filas de las tablas nuevas (el progreso y los usuarios no cambian).
SELECT 'NIVELES' AS tabla, COUNT(*) AS filas FROM niveles
UNION ALL SELECT 'HABILIDADES', COUNT(*) FROM habilidades
UNION ALL SELECT 'HABILIDADES_ALUMNO', COUNT(*) FROM habilidades_alumno
UNION ALL SELECT 'EVALUACIONES_RUBRICA', COUNT(*) FROM evaluaciones_rubrica
UNION ALL SELECT 'VERSIONES_BLENDER', COUNT(*) FROM versiones_blender
UNION ALL SELECT 'VERIFICACIONES_BLENDER', COUNT(*) FROM verificaciones_blender
UNION ALL SELECT 'MODULOS sin nivel', COUNT(*) FROM modulos WHERE nivel_id IS NULL;
