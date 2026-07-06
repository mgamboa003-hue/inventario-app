# Guía de Despliegue — Railway

## Pasos para publicar en Railway

### 1. Subir los cambios a GitHub
Desde tu PC (no desde la app), en la carpeta del proyecto:
```bash
cd app_inventario
git add -A
git commit -m "Roles, solicitudes, cotizaciones, control de accesos"
git push origin main
```
> Si git muestra un error mencionando `index.lock`, borra el archivo
> `.git\index.lock` dentro de la carpeta del proyecto y vuelve a intentar.

### 2. Crear cuenta y proyecto en Railway
1. Ve a https://railway.app y crea cuenta (puedes entrar con tu cuenta de GitHub)
2. Click en "New Project" → "Deploy from GitHub repo"
3. Conecta tu repositorio `inventario-app`

### 3. Agregar PostgreSQL (recomendado antes de configurar variables)
1. En tu proyecto Railway → "New" → "Database" → "Add PostgreSQL"
2. Railway agrega la variable `DATABASE_URL` automáticamente al servicio de la app
3. La app detecta PostgreSQL solo y lo usa en vez de SQLite — no requiere tocar código

### 4. Variables de entorno en Railway
En el servicio de la app (no en el de PostgreSQL) → pestaña "Variables", agrega:

| Variable              | Valor                                          |
|------------------------|------------------------------------------------|
| SECRET_KEY             | genera una cadena larga aleatoria (ver abajo)  |
| ADMIN_PASSWORD         | tu contraseña segura para el primer login      |
| MAX_UPLOAD_MB          | 10                                              |
| DEBUG                  | False                                           |
| FORCE_HTTPS            | True (una vez que confirmes que la app abre bien) |
| MAX_LOGIN_ATTEMPTS     | 5                                                |
| LOGIN_LOCKOUT_MINUTES  | 15                                               |

Opcionales (tienen valores por defecto razonables si no las agregas):

| Variable               | Para qué sirve                                          | Por defecto |
|-------------------------|----------------------------------------------------------|-------------|
| DIAS_ATRASO_SOLICITUD   | días antes de marcar una solicitud como "Atrasada"       | 5           |
| DIAS_GRACIA_SOLICITUD   | días que una solicitud rechazada/cancelada sigue visible | 7           |
| SESION_ACTIVA_MINUTOS   | minutos de inactividad antes de dejar de mostrar "En línea" | 5        |
| SMTP_HOST / SMTP_TO / etc. | alertas de stock bajo y notificaciones por correo     | desactivado si se dejan vacías |
| S3_BUCKET / S3_ACCESS_KEY / etc. | fotos y documentos en la nube en vez de disco  | desactivado si se dejan vacías |

Para generar un `SECRET_KEY` seguro, corre esto en tu PC (con Python instalado):
```
python -c "import secrets; print(secrets.token_hex(32))"
```

### 5. Verificar el deploy
1. Railway construye e inicia la app automáticamente al detectar el push
2. En "Settings" → "Networking" → "Generate Domain" para obtener tu URL pública (algo como `inventario-app-production.up.railway.app`)
3. Abre esa URL, entra con `admin` y la contraseña que pusiste en `ADMIN_PASSWORD`
4. Cambia la contraseña desde Administración → Usuarios inmediatamente

### 6. Plan de Railway
- **Hobby**: US$5/mes — incluye 512 MB RAM, SSL automático, no se "duerme"
- El costo real depende del uso (RAM/CPU/red); para una app de este tamaño con pocos usuarios normalmente se mantiene cerca del mínimo del plan

---

## Credenciales por defecto
- **Usuario**: admin
- **Contraseña**: la que pusiste en `ADMIN_PASSWORD`

⚠️ **Cambia la contraseña desde Administración → Usuarios inmediatamente después del primer login.**

---

## Fotos y documentos
Las fotos se comprimen automáticamente a WebP (~150KB) al subirse.
Sin S3/R2 configurado, las fotos y documentos de cotizaciones se guardan en el disco
del servidor de Railway — **se pierden en cada redeploy**. Para producción permanente,
configura `S3_BUCKET`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_ENDPOINT` (Cloudflare R2 tiene
plan gratis generoso y es compatible con S3).

---

## Backups en la nube
El botón "Crear respaldo ahora" y `backup_db.py` funcionan igual, pero en Railway con
PostgreSQL conviene además programar un respaldo automático de la base de datos desde
el propio panel de Railway ("Settings" → "Backups" del servicio de PostgreSQL), ya que
los respaldos locales en `backups/` no persisten entre redeploys.
