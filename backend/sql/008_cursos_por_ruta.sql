-- =============================================================================
-- AMATISTA · 008 · CURSOS POR RUTA Y REQUISITO (motor v3, plan de estudios)
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- El plan de estudios v3 parte Blender en cuatro cursos seguidos
-- (Principiante, Principiante-Intermedio, Intermedio y Avanzado). Para que
-- la base sepa que son una misma ruta y en qué orden se toman:
--   - CURSOS.RUTA ........... familia del curso: 'blender', 'aframe'. Los
--                             cuatro cursos de Blender comparten 'blender'.
--   - CURSOS.REQUISITO_ID ... curso que conviene terminar antes (llave
--                             foránea a CURSOS). Principiante-Intermedio
--                             pide Principiante. NULL = sin requisito.
--   - Llena RUTA en los cursos que ya existen (solo donde está vacía).
--
-- Por qué columnas y no una tabla RUTAS: hoy hay dos rutas y nada más que
-- decir de ellas; una tabla más sería un JOIN más en el catálogo. Si algún
-- día una ruta necesita título o portada, RUTA ya es el id para crearla
-- (docs/base-de-datos/01_rediseno_v3.md, sección 4).
--
-- Script INCREMENTAL e IDEMPOTENTE, igual que 002, 005 y 007:
--   - NO borra tablas ni filas: ALTER TABLE ... ADD y UPDATE solo donde
--     RUTA es NULL. Se puede repetir.
--   - No toca usuarios, progreso, insignias ni prácticas.
--   - El backend anterior sigue funcionando con estas columnas (las ignora):
--     se puede ejecutar antes o después de actualizar el código.
--
-- Orden: ... -> 007 -> 008 -> 009.
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5). Guía completa:
-- docs/base-de-datos/02_manual_008_009.md.
--
-- ESPACIO: dos columnas cortas en una tabla de menos de 10 filas: nada.
-- ÍNDICES: ninguno. CURSOS cabe en un bloque; el catálogo la lee completa.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  -- Ejecuta un DDL o DML dinámico y lo deja anotado en la salida del script.
  PROCEDURE ejecutar(p_sql IN VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
    DBMS_OUTPUT.PUT_LINE('OK: ' || SUBSTR(REGEXP_REPLACE(p_sql, '\s+', ' '), 1, 200));
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

  PROCEDURE exigir_columna(p_tabla IN VARCHAR2, p_columna IN VARCHAR2) IS
  BEGIN
    IF NOT existe_columna(p_tabla, p_columna) THEN
      RAISE_APPLICATION_ERROR(-20001, 'Falta ' || p_tabla || '.' || p_columna
        || '. Ejecuta antes 007_motor_practicas.sql (y los anteriores que falten).');
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

BEGIN
  -- 0. Punto de partida: 007 (la última tabla que agrega es PROGRESO_PRACTICAS).
  exigir_columna('CURSOS', 'ESTADO');
  exigir_columna('PROGRESO_PRACTICAS', 'PRACTICA_ID');

  -- 1. Las dos columnas nuevas (anulables: las filas existentes siguen igual).
  agregar_columna('CURSOS', 'RUTA', 'VARCHAR2(30)');
  agregar_columna('CURSOS', 'REQUISITO_ID', 'VARCHAR2(50)');
  agregar_restriccion('CURSOS', 'FK_CURSOS_REQUISITO', 'FOREIGN KEY (requisito_id) REFERENCES cursos (id)');
  agregar_restriccion('CURSOS', 'CK_CURSOS_REQUISITO', 'CHECK (requisito_id IS NULL OR requisito_id <> id)');

  -- 2. RUTA de los cursos que ya existen (solo donde está vacía).
  ejecutar('UPDATE cursos SET ruta = ''blender'' WHERE ruta IS NULL AND (id = ''blender'' OR SUBSTR(id, 1, 8) = ''blender_'')');
  ejecutar('UPDATE cursos SET ruta = ''aframe'' WHERE ruta IS NULL AND id = ''aframe''');
  -- Requisito de Principiante-Intermedio, si los dos cursos ya existen
  -- (los crea «python herramientas/contenido.py importar»).
  ejecutar('UPDATE cursos SET requisito_id = ''blender_principiante''
             WHERE id = ''blender_principiante_intermedio'' AND requisito_id IS NULL
               AND EXISTS (SELECT 1 FROM cursos c WHERE c.id = ''blender_principiante'')');
  COMMIT;

  DBMS_OUTPUT.PUT_LINE('008 aplicado. No hace falta reiniciar el backend.');
END;
/

-- 3. Verificar: las 18 tablas deben decir OK (columnas reales = esperadas).
--    Esta consulta sustituye a la de 007 (CURSOS pasa de 12 a 14 columnas).
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
        UNION ALL SELECT 'PROGRESO_PRACTICAS', 16 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 4. Los cursos con su ruta y su requisito.
SELECT id, ruta, requisito_id, orden, estado FROM cursos ORDER BY ruta, orden;
