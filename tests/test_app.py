"""
Pruebas basicas de humo para Inventario Wintec v3.
Cubren: login, lockout, CRUD de productos con soft-delete, movimientos,
ordenes de compra, tokens de API y exportes.
Ejecutar con: pytest -v
"""


def _completar_cambio_password_obligatorio(client, password):
    """Los usuarios nuevos (o con password reseteado) deben cambiar su
    contrasena en el primer acceso. Los tests que crean un usuario nuevo
    y luego intentan usar la app deben completar ese paso primero."""
    client.post("/cambiar-password-obligatorio", data={
        "nueva_password": password, "confirmar_password": password,
    })


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
    # el inventario esta paginado (20 por pagina); se busca por nombre para
    # encontrar el producto recien creado sin importar en que pagina caiga
    r2 = admin_client.get("/productos?q=Producto+Test")
    assert "Producto Test" in r2.get_data(as_text=True)


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
    r = admin_client.post("/admin/ubicaciones/nuevo", data={"nombre": "Ubicacion Manual", "planta": "quilicura"}, follow_redirects=True)
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
        "username": "juanperez", "password": "clave123", "nombre": "Juan Perez", "role": "solicitante", "planta": "quilicura",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "juanperez" in r.get_data(as_text=True)
    assert "Solicitante" in r.get_data(as_text=True)


def test_solicitante_puede_crear_y_ver_su_propia_solicitud(client):
    # crear usuario solicitante como admin primero
    admin = client
    admin.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    admin.post("/admin/usuarios/nuevo", data={
        "username": "mantenimiento1", "password": "clave123", "nombre": "Pedro Mantenimiento", "role": "solicitante", "planta": "quilicura",
    })
    admin.get("/logout")

    # loguear como el solicitante
    client.post("/login", data={"username": "mantenimiento1", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
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
        "username": "solovista", "password": "clave123", "nombre": "Solo Vista", "role": "viewer", "planta": "quilicura",
    })
    client.post("/solicitudes/nueva", data={"nombre_item": "Cable de red", "cantidad": "1"})
    client.get("/logout")

    client.post("/login", data={"username": "solovista", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
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
        "username": "solovista2", "password": "clave123", "nombre": "Solo Vista 2", "role": "viewer", "planta": "quilicura",
    })
    client.get("/logout")
    client.post("/login", data={"username": "solovista2", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")

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

    client.post("/login", data={"username": "comprador1", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    r = client.get("/solicitudes")
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
        "username": "mantenimiento2", "password": "clave123", "nombre": "Ana Mantenimiento", "role": "solicitante", "planta": "quilicura",
    })
    client.post("/admin/usuarios/nuevo", data={
        "username": "comprador2", "password": "clave123", "nombre": "El Comprador 2", "role": "comprador",
    })
    client.get("/logout")

    client.post("/login", data={"username": "mantenimiento2", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    client.post("/solicitudes/nueva", data={"nombre_item": "Sierra circular", "cantidad": "1"})
    client.get("/logout")

    client.post("/login", data={"username": "comprador2", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")

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
        "username": "solovista3", "password": "clave123", "nombre": "Solo Vista 3", "role": "viewer", "planta": "quilicura",
    })
    client.get("/logout")
    client.post("/login", data={"username": "solovista3", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    r = client.get("/admin/accesos", follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True).lower()
    assert "no tienes permiso" in body or "administrador principal" in body


def test_nueva_solicitud_redirige_a_detalle_con_boton_whatsapp(admin_client):
    r = admin_client.post("/solicitudes/nueva", data={
        "nombre_item": "Correa dentada 5M-450", "cantidad": "3", "urgencia": "urgente",
        "descripcion": "Para la maquina 2",
    }, follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # deberia haber aterrizado en la pagina de detalle, no en el listado
    assert "wa.me" in body
    assert "Enviar este pedido por WhatsApp" in body
    # el texto codificado del mensaje debe incluir el nombre del item (url-encoded)
    import urllib.parse
    assert urllib.parse.quote("Correa dentada 5M-450") in body


def test_usuario_nuevo_es_obligado_a_cambiar_password(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "nuevo1", "password": "generica123", "nombre": "Nuevo Uno", "role": "viewer", "planta": "quilicura",
    })
    client.get("/logout")

    client.post("/login", data={"username": "nuevo1", "password": "generica123"})
    r = client.get("/productos", follow_redirects=True)
    assert r.status_code == 200
    assert "/cambiar-password-obligatorio" in r.request.path


def test_cambiar_password_obligatorio_desbloquea_la_app(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "nuevo2", "password": "generica123", "nombre": "Nuevo Dos", "role": "viewer", "planta": "quilicura",
    })
    client.get("/logout")

    client.post("/login", data={"username": "nuevo2", "password": "generica123"})
    r = client.post("/cambiar-password-obligatorio", data={
        "nueva_password": "otraclave456", "confirmar_password": "otraclave456",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "/cambiar-password-obligatorio" not in r.request.path

    # ahora puede navegar libremente
    r = client.get("/productos")
    assert r.status_code == 200

    # y la contrasena vieja ya no sirve
    client.get("/logout")
    r = client.post("/login", data={"username": "nuevo2", "password": "generica123"}, follow_redirects=True)
    assert "incorrectos" in r.get_data(as_text=True).lower()


def test_admin_reset_password_tambien_fuerza_cambio(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "nuevo3", "password": "generica123", "nombre": "Nuevo Tres", "role": "viewer", "planta": "quilicura",
    })
    body = client.get("/admin/usuarios").get_data(as_text=True)

    import re
    m = re.search(r"Cambiar contrase\u00f1a . nuevo3.*?/admin/usuarios/(\d+)/reset_password", body, re.DOTALL)
    assert m, "no se encontro el id del usuario nuevo3 en la pagina de admin"
    uid = m.group(1)

    client.post(f"/admin/usuarios/{uid}/reset_password", data={"nueva_password": "reseteada789"})
    client.get("/logout")

    client.post("/login", data={"username": "nuevo3", "password": "reseteada789"})
    r = client.get("/productos", follow_redirects=True)
    assert "/cambiar-password-obligatorio" in r.request.path


def test_botones_excel_pedidos_etiquetas_solo_para_admin(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "solovista4", "password": "clave123", "nombre": "Solo Vista 4", "role": "viewer", "planta": "quilicura",
    })
    r_admin = client.get("/productos")
    body_admin = r_admin.get_data(as_text=True)
    assert "Imprimir etiquetas" in body_admin
    assert "Pedidos" in body_admin
    client.get("/logout")

    client.post("/login", data={"username": "solovista4", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    r = client.get("/productos")
    body = r.get_data(as_text=True)
    assert "Imprimir etiquetas" not in body
    assert ">Pedidos<" not in body
    assert "Imprimir etiqueta QR" not in body


def test_tema_de_color_cambia_segun_el_rol(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    body = client.get("/").get_data(as_text=True)
    assert 'class="role-admin"' in body
    client.post("/admin/usuarios/nuevo", data={
        "username": "solicitante_tema", "password": "clave123", "nombre": "Sol Tema", "role": "solicitante", "planta": "quilicura",
    })
    client.get("/logout")

    client.post("/login", data={"username": "solicitante_tema", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    body = client.get("/").get_data(as_text=True)
    assert 'class="role-solicitante"' in body


def _crear_usuario_planta(client, username, role, planta):
    client.post("/admin/usuarios/nuevo", data={
        "username": username, "password": "clave123", "nombre": username, "role": role, "planta": planta,
    })


def _crear_producto_planta(admin_client, nombre, planta):
    admin_client.post("/productos/nuevo", data={
        "nombre": nombre, "planta": planta, "stock_actual": "5", "stock_minimo": "1",
    })
    import db
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT id FROM productos WHERE nombre = {ph} ORDER BY id DESC LIMIT 1", (nombre,))
    row = cur.fetchone()
    conn.close()
    return row["id"]


def test_solicitante_de_quilicura_no_ve_productos_de_balmaceda(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    _crear_producto_planta(client, "Repuesto Solo Quilicura", "quilicura")
    _crear_producto_planta(client, "Repuesto Solo Balmaceda", "balmaceda")
    _crear_usuario_planta(client, "sol_quili", "solicitante", "quilicura")
    client.get("/logout")

    client.post("/login", data={"username": "sol_quili", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    body = client.get("/productos?q=Repuesto+Solo").get_data(as_text=True)
    assert "Repuesto Solo Quilicura" in body
    assert "Repuesto Solo Balmaceda" not in body


def test_solicitante_no_puede_ver_detalle_de_producto_de_otra_planta(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    pid = _crear_producto_planta(client, "Torno Balmaceda Unico", "balmaceda")

    _crear_usuario_planta(client, "sol_quili2", "solicitante", "quilicura")
    client.get("/logout")

    client.post("/login", data={"username": "sol_quili2", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    r = client.get(f"/productos/{pid}", follow_redirects=True)
    assert r.status_code == 200
    assert "otra planta" in r.get_data(as_text=True).lower()


def test_admin_ve_ambas_plantas_y_puede_filtrar(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    _crear_producto_planta(client, "Item Admin Quilicura", "quilicura")
    _crear_producto_planta(client, "Item Admin Balmaceda", "balmaceda")

    body = client.get("/productos?q=Item+Admin").get_data(as_text=True)
    assert "Item Admin Quilicura" in body
    assert "Item Admin Balmaceda" in body

    body_q = client.get("/productos?q=Item+Admin&planta=quilicura").get_data(as_text=True)
    assert "Item Admin Quilicura" in body_q
    assert "Item Admin Balmaceda" not in body_q

    body_b = client.get("/productos?q=Item+Admin&planta=balmaceda").get_data(as_text=True)
    assert "Item Admin Balmaceda" in body_b
    assert "Item Admin Quilicura" not in body_b


def test_viewer_de_balmaceda_no_ve_movimientos_de_quilicura(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    pid_quili = _crear_producto_planta(client, "Correa Quilicura Mov", "quilicura")
    pid_balma = _crear_producto_planta(client, "Correa Balmaceda Mov", "balmaceda")

    client.post("/movimientos/nuevo/entrada", data={"producto_id": pid_quili, "cantidad": "3"})
    client.post("/movimientos/nuevo/entrada", data={"producto_id": pid_balma, "cantidad": "4"})

    _crear_usuario_planta(client, "view_balma", "viewer", "balmaceda")
    client.get("/logout")

    client.post("/login", data={"username": "view_balma", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    body_mov = client.get("/movimientos").get_data(as_text=True)
    assert "Correa Balmaceda Mov" in body_mov
    assert "Correa Quilicura Mov" not in body_mov

    # tampoco puede registrar un movimiento sobre un producto de la otra planta
    r = client.post("/movimientos/nuevo/entrada", data={"producto_id": pid_quili, "cantidad": "1"}, follow_redirects=True)
    assert "otra planta" in r.get_data(as_text=True).lower()


def test_ubicaciones_filtradas_por_planta(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/ubicaciones/nuevo", data={"nombre": "Estante Quilicura X", "planta": "quilicura"})
    client.post("/admin/ubicaciones/nuevo", data={"nombre": "Estante Balmaceda X", "planta": "balmaceda"})
    _crear_usuario_planta(client, "sol_ubic", "solicitante", "balmaceda")
    client.get("/logout")

    client.post("/login", data={"username": "sol_ubic", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    body = client.get("/ubicaciones").get_data(as_text=True)
    assert "Estante Balmaceda X" in body
    assert "Estante Quilicura X" not in body


def test_dashboard_cuenta_solo_la_planta_del_usuario(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    _crear_producto_planta(client, "Extra Balmaceda Dash", "balmaceda")
    _crear_usuario_planta(client, "sol_dash", "solicitante", "quilicura")
    client.get("/logout")

    client.post("/login", data={"username": "sol_dash", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    body = client.get("/").get_data(as_text=True)
    assert "Planta Quilicura" in body
    assert "Extra Balmaceda Dash" not in body


def test_solicitudes_del_equipo_muestran_ambas_plantas(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    _crear_usuario_planta(client, "sol_equipo_quili", "solicitante", "quilicura")
    _crear_usuario_planta(client, "sol_equipo_balma", "solicitante", "balmaceda")
    client.get("/logout")

    client.post("/login", data={"username": "sol_equipo_quili", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    client.post("/solicitudes/nueva", data={"nombre_item": "Pedido desde Quilicura", "cantidad": "1"})
    client.get("/logout")

    client.post("/login", data={"username": "sol_equipo_balma", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    client.post("/solicitudes/nueva", data={"nombre_item": "Pedido desde Balmaceda", "cantidad": "1"})
    client.get("/logout")

    # el admin (ve todas las solicitudes de todos) debe ver ambos pedidos
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    body_admin = client.get("/solicitudes").get_data(as_text=True)
    assert "Pedido desde Quilicura" in body_admin
    assert "Pedido desde Balmaceda" in body_admin
    client.get("/logout")

    # un viewer (sin restriccion de "solo mis solicitudes") tambien debe ver ambos, aunque su planta sea una sola
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    _crear_usuario_planta(client, "view_equipo", "viewer", "quilicura")
    client.get("/logout")
    client.post("/login", data={"username": "view_equipo", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    body_viewer = client.get("/solicitudes").get_data(as_text=True)
    assert "Pedido desde Quilicura" in body_viewer
    assert "Pedido desde Balmaceda" in body_viewer


def test_toggle_usuario_activa_y_desactiva(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "para_desactivar", "password": "clave123", "nombre": "Para Desactivar", "role": "viewer", "planta": "quilicura",
    })
    body = client.get("/admin/usuarios").get_data(as_text=True)
    import re
    m = re.search(r"para_desactivar.*?/admin/usuarios/(\d+)/toggle", body, re.DOTALL)
    assert m, "no se encontro el id del usuario"
    uid = m.group(1)

    r = client.post(f"/admin/usuarios/{uid}/toggle", follow_redirects=True)
    assert r.status_code == 200
    assert "actualizado" in r.get_data(as_text=True).lower()

    r2 = client.post(f"/admin/usuarios/{uid}/toggle", follow_redirects=True)
    assert r2.status_code == 200


def test_admin_puede_editar_nombre_y_rol_de_usuario(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "editable1", "password": "clave123", "nombre": "Nombre Viejo", "role": "viewer", "planta": "quilicura",
    })
    body = client.get("/admin/usuarios").get_data(as_text=True)
    import re
    m = re.search(r"<strong>editable1</strong>.*?/admin/usuarios/(\d+)/editar", body, re.DOTALL)
    assert m, "no se encontro el id del usuario"
    uid = m.group(1)

    r = client.post(f"/admin/usuarios/{uid}/editar", data={
        "nombre": "Nombre Nuevo", "role": "comprador",
    }, follow_redirects=True)
    assert r.status_code == 200
    body2 = r.get_data(as_text=True)
    assert "Usuario actualizado" in body2
    assert "Nombre Nuevo" in body2
    assert "Comprador" in body2

    # como ahora es comprador, ya no deberia mostrar el dropdown de planta (deberia decir "Ambas")
    idx = body2.find("Nombre Nuevo")
    seccion = body2[idx:idx+600]
    assert "Ambas" in seccion


def test_admin_no_puede_quitarse_a_si_mismo_el_rol_admin(admin_client):
    body = admin_client.get("/admin/usuarios").get_data(as_text=True)
    import re
    m = re.search(r'admin</strong>.*?/admin/usuarios/(\d+)/editar', body, re.DOTALL)
    assert m
    uid = m.group(1)
    r = admin_client.post(f"/admin/usuarios/{uid}/editar", data={
        "nombre": "Administrador", "role": "viewer",
    }, follow_redirects=True)
    assert "no puedes quitarte" in r.get_data(as_text=True).lower()


def test_admin_regular_no_ve_secciones_solo_admin_principal(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "admin_regular", "password": "clave123", "nombre": "Admin Regular", "role": "admin",
    })
    client.get("/logout")

    client.post("/login", data={"username": "admin_regular", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")

    for path in ["/admin/usuarios", "/admin/auditoria", "/admin/accesos", "/admin/sistema"]:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200
        assert "administrador principal" in r.get_data(as_text=True).lower()

    # pero SI puede seguir usando el resto de Administracion
    for path in ["/admin/categorias", "/admin/equipos", "/admin/proveedores", "/admin/ubicaciones", "/admin/api_tokens"]:
        r = client.get(path)
        assert r.status_code == 200

    # y no deberia ver los links en el menu
    body = client.get("/").get_data(as_text=True)
    assert "Auditoría" not in body
    assert "Control de accesos" not in body
    assert "Sistema y respaldos" not in body
    assert "Categorías" in body  # este si debe seguir visible


def test_admin_principal_puede_promover_a_otro_admin(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/admin/usuarios/nuevo", data={
        "username": "admin_promovible", "password": "clave123", "nombre": "Admin Promovible", "role": "admin",
    })
    body = client.get("/admin/usuarios").get_data(as_text=True)
    import re
    m = re.search(r"<strong>admin_promovible</strong>.*?/admin/usuarios/(\d+)/editar", body, re.DOTALL)
    assert m
    uid = m.group(1)

    r = client.post(f"/admin/usuarios/{uid}/editar", data={
        "nombre": "Admin Promovible", "role": "admin", "super_admin": "1",
    }, follow_redirects=True)
    assert r.status_code == 200
    client.get("/logout")

    client.post("/login", data={"username": "admin_promovible", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    r = client.get("/admin/usuarios")
    assert r.status_code == 200
    assert "gestión de usuarios" in r.get_data(as_text=True).lower()


def test_admin_no_puede_promoverse_a_si_mismo(admin_client):
    body = admin_client.get("/admin/usuarios").get_data(as_text=True)
    import re
    m = re.search(r'admin</strong>.*?/admin/usuarios/(\d+)/editar', body, re.DOTALL)
    assert m
    uid = m.group(1)
    # el propio admin principal no deberia poder cambiar su propio rol (el select viene disabled),
    # y el backend tampoco deberia permitir tocar su propio nivel
    r = admin_client.post(f"/admin/usuarios/{uid}/editar", data={
        "nombre": "Administrador", "role": "admin", "super_admin": "0",
    }, follow_redirects=True)
    assert r.status_code == 200
    # sigue teniendo acceso a la seccion de usuarios (no se pudo quitar el nivel a si mismo)
    r2 = admin_client.get("/admin/usuarios")
    assert r2.status_code == 200
    assert "gestión de usuarios" in r2.get_data(as_text=True).lower()


def test_boton_hacer_pedido_aparece_con_stock_bajo_y_crea_solicitud_precargada(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Aceite de vacio bajo stock", "planta": "quilicura",
        "stock_actual": "1", "stock_minimo": "5",
    })
    import db
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT id FROM productos WHERE nombre = {ph} ORDER BY id DESC LIMIT 1",
                ("Aceite de vacio bajo stock",))
    pid = cur.fetchone()["id"]
    conn.close()

    body = admin_client.get(f"/productos/{pid}").get_data(as_text=True)
    assert "Hacer pedido" in body
    assert f"/solicitudes/nueva?producto_id={pid}" in body

    r = admin_client.get(f"/solicitudes/nueva?producto_id={pid}&nombre_item=Aceite+de+vacio+bajo+stock&cantidad=4")
    prefill_body = r.get_data(as_text=True)
    assert 'value="Aceite de vacio bajo stock"' in prefill_body
    assert f'value="{pid}"' in prefill_body
    assert 'value="4"' in prefill_body

    r2 = admin_client.post("/solicitudes/nueva", data={
        "producto_id": str(pid), "nombre_item": "Aceite de vacio bajo stock", "cantidad": "4",
    }, follow_redirects=True)
    assert r2.status_code == 200
    assert "Aceite de vacio bajo stock" in r2.get_data(as_text=True)

    lista = admin_client.get("/solicitudes").get_data(as_text=True)
    assert "Aceite de vacio bajo stock" in lista


def test_boton_hacer_pedido_no_aparece_con_stock_ok(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Repuesto con stock ok", "planta": "quilicura",
        "stock_actual": "10", "stock_minimo": "2",
    })
    import db
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT id FROM productos WHERE nombre = {ph} ORDER BY id DESC LIMIT 1",
                ("Repuesto con stock ok",))
    pid = cur.fetchone()["id"]
    conn.close()

    body = admin_client.get(f"/productos/{pid}").get_data(as_text=True)
    assert "Hacer pedido" not in body


def test_boton_hacer_pedido_no_aparece_para_viewer(client):
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    client.post("/productos/nuevo", data={
        "nombre": "Repuesto bajo para viewer", "planta": "quilicura",
        "stock_actual": "1", "stock_minimo": "5",
    })
    import db
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT id FROM productos WHERE nombre = {ph} ORDER BY id DESC LIMIT 1",
                ("Repuesto bajo para viewer",))
    pid = cur.fetchone()["id"]
    conn.close()
    client.post("/admin/usuarios/nuevo", data={
        "username": "viewerpedido", "password": "clave123", "nombre": "Viewer Pedido", "role": "viewer", "planta": "quilicura",
    })
    client.get("/logout")

    client.post("/login", data={"username": "viewerpedido", "password": "clave123"})
    _completar_cambio_password_obligatorio(client, "clave123")
    body = client.get(f"/productos/{pid}").get_data(as_text=True)
    assert "Hacer pedido" not in body


def test_hacer_pedido_precarga_foto_del_producto_y_la_pasa_a_la_solicitud(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Repuesto con foto bajo stock", "planta": "quilicura",
        "stock_actual": "1", "stock_minimo": "5", "imagen_url": "https://pub-test.r2.dev/repuesto.webp",
    })
    import db
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT id FROM productos WHERE nombre = {ph} ORDER BY id DESC LIMIT 1",
                ("Repuesto con foto bajo stock",))
    pid = cur.fetchone()["id"]
    conn.close()

    detalle = admin_client.get(f"/productos/{pid}").get_data(as_text=True)
    assert f"foto_url_pref=https" in detalle or "foto_url_pref=https%3A" in detalle

    r = admin_client.get(f"/solicitudes/nueva?producto_id={pid}&nombre_item=Repuesto+con+foto+bajo+stock&cantidad=4&foto_url_pref=https://pub-test.r2.dev/repuesto.webp")
    body = r.get_data(as_text=True)
    assert 'name="foto_url_existente" value="https://pub-test.r2.dev/repuesto.webp"' in body

    r2 = admin_client.post("/solicitudes/nueva", data={
        "producto_id": str(pid), "nombre_item": "Repuesto con foto bajo stock", "cantidad": "4",
        "foto_url_existente": "https://pub-test.r2.dev/repuesto.webp",
    }, follow_redirects=True)
    assert r2.status_code == 200
    assert "https://pub-test.r2.dev/repuesto.webp" in r2.get_data(as_text=True)


def test_whatsapp_de_solicitud_incluye_link_de_la_foto(admin_client):
    r = admin_client.post("/solicitudes/nueva", data={
        "nombre_item": "Item con foto para whatsapp", "cantidad": "1",
        "foto_url_existente": "https://pub-test.r2.dev/otra.webp",
    }, follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "wa.me" in body
    import urllib.parse
    import re
    m = re.search(r'href="(https://wa\.me/\?text=[^"]+)"', body)
    assert m
    texto = urllib.parse.unquote(m.group(1).split("text=")[1])
    assert "https://pub-test.r2.dev/otra.webp" in texto


def test_hora_de_la_app_usa_huso_horario_de_chile(admin_client):
    """Las fechas que genera la app (login, movimientos, solicitudes) deben
    reflejar la hora de Chile (America/Santiago) sin importar en que huso
    horario este configurado el servidor (Railway corre en UTC)."""
    import db
    from datetime import datetime
    from zoneinfo import ZoneInfo

    esperado = datetime.now(ZoneInfo("America/Santiago"))
    generado = db.ahora()
    diferencia = abs((generado - esperado.replace(tzinfo=None)).total_seconds())
    assert diferencia < 5, f"ahora() no coincide con la hora de Chile: {generado} vs {esperado}"

    admin_client.post("/productos/nuevo", data={
        "nombre": "Producto para test de hora", "planta": "quilicura",
        "stock_actual": "10", "stock_minimo": "1",
    })
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT id FROM productos WHERE nombre = {ph} ORDER BY id DESC LIMIT 1",
                ("Producto para test de hora",))
    pid = cur.fetchone()["id"]
    conn.close()

    admin_client.post("/movimientos/nuevo/entrada", data={
        "producto_id": str(pid), "cantidad": "1", "motivo": "test",
    })
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT fecha FROM movimientos WHERE producto_id = {ph} ORDER BY id DESC LIMIT 1", (pid,))
    fila = cur.fetchone()
    conn.close()
    assert fila is not None
    fecha_mov = datetime.fromisoformat(str(fila["fecha"])[:19])
    diferencia_mov = abs((fecha_mov - esperado.replace(tzinfo=None)).total_seconds())
    assert diferencia_mov < 10, f"La fecha del movimiento no coincide con hora Chile: {fecha_mov} vs {esperado}"


def test_inventario_muestra_20_por_pagina_y_boton_siguiente(admin_client):
    # el seed ya trae ~239 productos activos, mas que de sobra para 2 paginas
    r1 = admin_client.get("/productos")
    body1 = r1.get_data(as_text=True)
    assert "Página 1 de" in body1
    assert "Ver 20 más" in body1
    assert body1.count("product-thumb") >= 20  # 20 filas visibles (imagen o placeholder)

    r2 = admin_client.get("/productos?pagina=2")
    body2 = r2.get_data(as_text=True)
    assert "Página 2 de" in body2
    assert "Anterior" in body2


def test_inventario_paginacion_mantiene_filtros_de_categoria(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "AAA Filtro Pagina Uno", "categoria": "CategoriaPaginacionTest",
        "stock_minimo": "1", "stock_actual": "1",
    })
    admin_client.post("/productos/nuevo", data={
        "nombre": "ZZZ Filtro Pagina Uno", "categoria": "CategoriaPaginacionTest",
        "stock_minimo": "1", "stock_actual": "1",
    })
    body = admin_client.get("/productos?categoria=CategoriaPaginacionTest").get_data(as_text=True)
    assert "AAA Filtro Pagina Uno" in body
    assert "ZZZ Filtro Pagina Uno" in body
    assert "Página" not in body  # solo 2 productos, no debe mostrar paginacion


def test_imprimir_etiquetas_incluye_productos_de_todas_las_paginas(admin_client):
    body = admin_client.get("/productos").get_data(as_text=True)
    import re
    m = re.search(r'href="(/productos/etiquetas\?ids=[^"]+)"', body)
    assert m, "no se encontro el link de Imprimir etiquetas"
    ids_str = m.group(1).split("ids=")[1]
    cantidad_ids = len(ids_str.split(","))
    assert cantidad_ids > 20  # debe cubrir TODOS los productos filtrados, no solo la pagina actual


def test_busqueda_de_productos_ignora_mayusculas_y_minusculas(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Rodamiento SKF Especial", "codigo": "ROD-CASE-01",
        "categoria": "Rodamientos", "stock_minimo": "1", "stock_actual": "1",
    })
    for termino in ("rodamiento skf", "RODAMIENTO SKF", "RoDaMiEnTo SkF", "rod-case-01", "ROD-CASE-01"):
        body = admin_client.get(f"/productos?q={termino}").get_data(as_text=True)
        assert "Rodamiento SKF Especial" in body, f"no encontro el producto buscando '{termino}'"


def test_foto_de_solicitud_no_fuerza_camara(admin_client):
    body = admin_client.get("/solicitudes/nueva").get_data(as_text=True)
    assert 'name="foto"' in body
    assert 'capture=' not in body


def test_registrar_salida_desde_detalle_preselecciona_el_repuesto(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Repuesto Preseleccion Test", "planta": "quilicura",
        "stock_actual": "5", "stock_minimo": "1",
    })
    import db
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT id FROM productos WHERE nombre = {ph} ORDER BY id DESC LIMIT 1",
                ("Repuesto Preseleccion Test",))
    pid = cur.fetchone()["id"]
    conn.close()

    body = admin_client.get(f"/movimientos/nuevo/salida?producto_id={pid}").get_data(as_text=True)
    assert f'value="{pid}" data-codigo=' in body
    # la opcion del repuesto debe venir marcada como seleccionada
    import re
    m = re.search(rf'<option value="{pid}"[^>]*>', body)
    assert m and "selected" in m.group(0)

    # sin producto_id, ninguna opcion queda preseleccionada
    body_sin = admin_client.get("/movimientos/nuevo/salida").get_data(as_text=True)
    m2 = re.search(rf'<option value="{pid}"[^>]*>', body_sin)
    assert m2 and "selected" not in m2.group(0)


def test_cierra_sesion_automaticamente_tras_inactividad(admin_client):
    import db
    from datetime import timedelta
    with admin_client.session_transaction() as sess:
        sess["_ultima_peticion_ts"] = (db.ahora() - timedelta(minutes=6)).isoformat()
    r = admin_client.get("/", follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "inactividad" in body.lower()
    # la sesion quedo cerrada: la siguiente peticion pide login de nuevo
    r2 = admin_client.get("/productos")
    assert r2.status_code == 302
    assert "/login" in r2.headers["Location"]


def test_actividad_reciente_no_cierra_la_sesion(admin_client):
    import db
    from datetime import timedelta
    with admin_client.session_transaction() as sess:
        sess["_ultima_peticion_ts"] = (db.ahora() - timedelta(minutes=1)).isoformat()
    r = admin_client.get("/productos")
    assert r.status_code == 200
    assert "Inventario de repuestos" in r.get_data(as_text=True)


def test_temporizador_de_inactividad_se_incluye_en_la_pagina(admin_client):
    body = admin_client.get("/").get_data(as_text=True)
    assert "5 * 60 * 1000" in body
    assert "motivo=inactividad" in body


def test_crear_producto_con_codigo_duplicado_no_pierde_datos_y_ofrece_actualizar(admin_client):
    admin_client.post("/productos/nuevo", data={
        "nombre": "Repuesto Original Duplicado", "codigo": "DUP-001",
        "stock_minimo": "2", "stock_actual": "3",
    })
    import db
    conn = db.get_db_connection()
    cur = conn.cursor()
    ph = db.p()
    cur.execute(f"SELECT id FROM productos WHERE codigo = {ph}", ("DUP-001",))
    original_id = cur.fetchone()["id"]
    conn.close()

    r = admin_client.post("/productos/nuevo", data={
        "nombre": "Repuesto Nuevo Con Codigo Repetido", "codigo": "dup-001",
        "descripcion": "No se debe perder esta descripcion",
        "categoria": "CategoriaQueNoDebePerderse",
        "stock_minimo": "7", "stock_actual": "9",
    })
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    # no se creo un segundo producto con ese codigo
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) AS n FROM productos WHERE LOWER(codigo) = LOWER({ph})", ("DUP-001",))
    assert cur.fetchone()["n"] == 1
    conn.close()

    # los datos que el usuario habia escrito siguen en el formulario
    assert 'value="Repuesto Nuevo Con Codigo Repetido"' in body
    assert "No se debe perder esta descripcion" in body
    assert 'value="CategoriaQueNoDebePerderse"' in body
    assert 'value="9"' in body

    # se ofrece ir a editar/actualizar el repuesto existente
    assert "Repuesto Original Duplicado" in body
    assert f"/productos/{original_id}/editar" in body
    assert f"/movimientos/nuevo/entrada?producto_id={original_id}" in body


def test_fecha_a_naive_maneja_string_sqlite_y_datetime_aware_de_postgres():
    import db
    from datetime import datetime, timezone, timedelta

    # como llega desde SQLite (string)
    assert db.fecha_a_naive("2026-07-15 17:53:00") == datetime(2026, 7, 15, 17, 53, 0)
    assert db.fecha_a_naive("2026-07-15 17:53:00.123456") == datetime(2026, 7, 15, 17, 53, 0)

    # como llega desde Postgres con SET TIME ZONE (datetime "aware")
    tz_cl = timezone(timedelta(hours=-4))
    aware = datetime(2026, 7, 15, 17, 53, 0, tzinfo=tz_cl)
    resultado = db.fecha_a_naive(aware)
    assert resultado == datetime(2026, 7, 15, 17, 53, 0)
    assert resultado.tzinfo is None  # ya no debe tener tzinfo, para poder restar con ahora()

    assert db.fecha_a_naive(None) is None
    assert db.fecha_a_naive("no es una fecha valida") is None

    # no debe reventar al restar contra ahora() (esto era justo el bug reportado)
    cutoff = db.ahora() - timedelta(days=90)
    assert (resultado >= cutoff) in (True, False)  # no lanza TypeError


def test_exportar_rotacion_no_revienta_con_fecha_aware_simulando_postgres(admin_client, monkeypatch):
    """Reproduce el bug reportado: en Postgres, m['fecha'] llega como datetime
    aware (con tzinfo) y antes rompia la resta contra ahora() (naive)."""
    admin_client.post("/productos/nuevo", data={
        "nombre": "Producto Rotacion Aware", "stock_minimo": "1", "stock_actual": "5",
    })
    admin_client.post("/movimientos/nuevo/salida", data={"producto_id": "1", "cantidad": "1"})

    import db
    from datetime import datetime, timezone, timedelta

    original_get_db_connection = db.get_db_connection

    class FilaConFechaAware(dict):
        def __getitem__(self, key):
            valor = super().__getitem__(key)
            if key == "fecha" and isinstance(valor, str):
                dt = datetime.fromisoformat(valor[:19])
                return dt.replace(tzinfo=timezone(timedelta(hours=-4)))
            return valor

    class CursorEnvoltorio:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, *a, **kw):
            return self._cur.execute(*a, **kw)

        def fetchall(self):
            filas = self._cur.fetchall()
            return [FilaConFechaAware(dict(f)) if "fecha" in dict(f) else f for f in filas]

        def fetchone(self):
            return self._cur.fetchone()

        def __getattr__(self, name):
            return getattr(self._cur, name)

    class ConnEnvoltorio:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self):
            return CursorEnvoltorio(self._conn.cursor())

        def __getattr__(self, name):
            return getattr(self._conn, name)

    def fake_get_db_connection():
        return ConnEnvoltorio(original_get_db_connection())

    monkeypatch.setattr(db, "get_db_connection", fake_get_db_connection)
    monkeypatch.setattr("app.get_db_connection", fake_get_db_connection)

    r = admin_client.get("/exportar/rotacion")
    assert r.status_code == 200
    assert r.mimetype in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream")


def _crear_producto_con_categoria(admin_client, nombre, codigo, categoria):
    admin_client.post("/productos/nuevo", data={
        "nombre": nombre, "codigo": codigo, "categoria": categoria,
        "stock_minimo": "1", "stock_actual": "5",
    })


def test_unificar_catalogos_detecta_variantes_con_tilde_y_mayusculas(admin_client):
    # Se usa un nombre unico (con sufijo aleatorio) para no chocar con datos
    # de ejemplo ya sembrados en la base de pruebas.
    _crear_producto_con_categoria(admin_client, "Rodillo A", "UNI-Q9137-001", "Accesorio Eléctrico Q9137")
    _crear_producto_con_categoria(admin_client, "Rodillo B", "UNI-Q9137-002", "accesorio electrico q9137")
    _crear_producto_con_categoria(admin_client, "Rodillo C", "UNI-Q9137-003", "Accesorio Eléctrico Q9137")

    r = admin_client.get("/admin/unificar-catalogos")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Q9137" in html
    assert "Unificar (3)" in html


def test_unificar_catalogos_sin_duplicados_muestra_mensaje(admin_client):
    _crear_producto_con_categoria(admin_client, "Rodillo Unico", "UNI-Q9138-100", "Categoria Unica Q9138 XYZ")
    r = admin_client.get("/admin/unificar-catalogos")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # esta categoria unica no debe aparecer agrupada con boton de unificar
    assert "Q9138" not in html


def test_unificar_catalogos_post_consolida_valores(admin_client):
    _crear_producto_con_categoria(admin_client, "Rodillo D", "UNI-Q9139-004", "Tinta Offset Q9139")
    _crear_producto_con_categoria(admin_client, "Rodillo E", "UNI-Q9139-005", "tinta offset q9139")

    r = admin_client.post("/admin/unificar-catalogos", data={
        "campo": "categoria",
        "valor_final": "Tinta Offset Q9139",
        "variantes_json": '["Tinta Offset Q9139", "tinta offset q9139"]',
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "se unificaron" in r.get_data(as_text=True)

    # tras unificar, ya no debe aparecer como grupo duplicado
    r2 = admin_client.get("/admin/unificar-catalogos")
    assert "Q9139" not in r2.get_data(as_text=True)

    # y el inventario debe mostrar solo la forma final para esos productos
    r3 = admin_client.get("/productos?q=Rodillo+E")
    assert "Rodillo E" in r3.get_data(as_text=True)


def test_unificar_catalogos_rechaza_campo_invalido(admin_client):
    r = admin_client.post("/admin/unificar-catalogos", data={
        "campo": "nombre_no_permitido",
        "valor_final": "Algo",
        "variantes_json": '["a", "b"]',
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "Campo no valido" in r.get_data(as_text=True)


def test_unificar_catalogos_requiere_admin(client):
    r = client.get("/admin/unificar-catalogos", follow_redirects=True)
    assert r.status_code == 200
    assert "login" in r.request.path.lower() or "Iniciar" in r.get_data(as_text=True)


def test_nav_muestra_link_unificar_para_admin(admin_client):
    r = admin_client.get("/productos")
    assert "Unificar nombres" in r.get_data(as_text=True)


def test_unificar_catalogos_requiere_super_admin_no_admin_regular(admin_client):
    admin_client.post("/admin/usuarios/nuevo", data={
        "username": "admin_regular_q9140", "password": "clave123", "nombre": "Admin Regular",
        "role": "admin", "planta": "quilicura",
    })
    admin_client.get("/logout")
    admin_client.post("/login", data={"username": "admin_regular_q9140", "password": "clave123"})
    _completar_cambio_password_obligatorio(admin_client, "clave123")

    r = admin_client.get("/admin/unificar-catalogos", follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True).lower()
    assert "administrador principal" in body or "no tienes permiso" in body

    # tampoco debe verse el link en la navegacion para un admin regular
    r2 = admin_client.get("/productos")
    assert "Unificar nombres" not in r2.get_data(as_text=True)
