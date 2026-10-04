#!/usr/bin/env bash
# Actualiza la API en el servidor: git pull → dependencias → pytest →
# reinicio → prueba de /api/salud. Si algo falla, vuelve al commit anterior.
#
# Uso (como el usuario dueño del repositorio, con sudo para systemctl):
#   bash /opt/amatista/despliegue/actualizar.sh          # rama main
#   bash /opt/amatista/despliegue/actualizar.sh dev      # otra rama
#
# Variables opcionales: AMATISTA_REPO (por defecto, la carpeta de este
# repositorio), AMATISTA_SERVICIO y AMATISTA_URL_SALUD
# (http://127.0.0.1:8000/api/salud).
#
# Servicio: amatista-api (la unidad de despliegue/amatista-api.service). La VM
# de producción corre hoy la unidad que ya tenía instalada, amatista-backend
# (bitácora del 3 de octubre, §20): si amatista-api no existe y
# amatista-backend sí, el script usa esa sola.
#
# Rama: si la VM está en otra rama local (por ejemplo despliegue/v3-2026-10-03)
# y todos sus commits ya están en origin/<rama>, el script se cambia solo a
# <rama>. Si tiene commits propios, se detiene y los muestra.
#
# Un cambio de esquema NO se aplica aquí: los scripts de backend/sql/ se
# ejecutan a mano en Database Actions (ver backend/sql/LEEME.txt) ANTES de
# actualizar el código que los necesita. Revertir el código no revierte la base.
set -euo pipefail

REPO="${AMATISTA_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RAMA="${1:-main}"
SERVICIO="${AMATISTA_SERVICIO:-amatista-api}"
if [ -z "${AMATISTA_SERVICIO:-}" ] && ! systemctl cat amatista-api >/dev/null 2>&1 \
   && systemctl cat amatista-backend >/dev/null 2>&1; then
  SERVICIO=amatista-backend
fi
URL_SALUD="${AMATISTA_URL_SALUD:-http://127.0.0.1:8000/api/salud}"

paso() { printf '\n==> %s\n' "$*"; }
fallar() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

cd "$REPO"
git diff --quiet && git diff --cached --quiet \
  || fallar "hay cambios sin confirmar en $REPO; el servidor no debe tener ediciones locales (git status)."

anterior="$(git rev-parse HEAD)"

volver() {
  printf '\n==> Volviendo a %s\n' "${anterior:0:7}" >&2
  git -C "$REPO" reset -q --keep "$anterior"
  "$REPO/backend/venv/bin/pip" install -q -r "$REPO/backend/requirements.txt" || true
}

paso "Descargando $RAMA"
git fetch --prune origin
git rev-parse --verify -q "origin/$RAMA" >/dev/null || fallar "la rama $RAMA no existe en origin."

# La VM puede estar en una rama local que ya no existe en origin (por
# ejemplo despliegue/v3-2026-10-03). Si todo lo suyo ya está en
# origin/$RAMA, se cambia sola; si tiene commits propios, se detiene.
actual="$(git rev-parse --abbrev-ref HEAD)"
if [ "$actual" != "$RAMA" ]; then
  propios="$(git log --oneline "origin/$RAMA..HEAD")"
  if [ -n "$propios" ]; then
    printf '%s\n' "$propios" >&2
    fallar "la rama local $actual tiene esos commits que no están en origin/$RAMA. Respáldalos (git branch respaldo-$actual) y cambia de rama a mano."
  fi
  paso "Cambiando de $actual a $RAMA (todo lo de $actual ya está en origin/$RAMA)"
  if git show-ref --verify -q "refs/heads/$RAMA"; then
    git switch -q "$RAMA"
  else
    git switch -q -c "$RAMA" --track "origin/$RAMA"
  fi
fi
git merge --ff-only "origin/$RAMA"
nuevo="$(git rev-parse HEAD)"
if [ "$nuevo" = "$anterior" ]; then
  echo "Sin cambios: ya estás en ${nuevo:0:7}."
fi
git log --oneline "$anterior..$nuevo" | head -20

cd "$REPO/backend"
paso "Dependencias"
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip install -q -r requirements-dev.txt \
  || { volver; fallar "no se pudieron instalar las dependencias; el servicio no se reinició."; }

paso "Pruebas (SQLite temporal, no tocan Oracle)"
if [ -f .env ]; then
  echo "Aviso: existe backend/.env; sus variables también llegan a las pruebas."
  echo "       En producción usa /etc/amatista/amatista-api.env (ver la guía de despliegue)."
fi
if ! venv/bin/python -m pytest -q; then
  volver
  fallar "las pruebas fallaron: el servicio sigue con la versión anterior (no se reinició)."
fi
venv/bin/python herramientas/contenido.py validar >/dev/null \
  || { volver; fallar "los módulos de contenido no validan (python herramientas/contenido.py validar)."; }

paso "Reiniciando $SERVICIO"
sudo systemctl restart "$SERVICIO"

paso "Probando $URL_SALUD"
for _ in $(seq 1 30); do
  if respuesta="$(curl -fsS --max-time 5 "$URL_SALUD" 2>/dev/null)"; then
    echo "$respuesta"
    paso "Listo: ${anterior:0:7} → ${nuevo:0:7}"
    exit 0
  fi
  sleep 1
done

sudo journalctl -u "$SERVICIO" -n 40 --no-pager >&2 || true
volver
sudo systemctl restart "$SERVICIO"
fallar "/api/salud no respondió en 30 s; se restauró ${anterior:0:7} y se reinició el servicio."
