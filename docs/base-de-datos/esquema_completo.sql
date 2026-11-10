-- #############################################################################
-- #                                                                           #
-- #   ESQUEMA COMPLETO DE AMATISTA (ORACLE) · ARCHIVO DE REFERENCIA          #
-- #                                                                           #
-- #   Solo referencia. No ejecutar. Para instalar o actualizar usa los        #
-- #   scripts de backend/sql/ en el orden de LEEME.txt.                       #
-- #                                                                           #
-- #   Este archivo NO es idempotente, NO comprueba nada, NO da permisos y     #
-- #   NO crea el job de purga. Ejecutarlo sobre una base con datos fallaría   #
-- #   (las tablas ya existen) y sobre una base vacía dejaría un esquema       #
-- #   que los scripts 002-012 no reconocen como suyo en todos los detalles    #
-- #   (orden de columnas, partición inicial, permisos).                      #
-- #                                                                           #
-- #############################################################################
--
-- Qué es: el estado FINAL del esquema ADMIN tras aplicar
--   001 -> 002 -> 003 -> 005 -> 006 -> 007 -> 008 -> 010 -> 011 -> 012 (y 004, opcional;
--   009 solo archiva filas y no cambia el esquema),
-- escrito como un CREATE TABLE por tabla con los ALTER TABLE de los scripts
-- posteriores ya aplicados. Cada columna indica entre corchetes el script que
-- la crea o la cambia por última vez.
--
-- Actualizado: 10 de octubre de 2026 (script 012, Amatista Taller, motor 5).
-- Fuentes: backend/sql/001_esquema_amatista.sql ... 012_taller.sql
--          y backend/database/modelos.py (fuente de verdad de columnas).
-- Explicación de cada tabla y columna: docs/base-de-datos/esquema.md
--
-- Producción (03/10/2026): tiene 002, 003, 005 y 006 sobre la base de 001
-- (14 tablas). 007 (las 4 últimas tablas y V_AMATISTA_PRACTICAS) está
-- pendiente hasta después del piloto del 8 de octubre (T-055), igual que 008
-- y 009 (T-064), 010 (las 2 tablas del enlace en vivo, T-087) y 011 (la
-- columna ADDON_ENLACES.DETALLE de Motor 3.5.1) y 012 (el modelo del alumno
-- de Amatista Taller, motor 5). 004 no se ha aplicado (T-004).
--
-- Orden de este archivo: por dependencias de llaves foráneas (NIVELES va
-- antes que MODULOS aunque la crea 005; en la base real MODULOS existe antes
-- y FK_MODULOS_NIVEL se agrega con ALTER TABLE). Dentro de cada tabla, las
-- columnas siguen el orden real de Oracle (COLUMN_ID): las que agregó un ALTER
-- quedan al final.
-- =============================================================================


-- =============================================================================
-- 1. IDENTIDAD
-- =============================================================================

-- USUARIOS · 17 columnas · creada por 001, ampliada por 002.
CREATE TABLE usuarios (
  id                   VARCHAR2(100)       NOT NULL,                   -- [001]
  nombre               VARCHAR2(150 CHAR),                             -- [001]
  creado_en            TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,        -- [001]
  email                VARCHAR2(100 CHAR),                             -- [002] minúsculas; NULL = anónimo
  telefono             VARCHAR2(25 CHAR),                              -- [002]
  rol                  VARCHAR2(20) DEFAULT 'alumno' NOT NULL,         -- [002]
  password_hash        VARCHAR2(255),                                  -- [002] pbkdf2_sha256$...; NULL = anónimo
  correo_confirmado    NUMBER(1) DEFAULT 0 NOT NULL,                   -- [002]
  codigo_verificacion  VARCHAR2(64),                                   -- [002] hash SHA-256 del código
  codigo_proposito     VARCHAR2(12),                                   -- [002] 'correo' | 'password'
  codigo_expira        TIMESTAMP,                                      -- [002]
  codigo_intentos      NUMBER(5) DEFAULT 0 NOT NULL,                   -- [002]
  intentos_fallidos    NUMBER(10) DEFAULT 0 NOT NULL,                  -- [002]
  bloqueado_hasta      TIMESTAMP,                                      -- [002]
  es_prueba            NUMBER(1) DEFAULT 0 NOT NULL,                   -- [002]
  fusionado_en         VARCHAR2(100),                                  -- [002] id de la cuenta destino (sin FK)
  ultimo_acceso        TIMESTAMP,                                      -- [002]
  CONSTRAINT pk_usuarios PRIMARY KEY (id),                                                      -- [001]
  CONSTRAINT uq_usuarios_email UNIQUE (email),                                                  -- [002]
  CONSTRAINT ck_usuarios_rol CHECK (rol IN ('alumno', 'profesor', 'admin')),                    -- [002]
  CONSTRAINT ck_usuarios_correo_confirmado CHECK (correo_confirmado IN (0, 1)),                 -- [002]
  CONSTRAINT ck_usuarios_es_prueba CHECK (es_prueba IN (0, 1))                                  -- [002]
);

-- SESIONES · 7 columnas · creada por 001, ampliada por 002.
CREATE TABLE sesiones (
  id             VARCHAR2(64)        NOT NULL,                         -- [002] era VARCHAR2(36) en 001; hash SHA-256 del token
  usuario_id     VARCHAR2(100)       NOT NULL,                         -- [001]
  dispositivo    VARCHAR2(200 CHAR),                                   -- [001]
  activa         NUMBER(1) DEFAULT 1 NOT NULL,                         -- [001]
  ultimo_acceso  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,              -- [001]
  creado_en      TIMESTAMP DEFAULT SYSTIMESTAMP,                       -- [002] anulable
  expira_en      TIMESTAMP,                                            -- [002]
  CONSTRAINT pk_sesiones PRIMARY KEY (id),                                                      -- [001]
  CONSTRAINT fk_sesiones_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),             -- [001]
  CONSTRAINT ck_sesiones_activa CHECK (activa IN (0, 1))                                        -- [001]
);
CREATE INDEX ix_sesiones_usuario ON sesiones (usuario_id);                                      -- [001]


-- =============================================================================
-- 2. PROGRESO Y EVENTOS
-- =============================================================================

-- PROGRESO_LECCIONES · 9 columnas · creada por 001, ampliada por 002.
CREATE TABLE progreso_lecciones (
  usuario_id      VARCHAR2(100)      NOT NULL,                         -- [001]
  curso_id        VARCHAR2(50)       NOT NULL,                         -- [001] sin FK
  leccion_id      VARCHAR2(50)       NOT NULL,                         -- [001] sin FK
  completada      NUMBER(1) DEFAULT 0 NOT NULL,                        -- [001] nunca vuelve de 1 a 0
  puntaje         NUMBER(3),                                           -- [001] el máximo
  intentos        NUMBER(5) DEFAULT 0 NOT NULL,                        -- [001]
  actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,             -- [001]
  datos_ligeros   VARCHAR2(250 CHAR),                                  -- [002] JSON plano compacto
  completada_en   TIMESTAMP,                                           -- [002]
  CONSTRAINT pk_progreso_lecciones PRIMARY KEY (usuario_id, curso_id, leccion_id),              -- [001]
  CONSTRAINT fk_progreso_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),             -- [001]
  CONSTRAINT ck_progreso_completada CHECK (completada IN (0, 1)),                               -- [001]
  CONSTRAINT ck_progreso_puntaje CHECK (puntaje BETWEEN 0 AND 100),                             -- [001]
  CONSTRAINT ck_progreso_datos_json CHECK (datos_ligeros IS JSON)                               -- [002]
);

-- LOGROS · 3 columnas · 002 · tabla organizada por índice (IOT).
CREATE TABLE logros (
  usuario_id   VARCHAR2(100) NOT NULL,
  insignia_id  VARCHAR2(100) NOT NULL,                                 -- '<curso_id>:<modulo_id>'
  obtenido_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_logros PRIMARY KEY (usuario_id, insignia_id),
  CONSTRAINT fk_logros_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
) ORGANIZATION INDEX;

-- EVENTOS_APRENDIZAJE · 11 columnas · 002 · particionada por mes.
-- Si la base no admite particionado (ORA-00439), 002 la crea sin la cláusula
-- PARTITION BY y los índices sin LOCAL.
CREATE TABLE eventos_aprendizaje (
  id                  VARCHAR2(36)       NOT NULL,                     -- UUID del dispositivo
  usuario_id          VARCHAR2(100)      NOT NULL,
  tipo                VARCHAR2(30)       NOT NULL,
  curso_id            VARCHAR2(50),                                    -- sin FK
  leccion_id          VARCHAR2(50),                                    -- sin FK
  sesion_aprendizaje  VARCHAR2(36),
  ocurrido_en         TIMESTAMP          NOT NULL,                     -- llave de partición
  recibido_en         TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  version_app         VARCHAR2(20),
  es_prueba           NUMBER(1) DEFAULT 0 NOT NULL,
  datos               VARCHAR2(250 CHAR),                              -- JSON compacto, SIN CHECK IS JSON
  CONSTRAINT pk_eventos_aprendizaje PRIMARY KEY (id),
  CONSTRAINT fk_eventos_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT ck_eventos_tipo CHECK (tipo IN ('account_created', 'learning_session_started',
    'lesson_completed', 'activity_submitted', 'sync_succeeded')),
  CONSTRAINT ck_eventos_es_prueba CHECK (es_prueba IN (0, 1))
)
PARTITION BY RANGE (ocurrido_en) INTERVAL (NUMTOYMINTERVAL(1, 'MONTH'))
(PARTITION p_inicial VALUES LESS THAN (TIMESTAMP '2026-01-01 00:00:00'));
CREATE INDEX ix_eventos_usuario_fecha ON eventos_aprendizaje (usuario_id, ocurrido_en) LOCAL;
CREATE INDEX ix_eventos_fecha ON eventos_aprendizaje (ocurrido_en) LOCAL;


-- =============================================================================
-- 3. CONTENIDO: CURSOS > NIVELES > MODULOS > LECCIONES
-- =============================================================================

-- CURSOS · 14 columnas · creada por 002, ampliada por 008.
CREATE TABLE cursos (
  id              VARCHAR2(50)        NOT NULL,
  numero          VARCHAR2(5),
  titulo          VARCHAR2(100 CHAR)  NOT NULL,
  subtitulo       VARCHAR2(150 CHAR),
  descripcion     VARCHAR2(1000 CHAR),
  nivel           VARCHAR2(30 CHAR),
  acento          VARCHAR2(20),
  recurso_texto   VARCHAR2(100 CHAR),
  recurso_url     VARCHAR2(300 CHAR),
  orden           NUMBER(5) DEFAULT 0 NOT NULL,
  estado          VARCHAR2(12) DEFAULT 'publicado' NOT NULL,
  actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  ruta            VARCHAR2(30),                                        -- [008] 'blender', 'aframe'
  requisito_id    VARCHAR2(50),                                        -- [008] curso que conviene terminar antes
  CONSTRAINT pk_cursos PRIMARY KEY (id),
  CONSTRAINT ck_cursos_estado CHECK (estado IN ('borrador', 'publicado', 'archivado')),
  CONSTRAINT fk_cursos_requisito FOREIGN KEY (requisito_id) REFERENCES cursos (id),          -- [008]
  CONSTRAINT ck_cursos_requisito CHECK (requisito_id IS NULL OR requisito_id <> id)          -- [008]
);

-- NIVELES · 10 columnas · 005.
CREATE TABLE niveles (
  id               VARCHAR2(50)        NOT NULL,                       -- '<curso>-n<numero>[-<rama>]'
  curso_id         VARCHAR2(50)        NOT NULL,
  numero           NUMBER(1)           NOT NULL,
  rama             VARCHAR2(50),                                       -- solo nivel 5
  titulo           VARCHAR2(100 CHAR)  NOT NULL,
  perfil           VARCHAR2(500 CHAR),
  proyecto         VARCHAR2(300 CHAR),
  criterio_salida  VARCHAR2(500 CHAR),
  estado           VARCHAR2(12) DEFAULT 'borrador' NOT NULL,
  actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_niveles PRIMARY KEY (id),
  CONSTRAINT fk_niveles_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
  CONSTRAINT uq_niveles_curso_numero_rama UNIQUE (curso_id, numero, rama),
  CONSTRAINT ck_niveles_numero CHECK (numero IN (1, 2, 3, 4, 5)),
  CONSTRAINT ck_niveles_rama CHECK (numero = 5 OR rama IS NULL),
  CONSTRAINT ck_niveles_estado CHECK (estado IN ('borrador', 'publicado', 'archivado'))
);

-- MODULOS · 12 columnas · creada por 002, ampliada por 005.
CREATE TABLE modulos (
  id              VARCHAR2(50)        NOT NULL,                        -- [002]
  curso_id        VARCHAR2(50)        NOT NULL,                        -- [002]
  numero          NUMBER(5)           NOT NULL,                        -- [002]
  titulo          VARCHAR2(200 CHAR)  NOT NULL,                        -- [002]
  descripcion     VARCHAR2(1000 CHAR),                                 -- [002]
  insignia        VARCHAR2(80 CHAR),                                   -- [002]
  minutos         NUMBER(5),                                           -- [002]
  estado          VARCHAR2(12) DEFAULT 'borrador' NOT NULL,            -- [002]
  version         NUMBER(10) DEFAULT 1 NOT NULL,                       -- [002]
  actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,             -- [002]
  publicado_en    TIMESTAMP,                                           -- [002]
  nivel_id        VARCHAR2(50),                                        -- [005] NULL = módulo anterior a los niveles
  CONSTRAINT pk_modulos PRIMARY KEY (id),                                                       -- [002]
  CONSTRAINT fk_modulos_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),                    -- [002]
  CONSTRAINT ck_modulos_estado CHECK (estado IN ('borrador', 'publicado', 'archivado')),        -- [002]
  CONSTRAINT fk_modulos_nivel FOREIGN KEY (nivel_id) REFERENCES niveles (id)                    -- [005]
);
CREATE INDEX ix_modulos_curso ON modulos (curso_id, numero);                                    -- [002]

-- LECCIONES · 14 columnas · 002.
CREATE TABLE lecciones (
  curso_id           VARCHAR2(50)        NOT NULL,
  id                 VARCHAR2(50)        NOT NULL,
  modulo_id          VARCHAR2(50)        NOT NULL,
  orden              NUMBER(5)           NOT NULL,
  titulo             VARCHAR2(200 CHAR)  NOT NULL,
  tipo               VARCHAR2(30)        NOT NULL,
  duracion_segundos  NUMBER(7),
  bloqueada          NUMBER(1) DEFAULT 1 NOT NULL,
  contenido          CLOB                NOT NULL,                     -- JSON completo de la lección
  reemplaza          VARCHAR2(250),                                    -- ids viejos separados por comas
  estado             VARCHAR2(12) DEFAULT 'borrador' NOT NULL,
  version            NUMBER(10) DEFAULT 1 NOT NULL,
  actualizado_en     TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  publicado_en       TIMESTAMP,
  CONSTRAINT pk_lecciones PRIMARY KEY (curso_id, id),
  CONSTRAINT fk_lecciones_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
  CONSTRAINT fk_lecciones_modulo FOREIGN KEY (modulo_id) REFERENCES modulos (id),
  CONSTRAINT ck_lecciones_contenido CHECK (contenido IS JSON),
  CONSTRAINT ck_lecciones_bloqueada CHECK (bloqueada IN (0, 1)),
  CONSTRAINT ck_lecciones_estado CHECK (estado IN ('borrador', 'publicado', 'archivado'))
);
CREATE INDEX ix_lecciones_modulo ON lecciones (modulo_id, orden);


-- =============================================================================
-- 4. REESTRUCTURACIÓN v3 (005): HABILIDADES, RÚBRICA, VERSIONES DE BLENDER
-- =============================================================================

-- HABILIDADES · 7 columnas · 005 (007 inserta 4 filas bl-* si existe blender-n1).
CREATE TABLE habilidades (
  id              VARCHAR2(50)        NOT NULL,
  curso_id        VARCHAR2(50)        NOT NULL,
  nivel_id        VARCHAR2(50),
  nombre          VARCHAR2(150 CHAR)  NOT NULL,
  descripcion     VARCHAR2(500 CHAR),
  orden           NUMBER(5) DEFAULT 0 NOT NULL,
  actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_habilidades PRIMARY KEY (id),
  CONSTRAINT fk_habilidades_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
  CONSTRAINT fk_habilidades_nivel FOREIGN KEY (nivel_id) REFERENCES niveles (id)
);
CREATE INDEX ix_habilidades_curso ON habilidades (curso_id, orden);

-- HABILIDADES_ALUMNO · 15 columnas · 005 + 012 · IOT.
CREATE TABLE habilidades_alumno (
  usuario_id      VARCHAR2(100)       NOT NULL,
  habilidad_id    VARCHAR2(50)        NOT NULL,
  estado          VARCHAR2(14) DEFAULT 'sin_practicar' NOT NULL,
  evidencia       VARCHAR2(300 CHAR),
  actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  dominio         NUMBER(9,8),                                         -- [012] P(L) del modelo del alumno
  intentos        NUMBER(6),                                           -- [012]
  aciertos        NUMBER(6),                                           -- [012]
  sin_ayuda       NUMBER(6),                                           -- [012]
  ultimo          NUMBER(14,3),                                        -- [012] epoch del último intento
  caja            NUMBER(2),                                           -- [012] Leitner
  proximo         NUMBER(14,3),                                        -- [012] epoch del próximo repaso
  pruebas         NUMBER(4),                                           -- [012]
  vio_demo        NUMBER(1),                                           -- [012]
  ayuda_max       NUMBER(1),                                           -- [012]
  CONSTRAINT pk_habilidades_alumno PRIMARY KEY (usuario_id, habilidad_id),
  CONSTRAINT fk_hab_alumno_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT fk_hab_alumno_habilidad FOREIGN KEY (habilidad_id) REFERENCES habilidades (id),
  CONSTRAINT ck_hab_alumno_estado CHECK (estado IN ('sin_practicar', 'con_guia', 'con_pistas', 'autonoma')),
  CONSTRAINT ck_hab_alumno_dominio CHECK (dominio IS NULL OR dominio BETWEEN 0 AND 1),             -- [012]
  CONSTRAINT ck_hab_alumno_vio_demo CHECK (vio_demo IS NULL OR vio_demo IN (0, 1)),                -- [012]
  CONSTRAINT ck_hab_alumno_ayuda_max CHECK (ayuda_max IS NULL OR ayuda_max BETWEEN 0 AND 4)        -- [012]
) ORGANIZATION INDEX;

-- EVALUACIONES_RUBRICA · 8 columnas · 005 · tabla normal (no IOT, ORA-01429).
CREATE TABLE evaluaciones_rubrica (
  usuario_id      VARCHAR2(100)       NOT NULL,
  nivel_id        VARCHAR2(50)        NOT NULL,
  criterio        VARCHAR2(1)         NOT NULL,
  logro           VARCHAR2(12) DEFAULT 'pendiente' NOT NULL,
  evidencia       VARCHAR2(300 CHAR),
  comentario      VARCHAR2(500 CHAR),
  revisado_por    VARCHAR2(100),                                       -- sin FK
  actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_evaluaciones_rubrica PRIMARY KEY (usuario_id, nivel_id, criterio),
  CONSTRAINT fk_rubrica_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT fk_rubrica_nivel FOREIGN KEY (nivel_id) REFERENCES niveles (id),
  CONSTRAINT ck_rubrica_criterio CHECK (criterio IN ('A', 'B', 'C', 'D', 'E')),
  CONSTRAINT ck_rubrica_logro CHECK (logro IN ('pendiente', 'con_ayuda', 'autonomo'))
);

-- VERSIONES_BLENDER · 6 columnas · 005 · nace vacía.
CREATE TABLE versiones_blender (
  version         VARCHAR2(20)        NOT NULL,
  categoria       VARCHAR2(14) DEFAULT 'sin_verificar' NOT NULL,
  es_lts          NUMBER(1) DEFAULT 0 NOT NULL,
  soporte_hasta   TIMESTAMP,
  notas           VARCHAR2(500 CHAR),
  actualizado_en  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_versiones_blender PRIMARY KEY (version),
  CONSTRAINT ck_versiones_categoria CHECK (categoria IN ('principal', 'compatible', 'sin_verificar', 'retirada')),
  CONSTRAINT ck_versiones_es_lts CHECK (es_lts IN (0, 1))
);

-- VERIFICACIONES_BLENDER · 12 columnas · 005.
CREATE TABLE verificaciones_blender (
  id               VARCHAR2(36)        NOT NULL,
  curso_id         VARCHAR2(50)        NOT NULL,
  leccion_id       VARCHAR2(50)        NOT NULL,
  version_blender  VARCHAR2(20)        NOT NULL,
  sistema          VARCHAR2(60 CHAR)   NOT NULL,
  version_leccion  NUMBER(10)          NOT NULL,
  version_addon    VARCHAR2(20),
  resultado        VARCHAR2(16)        NOT NULL,
  diferencias      VARCHAR2(1000 CHAR),
  evidencia        VARCHAR2(300 CHAR),
  responsable      VARCHAR2(100 CHAR),
  verificado_en    TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_verificaciones_blender PRIMARY KEY (id),
  CONSTRAINT fk_verificaciones_leccion FOREIGN KEY (curso_id, leccion_id) REFERENCES lecciones (curso_id, id),
  CONSTRAINT fk_verificaciones_version FOREIGN KEY (version_blender) REFERENCES versiones_blender (version),
  CONSTRAINT ck_verificaciones_resultado CHECK (resultado IN ('verificada', 'con_diferencias', 'falla'))
);
CREATE INDEX ix_verificaciones_leccion ON verificaciones_blender (curso_id, leccion_id);


-- =============================================================================
-- 5. MOTOR DE PRÁCTICAS DE BLENDER (007) · PENDIENTE EN PRODUCCIÓN (T-055)
-- =============================================================================

-- ADDON_VINCULOS · 8 columnas · 007.
CREATE TABLE addon_vinculos (
  id             VARCHAR2(36)        NOT NULL,
  codigo         VARCHAR2(9)         NOT NULL,                         -- 'ABCD-2345'
  secreto_hash   VARCHAR2(64)        NOT NULL,
  usuario_id     VARCHAR2(100),
  dispositivo    VARCHAR2(200 CHAR),
  estado         VARCHAR2(10) DEFAULT 'pendiente' NOT NULL,
  creado_en      TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  expira_en      TIMESTAMP           NOT NULL,
  CONSTRAINT pk_addon_vinculos PRIMARY KEY (id),
  CONSTRAINT uq_vinculos_codigo UNIQUE (codigo),
  CONSTRAINT fk_vinculos_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT ck_vinculos_estado CHECK (estado IN ('pendiente', 'listo', 'canjeado'))
);

-- PRACTICAS · 13 columnas · 007.
CREATE TABLE practicas (
  id                 VARCHAR2(80)        NOT NULL,                     -- 'blender.n1.mesa'
  curso_id           VARCHAR2(50),
  leccion_id         VARCHAR2(50),                                     -- sin FK
  titulo             VARCHAR2(200 CHAR)  NOT NULL,
  nivel              NUMBER(1) DEFAULT 1 NOT NULL,
  version            NUMBER(10) DEFAULT 1 NOT NULL,
  version_publicada  NUMBER(10),
  definicion         CLOB                NOT NULL,                     -- practice.json vigente
  estado             VARCHAR2(12) DEFAULT 'borrador' NOT NULL,
  origen             VARCHAR2(12) DEFAULT 'addon' NOT NULL,
  autor_id           VARCHAR2(100),
  actualizado_en     TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  publicado_en       TIMESTAMP,
  CONSTRAINT pk_practicas PRIMARY KEY (id),
  CONSTRAINT fk_practicas_curso FOREIGN KEY (curso_id) REFERENCES cursos (id),
  CONSTRAINT fk_practicas_autor FOREIGN KEY (autor_id) REFERENCES usuarios (id),
  CONSTRAINT ck_practicas_definicion CHECK (definicion IS JSON),
  CONSTRAINT ck_practicas_nivel CHECK (nivel IN (1, 2, 3, 4, 5)),
  CONSTRAINT ck_practicas_estado CHECK (estado IN ('borrador', 'publicado', 'archivado')),
  CONSTRAINT ck_practicas_origen CHECK (origen IN ('repositorio', 'addon', 'panel')),
  CONSTRAINT ck_practicas_publicada CHECK (version_publicada IS NULL OR version_publicada <= version)
);
CREATE INDEX ix_practicas_leccion ON practicas (curso_id, leccion_id);

-- PRACTICA_VERSIONES · 9 columnas · 007.
CREATE TABLE practica_versiones (
  practica_id      VARCHAR2(80)        NOT NULL,
  version          NUMBER(10)          NOT NULL,
  definicion       CLOB                NOT NULL,
  huella           VARCHAR2(64)        NOT NULL,                       -- SHA-256 del JSON canónico
  nota             VARCHAR2(500 CHAR),
  autor_id         VARCHAR2(100),
  version_addon    VARCHAR2(20),
  version_blender  VARCHAR2(20),                                       -- sin FK
  creado_en        TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_practica_versiones PRIMARY KEY (practica_id, version),
  CONSTRAINT fk_prac_versiones_practica FOREIGN KEY (practica_id) REFERENCES practicas (id),
  CONSTRAINT fk_prac_versiones_autor FOREIGN KEY (autor_id) REFERENCES usuarios (id),
  CONSTRAINT ck_prac_versiones_definicion CHECK (definicion IS JSON)
);

-- PROGRESO_PRACTICAS · 16 columnas · 007.
CREATE TABLE progreso_practicas (
  usuario_id       VARCHAR2(100)       NOT NULL,
  practica_id      VARCHAR2(80)        NOT NULL,
  version          NUMBER(10) DEFAULT 1 NOT NULL,
  progreso         NUMBER(3) DEFAULT 0 NOT NULL,
  completada       NUMBER(1) DEFAULT 0 NOT NULL,
  autonomia        VARCHAR2(14),
  pistas           NUMBER(5) DEFAULT 0 NOT NULL,
  correcciones     NUMBER(5) DEFAULT 0 NOT NULL,
  intentos         NUMBER(10) DEFAULT 0 NOT NULL,
  paso_actual      VARCHAR2(80),
  objetivos        VARCHAR2(1000 CHAR),                                -- JSON: ["cubierta","patas"]
  version_blender  VARCHAR2(20),
  version_addon    VARCHAR2(20),
  abierta_en       TIMESTAMP,
  completada_en    TIMESTAMP,
  actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_progreso_practicas PRIMARY KEY (usuario_id, practica_id),
  CONSTRAINT fk_prog_practicas_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT fk_prog_practicas_practica FOREIGN KEY (practica_id) REFERENCES practicas (id),
  CONSTRAINT ck_prog_practicas_progreso CHECK (progreso BETWEEN 0 AND 100),
  CONSTRAINT ck_prog_practicas_completada CHECK (completada IN (0, 1)),
  CONSTRAINT ck_prog_practicas_autonomia CHECK (autonomia IN ('con_guia', 'con_pistas', 'autonoma')),
  CONSTRAINT ck_prog_practicas_objetivos CHECK (objetivos IS JSON)
);
CREATE INDEX ix_prog_practicas_practica ON progreso_practicas (practica_id);


-- =============================================================================
-- 5b. ENLACE EN VIVO PLATAFORMA <-> BLENDER (010 y 011) · PENDIENTE EN PRODUCCIÓN (T-087)
-- =============================================================================

-- ADDON_ENLACES · 12 columnas · 010 y 011. Un Blender abierto y conectado: su
-- último latido, la orden pendiente y lo que muestra el instructor (011). Se borra con su sesión (ON DELETE CASCADE).
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
  orden            VARCHAR2(1000 CHAR),                                -- JSON: {"id","tipo","datos"}
  orden_en         TIMESTAMP,
  detalle          CLOB,                                               -- [011] JSON: lo que muestra el instructor
  CONSTRAINT pk_addon_enlaces PRIMARY KEY (sesion_id),
  CONSTRAINT fk_enlaces_sesion FOREIGN KEY (sesion_id) REFERENCES sesiones (id) ON DELETE CASCADE,
  CONSTRAINT fk_enlaces_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT ck_enlaces_progreso CHECK (progreso IS NULL OR progreso BETWEEN 0 AND 100),
  CONSTRAINT ck_enlaces_enfocado CHECK (enfocado IN (0, 1)),
  CONSTRAINT ck_enlaces_orden CHECK (orden IS JSON),
  CONSTRAINT ck_enlaces_detalle CHECK (detalle IS JSON)                       -- [011]
);
CREATE INDEX ix_addon_enlaces_usuario ON addon_enlaces (usuario_id);

-- ADDON_AJUSTES · 3 columnas · 010. Cómo se ve Blender para cada alumno
-- (Mi Blender): enfoque, acompañamiento, avisos y tarjeta 3D.
CREATE TABLE addon_ajustes (
  usuario_id       VARCHAR2(100)       NOT NULL,
  datos            VARCHAR2(1000 CHAR) NOT NULL,                       -- JSON
  actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_addon_ajustes PRIMARY KEY (usuario_id),
  CONSTRAINT fk_ajustes_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT ck_ajustes_datos CHECK (datos IS JSON)
);

-- TALLERES_ALUMNO · 5 columnas · 012 · tabla normal (ISLA es CLOB).
CREATE TABLE talleres_alumno (
  usuario_id       VARCHAR2(100)       NOT NULL,
  taller_id        VARCHAR2(60)        NOT NULL,                         -- practices/blender/habilidades.json
  reto_hecho       NUMBER(1) DEFAULT 0 NOT NULL,
  isla             CLOB,                                                 -- JSON
  actualizado_en   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_talleres_alumno PRIMARY KEY (usuario_id, taller_id),
  CONSTRAINT fk_talleres_alumno_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
  CONSTRAINT ck_talleres_alumno_reto CHECK (reto_hecho IN (0, 1)),
  CONSTRAINT ck_talleres_alumno_isla CHECK (isla IS JSON)
);


-- =============================================================================
-- 6. VISTAS
-- =============================================================================

-- V_AMATISTA_ESPACIO · 003.
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

-- V_AMATISTA_MAPA · 006.
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

-- V_AMATISTA_FICHAS_INCOMPLETAS · 006.
CREATE OR REPLACE VIEW v_amatista_fichas_incompletas AS
SELECT curso_id, nivel_numero, nivel_rama, modulo_numero, modulo_id, leccion_orden,
       leccion_id, leccion_titulo, leccion_estado, pendientes
  FROM v_amatista_mapa
 WHERE pendientes IS NOT NULL
   AND modulo_estado <> 'archivado'
   AND NVL(leccion_estado, 'borrador') <> 'archivado';

-- V_AMATISTA_COMPATIBILIDAD · 006.
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

-- V_AMATISTA_PRACTICAS · 007.
CREATE OR REPLACE VIEW v_amatista_practicas AS
SELECT p.id,
       p.titulo,
       p.nivel,
       p.estado,
       p.version,
       p.version_publicada,
       p.origen,
       p.curso_id,
       p.leccion_id,
       u.email                                                        AS autor,
       p.actualizado_en,
       p.publicado_en,
       (SELECT COUNT(*) FROM progreso_practicas pp WHERE pp.practica_id = p.id)                      AS alumnos,
       (SELECT COUNT(*) FROM progreso_practicas pp WHERE pp.practica_id = p.id AND pp.completada = 1) AS completadas,
       (SELECT ROUND(AVG(pp.progreso)) FROM progreso_practicas pp WHERE pp.practica_id = p.id)       AS progreso_promedio
  FROM practicas p
  LEFT JOIN usuarios u ON u.id = p.autor_id;


-- =============================================================================
-- 7. PL/SQL
-- =============================================================================

-- AMATISTA_PURGAR · 003 · procedimiento independiente (no está en un paquete).
-- Cuerpo completo (lotes de 10 000, DROP PARTITION ... UPDATE GLOBAL INDEXES y
-- DELETE del resto): backend/sql/003_mantenimiento.sql, sección 1.
--
--   CREATE OR REPLACE PROCEDURE amatista_purgar (
--     p_dias_sesiones IN NUMBER DEFAULT 90,
--     p_dias_eventos  IN NUMBER DEFAULT 400
--   );
--
-- Job AMATISTA_PURGA_DIARIA · 003 (DBMS_SCHEDULER.CREATE_JOB):
--   job_type        => 'PLSQL_BLOCK'
--   job_action      => 'BEGIN amatista_purgar; END;'
--   start_date      => SYSTIMESTAMP AT TIME ZONE 'UTC'
--   repeat_interval => 'FREQ=DAILY;BYHOUR=9;BYMINUTE=15;BYSECOND=0'   -- 09:15 UTC
--   enabled         => TRUE

-- AMATISTA_AUTOR · 006 · especificación (copia literal).
-- Cuerpo completo (unas 460 líneas, con los subprogramas privados exigir_id,
-- lista, revisar_habilidades, paso y siguiente_orden):
-- backend/sql/006_herramientas_autor.sql, «CREATE OR REPLACE PACKAGE BODY».
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


-- =============================================================================
-- 8. USUARIO DE APLICACIÓN (004, OPCIONAL) · solo como comentario
-- =============================================================================
-- El script real exige las tablas de 002 y 005 (las de 007, 010 y 012 son
-- opcionales), da permisos sobre las 21 tablas que existan, pide la contraseña en v_password y es
-- repetible: backend/sql/004_usuario_aplicacion.sql.
--
--   CREATE USER amatista_app IDENTIFIED BY "<contraseña>";
--   GRANT CREATE SESSION TO amatista_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON <cada una de las 21 tablas> TO amatista_app;
--
-- backend/.env: DB_USER=AMATISTA_APP, DB_ESQUEMA=ADMIN.


-- =============================================================================
-- 9. LO QUE NO EXISTE EN EL ESQUEMA
-- =============================================================================
-- Sin secuencias, sin triggers, sin columnas IDENTITY, sin sinónimos, sin
-- compresión declarada y sin columnas de tipo nativo JSON (el JSON va en
-- VARCHAR2 o CLOB con CHECK (... IS JSON)). Los ids los genera la aplicación.
-- =============================================================================
