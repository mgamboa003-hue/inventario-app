"""Pruebas de la estructura del Portal Mantenimiento Wintec."""


def test_bienvenida_muestra_las_areas(admin_client):
    body = admin_client.get("/").get_data(as_text=True)
    for texto in ("Requiere atención", "Bodega", "Compras", "Personal · Turnos", "Reportes Excel", "Administración"):
        assert texto in body, texto
    assert 'class="sidebar' not in body          # ya no hay barra lateral
    assert "data-set-modo" in body and "data-set-color" in body   # selector de apariencia


def test_pantallas_nuevas_responden(admin_client):
    for ruta in ("/bodega", "/reportes", "/escanear", "/productos", "/turnos"):
        assert admin_client.get(ruta).status_code == 200, ruta


def test_pestanas_del_area_en_cada_pantalla(admin_client):
    body = admin_client.get("/turnos/cambios").get_data(as_text=True)
    assert "03 · PERSONAL · TURNOS" in body
    assert "Correo de ingreso" in body and "Planificación" in body
    body = admin_client.get("/admin/ubicaciones").get_data(as_text=True)
    assert "05 · ADMINISTRACIÓN" in body


def test_reportes_solo_para_admin(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "tec_portal", "password": "clave123", "nombre": "Tec Portal", "role": "solicitante", "planta": "quilicura",
    })
    client.get("/logout")
    client.post("/login", data={"username": "tec_portal", "password": "clave123"})
    client.post("/cambiar-password-obligatorio", data={"password": "NuevaClave123!", "password2": "NuevaClave123!",
                                                        "nueva": "NuevaClave123!", "confirmar": "NuevaClave123!"})
    assert client.get("/reportes").status_code == 302
    body = client.get("/", follow_redirects=True).get_data(as_text=True)
    assert "Reportes Excel" not in body
