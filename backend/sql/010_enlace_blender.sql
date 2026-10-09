-- =============================================================================
-- AMATISTA · 010 · ENLACE EN VIVO PLATAFORMA ↔ BLENDER (motor 3.4)
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- Con esto la plataforma «administra» Blender:
--   - ADDON_ENLACES ... un Blender abierto y conectado: su último latido
--                       (práctica, paso, progreso, modo enfocado) y la orden
--                       pendiente que la plataforma le deja («abre esta
--                       práctica»). Una fila por sesión del add-on.
--   - ADDON_AJUSTES ... cómo se ve Blender para cada alumno (modo enfocado,
--                       acompañamiento, avisos), decidido en «Mi Blender».
--
-- Script INCREMENTAL e IDEMPOTENTE, igual que 007:
--   - NO borra tablas ni filas: solo CREATE.
--   - Antes de cada cambio consulta el diccionario; se puede repetir.
--   - No toca usuarios, progreso, prácticas ni contenido.
--
-- Orden: ... -> 007 -> 008 -> 009 -> 010.
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5).
--
-- Sin estas tablas el add-on y la plataforma siguen funcionando como antes
-- (la API responde «sin enlace en vivo»): se puede ejecutar antes o después
-- de actualizar el backend.
--
-- ESPACIO (bytes por fila con índices; ver 002 para el método):
--   ADDON_ENLACES ~400 x 1 Blender por alumno ........ ~16 MB con 40,000
--   ADDON_AJUSTES ~200 x 1 por alumno ................ ~8 MB con 40,000
--   El latido solo reescribe la fila cada 15 s o al cambiar algo.
--   Cuando la purga (003) borra una sesión vieja, su enlace se borra con ella
--   (ON DELETE CASCADE): 003 no se edita y sigue funcionando igual.
--
-- ÍNDICES (regla 2: nada sobre columnas que ya son PRIMARY KEY o UNIQUE):
--   - ix_addon_enlaces_usuario (usuario_id): los Blender de un alumno.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
  v_tablas  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST('ADDON_ENLACES', 'ADDON_AJUSTES');

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
        || '. Ejecuta antes 007_motor_practicas.sql (y 002 si falta).');
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
  -- 0. Punto de partida: 002 (usuarios, sesiones) y 007 (motor de prácticas).
  exigir_tabla('USUARIOS');
  exigir_tabla('SESIONES');
  exigir_tabla('PRACTICAS');

  -- 1. ADDON_ENLACES: el latido de cada Blender conectado y su orden pendiente.
  crear_tabla('ADDON_ENLACES', '
    CREATE TABLE addon_enlaces (
      sesion_id        VARCHAR2(64)        NOT NULL,
      usuario_id       VARCHAR2(100)       NOT NULL,
      visto_en         TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      practica_id      VARCHAR2(80),
      paso             VARCHAR2(80),
      progreso         NUMBER(3),
      enfocado         NUMBER(1) DEFAULT 0 NOT NULL,
      version_addon    VARCHAR2(20),
      version_blender  VARCHAR2(20),
      orden            VARCHAR2(1000 CHAR),
      orden_en         TIMESTAMP,
      CONSTRAINT pk_addon_enlaces PRIMARY KEY (sesion_id),
      CONSTRAINT fk_enlaces_sesion FOREIGN KEY (sesion_id) REFERENCES sesiones (id) ON DELETE CASCADE,
      CONSTRAINT fk_enlaces_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT ck_enlaces_progreso CHECK (progreso IS NULL OR progreso BETWEEN 0 AND 100),
      CONSTRAINT ck_enlaces_enfocado CHECK (enfocado IN (0, 1)),
      CONSTRAINT ck_enlaces_orden CHECK (orden IS JSON)
    )');
  crear_indice('IX_ADDON_ENLACES_USUARIO', 'ADDON_ENLACES', 'usuario_id');

  -- 2. ADDON_AJUSTES: cómo se ve Blender para cada alumno (Mi Blender).
  crear_tabla('ADDON_AJUSTES', '
    CREATE TABLE addon_ajustes (
      usuario_id       VARCHAR2(100)       NOT NULL,
      datos            VARCHAR2(1000 CHAR) NOT NULL,
      actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_addon_ajustes PRIMARY KEY (usuario_id),
      CONSTRAINT fk_ajustes_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT ck_ajustes_datos CHECK (datos IS JSON)
    )');

  -- 3. Si ya se usa AMATISTA_APP (004), recibe permisos sobre las tablas
  --    nuevas. Volver a ejecutar 004 hace lo mismo.
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_tablas.COUNT LOOP
      ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app');
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('010 aplicado. Reinicia el backend: sudo systemctl restart amatista-backend');
END;
/

-- 4. Verificar: las 20 tablas deben decir OK (columnas reales = esperadas).
--    Esta consulta sustituye a la de 008 (se suman ADDON_ENLACES y ADDON_AJUSTES).
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
        UNION ALL SELECT 'HABILIDADES_ALUMNO', 5 FROM dual
        UNION ALL SELECT 'EVALUACIONES_RUBRICA', 8 FROM dual
        UNION ALL SELECT 'VERSIONES_BLENDER', 6 FROM dual
        UNION ALL SELECT 'VERIFICACIONES_BLENDER', 12 FROM dual
        UNION ALL SELECT 'ADDON_VINCULOS', 8 FROM dual
        UNION ALL SELECT 'PRACTICAS', 13 FROM dual
        UNION ALL SELECT 'PRACTICA_VERSIONES', 9 FROM dual
        UNION ALL SELECT 'PROGRESO_PRACTICAS', 16 FROM dual
        UNION ALL SELECT 'ADDON_ENLACES', 11 FROM dual
        UNION ALL SELECT 'ADDON_AJUSTES', 3 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 5. Los Blender conectados ahora mismo (último latido hace menos de un minuto).
SELECT COUNT(*) AS blender_en_linea
  FROM addon_enlaces
 WHERE visto_en > SYSTIMESTAMP - INTERVAL '1' MINUTE;

SELECT COUNT(*) AS alumnos_con_ajustes FROM addon_ajustes;
