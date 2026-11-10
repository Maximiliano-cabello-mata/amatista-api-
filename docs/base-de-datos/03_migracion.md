# Cómo migrar Amatista a otra base de datos

Plan para el día en que Amatista deje Oracle Autonomous Database (por costo, por límites del Free Tier o por cambiar de nube), con la herramienta que ya existe para hacerlo. Para quien decide la infraestructura y para quien ejecuta la migración.

Actualizado: 4 de octubre de 2026

## Índice

1. [Cuándo conviene migrar](#1-cuándo-conviene-migrar)
2. [A qué base: recomendación](#2-a-qué-base-recomendación)
3. [La herramienta: herramientas/migrar.py](#3-la-herramienta-herramientasmigrarpy)
4. [Equivalencias Oracle → PostgreSQL](#4-equivalencias-oracle--postgresql)
5. [Paso a paso de la migración](#5-paso-a-paso-de-la-migración)
6. [Volver atrás](#6-volver-atrás)
7. [Lo que falta para el día de la migración](#7-lo-que-falta-para-el-día-de-la-migración)

---

## 1. Cuándo conviene migrar

Hoy **no** hace falta. Oracle Free Tier da 20 GB, y Amatista usa ~32 KB permanentes por alumno más los eventos con retención (cálculo en el encabezado de `sql/007`). Señales de que llegó el momento:

- `V_AMATISTA_ESPACIO` pasa del 70 % aun con la retención de eventos en 180 días.
- La ACL de Oracle (solo la IP de la VM) estorba para desplegar la API en otro lado (por ejemplo, Fly.io: ver [despliegue con dominio](../despliegue/2026-10-04_dominio_amatista-3d.md), apéndice).
- Se necesitan más sesiones simultáneas de las que permite el Free Tier, o réplicas en otra región.

## 2. A qué base: recomendación

**PostgreSQL** (16 o más), administrado: Neon, Supabase, Fly Postgres o el de la nube que se use.

| Opción | Ventaja | En contra |
|---|---|---|
| **PostgreSQL** (recomendada) | SQLAlchemy y el backend ya funcionan igual. `jsonb` nativo, particiones declarativas y `pg_cron`. Hay planes gratis y en todas las nubes. | Hay que portar la purga y las vistas (§4). |
| MySQL / MariaDB | Muy común y barata. | Los CHECK se respetan solo desde MySQL 8.0.16, y su JSON es más pobre. |
| SQLite (Litestream) | Cero servidores; sirve para una sola VM. | Un solo escritor: no escala a varias instancias de la API. |
| Quedarse en Oracle de pago | Cero migración. | El costo. |

## 3. La herramienta: herramientas/migrar.py

Hace la migración sin depender de las herramientas de Oracle (Data Pump) ni de las del destino. Usa SQLAlchemy y `database/modelos.py`, así que lee y escribe en cualquier base que SQLAlchemy soporte.

```bash
cd backend && source venv/bin/activate

# 1) Esquema para el destino (CREATE TABLE, CHECK de valores permitidos, índices)
python herramientas/migrar.py ddl --dialecto postgresql --salida esquema_postgresql.sql

# 2) Respaldo del origen (base de backend/.env). Solo lee.
python herramientas/migrar.py exportar respaldo/

# 3) Carga en el destino vacío (crea las tablas si se pide) y compara conteos
python herramientas/migrar.py importar respaldo/ --url "postgresql+psycopg://usuario:clave@host/amatista" --crear-tablas

# 4) Otra vez la comparación, cuando quieras
python herramientas/migrar.py verificar respaldo/ --url "postgresql+psycopg://usuario:clave@host/amatista"
```

Formato del respaldo (`amatista.export/1`):

- `manifiesto.json`: formato, fecha, motor de origen, orden de carga (respeta las llaves foráneas), filas y huella SHA-256 de cada archivo.
- `<tabla>.jsonl`: una fila por línea, como objeto JSON; fechas en ISO 8601 (UTC, sin zona, como las guarda la aplicación).

Garantías (cubiertas por `backend/tests/test_migrar.py`):

- `exportar` nunca escribe en el origen.
- `importar` se niega si alguna tabla del destino ya tiene filas: nunca mezcla ni sobrescribe. Carga todo en una transacción (todo o nada).
- Si un `.jsonl` cambió después de exportar, la huella no coincide e `importar` se detiene.
- Las filas que se apuntan entre sí (`CURSOS.REQUISITO_ID`) se cargan en el orden correcto.
- Al final cuenta las filas de cada tabla y las compara con el manifiesto.

El esquema de PostgreSQL ya generado está en [`esquema_postgresql.sql`](esquema_postgresql.sql) (se regenera con el paso 1; una prueba falla si queda atrasado).

## 4. Equivalencias Oracle → PostgreSQL

| Oracle (hoy) | PostgreSQL | Cómo se resuelve |
|---|---|---|
| `VARCHAR2(n)` / `VARCHAR2(n CHAR)` | `varchar(n)` (siempre cuenta caracteres) | DDL generado |
| `NUMBER(p)` | `integer` | DDL generado |
| `TIMESTAMP` (UTC sin zona) | `timestamp without time zone` | DDL generado; la app ya guarda UTC |
| `CLOB` con `IS JSON` | `text` (opcional: `jsonb`) | DDL generado como `text`, porque `TextoJSON` siempre entrega texto. Cambiar a `jsonb` es una mejora posterior |
| CHECK de valores (`ck_*_estado`…) | Mismos CHECK | Salen de las constantes de `modelos.py` |
| `EVENTOS_APRENDIZAJE` particionada por mes | Tabla normal; con más de ~10 M de filas, `PARTITION BY RANGE (ocurrido_en)` | Manual, el día que haga falta |
| Job `AMATISTA_PURGA_DIARIA` + `AMATISTA_PURGAR` (003) | `pg_cron`, o un timer de systemd que corra los dos `DELETE` | Las consultas están en `sql/003` (sesiones vencidas y eventos de más de 400 días) |
| Vistas `V_AMATISTA_*` (003, 006, 007) | `CREATE VIEW` con el mismo SELECT | Casi todas son SQL estándar. `V_AMATISTA_ESPACIO` usa `USER_SEGMENTS`, que en PostgreSQL es `pg_total_relation_size` |
| Paquete `AMATISTA_AUTOR` (006) | No se porta | `herramientas/contenido.py` y el panel de admin hacen lo mismo |
| Usuario `AMATISTA_APP` (004) | `CREATE ROLE amatista_app` con `GRANT SELECT, INSERT, UPDATE, DELETE` | Manual |
| `ALTER SESSION SET CURRENT_SCHEMA` (`conexion.py`) | No hace falta | Solo corre con Oracle; con `DATABASE_URL` no se usa |

Código del backend: **no cambia**. `database/conexion.py` ya acepta `DATABASE_URL` para cualquier motor. Solo hay que agregar el controlador a `requirements.txt` (`psycopg[binary]` para PostgreSQL).

## 5. Paso a paso de la migración

Con una ventana de mantenimiento corta (la PWA guarda el progreso offline y lo sincroniza después, así que los alumnos no pierden nada):

1. **Ensayo** (días antes): crea la base nueva, corre `exportar` sobre producción e `importar` en la base nueva. Arranca un backend de prueba con `DATABASE_URL` apuntando a ella y corre `diagnostico` y las pruebas de humo. Repite hasta que salga limpio.
2. **Congela escrituras**: `sudo systemctl stop amatista-backend`. La PWA sigue funcionando offline y encola el progreso.
3. **Respaldo final**: `python herramientas/migrar.py exportar respaldo-final/` (con el `.env` de Oracle).
4. **Base nueva vacía**: bórrala y vuelve a crearla, o usa una nueva. `importar` exige tablas vacías.
5. **Carga**: `python herramientas/migrar.py importar respaldo-final/ --url ... --crear-tablas`. Debe terminar en «Conteos verificados».
6. **Cambia la conexión**: en `backend/.env` pon `DATABASE_URL=postgresql+psycopg://...` y comenta `DB_USER`, `DB_PASSWORD` y `DB_DSN`.
7. **Arranca**: `sudo systemctl start amatista-backend`. Revisa `/api/salud` (dice `"motor": "postgresql"`) e inicia sesión con una cuenta real.
8. **Purga**: programa la purga diaria (§4).
9. **Conserva Oracle** sin tocar al menos 30 días.

## 6. Volver atrás

Mientras Oracle siga intacto, volver es devolver el `.env` (paso 6 al revés) y reiniciar. Lo escrito en la base nueva después de migrar se recupera igual: `exportar` desde PostgreSQL. Si hubo poca actividad, es más simple dejar que las PWA vuelvan a sincronizar.

## 7. Lo que falta para el día de la migración

Queda como tarea en el tablero (no hace falta hoy):

- Ensayo real contra un PostgreSQL (las pruebas automáticas usan SQLite → SQLite).
- Portar la purga y las vistas (§4) a un script `sql/postgresql/`.
- Decidir si las columnas JSON pasan a `jsonb`.
