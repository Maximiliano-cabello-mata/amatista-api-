-- =============================================================================
-- AMATISTA · 009 · ARCHIVAR EL CURSO DE BLENDER DE LA V2
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- El plan de estudios v3 reemplaza al curso 'blender' de la v2 por los
-- cursos 'blender_principiante' y 'blender_principiante_intermedio'. Este
-- script ARCHIVA (no borra) lo de la v2:
--   - CURSOS 'blender' ................... estado 'archivado'
--   - sus NIVELES y MODULOS ............... estado 'archivado'
--   - PRACTICAS blender.n1.* (mesa, podio)  estado 'archivado'
--
-- Lo que NO toca: USUARIOS, PROGRESO_LECCIONES, LOGROS, PROGRESO_PRACTICAS,
-- HABILIDADES_ALUMNO ni EVENTOS_APRENDIZAJE. Quien avanzó en la v2 conserva
-- su progreso y sus insignias; el panel del admin lo sigue mostrando.
-- Las LECCIONES quedan como están: el catálogo ya oculta las lecciones de un
-- curso o módulo archivado (api/contenido.py filtra por los tres estados).
--
-- CUÁNDO: DESPUÉS del piloto del 08/10/2026 y DESPUÉS de importar los cursos
-- nuevos (python herramientas/contenido.py importar): si se archiva antes, el
-- catálogo se queda sin Blender.
--
-- Script IDEMPOTENTE: solo cambia filas que todavía no están archivadas.
-- Para deshacerlo, la sección 4 trae los UPDATE inversos (comentados).
--
-- Orden: ... -> 007 -> 008 -> 009.
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5). Guía completa:
-- docs/base-de-datos/02_manual_008_009.md.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
BEGIN
  -- 0. Seguro: sin los cursos nuevos publicados no se archiva el viejo.
  SELECT COUNT(*) INTO v_cuenta
    FROM cursos
   WHERE id = 'blender_principiante' AND estado = 'publicado';
  IF v_cuenta = 0 THEN
    RAISE_APPLICATION_ERROR(-20002, 'Falta el curso blender_principiante publicado. '
      || 'Primero: python herramientas/contenido.py importar (desde backend/ en el servidor).');
  END IF;

  -- 1. Curso, niveles y módulos de la v2.
  UPDATE cursos SET estado = 'archivado', actualizado_en = SYSTIMESTAMP
   WHERE id = 'blender' AND estado <> 'archivado';
  DBMS_OUTPUT.PUT_LINE('Cursos archivados: ' || SQL%ROWCOUNT);

  UPDATE niveles SET estado = 'archivado', actualizado_en = SYSTIMESTAMP
   WHERE curso_id = 'blender' AND estado <> 'archivado';
  DBMS_OUTPUT.PUT_LINE('Niveles archivados: ' || SQL%ROWCOUNT);

  UPDATE modulos SET estado = 'archivado', actualizado_en = SYSTIMESTAMP
   WHERE curso_id = 'blender' AND estado <> 'archivado';
  DBMS_OUTPUT.PUT_LINE('Módulos archivados: ' || SQL%ROWCOUNT);

  -- 2. Prácticas del motor v1/v2 (practices/archivo/v2/): el add-on 3.0 ya no
  --    las ofrece a los alumnos. Su progreso se conserva.
  UPDATE practicas SET estado = 'archivado', actualizado_en = SYSTIMESTAMP
   WHERE (curso_id = 'blender' OR SUBSTR(id, 1, 11) = 'blender.n1.') AND estado <> 'archivado';
  DBMS_OUTPUT.PUT_LINE('Prácticas archivadas: ' || SQL%ROWCOUNT);

  COMMIT;
  DBMS_OUTPUT.PUT_LINE('009 aplicado. El catálogo se actualiza solo (ETag); no hace falta reiniciar.');
END;
/

-- 3. Verificar: el curso viejo archivado y los nuevos publicados.
SELECT id, ruta, estado, orden FROM cursos ORDER BY orden;
SELECT curso_id, estado, COUNT(*) AS modulos FROM modulos GROUP BY curso_id, estado ORDER BY curso_id;
SELECT id, estado FROM practicas ORDER BY id;
-- El progreso de la v2 sigue ahí:
SELECT COUNT(*) AS progreso_v2 FROM progreso_lecciones WHERE curso_id = 'blender';

-- 4. DESHACER (solo si hace falta volver a la v2): quita los "--" y ejecuta.
-- UPDATE cursos    SET estado = 'publicado' WHERE id = 'blender';
-- UPDATE niveles   SET estado = 'publicado' WHERE curso_id = 'blender';
-- UPDATE modulos   SET estado = 'publicado' WHERE curso_id = 'blender';
-- UPDATE practicas SET estado = 'publicado' WHERE SUBSTR(id, 1, 11) = 'blender.n1.';
-- COMMIT;
