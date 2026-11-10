# Las API de Amatista y sus errores

Un solo proceso sirve cinco API. Cada una tiene una responsabilidad, vive en su paquete dentro de `backend/api/` y firma sus errores con su nombre. La lista está en [`backend/api/registro.py`](../../backend/api/registro.py) y se consulta en vivo con `GET /api/apis`.

| API | Paquete | Prefijos | Responsabilidad | Operaciones |
|---|---|---|---|---|
| `plataforma` | `api/plataforma/` | `/api/auth`, `/api/contenido`, `/api/blender`, `/api/perfil`, `/api/buzon` y el resto de `/api` | Lo que usa la web del alumno: cuentas, progreso, sincronización, contenido, perfil y buzón | 53 |
| `motor` | `api/motor/` | `/api/addon/v1` | Lo que usa el complemento de Blender: vinculación, prácticas, intentos, enlace en vivo y taller | 32 |
| `aula` | `api/aula/` | `/api/evaluacion` | Panel del profesor y «Mi clase»: grupos, prácticas asignadas, entregas y evaluación | 17 |
| `administracion` | `api/administracion/` | `/api/admin` | Usuarios, sistema y auditoría. Solo profesores y administradores | 7 |
| `datos` | `api/datos/` | `/api/g7b/v1`, `/api/nube/v1` | Estado agregado de G7B para Geoda y la propuesta de Blender en la nube | 1 |

Lo compartido queda en la raíz de `api/`: `dependencias.py` (sesión y roles), `limites.py` (límites por IP), `comun.py`, `correo.py`, `fusion.py`, `registro.py` y `errores.py`. `/`, `/api/salud` y `/api/apis` son del `sistema`.

Las rutas no cambiaron con la separación: lo que cambió es dónde vive el código y cómo se firman los errores.

## Formato de un error

```json
{
  "detail": "No existe esa práctica o todavía no está publicada.",
  "error": {
    "api": "motor",
    "operacion": "leer_practica",
    "codigo": "motor.leer_practica.no_encontrado",
    "estado": 404
  }
}
```

- `detail` es el mensaje de siempre. La web y el complemento lo siguen leyendo igual.
- `error.api` dice qué API falló; `error.operacion` es la función de la ruta.
- `error.codigo` es estable: `api.operacion.motivo`. Sirve para mostrar un mensaje propio o contar fallas.
- Toda respuesta, correcta o no, lleva la cabecera `X-Peticion-Id`.
- En un error interno (500) el cuerpo trae además `error.peticion` con ese identificador, y el detalle completo queda solo en el registro.

| Estado | Motivo en el código |
|---|---|
| 400 | `peticion_invalida` |
| 401 | `sin_sesion` |
| 403 | `sin_permiso` |
| 404 | `no_encontrado` |
| 409 | `conflicto` |
| 413 | `demasiado_grande` |
| 422 | `datos_invalidos` |
| 423 | `bloqueado` |
| 429 | `demasiados_intentos` |
| 500 | `error_interno` |
| 503 | `no_disponible` |

## Encontrar una falla en la instancia

```bash
amatista errores
```

Cuenta los errores internos de las últimas 24 horas por API y operación.

```bash
amatista buscar 58011f353bae
```

Muestra todo lo registrado de una petición, a partir del `X-Peticion-Id` que vio el usuario o el complemento.

## Agregar una ruta o una API

- Una ruta nueva va en el módulo de su API. Si es pública, hay que declararla en `tests/test_seguridad.py`.
- Una API nueva es un paquete en `backend/api/` y una entrada en `APIS`. `tests/test_apis.py` comprueba que cada módulo vive en el paquete de su API y que ninguna ruta queda sin dueño.

## Límites conocidos

- Las cinco API comparten proceso, base de datos y límites por IP. Si el proceso cae, caen todas.
- Los errores 413 y 415 que corta la capa de tamaño y compresión salen antes de llegar a las rutas y no llevan el bloque `error`.
- El motivo sale del estado HTTP: dos fallas distintas con el mismo estado en la misma operación comparten código. Se distinguen por `detail`.
