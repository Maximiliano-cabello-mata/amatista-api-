-- Esquema de Amatista para postgresql, generado desde backend/database/modelos.py
-- con: python herramientas/migrar.py ddl --dialecto postgresql
-- No editar a mano: se regenera. Guía: docs/base-de-datos/03_migracion.md

CREATE TABLE cursos (
	id VARCHAR(50) NOT NULL, 
	numero VARCHAR(5), 
	titulo VARCHAR(100) NOT NULL, 
	subtitulo VARCHAR(150), 
	descripcion VARCHAR(1000), 
	nivel VARCHAR(30), 
	acento VARCHAR(20), 
	recurso_texto VARCHAR(100), 
	recurso_url VARCHAR(300), 
	orden INTEGER NOT NULL, 
	estado VARCHAR(12) NOT NULL, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	ruta VARCHAR(30), 
	requisito_id VARCHAR(50), 
	PRIMARY KEY (id), 
	FOREIGN KEY(requisito_id) REFERENCES cursos (id), 
	CONSTRAINT ck_cursos_estado CHECK (estado IN ('borrador', 'publicado', 'archivado'))
);

CREATE TABLE usuarios (
	id VARCHAR(100) NOT NULL, 
	nombre VARCHAR(150), 
	email VARCHAR(100), 
	telefono VARCHAR(25), 
	rol VARCHAR(20) NOT NULL, 
	password_hash VARCHAR(255), 
	correo_confirmado INTEGER NOT NULL, 
	codigo_verificacion VARCHAR(64), 
	codigo_proposito VARCHAR(12), 
	codigo_expira TIMESTAMP WITHOUT TIME ZONE, 
	codigo_intentos INTEGER NOT NULL, 
	intentos_fallidos INTEGER NOT NULL, 
	bloqueado_hasta TIMESTAMP WITHOUT TIME ZONE, 
	es_prueba INTEGER NOT NULL, 
	fusionado_en VARCHAR(100), 
	creado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	ultimo_acceso TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (email), 
	CONSTRAINT ck_usuarios_rol CHECK (rol IN ('alumno', 'profesor', 'admin'))
);

CREATE TABLE versiones_blender (
	version VARCHAR(20) NOT NULL, 
	categoria VARCHAR(14) NOT NULL, 
	es_lts INTEGER NOT NULL, 
	soporte_hasta TIMESTAMP WITHOUT TIME ZONE, 
	notas VARCHAR(500), 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (version), 
	CONSTRAINT ck_versiones_blender_categoria CHECK (categoria IN ('principal', 'compatible', 'sin_verificar', 'retirada'))
);

CREATE TABLE addon_ajustes (
	usuario_id VARCHAR(100) NOT NULL, 
	datos VARCHAR(1000) NOT NULL, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (usuario_id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE TABLE addon_vinculos (
	id VARCHAR(36) NOT NULL, 
	codigo VARCHAR(9) NOT NULL, 
	secreto_hash VARCHAR(64) NOT NULL, 
	usuario_id VARCHAR(100), 
	dispositivo VARCHAR(200), 
	estado VARCHAR(10) NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	expira_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id), 
	UNIQUE (codigo), 
	CONSTRAINT ck_addon_vinculos_estado CHECK (estado IN ('pendiente', 'listo', 'canjeado'))
);

CREATE TABLE eventos_aprendizaje (
	id VARCHAR(36) NOT NULL, 
	usuario_id VARCHAR(100) NOT NULL, 
	tipo VARCHAR(30) NOT NULL, 
	curso_id VARCHAR(50), 
	leccion_id VARCHAR(50), 
	sesion_aprendizaje VARCHAR(36), 
	ocurrido_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	recibido_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	version_app VARCHAR(20), 
	es_prueba INTEGER NOT NULL, 
	datos VARCHAR(250), 
	PRIMARY KEY (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id), 
	CONSTRAINT ck_eventos_aprendizaje_tipo CHECK (tipo IN ('account_created', 'learning_session_started', 'lesson_completed', 'activity_submitted', 'sync_succeeded'))
);

CREATE INDEX ix_eventos_fecha ON eventos_aprendizaje (ocurrido_en);
CREATE INDEX ix_eventos_usuario_fecha ON eventos_aprendizaje (usuario_id, ocurrido_en);

CREATE TABLE grupos (
	id VARCHAR(40) NOT NULL, 
	profesor_id VARCHAR(100) NOT NULL, 
	nombre VARCHAR(120) NOT NULL, 
	codigo VARCHAR(12) NOT NULL, 
	archivado INTEGER NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(profesor_id) REFERENCES usuarios (id), 
	UNIQUE (codigo)
);

CREATE INDEX ix_grupos_profesor ON grupos (profesor_id);

CREATE TABLE logros (
	usuario_id VARCHAR(100) NOT NULL, 
	insignia_id VARCHAR(100) NOT NULL, 
	obtenido_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (usuario_id, insignia_id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE TABLE niveles (
	id VARCHAR(50) NOT NULL, 
	curso_id VARCHAR(50) NOT NULL, 
	numero INTEGER NOT NULL, 
	rama VARCHAR(50), 
	titulo VARCHAR(100) NOT NULL, 
	perfil VARCHAR(500), 
	proyecto VARCHAR(300), 
	criterio_salida VARCHAR(500), 
	estado VARCHAR(12) NOT NULL, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(curso_id) REFERENCES cursos (id), 
	CONSTRAINT uq_niveles_curso_numero_rama UNIQUE (curso_id, numero, rama), 
	CONSTRAINT ck_niveles_estado CHECK (estado IN ('borrador', 'publicado', 'archivado'))
);

CREATE TABLE nube_sesiones (
	id VARCHAR(40) NOT NULL, 
	usuario_id VARCHAR(100) NOT NULL, 
	nodo VARCHAR(60) NOT NULL, 
	estado VARCHAR(20) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	creada_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	expira_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	terminada_en TIMESTAMP WITHOUT TIME ZONE, 
	motivo_fin VARCHAR(40), 
	PRIMARY KEY (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE INDEX ix_nube_sesiones_estado ON nube_sesiones (estado, expira_en);
CREATE INDEX ix_nube_sesiones_usuario ON nube_sesiones (usuario_id, creada_en);

CREATE TABLE perfiles (
	usuario_id VARCHAR(100) NOT NULL, 
	avatar VARCHAR(40), 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (usuario_id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE TABLE practicas (
	id VARCHAR(80) NOT NULL, 
	curso_id VARCHAR(50), 
	leccion_id VARCHAR(50), 
	titulo VARCHAR(200) NOT NULL, 
	nivel INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	version_publicada INTEGER, 
	definicion TEXT NOT NULL, 
	estado VARCHAR(12) NOT NULL, 
	origen VARCHAR(12) NOT NULL, 
	autor_id VARCHAR(100), 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	publicado_en TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(autor_id) REFERENCES usuarios (id), 
	FOREIGN KEY(curso_id) REFERENCES cursos (id), 
	CONSTRAINT ck_practicas_estado CHECK (estado IN ('borrador', 'publicado', 'archivado')), 
	CONSTRAINT ck_practicas_origen CHECK (origen IN ('repositorio', 'addon', 'panel'))
);

CREATE INDEX ix_practicas_leccion ON practicas (curso_id, leccion_id);

CREATE TABLE progreso_lecciones (
	usuario_id VARCHAR(100) NOT NULL, 
	curso_id VARCHAR(50) NOT NULL, 
	leccion_id VARCHAR(50) NOT NULL, 
	completada INTEGER NOT NULL, 
	puntaje INTEGER, 
	intentos INTEGER NOT NULL, 
	datos_ligeros VARCHAR(250), 
	completada_en TIMESTAMP WITHOUT TIME ZONE, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (usuario_id, curso_id, leccion_id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE TABLE sesiones (
	id VARCHAR(64) NOT NULL, 
	usuario_id VARCHAR(100) NOT NULL, 
	dispositivo VARCHAR(200), 
	activa INTEGER NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE, 
	ultimo_acceso TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	expira_en TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE INDEX ix_sesiones_usuario_id ON sesiones (usuario_id);

CREATE TABLE sugerencias (
	id VARCHAR(40) NOT NULL, 
	usuario_id VARCHAR(100) NOT NULL, 
	categoria VARCHAR(20) NOT NULL, 
	pagina VARCHAR(200), 
	estado VARCHAR(20) NOT NULL, 
	sin_leer_alumno INTEGER NOT NULL, 
	sin_leer_creador INTEGER NOT NULL, 
	creada_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	actualizada_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE INDEX ix_sugerencias_estado ON sugerencias (estado, actualizada_en);
CREATE INDEX ix_sugerencias_usuario ON sugerencias (usuario_id, actualizada_en);

CREATE TABLE talleres_alumno (
	usuario_id VARCHAR(100) NOT NULL, 
	taller_id VARCHAR(60) NOT NULL, 
	reto_hecho INTEGER NOT NULL, 
	isla TEXT, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (usuario_id, taller_id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE TABLE addon_enlaces (
	sesion_id VARCHAR(64) NOT NULL, 
	usuario_id VARCHAR(100) NOT NULL, 
	visto_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	practica_id VARCHAR(80), 
	paso VARCHAR(80), 
	progreso INTEGER, 
	enfocado INTEGER NOT NULL, 
	version_addon VARCHAR(20), 
	version_blender VARCHAR(20), 
	orden VARCHAR(1000), 
	orden_en TIMESTAMP WITHOUT TIME ZONE, 
	detalle TEXT, 
	PRIMARY KEY (sesion_id), 
	FOREIGN KEY(sesion_id) REFERENCES sesiones (id) ON DELETE CASCADE, 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id)
);

CREATE INDEX ix_addon_enlaces_usuario ON addon_enlaces (usuario_id);

CREATE TABLE asignaciones (
	id VARCHAR(40) NOT NULL, 
	grupo_id VARCHAR(40) NOT NULL, 
	profesor_id VARCHAR(100) NOT NULL, 
	practica_id VARCHAR(80) NOT NULL, 
	titulo VARCHAR(200) NOT NULL, 
	indicaciones VARCHAR(1000), 
	fecha_limite TIMESTAMP WITHOUT TIME ZONE, 
	estado VARCHAR(12) NOT NULL, 
	creada_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(grupo_id) REFERENCES grupos (id), 
	FOREIGN KEY(practica_id) REFERENCES practicas (id), 
	FOREIGN KEY(profesor_id) REFERENCES usuarios (id)
);

CREATE INDEX ix_asignaciones_grupo ON asignaciones (grupo_id, creada_en);

CREATE TABLE evaluaciones_rubrica (
	usuario_id VARCHAR(100) NOT NULL, 
	nivel_id VARCHAR(50) NOT NULL, 
	criterio VARCHAR(1) NOT NULL, 
	logro VARCHAR(12) NOT NULL, 
	evidencia VARCHAR(300), 
	comentario VARCHAR(500), 
	revisado_por VARCHAR(100), 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (usuario_id, nivel_id, criterio), 
	FOREIGN KEY(nivel_id) REFERENCES niveles (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id), 
	CONSTRAINT ck_evaluaciones_rubrica_criter CHECK (criterio IN ('A', 'B', 'C', 'D', 'E')), 
	CONSTRAINT ck_evaluaciones_rubrica_logro CHECK (logro IN ('pendiente', 'con_ayuda', 'autonomo'))
);

CREATE TABLE grupo_alumnos (
	grupo_id VARCHAR(40) NOT NULL, 
	alumno_id VARCHAR(100) NOT NULL, 
	unido_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (grupo_id, alumno_id), 
	FOREIGN KEY(alumno_id) REFERENCES usuarios (id), 
	FOREIGN KEY(grupo_id) REFERENCES grupos (id)
);

CREATE INDEX ix_grupo_alumnos_alumno ON grupo_alumnos (alumno_id);

CREATE TABLE habilidades (
	id VARCHAR(50) NOT NULL, 
	curso_id VARCHAR(50) NOT NULL, 
	nivel_id VARCHAR(50), 
	nombre VARCHAR(150) NOT NULL, 
	descripcion VARCHAR(500), 
	orden INTEGER NOT NULL, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(curso_id) REFERENCES cursos (id), 
	FOREIGN KEY(nivel_id) REFERENCES niveles (id)
);

CREATE INDEX ix_habilidades_curso ON habilidades (curso_id, orden);

CREATE TABLE modulos (
	id VARCHAR(50) NOT NULL, 
	curso_id VARCHAR(50) NOT NULL, 
	nivel_id VARCHAR(50), 
	numero INTEGER NOT NULL, 
	titulo VARCHAR(200) NOT NULL, 
	descripcion VARCHAR(1000), 
	insignia VARCHAR(80), 
	minutos INTEGER, 
	estado VARCHAR(12) NOT NULL, 
	version INTEGER NOT NULL, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	publicado_en TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(curso_id) REFERENCES cursos (id), 
	FOREIGN KEY(nivel_id) REFERENCES niveles (id), 
	CONSTRAINT ck_modulos_estado CHECK (estado IN ('borrador', 'publicado', 'archivado'))
);

CREATE INDEX ix_modulos_curso ON modulos (curso_id, numero);

CREATE TABLE practica_versiones (
	practica_id VARCHAR(80) NOT NULL, 
	version INTEGER NOT NULL, 
	definicion TEXT NOT NULL, 
	huella VARCHAR(64) NOT NULL, 
	nota VARCHAR(500), 
	autor_id VARCHAR(100), 
	version_addon VARCHAR(20), 
	version_blender VARCHAR(20), 
	creado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (practica_id, version), 
	FOREIGN KEY(autor_id) REFERENCES usuarios (id), 
	FOREIGN KEY(practica_id) REFERENCES practicas (id)
);

CREATE TABLE progreso_practicas (
	usuario_id VARCHAR(100) NOT NULL, 
	practica_id VARCHAR(80) NOT NULL, 
	version INTEGER NOT NULL, 
	progreso INTEGER NOT NULL, 
	completada INTEGER NOT NULL, 
	autonomia VARCHAR(14), 
	pistas INTEGER NOT NULL, 
	correcciones INTEGER NOT NULL, 
	intentos INTEGER NOT NULL, 
	paso_actual VARCHAR(80), 
	objetivos VARCHAR(1000), 
	version_blender VARCHAR(20), 
	version_addon VARCHAR(20), 
	abierta_en TIMESTAMP WITHOUT TIME ZONE, 
	completada_en TIMESTAMP WITHOUT TIME ZONE, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (usuario_id, practica_id), 
	FOREIGN KEY(practica_id) REFERENCES practicas (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id), 
	CONSTRAINT ck_progreso_practicas_autonomi CHECK (autonomia IN ('con_guia', 'con_pistas', 'autonoma'))
);

CREATE INDEX ix_prog_practicas_practica ON progreso_practicas (practica_id);

CREATE TABLE sugerencia_mensajes (
	id VARCHAR(40) NOT NULL, 
	sugerencia_id VARCHAR(40) NOT NULL, 
	autor_id VARCHAR(100) NOT NULL, 
	de_creador INTEGER NOT NULL, 
	texto VARCHAR(2000) NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(autor_id) REFERENCES usuarios (id), 
	FOREIGN KEY(sugerencia_id) REFERENCES sugerencias (id)
);

CREATE INDEX ix_sug_mensajes_sug ON sugerencia_mensajes (sugerencia_id, creado_en);

CREATE TABLE entregas (
	asignacion_id VARCHAR(40) NOT NULL, 
	alumno_id VARCHAR(100) NOT NULL, 
	estado VARCHAR(12) NOT NULL, 
	progreso INTEGER NOT NULL, 
	completada INTEGER NOT NULL, 
	intentos INTEGER NOT NULL, 
	resumen VARCHAR(2000), 
	modelo TEXT, 
	modelo_bytes INTEGER NOT NULL, 
	entregada_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	recogido_en TIMESTAMP WITHOUT TIME ZONE, 
	calificacion INTEGER, 
	comentario VARCHAR(1000), 
	evaluada_en TIMESTAMP WITHOUT TIME ZONE, 
	vista_alumno INTEGER NOT NULL, 
	PRIMARY KEY (asignacion_id, alumno_id), 
	FOREIGN KEY(alumno_id) REFERENCES usuarios (id), 
	FOREIGN KEY(asignacion_id) REFERENCES asignaciones (id)
);

CREATE INDEX ix_entregas_alumno ON entregas (alumno_id);

CREATE TABLE habilidades_alumno (
	usuario_id VARCHAR(100) NOT NULL, 
	habilidad_id VARCHAR(50) NOT NULL, 
	estado VARCHAR(14) NOT NULL, 
	evidencia VARCHAR(300), 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	dominio FLOAT, 
	intentos INTEGER, 
	aciertos INTEGER, 
	sin_ayuda INTEGER, 
	ultimo FLOAT, 
	caja INTEGER, 
	proximo FLOAT, 
	pruebas INTEGER, 
	vio_demo INTEGER, 
	ayuda_max INTEGER, 
	PRIMARY KEY (usuario_id, habilidad_id), 
	FOREIGN KEY(habilidad_id) REFERENCES habilidades (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id), 
	CONSTRAINT ck_habilidades_alumno_estado CHECK (estado IN ('sin_practicar', 'con_guia', 'con_pistas', 'autonoma'))
);

CREATE TABLE lecciones (
	curso_id VARCHAR(50) NOT NULL, 
	id VARCHAR(50) NOT NULL, 
	modulo_id VARCHAR(50) NOT NULL, 
	orden INTEGER NOT NULL, 
	titulo VARCHAR(200) NOT NULL, 
	tipo VARCHAR(30) NOT NULL, 
	duracion_segundos INTEGER, 
	bloqueada INTEGER NOT NULL, 
	contenido TEXT NOT NULL, 
	reemplaza VARCHAR(250), 
	estado VARCHAR(12) NOT NULL, 
	version INTEGER NOT NULL, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	publicado_en TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (curso_id, id), 
	FOREIGN KEY(curso_id) REFERENCES cursos (id), 
	FOREIGN KEY(modulo_id) REFERENCES modulos (id), 
	CONSTRAINT ck_lecciones_estado CHECK (estado IN ('borrador', 'publicado', 'archivado'))
);

CREATE INDEX ix_lecciones_modulo ON lecciones (modulo_id, orden);

CREATE TABLE verificaciones_blender (
	id VARCHAR(36) NOT NULL, 
	curso_id VARCHAR(50) NOT NULL, 
	leccion_id VARCHAR(50) NOT NULL, 
	version_blender VARCHAR(20) NOT NULL, 
	sistema VARCHAR(60) NOT NULL, 
	version_leccion INTEGER NOT NULL, 
	version_addon VARCHAR(20), 
	resultado VARCHAR(16) NOT NULL, 
	diferencias VARCHAR(1000), 
	evidencia VARCHAR(300), 
	responsable VARCHAR(100), 
	verificado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(curso_id, leccion_id) REFERENCES lecciones (curso_id, id), 
	FOREIGN KEY(version_blender) REFERENCES versiones_blender (version), 
	CONSTRAINT ck_verificaciones_blender_resu CHECK (resultado IN ('verificada', 'con_diferencias', 'falla'))
);

CREATE INDEX ix_verificaciones_leccion ON verificaciones_blender (curso_id, leccion_id);
