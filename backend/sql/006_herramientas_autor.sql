-- =============================================================================
-- AMATISTA · 006 · HERRAMIENTAS DE AUTOR (vistas y paquete AMATISTA_AUTOR)
-- (Oracle Autonomous Database ATP 26ai)
--
-- Para crear y revisar lecciones desde Database Actions sin escribir JSON a
-- mano. No crea tablas ni columnas (eso es 005); solo vistas y un paquete.
-- IDEMPOTENTE: todo es CREATE OR REPLACE; se puede ejecutar las veces que haga
-- falta (por ejemplo, después de actualizar el repositorio).
--
-- Vistas (consultas de solo lectura):
--   V_AMATISTA_MAPA ................ curso > nivel > módulo > lección, con la
--                                    ficha de cada lección y lo que le falta.
--   V_AMATISTA_FICHAS_INCOMPLETAS .. solo las lecciones con ficha incompleta.
--   V_AMATISTA_COMPATIBILIDAD ...... matriz lección x versión de Blender con
--                                    la prueba más reciente de cada par.
--
-- Paquete AMATISTA_AUTOR (cada procedimiento valida y confirma su cambio):
--   guardar_nivel, publicar_nivel, asignar_nivel, guardar_habilidad,
--   nueva_leccion, duplicar_leccion, guardar_ficha, guardar_version_blender,
--   registrar_verificacion.
--
-- Lo que este paquete NO hace a propósito: publicar lecciones. Publicar pasa
-- por la validación de backend/contenido/validacion.py (panel de
-- administración o «python herramientas/contenido.py importar»), así nada
-- que la PWA no sepa mostrar llega a los alumnos.
--
-- Requisito: 005 aplicado. Ejecútalo con el dueño de las tablas (ADMIN) en
-- Database Actions > SQL con «Ejecutar script» (F5).
-- Ejemplos de uso: docs/reestructuracion/02_manual_oracle.md, sección 5.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

-- 0. Requisito: las tablas de 005.
DECLARE
  v_cuenta PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_cuenta
    FROM user_tab_columns
   WHERE table_name = 'MODULOS' AND column_name = 'NIVEL_ID';
  IF v_cuenta = 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'Falta MODULOS.NIVEL_ID: ejecuta antes 005_niveles_habilidades_versiones.sql.');
  END IF;
END;
/

-- 1. Mapa del curso: una fila por lección (o por módulo sin lecciones).
--    «pendientes» queda vacío cuando la ficha cumple la estructura mínima.
CREATE OR REPLACE VIEW v_amatista_mapa AS
SELECT m.curso_id,
       n.numero                                                        AS nivel_numero,
       n.rama                                                          AS nivel_rama,
       m.nivel_id,
       n.titulo                                                        AS nivel_titulo,
       m.numero                                                        AS modulo_numero,
       m.id                                                            AS modulo_id,
       m.titulo                                                        AS modulo_titulo,
       m.estado                                                        AS modulo_estado,
       l.orden                                                         AS leccion_orden,
       l.id                                                            AS leccion_id,
       l.titulo                                                        AS leccion_titulo,
       l.tipo                                                          AS leccion_tipo,
       l.estado                                                        AS leccion_estado,
       l.version                                                       AS leccion_version,
       JSON_VALUE(l.contenido, '$.ficha.objetivo' RETURNING VARCHAR2(300))            AS objetivo,
       JSON_QUERY(l.contenido, '$.ficha.habilidades' RETURNING VARCHAR2(400))         AS habilidades,
       JSON_VALUE(l.contenido, '$.ficha.blender.verificadaEn' RETURNING VARCHAR2(20)) AS blender_verificada,
       JSON_VALUE(l.contenido, '$.ficha.offline' RETURNING VARCHAR2(5))               AS es_offline,
       CASE
         WHEN l.id IS NULL THEN 'el módulo no tiene lecciones'
         WHEN NOT JSON_EXISTS(l.contenido, '$.ficha') THEN 'ficha'
         ELSE RTRIM(
                CASE WHEN JSON_VALUE(l.contenido, '$.ficha.objetivo' RETURNING VARCHAR2(300)) IS NULL
                     THEN 'objetivo, ' END
             || CASE WHEN NOT JSON_EXISTS(l.contenido, '$.ficha.habilidades[0]')
                     THEN 'habilidades, ' END
             || CASE WHEN m.curso_id = 'blender'
                      AND JSON_VALUE(l.contenido, '$.ficha.blender.verificadaEn' RETURNING VARCHAR2(20)) IS NULL
                     THEN 'versión de Blender verificada, ' END
             || CASE WHEN NOT JSON_EXISTS(l.contenido, '$.ficha.comprobacion[0]')
                     THEN 'criterios de comprobación, ' END,
              ', ')
       END                                                             AS pendientes
  FROM modulos m
  LEFT JOIN niveles n   ON n.id = m.nivel_id
  LEFT JOIN lecciones l ON l.modulo_id = m.id AND l.curso_id = m.curso_id;

-- 2. Lo que falta por completar (sin lo archivado).
CREATE OR REPLACE VIEW v_amatista_fichas_incompletas AS
SELECT curso_id, nivel_numero, nivel_rama, modulo_numero, modulo_id, leccion_orden,
       leccion_id, leccion_titulo, leccion_estado, pendientes
  FROM v_amatista_mapa
 WHERE pendientes IS NOT NULL
   AND modulo_estado <> 'archivado'
   AND NVL(leccion_estado, 'borrador') <> 'archivado';

-- 3. Matriz de compatibilidad: cada lección contra cada versión registrada.
--    estado_prueba: «sin verificar», «repetir: la lección cambió» (la prueba
--    se hizo con una versión anterior de la lección) o «vigente».
CREATE OR REPLACE VIEW v_amatista_compatibilidad AS
SELECT l.curso_id,
       l.id              AS leccion_id,
       l.titulo          AS leccion_titulo,
       l.version         AS leccion_version,
       v.version         AS version_blender,
       v.categoria,
       u.resultado,
       u.sistema,
       u.version_leccion,
       u.version_addon,
       u.diferencias,
       u.responsable,
       u.verificado_en,
       CASE
         WHEN u.id IS NULL THEN 'sin verificar'
         WHEN u.version_leccion < l.version THEN 'repetir: la lección cambió'
         ELSE 'vigente'
       END               AS estado_prueba
  FROM lecciones l
 CROSS JOIN versiones_blender v
  LEFT JOIN (SELECT vb.*,
                    ROW_NUMBER() OVER (PARTITION BY vb.curso_id, vb.leccion_id, vb.version_blender
                                       ORDER BY vb.verificado_en DESC, vb.id) AS rn
               FROM verificaciones_blender vb) u
    ON u.curso_id = l.curso_id AND u.leccion_id = l.id AND u.version_blender = v.version AND u.rn = 1
 WHERE l.estado <> 'archivado'
   AND v.categoria <> 'retirada';

-- 4. Paquete de autor.
CREATE OR REPLACE PACKAGE amatista_autor AS
  -- Hora UTC sin zona, como la guarda el backend (database/modelos.py: ahora()).
  -- Es pública porque las sentencias SQL del paquete la usan.
  FUNCTION ahora RETURN TIMESTAMP;
  -- Crea o actualiza un nivel. id = <curso>-n<numero>[-<rama>]; nace en borrador.
  PROCEDURE guardar_nivel(p_curso_id        IN VARCHAR2,
                          p_numero          IN PLS_INTEGER,
                          p_titulo          IN VARCHAR2,
                          p_rama            IN VARCHAR2 DEFAULT NULL,
                          p_perfil          IN VARCHAR2 DEFAULT NULL,
                          p_proyecto        IN VARCHAR2 DEFAULT NULL,
                          p_criterio_salida IN VARCHAR2 DEFAULT NULL);
  -- Hace visible el nivel en el catálogo (sus módulos se publican aparte).
  PROCEDURE publicar_nivel(p_nivel_id IN VARCHAR2);
  -- Coloca un módulo en un nivel del mismo curso (NULL lo saca del nivel).
  PROCEDURE asignar_nivel(p_modulo_id IN VARCHAR2, p_nivel_id IN VARCHAR2);
  -- Crea o actualiza una habilidad observable.
  PROCEDURE guardar_habilidad(p_id          IN VARCHAR2,
                              p_curso_id    IN VARCHAR2,
                              p_nombre      IN VARCHAR2,
                              p_nivel_id    IN VARCHAR2 DEFAULT NULL,
                              p_descripcion IN VARCHAR2 DEFAULT NULL,
                              p_orden       IN PLS_INTEGER DEFAULT NULL);
  -- Lección borrador con los 10 pasos de la estructura mínima y su ficha,
  -- al final del módulo. p_habilidades: ids separados por comas.
  PROCEDURE nueva_leccion(p_modulo_id   IN VARCHAR2,
                          p_leccion_id  IN VARCHAR2,
                          p_titulo      IN VARCHAR2,
                          p_objetivo    IN VARCHAR2 DEFAULT NULL,
                          p_habilidades IN VARCHAR2 DEFAULT NULL);
  -- Copia una lección como borrador nuevo (variante, transferencia, otra versión).
  PROCEDURE duplicar_leccion(p_curso_id  IN VARCHAR2,
                             p_origen_id IN VARCHAR2,
                             p_nuevo_id  IN VARCHAR2,
                             p_modulo_id IN VARCHAR2 DEFAULT NULL,
                             p_titulo    IN VARCHAR2 DEFAULT NULL);
  -- Llena la ficha. Solo cambia lo que no es NULL.
  -- p_habilidades: ids separados por comas; p_comprobacion: criterios separados por «|»;
  -- p_offline: 'si' o 'no'.
  PROCEDURE guardar_ficha(p_curso_id      IN VARCHAR2,
                          p_leccion_id    IN VARCHAR2,
                          p_objetivo      IN VARCHAR2 DEFAULT NULL,
                          p_habilidades   IN VARCHAR2 DEFAULT NULL,
                          p_blender       IN VARCHAR2 DEFAULT NULL,
                          p_notas_blender IN VARCHAR2 DEFAULT NULL,
                          p_comprobacion  IN VARCHAR2 DEFAULT NULL,
                          p_offline       IN VARCHAR2 DEFAULT NULL);
  -- Registra una versión de Blender. Solo puede haber una «principal»: al
  -- marcar otra, la anterior pasa a «compatible».
  PROCEDURE guardar_version_blender(p_version       IN VARCHAR2,
                                    p_categoria     IN VARCHAR2 DEFAULT 'sin_verificar',
                                    p_es_lts        IN PLS_INTEGER DEFAULT 0,
                                    p_soporte_hasta IN DATE DEFAULT NULL,
                                    p_notas         IN VARCHAR2 DEFAULT NULL);
  -- Anota una prueba de la matriz de compatibilidad (con la versión actual de la lección).
  PROCEDURE registrar_verificacion(p_curso_id        IN VARCHAR2,
                                   p_leccion_id      IN VARCHAR2,
                                   p_version_blender IN VARCHAR2,
                                   p_sistema         IN VARCHAR2,
                                   p_resultado       IN VARCHAR2,
                                   p_diferencias     IN VARCHAR2 DEFAULT NULL,
                                   p_evidencia       IN VARCHAR2 DEFAULT NULL,
                                   p_responsable     IN VARCHAR2 DEFAULT NULL,
                                   p_version_addon   IN VARCHAR2 DEFAULT NULL);
END amatista_autor;
/

CREATE OR REPLACE PACKAGE BODY amatista_autor AS
  c_reemplaza CONSTANT VARCHAR2(30) := 'Reemplaza este contenido';
  c_patron_id CONSTANT VARCHAR2(40) := '^[A-Za-z0-9][A-Za-z0-9_-]*$';

  FUNCTION ahora RETURN TIMESTAMP IS
  BEGIN
    RETURN SYS_EXTRACT_UTC(SYSTIMESTAMP);
  END ahora;

  PROCEDURE exigir_id(p_id IN VARCHAR2, p_que IN VARCHAR2) IS
  BEGIN
    IF p_id IS NULL OR LENGTH(p_id) > 50 OR NOT REGEXP_LIKE(p_id, c_patron_id) THEN
      RAISE_APPLICATION_ERROR(-20010, p_que || ' «' || p_id
        || '»: usa solo letras, números, guion y guion bajo (máximo 50).');
    END IF;
  END exigir_id;

  -- 'a, b,c' -> ["a","b","c"] (sin vacíos).
  FUNCTION lista(p_texto IN VARCHAR2, p_separador IN VARCHAR2) RETURN JSON_ARRAY_T IS
    v_lista JSON_ARRAY_T := JSON_ARRAY_T();
    v_parte VARCHAR2(4000);
    v_i     PLS_INTEGER := 1;
  BEGIN
    LOOP
      v_parte := REGEXP_SUBSTR(p_texto, '[^' || p_separador || ']+', 1, v_i);
      EXIT WHEN v_parte IS NULL;
      v_parte := TRIM(v_parte);
      IF v_parte IS NOT NULL THEN
        v_lista.append(v_parte);
      END IF;
      v_i := v_i + 1;
    END LOOP;
    RETURN v_lista;
  END lista;

  -- Avisa (sin detenerse) de habilidades que todavía no existen.
  PROCEDURE revisar_habilidades(p_habilidades IN VARCHAR2) IS
    v_lista  JSON_ARRAY_T := lista(p_habilidades, ',');
    v_id     VARCHAR2(4000);
    v_cuenta PLS_INTEGER;
  BEGIN
    FOR i IN 0 .. v_lista.get_size - 1 LOOP
      -- Los métodos de JSON_ARRAY_T no se pueden usar dentro de SQL (ORA-40573).
      v_id := v_lista.get_string(i);
      exigir_id(v_id, 'Habilidad');
      SELECT COUNT(*) INTO v_cuenta FROM habilidades WHERE id = v_id;
      IF v_cuenta = 0 THEN
        DBMS_OUTPUT.PUT_LINE('Aviso: la habilidad «' || v_id
          || '» no existe todavía; créala con amatista_autor.guardar_habilidad.');
      END IF;
    END LOOP;
  END revisar_habilidades;

  -- Agrega un paso de la estructura mínima (backend/contenido/plantillas.py:
  -- ESTRUCTURA_LECCION tiene los mismos títulos, en el mismo orden).
  PROCEDURE paso(p_bloques IN OUT NOCOPY JSON_ARRAY_T, p_tipo IN VARCHAR2, p_titulo IN VARCHAR2,
                 p_guia IN VARCHAR2, p_texto IN VARCHAR2 DEFAULT NULL) IS
    v_bloque JSON_OBJECT_T := JSON_OBJECT_T();
  BEGIN
    v_bloque.put('type', p_tipo);
    IF p_tipo = 'callout' THEN
      v_bloque.put('variant', CASE WHEN p_titulo = 'Tu variante' THEN 'reto' ELSE 'dato' END);
      v_bloque.put('title', p_titulo);
      v_bloque.put('body', NVL(p_texto, c_reemplaza || ': ' || p_guia));
    ELSE
      v_bloque.put('body', '### ' || p_titulo || CHR(10) || c_reemplaza || ': ' || p_guia);
    END IF;
    p_bloques.append(v_bloque);
  END paso;

  FUNCTION siguiente_orden(p_modulo_id IN VARCHAR2) RETURN PLS_INTEGER IS
    v_orden PLS_INTEGER;
  BEGIN
    SELECT NVL(MAX(orden), 0) + 1 INTO v_orden FROM lecciones WHERE modulo_id = p_modulo_id;
    RETURN v_orden;
  END siguiente_orden;

  PROCEDURE guardar_nivel(p_curso_id        IN VARCHAR2,
                          p_numero          IN PLS_INTEGER,
                          p_titulo          IN VARCHAR2,
                          p_rama            IN VARCHAR2 DEFAULT NULL,
                          p_perfil          IN VARCHAR2 DEFAULT NULL,
                          p_proyecto        IN VARCHAR2 DEFAULT NULL,
                          p_criterio_salida IN VARCHAR2 DEFAULT NULL) IS
    v_id     VARCHAR2(50);
    v_cuenta PLS_INTEGER;
  BEGIN
    IF p_numero NOT BETWEEN 1 AND 5 THEN
      RAISE_APPLICATION_ERROR(-20011, 'El nivel debe ser un número del 1 al 5.');
    END IF;
    IF p_rama IS NOT NULL AND p_numero <> 5 THEN
      RAISE_APPLICATION_ERROR(-20012, 'Solo el nivel 5 tiene ramas (web, animacion, producto...).');
    END IF;
    SELECT COUNT(*) INTO v_cuenta FROM cursos WHERE id = p_curso_id;
    IF v_cuenta = 0 THEN
      RAISE_APPLICATION_ERROR(-20013, 'No existe el curso «' || p_curso_id || '».');
    END IF;
    v_id := p_curso_id || '-n' || p_numero || CASE WHEN p_rama IS NOT NULL THEN '-' || p_rama END;
    exigir_id(v_id, 'Nivel');
    MERGE INTO niveles n
    USING (SELECT v_id AS id FROM dual) x
       ON (n.id = x.id)
     WHEN MATCHED THEN UPDATE SET
          n.titulo          = NVL(p_titulo, n.titulo),
          n.perfil          = NVL(p_perfil, n.perfil),
          n.proyecto        = NVL(p_proyecto, n.proyecto),
          n.criterio_salida = NVL(p_criterio_salida, n.criterio_salida),
          n.actualizado_en  = ahora()
     WHEN NOT MATCHED THEN INSERT
          (id, curso_id, numero, rama, titulo, perfil, proyecto, criterio_salida, estado, actualizado_en)
          VALUES (v_id, p_curso_id, p_numero, p_rama, p_titulo, p_perfil, p_proyecto, p_criterio_salida,
                  'borrador', ahora());
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Nivel guardado: ' || v_id);
  END guardar_nivel;

  PROCEDURE publicar_nivel(p_nivel_id IN VARCHAR2) IS
  BEGIN
    UPDATE niveles SET estado = 'publicado', actualizado_en = ahora()
     WHERE id = p_nivel_id AND estado <> 'publicado';
    IF SQL%ROWCOUNT = 0 THEN
      DBMS_OUTPUT.PUT_LINE('Sin cambios: el nivel «' || p_nivel_id || '» no existe o ya estaba publicado.');
    END IF;
    COMMIT;
  END publicar_nivel;

  PROCEDURE asignar_nivel(p_modulo_id IN VARCHAR2, p_nivel_id IN VARCHAR2) IS
    v_curso_modulo VARCHAR2(50);
    v_curso_nivel  VARCHAR2(50);
  BEGIN
    BEGIN
      SELECT curso_id INTO v_curso_modulo FROM modulos WHERE id = p_modulo_id;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20014, 'No existe el módulo «' || p_modulo_id || '».');
    END;
    IF p_nivel_id IS NOT NULL THEN
      BEGIN
        SELECT curso_id INTO v_curso_nivel FROM niveles WHERE id = p_nivel_id;
      EXCEPTION
        WHEN NO_DATA_FOUND THEN
          RAISE_APPLICATION_ERROR(-20015, 'No existe el nivel «' || p_nivel_id || '».');
      END;
      IF v_curso_nivel <> v_curso_modulo THEN
        RAISE_APPLICATION_ERROR(-20016, 'El nivel «' || p_nivel_id || '» es del curso «' || v_curso_nivel
          || '» y el módulo del curso «' || v_curso_modulo || '».');
      END IF;
    END IF;
    -- version + 1 y la fecha cambian la huella del catálogo (la PWA se entera).
    UPDATE modulos SET nivel_id = p_nivel_id, version = version + 1, actualizado_en = ahora()
     WHERE id = p_modulo_id AND DECODE(nivel_id, p_nivel_id, 1, 0) = 0;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Módulo ' || p_modulo_id || ' -> nivel ' || NVL(p_nivel_id, '(ninguno)'));
  END asignar_nivel;

  PROCEDURE guardar_habilidad(p_id          IN VARCHAR2,
                              p_curso_id    IN VARCHAR2,
                              p_nombre      IN VARCHAR2,
                              p_nivel_id    IN VARCHAR2 DEFAULT NULL,
                              p_descripcion IN VARCHAR2 DEFAULT NULL,
                              p_orden       IN PLS_INTEGER DEFAULT NULL) IS
    v_orden PLS_INTEGER := p_orden;
  BEGIN
    exigir_id(p_id, 'Habilidad');
    IF v_orden IS NULL THEN
      SELECT NVL(MAX(orden), 0) + 1 INTO v_orden FROM habilidades WHERE curso_id = p_curso_id;
    END IF;
    MERGE INTO habilidades h
    USING (SELECT p_id AS id FROM dual) x
       ON (h.id = x.id)
     WHEN MATCHED THEN UPDATE SET
          h.nombre         = NVL(p_nombre, h.nombre),
          h.nivel_id       = NVL(p_nivel_id, h.nivel_id),
          h.descripcion    = NVL(p_descripcion, h.descripcion),
          h.orden          = NVL(p_orden, h.orden),
          h.actualizado_en = ahora()
     WHEN NOT MATCHED THEN INSERT (id, curso_id, nivel_id, nombre, descripcion, orden, actualizado_en)
          VALUES (p_id, p_curso_id, p_nivel_id, p_nombre, p_descripcion, v_orden, ahora());
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Habilidad guardada: ' || p_id);
  END guardar_habilidad;

  PROCEDURE nueva_leccion(p_modulo_id   IN VARCHAR2,
                          p_leccion_id  IN VARCHAR2,
                          p_titulo      IN VARCHAR2,
                          p_objetivo    IN VARCHAR2 DEFAULT NULL,
                          p_habilidades IN VARCHAR2 DEFAULT NULL) IS
    v_curso_id VARCHAR2(50);
    v_estado   VARCHAR2(12);
    v_orden    PLS_INTEGER;
    v_cuenta   PLS_INTEGER;
    v_leccion  JSON_OBJECT_T := JSON_OBJECT_T();
    v_ficha    JSON_OBJECT_T := JSON_OBJECT_T();
    v_blender  JSON_OBJECT_T := JSON_OBJECT_T();
    v_practica JSON_OBJECT_T := JSON_OBJECT_T();
    v_bloques  JSON_ARRAY_T := JSON_ARRAY_T();
    v_clob     CLOB;
  BEGIN
    exigir_id(p_leccion_id, 'Lección');
    IF p_titulo IS NULL OR LENGTH(p_titulo) > 200 THEN
      RAISE_APPLICATION_ERROR(-20017, 'Escribe un título de hasta 200 caracteres.');
    END IF;
    BEGIN
      SELECT curso_id, estado INTO v_curso_id, v_estado FROM modulos WHERE id = p_modulo_id;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20014, 'No existe el módulo «' || p_modulo_id || '».');
    END;
    IF v_estado = 'archivado' THEN
      RAISE_APPLICATION_ERROR(-20018, 'El módulo «' || p_modulo_id || '» está archivado.');
    END IF;
    SELECT COUNT(*) INTO v_cuenta FROM lecciones WHERE curso_id = v_curso_id AND id = p_leccion_id;
    IF v_cuenta > 0 THEN
      RAISE_APPLICATION_ERROR(-20019, 'Ya existe la lección «' || p_leccion_id
        || '» en el curso «' || v_curso_id || '». Los ids no se reutilizan.');
    END IF;
    revisar_habilidades(p_habilidades);
    v_orden := siguiente_orden(p_modulo_id);

    paso(v_bloques, 'callout', 'Objetivo', 'Qué podrá hacer el alumno al terminar, en una frase observable.', p_objetivo);
    paso(v_bloques, 'markdown_text', 'Antes de empezar', 'Prerrequisitos y versión de Blender en la que se verificó la lección.');
    paso(v_bloques, 'markdown_text', 'Resultado esperado', 'Describe la imagen de referencia del resultado (agrega un bloque image).');
    paso(v_bloques, 'markdown_text', 'Conceptos clave', 'Explicación breve de lo necesario, nada más.');
    paso(v_bloques, 'markdown_text', 'Práctica guiada', 'Pasos numerados, con menús además de atajos.');
    paso(v_bloques, 'callout', 'Tu variante', 'Reto de transferencia: cambia algo manteniendo el objetivo.');
    paso(v_bloques, 'markdown_text', 'Errores frecuentes y pistas', 'Problema → pista opcional → cómo recuperarse.');
    paso(v_bloques, 'markdown_text', 'Comprueba tu trabajo', 'Lista de criterios de comprobación (los mismos de la ficha).');
    paso(v_bloques, 'markdown_text', 'Guarda tu evidencia', 'Qué guardar: archivo editable, captura y variación.');
    paso(v_bloques, 'markdown_text', 'Repaso', 'Dónde se vuelve a usar esta habilidad más adelante.');

    v_ficha.put('objetivo', NVL(p_objetivo, ''));
    v_ficha.put('habilidades', lista(p_habilidades, ','));
    v_ficha.put('prerrequisitos', JSON_ARRAY_T());
    v_blender.put_null('verificadaEn');
    v_blender.put('notas', '');
    v_ficha.put('blender', v_blender);
    v_ficha.put('edicion', '1.0');
    v_practica.put('archivo', '');
    v_practica.put('evidencia', 'Archivo editable y captura del resultado');
    v_ficha.put('practica', v_practica);
    v_ficha.put('comprobacion', JSON_ARRAY_T());
    v_ficha.put('offline', FALSE);

    v_leccion.put('id', p_leccion_id);
    v_leccion.put('title', p_titulo);
    v_leccion.put('type', 'theory_reading');
    v_leccion.put('durationSeconds', 900);
    v_leccion.put('isLocked', v_orden > 1);
    v_leccion.put('ficha', v_ficha);
    v_leccion.put('contentBlocks', v_bloques);
    v_clob := v_leccion.to_clob;

    INSERT INTO lecciones (curso_id, id, modulo_id, orden, titulo, tipo, duracion_segundos, bloqueada,
                           contenido, reemplaza, estado, version, actualizado_en, publicado_en)
    VALUES (v_curso_id, p_leccion_id, p_modulo_id, v_orden, p_titulo, 'theory_reading', 900,
            CASE WHEN v_orden > 1 THEN 1 ELSE 0 END, v_clob, NULL, 'borrador', 1, ahora(), NULL);
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Lección ' || p_leccion_id || ' creada en borrador (posición ' || v_orden
      || '). Complétala en el panel (#/admin) y publícala desde ahí.');
  END nueva_leccion;

  PROCEDURE duplicar_leccion(p_curso_id  IN VARCHAR2,
                             p_origen_id IN VARCHAR2,
                             p_nuevo_id  IN VARCHAR2,
                             p_modulo_id IN VARCHAR2 DEFAULT NULL,
                             p_titulo    IN VARCHAR2 DEFAULT NULL) IS
    v_origen  lecciones%ROWTYPE;
    v_modulo  VARCHAR2(50);
    v_curso   VARCHAR2(50);
    v_cuenta  PLS_INTEGER;
    v_orden   PLS_INTEGER;
    v_leccion JSON_OBJECT_T;
    v_titulo  VARCHAR2(200);
    v_clob    CLOB;
  BEGIN
    exigir_id(p_nuevo_id, 'Lección');
    BEGIN
      SELECT * INTO v_origen FROM lecciones WHERE curso_id = p_curso_id AND id = p_origen_id;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20020, 'No existe la lección «' || p_origen_id || '» en «' || p_curso_id || '».');
    END;
    v_modulo := NVL(p_modulo_id, v_origen.modulo_id);
    BEGIN
      SELECT curso_id INTO v_curso FROM modulos WHERE id = v_modulo;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20014, 'No existe el módulo «' || v_modulo || '».');
    END;
    IF v_curso <> p_curso_id THEN
      RAISE_APPLICATION_ERROR(-20016, 'El módulo «' || v_modulo || '» es de otro curso.');
    END IF;
    SELECT COUNT(*) INTO v_cuenta FROM lecciones WHERE curso_id = p_curso_id AND id = p_nuevo_id;
    IF v_cuenta > 0 THEN
      RAISE_APPLICATION_ERROR(-20019, 'Ya existe la lección «' || p_nuevo_id || '». Los ids no se reutilizan.');
    END IF;
    v_titulo := SUBSTR(NVL(p_titulo, v_origen.titulo || ' (copia)'), 1, 200);
    v_orden := siguiente_orden(v_modulo);
    v_leccion := JSON_OBJECT_T.parse(v_origen.contenido);
    v_leccion.put('id', p_nuevo_id);
    v_leccion.put('title', v_titulo);
    -- Una copia no sustituye a nadie: «replaces» lo decide el autor.
    v_leccion.remove('replaces');
    v_clob := v_leccion.to_clob;
    INSERT INTO lecciones (curso_id, id, modulo_id, orden, titulo, tipo, duracion_segundos, bloqueada,
                           contenido, reemplaza, estado, version, actualizado_en, publicado_en)
    VALUES (p_curso_id, p_nuevo_id, v_modulo, v_orden, v_titulo, v_origen.tipo, v_origen.duracion_segundos,
            v_origen.bloqueada, v_clob, NULL, 'borrador', 1, ahora(), NULL);
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Lección ' || p_nuevo_id || ' copiada de ' || p_origen_id || ' en borrador.');
  END duplicar_leccion;

  PROCEDURE guardar_ficha(p_curso_id      IN VARCHAR2,
                          p_leccion_id    IN VARCHAR2,
                          p_objetivo      IN VARCHAR2 DEFAULT NULL,
                          p_habilidades   IN VARCHAR2 DEFAULT NULL,
                          p_blender       IN VARCHAR2 DEFAULT NULL,
                          p_notas_blender IN VARCHAR2 DEFAULT NULL,
                          p_comprobacion  IN VARCHAR2 DEFAULT NULL,
                          p_offline       IN VARCHAR2 DEFAULT NULL) IS
    v_contenido CLOB;
    v_leccion   JSON_OBJECT_T;
    v_ficha     JSON_OBJECT_T;
    v_blender   JSON_OBJECT_T;
  BEGIN
    BEGIN
      SELECT contenido INTO v_contenido FROM lecciones
       WHERE curso_id = p_curso_id AND id = p_leccion_id
         FOR UPDATE;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20020, 'No existe la lección «' || p_leccion_id || '» en «' || p_curso_id || '».');
    END;
    IF p_objetivo IS NOT NULL AND LENGTH(p_objetivo) > 300 THEN
      RAISE_APPLICATION_ERROR(-20021, 'El objetivo admite hasta 300 caracteres.');
    END IF;
    IF p_blender IS NOT NULL AND NOT REGEXP_LIKE(p_blender, '^[0-9]+\.[0-9]+(\.[0-9]+)?$') THEN
      RAISE_APPLICATION_ERROR(-20022, 'La versión de Blender debe ser como 4.2 o 4.2.3.');
    END IF;
    IF p_offline IS NOT NULL AND LOWER(p_offline) NOT IN ('si', 'no') THEN
      RAISE_APPLICATION_ERROR(-20023, 'p_offline acepta si o no.');
    END IF;
    revisar_habilidades(p_habilidades);

    v_leccion := JSON_OBJECT_T.parse(v_contenido);
    v_ficha := v_leccion.get_object('ficha');
    IF v_ficha IS NULL THEN
      v_ficha := JSON_OBJECT_T();
    END IF;
    IF p_objetivo IS NOT NULL THEN
      v_ficha.put('objetivo', p_objetivo);
    END IF;
    IF p_habilidades IS NOT NULL THEN
      v_ficha.put('habilidades', lista(p_habilidades, ','));
    END IF;
    IF p_blender IS NOT NULL OR p_notas_blender IS NOT NULL THEN
      v_blender := v_ficha.get_object('blender');
      IF v_blender IS NULL THEN
        v_blender := JSON_OBJECT_T();
      END IF;
      IF p_blender IS NOT NULL THEN
        v_blender.put('verificadaEn', p_blender);
      END IF;
      IF p_notas_blender IS NOT NULL THEN
        v_blender.put('notas', SUBSTR(p_notas_blender, 1, 500));
      END IF;
      v_ficha.put('blender', v_blender);
    END IF;
    IF p_comprobacion IS NOT NULL THEN
      v_ficha.put('comprobacion', lista(p_comprobacion, '|'));
    END IF;
    IF p_offline IS NOT NULL THEN
      v_ficha.put('offline', LOWER(p_offline) = 'si');
    END IF;
    v_leccion.put('ficha', v_ficha);
    v_contenido := v_leccion.to_clob;

    UPDATE lecciones
       SET contenido = v_contenido, version = version + 1, actualizado_en = ahora()
     WHERE curso_id = p_curso_id AND id = p_leccion_id;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Ficha de ' || p_leccion_id || ' actualizada.');
  END guardar_ficha;

  PROCEDURE guardar_version_blender(p_version       IN VARCHAR2,
                                    p_categoria     IN VARCHAR2 DEFAULT 'sin_verificar',
                                    p_es_lts        IN PLS_INTEGER DEFAULT 0,
                                    p_soporte_hasta IN DATE DEFAULT NULL,
                                    p_notas         IN VARCHAR2 DEFAULT NULL) IS
  BEGIN
    IF p_version IS NULL OR NOT REGEXP_LIKE(p_version, '^[0-9]+\.[0-9]+(\.[0-9]+)?$') THEN
      RAISE_APPLICATION_ERROR(-20022, 'La versión de Blender debe ser como 4.2 o 4.2.3.');
    END IF;
    IF p_categoria NOT IN ('principal', 'compatible', 'sin_verificar', 'retirada') THEN
      RAISE_APPLICATION_ERROR(-20024, 'Categoría: principal, compatible, sin_verificar o retirada.');
    END IF;
    IF p_categoria = 'principal' THEN
      UPDATE versiones_blender SET categoria = 'compatible', actualizado_en = ahora()
       WHERE categoria = 'principal' AND version <> p_version;
      IF SQL%ROWCOUNT > 0 THEN
        DBMS_OUTPUT.PUT_LINE('La versión principal anterior pasó a «compatible».');
      END IF;
    END IF;
    MERGE INTO versiones_blender v
    USING (SELECT p_version AS version FROM dual) x
       ON (v.version = x.version)
     WHEN MATCHED THEN UPDATE SET
          v.categoria      = p_categoria,
          v.es_lts         = NVL(p_es_lts, v.es_lts),
          v.soporte_hasta  = NVL(CAST(p_soporte_hasta AS TIMESTAMP), v.soporte_hasta),
          v.notas          = NVL(p_notas, v.notas),
          v.actualizado_en = ahora()
     WHEN NOT MATCHED THEN INSERT (version, categoria, es_lts, soporte_hasta, notas, actualizado_en)
          VALUES (p_version, p_categoria, NVL(p_es_lts, 0), CAST(p_soporte_hasta AS TIMESTAMP), p_notas, ahora());
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Blender ' || p_version || ': ' || p_categoria);
  END guardar_version_blender;

  PROCEDURE registrar_verificacion(p_curso_id        IN VARCHAR2,
                                   p_leccion_id      IN VARCHAR2,
                                   p_version_blender IN VARCHAR2,
                                   p_sistema         IN VARCHAR2,
                                   p_resultado       IN VARCHAR2,
                                   p_diferencias     IN VARCHAR2 DEFAULT NULL,
                                   p_evidencia       IN VARCHAR2 DEFAULT NULL,
                                   p_responsable     IN VARCHAR2 DEFAULT NULL,
                                   p_version_addon   IN VARCHAR2 DEFAULT NULL) IS
    v_version_leccion PLS_INTEGER;
    v_cuenta          PLS_INTEGER;
    v_id              VARCHAR2(36);
  BEGIN
    IF p_resultado NOT IN ('verificada', 'con_diferencias', 'falla') THEN
      RAISE_APPLICATION_ERROR(-20025, 'Resultado: verificada, con_diferencias o falla.');
    END IF;
    IF p_sistema IS NULL THEN
      RAISE_APPLICATION_ERROR(-20026, 'Indica el sistema operativo probado (por ejemplo, Windows 11).');
    END IF;
    BEGIN
      SELECT version INTO v_version_leccion FROM lecciones WHERE curso_id = p_curso_id AND id = p_leccion_id;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20020, 'No existe la lección «' || p_leccion_id || '» en «' || p_curso_id || '».');
    END;
    SELECT COUNT(*) INTO v_cuenta FROM versiones_blender WHERE version = p_version_blender;
    IF v_cuenta = 0 THEN
      RAISE_APPLICATION_ERROR(-20027, 'Registra antes la versión ' || p_version_blender
        || ' con amatista_autor.guardar_version_blender.');
    END IF;
    -- UUID en minúsculas con guiones, como los que genera el backend.
    v_id := LOWER(REGEXP_REPLACE(RAWTOHEX(SYS_GUID()),
                                 '^(.{8})(.{4})(.{4})(.{4})(.{12})$', '\1-\2-\3-\4-\5'));
    INSERT INTO verificaciones_blender (id, curso_id, leccion_id, version_blender, sistema, version_leccion,
                                        version_addon, resultado, diferencias, evidencia, responsable,
                                        verificado_en)
    VALUES (v_id, p_curso_id, p_leccion_id, p_version_blender, SUBSTR(p_sistema, 1, 60), v_version_leccion,
            p_version_addon, p_resultado, SUBSTR(p_diferencias, 1, 1000), SUBSTR(p_evidencia, 1, 300),
            SUBSTR(p_responsable, 1, 100), ahora());
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Verificación anotada: ' || p_leccion_id || ' en Blender ' || p_version_blender
      || ' (' || p_resultado || ').');
  END registrar_verificacion;
END amatista_autor;
/

-- 5. Comprobar: el paquete debe quedar VALID y las tres vistas, creadas.
SELECT object_name, object_type, status
  FROM user_objects
 WHERE object_name IN ('AMATISTA_AUTOR', 'V_AMATISTA_MAPA', 'V_AMATISTA_FICHAS_INCOMPLETAS',
                       'V_AMATISTA_COMPATIBILIDAD')
 ORDER BY object_name, object_type;
