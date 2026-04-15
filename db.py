# db.py
import sqlite3
import os

DB_NAME = "inventario.db"


def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crea las tablas si no existen."""
    conn = get_db_connection()
    cur = conn.cursor()

        # Tabla de productos
    cur.execute(
        """
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
            imagen_url TEXT
        );
        """


    )


    # Tabla de movimientos
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            tipo TEXT NOT NULL, -- 'entrada' o 'salida'
            cantidad INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            usuario TEXT,
            motivo TEXT,
            FOREIGN KEY(producto_id) REFERENCES productos(id)
        );
        """
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    # Para crear la base de datos manualmente si quieres:
    if not os.path.exists(DB_NAME):
        init_db()
        print("Base de datos creada.")
    else:
        print("La base de datos ya existe.")
