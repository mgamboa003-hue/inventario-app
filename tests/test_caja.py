"""Pruebas del modulo de caja chica."""
import io

import pytest
from PIL import Image

import caja_logica as cl

TED = ('<TED version="1.0"><DD><RE>76354771-K</RE><TD>33</TD><F>48213</F><FE>2026-11-17</FE>'
       '<RR>96512340-7</RR><RSR>WINTEC S.A.</RSR><MNT>119000</MNT><IT1>RODAMIENTO 6204 2RS</IT1>'
       '<CAF version="1.0"><DA><RE>76354771-K</RE><RS>FERRETERIA EL PERNO LTDA</RS><TD>33</TD>'
       '<RNG><D>1</D><H>100000</H></RNG><FA>2025-01-10</FA><RSAPK><M>' + 'A' * 170 + '</M><E>Aw==</E>'
       '</RSAPK><IDK>100</IDK></DA><FRMA algoritmo="SHA1withRSA">' + 'B' * 88 + '</FRMA></CAF>'
       '<TSTED>2026-11-17T10:22:31</TSTED></DD><FRMT algoritmo="SHA1withRSA">' + 'C' * 88 + '</FRMT></TED>')


def _foto_con_timbre(texto=TED, angulo=3):
    """Simula la foto de una factura: timbre PDF417 sobre una hoja, algo girada."""
    zxingcpp = pytest.importorskip("zxingcpp")
    bc = zxingcpp.create_barcode(texto, zxingcpp.BarcodeFormat.PDF417)
    im = bc.to_image(scale=3)
    im = im if isinstance(im, Image.Image) else Image.fromarray(im)
    hoja = Image.new("L", (2000, 2600), 255)
    hoja.paste(im.convert("L"), (150, 1900))
    hoja = hoja.rotate(angulo, fillcolor=235)
    buf = io.BytesIO()
    hoja.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ── Logica pura ──────────────────────────────────────────────────
def test_rut():
    assert cl.rut_valido("76.354.771-k")
    assert cl.rut_valido("96512340-7")
    assert not cl.rut_valido("76354771-1")
    assert cl.formatear_rut("76354771k") == "76.354.771-K"


def test_montos():
    assert cl.a_entero("$1.250.000") == 1250000
    assert cl.a_entero("1.250.000,50") == 1250000
    assert cl.a_entero("") is None
    assert cl.clp(1250000) == "$1.250.000"
    assert cl.clp(-500) == "-$500"


def test_parsear_ted():
    d = cl.parsear_ted(TED)
    assert d["rut_emisor"] == "76354771-K"
    assert d["tipo_doc"] == "33" and d["folio"] == "48213"
    assert d["fecha_doc"] == "2026-11-17" and d["monto"] == 119000
    assert d["proveedor"] == "FERRETERIA EL PERNO LTDA"
    assert cl.parsear_ted("hola") is None


def test_cuadre_y_nota_de_credito():
    gastos = [{"tipo_doc": "33", "monto": 300000}, {"tipo_doc": "39", "monto": 50000},
              {"tipo_doc": "61", "monto": 10000}]
    c = cl.cuadre(1000000, gastos)
    assert c["total_documentos"] == 340000 and c["disponible"] == 660000 and c["cuadra"] is None
    assert cl.cuadre(1000000, gastos, 660000)["cuadra"] is True
    c = cl.cuadre(1000000, gastos, 650000)
    assert c["diferencia"] == 10000 and c["cuadra"] is False


def test_lee_timbre_de_foto_girada():
    d = cl.leer_timbre(_foto_con_timbre())
    assert d and d["folio"] == "48213" and d["monto"] == 119000


def test_foto_sin_timbre():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "JPEG")
    assert cl.leer_timbre(buf.getvalue()) is None


# ── Flujo completo ───────────────────────────────────────────────
def _crear_usuario(client, username, role):
    client.post("/admin/usuarios/nuevo", data={"username": username, "password": "clave123", "nombre": username.title(),
                                               "role": role, "planta": "quilicura"})


def _entrar(client, username):
    client.get("/logout")
    client.post("/login", data={"username": username, "password": "clave123"})
    client.post("/cambiar-password-obligatorio", data={"nueva_password": "NuevaClave123!",
                                                        "confirmar_password": "NuevaClave123!"})


def _entrar_admin(client):
    client.get("/logout")
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})


def _id_comprador(client, username):
    import db
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM usuarios WHERE username = ?", (username,))
        return cur.fetchone()["id"]
    finally:
        conn.close()


def _nueva_entrega(client, comprador_id, monto="1.000.000"):
    r = client.post("/caja/entregas/nueva", data={"comprador_id": comprador_id, "monto": monto, "fecha": "2026-11-18"})
    assert r.status_code == 302
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def _gasto(client, eid, **kw):
    datos = {"tipo_doc": "33", "folio": "100", "rut_emisor": "96512340-7", "proveedor": "Proveedor X",
             "fecha_doc": "2026-11-18", "monto": "100.000", "categoria": "Repuestos", "motivo": "test"}
    datos.update(kw)
    return client.post(f"/caja/entregas/{eid}/gastos/nuevo", data=datos, follow_redirects=True)


def test_flujo_entrega_rendicion_aprobacion(admin_client):
    c = admin_client
    _crear_usuario(c, "comprador1", "comprador")
    cid = _id_comprador(c, "comprador1")
    eid = _nueva_entrega(c, cid)

    assert "Proveedor X" in _gasto(c, eid).get_data(as_text=True)
    # el mismo documento no se puede rendir dos veces
    body = _gasto(c, eid, monto="5.000").get_data(as_text=True)
    assert "ya está rendido" in body
    _gasto(c, eid, folio="101", monto="250.000")
    _gasto(c, eid, tipo_doc="VALE", folio="", rut_emisor="", monto="2.000", motivo="Estacionamiento")

    # el comprador ve su entrega, rinde con diferencia: exige observacion
    _entrar(c, "comprador1")
    assert c.get(f"/caja/entregas/{eid}").status_code == 200
    c.post(f"/caja/entregas/{eid}/rendir", data={"saldo_devuelto": "640.000"})
    assert "Abierta" in c.get(f"/caja/entregas/{eid}").get_data(as_text=True)
    c.post(f"/caja/entregas/{eid}/rendir", data={"saldo_devuelto": "640.000", "obs_cierre": "Faltan 8 mil"})
    body = c.get(f"/caja/entregas/{eid}").get_data(as_text=True)
    assert "Rendida" in body and "$8.000" in body
    # ya rendida: no se agregan documentos
    assert "no se pueden agregar" in _gasto(c, eid, folio="999").get_data(as_text=True)

    _entrar_admin(c)
    assert "rendición" in c.get("/").get_data(as_text=True).lower()
    c.post(f"/caja/entregas/{eid}/aprobar", data={"obs_aprobacion": "ok"})
    assert "Aprobada" in c.get(f"/caja/entregas/{eid}").get_data(as_text=True)

    # Exportaciones
    r = c.get(f"/caja/entregas/{eid}/pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    r = c.get(f"/caja/entregas/{eid}/excel")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    for fmt, firma in (("pdf", b"%PDF"), ("excel", b"PK")):
        r = c.get(f"/caja/reporte/{fmt}?desde=2026-11-01&hasta=2026-11-30")
        assert r.status_code == 200 and r.data[:len(firma)] == firma


def test_cuadre_perfecto(admin_client):
    c = admin_client
    _crear_usuario(c, "comprador3", "comprador")
    eid = _nueva_entrega(c, _id_comprador(c, "comprador3"), "200.000")
    _gasto(c, eid, folio="555", monto="150.000")
    c.post(f"/caja/entregas/{eid}/rendir", data={"saldo_devuelto": "50.000"})
    body = c.get(f"/caja/entregas/{eid}").get_data(as_text=True)
    assert "Rendida" in body and "cuadra" in body


def test_leer_documento_con_foto(admin_client):
    c = admin_client
    r = c.post("/caja/leer-documento", data={"foto": (io.BytesIO(_foto_con_timbre()), "f.jpg")},
               content_type="multipart/form-data")
    j = r.get_json()
    assert j["ok"] and j["leido"] and j["datos"]["folio"] == "48213"
    assert j["datos"]["proveedor"] == "FERRETERIA EL PERNO LTDA"
    assert cl.referencia_valida(j["foto"])
    # la foto queda guardada y se puede ver con sesion
    assert c.get(f"/caja/foto?ref={j['foto']}").status_code == 200

    _crear_usuario(c, "comprador4", "comprador")
    eid = _nueva_entrega(c, _id_comprador(c, "comprador4"))
    d = j["datos"]
    _gasto(c, eid, tipo_doc=d["tipo_doc"], folio=d["folio"], rut_emisor=d["rut_emisor"],
           proveedor="Ferretería El Perno", monto=str(d["monto"]), foto=j["foto"], origen="timbre")
    body = c.get(f"/caja/entregas/{eid}").get_data(as_text=True)
    assert "Timbre SII" in body and "$119.000" in body
    # segunda lectura del mismo documento: avisa que esta repetido y recuerda el nombre
    j2 = c.post("/caja/leer-documento", data={"foto": (io.BytesIO(_foto_con_timbre()), "f.jpg"), "guardar": "0"},
                content_type="multipart/form-data").get_json()
    assert "ya está rendido" in j2["repetido"] and j2["foto"] is None
    assert j2["datos"]["proveedor"] == "Ferretería El Perno"
    # PDF con anexo de fotos
    assert c.get(f"/caja/entregas/{eid}/pdf").data[:4] == b"%PDF"


def test_permisos(admin_client):
    c = admin_client
    _crear_usuario(c, "compA", "comprador")
    _crear_usuario(c, "compB", "comprador")
    _crear_usuario(c, "tecnico9", "solicitante")
    eid = _nueva_entrega(c, _id_comprador(c, "compA"))

    _entrar(c, "compB")
    assert c.get(f"/caja/entregas/{eid}").status_code == 404
    assert c.get(f"/caja/entregas/{eid}/pdf").status_code == 404
    assert c.post("/caja/entregas/nueva", data={"comprador_id": 1, "monto": "10"}).status_code == 302
    assert "Caja chica" in c.get("/caja/").get_data(as_text=True)

    _entrar(c, "tecnico9")
    assert c.get("/caja/").status_code == 302
    assert c.get("/caja/foto?ref=local:x.webp").status_code == 302


def test_area_en_el_portal(admin_client):
    body = admin_client.get("/").get_data(as_text=True)
    assert "Caja chica" in body
    body = admin_client.get("/caja/").get_data(as_text=True)
    assert "06 · CAJA CHICA" in body
