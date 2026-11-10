# Actualizar Oracle y el servidor después de fusionar Hack4Edu (4 de noviembre de 2026)

Cómo dejar la base de Oracle y la API de producción al día con `main`, después de fusionar la PR de Hack4Edu: película, Geoda en la PWA, biblioteca de recursos, auditorías y app de escritorio.

## 0. Qué cambia en la base con esta fusión: nada

Revisado con `git diff --stat hack4edu..main -- backend frontend/src/data/modulos practices`:

| | ¿Cambia? | Qué hacer |
|---|---|---|
| Esquema (`backend/sql/`, `backend/database/modelos.py`) | **No.** No hay script nuevo; el siguiente libre sigue siendo `014` | Solo comprobar que producción tenga **todos** los anteriores (pasos 2 y 3) |
| Contenido (módulos y prácticas) | **No** | `importar` es opcional; si se corre, dice «sin cambios» |
| Dependencias de la API | Sí: una línea en `backend/requirements.txt` (portabilidad) | La instala `actualizar.sh` |
| Geoda y la biblioteca | No tocan Oracle: viven en la PWA (`localStorage` y caché del navegador) | Nada |

Lo único obligatorio es **confirmar que Oracle tiene los scripts 002 a 013** y actualizar el código del servidor. Según `backend/sql/LEEME.txt`, producción podría no tener todavía alguno de los scripts 007 a 013. El diagnóstico del paso 2 lo confirma.

## 1. Respaldo (en el servidor, 2 minutos)

Las bases de datos solo aceptan la IP del servidor, así que todo se hace ahí, por SSH, como `opc`:

```bash
cd ~/amatista/backend
source venv/bin/activate
python herramientas/migrar.py exportar ~/respaldos/2026-11-04/
```

Solo lee la base y deja un JSONL por tabla. Si algo sale mal, ese es el punto de regreso.

## 2. Diagnóstico: ¿qué script falta?

```bash
python diagnostico_oracle.py
```

- **«✓ Las tablas coinciden con lo que espera el backend»:** salta al paso 4.
- **Si faltan tablas o columnas:** el diagnóstico las nombra. Usa esta tabla para saber qué script las crea:

| Si falta… | Script |
|---|---|
| `ADDON_VINCULOS`, `PRACTICAS`, `PRACTICA_VERSIONES`, `PROGRESO_PRACTICAS` | `007_motor_practicas.sql` |
| `CURSOS.RUTA`, `CURSOS.REQUISITO_ID` | `008_cursos_por_ruta.sql` (y después `009`, ver paso 3) |
| `ADDON_ENLACES`, `ADDON_AJUSTES` | `010_enlace_blender.sql` |
| `ADDON_ENLACES.DETALLE` | `011_detalle_instructor.sql` |
| Columnas `DOMINIO`, `CAJA`, `PROXIMO`… en `HABILIDADES_ALUMNO`, o la tabla `TALLERES_ALUMNO` | `012_taller.sql` |
| `PERFILES`, `SUGERENCIAS`, `SUGERENCIA_MENSAJES` | `013_avatares_y_buzon.sql` |

## 3. Ejecutar los scripts que falten (Database Actions)

1. OCI → **Autonomous Database** → tu base → **Database Actions** → **SQL**, con el usuario **ADMIN**.
2. Abre el script en GitHub (`backend/sql/0XX_….sql`), copia **todo** el archivo y pégalo en la hoja.
3. Pulsa **«Ejecutar script» (F5)**. No uses «Ejecutar sentencia» (Ctrl+Enter): los bloques PL/SQL terminan en `/` y solo el modo script los entiende.
4. En **«Salida de script»** cada cambio aparece como `OK: …`. Un `ORA-20001` a `ORA-20003` es un aviso del propio script que dice qué ejecutar antes.
5. Sigue con el siguiente que falte, **siempre en este orden**:

```
007 → 008 → 010 → 011 → 012 → 013 → 009
```

**Nunca ejecutes `001`:** borra usuarios y progreso. Los scripts 002 a 013 son idempotentes: si uno ya estaba aplicado, solo dice `OK` sin cambiar nada, así que equivocarse de orden no rompe datos.

`009` archiva el curso de Blender de la v2. Va **después** de importar los cursos nuevos (paso 4) y solo si la base todavía tiene el curso `blender` de la v2. Guía: `docs/base-de-datos/02_manual_008_009.md`.

Al terminar, vuelve a correr `python diagnostico_oracle.py` hasta ver ✓.

## 4. Actualizar el código del servidor

```bash
bash ~/amatista/despliegue/actualizar.sh
```

El script hace todo esto en orden:
1. trae `main`;
2. instala dependencias;
3. corre `pytest`;
4. **vuelve a correr el diagnóstico** y se detiene sin reiniciar si a Oracle le falta algo;
5. reinicia `amatista-backend`;
6. prueba `/api/salud`.

Si algo falla, regresa solo al commit anterior.

**Contenido (opcional):** esta fusión no lo cambia, pero si el diagnóstico pidió `008`, o si la plataforma no muestra algún curso:

```bash
cd ~/amatista/backend && source venv/bin/activate
python herramientas/contenido.py importar          # upsert; repetido dice «sin cambios»
python herramientas/contenido.py practicas --revisar
```

## 5. Comprobar

```bash
curl -s https://api.amatista-3d.tech/api/salud
```

En Database Actions:

```sql
SELECT * FROM v_amatista_espacio ORDER BY mb DESC;                    -- espacio contra los 20 GB
SELECT job_name, state, next_run_date FROM user_scheduler_jobs;        -- purga diaria activa
```

En <https://amatista-3d.tech>:
- entrar con una cuenta, completar un paso y ver que el progreso se guarde;
- abrir `#/geoda` y `#/biblioteca`, que son nuevas y no usan la base.

## 6. Lo que viene para Oracle (no es de esta fusión)

El puente con G7B necesita el script **`014_g7b.sql`** con la tabla `G7B_CACHE`: una copia verificada de lo que publica G7B (T-196). Ese script va con su PR y sigue las reglas de la sección 6 de `backend/sql/LEEME.txt`:
- el mismo cambio en `modelos.py`, en el script y en `diagnostico_oracle.py`;
- idempotente;
- solo agrega.

Cuando exista, se ejecuta igual que en el paso 3, **antes** de actualizar el código que lo usa.
