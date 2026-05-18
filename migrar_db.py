"""
Script para migrar la base de datos existente al nuevo esquema.
Ejecutar UNA SOLA VEZ: python migrar_db.py
"""
import sqlite3
from werkzeug.security import generate_password_hash

DB_VIEJA = 'inventario.db'  # tu base de datos actual

def migrar():
    conn = sqlite3.connect(DB_VIEJA)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Verificar tablas existentes
    tablas = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"Tablas encontradas: {tablas}")

    # Agregar tabla usuarios si no existe
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        nombre TEXT,
        role TEXT DEFAULT 'bodeguero',
        created_at TEXT DEFAULT (datetime('now'))
    )''')

    # Agregar columnas nuevas a productos si no existen
    cols_productos = [r[1] for r in c.execute("PRAGMA table_info(productos)").fetchall()]
    print(f"Columnas productos: {cols_productos}")

    nuevas_cols = {
        'descripcion': 'TEXT',
        'equipo_id': 'INTEGER',
        'linea': 'TEXT',
        'precio': 'REAL',
    }
    for col, tipo in nuevas_cols.items():
        if col not in cols_productos:
            c.execute(f"ALTER TABLE productos ADD COLUMN {col} {tipo}")
            print(f"  + Columna {col} agregada a productos")

    # Crear tabla equipos si no existe
    c.execute('''CREATE TABLE IF NOT EXISTS equipos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL
    )''')

    # Verificar tabla proveedores - agregar columnas si faltan
    if 'proveedores' in tablas:
        cols_prov = [r[1] for r in c.execute("PRAGMA table_info(proveedores)").fetchall()]
        for col in ['contacto', 'email', 'telefono']:
            if col not in cols_prov:
                c.execute(f"ALTER TABLE proveedores ADD COLUMN {col} TEXT")
                print(f"  + Columna {col} agregada a proveedores")

    # Admin por defecto
    admin = c.execute("SELECT id FROM usuarios WHERE username='admin'").fetchone()
    if not admin:
        c.execute("INSERT INTO usuarios (username, password, nombre, role) VALUES (?,?,?,?)",
                  ('admin', generate_password_hash('admin123'), 'Administrador', 'admin'))
        print("  + Usuario admin creado (password: admin123)")

    conn.commit()
    conn.close()
    print("\n✅ Migración completada exitosamente!")
    print("   Recuerda cambiar la contraseña del admin en la sección Usuarios.")

if __name__ == '__main__':
    migrar()
