# Base de datos de Amatista

Qué es la base de datos de Amatista, qué scripts la crean y en qué estado está en producción. Para quien mantiene el backend, aplica scripts en Oracle o necesita saber dónde vive cada dato.

Actualizado: 10 de octubre de 2026 (script 012 de Amatista Taller, motor 5)

## Índice

1. [Qué hay en esta carpeta](#1-qué-hay-en-esta-carpeta)
2. [Resumen](#2-resumen)
3. [Orden de los scripts](#3-orden-de-los-scripts)
4. [Qué crea cada script](#4-qué-crea-cada-script)
5. [Estado en producción](#5-estado-en-producción)
6. [Cómo se mantiene alineado el esquema](#6-cómo-se-mantiene-alineado-el-esquema)
7. [Inconsistencias encontradas en los scripts](#7-inconsistencias-encontradas-en-los-scripts)
8. [Documentación relacionada](#8-documentación-relacionada)

---

## 1. Qué hay en esta carpeta

| Archivo | Qué contiene |
|---|---|
| [`README.md`](README.md) | Este resumen: motor, scripts, orden y estado en producción. |
| [`esquema.md`](esquema.md) | El esquema completo: diagrama entidad-relación, cada tabla con sus columnas, llaves, índices y CHECK, el modelo SQLAlchemy que la mapea, vistas, paquete `AMATISTA_AUTOR`, job de purga, usuario de aplicación y columnas JSON. |
| [`esquema_completo.sql`](esquema_completo.sql) | **Solo referencia, no se ejecuta.** El estado final del esquema tras 001→011 escrito como `CREATE TABLE` consolidados (con los `ALTER` posteriores ya aplicados), más las vistas y la especificación del paquete. |
| [`01_rediseno_v3.md`](01_rediseno_v3.md) | **Motor v3**: cursos por ruta (008), archivo de la v2 (009), revisión de índices y reglas de portabilidad. |
| [`02_manual_008_009.md`](02_manual_008_009.md) | Paso a paso para aplicar 007, 008 y 009 en producción después del piloto. |
| [`03_migracion.md`](03_migracion.md) | Cómo migrar a otra base (PostgreSQL recomendado) con `herramientas/migrar.py`. |
| [`esquema_postgresql.sql`](esquema_postgresql.sql) | El esquema para PostgreSQL, generado desde `modelos.py` (no se edita a mano). |

La fuente para instalar o actualizar la base **no** es esta carpeta: son los scripts numerados de [`backend/sql/`](../../backend/sql/) en el orden de [`backend/sql/LEEME.txt`](../../backend/sql/LEEME.txt).

## 2. Resumen

| Aspecto | Valor |
|---|---|
| Motor en producción | Oracle Autonomous Database (ATP, Free Tier de 20 GB). Los scripts lo llaman «ATP 26ai» (`backend/sql/LEEME.txt:2`). La bitácora del servidor del 3 de octubre registra la versión 23.26 en producción, y 007 se probó en un contenedor Oracle 23ai. |
| Esquema dueño de las tablas | `ADMIN`. Todos los scripts se ejecutan como ADMIN en Database Actions > SQL con «Ejecutar script» (F5). |
| Usuario con el que se conecta el backend | Hoy `ADMIN` (T-004 pendiente). Opcional: `AMATISTA_APP` (script 004) con `DB_ESQUEMA=ADMIN` en `backend/.env`; [`backend/database/conexion.py`](../../backend/database/conexion.py) hace `ALTER SESSION SET CURRENT_SCHEMA` en cada conexión. |
| Desarrollo y pruebas | SQLite. Con `DATABASE_URL=sqlite:///./amatista_local.db` el backend crea las tablas solo con `Base.metadata.create_all` al arrancar ([`backend/main.py`](../../backend/main.py), también `herramientas/crear_admin.py` y `herramientas/contenido.py`). Las pruebas (`backend/tests/conftest.py`) usan una SQLite temporal por prueba. |
| Fuente de verdad de tablas y columnas | [`backend/database/modelos.py`](../../backend/database/modelos.py) (lo dice `LEEME.txt:3`). En Oracle las tablas **no** las crea SQLAlchemy: las crean los scripts. |
| Tablas | 21 al aplicar 001→012 (20 con 010 y 011, 18 con 007; 14 hoy en producción). 011 no crea tablas: agrega una columna; 012 agrega 10 columnas a `HABILIDADES_ALUMNO` y la tabla `TALLERES_ALUMNO`. |
| Vistas | 6: `V_AMATISTA_ESPACIO` (003), `V_AMATISTA_MAPA`, `V_AMATISTA_FICHAS_INCOMPLETAS`, `V_AMATISTA_COMPATIBILIDAD` (006), `V_AMATISTA_PRACTICAS` (007). |
| Código PL/SQL | Procedimiento `AMATISTA_PURGAR` y job `AMATISTA_PURGA_DIARIA` (003); paquete `AMATISTA_AUTOR` (006). |
| Secuencias y triggers | Ninguno. Todos los ids los genera la aplicación (UUID o ids de texto legibles). |
| Particionado | Solo `EVENTOS_APRENDIZAJE`: por rango mensual con `INTERVAL` sobre `OCURRIDO_EN`. |
| Tablas organizadas por índice (IOT) | `LOGROS` y `HABILIDADES_ALUMNO`. |
| Compresión | Ningún script declara compresión. |

## 3. Orden de los scripts

Tomado de [`backend/sql/LEEME.txt`](../../backend/sql/LEEME.txt), sección 2:

```text
Base NUEVA (sin tablas, o prototipo que se puede borrar):
    001 -> 002 -> 003 -> 005 -> 006 -> 007 -> 008 -> 010 -> 011 -> 012 -> 013 -> 014 -> 004 (opcional)
    (009 solo si hay datos de la v2 que archivar)

Base EXISTENTE CON DATOS (alumnos reales):
    002 -> 003 -> 005 -> 006 -> 007 -> 008 -> 010 -> 011 -> 012 -> 013 -> 014 -> 004 (opcional) -> 009
    NUNCA 001: borra usuarios, sesiones y progreso.
```

- **001 es destructivo**: borra `PROGRESO_LECCIONES`, `SESIONES_WEB` (tabla de un diseño antiguo), `SESIONES`, `USUARIOS`, `LOGROS`, `EVENTOS_APRENDIZAJE`, `LECCIONES`, `MODULOS` y `CURSOS` con `CASCADE CONSTRAINTS PURGE` y crea limpias las tres tablas base.
- **002 a 014 son idempotentes**: antes de cada cambio consultan `USER_TABLES`, `USER_TAB_COLUMNS`, `USER_CONSTRAINTS` o `USER_INDEXES`, o usan `CREATE OR REPLACE`. Se pueden repetir.
- **004 requiere 002 y 005.** Da permisos sobre las 21 tablas; las de 007, 010 y 012 son opcionales (`v_opcional`): si ya existen reciben permisos y, si no, 007, 010 y 012 los dan al ejecutarse. Por eso 004 se puede ejecutar hoy en producción. Va al final del orden para dar todos los permisos de una vez.
- **009 va después de importar** los cursos nuevos (`python herramientas/contenido.py importar`): si se archiva antes, el catálogo se queda sin Blender.
- Cada script avisa con `RAISE_APPLICATION_ERROR(-20001 …)` si falta un script anterior.

## 4. Qué crea cada script

| Script | Tipo | Crea o modifica | Tablas al terminar |
|---|---|---|---|
| [`001_esquema_amatista.sql`](../../backend/sql/001_esquema_amatista.sql) | Destructivo | Borra las tablas de la app. Crea `USUARIOS` (3 columnas), `SESIONES` (5), `PROGRESO_LECCIONES` (7) y el índice `IX_SESIONES_USUARIO`. | 3 |
| [`002_autenticacion_contenido_eventos.sql`](../../backend/sql/002_autenticacion_contenido_eventos.sql) | Incremental | `USUARIOS` +14 columnas, `UQ_USUARIOS_EMAIL` y 3 CHECK. `SESIONES.ID` se amplía a `VARCHAR2(64)`, +`CREADO_EN`, `EXPIRA_EN`; cierra las sesiones antiguas (`activa = 0` si `LENGTH(id) <> 64`). `PROGRESO_LECCIONES` +`DATOS_LIGEROS` (IS JSON), `COMPLETADA_EN` (rellena con `actualizado_en` las completadas). Tablas nuevas: `LOGROS` (IOT), `EVENTOS_APRENDIZAJE` (particionada por mes, con 2 índices LOCAL), `CURSOS`, `MODULOS`, `LECCIONES` (+ `IX_MODULOS_CURSO`, `IX_LECCIONES_MODULO`). | 8 |
| [`003_mantenimiento.sql`](../../backend/sql/003_mantenimiento.sql) | Idempotente | Procedimiento `AMATISTA_PURGAR(p_dias_sesiones DEFAULT 90, p_dias_eventos DEFAULT 400)`, job `AMATISTA_PURGA_DIARIA` (todos los días a las 09:15 UTC), vista `V_AMATISTA_ESPACIO`, consultas de espacio y de filas. No crea tablas. | 8 |
| [`004_usuario_aplicacion.sql`](../../backend/sql/004_usuario_aplicacion.sql) | Opcional (T-004) | Usuario `AMATISTA_APP` con `CREATE SESSION` y `SELECT, INSERT, UPDATE, DELETE` sobre las 21 tablas. Exige las de 002 y 005; las de 007, 010 y 012 reciben permisos si ya existen. | — |
| [`005_niveles_habilidades_versiones.sql`](../../backend/sql/005_niveles_habilidades_versiones.sql) | Incremental (v3.0.0) | Tablas `NIVELES`, `HABILIDADES`, `HABILIDADES_ALUMNO` (IOT), `EVALUACIONES_RUBRICA`, `VERSIONES_BLENDER`, `VERIFICACIONES_BLENDER`; columna `MODULOS.NIVEL_ID` con `FK_MODULOS_NIVEL`; índices `IX_HABILIDADES_CURSO`, `IX_VERIFICACIONES_LECCION`. Si existe `AMATISTA_APP`, le da permisos. | 14 |
| [`006_herramientas_autor.sql`](../../backend/sql/006_herramientas_autor.sql) | Idempotente | Vistas `V_AMATISTA_MAPA`, `V_AMATISTA_FICHAS_INCOMPLETAS`, `V_AMATISTA_COMPATIBILIDAD` y el paquete `AMATISTA_AUTOR` (función `ahora` y 9 procedimientos). No crea tablas. | 14 |
| [`007_motor_practicas.sql`](../../backend/sql/007_motor_practicas.sql) | Incremental | Tablas `ADDON_VINCULOS`, `PRACTICAS`, `PRACTICA_VERSIONES`, `PROGRESO_PRACTICAS`; índices `IX_PRACTICAS_LECCION`, `IX_PROG_PRACTICAS_PRACTICA`; vista `V_AMATISTA_PRACTICAS`; inserta (con `MERGE` que solo inserta) 4 habilidades `bl-*` de la práctica de la mesa si existe el nivel `blender-n1`. Si existe `AMATISTA_APP`, le da permisos. | 18 |
| [`008_cursos_por_ruta.sql`](../../backend/sql/008_cursos_por_ruta.sql) | Incremental (motor v3) | Columnas `CURSOS.RUTA` y `CURSOS.REQUISITO_ID` (CURSOS pasa de 12 a 14 columnas), `FK_CURSOS_REQUISITO` y `CK_CURSOS_REQUISITO`; llena `RUTA` donde está vacía. | 18 |
| [`009_archivar_blender_v2.sql`](../../backend/sql/009_archivar_blender_v2.sql) | Idempotente | Archiva (no borra) el curso `blender` de la v2, sus niveles y módulos y las prácticas `blender.n1.*`. No crea tablas. | 18 |
| [`010_enlace_blender.sql`](../../backend/sql/010_enlace_blender.sql) | Incremental (motor 3.4) | Tablas `ADDON_ENLACES` (el latido de cada Blender conectado y su orden pendiente) y `ADDON_AJUSTES` (cómo se ve Blender para cada alumno); índice `IX_ADDON_ENLACES_USUARIO`. Si existe `AMATISTA_APP`, le da permisos. Sin 010 el backend sigue funcionando, sin enlace en vivo. | 20 |
| [`011_detalle_instructor.sql`](../../backend/sql/011_detalle_instructor.sql) | Incremental (motor 3.5.1) | Columna `ADDON_ENLACES.DETALLE` (`CLOB`, anulable) con `CK_ENLACES_DETALLE` (`IS JSON`): lo que muestra el instructor del add-on, para que «Ahora en Blender» lo vea aunque el backend corra en varios procesos. Exige 010. ADDON_ENLACES pasa de 11 a 12 columnas. Los permisos que ya tiene `AMATISTA_APP` cubren la columna nueva. | 20 |
| [`012_taller.sql`](../../backend/sql/012_taller.sql) | Incremental (motor 5) | Amatista Taller: 10 columnas anulables en `HABILIDADES_ALUMNO` (el modelo del alumno: dominio, intentos, aciertos, repaso…) con 3 CHECK y la tabla `TALLERES_ALUMNO` (reto final y pieza de la isla de cada taller). Si existe `AMATISTA_APP`, le da permisos. Sin 012 el backend sigue funcionando: el taller se guarda en Blender y se envía después. | 21 |
| [`013_avatares_y_buzon.sql`](../../backend/sql/013_avatares_y_buzon.sql) | Incremental (avatares y buzón) | `PERFILES` (avatar low poly de cada cuenta), `SUGERENCIAS` y `SUGERENCIA_MENSAJES` (el buzón del creador). Si existe `AMATISTA_APP`, le da permisos. Sin 013 la plataforma funciona: el avatar se queda en el dispositivo y el buzón responde 503. | 24 |
| [`014_panel_del_profesor.sql`](../../backend/sql/014_panel_del_profesor.sql) | Incremental (panel del profesor evaluador) | `GRUPOS`, `GRUPO_ALUMNOS`, `ASIGNACIONES` y `ENTREGAS`. El modelo de una entrega solo pasa por la base: se borra cuando el profesor lo recoge o a los 30 días. Si existe `AMATISTA_APP`, le da permisos. Sin 014 el panel del profesor y «Mi grupo» responden 503. | 28 |

El detalle de cada tabla, columna y restricción está en [`esquema.md`](esquema.md).

## 5. Estado en producción

Según [`backend/sql/LEEME.txt`](../../backend/sql/LEEME.txt) (líneas 60-64) y la bitácora [`docs/bitacora/2026-10-03_bitacora_tecnica_v3_servidor.md`](../bitacora/2026-10-03_bitacora_tecnica_v3_servidor.md) (secciones 14 a 22), al 3 de octubre de 2026:

| Script | Estado en producción |
|---|---|
| 001 | Aplicado en su momento (instalación inicial). No se vuelve a ejecutar. |
| 002 | Aplicado. |
| 003 | Aplicado (job de purga activo). |
| 004 | **No aplicado.** El backend sigue conectándose como `ADMIN` (T-004). |
| 005 | Aplicado. |
| 006 | Aplicado. |
| 007 | **Pendiente.** Se ejecuta después del piloto del 8 de octubre y antes de desplegar el backend del motor de prácticas (T-055 en [`tablero/tareas.yml`](../../tablero/tareas.yml)). Sin 007 las rutas `/api/addon/v1/...` responden 503; el resto funciona igual. |
| 008 | **Pendiente.** Después del piloto, con la importación de los cursos nuevos (T-064). |
| 009 | **Pendiente.** Después de importar los cursos nuevos (T-064). |
| 010 | **Pendiente.** Después del piloto (T-087). Sin 010 la API responde «sin enlace en vivo» y lo demás funciona igual. |
| 011 | **Pendiente.** Va después de 010 y antes del backend 3.5.1 ([correcciones 3.5.1](../despliegue/2026-10-09_correcciones_3_5_1.md)). |
| 012 | **Pendiente.** Va después de 011 y antes del backend del motor 5 ([Amatista Taller](../despliegue/2026-10-10_amatista_taller_motor_5.md)). |
| 013 | **Pendiente** (T-149). Va después de 012; con `AMATISTA_CREADOR` en `backend/.env` ([avatares y buzón](../plataforma/16_avatares_y_buzon.md)). |
| 014 | **Pendiente** (T-150). Va después de 013 ([panel del profesor](../plataforma/17_panel_del_profesor.md)). |

**Verificación de los números contra el SQL** (hecha leyendo los `CREATE TABLE` de cada script):

| Después de | Tablas nuevas | Total | ¿Cuadra? |
|---|---|---|---|
| 001 | `USUARIOS`, `SESIONES`, `PROGRESO_LECCIONES` | 3 | — |
| 002 | `LOGROS`, `EVENTOS_APRENDIZAJE`, `CURSOS`, `MODULOS`, `LECCIONES` | 8 | Sí: la verificación final de 002 lista 8 tablas. |
| 005 | `NIVELES`, `HABILIDADES`, `HABILIDADES_ALUMNO`, `EVALUACIONES_RUBRICA`, `VERSIONES_BLENDER`, `VERIFICACIONES_BLENDER` | **14** | Sí: coincide con «14 tablas» de `LEEME.txt` y de la bitácora, y con la consulta de verificación de 005. |
| 007 | `ADDON_VINCULOS`, `PRACTICAS`, `PRACTICA_VERSIONES`, `PROGRESO_PRACTICAS` | **18** | Sí: coincide con la consulta de verificación de 007, la lista de 004 y `ESPERADO` en `backend/diagnostico_oracle.py` (18 tablas). |
| 008 | Ninguna (`CURSOS` +2 columnas) | 18 | Sí: la verificación de 008 espera 14 columnas en `CURSOS`. |
| 010 | `ADDON_ENLACES`, `ADDON_AJUSTES` | **20** | Sí: coincide con la verificación de 010, la lista de 004 y los 20 modelos de `modelos.py`. |
| 011 | Ninguna (`ADDON_ENLACES` +1 columna) | 20 | Sí: la verificación de 011 espera 12 columnas en `ADDON_ENLACES`. |
| 012 | `TALLERES_ALUMNO` (y `HABILIDADES_ALUMNO` +10 columnas) | **21** | Sí: la verificación de 012 espera 21 tablas y 15 columnas en `HABILIDADES_ALUMNO`. |
| 013 | `PERFILES`, `SUGERENCIAS`, `SUGERENCIA_MENSAJES` | **24** | Sí: la verificación de 013 espera 24 tablas. |
| 014 | `GRUPOS`, `GRUPO_ALUMNOS`, `ASIGNACIONES`, `ENTREGAS` | **28** | Sí: la verificación de 014 espera 28 tablas. |

003, 006, 008, 009 y 011 no crean tablas, por eso no cambian la cuenta. Los conteos de columnas esperados por las consultas de verificación (USUARIOS 17, SESIONES 7, PROGRESO_LECCIONES 9, LOGROS 3, EVENTOS_APRENDIZAJE 11, CURSOS 12 con 007 y 14 con 008, MODULOS 12, LECCIONES 14, NIVELES 10, HABILIDADES 7, HABILIDADES_ALUMNO 5 y 15 con 012, EVALUACIONES_RUBRICA 8, VERSIONES_BLENDER 6, VERIFICACIONES_BLENDER 12, ADDON_VINCULOS 8, PRACTICAS 13, PRACTICA_VERSIONES 9, PROGRESO_PRACTICAS 16, ADDON_ENLACES 11 con 010 y 12 con 011, ADDON_AJUSTES 3, TALLERES_ALUMNO 5) también cuadran con las columnas de los `CREATE TABLE` y `ALTER TABLE ... ADD`.

Consecuencia práctica: **004 se puede ejecutar hoy en producción**. Solo exige las tablas de 002 y 005; las de 007 y 010 reciben permisos cuando existan (o al volver a ejecutar 004).

## 6. Cómo se mantiene alineado el esquema

La regla 6 de `LEEME.txt` pide cambiar tres lugares en el mismo commit:

1. [`backend/database/modelos.py`](../../backend/database/modelos.py) — los modelos SQLAlchemy (fuente de verdad).
2. Un script nuevo numerado en `backend/sql/` (nunca se edita uno ya ejecutado en producción).
3. `ESPERADO` en [`backend/diagnostico_oracle.py`](../../backend/diagnostico_oracle.py).

[`backend/tests/test_esquema.py`](../../backend/tests/test_esquema.py) lee los scripts como texto, reconstruye el estado final de 001→012 y falla si modelos, scripts y diagnóstico no coinciden (tablas, columnas, tipos, largos, nulos, valores de los CHECK, columnas IS JSON e índices). Para comprobar la base real se usa `python diagnostico_oracle.py` en el servidor (solo lee; la ACL de Oracle solo acepta la IP del servidor).

## 7. Inconsistencias encontradas en los scripts

No se corrigen aquí (los scripts ejecutados no se editan); se anotan para quien los lea:

- **Encabezado de 001** (`001_esquema_amatista.sql:11-13`): da el orden «001 → 002 → 003 → 004», sin 005-007. El orden vigente es el de `LEEME.txt`.
- **Encabezado de 005** (`005_niveles_habilidades_versiones.sql:24`): «001 → 002 → 003 → (004) → 005 → 006». Hoy 004 exige las tablas de 002 y 005 y va al final para dar de una vez los permisos de las tablas de 007 y 010.
- **Encabezado de 007** (`007_motor_practicas.sql:31`): remite a `02_manual_oracle.md`, «sección 7», pero la guía de 007 es la **sección 10** de ese manual (la 7 es «Espacio»). `LEEME.txt` sí dice sección 10.
- **Encabezados de 008, 009 y 010**: ponen 009 antes de 010 («… → 007 → 008 → 009 → 010»). El orden vigente de `LEEME.txt` ejecuta 010 antes de 004 y deja 009 al final, después de importar los cursos nuevos.
- **`LEEME.txt`, sección 6, regla 3**: dice copiar los procedimientos «de 002»; 005 y 007 los repiten (007 solo una parte: no tiene `agregar_columna` ni `agregar_restriccion`).

## 8. Documentación relacionada

- [`backend/sql/LEEME.txt`](../../backend/sql/LEEME.txt) — orden, ejecución, verificación y reglas para scripts nuevos.
- [`docs/despliegue/2026-10-02_oracle_paso_a_paso.md`](../despliegue/2026-10-02_oracle_paso_a_paso.md) — guía paso a paso de la base real.
- [`docs/reestructuracion/02_manual_oracle.md`](../reestructuracion/02_manual_oracle.md) — aplicar 005, 006 y 007, usar el paquete `AMATISTA_AUTOR` (sección 5) y el motor de prácticas (sección 10).
- [`docs/bitacora/2026-10-03_bitacora_tecnica_v3_servidor.md`](../bitacora/2026-10-03_bitacora_tecnica_v3_servidor.md) — registro de la aplicación de 005 y 006 en producción.
- [`backend/README.md`](../../backend/README.md) — cómo correr el backend con SQLite u Oracle.
- [`docs/motor/`](../motor/README.md) — el motor de prácticas que usan las tablas de 007.
