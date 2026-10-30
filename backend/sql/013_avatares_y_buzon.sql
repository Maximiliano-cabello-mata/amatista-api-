-- =============================================================================
-- AMATISTA · 013 · AVATARES Y BUZÓN DEL CREADOR
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- Personajes de la plataforma (docs/plataforma/16_avatares_y_buzon.md):
--   - PERFILES ............. el avatar low poly que eligió cada cuenta (un id
--                            corto del catálogo de la PWA: 'zorro', 'ajolote'…;
--                            'pavo_real' solo para el creador). Tabla aparte
--                            y no una columna de USUARIOS: si el backend nuevo
--                            corre sin 013, nada de lo de antes se rompe.
--   - SUGERENCIAS .......... una conversación del buzón del creador: quién la
--                            abrió, de qué trata (error, idea, contenido u
--                            otra), desde qué página y si sigue abierta.
--   - SUGERENCIA_MENSAJES .. los mensajes de cada conversación, del alumno o
--                            del creador (DE_CREADOR = 1).
--
-- Script INCREMENTAL e IDEMPOTENTE, igual que 010, 011 y 012:
--   - NO borra tablas ni filas: solo CREATE.
--   - Antes de cada cambio consulta el diccionario; se puede repetir.
--   - No toca usuarios, progreso, prácticas ni contenido.
--
-- Orden: ... -> 010 -> 011 -> 012 -> 013.
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5).
--
-- Sin 013 el backend arranca igual: el avatar se queda en el dispositivo
-- (GET /api/perfil responde «guardado»: false) y el buzón responde 503 con un
-- mensaje claro.
--
-- ESPACIO (bytes por fila con índices; ver 002 para el método):
--   PERFILES ~80 por cuenta ............................... ~80 B/alumno
--   SUGERENCIAS ~150 y SUGERENCIA_MENSAJES ~600 (2000 caracteres como
--   máximo, la mayoría mucho menos). Con 5 conversaciones de 4 mensajes por
--   alumno que escribe: ~13 KB. El buzón limita a 10 mensajes por hora y
--   cuenta, así que no puede crecer sin control.
--
-- ÍNDICES (regla 2: nada sobre columnas que ya son PRIMARY KEY o UNIQUE):
--   IX_SUGERENCIAS_USUARIO (las conversaciones de un alumno),
--   IX_SUGERENCIAS_ESTADO (la bandeja del creador, abiertas primero) e
--   IX_SUG_MENSAJES_SUG (los mensajes de una conversación).
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
  v_tablas  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST('PERFILES', 'SUGERENCIAS', 'SUGERENCIA_MENSAJES');

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
  -- 0. Punto de partida: 002 (usuarios).
  exigir_tabla('USUARIOS', '002_autenticacion_contenido_eventos.sql');

  -- 1. PERFILES: el avatar de cada cuenta.
  crear_tabla('PERFILES', '
    CREATE TABLE perfiles (
      usuario_id       VARCHAR2(100) NOT NULL,
      avatar           VARCHAR2(40),
      actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_perfiles PRIMARY KEY (usuario_id),
      CONSTRAINT fk_perfiles_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
    )');

  -- 2. SUGERENCIAS: una conversación del buzón del creador.
  crear_tabla('SUGERENCIAS', '
    CREATE TABLE sugerencias (
      id               VARCHAR2(40)  NOT NULL,
      usuario_id       VARCHAR2(100) NOT NULL,
      categoria        VARCHAR2(20)  NOT NULL,
      pagina           VARCHAR2(200),
      estado           VARCHAR2(20)  DEFAULT ''abierta'' NOT NULL,
      sin_leer_alumno  NUMBER(1)     DEFAULT 0 NOT NULL,
      sin_leer_creador NUMBER(1)     DEFAULT 1 NOT NULL,
      creada_en        TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      actualizada_en   TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_sugerencias PRIMARY KEY (id),
      CONSTRAINT fk_sugerencias_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT ck_sugerencias_categoria CHECK (categoria IN (''error'', ''idea'', ''contenido'', ''otra'')),
      CONSTRAINT ck_sugerencias_estado CHECK (estado IN (''abierta'', ''respondida'', ''resuelta'')),
      CONSTRAINT ck_sugerencias_leer_a CHECK (sin_leer_alumno IN (0, 1)),
      CONSTRAINT ck_sugerencias_leer_c CHECK (sin_leer_creador IN (0, 1))
    )');
  IF NOT existe_indice('IX_SUGERENCIAS_USUARIO') THEN
    ejecutar('CREATE INDEX ix_sugerencias_usuario ON sugerencias (usuario_id, actualizada_en)');
  END IF;
  IF NOT existe_indice('IX_SUGERENCIAS_ESTADO') THEN
    ejecutar('CREATE INDEX ix_sugerencias_estado ON sugerencias (estado, actualizada_en)');
  END IF;

  -- 3. SUGERENCIA_MENSAJES: los mensajes de cada conversación.
  crear_tabla('SUGERENCIA_MENSAJES', '
    CREATE TABLE sugerencia_mensajes (
      id               VARCHAR2(40)   NOT NULL,
      sugerencia_id    VARCHAR2(40)   NOT NULL,
      autor_id         VARCHAR2(100)  NOT NULL,
      de_creador       NUMBER(1)      DEFAULT 0 NOT NULL,
      texto            VARCHAR2(2000) NOT NULL,
      creado_en        TIMESTAMP      DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_sugerencia_mensajes PRIMARY KEY (id),
      CONSTRAINT fk_sug_mensajes_sug FOREIGN KEY (sugerencia_id) REFERENCES sugerencias (id),
      CONSTRAINT fk_sug_mensajes_autor FOREIGN KEY (autor_id) REFERENCES usuarios (id),
      CONSTRAINT ck_sug_mensajes_creador CHECK (de_creador IN (0, 1))
    )');
  IF NOT existe_indice('IX_SUG_MENSAJES_SUG') THEN
    ejecutar('CREATE INDEX ix_sug_mensajes_sug ON sugerencia_mensajes (sugerencia_id, creado_en)');
  END IF;

  -- 4. Si ya se usa AMATISTA_APP (004), recibe permisos sobre las tablas nuevas.
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_tablas.COUNT LOOP
      ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app');
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('013 aplicado. Actualiza el backend y reinicia: sudo systemctl restart amatista-backend');
END;
/

-- 5. Verificar: las 24 tablas deben decir OK (columnas reales = esperadas).
--    Sustituye a la consulta de 012 (se suman PERFILES, SUGERENCIAS y
--    SUGERENCIA_MENSAJES).
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
        UNION ALL SELECT 'SUGERENCIA_MENSAJES', 6 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 6. Filas: avatares elegidos y conversaciones del buzón.
SELECT COUNT(*) AS perfiles FROM perfiles;

SELECT COUNT(*) AS sugerencias FROM sugerencias;

SELECT COUNT(*) AS sugerencia_mensajes FROM sugerencia_mensajes;
