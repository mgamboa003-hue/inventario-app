"""
db.py -- Modulo de base de datos para Inventario Wintec v3
Soporta SQLite (desarrollo/local) y PostgreSQL (produccion Railway/Render).
"""

import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = DATABASE_URL.startswith("postgres")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "inventario.db")

TZ_CHILE = ZoneInfo("America/Santiago")


def ahora():
    """Fecha/hora actual en el huso horario de Chile (America/Santiago), como
    datetime "naive" (sin tzinfo) para poder usarse igual que datetime.now()
    en toda la app (restas, strftime, comparaciones), sin depender del huso
    horario del servidor donde corre el contenedor (Railway usa UTC)."""
    return datetime.now(TZ_CHILE).replace(tzinfo=None)


def ahora_str():
    return ahora().strftime("%Y-%m-%d %H:%M:%S")


def _sqlite_conn():
    conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA busy_timeout=15000;")
    except Exception:
        pass
    return conn


def _pg_conn():
    try:
        import psycopg2
        import psycopg2.extras
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute("SET TIME ZONE 'America/Santiago'")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        return conn
    except ImportError:
        raise RuntimeError("psycopg2 no instalado. Ejecuta: pip install psycopg2-binary")


def get_db_connection():
    if USE_POSTGRES:
        return _pg_conn()
    return _sqlite_conn()


def p():
    """Placeholder correcto segun el motor de BD."""
    return "%s" if USE_POSTGRES else "?"


_DDL_SQLITE = [
    """CREATE TABLE IF NOT EXISTS usuarios (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        username        TEXT UNIQUE NOT NULL,
        password        TEXT NOT NULL,
        nombre          TEXT,
        role            TEXT DEFAULT 'viewer',
        activo          INTEGER DEFAULT 1,
        failed_attempts INTEGER DEFAULT 0,
        locked_until    TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS equipos (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS categorias (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS proveedores (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre   TEXT UNIQUE NOT NULL,
        contacto TEXT,
        email    TEXT,
        telefono TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ubicaciones (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS productos (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo       TEXT UNIQUE,
        nombre       TEXT NOT NULL,
        descripcion  TEXT,
        categoria    TEXT,
        categoria_id INTEGER,
        equipo       TEXT,
        equipo_id    INTEGER,
        linea        TEXT,
        proveedor    TEXT,
        proveedor_id INTEGER,
        stock_minimo INTEGER DEFAULT 0,
        stock_actual INTEGER DEFAULT 0,
        ubicacion    TEXT,
        precio       REAL DEFAULT 0,
        imagen_url   TEXT,
        foto         TEXT,
        activo       INTEGER DEFAULT 1,
        created_at   TEXT DEFAULT (datetime('now')),
        updated_at   TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS movimientos (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        producto_id INTEGER NOT NULL,
        tipo        TEXT NOT NULL,
        cantidad    INTEGER NOT NULL,
        fecha       TEXT NOT NULL,
        usuario     TEXT,
        usuario_id  INTEGER,
        motivo      TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS auditoria (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tabla       TEXT NOT NULL,
        registro_id INTEGER,
        accion      TEXT NOT NULL,
        usuario_id  INTEGER,
        usuario     TEXT,
        detalle     TEXT,
        fecha       TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS api_tokens (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id   INTEGER,
        nombre       TEXT,
        token_hash   TEXT UNIQUE NOT NULL,
        activo       INTEGER DEFAULT 1,
        created_at   TEXT DEFAULT (datetime('now')),
        last_used_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ordenes_compra (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        numero           TEXT UNIQUE,
        proveedor_id     INTEGER,
        proveedor_nombre TEXT,
        estado           TEXT DEFAULT 'pendiente',
        creado_por       TEXT,
        creado_por_id    INTEGER,
        fecha            TEXT DEFAULT (datetime('now')),
        total_estimado   REAL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS ordenes_compra_items (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        orden_id        INTEGER NOT NULL,
        producto_id     INTEGER,
        codigo          TEXT,
        nombre          TEXT,
        cantidad        INTEGER,
        precio_unitario REAL,
        subtotal        REAL
    )""",
    """CREATE TABLE IF NOT EXISTS cotizaciones (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        numero           TEXT UNIQUE,
        proveedor_id     INTEGER,
        proveedor_nombre TEXT,
        fecha_recibida   TEXT,
        fecha_vigencia   TEXT,
        monto_total      REAL DEFAULT 0,
        estado           TEXT DEFAULT 'pendiente',
        documento_url    TEXT,
        notas            TEXT,
        orden_compra_id  INTEGER,
        creado_por       TEXT,
        creado_por_id    INTEGER,
        creado_en        TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS cotizacion_items (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        cotizacion_id   INTEGER NOT NULL,
        producto_id     INTEGER,
        codigo          TEXT,
        nombre          TEXT,
        cantidad        INTEGER,
        precio_unitario REAL,
        subtotal        REAL
    )""",
    """CREATE TABLE IF NOT EXISTS solicitudes (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        producto_id      INTEGER,
        nombre_item      TEXT NOT NULL,
        descripcion      TEXT,
        cantidad         INTEGER DEFAULT 1,
        urgencia         TEXT DEFAULT 'normal',
        foto_url         TEXT,
        estado           TEXT DEFAULT 'pendiente',
        solicitado_por   TEXT,
        solicitado_por_id INTEGER,
        fecha_solicitud  TEXT DEFAULT (datetime('now')),
        fecha_atendida   TEXT,
        comprado_por     TEXT,
        notas            TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS sesiones (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id        INTEGER,
        ip                TEXT,
        user_agent        TEXT,
        inicio            TEXT DEFAULT (datetime('now')),
        ultima_actividad  TEXT,
        fin               TEXT,
        duracion_segundos INTEGER
    )""",
]

_DDL_POSTGRES = [
    """CREATE TABLE IF NOT EXISTS usuarios (
        id              SERIAL PRIMARY KEY,
        username        TEXT UNIQUE NOT NULL,
        password        TEXT NOT NULL,
        nombre          TEXT,
        role            TEXT DEFAULT 'viewer',
        activo          BOOLEAN DEFAULT TRUE,
        failed_attempts INTEGER DEFAULT 0,
        locked_until    TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS equipos (
        id     SERIAL PRIMARY KEY,
        nombre TEXT UNIQUE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS categorias (
        id     SERIAL PRIMARY KEY,
        nombre TEXT UNIQUE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS proveedores (
        id       SERIAL PRIMARY KEY,
        nombre   TEXT UNIQUE NOT NULL,
        contacto TEXT,
        email    TEXT,
        telefono TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ubicaciones (
        id     SERIAL PRIMARY KEY,
        nombre TEXT UNIQUE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS productos (
        id           SERIAL PRIMARY KEY,
        codigo       TEXT UNIQUE,
        nombre       TEXT NOT NULL,
        descripcion  TEXT,
        categoria    TEXT,
        categoria_id INTEGER,
        equipo       TEXT,
        equipo_id    INTEGER,
        linea        TEXT,
        proveedor    TEXT,
        proveedor_id INTEGER,
        stock_minimo INTEGER DEFAULT 0,
        stock_actual INTEGER DEFAULT 0,
        ubicacion    TEXT,
        precio       NUMERIC DEFAULT 0,
        imagen_url   TEXT,
        foto         TEXT,
        activo       BOOLEAN DEFAULT TRUE,
        created_at   TIMESTAMPTZ DEFAULT NOW(),
        updated_at   TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS movimientos (
        id          SERIAL PRIMARY KEY,
        producto_id INTEGER NOT NULL,
        tipo        TEXT NOT NULL,
        cantidad    INTEGER NOT NULL,
        fecha       TIMESTAMPTZ NOT NULL,
        usuario     TEXT,
        usuario_id  INTEGER,
        motivo      TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS auditoria (
        id          SERIAL PRIMARY KEY,
        tabla       TEXT NOT NULL,
        registro_id INTEGER,
        accion      TEXT NOT NULL,
        usuario_id  INTEGER,
        usuario     TEXT,
        detalle     TEXT,
        fecha       TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS api_tokens (
        id           SERIAL PRIMARY KEY,
        usuario_id   INTEGER,
        nombre       TEXT,
        token_hash   TEXT UNIQUE NOT NULL,
        activo       BOOLEAN DEFAULT TRUE,
        created_at   TIMESTAMPTZ DEFAULT NOW(),
        last_used_at TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS ordenes_compra (
        id               SERIAL PRIMARY KEY,
        numero           TEXT UNIQUE,
        proveedor_id     INTEGER,
        proveedor_nombre TEXT,
        estado           TEXT DEFAULT 'pendiente',
        creado_por       TEXT,
        creado_por_id    INTEGER,
        fecha            TIMESTAMPTZ DEFAULT NOW(),
        total_estimado   NUMERIC DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS ordenes_compra_items (
        id              SERIAL PRIMARY KEY,
        orden_id        INTEGER NOT NULL,
        producto_id     INTEGER,
        codigo          TEXT,
        nombre          TEXT,
        cantidad        INTEGER,
        precio_unitario NUMERIC,
        subtotal        NUMERIC
    )""",
    """CREATE TABLE IF NOT EXISTS cotizaciones (
        id               SERIAL PRIMARY KEY,
        numero           TEXT UNIQUE,
        proveedor_id     INTEGER,
        proveedor_nombre TEXT,
        fecha_recibida   TEXT,
        fecha_vigencia   TEXT,
        monto_total      NUMERIC DEFAULT 0,
        estado           TEXT DEFAULT 'pendiente',
        documento_url    TEXT,
        notas            TEXT,
        orden_compra_id  INTEGER,
        creado_por       TEXT,
        creado_por_id    INTEGER,
        creado_en        TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS cotizacion_items (
        id              SERIAL PRIMARY KEY,
        cotizacion_id   INTEGER NOT NULL,
        producto_id     INTEGER,
        codigo          TEXT,
        nombre          TEXT,
        cantidad        INTEGER,
        precio_unitario NUMERIC,
        subtotal        NUMERIC
    )""",
    """CREATE TABLE IF NOT EXISTS solicitudes (
        id               SERIAL PRIMARY KEY,
        producto_id      INTEGER,
        nombre_item      TEXT NOT NULL,
        descripcion      TEXT,
        cantidad         INTEGER DEFAULT 1,
        urgencia         TEXT DEFAULT 'normal',
        foto_url         TEXT,
        estado           TEXT DEFAULT 'pendiente',
        solicitado_por   TEXT,
        solicitado_por_id INTEGER,
        fecha_solicitud  TIMESTAMPTZ DEFAULT NOW(),
        fecha_atendida   TIMESTAMPTZ,
        comprado_por     TEXT,
        notas            TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS sesiones (
        id                SERIAL PRIMARY KEY,
        usuario_id        INTEGER,
        ip                TEXT,
        user_agent        TEXT,
        inicio            TIMESTAMPTZ DEFAULT NOW(),
        ultima_actividad  TIMESTAMPTZ,
        fin               TIMESTAMPTZ,
        duracion_segundos INTEGER
    )""",
]


def _run_migrations(conn):
    """Agrega columnas faltantes a tablas existentes (compatibilidad hacia adelante)."""
    cur = conn.cursor()
    migrations = [
        ("usuarios",   "activo",          "INTEGER DEFAULT 1" if not USE_POSTGRES else "BOOLEAN DEFAULT TRUE"),
        ("usuarios",   "nombre",          "TEXT"),
        ("usuarios",   "failed_attempts", "INTEGER DEFAULT 0"),
        ("usuarios",   "locked_until",    "TEXT" if not USE_POSTGRES else "TIMESTAMPTZ"),
        ("usuarios",   "ultimo_login",    "TEXT" if not USE_POSTGRES else "TIMESTAMPTZ"),
        ("usuarios",   "ultima_ip",       "TEXT"),
        ("usuarios",   "debe_cambiar_password", "INTEGER DEFAULT 0" if not USE_POSTGRES else "BOOLEAN DEFAULT FALSE"),
        ("usuarios",   "planta",          "TEXT"),
        ("usuarios",   "super_admin",     "INTEGER DEFAULT 0" if not USE_POSTGRES else "BOOLEAN DEFAULT FALSE"),
        ("ubicaciones","planta",          "TEXT"),
        ("productos",  "descripcion",     "TEXT"),
        ("productos",  "planta",          "TEXT"),
        ("productos",  "updated_at",      "TEXT" if not USE_POSTGRES else "TIMESTAMPTZ"),
        ("productos",  "created_at",      "TEXT" if not USE_POSTGRES else "TIMESTAMPTZ"),
        ("productos",  "activo",          "INTEGER DEFAULT 1" if not USE_POSTGRES else "BOOLEAN DEFAULT TRUE"),
        ("movimientos","usuario_id",      "INTEGER"),
    ]
    for table, col, coldef in migrations:
        try:
            if USE_POSTGRES:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {coldef}")
            else:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    try:
        if USE_POSTGRES:
            cur.execute("UPDATE productos SET activo = TRUE WHERE activo IS NULL")
        else:
            cur.execute("UPDATE productos SET activo = 1 WHERE activo IS NULL")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    try:
        cur.execute("UPDATE productos SET planta = 'quilicura' WHERE planta IS NULL")
        cur.execute("UPDATE ubicaciones SET planta = 'quilicura' WHERE planta IS NULL")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _sincronizar_ubicaciones(conn):
    """Rellena el catalogo de ubicaciones con los valores de texto ya usados
    en productos.ubicacion, para que las ubicaciones existentes (Caja 1, etc.)
    aparezcan automaticamente sin tener que volver a escribirlas a mano."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT ubicacion FROM productos WHERE ubicacion IS NOT NULL AND ubicacion <> ''")
        nombres = [r["ubicacion"] if hasattr(r, "keys") else r[0] for r in cur.fetchall()]
        ph = p()
        for nombre in nombres:
            nombre = nombre.strip()
            if not nombre:
                continue
            if USE_POSTGRES:
                cur.execute(f"INSERT INTO ubicaciones (nombre) VALUES ({ph}) ON CONFLICT DO NOTHING", (nombre,))
            else:
                cur.execute(f"INSERT OR IGNORE INTO ubicaciones (nombre) VALUES ({ph})", (nombre,))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def init_db():
    """Inicializa tablas y carga seed si la BD esta vacia."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        ddl = _DDL_POSTGRES if USE_POSTGRES else _DDL_SQLITE
        for stmt in ddl:
            cur.execute(stmt)
        conn.commit()

        _run_migrations(conn)
        _sincronizar_ubicaciones(conn)

        cur.execute("SELECT COUNT(*) AS n FROM usuarios")
        row = cur.fetchone()
        n_usuarios = row["n"] if hasattr(row, "__getitem__") else row[0]
        if n_usuarios == 0:
            _seed_admin(cur)
            conn.commit()

        cur.execute("SELECT COUNT(*) AS n FROM productos")
        row = cur.fetchone()
        n_productos = row["n"] if hasattr(row, "__getitem__") else row[0]
        if n_productos == 0:
            _seed_data(cur)
            conn.commit()
            _sincronizar_ubicaciones(conn)

        try:
            cur.execute("UPDATE productos SET planta = 'quilicura' WHERE planta IS NULL")
            cur.execute("UPDATE ubicaciones SET planta = 'quilicura' WHERE planta IS NULL")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

        try:
            cur.execute("SELECT COUNT(*) AS n FROM usuarios WHERE super_admin = " + ("TRUE" if USE_POSTGRES else "1"))
            row = cur.fetchone()
            n_super = row["n"] if hasattr(row, "__getitem__") else row[0]
            if n_super == 0:
                cur.execute("SELECT id FROM usuarios WHERE role = 'admin' ORDER BY id ASC LIMIT 1")
                primero = cur.fetchone()
                if primero:
                    pid = primero["id"] if hasattr(primero, "__getitem__") else primero[0]
                    ph_local = p()
                    valor = True if USE_POSTGRES else 1
                    cur.execute(f"UPDATE usuarios SET super_admin = {ph_local} WHERE id = {ph_local}", (valor, pid))
                    conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    finally:
        conn.close()


def _seed_admin(cur):
    import bcrypt
    password_raw = os.environ.get("ADMIN_PASSWORD", "Wintec2024!")
    hashed = bcrypt.hashpw(password_raw.encode(), bcrypt.gensalt()).decode()
    ph = p()
    cur.execute(
        f"INSERT INTO usuarios (username, password, nombre, role) VALUES ({ph},{ph},{ph},{ph})",
        ("admin", hashed, "Administrador", "admin"),
    )


def _seed_data(cur):
    seed_file = os.path.join(BASE_DIR, "seed_data.json")
    if not os.path.exists(seed_file):
        return
    with open(seed_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    ph = p()
    is_pg = USE_POSTGRES

    def insert_ignore(table, cols, vals):
        ph_list = ", ".join([ph] * len(vals))
        if is_pg:
            cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph_list}) ON CONFLICT DO NOTHING", vals)
        else:
            cur.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({ph_list})", vals)

    for e in data.get("equipos", []):
        insert_ignore("equipos", "nombre", (e["nombre"],))

    for c in data.get("categorias", []):
        insert_ignore("categorias", "nombre", (c["nombre"],))

    for pv in data.get("proveedores", []):
        insert_ignore("proveedores", "nombre, contacto, email, telefono",
                      (pv.get("nombre"), pv.get("contacto"), pv.get("email"), pv.get("telefono")))

    for prod in data.get("productos", []):
        insert_ignore(
            "productos",
            "codigo,nombre,descripcion,categoria,equipo,linea,proveedor,stock_minimo,stock_actual,ubicacion,precio,imagen_url,foto",
            (prod.get("codigo"), prod.get("nombre"), prod.get("descripcion"),
             prod.get("categoria"), prod.get("equipo"), prod.get("linea"),
             prod.get("proveedor"), prod.get("stock_minimo", 0), prod.get("stock_actual", 0),
             prod.get("ubicacion"), prod.get("precio", 0), prod.get("imagen_url"), prod.get("foto"))
        )

    for m in data.get("movimientos", []):
        insert_ignore(
            "movimientos",
            "producto_id,tipo,cantidad,fecha,usuario,motivo",
            (m.get("producto_id"), m.get("tipo"), m.get("cantidad"),
             m.get("fecha"), m.get("usuario"), m.get("motivo"))
        )
