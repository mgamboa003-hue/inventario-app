"""
backup_db.py — Respaldo automático de la base de datos.

Uso manual:
    python backup_db.py

Uso programado (Windows Task Scheduler), diario a las 23:00:
    schtasks /create /tn "Backup Inventario Wintec" /tr "C:\\ruta\\venv\\Scripts\\python.exe C:\\ruta\\backup_db.py" /sc daily /st 23:00

Guarda copias con timestamp en la carpeta backups/ y elimina las copias
más antiguas, dejando solo las últimas N (por defecto 14).
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
MANTENER = int(os.environ.get("BACKUPS_A_MANTENER", 14))

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def backup_sqlite():
    origen = os.path.join(BASE_DIR, "inventario.db")
    if not os.path.exists(origen):
        print("No se encontró inventario.db, nada que respaldar.")
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(BACKUP_DIR, f"inventario_{timestamp}.db")
    shutil.copy2(origen, destino)
    print(f"Backup SQLite creado: {destino}")
    return destino


def backup_postgres():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(BACKUP_DIR, f"inventario_{timestamp}.sql")
    try:
        with open(destino, "wb") as f:
            subprocess.run(["pg_dump", DATABASE_URL], stdout=f, check=True)
        print(f"Backup Postgres creado: {destino}")
        return destino
    except FileNotFoundError:
        print("pg_dump no está instalado en este entorno. Usa el backup automático de Railway/Render, "
              "o instala postgresql-client para respaldos manuales.")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Error ejecutando pg_dump: {e}")
        return None


def limpiar_backups_antiguos():
    if not os.path.isdir(BACKUP_DIR):
        return
    archivos = sorted(
        (os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)),
        key=os.path.getmtime,
        reverse=True,
    )
    for viejo in archivos[MANTENER:]:
        try:
            os.remove(viejo)
            print(f"Backup antiguo eliminado: {viejo}")
        except OSError:
            pass


if __name__ == "__main__":
    if DATABASE_URL.startswith("postgres"):
        backup_postgres()
    else:
        backup_sqlite()
    limpiar_backups_antiguos()
