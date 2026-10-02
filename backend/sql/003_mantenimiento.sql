-- =============================================================================
-- AMATISTA · 003 · MANTENIMIENTO DE LOS 20 GB
-- (Oracle Autonomous Database ATP 26ai · Free Tier)
--
-- Crea o reemplaza:
--   1. AMATISTA_PURGAR(p_dias_sesiones DEFAULT 90, p_dias_eventos DEFAULT 400)
--      - SESIONES: borra las cerradas (activa = 0), las vencidas (expira_en)
--        y las que llevan p_dias_sesiones días sin acceso.
--      - EVENTOS_APRENDIZAJE: quita los meses completos más viejos que
--        p_dias_eventos con DROP PARTITION (instantáneo, sin undo) y borra el
--        resto por lotes con DELETE (también si la tabla no está particionada).
--      Nunca toca USUARIOS, PROGRESO_LECCIONES, LOGROS ni el contenido.
--   2. El job diario AMATISTA_PURGA_DIARIA (09:15 UTC = 03:15 en UTC-6).
--   3. La vista V_AMATISTA_ESPACIO: MB por segmento y % de los 20 GB.
--   4. Consultas de espacio por tabla y filas por tabla.
--
-- IDEMPOTENTE: CREATE OR REPLACE, y el job se borra y se vuelve a crear.
-- Requisito: 002 ya aplicado. Ejecútalo con el dueño de las tablas (ADMIN)
-- en Database Actions > SQL con "Ejecutar script" (F5).
--
-- Purga manual (por ejemplo, conservar solo 180 días de eventos):
--   BEGIN amatista_purgar(p_dias_sesiones => 90, p_dias_eventos => 180); END;
--   /
-- El panel de administración hace lo mismo desde la API
-- (POST /api/admin/mantenimiento/purgar), con DELETE y sin quitar particiones.
--
-- Si V_AMATISTA_ESPACIO muestra segmentos BIN$..., son tablas en la papelera
-- de reciclaje: PURGE RECYCLEBIN; los libera.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

-- 0. Requisito: 002 (columna SESIONES.EXPIRA_EN y tabla EVENTOS_APRENDIZAJE).
DECLARE
  v_cuenta PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_cuenta
    FROM user_tab_columns
   WHERE table_name = 'SESIONES' AND column_name = 'EXPIRA_EN';
  IF v_cuenta = 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'Falta SESIONES.EXPIRA_EN: ejecuta primero 002_autenticacion_contenido_eventos.sql.');
  END IF;
  SELECT COUNT(*) INTO v_cuenta FROM user_tables WHERE table_name = 'EVENTOS_APRENDIZAJE';
  IF v_cuenta = 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'Falta EVENTOS_APRENDIZAJE: ejecuta primero 002_autenticacion_contenido_eventos.sql.');
  END IF;
END;
/

-- 1. Procedimiento de purga. Las fechas de la app se guardan en UTC sin zona,
--    por eso el corte se calcula con SYS_EXTRACT_UTC.
CREATE OR REPLACE PROCEDURE amatista_purgar (
  p_dias_sesiones IN NUMBER DEFAULT 90,
  p_dias_eventos  IN NUMBER DEFAULT 400
) AS
  TYPE t_nombres IS TABLE OF VARCHAR2(128);
  -- Lotes chicos: cada COMMIT libera undo (el Free Tier tiene poco espacio).
  c_lote         CONSTANT PLS_INTEGER := 10000;
  v_ahora        TIMESTAMP := SYS_EXTRACT_UTC(SYSTIMESTAMP);
  v_corte_ses    TIMESTAMP;
  v_corte_eve    TIMESTAMP;
  v_alto         VARCHAR2(4000);
  v_hasta        TIMESTAMP;
  v_viejas       t_nombres := t_nombres();
  v_n            PLS_INTEGER;
  v_sesiones     PLS_INTEGER := 0;
  v_eventos      PLS_INTEGER := 0;
  v_meses        PLS_INTEGER := 0;
BEGIN
  IF p_dias_sesiones IS NULL OR p_dias_sesiones < 1 OR p_dias_eventos IS NULL OR p_dias_eventos < 1 THEN
    RAISE_APPLICATION_ERROR(-20010, 'amatista_purgar: los días deben ser 1 o más.');
  END IF;
  v_corte_ses := v_ahora - NUMTODSINTERVAL(p_dias_sesiones, 'DAY');
  v_corte_eve := v_ahora - NUMTODSINTERVAL(p_dias_eventos, 'DAY');

  -- Sesiones cerradas, vencidas o sin acceso en p_dias_sesiones días.
  LOOP
    DELETE FROM sesiones
     WHERE (activa = 0 OR expira_en < v_ahora OR ultimo_acceso < v_corte_ses)
       AND ROWNUM <= c_lote;
    v_n := SQL%ROWCOUNT;
    v_sesiones := v_sesiones + v_n;
    COMMIT;
    EXIT WHEN v_n < c_lote;
  END LOOP;

  -- Eventos, paso 1: meses completos. Primero se juntan los nombres (el
  -- límite superior HIGH_VALUE es LONG y se evalúa con SQL dinámico) y luego
  -- se quitan, para no cambiar el diccionario mientras se recorre. La
  -- posición 1 es la partición inicial de rango: Oracle no deja quitarla.
  FOR p IN (SELECT partition_name, high_value
              FROM user_tab_partitions
             WHERE table_name = 'EVENTOS_APRENDIZAJE'
               AND partition_position > 1
             ORDER BY partition_position) LOOP
    v_alto := p.high_value;
    EXECUTE IMMEDIATE 'SELECT ' || v_alto || ' FROM dual' INTO v_hasta;
    -- Todas las filas de la partición son anteriores a v_hasta.
    EXIT WHEN v_hasta > v_corte_eve;
    v_viejas.EXTEND;
    v_viejas(v_viejas.COUNT) := p.partition_name;
  END LOOP;

  FOR i IN 1 .. v_viejas.COUNT LOOP
    BEGIN
      EXECUTE IMMEDIATE 'SELECT COUNT(*) FROM eventos_aprendizaje PARTITION (' || v_viejas(i) || ')'
        INTO v_n;
      -- UPDATE GLOBAL INDEXES mantiene usable la llave primaria (índice global).
      EXECUTE IMMEDIATE 'ALTER TABLE eventos_aprendizaje DROP PARTITION ' || v_viejas(i)
        || ' UPDATE GLOBAL INDEXES';
      v_eventos := v_eventos + v_n;
      v_meses := v_meses + 1;
    EXCEPTION
      WHEN OTHERS THEN
        DBMS_OUTPUT.PUT_LINE('No se pudo quitar la partición ' || v_viejas(i) || ': ' || SQLERRM
          || ' (sus filas se borran con DELETE).');
    END;
  END LOOP;

  -- Eventos, paso 2: lo que quede antes del corte (meses parciales, la
  -- partición inicial o una tabla sin particiones).
  LOOP
    DELETE FROM eventos_aprendizaje
     WHERE ocurrido_en < v_corte_eve
       AND ROWNUM <= c_lote;
    v_n := SQL%ROWCOUNT;
    v_eventos := v_eventos + v_n;
    COMMIT;
    EXIT WHEN v_n < c_lote;
  END LOOP;

  DBMS_OUTPUT.PUT_LINE('Purga Amatista: ' || v_sesiones || ' sesiones y ' || v_eventos
    || ' eventos borrados (' || v_meses || ' meses completos).');
END amatista_purgar;
/

-- 2. Job diario. Si ya existe se borra y se vuelve a crear con esta definición.
DECLARE
  v_cuenta PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_cuenta FROM user_scheduler_jobs WHERE job_name = 'AMATISTA_PURGA_DIARIA';
  IF v_cuenta > 0 THEN
    DBMS_SCHEDULER.DROP_JOB(job_name => 'AMATISTA_PURGA_DIARIA', force => TRUE);
  END IF;
  DBMS_SCHEDULER.CREATE_JOB(
    job_name        => 'AMATISTA_PURGA_DIARIA',
    job_type        => 'PLSQL_BLOCK',
    job_action      => 'BEGIN amatista_purgar; END;',
    start_date      => SYSTIMESTAMP AT TIME ZONE 'UTC',
    repeat_interval => 'FREQ=DAILY;BYHOUR=9;BYMINUTE=15;BYSECOND=0',
    enabled         => TRUE,
    comments        => 'Amatista: purga sesiones viejas y eventos fuera de la retención (003_mantenimiento.sql).');
  DBMS_OUTPUT.PUT_LINE('Job AMATISTA_PURGA_DIARIA listo.');
END;
/

-- 3. Espacio por segmento. Cada índice y cada LOB se atribuye a su tabla;
--    las particiones de un mismo segmento se suman.
CREATE OR REPLACE VIEW v_amatista_espacio AS
SELECT NVL(i.table_name, NVL(l.table_name, s.segment_name)) AS tabla,
       s.segment_name                                       AS segmento,
       s.segment_type                                       AS tipo,
       ROUND(SUM(s.bytes) / 1048576, 2)                     AS mb,
       ROUND(SUM(s.bytes) * 100 / 21474836480, 3)           AS porcentaje_20gb
  FROM user_segments s
  LEFT JOIN user_indexes i ON i.index_name = s.segment_name
  LEFT JOIN user_lobs l ON l.segment_name = s.segment_name
 GROUP BY NVL(i.table_name, NVL(l.table_name, s.segment_name)), s.segment_name, s.segment_type;

-- 4. Espacio por tabla (tabla + índices + LOB) y el total contra los 20 GB.
SELECT CASE WHEN GROUPING(tabla) = 1 THEN '(TOTAL)' ELSE tabla END AS tabla,
       SUM(mb)              AS mb,
       SUM(porcentaje_20gb) AS porcentaje_20gb
  FROM v_amatista_espacio
 GROUP BY ROLLUP (tabla)
 ORDER BY 2 DESC;

-- 5. Filas por tabla (conteo exacto).
SELECT 'USUARIOS' AS tabla, COUNT(*) AS filas FROM usuarios
UNION ALL SELECT 'SESIONES', COUNT(*) FROM sesiones
UNION ALL SELECT 'PROGRESO_LECCIONES', COUNT(*) FROM progreso_lecciones
UNION ALL SELECT 'LOGROS', COUNT(*) FROM logros
UNION ALL SELECT 'EVENTOS_APRENDIZAJE', COUNT(*) FROM eventos_aprendizaje
UNION ALL SELECT 'CURSOS', COUNT(*) FROM cursos
UNION ALL SELECT 'MODULOS', COUNT(*) FROM modulos
UNION ALL SELECT 'LECCIONES', COUNT(*) FROM lecciones;

-- 6. Estado del job y meses de eventos guardados.
SELECT job_name, enabled, state, next_run_date, last_start_date
  FROM user_scheduler_jobs
 WHERE job_name = 'AMATISTA_PURGA_DIARIA';

SELECT partition_position, partition_name, num_rows
  FROM user_tab_partitions
 WHERE table_name = 'EVENTOS_APRENDIZAJE'
 ORDER BY partition_position;
