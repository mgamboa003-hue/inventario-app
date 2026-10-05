"""
restaurar_respaldo.py -- Restaura un respaldo del Portal Mantenimiento Wintec.

Los respaldos automaticos (todas las noches a las 3:00) quedan en el bucket
S3/R2, carpeta backups-db/, como inventario_AAAAMMDD_HHMMSS.sql.gz. Tambien
se pueden descargar desde Administracion > Sistema y respaldos.

Uso (en una base NUEVA y vacia; la base de destino sale de DATABASE_URL,
o el archivo inventario.db local si DATABASE_URL no esta definida):

    python restaurar_respaldo.py inventario_20261005_030000.sql.gz

Si la base de destino ya tiene datos, el script se detiene para no
mezclar informacion. Para BORRAR lo que tenga y reemplazarlo por el
respaldo (por ejemplo, al volver atras un error grave):

    python restaurar_respaldo.py respaldo.sql.gz --reemplazar

Al terminar muestra cuantas filas quedaron en cada tabla, para compararlas
con el resumen al inicio del archivo de respaldo.
"""

import gzip
import sys

from db import (TABLAS_RESPALDO, USE_POSTGRES, crear_estructura, get_db_connection)


def leer_respaldo(ruta):
    with open(ruta, "rb") as f:
        crudo = f.read()
    if crudo[:2] == b"\x1f\x8b":          # comprimido con gzip
        crudo = gzip.decompress(crudo)
    return crudo.decode("utf-8")


def contar(cur, tabla):
    try:
        cur.execute(f"SELECT COUNT(*) AS n FROM {tabla}")
        fila = cur.fetchone()
        return fila["n"] if hasattr(fila, "keys") else fila[0]
    except Exception:
        return None


def ajustar_secuencias(conn):
    """En PostgreSQL los ids se generan con una secuencia. Al insertar filas
    con su id original la secuencia queda atras y el proximo registro nuevo
    chocaria con uno existente: se adelanta al id mas alto de cada tabla."""
    cur = conn.cursor()
    for tabla in TABLAS_RESPALDO:
        try:
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id') AS s", (tabla,))
            fila = cur.fetchone()
            secuencia = fila["s"] if fila else None
            if not secuencia:
                continue
            cur.execute(f"SELECT COALESCE(MAX(id), 0) AS m FROM {tabla}")
            maximo = cur.fetchone()["m"]
            if maximo > 0:
                cur.execute("SELECT setval(%s, %s, true)", (secuencia, maximo))
            else:
                cur.execute("SELECT setval(%s, 1, false)", (secuencia,))
            conn.commit()
        except Exception:
            conn.rollback()


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 1
    ruta = argv[1]
    reemplazar = "--reemplazar" in argv

    sql = leer_respaldo(ruta)
    print(f"Respaldo: {ruta}")
    print(f"Destino : {'PostgreSQL (DATABASE_URL)' if USE_POSTGRES else 'SQLite local (inventario.db)'}")

    conn = get_db_connection()
    try:
        crear_estructura(conn)
        cur = conn.cursor()
        con_datos = {t: n for t in TABLAS_RESPALDO if (n := contar(cur, t))}
        if con_datos and not reemplazar:
            print("\nLa base de destino YA tiene datos:")
            for t, n in con_datos.items():
                print(f"  {t}: {n}")
            print("\nNo se restauro nada. Usa una base nueva, o agrega --reemplazar para borrar")
            print("estos datos y dejar solo los del respaldo.")
            return 2
        if con_datos:
            print("\nBorrando los datos actuales de la base de destino...")
            for t in reversed(TABLAS_RESPALDO):
                try:
                    cur.execute(f"DELETE FROM {t}")
                    conn.commit()
                except Exception:
                    conn.rollback()

        print("Cargando el respaldo...")
        if USE_POSTGRES:
            cur.execute(sql)
            conn.commit()
            ajustar_secuencias(conn)
        else:
            conn.executescript(sql)
            conn.commit()

        print("\nListo. Filas por tabla:")
        cur = conn.cursor()
        for t in TABLAS_RESPALDO:
            n = contar(cur, t)
            if n:
                print(f"  {t}: {n}")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"\nERROR: no se pudo restaurar ({e}). La base de destino quedo sin cambios del respaldo.")
        return 3
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
