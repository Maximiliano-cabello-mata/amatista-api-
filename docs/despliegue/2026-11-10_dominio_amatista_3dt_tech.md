# Dominio `amatista-3dt.tech` (10 de noviembre de 2026)

`amatista-3dt.tech` pasa a ser el dominio oficial. `amatista-3d.tech` sigue aceptado mientras convivan los dos.

```
Navegador ──HTTPS──> Cloudflare (amatista-3dt.tech) ──> Worker «amatista» (PWA)
           └─HTTPS──> Cloudflare (api.amatista-3dt.tech) ──> Caddy :443 en OCI ──> uvicorn 127.0.0.1:8000 ──> Oracle
```

## Estado comprobado el 10 de noviembre (en la VM)

| Comprobación | Resultado |
|---|---|
| `curl http://127.0.0.1:8000/api/salud` en la VM | `{"estado":"ok","motor":"oracle"}`: la API conecta con Oracle |
| Bloque `api.amatista-3dt.tech` en `/etc/caddy/Caddyfile` con `certs/api-3dt.pem` | Cargado con `caddy reload` (respaldo en `/etc/caddy/Caddyfile.antes-3dt.*`) |
| Prueba local por `127.0.0.1:443` | HTTP 403 esperado: el Caddyfile de la VM solo atiende IP de Cloudflare (`remote_ip`) |
| `systemctl reload caddy` | Falla con `status=226/NAMESPACE` (ver más abajo). `caddy reload --address 127.0.0.1:2019` sí funciona |

Comprobado desde fuera (sesión en la nube, 10 nov, 01:15 UTC):

| Comprobación | Resultado |
|---|---|
| `https://api.amatista-3dt.tech/api/salud` | 200, `{"estado":"ok","motor":"oracle"}` |
| Preflight CORS con `Origin: https://amatista-3dt.tech` | 200, `access-control-allow-origin: https://amatista-3dt.tech` |
| `https://amatista-3dt.tech/` | 200, pero la PWA publicada tiene compilada `https://api.amatista-3d.tech` y la CSP solo permite esa API |
| `https://amatista-3d.tech/`, `https://api.amatista-3d.tech/`, `https://www.amatista-3dt.tech/` | No responden: el proxy de la sesión da 502 al conectar, lo que apunta a que esos nombres no resuelven |

**Causa de «no conecta»:** la PWA publicada en `amatista-3dt.tech` llama a `api.amatista-3d.tech`, que ya no responde. Se corrige publicando un build con este cambio y con `VITE_API_URL` vacía o en `https://api.amatista-3dt.tech` (sección 2).

Si la PWA no carga datos en el dominio nuevo, el problema está entre el navegador y la API, no en Oracle. Hay tres causas posibles, y cualquiera basta para cortar la conexión:

1. **CORS del backend.** `CORS_ORIGINS` no incluye `https://amatista-3dt.tech`, así que el navegador descarta las respuestas.
2. **PWA antigua.** En builds anteriores a este cambio, `apiPorDefecto()` solo reconoce `amatista-3d.tech`. En el host nuevo intenta `http://localhost:8000`, y la sincronización se desactiva por contenido mixto.
3. **CSP de `_headers`.** En builds anteriores, `connect-src` solo permite `https://api.amatista-3d.tech`.

## 1. Servidor: CORS y direcciones públicas (VM, por SSH)

El script agrega los orígenes sin borrar los que ya existen, guarda un respaldo y reinicia la unidad. Cópialo completo en la sesión SSH, **no** en PowerShell:

```bash
sudo bash <<'CORS'
set -eu
f=/etc/amatista/amatista-api.env
[ -f "$f" ] || f=/home/opc/amatista/backend/.env
cp -p "$f" "$f.antes-3dt.$(date +%Y%m%d-%H%M%S)"
nuevos="https://amatista-3dt.tech,https://www.amatista-3dt.tech,https://amatista-3d.tech,https://www.amatista-3d.tech"
actual=$(grep -E '^CORS_ORIGINS=' "$f" | tail -1 | cut -d= -f2- | tr -d '"')
lista=$(printf '%s,%s' "$actual" "$nuevos" | tr ',' '\n' | sed 's/[[:space:]]//g;/^$/d' | awk '!v[$0]++' | paste -sd, -)
tmp=$(mktemp)
grep -vE '^(CORS_ORIGINS|AMATISTA_URL_PWA|AMATISTA_URL_API)=' "$f" > "$tmp" || true
printf 'CORS_ORIGINS=%s\nAMATISTA_URL_PWA=https://amatista-3dt.tech\nAMATISTA_URL_API=https://api.amatista-3dt.tech\n' "$lista" >> "$tmp"
cat "$tmp" > "$f"; rm -f "$tmp"   # conserva dueño y permisos del archivo
svc=amatista-api; systemctl cat amatista-api >/dev/null 2>&1 || svc=amatista-backend
systemctl restart "$svc"; sleep 4
echo "Archivo: $f · servicio: $svc"
curl -fsS http://127.0.0.1:8000/api/salud; echo
curl -sS -o /dev/null -D - -X OPTIONS http://127.0.0.1:8000/api/salud \
  -H 'Origin: https://amatista-3dt.tech' -H 'Access-Control-Request-Method: GET' |
  grep -i '^access-control-allow-origin' || { echo "CORS todavía no acepta el dominio nuevo"; exit 1; }
CORS
```

Debe terminar con `access-control-allow-origin: https://amatista-3dt.tech`.

## 2. Cloudflare (panel de `amatista-3dt.tech`)

- **DNS:** `api` de tipo A apuntando a `158.101.118.222`, con proxy activado (nube naranja). El dominio raíz y `www` se asignan como dominio personalizado del Worker `amatista`, en *Workers & Pages › amatista › Settings › Domains & Routes*.
- **SSL/TLS:** modo **Full (strict)**. El certificado de origen `*.amatista-3dt.tech, amatista-3dt.tech` ya está en `/etc/caddy/certs/api-3dt.pem` y `api-3dt.key`.
- **Workers Builds:** el build debe salir de este repositorio con este cambio ya incluido. En *Settings › Build › Variables*, deja `VITE_API_URL` vacía (cada dominio usa su propio `api.`) o fíjala en `https://api.amatista-3dt.tech`. Si queda `https://api.amatista-3d.tech`, la PWA nueva seguirá usando la API anterior.
- **Google (inicio de sesión):** en Google Cloud Console › Credenciales › cliente OAuth, agrega `https://amatista-3dt.tech` (y `https://www` si se usa) en *Orígenes de JavaScript autorizados*.

Comprobación desde **PowerShell en Windows** (no dentro de SSH; en la VM el comando es `curl`, no `curl.exe`):

```powershell
curl.exe -sS --max-time 15 -w "`nHTTP %{http_code}`n" https://api.amatista-3dt.tech/api/salud
curl.exe -sS -o NUL -D - -X OPTIONS https://api.amatista-3dt.tech/api/salud -H "Origin: https://amatista-3dt.tech" -H "Access-Control-Request-Method: GET"
```

## 3. Qué cambia en el repositorio

| Archivo | Cambio |
|---|---|
| `frontend/src/services/api.js` | `DOMINIOS = ["amatista-3dt.tech", "amatista-3d.tech"]`. Cada dominio usa `https://api.<dominio>` |
| `frontend/public/_headers` | `connect-src` permite las dos API |
| `backend/api/correo.py` | Los enlaces de los correos salen de `AMATISTA_URL_PWA` (por defecto `https://amatista-3dt.tech`) |
| `backend/.env.example`, `frontend/.env.example` | CORS con los dos dominios y URL nuevas |
| `despliegue/Caddyfile` | Fragmento común `(api_amatista)` y dos sitios, cada uno con su certificado. **No está validado con `caddy validate`**: hay que ejecutarlo en la VM antes de copiarlo. El de la VM además restringe las peticiones a IP de Cloudflare |
| `escritorio/`, `addon/herramientas/`, `nube/nodo/` | Direcciones por defecto en el dominio nuevo |

No cambian: los remitentes `no-responder@amatista-3d.tech` y `seguridad@amatista-3d.tech`, porque dependen del dominio verificado en Brevo/SMTP; el identificador de la app de macOS `tech.amatista-3d.lanzador`; y la documentación histórica.

## 4. `systemctl reload caddy` falla con `226/NAMESPACE`

`Failed to create destination mount point node '/run/systemd/unit-root/tmp'` indica que el espacio de nombres privado (`PrivateTmp`) del proceso de Caddy en marcha quedó inconsistente. Mientras tanto, recarga por la API de administración:

```bash
sudo caddy reload --address 127.0.0.1:2019 --config /etc/caddy/Caddyfile --adapter caddyfile
```

Un `sudo systemctl restart caddy` en una ventana tranquila crea el espacio de nombres de nuevo y devuelve el funcionamiento a `reload`. Corta las conexiones durante un segundo.

## 5. Copiar el respaldo Git a Windows

`scp` se ejecuta en **PowerShell de Windows**, después de salir de SSH (`exit`). Dentro de la VM, `$env:USERPROFILE` no existe:

```powershell
$destino = "$env:USERPROFILE\Documents\Codex\2026-10-03\new-chat-4"
New-Item -ItemType Directory -Force $destino | Out-Null
scp -i "$env:USERPROFILE\Downloads\ssh-key-2026-09-27.key" opc@158.101.118.222:/home/opc/respaldos/amatista-git-20261110-002103.bundle "$destino\amatista-recuperada.bundle"
git clone "$destino\amatista-recuperada.bundle" "$destino\amatista"
```
