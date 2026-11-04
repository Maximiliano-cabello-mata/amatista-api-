-- =============================================================================
-- AMATISTA · 014 · AMATISTA CLOUD
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- Amatista Cloud (docs/nube/README.md): una hora de Blender en un nodo de la
-- nube (Oracle) o de una computadora del equipo, solo para cuentas de la UTN.
--   - NUBE_SESIONES ........ una fila por sesión: quién la pidió, en qué nodo,
--                            cuándo vence (60 minutos) y por qué terminó.
--                            Es la bitácora de seguridad y la cuota diaria
--                            (2 sesiones por cuenta al día, configurable).
--                            El token de acceso NO se guarda: solo su hash
--                            SHA-256, igual que en SESIONES.
--   El estado vivo de los nodos (capacidad, latidos) vive en la memoria de la
--   API: los nodos se vuelven a anunciar solos cada 10 segundos.
--
-- Script INCREMENTAL e IDEMPOTENTE, igual que 010 a 013:
--   - NO borra tablas ni filas: solo CREATE.
--   - Antes de cada cambio consulta el diccionario; se puede repetir.
--   - No toca usuarios, progreso, prácticas ni contenido.
--
-- Orden: ... -> 012 -> 013 -> 014.
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5).
--
-- Sin 014 el backend arranca igual: Amatista Cloud responde 503 con un
-- mensaje claro y el resto de la plataforma no cambia.
--
-- ESPACIO: ~250 B por sesión con índices. 5 sesiones a la vez, 10 horas al
-- día y 200 días al año: ~10,000 filas, ~2.5 MB al año.
--
-- ÍNDICES (regla 2: nada sobre columnas que ya son PRIMARY KEY o UNIQUE):
--   IX_NUBE_SESIONES_USUARIO (cuota diaria y sesión activa de un alumno) e
--   IX_NUBE_SESIONES_ESTADO (sesiones vivas que vencen, para cerrarlas).
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
  v_tablas  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST('NUBE_SESIONES');

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

  -- 1. NUBE_SESIONES: una hora de Blender en la nube por fila.
  crear_tabla('NUBE_SESIONES', '
    CREATE TABLE nube_sesiones (
      id               VARCHAR2(40)  NOT NULL,
      usuario_id       VARCHAR2(100) NOT NULL,
      nodo             VARCHAR2(60)  NOT NULL,
      estado           VARCHAR2(20)  DEFAULT ''asignada'' NOT NULL,
      token_hash       VARCHAR2(64)  NOT NULL,
      creada_en        TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
      expira_en        TIMESTAMP     NOT NULL,
      terminada_en     TIMESTAMP,
      motivo_fin       VARCHAR2(40),
      CONSTRAINT pk_nube_sesiones PRIMARY KEY (id),
      CONSTRAINT fk_nube_sesiones_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT ck_nube_sesiones_estado CHECK (estado IN (''asignada'', ''lista'', ''terminada'', ''fallida''))
    )');
  IF NOT existe_indice('IX_NUBE_SESIONES_USUARIO') THEN
    ejecutar('CREATE INDEX ix_nube_sesiones_usuario ON nube_sesiones (usuario_id, creada_en)');
  END IF;
  IF NOT existe_indice('IX_NUBE_SESIONES_ESTADO') THEN
    ejecutar('CREATE INDEX ix_nube_sesiones_estado ON nube_sesiones (estado, expira_en)');
  END IF;

  -- 2. Si ya se usa AMATISTA_APP (004), recibe permisos sobre la tabla nueva.
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_tablas.COUNT LOOP
      ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app');
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('014 aplicado. Actualiza el backend y reinicia: sudo systemctl restart amatista-backend');
END;
/

-- 3. Verificar: las 25 tablas deben decir OK (columnas reales = esperadas).
--    Sustituye a la consulta de 013 (se suma NUBE_SESIONES).
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
        UNION ALL SELECT 'NUBE_SESIONES', 9 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 4. Filas: sesiones de Amatista Cloud por estado.
SELECT estado, COUNT(*) AS nube_sesiones FROM nube_sesiones GROUP BY estado;

SELECT COUNT(*) AS nube_sesiones FROM nube_sesiones;
