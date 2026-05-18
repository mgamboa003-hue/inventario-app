# db.py
import json
import os
import sqlite3

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventario.db")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE,
            nombre TEXT NOT NULL,
            categoria TEXT,
            equipo TEXT,
            linea TEXT,
            stock_minimo INTEGER DEFAULT 0,
            stock_actual INTEGER DEFAULT 0,
            ubicacion TEXT,
            proveedor TEXT,
            precio REAL,
            imagen_url TEXT,
            descripcion TEXT,
            equipo_id INTEGER,
            categoria_id INTEGER,
            proveedor_id INTEGER,
            foto TEXT
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            cantidad INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            usuario TEXT,
            motivo TEXT,
            usuario_id INTEGER,
            FOREIGN KEY(producto_id) REFERENCES productos(id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            nombre TEXT,
            role TEXT DEFAULT 'bodeguero',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS equipos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL,
            contacto TEXT,
            email TEXT,
            telefono TEXT
        );
    """)

    conn.commit()

    # Cargar datos reales si la tabla está vacía (primer arranque en Render)
    cur.execute("SELECT COUNT(*) FROM productos")
    if cur.fetchone()[0] == 0:
        _seed_data(conn)

    conn.close()


def _seed_data(conn):
    seed_file = os.path.join(BASE_DIR, "seed_data.json")
    if not os.path.exists(seed_file):
        return

    with open(seed_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    cur = conn.cursor()

    for p in data.get("productos", []):
        cur.execute("""
            INSERT OR IGNORE INTO productos
            (id, codigo, nombre, categoria, equipo, linea, stock_minimo, stock_actual,
             ubicacion, proveedor, precio, imagen_url, descripcion,
             equipo_id, categoria_id, proveedor_id, foto)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p.get("id"), p.get("codigo"), p.get("nombre"), p.get("categoria"),
            p.get("equipo"), p.get("linea"), p.get("stock_minimo", 0),
            p.get("stock_actual", 0), p.get("ubicacion"), p.get("proveedor"),
            p.get("precio", 0), p.get("imagen_url"), p.get("descripcion"),
            p.get("equipo_id"), p.get("categoria_id"), p.get("proveedor_id"),
            p.get("foto"),
        ))

    for m in data.get("movimientos", []):
        cur.execute("""
            INSERT OR IGNORE INTO movimientos
            (id, producto_id, tipo, cantidad, fecha, usuario, motivo, usuario_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m.get("id"), m.get("producto_id"), m.get("tipo"), m.get("cantidad"),
            m.get("fecha"), m.get("usuario"), m.get("motivo"), m.get("usuario_id"),
        ))

    conn.commit()
