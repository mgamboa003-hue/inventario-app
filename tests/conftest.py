import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["DATABASE_URL"] = ""
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["DEBUG"] = "False"


@pytest.fixture()
def client():
    """Cliente de pruebas con una base de datos SQLite temporal y limpia."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "inventario_test.db")

    import db
    db.SQLITE_PATH = db_path

    import importlib
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    appmod.app.config["WTF_CSRF_ENABLED"] = False

    with appmod.app.test_client() as c:
        yield c


@pytest.fixture()
def admin_client(client):
    """Cliente ya autenticado como admin."""
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    return client
