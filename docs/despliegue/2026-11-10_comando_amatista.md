# El comando `amatista` y el paso a repositorios separados

Desde el 10 de noviembre de 2026 la API vive en este repositorio y el motor en [`amatista-motor-`](https://github.com/Maximiliano-cabello-mata/amatista-motor-). En la instancia de Oracle quedan como carpetas hermanas:

```text
/home/opc/amatista-api-/     este repositorio (backend/, despliegue/)
/home/opc/amatista-motor-/   engine/, addon/, practices/
/home/opc/amatista/          el monorepo anterior; se conserva y ya no se usa para servir
```

Todo se opera con [`despliegue/amatista`](../../despliegue/amatista).

## Pasar la instancia a los repositorios nuevos (una vez)

Por SSH, como `opc`:

```bash
git clone https://github.com/Maximiliano-cabello-mata/amatista-api- ~/amatista-api-
```

```bash
~/amatista-api-/despliegue/amatista instalar
```

`instalar` hace esto, en orden:

1. Clona el motor en `~/amatista-motor-` si no está.
2. Crea `backend/venv` e instala las dependencias.
3. Usa `/etc/amatista/amatista-api.env` si existe. Si no, usa `backend/.env`; si tampoco existe, lo copia de `~/amatista/backend/.env` con sus mismos permisos.
4. Comprueba que la API carga con el motor.
5. Escribe `/etc/systemd/system/amatista-api.service` con las rutas reales, detiene la unidad anterior (`amatista-backend`) y arranca la nueva.
6. Espera a `/api/salud`. Si no responde en 30 segundos, apaga la nueva y vuelve a encender la anterior.
7. Deja `amatista` en `/usr/local/bin`.

Durante el paso 5 la API no responde unos segundos.

## Uso diario

| Orden | Qué hace |
|---|---|
| `amatista actualizar` | Descarga motor y API, instala dependencias, corre las pruebas, compara el esquema de Oracle, reinicia y comprueba `/api/salud`. Si algo falla, vuelve los dos repositorios al commit anterior |
| `amatista estado` | Commit de cada repositorio, servicio, salud de la API, versión del complemento, Caddy, disco y último respaldo |
| `amatista registros [n]` | Últimas líneas del servicio. Con `-f` las sigue |
| `amatista errores [horas]` | Errores internos de las últimas horas, contados por API y operación |
| `amatista buscar <id>` | Todo lo registrado de una petición, a partir de su `X-Peticion-Id` |
| `amatista diagnostico` | Compara el esquema de Oracle con el del código. Solo lee |
| `amatista respaldo` | Copia Git de los dos repositorios y exporta la base a `~/respaldos/amatista-<fecha>/` |
| `amatista admin correo@dominio.com` | Da rol de administrador. Con `--rol profesor`, de profesor |
| `amatista caddy` | Valida el `Caddyfile` y lo recarga por la API de administración |
| `amatista reiniciar` | Reinicia la API y comprueba que responde |

Un cambio de esquema sigue sin aplicarse solo: los scripts de `backend/sql/` se ejecutan en Database Actions antes de actualizar (ver `backend/sql/LEEME.txt`). `amatista actualizar` se detiene sin reiniciar si a la base le falta algo.

## Qué está comprobado

Probado el 10 de noviembre en una máquina de desarrollo, con la API sobre SQLite y el motor en la carpeta hermana: `estado`, `respaldo`, `diagnostico` (responde que no hay Oracle, salida 1) y la comprobación de carga del paso 4.

**No probado todavía en la instancia:** `instalar`, `actualizar`, `registros`, `buscar`, `errores`, `caddy` y `reiniciar`, porque usan systemd y Oracle. La primera ejecución de `instalar` conviene hacerla con tiempo y con un `amatista respaldo` del monorepo hecho antes.
