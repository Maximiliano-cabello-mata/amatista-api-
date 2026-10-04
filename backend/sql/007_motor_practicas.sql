-- =============================================================================
-- AMATISTA · 007 · MOTOR DE PRÁCTICAS DE BLENDER (Amatista Engine + add-on)
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- Agrega lo que necesitan el add-on de Blender y las prácticas del motor
-- (docs/motor/):
--   - ADDON_VINCULOS ...... código para conectar un Blender con una cuenta
--                           (se canjea una vez por una sesión de SESIONES;
--                           las filas vencidas las borra la API sola).
--   - PRACTICAS ........... cada práctica (practice.json) con su versión
--                           vigente y la versión publicada para los alumnos.
--   - PRACTICA_VERSIONES .. historial: cada versión subida, quién y desde
--                           dónde. Subir NUNCA publica; publicar es del admin.
--   - PROGRESO_PRACTICAS .. avance de cada alumno en cada práctica (una fila
--                           por par). El detalle de cada envío va a
--                           EVENTOS_APRENDIZAJE (activity_submitted), que ya
--                           tiene retención y purga (003).
--   - V_AMATISTA_PRACTICAS  vista: prácticas con alumnos, completadas y
--                           progreso promedio.
--   - Habilidades de la primera práctica («Construir una mesa») si el curso
--     blender y su nivel 1 existen. Solo inserta las que faltan.
--
-- Script INCREMENTAL e IDEMPOTENTE, igual que 002 y 005:
--   - NO borra tablas ni filas: solo CREATE y MERGE que solo inserta.
--   - Antes de cada cambio consulta el diccionario; se puede repetir.
--   - No toca usuarios, progreso de lecciones, insignias ni contenido.
--
-- Orden: 001 -> 002 -> 003 -> (004) -> 005 -> 006 -> 007.
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5). Guía completa:
-- docs/reestructuracion/02_manual_oracle.md, sección 7.
--
-- IMPORTANTE: ejecútalo ANTES de actualizar el backend a esta versión. Sin
-- estas tablas, las rutas /api/addon/v1/... responden 503; el resto de la
-- plataforma (lecciones, progreso, panel) sigue funcionando igual.
--
-- ESPACIO (bytes por fila con índices; ver 002 para el método):
--   ADDON_VINCULOS ~250, vive minutos: siempre < 100 KB.
--   PRACTICAS ~6 KB (CLOB en línea) x ~200 prácticas ............. ~1 MB
--   PRACTICA_VERSIONES ~6 KB x ~5 versiones por práctica ......... ~6 MB
--   PROGRESO_PRACTICAS ~250 x ~40 prácticas por alumno ...... ~10 KB/alumno
--   Total permanente por alumno pasa de ~22 KB a ~32 KB: 5 GB siguen
--   alcanzando para más de 150,000 alumnos.
--
-- ÍNDICES (regla 2: nada sobre columnas que ya son PRIMARY KEY o UNIQUE):
--   - uq_vinculos_codigo: el UNIQUE crea su índice (búsqueda por código).
--   - ix_practicas_leccion (curso_id, leccion_id): práctica de una lección.
--   - ix_prog_practicas_practica (practica_id): alumnos de una práctica
--     (vista y panel) y llave foránea hacia PRACTICAS. Las consultas por
--     alumno usan la llave primaria (usuario_id, practica_id).
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
  v_tablas  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST(
              'ADDON_VINCULOS', 'PRACTICAS', 'PRACTICA_VERSIONES', 'PROGRESO_PRACTICAS');

  -- Ejecuta un DDL dinámico y lo deja anotado en la salida del script.
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

  PROCEDURE exigir_tabla(p_tabla IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      RAISE_APPLICATION_ERROR(-20001, 'Falta la tabla ' || p_tabla
        || '. Ejecuta antes 005_niveles_habilidades_versiones.sql (y 002 si falta).');
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
  -- 0. Punto de partida: 002 (usuarios, cursos) y 005 (niveles, habilidades).
  exigir_tabla('USUARIOS');
  exigir_tabla('SESIONES');
  exigir_tabla('CURSOS');
  exigir_tabla('NIVELES');
  exigir_tabla('HABILIDADES');

  -- 1. ADDON_VINCULOS: el add-on muestra un código (ABCD-2345), el alumno lo
  --    escribe en la plataforma y Blender queda conectado. Solo el hash del
  --    secreto; el token de la sesión nunca se guarda en claro.
  crear_tabla('ADDON_VINCULOS', '
    CREATE TABLE addon_vinculos (
      id             VARCHAR2(36)        NOT NULL,
      codigo         VARCHAR2(9)         NOT NULL,
      secreto_hash   VARCHAR2(64)        NOT NULL,
      usuario_id     VARCHAR2(100),
      dispositivo    VARCHAR2(200 CHAR),
      estado         VARCHAR2(10) DEFAULT ''pendiente'' NOT NULL,
      creado_en      TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      expira_en      TIMESTAMP           NOT NULL,
      CONSTRAINT pk_addon_vinculos PRIMARY KEY (id),
      CONSTRAINT uq_vinculos_codigo UNIQUE (codigo),
      CONSTRAINT fk_vinculos_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT ck_vinculos_estado CHECK (estado IN (''pendiente'', ''listo'', ''canjeado''))
    )');

  -- 2. PRACTICAS: la definición vigente (CLOB con JSON) y la publicada.
  crear_tabla('PRACTICAS', '
    CREATE TABLE practicas (
      id                 VARCHAR2(80)        NOT NULL,
      curso_id           VARCHAR2(50),
      leccion_id         VARCHAR2(50),
      titulo             VARCHAR2(200 CHAR)  NOT NULL,
      nivel              NUMBER(1) DEFAULT 1 NOT NULL,
      version            NUMBER(10) DEFAULT 1 NOT NULL,
      version_publicada  NUMBER(10),
      definicion         CLOB                NOT NULL,
      estado             VARCHAR2(12) DEFAULT ''borrador'' NOT NULL,
      origen             VARCHAR2(12) DEFAULT ''addon'' NOT NULL,
      autor_id           VARCHAR2(100),
      actualizado_en     TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      publicado_en       TIMESTAMP,
      CONSTRAINT pk_practicas PRIMARY KEY (id),
      CONSTRAINT fk_practicas_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
      CONSTRAINT fk_practicas_autor FOREIGN KEY (autor_id) REFERENCES usuarios (id),
      CONSTRAINT ck_practicas_definicion CHECK (definicion IS JSON),
      CONSTRAINT ck_practicas_nivel CHECK (nivel IN (1, 2, 3, 4, 5)),
      CONSTRAINT ck_practicas_estado CHECK (estado IN (''borrador'', ''publicado'', ''archivado'')),
      CONSTRAINT ck_practicas_origen CHECK (origen IN (''repositorio'', ''addon'', ''panel'')),
      CONSTRAINT ck_practicas_publicada CHECK (version_publicada IS NULL OR version_publicada <= version)
    )');
  crear_indice('IX_PRACTICAS_LECCION', 'PRACTICAS', 'curso_id, leccion_id');

  -- 3. PRACTICA_VERSIONES: una fila por versión subida (nunca se reescribe).
  crear_tabla('PRACTICA_VERSIONES', '
    CREATE TABLE practica_versiones (
      practica_id      VARCHAR2(80)        NOT NULL,
      version          NUMBER(10)          NOT NULL,
      definicion       CLOB                NOT NULL,
      huella           VARCHAR2(64)        NOT NULL,
      nota             VARCHAR2(500 CHAR),
      autor_id         VARCHAR2(100),
      version_addon    VARCHAR2(20),
      version_blender  VARCHAR2(20),
      creado_en        TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_practica_versiones PRIMARY KEY (practica_id, version),
      CONSTRAINT fk_prac_versiones_practica FOREIGN KEY (practica_id) REFERENCES practicas (id),
      CONSTRAINT fk_prac_versiones_autor FOREIGN KEY (autor_id) REFERENCES usuarios (id),
      CONSTRAINT ck_prac_versiones_definicion CHECK (definicion IS JSON)
    )');

  -- 4. PROGRESO_PRACTICAS: el mejor avance de cada alumno en cada práctica.
  crear_tabla('PROGRESO_PRACTICAS', '
    CREATE TABLE progreso_practicas (
      usuario_id       VARCHAR2(100)       NOT NULL,
      practica_id      VARCHAR2(80)        NOT NULL,
      version          NUMBER(10) DEFAULT 1 NOT NULL,
      progreso         NUMBER(3) DEFAULT 0 NOT NULL,
      completada       NUMBER(1) DEFAULT 0 NOT NULL,
      autonomia        VARCHAR2(14),
      pistas           NUMBER(5) DEFAULT 0 NOT NULL,
      correcciones     NUMBER(5) DEFAULT 0 NOT NULL,
      intentos         NUMBER(10) DEFAULT 0 NOT NULL,
      paso_actual      VARCHAR2(80),
      objetivos        VARCHAR2(1000 CHAR),
      version_blender  VARCHAR2(20),
      version_addon    VARCHAR2(20),
      abierta_en       TIMESTAMP,
      completada_en    TIMESTAMP,
      actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_progreso_practicas PRIMARY KEY (usuario_id, practica_id),
      CONSTRAINT fk_prog_practicas_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT fk_prog_practicas_practica FOREIGN KEY (practica_id) REFERENCES practicas (id),
      CONSTRAINT ck_prog_practicas_progreso CHECK (progreso BETWEEN 0 AND 100),
      CONSTRAINT ck_prog_practicas_completada CHECK (completada IN (0, 1)),
      CONSTRAINT ck_prog_practicas_autonomia CHECK (autonomia IN (''con_guia'', ''con_pistas'', ''autonoma'')),
      CONSTRAINT ck_prog_practicas_objetivos CHECK (objetivos IS JSON)
    )');
  crear_indice('IX_PROG_PRACTICAS_PRACTICA', 'PROGRESO_PRACTICAS', 'practica_id');

  -- 5. Habilidades de «Construir una mesa» (practices/blender/level_1/mesa.json).
  --    Solo si existen el curso blender y su nivel 1; solo inserta las que faltan.
  SELECT COUNT(*) INTO v_cuenta FROM niveles WHERE id = 'blender-n1' AND curso_id = 'blender';
  IF v_cuenta > 0 THEN
    MERGE INTO habilidades h
    USING (SELECT 'bl-transformar' AS id, 'Mover, rotar y escalar objetos' AS nombre, 10 AS orden FROM dual
           UNION ALL SELECT 'bl-duplicar', 'Duplicar objetos y conservar su forma', 20 FROM dual
           UNION ALL SELECT 'bl-proporciones', 'Medir y ajustar proporciones con Dimensiones', 30 FROM dual
           UNION ALL SELECT 'bl-guardar-archivo', 'Guardar el archivo .blend', 40 FROM dual) s
       ON (h.id = s.id)
     WHEN NOT MATCHED THEN
       INSERT (id, curso_id, nivel_id, nombre, orden)
       VALUES (s.id, 'blender', 'blender-n1', s.nombre, s.orden);
    DBMS_OUTPUT.PUT_LINE('Habilidades nuevas del nivel 1: ' || SQL%ROWCOUNT);
    COMMIT;
  ELSE
    DBMS_OUTPUT.PUT_LINE('Sin nivel blender-n1: las habilidades de la mesa se agregan después (repite este script).');
  END IF;

  -- 6. Si ya se usa AMATISTA_APP (004), recibe permisos sobre las tablas
  --    nuevas. Volver a ejecutar 004 hace lo mismo.
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_tablas.COUNT LOOP
      ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app');
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('007 aplicado. Reinicia el backend: sudo systemctl restart amatista-backend');
END;
/

-- 7. Vista para revisar las prácticas desde Database Actions.
CREATE OR REPLACE VIEW v_amatista_practicas AS
SELECT p.id,
       p.titulo,
       p.nivel,
       p.estado,
       p.version,
       p.version_publicada,
       p.origen,
       p.curso_id,
       p.leccion_id,
       u.email                                                        AS autor,
       p.actualizado_en,
       p.publicado_en,
       (SELECT COUNT(*) FROM progreso_practicas pp WHERE pp.practica_id = p.id)                      AS alumnos,
       (SELECT COUNT(*) FROM progreso_practicas pp WHERE pp.practica_id = p.id AND pp.completada = 1) AS completadas,
       (SELECT ROUND(AVG(pp.progreso)) FROM progreso_practicas pp WHERE pp.practica_id = p.id)       AS progreso_promedio
  FROM practicas p
  LEFT JOIN usuarios u ON u.id = p.autor_id;

-- 8. Verificar: las 18 tablas deben decir OK (columnas reales = esperadas).
--    Esta consulta sustituye a la de 005.
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
        UNION ALL SELECT 'VERIFICACIONES_BLENDER', 12 FROM dual
        UNION ALL SELECT 'ADDON_VINCULOS', 8 FROM dual
        UNION ALL SELECT 'PRACTICAS', 13 FROM dual
        UNION ALL SELECT 'PRACTICA_VERSIONES', 9 FROM dual
        UNION ALL SELECT 'PROGRESO_PRACTICAS', 16 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 9. Filas de las tablas nuevas y la vista (el resto no cambia).
SELECT 'ADDON_VINCULOS' AS tabla, COUNT(*) AS filas FROM addon_vinculos
UNION ALL SELECT 'PRACTICAS', COUNT(*) FROM practicas
UNION ALL SELECT 'PRACTICA_VERSIONES', COUNT(*) FROM practica_versiones
UNION ALL SELECT 'PROGRESO_PRACTICAS', COUNT(*) FROM progreso_practicas
UNION ALL SELECT 'Habilidades bl- del nivel 1', COUNT(*) FROM habilidades WHERE nivel_id = 'blender-n1';

SELECT object_name, object_type, status
  FROM user_objects
 WHERE object_name = 'V_AMATISTA_PRACTICAS';
