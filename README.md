# Inventario Wintec v3

## Instalación

### 1. Copiar archivos
Copia todos los archivos de esta carpeta a `C:\Users\SOLDADORA\Desktop\app_inventario`

### 2. Migrar base de datos existente
Las columnas y tablas nuevas (auditoría, tokens de API, órdenes de compra, bloqueo de
login, soft-delete de productos) se crean automáticamente al iniciar la app. No hace
falta correr `migrar_db.py` a mano salvo que vengas de una versión muy antigua.

### 3. Instalar dependencias nuevas
```
venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Iniciar la app
Doble clic en `iniciar_inventario.bat`

---

## Acceso

- **Local (tu PC):** http://127.0.0.1:5002
- **Red LAN (otros PCs y celular):** http://[IP-DE-TU-PC]:5002
- **Nube (Railway/Render + PostgreSQL):** ver `DEPLOY_RAILWAY.md`. La app ya detecta
  `DATABASE_URL` automáticamente y usa PostgreSQL en vez de SQLite.

---

## Usuarios por defecto

| Usuario | Contraseña | Rol |
|---------|-----------|-----|
| admin   | admin123  | Admin |

**⚠️ Cambia la contraseña inmediatamente desde Administración → Usuarios**

---

## Roles

| Rol | Puede hacer |
|-----|------------|
| **Admin** | Todo: crear/editar/eliminar productos, movimientos, usuarios, exportar |
| **Solo lectura** | Ver productos y movimientos, no puede modificar |

---

## Funciones nuevas en v3

- 🔒 Bloqueo de cuenta tras varios intentos fallidos de login (configurable)
- 🔒 Cookies de sesión más seguras y soporte para forzar HTTPS en producción
- 📝 Auditoría: registro de quién crea, edita o elimina cada producto, usuario y movimiento (Administración → Auditoría)
- ♻️ Los productos ya no se borran en duro: se desactivan (soft-delete) y se pueden restaurar sin perder su historial de movimientos
- 📊 Nuevos reportes Excel: **Valorización de inventario**, **Rotación de stock** y **Clasificación ABC**
- 🛒 Órdenes de compra: genera automáticamente una OC por proveedor con los productos bajo el mínimo, y expórtala como Excel formal
- 🔑 Tokens de API (Administración → Tokens de API) para conectar la app con un lector de código de barras externo, un ERP, o cualquier integración vía `/api/productos` y `/api/movimientos`
- 📷 Escaneo de código de barras / QR desde la cámara del celular al registrar un movimiento
- 📱 App instalable (PWA): desde el celular, "Agregar a pantalla de inicio" para usarla como app nativa
- 💾 Respaldos automáticos de la base de datos (`backup_db.py`, `backup_diario.bat`) — ver sección Respaldos
- 📧 Alertas de stock bajo por correo (opcional, requiere configurar SMTP en `.env`)
- ☁️ Fotos de repuestos en S3 / Cloudflare R2 (opcional, requiere configurar en `.env`) en vez de solo disco local
- ✅ Pruebas automatizadas (`pytest`) y CI en GitHub Actions

---

## Respaldos

**Manual:** botón "Crear respaldo ahora" en Administración → Sistema y respaldos, o
ejecuta `python backup_db.py`.

**Automático diario:** ejecuta `registrar_backup_programado.bat` **una vez, como
administrador**. Esto registra una tarea en el Programador de tareas de Windows que
corre todos los días a las 23:00. Los respaldos quedan en la carpeta `backups/` y se
conservan los últimos 14 (configurable con `BACKUPS_A_MANTENER` en `.env`).

---

## Llevar la app a la nube (dejar de ser solo local)

1. Sigue `DEPLOY_RAILWAY.md` para desplegar en Railway (o el equivalente en Render).
2. Agrega el plugin de PostgreSQL — la app usa `DATABASE_URL` automáticamente.
3. Configura `S3_BUCKET` / `S3_ACCESS_KEY` (o el equivalente de Cloudflare R2) para que
   las fotos no se pierdan en cada redeploy.
4. Configura `SMTP_HOST` / `SMTP_TO` si quieres alertas de stock bajo por correo.
5. Activa `FORCE_HTTPS=true` una vez que el dominio tenga SSL.

Ninguno de estos pasos requiere tocar el código: todo se activa con variables de entorno.

---

## API REST

Genera un token en Administración → Tokens de API y úsalo así:

```
GET  /api/productos                 (lista productos activos)
GET  /api/productos/<id>            (detalle de un producto)
POST /api/productos                 (crear producto, requiere rol admin)
POST /api/movimientos               (registrar entrada/salida, requiere rol admin)
```

Con el header `Authorization: Bearer <tu-token>`.

---

## Foto desde celular

1. Abrir producto en la lista
2. Hacer clic en el botón 📱
3. Escanear QR con el celular
4. Tomar foto o elegir de galería
5. La foto se actualiza automáticamente

**Requiere que el celular y el PC estén en la misma red WiFi (o que la app esté en la nube).**

---

## Pruebas

```
pip install -r requirements-dev.txt
pytest -v
```
