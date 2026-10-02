-- =============================================================================
-- AMATISTA · 002 · AUTENTICACIÓN, LOGROS, EVENTOS Y CONTENIDO
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- Script INCREMENTAL: lleva una base creada con 001 al estado exacto de
-- backend/database/modelos.py.
--   - NO borra tablas ni filas de alumnos: solo agrega columnas, tablas,
--     restricciones e índices (ALTER TABLE ... ADD / CREATE).
--   - IDEMPOTENTE: antes de cada cambio consulta el diccionario
--     (USER_TABLES, USER_TAB_COLUMNS, USER_CONSTRAINTS, USER_INDEXES); se
--     puede ejecutar las veces que haga falta. Si algo falla a la mitad,
--     corrige la causa y vuelve a ejecutarlo completo.
--
-- Cómo ejecutarlo:
--   1. Database Actions > SQL, con el usuario dueño de las tablas (ADMIN).
--   2. Pega este archivo completo y pulsa "Ejecutar script" (F5).
--   3. Revisa la salida: cada cambio aplicado aparece como "OK: ...". La
--      última consulta debe mostrar OK en las 8 tablas.
--   4. En el servidor: python diagnostico_oracle.py
--   Después: 003_mantenimiento.sql (purga diaria) y, opcional, 004.
--
-- PRESUPUESTO DE ESPACIO (20 GB del Free Tier; planeamos con ~15 GB para la
-- app y dejamos el resto a Oracle: diccionario, undo, temporales, papelera).
-- Bytes por fila, contando bloque, PCTFREE e índices:
--
--   Tabla                 bytes/fila         filas por alumno       por alumno
--   USUARIOS              ~400 (anónimo ~150) 1                     ~0.4 KB
--   SESIONES              ~350               ~3 vivas (purga 90 d)  ~1 KB
--   PROGRESO_LECCIONES    ~200 (~450 con     ~50 (10 módulos x 5)   ~10 KB
--                         datos_ligeros lleno)                      (~22 KB)
--   LOGROS (IOT)          ~90                ~10 insignias          ~1 KB
--   --------------------------------------------------------------------------
--   Permanente por alumno: ~12 KB típico, ~25 KB en el peor caso.
--   5 GB alcanzan para ~200,000 alumnos aun en el peor caso.
--
--   EVENTOS_APRENDIZAJE   ~360               ~80 al mes por alumno activo
--   Es la única tabla que crece con el tiempo: ~29 KB por alumno activo al
--   mes. Con la retención por defecto (400 días, 003_mantenimiento.sql) son
--   ~380 KB por alumno activo todo el año: 10 GB = ~27,000 alumnos activos.
--   Con 180 días, ~60,000; con 90 días, ~120,000. La retención es la perilla:
--   revisa V_AMATISTA_ESPACIO (003) y baja p_dias_eventos si pasa del 70 %.
--
--   CURSOS / MODULOS / LECCIONES: ~200 lecciones x ~6 KB de JSON = ~1.2 MB.
--   Despreciable. Imágenes, videos y modelos .glb NUNCA van a la base
--   (viven en el frontend o en un almacenamiento de archivos).
--
-- POR QUÉ CADA ÍNDICE (regla 2 del informe: cero índices manuales sobre
-- columnas PRIMARY KEY o UNIQUE; Oracle ya los crea con la restricción):
--   - uq_usuarios_email: la restricción UNIQUE crea su propio índice y con él
--     se busca la cuenta al iniciar sesión. Admite varios NULL (anónimos).
--   - pk_logros, pk_lecciones: la llave primaria empieza por la columna de la
--     llave foránea (usuario_id, curso_id): ya cubre esas búsquedas.
--   - ix_eventos_usuario_fecha (usuario_id, ocurrido_en): días activos de un
--     alumno, fusión de cuentas y la llave foránea hacia USUARIOS.
--   - ix_eventos_fecha (ocurrido_en): métricas del panel por periodo (activos
--     semanales, serie diaria). Ambos son LOCAL: se borran solos al quitar
--     una partición mensual.
--   - ix_modulos_curso (curso_id, numero): catálogo ordenado y llave foránea.
--   - ix_lecciones_modulo (modulo_id, orden): lecciones de un módulo en orden
--     y llave foránea hacia MODULOS.
--   Si algún día el panel se vuelve lento con millones de filas de progreso,
--   el siguiente índice candidato es PROGRESO_LECCIONES (completada_en): solo
--   guarda las filas completadas (los NULL no entran al índice). Iría en un
--   script 005, no aquí.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_eventos  VARCHAR2(4000);
  v_n        PLS_INTEGER;

  -- Ejecuta un DDL o DML dinámico y lo deja anotado en la salida del script.
  PROCEDURE ejecutar(p_sql IN VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
    DBMS_OUTPUT.PUT_LINE('OK: ' || SUBSTR(REGEXP_REPLACE(p_sql, '\s+', ' '), 1, 200));
  END;

  FUNCTION existe_tabla(p_tabla IN VARCHAR2) RETURN BOOLEAN IS
    v_cuenta PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_cuenta FROM user_tables WHERE table_name = UPPER(p_tabla);
    RETURN v_cuenta > 0;
  END;

  FUNCTION existe_columna(p_tabla IN VARCHAR2, p_columna IN VARCHAR2) RETURN BOOLEAN IS
    v_cuenta PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_cuenta
      FROM user_tab_columns
     WHERE table_name = UPPER(p_tabla) AND column_name = UPPER(p_columna);
    RETURN v_cuenta > 0;
  END;

  FUNCTION existe_restriccion(p_nombre IN VARCHAR2) RETURN BOOLEAN IS
    v_cuenta PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_cuenta FROM user_constraints WHERE constraint_name = UPPER(p_nombre);
    RETURN v_cuenta > 0;
  END;

  FUNCTION existe_indice(p_nombre IN VARCHAR2) RETURN BOOLEAN IS
    v_cuenta PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_cuenta FROM user_indexes WHERE index_name = UPPER(p_nombre);
    RETURN v_cuenta > 0;
  END;

  FUNCTION particionada(p_tabla IN VARCHAR2) RETURN BOOLEAN IS
    v_cuenta PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO v_cuenta FROM user_part_tables WHERE table_name = UPPER(p_tabla);
    RETURN v_cuenta > 0;
  END;

  -- 002 amplía una base creada con 001: sin sus tablas no hay nada que ampliar.
  PROCEDURE exigir_tabla(p_tabla IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      RAISE_APPLICATION_ERROR(-20001, 'Falta la tabla ' || p_tabla
        || '. En una base vacía ejecuta primero 001_esquema_amatista.sql y luego este script.');
    END IF;
  END;

  -- Las bases del diseño de septiembre tenían ids numéricos (ORA-01722 y ORA-02267).
  PROCEDURE exigir_texto(p_tabla IN VARCHAR2, p_columna IN VARCHAR2) IS
  BEGIN
    FOR c IN (SELECT data_type
                FROM user_tab_columns
               WHERE table_name = p_tabla AND column_name = p_columna
                 AND data_type <> 'VARCHAR2') LOOP
      RAISE_APPLICATION_ERROR(-20002, p_tabla || '.' || p_columna || ' es ' || c.data_type
        || ' (diseño anterior con ids numéricos). 002 no lo corrige porque tendría que borrar datos:'
        || ' lee sql/LEEME.txt, sección «Base del diseño anterior».');
    END LOOP;
  END;

  PROCEDURE agregar_columna(p_tabla IN VARCHAR2, p_columna IN VARCHAR2, p_definicion IN VARCHAR2) IS
  BEGIN
    IF NOT existe_columna(p_tabla, p_columna) THEN
      ejecutar('ALTER TABLE ' || p_tabla || ' ADD (' || p_columna || ' ' || p_definicion || ')');
    END IF;
  END;

  -- Solo agranda: nunca acorta una columna con datos.
  PROCEDURE ampliar_columna(p_tabla IN VARCHAR2, p_columna IN VARCHAR2, p_tipo IN VARCHAR2) IS
    v_largo  NUMBER;
    v_nuevo  NUMBER := TO_NUMBER(REGEXP_SUBSTR(p_tipo, '[0-9]+'));
  BEGIN
    SELECT MAX(NULLIF(char_length, 0)) INTO v_largo
      FROM user_tab_columns
     WHERE table_name = UPPER(p_tabla) AND column_name = UPPER(p_columna);
    IF v_largo IS NULL OR v_largo < v_nuevo THEN
      ejecutar('ALTER TABLE ' || p_tabla || ' MODIFY (' || p_columna || ' ' || p_tipo || ')');
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

  PROCEDURE crear_indice(p_nombre IN VARCHAR2, p_tabla IN VARCHAR2, p_columnas IN VARCHAR2,
                         p_local IN BOOLEAN DEFAULT FALSE) IS
    v_sql VARCHAR2(400);
  BEGIN
    IF existe_indice(p_nombre) THEN
      RETURN;
    END IF;
    v_sql := 'CREATE INDEX ' || p_nombre || ' ON ' || p_tabla || ' (' || p_columnas || ')';
    -- LOCAL: una porción del índice por partición; se va junto con el mes purgado.
    IF p_local AND particionada(p_tabla) THEN
      v_sql := v_sql || ' LOCAL';
    END IF;
    ejecutar(v_sql);
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
  -- 0. Punto de partida: las tablas de 001 con ids de texto.
  exigir_tabla('USUARIOS');
  exigir_tabla('SESIONES');
  exigir_tabla('PROGRESO_LECCIONES');
  exigir_texto('USUARIOS', 'ID');
  exigir_texto('SESIONES', 'USUARIO_ID');
  exigir_texto('PROGRESO_LECCIONES', 'USUARIO_ID');

  -- 1. USUARIOS: correo, contraseña, rol, códigos de verificación y bloqueo.
  --    Las columnas NOT NULL llevan DEFAULT: Oracle rellena las filas que ya
  --    existen sin reescribir la tabla (solo cambia el diccionario).
  agregar_columna('USUARIOS', 'EMAIL',               'VARCHAR2(100 CHAR)');
  agregar_columna('USUARIOS', 'TELEFONO',            'VARCHAR2(25 CHAR)');
  agregar_columna('USUARIOS', 'ROL',                 'VARCHAR2(20) DEFAULT ''alumno'' NOT NULL');
  agregar_columna('USUARIOS', 'PASSWORD_HASH',       'VARCHAR2(255)');
  agregar_columna('USUARIOS', 'CORREO_CONFIRMADO',   'NUMBER(1) DEFAULT 0 NOT NULL');
  agregar_columna('USUARIOS', 'CODIGO_VERIFICACION', 'VARCHAR2(64)');
  agregar_columna('USUARIOS', 'CODIGO_PROPOSITO',    'VARCHAR2(12)');
  agregar_columna('USUARIOS', 'CODIGO_EXPIRA',       'TIMESTAMP');
  agregar_columna('USUARIOS', 'CODIGO_INTENTOS',     'NUMBER(5) DEFAULT 0 NOT NULL');
  agregar_columna('USUARIOS', 'INTENTOS_FALLIDOS',   'NUMBER(10) DEFAULT 0 NOT NULL');
  agregar_columna('USUARIOS', 'BLOQUEADO_HASTA',     'TIMESTAMP');
  agregar_columna('USUARIOS', 'ES_PRUEBA',           'NUMBER(1) DEFAULT 0 NOT NULL');
  agregar_columna('USUARIOS', 'FUSIONADO_EN',        'VARCHAR2(100)');
  agregar_columna('USUARIOS', 'ULTIMO_ACCESO',       'TIMESTAMP');
  -- UNIQUE crea su propio índice: no se agrega otro sobre email (regla 2).
  agregar_restriccion('USUARIOS', 'UQ_USUARIOS_EMAIL', 'UNIQUE (email)');
  agregar_restriccion('USUARIOS', 'CK_USUARIOS_ROL',
    'CHECK (rol IN (''alumno'', ''profesor'', ''admin''))');
  agregar_restriccion('USUARIOS', 'CK_USUARIOS_CORREO_CONFIRMADO', 'CHECK (correo_confirmado IN (0, 1))');
  agregar_restriccion('USUARIOS', 'CK_USUARIOS_ES_PRUEBA', 'CHECK (es_prueba IN (0, 1))');

  -- 2. SESIONES: id guarda el hash SHA-256 del token (64 caracteres hex).
  ampliar_columna('SESIONES', 'ID', 'VARCHAR2(64)');
  agregar_columna('SESIONES', 'CREADO_EN', 'TIMESTAMP DEFAULT SYSTIMESTAMP');
  agregar_columna('SESIONES', 'EXPIRA_EN', 'TIMESTAMP');
  -- Las sesiones de antes guardaban el uuid en claro y no sirven como token:
  -- se cierran. Las nuevas (hash de 64) no se tocan, así que repetirlo es inocuo.
  EXECUTE IMMEDIATE 'UPDATE sesiones SET activa = 0 WHERE activa = 1 AND LENGTH(id) <> 64';
  v_n := SQL%ROWCOUNT;
  COMMIT;
  DBMS_OUTPUT.PUT_LINE('Sesiones antiguas cerradas: ' || v_n);

  -- 3. PROGRESO_LECCIONES: JSON plano compacto y fecha de la primera vez que
  --    se completó (activación del alumno en el panel).
  agregar_columna('PROGRESO_LECCIONES', 'DATOS_LIGEROS', 'VARCHAR2(250 CHAR)');
  agregar_columna('PROGRESO_LECCIONES', 'COMPLETADA_EN', 'TIMESTAMP');
  agregar_restriccion('PROGRESO_LECCIONES', 'CK_PROGRESO_DATOS_JSON', 'CHECK (datos_ligeros IS JSON)');
  -- Lecciones completadas antes de existir la columna: la mejor fecha
  -- disponible es la última actualización.
  EXECUTE IMMEDIATE 'UPDATE progreso_lecciones SET completada_en = actualizado_en '
                 || 'WHERE completada = 1 AND completada_en IS NULL';
  v_n := SQL%ROWCOUNT;
  COMMIT;
  DBMS_OUTPUT.PUT_LINE('Lecciones completadas con fecha recuperada: ' || v_n);

  -- 4. LOGROS: insignias ganadas (nunca se retiran). Tabla organizada por
  --    índice (IOT): la fila vive dentro de la llave primaria y se ahorra el
  --    segmento de tabla (casi la mitad del espacio).
  crear_tabla('LOGROS', '
    CREATE TABLE logros (
      usuario_id   VARCHAR2(100) NOT NULL,
      insignia_id  VARCHAR2(100) NOT NULL,
      obtenido_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_logros PRIMARY KEY (usuario_id, insignia_id),
      CONSTRAINT fk_logros_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
    ) ORGANIZATION INDEX');

  -- 5. EVENTOS_APRENDIZAJE: eventos mínimos para medir alumnos activos.
  --    Particionada por mes: la purga quita meses completos (DROP PARTITION)
  --    en vez de borrar fila por fila. La partición inicial queda vacía: Oracle
  --    no deja quitar la última partición de rango (ORA-14758).
  v_eventos := '
    CREATE TABLE eventos_aprendizaje (
      id                  VARCHAR2(36)       NOT NULL,
      usuario_id          VARCHAR2(100)      NOT NULL,
      tipo                VARCHAR2(30)       NOT NULL,
      curso_id            VARCHAR2(50),
      leccion_id          VARCHAR2(50),
      sesion_aprendizaje  VARCHAR2(36),
      ocurrido_en         TIMESTAMP          NOT NULL,
      recibido_en         TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      version_app         VARCHAR2(20),
      es_prueba           NUMBER(1) DEFAULT 0 NOT NULL,
      datos               VARCHAR2(250 CHAR),
      CONSTRAINT pk_eventos_aprendizaje PRIMARY KEY (id),
      CONSTRAINT fk_eventos_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT ck_eventos_tipo CHECK (tipo IN (''account_created'', ''learning_session_started'',
        ''lesson_completed'', ''activity_submitted'', ''sync_succeeded'')),
      CONSTRAINT ck_eventos_es_prueba CHECK (es_prueba IN (0, 1))
    )';
  IF NOT existe_tabla('EVENTOS_APRENDIZAJE') THEN
    BEGIN
      ejecutar(v_eventos || '
    PARTITION BY RANGE (ocurrido_en) INTERVAL (NUMTOYMINTERVAL(1, ''MONTH''))
    (PARTITION p_inicial VALUES LESS THAN (TIMESTAMP ''2026-01-01 00:00:00''))');
    EXCEPTION
      WHEN OTHERS THEN
        -- ORA-00439: la base no tiene particionamiento. Funciona igual; la
        -- purga de 003 borra por lotes con DELETE.
        IF SQLCODE = -439 THEN
          DBMS_OUTPUT.PUT_LINE('Aviso: sin particionamiento; la purga usará DELETE por lotes.');
          ejecutar(v_eventos);
        ELSE
          RAISE;
        END IF;
    END;
  END IF;
  crear_indice('IX_EVENTOS_USUARIO_FECHA', 'EVENTOS_APRENDIZAJE', 'usuario_id, ocurrido_en', TRUE);
  crear_indice('IX_EVENTOS_FECHA', 'EVENTOS_APRENDIZAJE', 'ocurrido_en', TRUE);

  -- 6. Contenido que administra el panel: CURSOS, MODULOS y LECCIONES.
  --    Textos para personas en CHAR: los acentos no restan espacio al límite.
  crear_tabla('CURSOS', '
    CREATE TABLE cursos (
      id              VARCHAR2(50)        NOT NULL,
      numero          VARCHAR2(5),
      titulo          VARCHAR2(100 CHAR)  NOT NULL,
      subtitulo       VARCHAR2(150 CHAR),
      descripcion     VARCHAR2(1000 CHAR),
      nivel           VARCHAR2(30 CHAR),
      acento          VARCHAR2(20),
      recurso_texto   VARCHAR2(100 CHAR),
      recurso_url     VARCHAR2(300 CHAR),
      orden           NUMBER(5) DEFAULT 0 NOT NULL,
      estado          VARCHAR2(12) DEFAULT ''publicado'' NOT NULL,
      actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_cursos PRIMARY KEY (id),
      CONSTRAINT ck_cursos_estado CHECK (estado IN (''borrador'', ''publicado'', ''archivado''))
    )');

  crear_tabla('MODULOS', '
    CREATE TABLE modulos (
      id              VARCHAR2(50)        NOT NULL,
      curso_id        VARCHAR2(50)        NOT NULL,
      numero          NUMBER(5)           NOT NULL,
      titulo          VARCHAR2(200 CHAR)  NOT NULL,
      descripcion     VARCHAR2(1000 CHAR),
      insignia        VARCHAR2(80 CHAR),
      minutos         NUMBER(5),
      estado          VARCHAR2(12) DEFAULT ''borrador'' NOT NULL,
      version         NUMBER(10) DEFAULT 1 NOT NULL,
      actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      publicado_en    TIMESTAMP,
      CONSTRAINT pk_modulos PRIMARY KEY (id),
      CONSTRAINT fk_modulos_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
      CONSTRAINT ck_modulos_estado CHECK (estado IN (''borrador'', ''publicado'', ''archivado''))
    )');
  crear_indice('IX_MODULOS_CURSO', 'MODULOS', 'curso_id, numero');

  -- contenido: el JSON completo de la lección (mismo formato que
  -- frontend/src/data/modulos/*.json). IS JSON rechaza texto mal formado.
  -- La llave primaria (curso_id, id) también cubre la llave foránea a CURSOS.
  crear_tabla('LECCIONES', '
    CREATE TABLE lecciones (
      curso_id           VARCHAR2(50)        NOT NULL,
      id                 VARCHAR2(50)        NOT NULL,
      modulo_id          VARCHAR2(50)        NOT NULL,
      orden              NUMBER(5)           NOT NULL,
      titulo             VARCHAR2(200 CHAR)  NOT NULL,
      tipo               VARCHAR2(30)        NOT NULL,
      duracion_segundos  NUMBER(7),
      bloqueada          NUMBER(1) DEFAULT 1 NOT NULL,
      contenido          CLOB                NOT NULL,
      reemplaza          VARCHAR2(250),
      estado             VARCHAR2(12) DEFAULT ''borrador'' NOT NULL,
      version            NUMBER(10) DEFAULT 1 NOT NULL,
      actualizado_en     TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      publicado_en       TIMESTAMP,
      CONSTRAINT pk_lecciones PRIMARY KEY (curso_id, id),
      CONSTRAINT fk_lecciones_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
      CONSTRAINT fk_lecciones_modulo FOREIGN KEY (modulo_id) REFERENCES modulos (id),
      CONSTRAINT ck_lecciones_contenido CHECK (contenido IS JSON),
      CONSTRAINT ck_lecciones_bloqueada CHECK (bloqueada IN (0, 1)),
      CONSTRAINT ck_lecciones_estado CHECK (estado IN (''borrador'', ''publicado'', ''archivado''))
    )');
  crear_indice('IX_LECCIONES_MODULO', 'LECCIONES', 'modulo_id, orden');

  DBMS_OUTPUT.PUT_LINE('002 aplicado. Sigue con 003_mantenimiento.sql.');
END;
/

-- 7. Verificar: las 8 tablas deben decir OK (columnas reales = esperadas).
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
        UNION ALL SELECT 'MODULOS', 11 FROM dual
        UNION ALL SELECT 'LECCIONES', 14 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- Restricciones con nombre (llaves, UNIQUE de email y CHECK) e índices.
SELECT table_name, constraint_name, constraint_type
  FROM user_constraints
 WHERE table_name IN ('USUARIOS', 'SESIONES', 'PROGRESO_LECCIONES', 'LOGROS',
                      'EVENTOS_APRENDIZAJE', 'CURSOS', 'MODULOS', 'LECCIONES')
   AND constraint_name NOT LIKE 'SYS%'
 ORDER BY table_name, constraint_type, constraint_name;

SELECT table_name, index_name, uniqueness, partitioned
  FROM user_indexes
 WHERE table_name IN ('USUARIOS', 'SESIONES', 'PROGRESO_LECCIONES', 'LOGROS',
                      'EVENTOS_APRENDIZAJE', 'CURSOS', 'MODULOS', 'LECCIONES')
   AND index_type <> 'LOB'
 ORDER BY table_name, index_name;
