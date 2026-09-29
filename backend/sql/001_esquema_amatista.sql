-- =============================================================================
-- AMATISTA · ESQUEMA DE BASE DE DATOS (Oracle Autonomous Database)
--
-- Cómo ejecutarlo:
--   1. Entra a Database Actions > SQL como ADMIN.
--   2. Pega este archivo completo.
--   3. Pulsa "Ejecutar script" (F5), NO "Ejecutar sentencia" (Ctrl+Enter).
--
-- ATENCIÓN: borra las tablas de la app (USUARIOS, SESIONES, SESIONES_WEB y
-- PROGRESO_LECCIONES) con sus datos de prueba, y las vuelve a crear limpias.
--
-- Reglas del esquema:
--   - Los identificadores de usuario son texto (VARCHAR2) en TODAS las tablas.
--     Así caben correos y UUID, y las llaves foráneas tienen el mismo tipo en
--     ambos lados (evita ORA-01722 y ORA-02267).
--   - Nombres sin comillas: Oracle los guarda en MAYÚSCULAS y los encuentra
--     escritos de cualquier forma (evita ORA-00942).
-- =============================================================================

-- 1. Borrar las tablas anteriores, también las creadas con comillas o en minúsculas.
BEGIN
  FOR t IN (
    SELECT table_name
      FROM user_tables
     WHERE UPPER(table_name) IN ('PROGRESO_LECCIONES', 'SESIONES_WEB', 'SESIONES', 'USUARIOS')
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
