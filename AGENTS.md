# API de Amatista · instrucciones para agentes

**Leer primero la [base oficial del proyecto](https://github.com/Maximiliano-cabello-mata/amatista-recuperada-20261106/blob/recuperacion/BASE.md)** (versión 1, congelada el 22 de noviembre de 2026). Define las piezas, el flujo de trabajo y las prioridades, y manda sobre cualquier otro plan.

> **Amatista** enseña y mide; **G7B** extrae y guarda; **Amatista IA** audita; **Geoda** adapta; **Amatista Machine Learning** crea.

## Lugar de este repositorio en la base

- **Es la API de Amatista y la aduana de la frontera de privacidad.** Guarda cuentas, progreso, contenido y clases en Oracle. Los datos personales se quedan aquí.
- **Hacia G7B solo salen mediciones agregadas y con permiso del alumno.** Desde Geoda solo entran recomendaciones y ajustes, por la API `datos` (`/api/g7b/v1`).
- **Sirve al add-on de Blender** por la API `motor` (`/api/addon/v1`). El add-on es sonda de Blender y es donde corre Geoda.
- **No decide si una práctica está bien hecha:** eso lo decide el motor de Amatista (`amatista_engine`), en [`amatista-motor-`](https://github.com/Maximiliano-cabello-mata/amatista-motor-). El motor de evaluación no es el motor adaptativo: ese es Geoda.

## Reglas

- No agregar piezas, servicios, bases de datos ni proveedores de IA. Una idea nueva va al estacionamiento de la base.
- Un solo cambio activo a la vez, y solo dentro de la prioridad vigente de la base.
- Ningún dato personal sale de esta API hacia G7B, Amatista IA o servicios externos.
- Los cambios de contrato (rutas o respuestas) van en un cambio aparte y se anotan en el historial del repositorio.
- No afirmar que algo está desplegado o probado sin evidencia.
