-- 011: detalle del instructor compartido entre procesos (Motor 3.5.1).
-- Ejecutar como ADMIN en Database Actions con F5 ANTES de actualizar el backend.
-- Requiere 010. Incremental e idempotente; no borra progreso ni usuarios.
-- Sigue habiendo 20 tablas; ADDON_ENLACES pasa de 11 a 12 columnas.
SET SERVEROUTPUT ON
SET DEFINE OFF
DECLARE
  v_n PLS_INTEGER;
  PROCEDURE agregar_columna(p_tabla VARCHAR2, p_columna VARCHAR2, p_tipo VARCHAR2) IS
  BEGIN
    SELECT COUNT(*) INTO v_n FROM user_tab_columns
      WHERE table_name = p_tabla AND column_name = p_columna;
    IF v_n = 0 THEN
      EXECUTE IMMEDIATE 'ALTER TABLE ' || p_tabla || ' ADD (' || p_columna || ' ' || p_tipo || ')';
      DBMS_OUTPUT.PUT_LINE('OK: columna ' || p_tabla || '.' || p_columna);
    END IF;
  END;
BEGIN
  SELECT COUNT(*) INTO v_n FROM user_tables WHERE table_name = 'ADDON_ENLACES';
  IF v_n = 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'Ejecuta primero 010_enlace_blender.sql.');
  END IF;
  agregar_columna('ADDON_ENLACES', 'DETALLE', 'CLOB');
  SELECT COUNT(*) INTO v_n FROM user_constraints WHERE constraint_name = 'CK_ENLACES_DETALLE';
  IF v_n = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE ADDON_ENLACES ADD CONSTRAINT CK_ENLACES_DETALLE CHECK (detalle IS JSON)';
  END IF;
  DBMS_OUTPUT.PUT_LINE('011 aplicado. Actualiza el backend y reinicia amatista-backend.');
END;
/
SELECT column_name, data_type FROM user_tab_columns
 WHERE table_name = 'ADDON_ENLACES' AND column_name = 'DETALLE';

-- Verificación vigente: 20 tablas; ADDON_ENLACES tiene 12 columnas.
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
        UNION ALL SELECT 'ADDON_ENLACES', 12 FROM dual
        UNION ALL SELECT 'ADDON_AJUSTES', 3 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

