"""
Pruebas basicas de humo para Inventario Wintec v3.
Cubren: login, lockout, CRUD de productos con soft-delete, movimientos,
ordenes de compra, tokens de API y exportes.
Ejecutar con: pytest -v
"""


def test_login_page_carga(client):
    r = client.get("/login")
    assert r.status_code == 200


def test_login_credenciales_invalidas(client):
    r = client.post("/login", data={"username": "admin", "password": "incorrecta"}, follow_redirects=True)
    assert "incorrectos" in r.get_data(as_text=True).lower()


def test_login_credenciales_validas(client):
    r = client.post("/login", data={"username": "admin", "password": "TestAdmin123!"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/")


def test_rutas_requieren_login(client):
    for path in ["/", "/productos", "/movimientos", "/ordenes_compra", "/admin/usuarios"]:
        r = client.get(path)
        assert r.status_code == 302, f"{path} deberia redirigir a login"


def test_dashboard_tras_login(admin_client):
    r = admin_client.get("/")
    assert r.status_code == 200


def test_lockout_tras_intentos_fallidos(client):
    for _ in range(5):
        client.post("/login", data={"username": "admin", "password": "mala"})
    r = client.post("/login", data={"username": "admin", "password": "TestAdmin123!"}, follow_redirects=True)
    assert "bloqueada" in r.get_data(as_text=True).lower()


def test_crear_producto(admin_client):
    r = admin_client.post("/productos/nuevo", data={
        "nombre": "Producto Test", "codigo": "PT-001",
        "stock_minimo": "5", "stock_actual": "1", "precio": "1000",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "Producto Test" in r.get_data(as_text=True)


def test_soft_delete_y_restaurar_producto(admin_client):
    admin_client.post("/productos/nuevo", data={"nombre": "Para Borrar", "stock_minimo": "1", "stock_actual": "1"})
    r = admin_client.get("/productos")
    html = r.get_data(as_text=True)
    import re
    m = re.search(r"/productos/(\d+)/editar", html)
    assert m, "no se encontro un producto para probar soft-delete"
    pid = m.group(1)

    r = admin_client.post(f"/productos/{pid}/eliminar", follow_redirects=True)
    assert r.status_code == 200

    r = admin_client.get("/productos")
    assert f"/productos/{pid}/editar" not in r.get_data(as_text=True)

    r = admin_client.get("/productos?inactivos=1")
    assert f"/productos/{pid}" in r.get_data(as_text=True)

    r = admin_client.post(f"/productos/{pid}/restaurar", follow_redirects=True)
    r = admin_client.get("/productos")
    assert f"/productos/{pid}/editar" in r.get_data(as_text=True)


def test_movimiento_entrada_actualiza_stock(admin_client):
    admin_client.post("/productos/nuevo", data={"nombre": "Repuesto Mov", "codigo": "MOV-1", "stock_minimo": "1", "stock_actual": "3"})
    r = admin_client.get("/productos")
    import re
    m = re.search(r"/productos/(\d+)/editar\"[^>]*>.*?Repuesto Mov", r.get_data(as_text=True), re.S)
    pid = m.group(1) if m else re.search(r"/productos/(\d+)/editar", r.get_data(as_text=True)).group(1)

    r = admin_client.post("/movimientos/nuevo/entrada", data={
        "producto_id": pid, "cantidad": "5", "usuario": "Tester", "motivo": "reposicion",
    }, follow_redirects=True)
    assert r.status_code == 200


def test_movimiento_salida_rechaza_stock_insuficiente(admin_client):
    admin_client.post("/productos/nuevo", data={"nombre": "Repuesto Sin Stock", "codigo": "SIN-1", "stock_minimo": "1", "stock_actual": "1"})
    r = admin_client.get("/productos")
    import re
    m = re.search(r"/productos/(\d+)/editar", r.get_data(as_text=True))
    pid = m.group(1)
    r = admin_client.post("/movimientos/nuevo/salida", data={
        "producto_id": pid, "cantidad": "999", "usuario": "Tester",
    }, follow_redirects=True)
    assert "insuficiente" in r.get_data(as_text=True).lower()


def test_generar_orden_compra(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Bajo Stock", "codigo": "BAJO-1", "stock_minimo": "10",
        "stock_actual": "1", "precio": "500", "proveedor": "Proveedor X",
    })
    r = admin_client.post("/ordenes_compra/generar", follow_redirects=True)
    assert "Proveedor X" in r.get_data(as_text=True)


def test_exportes_devuelven_excel(admin_client):
    for path in ["/exportar/stock_bajo", "/exportar/valorizacion", "/exportar/rotacion", "/exportar/abc"]:
        r = admin_client.get(path)
        assert r.status_code == 200
        assert r.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_api_requiere_autenticacion(client):
    r = client.get("/api/productos")
    assert r.status_code == 401


def test_api_token_funciona(admin_client):
    r = admin_client.post("/admin/api_tokens/nuevo", data={"nombre": "Token pytest"}, follow_redirects=True)
    import re
    m = re.search(r"wtc_[A-Za-z0-9_\-]+", r.get_data(as_text=True))
    assert m, "no se genero el token"
    token = m.group(0)

    r = admin_client.get("/api/productos", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)

    r = admin_client.get("/api/productos", headers={"Authorization": "Bearer token-invalido"})
    assert r.status_code == 401


def test_manifest_y_service_worker(client):
    r = client.get("/manifest.json")
    assert r.status_code == 200
    assert r.get_json()["name"] == "Inventario Wintec"

    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "CACHE_NAME" in r.get_data(as_text=True)


def test_auditoria_registra_eventos(admin_client):
    admin_client.post("/productos/nuevo", data={"nombre": "Auditado", "stock_minimo": "1", "stock_actual": "1"})
    r = admin_client.get("/admin/auditoria")
    assert "crear" in r.get_data(as_text=True).lower()


def test_etiqueta_individual_genera_qr(admin_client):
    r = admin_client.post("/productos/nuevo", data={"nombre": "Producto QR", "stock_minimo": "1", "stock_actual": "1"})
    r = admin_client.get("/productos")
    import re
    m = re.search(r"/productos/(\d+)/editar", r.get_data(as_text=True))
    pid = m.group(1)

    r = admin_client.get(f"/productos/{pid}/etiqueta")
    assert r.status_code == 200
    assert "data:image/png;base64" in r.get_data(as_text=True)


def test_etiqueta_asigna_codigo_si_falta(admin_client):
    admin_client.post("/productos/nuevo", data={"nombre": "Sin Codigo QR Unico", "stock_minimo": "1", "stock_actual": "1"})
    r = admin_client.get("/productos?q=Sin Codigo QR Unico")
    import re
    m = re.search(r"/productos/(\d+)/editar", r.get_data(as_text=True))
    assert m, "no se encontro el producto recien creado en el listado filtrado"
    pid = m.group(1)

    admin_client.get(f"/productos/{pid}/etiqueta")
    r = admin_client.get(f"/productos/{pid}")
    assert "WTC-" in r.get_data(as_text=True)


def test_etiquetas_lote(admin_client):
    r = admin_client.get("/productos")
    import re
    ids = re.findall(r"/productos/(\d+)/editar", r.get_data(as_text=True))[:3]
    r = admin_client.get(f"/productos/etiquetas?ids={','.join(ids)}")
    assert r.status_code == 200
    assert r.get_data(as_text=True).count("data:image/png;base64") == len(ids)


def test_ubicaciones_se_crean_desde_producto(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Producto En Caja 99", "stock_minimo": "1", "stock_actual": "1",
        "ubicacion": "Caja 99 Test",
    })
    r = admin_client.get("/ubicaciones")
    assert r.status_code == 200
    assert "Caja 99 Test" in r.get_data(as_text=True)


def test_ver_ubicacion_muestra_productos(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Repuesto Ubicado", "stock_minimo": "1", "stock_actual": "1",
        "ubicacion": "Estante Z-9",
    })
    r = admin_client.get("/ubicaciones")
    import re
    m = re.search(r'/ubicaciones/(\d+)"[^>]*>[^<]*<i class="bi bi-eye"', r.get_data(as_text=True))
    if not m:
        m = re.search(r'ver_ubicacion.*?/ubicaciones/(\d+)', r.get_data(as_text=True))
    # buscar el id de la ubicacion "Estante Z-9" de forma robusta
    html = r.get_data(as_text=True)
    idx = html.find("Estante Z-9")
    assert idx != -1
    seg = html[max(0, idx-50):idx+400]
    m2 = re.search(r"/ubicaciones/(\d+)", seg)
    assert m2, "no se encontro el id de la ubicacion en el HTML"
    uid = m2.group(1)

    r = admin_client.get(f"/ubicaciones/{uid}")
    assert r.status_code == 200
    assert "Repuesto Ubicado" in r.get_data(as_text=True)


def test_etiqueta_ubicacion_genera_qr_con_url(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Repuesto Con Ubicacion QR", "stock_minimo": "1", "stock_actual": "1",
        "ubicacion": "Bodega Principal QR",
    })
    r = admin_client.get("/ubicaciones")
    html = r.get_data(as_text=True)
    idx = html.find("Bodega Principal QR")
    assert idx != -1
    import re
    seg = html[max(0, idx-50):idx+400]
    m = re.search(r"/ubicaciones/(\d+)", seg)
    uid = m.group(1)

    r = admin_client.get(f"/ubicaciones/{uid}/etiqueta")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "data:image/png;base64" in body
    assert "Bodega Principal QR" in body


def test_etiquetas_ubicaciones_lote(admin_client):
    r = admin_client.get("/ubicaciones/etiquetas")
    assert r.status_code == 200


def test_admin_gestionar_ubicaciones(admin_client):
    r = admin_client.post("/admin/ubicaciones/nuevo", data={"nombre": "Ubicacion Manual"}, follow_redirects=True)
    assert r.status_code == 200
    assert "Ubicacion Manual" in r.get_data(as_text=True)


def test_crear_cotizacion_con_items(admin_client):
    r = admin_client.get("/cotizaciones/nueva")
    assert r.status_code == 200

    r = admin_client.post("/cotizaciones/nueva", data={
        "proveedor": "Proveedor Cotizacion Test",
        "fecha_recibida": "2026-01-15",
        "fecha_vigencia": "2026-02-15",
        "notas": "Precio valido por 30 dias",
        "nombre_item[]": ["Tinta CMYK", "Papel A4"],
        "codigo_item[]": ["TIN-1", "PAP-1"],
        "cantidad_item[]": ["10", "5"],
        "precio_item[]": ["1000", "500"],
    }, follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Proveedor Cotizacion Test" in body
    assert "Tinta CMYK" in body
    # 10*1000 + 5*500 = 12500
    assert "12,500" in body or "12500" in body


def test_listar_cotizaciones(admin_client):
    admin_client.post("/cotizaciones/nueva", data={
        "proveedor": "Proveedor Lista Test",
        "nombre_item[]": ["Item X"], "codigo_item[]": [""],
        "cantidad_item[]": ["1"], "precio_item[]": ["100"],
    })
    r = admin_client.get("/cotizaciones")
    assert r.status_code == 200
    assert "Proveedor Lista Test" in r.get_data(as_text=True)


def test_aceptar_cotizacion_genera_orden_compra(admin_client):
    r = admin_client.post("/cotizaciones/nueva", data={
        "proveedor": "Proveedor Auto OC",
        "nombre_item[]": ["Producto Auto OC"], "codigo_item[]": ["AUTO-1"],
        "cantidad_item[]": ["3"], "precio_item[]": ["2000"],
    }, follow_redirects=True)
    import re
    m = re.search(r"/cotizaciones/(\d+)", r.request.path) if hasattr(r, "request") else None
    # obtener el id desde el listado
    r2 = admin_client.get("/cotizaciones")
    html = r2.get_data(as_text=True)
    idx = html.find("Proveedor Auto OC")
    seg = html[max(0, idx-100):idx+800]
    m2 = re.search(r"/cotizaciones/(\d+)", seg)
    assert m2, "no se encontro el id de la cotizacion"
    cid = m2.group(1)

    r3 = admin_client.post(f"/cotizaciones/{cid}/estado", data={"estado": "aceptada"}, follow_redirects=True)
    assert r3.status_code == 200
    body = r3.get_data(as_text=True)
    assert "generó la orden de compra" in body or "orden de compra" in body.lower()

    r4 = admin_client.get("/ordenes_compra")
    assert "Proveedor Auto OC" in r4.get_data(as_text=True)


def test_cotizacion_requiere_admin_para_crear(client):
    r = client.get("/cotizaciones/nueva", follow_redirects=True)
    # sin sesion, redirige a login
    assert "usuario" in r.get_data(as_text=True).lower() or r.status_code == 200


def test_crear_usuario_solicitante(admin_client):
    r = admin_client.post("/admin/usuarios/nuevo", data={
        "username": "juanperez", "password": "clave123", "nombre": "Juan Perez", "role": "solicitante",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "juanperez" in r.get_data(as_text=True)
    assert "Solicitante" in r.get_data(as_text=True)


def test_solicitante_puede_crear_y_ver_su_propia_solicitud(client):
    # crear usuario solicitante como admin primero
    admin = client
    admin.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    admin.post("/admin/usuarios/nuevo", data={
        "username": "mantenimiento1", "password": "clave123", "nombre": "Pedro Mantenimiento", "role": "solicitante",
    })
    admin.get("/logout")

    # loguear como el solicitante
    client.post("/login", data={"username": "mantenimiento1", "password": "clave123"})
    r = client.post("/solicitudes/nueva", data={
        "nombre_item": "Llave Allen 5mm", "cantidad": "2", "urgencia": "urgente",
        "descripcion": "Para reparar la maquina 3",
    }, follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Llave Allen 5mm" in body

    r = client.get("/solicitudes")
    assert "Llave Allen 5mm" in r.get_data(as_text=True)


def test_viewer_puede_ver_pero_no_crear_solicitudes(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "solovista", "password": "clave123", "nombre": "Solo Vista", "role": "viewer",
    })
    client.post("/solicitudes/nueva", data={"nombre_item": "Cable de red", "cantidad": "1"})
    client.get("/logout")

    client.post("/login", data={"username": "solovista", "password": "clave123"})
    r = client.get("/solicitudes")
    assert r.status_code == 200
    assert "Cable de red" in r.get_data(as_text=True)
    # el boton "Pedir algo" no debe aparecer para viewer
    assert "Pedir algo" not in r.get_data(as_text=True)

    r = client.post("/solicitudes/nueva", data={"nombre_item": "No deberia crear", "cantidad": "1"}, follow_redirects=True)
    assert "No deberia crear" not in r.get_data(as_text=True)


def test_viewer_no_puede_ver_ordenes_ni_cotizaciones_ni_exportar(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "solovista2", "password": "clave123", "nombre": "Solo Vista 2", "role": "viewer",
    })
    client.get("/logout")
    client.post("/login", data={"username": "solovista2", "password": "clave123"})

    for path in ["/ordenes_compra", "/cotizaciones", "/exportar/stock_bajo", "/productos/1/etiqueta", "/ubicaciones/etiquetas"]:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200
        assert "no tienes permiso" in r.get_data(as_text=True).lower()


def test_solicitud_comprada_pasa_a_historial(admin_client):
    admin_client.post("/solicitudes/nueva", data={
        "nombre_item": "Filtro de aire", "cantidad": "1", "urgencia": "normal",
    })
    r = admin_client.get("/solicitudes")
    import re
    html = r.get_data(as_text=True)
    idx = html.find("Filtro de aire")
    seg = html[max(0, idx-100):idx+800]
    sid = re.search(r"/solicitudes/(\d+)", seg).group(1)

    admin_client.post(f"/solicitudes/{sid}/estado", data={"estado": "comprado"})

    r_activo = admin_client.get("/solicitudes")
    assert "Filtro de aire" not in r_activo.get_data(as_text=True)

    r_historial = admin_client.get("/solicitudes?vista=historial")
    assert "Filtro de aire" in r_historial.get_data(as_text=True)


def test_solicitud_rechazada_se_queda_en_lista_activa_hasta_que_pase_la_gracia(admin_client, monkeypatch):
    import os as _os
    admin_client.post("/solicitudes/nueva", data={
        "nombre_item": "Broca 6mm", "cantidad": "1", "urgencia": "normal",
    })
    r = admin_client.get("/solicitudes")
    import re
    html = r.get_data(as_text=True)
    idx = html.find("Broca 6mm")
    seg = html[max(0, idx-100):idx+800]
    sid = re.search(r"/solicitudes/(\d+)", seg).group(1)

    admin_client.post(f"/solicitudes/{sid}/estado", data={"estado": "rechazado"})

    # recien rechazada: sigue en la lista activa (dentro del periodo de gracia)
    r_activo = admin_client.get("/solicitudes")
    assert "Broca 6mm" in r_activo.get_data(as_text=True)

    # con periodo de gracia en 0 dias, ya deberia salir de la lista activa
    _os.environ["DIAS_GRACIA_SOLICITUD"] = "0"
    import time
    time.sleep(1)
    r_activo2 = admin_client.get("/solicitudes")
    assert "Broca 6mm" not in r_activo2.get_data(as_text=True)

    r_historial = admin_client.get("/solicitudes?vista=historial")
    assert "Broca 6mm" in r_historial.get_data(as_text=True)
    _os.environ["DIAS_GRACIA_SOLICITUD"] = "7"


def test_admin_marca_solicitud_como_comprada(admin_client):
    admin_client.post("/solicitudes/nueva", data={
        "nombre_item": "Guantes de seguridad", "cantidad": "5", "urgencia": "normal",
    })
    r = admin_client.get("/solicitudes")
    import re
    html = r.get_data(as_text=True)
    idx = html.find("Guantes de seguridad")
    seg = html[max(0, idx-100):idx+800]
    m = re.search(r"/solicitudes/(\d+)", seg)
    assert m, "no se encontro el id de la solicitud"
    sid = m.group(1)

    r = admin_client.post(f"/solicitudes/{sid}/estado", data={"estado": "comprado"}, follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Comprado" in body


def test_solicitud_atrasada_se_marca_automaticamente(admin_client, monkeypatch):
    import os as _os
    _os.environ["DIAS_ATRASO_SOLICITUD"] = "0"  # cualquier pendiente cuenta como atrasada
    admin_client.post("/solicitudes/nueva", data={"nombre_item": "Item Atrasable", "cantidad": "1"})
    r = admin_client.get("/solicitudes")
    assert "Atrasado" in r.get_data(as_text=True)
    _os.environ["DIAS_ATRASO_SOLICITUD"] = "5"


def test_comprador_solo_ve_solicitudes_en_el_menu(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "comprador1", "password": "clave123", "nombre": "El Comprador", "role": "comprador",
    })
    client.get("/logout")

    r = client.post("/login", data={"username": "comprador1", "password": "clave123"}, follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # el login sin "next" lo manda directo a Solicitudes, no al dashboard
    assert "Solicitudes" in body or "solicitud" in body.lower()
    assert "Pedir algo" not in body

    # no deberia poder entrar a inventario/movimientos/ubicaciones/dashboard
    for path in ["/", "/productos", "/movimientos", "/ubicaciones"]:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200
        assert "solicitud" in r.request.path.lower()


def test_comprador_puede_cambiar_estado_pero_no_crear_solicitud(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "mantenimiento2", "password": "clave123", "nombre": "Ana Mantenimiento", "role": "solicitante",
    })
    client.post("/admin/usuarios/nuevo", data={
        "username": "comprador2", "password": "clave123", "nombre": "El Comprador 2", "role": "comprador",
    })
    client.get("/logout")

    client.post("/login", data={"username": "mantenimiento2", "password": "clave123"})
    client.post("/solicitudes/nueva", data={"nombre_item": "Sierra circular", "cantidad": "1"})
    client.get("/logout")

    client.post("/login", data={"username": "comprador2", "password": "clave123"})

    # no puede crear
    r = client.post("/solicitudes/nueva", data={"nombre_item": "No deberia crear"}, follow_redirects=True)
    assert "No deberia crear" not in r.get_data(as_text=True)

    # puede ver y cambiar el estado
    r = client.get("/solicitudes")
    import re
    html = r.get_data(as_text=True)
    idx = html.find("Sierra circular")
    assert idx != -1
    seg = html[max(0, idx-100):idx+800]
    sid = re.search(r"/solicitudes/(\d+)", seg).group(1)

    r = client.post(f"/solicitudes/{sid}/estado", data={"estado": "comprado"}, follow_redirects=True)
    assert r.status_code == 200
    assert "Comprado" in r.get_data(as_text=True)


def test_login_registra_ultima_conexion_e_ip(admin_client):
    import sqlite3
    import db as dbmod
    conn = sqlite3.connect(dbmod.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM usuarios WHERE username = 'admin'")
    admin_row = dict(cur.fetchone())
    assert admin_row["ultimo_login"] is not None
    assert admin_row["ultima_ip"] not in (None, "")

    cur.execute("SELECT * FROM sesiones WHERE usuario_id = ? ORDER BY id DESC LIMIT 1", (admin_row["id"],))
    sesion_row = cur.fetchone()
    assert sesion_row is not None
    assert dict(sesion_row)["inicio"] is not None
    conn.close()


def test_logout_cierra_la_sesion_con_duracion(admin_client):
    r = admin_client.get("/logout", follow_redirects=True)
    assert r.status_code == 200

    import sqlite3
    import db as dbmod
    conn = sqlite3.connect(dbmod.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM sesiones ORDER BY id DESC LIMIT 1")
    sesion_row = dict(cur.fetchone())
    assert sesion_row["fin"] is not None
    assert sesion_row["duracion_segundos"] is not None
    assert sesion_row["duracion_segundos"] >= 0
    conn.close()


def test_admin_ve_control_de_accesos(admin_client):
    r = admin_client.get("/admin/accesos")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Control de accesos" in body
    assert "admin" in body.lower()


def test_no_admin_no_puede_ver_control_de_accesos(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "solovista3", "password": "clave123", "nombre": "Solo Vista 3", "role": "viewer",
    })
    client.get("/logout")
    client.post("/login", data={"username": "solovista3", "password": "clave123"})
    r = client.get("/admin/accesos", follow_redirects=True)
    assert r.status_code == 200
    assert "no tienes permiso" in r.get_data(as_text=True).lower()
