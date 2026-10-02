-- =============================================================================
-- AMATISTA · 001 · INSTALACIÓN INICIAL (Oracle Autonomous Database)
--
-- SOLO PARA UNA BASE NUEVA O UN PROTOTIPO SIN ALUMNOS REALES.
-- Este script es DESTRUCTIVO: borra las tablas de la app con todos sus datos
-- (USUARIOS, SESIONES, SESIONES_WEB, PROGRESO_LECCIONES y, si ya se había
-- aplicado 002, LOGROS, EVENTOS_APRENDIZAJE, CURSOS, MODULOS y LECCIONES)
-- y crea limpias las tres tablas base. NUNCA se ejecuta en producción con
-- alumnos: ahí solo se aplican los scripts incrementales.
--
-- Orden completo (ver sql/LEEME.txt):
--   Base nueva ............... 001 -> 002 -> 003 -> 004 (opcional)
--   Base con datos reales .... 002 -> 003 -> 004 (opcional). NUNCA 001.
-- Después de 001 el backend todavía NO funciona: 002 agrega autenticación,
-- logros, eventos y contenido (y amplía SESIONES.ID para el hash del token).
--
-- Cómo ejecutarlo:
--   1. Entra a Database Actions > SQL como ADMIN.
--   2. Pega este archivo completo.
--   3. Pulsa "Ejecutar script" (F5), NO "Ejecutar sentencia" (Ctrl+Enter).
--   4. Sigue con 002_autenticacion_contenido_eventos.sql.
--
-- Reglas del esquema:
--   - Los identificadores de usuario son texto (VARCHAR2) en TODAS las tablas.
--     Así caben correos y UUID, y las llaves foráneas tienen el mismo tipo en
--     ambos lados (evita ORA-01722 y ORA-02267).
--   - Nombres sin comillas: Oracle los guarda en MAYÚSCULAS y los encuentra
--     escritos de cualquier forma (evita ORA-00942).
-- =============================================================================

-- 1. Borrar las tablas anteriores, también las creadas con comillas o en minúsculas.
--    Incluye las de 002: si quedaran, sus filas apuntarían a usuarios borrados
--    y 002 no podría volver a crear sus llaves foráneas (ORA-02298).
BEGIN
  FOR t IN (
    SELECT table_name
      FROM user_tables
     WHERE UPPER(table_name) IN ('PROGRESO_LECCIONES', 'SESIONES_WEB', 'SESIONES', 'USUARIOS',
                                 'LOGROS', 'EVENTOS_APRENDIZAJE', 'LECCIONES', 'MODULOS', 'CURSOS')
  ) LOOP
    EXECUTE IMMEDIATE 'DROP TABLE "' || t.table_name || '" CASCADE CONSTRAINTS PURGE';
  END LOOP;
END;
/

-- 2. Crear las tablas.
CREATE TABLE usuarios (
  id         VARCHAR2(100)      NOT NULL,
  nombre     VARCHAR2(150 CHAR),
  creado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_usuarios PRIMARY KEY (id)
);

CREATE TABLE sesiones (
  id             VARCHAR2(36)       NOT NULL,
  usuario_id     VARCHAR2(100)      NOT NULL,
  dispositivo    VARCHAR2(200 CHAR),
  activa         NUMBER(1) DEFAULT 1 NOT NULL,
  ultimo_acceso  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_sesiones PRIMARY KEY (id),
  CONSTRAINT fk_sesiones_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT ck_sesiones_activa CHECK (activa IN (0, 1))
);

CREATE INDEX ix_sesiones_usuario ON sesiones (usuario_id);

CREATE TABLE progreso_lecciones (
  usuario_id      VARCHAR2(100) NOT NULL,
  curso_id        VARCHAR2(50)  NOT NULL,
  leccion_id      VARCHAR2(50)  NOT NULL,
  completada      NUMBER(1) DEFAULT 0 NOT NULL,
  puntaje         NUMBER(3),
  intentos        NUMBER(5) DEFAULT 0 NOT NULL,
  actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_progreso_lecciones PRIMARY KEY (usuario_id, curso_id, leccion_id),
  CONSTRAINT fk_progreso_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT ck_progreso_completada CHECK (completada IN (0, 1)),
  CONSTRAINT ck_progreso_puntaje CHECK (puntaje BETWEEN 0 AND 100)
);

-- 3. Verificar: deben aparecer las 3 tablas con sus columnas y tipos.
SELECT table_name, column_name, data_type, data_length
  FROM user_tab_columns
 WHERE table_name IN ('USUARIOS', 'SESIONES', 'PROGRESO_LECCIONES')
 ORDER BY table_name, column_id;
