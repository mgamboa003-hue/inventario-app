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


def test_correo_por_api_web(monkeypatch):
    """Con RESEND_API_KEY el correo sale por HTTPS (Railway bloquea SMTP)."""
    import services
    llamadas = []
    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    monkeypatch.setenv("EMAIL_FROM", "portal@wintec.cl")
    monkeypatch.setattr(services, "_post_json", lambda url, cab, datos, timeout=15: (llamadas.append((url, cab, datos)) or (200, "{}")))
    ok, msg = services.enviar_email(["tecnico@wintec.cl"], "Asunto", "<p>Hola</p>")
    assert ok, msg
    url, cab, datos = llamadas[0]
    assert url == "https://api.resend.com/emails"
    assert cab["Authorization"] == "Bearer re_prueba"
    assert datos["to"] == ["tecnico@wintec.cl"] and datos["from"].endswith("<portal@wintec.cl>")

    monkeypatch.setattr(services, "_post_json", lambda *a, **k: (403, '{"message":"domain not verified"}'))
    ok, msg = services.enviar_email(["x@wintec.cl"], "A", "<p>b</p>")
    assert not ok and "403" in msg and "domain not verified" in msg


def test_recuperar_clave_deja_registro_si_falla(client, monkeypatch):
    import services, app as appmod
    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    monkeypatch.setattr(services, "_post_json", lambda *a, **k: (500, "caido"))
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={"username": "tec_mail", "password": "clave123", "nombre": "Tec Mail",
                                              "role": "solicitante", "planta": "quilicura", "email": "tec@wintec.cl"})
    client.get("/logout")
    r = client.post("/olvide-password", data={"username": "tec_mail"})
    assert r.status_code == 302
    from db import get_db_connection
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT accion, detalle FROM auditoria WHERE accion LIKE 'reset_password%' ORDER BY id DESC")
    fila = cur.fetchone(); conn.close()
    assert fila["accion"] == "reset_password_fallo" and "500" in fila["detalle"]


def test_boton_probar_correo(admin_client, monkeypatch):
    import services
    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    monkeypatch.setattr(services, "_post_json", lambda *a, **k: (200, "{}"))
    body = admin_client.get("/admin/sistema").get_data(as_text=True)
    assert "Resend (API web)" in body
    r = admin_client.post("/admin/sistema/probar-correo", data={"destino": "yo@wintec.cl"}, follow_redirects=True)
    assert "Correo de prueba enviado" in r.get_data(as_text=True)
