-- =============================================================================
-- AMATISTA · 012 · AMATISTA TALLER: EL MODELO DEL ALUMNO (motor 5, add-on 5.0)
-- (Oracle Autonomous Database ATP 26ai · Free Tier de 20 GB)
--
-- Con Amatista Taller la unidad es la habilidad, no la práctica
-- (docs/investigacion/2026-10-10_addon_de_ensenanza/, secciones 6.4, 6.8 y 6.11):
--   - HABILIDADES_ALUMNO gana el modelo del alumno que calcula el motor
--     (engine/amatista_engine/taller/modelo.py: BKT con olvido y cajas de
--     Leitner): DOMINIO (P(L) después del último intento), INTENTOS,
--     ACIERTOS, SIN_AYUDA, ULTIMO (epoch del último intento), CAJA y
--     PROXIMO (repaso: epoch del próximo), PRUEBAS (camino rápido), VIO_DEMO
--     y AYUDA_MAX. Todas anulables: una fila de antes (sin modelo) sigue
--     valiendo, y ESTADO (sin_practicar, con_guia, con_pistas, autonoma) lo
--     sigue escribiendo el backend a partir de la fase del modelo.
--   - TALLERES_ALUMNO ... una fila por alumno y taller: si ya hizo el reto
--                         final y lo que eligió para su pieza de la isla
--                         (JSON corto).
--   Las filas de HABILIDADES (el catálogo practices/blender/habilidades.json)
--   las registra solo el backend al arrancar; este script no las escribe.
--
-- Script INCREMENTAL e IDEMPOTENTE, igual que 010 y 011:
--   - NO borra tablas ni filas: solo ALTER TABLE ... ADD y CREATE.
--   - Antes de cada cambio consulta el diccionario; se puede repetir.
--   - No toca usuarios, progreso, prácticas ni contenido.
--
-- Orden: ... -> 007 -> 008 -> 009 -> 010 -> 011 -> 012.
-- Cómo ejecutarlo: Database Actions > SQL con el dueño de las tablas (ADMIN),
-- pega el archivo completo y pulsa "Ejecutar script" (F5).
--
-- Va ANTES de subir el backend del motor 5 (despliegue/actualizar.sh se
-- detiene si diagnostico_oracle.py dice que falta). Si el backend nuevo
-- llegara a correr sin 012, no se rompe nada: GET /api/addon/v1/habilidades
-- y /talleres responden un estado vacío, POST /habilidades/intentos responde
-- 503 (el add-on guarda los intentos y los reenvía) y las prácticas, el
-- enlace en vivo y el progreso de siempre siguen igual.
--
-- ESPACIO (bytes por fila con índices; ver 002 para el método):
--   HABILIDADES_ALUMNO (IOT): +~60 bytes por fila (10 números)
--     x ~60 habilidades por alumno .......................... +~4 KB/alumno
--   TALLERES_ALUMNO ~200 x 9 talleres por alumno ............ ~2 KB/alumno
--   Total permanente por alumno: de ~22 KB a ~28 KB. 5 GB siguen alcanzando
--   para más de 150,000 alumnos.
--   Además cada intento de una habilidad deja un evento activity_submitted
--   (~200 bytes) en EVENTOS_APRENDIZAJE, que ya purga 003 (400 días): unos
--   15 intentos por día de práctica.
--
-- ÍNDICES (regla 2: nada sobre columnas que ya son PRIMARY KEY o UNIQUE):
--   ninguno nuevo. La llave primaria de TALLERES_ALUMNO empieza por
--   usuario_id y cubre la única consulta (los talleres de un alumno);
--   HABILIDADES_ALUMNO ya es IOT por (usuario_id, habilidad_id).
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  v_cuenta  PLS_INTEGER;
  v_tablas  SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST('TALLERES_ALUMNO');

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

  PROCEDURE exigir_tabla(p_tabla IN VARCHAR2, p_script IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      RAISE_APPLICATION_ERROR(-20001, 'Falta la tabla ' || p_tabla || '. Ejecuta antes ' || p_script || '.');
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

  PROCEDURE crear_tabla(p_tabla IN VARCHAR2, p_ddl IN VARCHAR2) IS
  BEGIN
    IF NOT existe_tabla(p_tabla) THEN
      ejecutar(p_ddl);
    END IF;
  END;

BEGIN
  -- 0. Punto de partida: 002 (usuarios) y 005 (habilidades por alumno).
  exigir_tabla('USUARIOS', '002_autenticacion_contenido_eventos.sql');
  exigir_tabla('HABILIDADES', '005_niveles_habilidades_versiones.sql');
  exigir_tabla('HABILIDADES_ALUMNO', '005_niveles_habilidades_versiones.sql');

  -- 1. HABILIDADES_ALUMNO: el modelo del alumno (Dominio.a_dict() del motor).
  --    Columnas anulables y sin DEFAULT: en una IOT Oracle no reescribe las
  --    filas que ya existen, y una fila sin DOMINIO es una fila de antes.
  agregar_columna('HABILIDADES_ALUMNO', 'DOMINIO', 'NUMBER(9,8)');
  agregar_columna('HABILIDADES_ALUMNO', 'INTENTOS', 'NUMBER(6)');
  agregar_columna('HABILIDADES_ALUMNO', 'ACIERTOS', 'NUMBER(6)');
  agregar_columna('HABILIDADES_ALUMNO', 'SIN_AYUDA', 'NUMBER(6)');
  agregar_columna('HABILIDADES_ALUMNO', 'ULTIMO', 'NUMBER(14,3)');
  agregar_columna('HABILIDADES_ALUMNO', 'CAJA', 'NUMBER(2)');
  agregar_columna('HABILIDADES_ALUMNO', 'PROXIMO', 'NUMBER(14,3)');
  agregar_columna('HABILIDADES_ALUMNO', 'PRUEBAS', 'NUMBER(4)');
  agregar_columna('HABILIDADES_ALUMNO', 'VIO_DEMO', 'NUMBER(1)');
  agregar_columna('HABILIDADES_ALUMNO', 'AYUDA_MAX', 'NUMBER(1)');
  agregar_restriccion('HABILIDADES_ALUMNO', 'CK_HAB_ALUMNO_DOMINIO',
                      'CHECK (dominio IS NULL OR dominio BETWEEN 0 AND 1)');
  agregar_restriccion('HABILIDADES_ALUMNO', 'CK_HAB_ALUMNO_VIO_DEMO',
                      'CHECK (vio_demo IS NULL OR vio_demo IN (0, 1))');
  agregar_restriccion('HABILIDADES_ALUMNO', 'CK_HAB_ALUMNO_AYUDA_MAX',
                      'CHECK (ayuda_max IS NULL OR ayuda_max BETWEEN 0 AND 4)');

  -- 2. TALLERES_ALUMNO: el reto final y la pieza de la isla de cada taller.
  --    Tabla normal (no IOT): ISLA es un CLOB.
  crear_tabla('TALLERES_ALUMNO', '
    CREATE TABLE talleres_alumno (
      usuario_id       VARCHAR2(100)       NOT NULL,
      taller_id        VARCHAR2(60)        NOT NULL,
      reto_hecho       NUMBER(1) DEFAULT 0 NOT NULL,
      isla             CLOB,
      actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT pk_talleres_alumno PRIMARY KEY (usuario_id, taller_id),
      CONSTRAINT fk_talleres_alumno_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
      CONSTRAINT ck_talleres_alumno_reto CHECK (reto_hecho IN (0, 1)),
      CONSTRAINT ck_talleres_alumno_isla CHECK (isla IS JSON)
    )');

  -- 3. Si ya se usa AMATISTA_APP (004), recibe permisos sobre la tabla nueva
  --    (los de HABILIDADES_ALUMNO ya cubren sus columnas nuevas). Volver a
  --    ejecutar 004 hace lo mismo.
  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta > 0 THEN
    FOR i IN 1 .. v_tablas.COUNT LOOP
      ejecutar('GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app');
    END LOOP;
  END IF;

  DBMS_OUTPUT.PUT_LINE('012 aplicado. Actualiza el backend y reinicia: sudo systemctl restart amatista-backend');
END;
/

-- 4. Verificar: las 21 tablas deben decir OK (columnas reales = esperadas).
--    Esta consulta sustituye a la de 011 (HABILIDADES_ALUMNO pasa de 5 a 15
--    columnas y se suma TALLERES_ALUMNO).
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
        UNION ALL SELECT 'TALLERES_ALUMNO', 5 FROM dual) e
  LEFT JOIN (SELECT table_name, COUNT(*) AS columnas
               FROM user_tab_columns
              GROUP BY table_name) c
    ON c.table_name = e.tabla
 ORDER BY e.tabla;

-- 5. Filas: talleres por alumno y habilidades que ya tienen modelo del alumno.
SELECT COUNT(*) AS talleres_alumno FROM talleres_alumno;

SELECT COUNT(*) AS habilidades_con_modelo FROM habilidades_alumno WHERE dominio IS NOT NULL;
