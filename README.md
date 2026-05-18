# Inventario Wintec v2

## Instalación

### 1. Copiar archivos
Copia todos los archivos de esta carpeta a `C:\Users\SOLDADORA\Desktop\app_inventario`

### 2. Migrar base de datos existente
Si ya tienes datos en `inventario.db`, ejecutar UNA SOLA VEZ:
```
python migrar_db.py
```

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
| **Bodeguero** | Crear/editar productos, registrar movimientos |
| **Solo lectura** | Ver productos y movimientos, no puede modificar |

---

## Funciones nuevas en v2

- ✅ Login con usuarios y roles
- ✅ Dashboard con KPIs y 4 gráficos
- ✅ Alertas de stock bajo en dashboard
- ✅ Fotos desde celular via QR (acceso en red local)
- ✅ Exportar a Excel: inventario completo, faltantes, movimientos
- ✅ Gestión de proveedores con email/teléfono
- ✅ Historial de movimientos por producto
- ✅ Interfaz profesional dark mode

---

## Foto desde celular

1. Abrir producto en la lista
2. Hacer clic en el botón 📱
3. Escanear QR con el celular
4. Tomar foto o elegir de galería
5. La foto se actualiza automáticamente

**Requiere que el celular y el PC estén en la misma red WiFi.**
