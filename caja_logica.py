"""
caja_logica.py -- Logica del modulo de Caja Chica (sin Flask).

  - Tablas (caja_entregas, caja_gastos, caja_emisores).
  - Cuadre: entregado - documentos - saldo devuelto = diferencia (debe dar 0).
  - Lectura del timbre electronico del SII (codigo PDF417 de facturas y
    boletas electronicas): RUT emisor, tipo de documento, folio, fecha y monto.
  - Guardado PRIVADO de las fotos de los documentos (S3/R2 si esta
    configurado, si no una carpeta local fuera de /static). Las fotos solo se
    ven con sesion iniciada, a traves de /caja/foto/<id>.
"""

import io
import os
import re
import uuid

from db import USE_POSTGRES, BASE_DIR, p

# ── Tipos de documento (codigos del SII) ─────────────────────────
TIPOS_DOC = {
    "33": "Factura electrónica",
    "34": "Factura exenta",
    "39": "Boleta electrónica",
    "41": "Boleta exenta",
    "46": "Factura de compra",
    "52": "Guía de despacho",
    "56": "Nota de débito",
    "61": "Nota de crédito",
    "BOL": "Boleta papel",
    "VALE": "Vale sin documento",
}
TIPOS_MANUALES = ["33", "39", "34", "41", "BOL", "61", "VALE"]
TIPOS_RESTAN = {"61"}          # la nota de credito descuenta

CATEGORIAS = ["Repuestos", "Ferretería", "Herramientas", "Insumos de aseo/oficina",
              "Combustible y transporte", "Alimentación", "Servicios", "Otros"]

ESTADOS = {
    "abierta": "Abierta",
    "rendida": "Rendida · por revisar",
    "aprobada": "Aprobada",
}


def _pk():
    return "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"


def ddl_caja():
    pk = _pk()
    return [
        f"""CREATE TABLE IF NOT EXISTS caja_entregas (
            id              {pk},
            fecha           TEXT NOT NULL,
            comprador_id    INTEGER NOT NULL,
            monto_entregado INTEGER NOT NULL,
            nota            TEXT,
            estado          TEXT NOT NULL DEFAULT 'abierta',
            saldo_devuelto  INTEGER,
            diferencia      INTEGER,
            obs_cierre      TEXT,
            entregado_por   TEXT,
            rendida_por     TEXT,
            rendida_at      TEXT,
            aprobada_por    TEXT,
            aprobada_at     TEXT,
            obs_aprobacion  TEXT,
            created_at      TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS caja_gastos (
            id          {pk},
            entrega_id  INTEGER NOT NULL,
            tipo_doc    TEXT NOT NULL,
            folio       TEXT,
            rut_emisor  TEXT,
            proveedor   TEXT,
            fecha_doc   TEXT,
            monto       INTEGER NOT NULL,
            categoria   TEXT,
            motivo      TEXT,
            foto        TEXT,
            origen      TEXT NOT NULL DEFAULT 'manual',
            creado_por  TEXT,
            created_at  TEXT
        )""",
        # Nombres de proveedores aprendidos por RUT (el timbre trae el RUT
        # del emisor pero no su nombre: se pregunta la primera vez y despues
        # se completa solo).
        """CREATE TABLE IF NOT EXISTS caja_emisores (
            rut        TEXT PRIMARY KEY,
            nombre     TEXT NOT NULL,
            updated_at TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_caja_entregas_fecha ON caja_entregas(fecha)",
        "CREATE INDEX IF NOT EXISTS idx_caja_entregas_comprador ON caja_entregas(comprador_id, estado)",
        "CREATE INDEX IF NOT EXISTS idx_caja_gastos_entrega ON caja_gastos(entrega_id)",
        "CREATE INDEX IF NOT EXISTS idx_caja_gastos_doc ON caja_gastos(rut_emisor, tipo_doc, folio)",
    ]


TABLAS_CAJA = ["caja_entregas", "caja_gastos", "caja_emisores"]


def crear_tablas_caja(conn):
    cur = conn.cursor()
    for stmt in ddl_caja():
        try:
            cur.execute(stmt)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


# ── Montos y RUT ──────────────────────────────────────────────────
def a_entero(texto):
    """'$1.250.000' / '1250000' / '1.250.000,00' -> 1250000. None si no hay numero."""
    if texto is None:
        return None
    if isinstance(texto, int):
        return texto
    s = str(texto).strip()
    if "," in s:                      # decimales a la chilena: se descartan
        s = s.split(",")[0]
    neg = s.startswith("-")
    digitos = re.sub(r"\D", "", s)
    if not digitos:
        return None
    n = int(digitos)
    return -n if neg else n


def clp(n):
    """1250000 -> '$1.250.000'"""
    if n is None:
        return "—"
    signo = "-" if n < 0 else ""
    return f"{signo}${abs(int(n)):,}".replace(",", ".")


def normalizar_rut(rut):
    """'76.123.456-k' -> '76123456-K'. '' si viene vacio."""
    if not rut:
        return ""
    s = re.sub(r"[^0-9kK]", "", str(rut)).upper()
    if len(s) < 2:
        return ""
    return f"{s[:-1]}-{s[-1]}"


def dv_rut(cuerpo):
    suma, mult = 0, 2
    for d in reversed(str(cuerpo)):
        suma += int(d) * mult
        mult = 2 if mult == 7 else mult + 1
    r = 11 - suma % 11
    return "0" if r == 11 else ("K" if r == 10 else str(r))


def rut_valido(rut):
    rut = normalizar_rut(rut)
    if not rut:
        return False
    cuerpo, dv = rut.split("-")
    return cuerpo.isdigit() and dv_rut(cuerpo) == dv


def formatear_rut(rut):
    """'76123456-K' -> '76.123.456-K'"""
    rut = normalizar_rut(rut)
    if not rut:
        return ""
    cuerpo, dv = rut.split("-")
    return f"{int(cuerpo):,}".replace(",", ".") + f"-{dv}"


def signo(tipo_doc):
    return -1 if tipo_doc in TIPOS_RESTAN else 1


# ── Cuadre ────────────────────────────────────────────────────────
def total_documentos(gastos):
    return sum(signo(g["tipo_doc"]) * int(g["monto"] or 0) for g in gastos)


def cuadre(monto_entregado, gastos, saldo_devuelto=None):
    """Resumen del cuadre. 'diferencia' > 0 = falta plata; < 0 = sobra."""
    total = total_documentos(gastos)
    disponible = int(monto_entregado) - total
    salida = {
        "entregado": int(monto_entregado),
        "total_documentos": total,
        "disponible": disponible,
        "n_documentos": len(gastos),
        "saldo_devuelto": saldo_devuelto,
        "diferencia": None,
        "cuadra": None,
    }
    if saldo_devuelto is not None:
        dif = disponible - int(saldo_devuelto)
        salida["diferencia"] = dif
        salida["cuadra"] = dif == 0
    return salida


# ── Timbre electronico (TED) ─────────────────────────────────────
_CAMPOS_TED = ("RE", "TD", "F", "FE", "RR", "RSR", "MNT", "IT1")


def parsear_ted(texto):
    """Extrae los datos del timbre electronico del SII.
    Devuelve dict (rut_emisor, tipo_doc, folio, fecha_doc, monto, rut_receptor,
    receptor, item) o None si el texto no es un timbre."""
    if not texto or "<TED" not in texto.upper():
        return None
    dd = re.search(r"<DD>(.*?)</DD>", texto, re.S | re.I)
    bloque = dd.group(1) if dd else texto
    datos = {}
    for campo in _CAMPOS_TED:
        m = re.search(rf"<{campo}>(.*?)</{campo}>", bloque, re.S)
        if m:
            datos[campo] = m.group(1).strip()
    if not datos.get("RE") or not datos.get("TD") or not datos.get("F"):
        return None
    rs = re.search(r"<RS>(.*?)</RS>", bloque, re.S)
    return {
        "rut_emisor": normalizar_rut(datos.get("RE")),
        "tipo_doc": str(int(datos["TD"])) if datos["TD"].isdigit() else datos["TD"],
        "folio": str(int(datos["F"])) if datos["F"].isdigit() else datos["F"],
        "fecha_doc": datos.get("FE", "")[:10],
        "monto": a_entero(datos.get("MNT")),
        "rut_receptor": normalizar_rut(datos.get("RR")),
        "receptor": datos.get("RSR", ""),
        "item": datos.get("IT1", ""),
        # La razon social del emisor viene dentro del CAF (autorizacion de folios)
        "proveedor": rs.group(1).strip() if rs else "",
    }


def _abrir_imagen(data_bytes):
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(data_bytes))
    img = ImageOps.exif_transpose(img)
    return img


def leer_timbre(data_bytes):
    """Busca el codigo PDF417 del timbre en la foto. Devuelve el dict de
    parsear_ted o None. Prueba varias versiones de la imagen porque las
    fotos de celular vienen grandes, torcidas o con poca luz."""
    try:
        import zxingcpp
    except ImportError:
        return None
    try:
        img = _abrir_imagen(data_bytes).convert("L")
    except Exception:
        return None

    from PIL import Image
    fmt = zxingcpp.BarcodeFormat.PDF417
    lado = max(img.size)
    if lado > 3200:                      # fotos de 12+ MP: suficiente detalle y mas rapido
        f = 3200 / lado
        img = img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)
    elif lado < 1400:                    # fotos chicas: agrandar ayuda al lector
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)

    def _leer(im, binarizer=None):
        kw = {"formats": fmt}
        if binarizer is not None:
            kw["binarizer"] = binarizer
        try:
            for r in zxingcpp.read_barcodes(im, **kw):
                datos = parsear_ted(r.text)
                if datos:
                    return datos
        except Exception:
            pass
        return None

    # El lector de PDF417 tolera poco giro (~1,5 grados): se prueba la foto
    # "enderezada" en varios angulos hasta que lea.
    for ang in (0, 1.5, -1.5, 3, -3, 4.5, -4.5, 6, -6, 8, -8, 10, -10):
        im = img if ang == 0 else img.rotate(ang, expand=True, fillcolor=255, resample=Image.BILINEAR)
        datos = _leer(im)
        if datos:
            return datos
    # Ultimo intento con otro metodo de blanco/negro (poca luz, papel termico gastado)
    b = getattr(zxingcpp.Binarizer, "GlobalHistogram", None)
    if b is not None:
        return _leer(img, b)
    return None


# ── Fotos privadas ───────────────────────────────────────────────
CARPETA_LOCAL = os.path.join(BASE_DIR, "datos_caja")
PREFIJO_S3 = "caja"


def _s3():
    from services import s3_configurado, _s3_client
    if not s3_configurado():
        return None, None
    return _s3_client(), os.environ.get("S3_BUCKET")


def preparar_foto(data_bytes, max_lado=2000):
    """Achica la foto y la deja en WEBP para guardarla (pesa ~10 veces menos)."""
    img = _abrir_imagen(data_bytes)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_lado, max_lado))
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=80)
    return buf.getvalue()


def guardar_foto(data_bytes, prefijo=""):
    """Guarda la foto (ya preparada). Devuelve la referencia que va a la BD:
    's3:caja/<archivo>' o 'local:<archivo>'."""
    nombre = f"{prefijo}{uuid.uuid4().hex}.webp"
    cliente, bucket = _s3()
    if cliente:
        try:
            cliente.put_object(Bucket=bucket, Key=f"{PREFIJO_S3}/{nombre}",
                               Body=data_bytes, ContentType="image/webp")
            return f"s3:{PREFIJO_S3}/{nombre}"
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Caja chica: fallo S3/R2, se guarda local: %s", e)
    os.makedirs(CARPETA_LOCAL, exist_ok=True)
    with open(os.path.join(CARPETA_LOCAL, nombre), "wb") as f:
        f.write(data_bytes)
    return f"local:{nombre}"


_REF_OK = re.compile(r"^(s3:caja/|local:)[0-9a-zA-Z_\-]+\.webp$")


def referencia_valida(ref):
    return bool(ref) and bool(_REF_OK.match(ref))


def leer_foto(ref):
    """Bytes de la foto guardada, o None."""
    if not referencia_valida(ref):
        return None
    if ref.startswith("s3:"):
        cliente, bucket = _s3()
        if not cliente:
            return None
        try:
            return cliente.get_object(Bucket=bucket, Key=ref[3:])["Body"].read()
        except Exception:
            return None
    ruta = os.path.join(CARPETA_LOCAL, ref[len("local:"):])
    if not os.path.isfile(ruta):
        return None
    with open(ruta, "rb") as f:
        return f.read()


# ── Consultas ────────────────────────────────────────────────────
def documento_repetido(cur, rut, tipo_doc, folio, excluir_id=None):
    """Busca el mismo documento (RUT + tipo + folio) ya rendido. Devuelve la fila o None."""
    rut = normalizar_rut(rut)
    if not rut or not folio or tipo_doc in ("VALE",):
        return None
    ph = p()
    sql = (f"SELECT g.id, g.entrega_id, e.fecha FROM caja_gastos g "
           f"JOIN caja_entregas e ON e.id = g.entrega_id "
           f"WHERE g.rut_emisor = {ph} AND g.tipo_doc = {ph} AND g.folio = {ph}")
    params = [rut, tipo_doc, str(folio).strip()]
    if excluir_id:
        sql += f" AND g.id <> {ph}"
        params.append(excluir_id)
    cur.execute(sql, params)
    r = cur.fetchone()
    return dict(r) if r else None


def nombre_emisor(cur, rut):
    rut = normalizar_rut(rut)
    if not rut:
        return ""
    cur.execute(f"SELECT nombre FROM caja_emisores WHERE rut = {p()}", (rut,))
    r = cur.fetchone()
    return r["nombre"] if r else ""


def recordar_emisor(cur, rut, nombre, cuando):
    rut = normalizar_rut(rut)
    nombre = (nombre or "").strip()
    if not rut or not nombre:
        return
    ph = p()
    cur.execute(f"SELECT rut FROM caja_emisores WHERE rut = {ph}", (rut,))
    if cur.fetchone():
        cur.execute(f"UPDATE caja_emisores SET nombre = {ph}, updated_at = {ph} WHERE rut = {ph}",
                    (nombre, cuando, rut))
    else:
        cur.execute(f"INSERT INTO caja_emisores (rut, nombre, updated_at) VALUES ({ph}, {ph}, {ph})",
                    (rut, nombre, cuando))


def gastos_de(cur, entrega_id):
    cur.execute(f"SELECT * FROM caja_gastos WHERE entrega_id = {p()} ORDER BY fecha_doc, id", (entrega_id,))
    return [dict(r) for r in cur.fetchall()]


def entrega_con_nombres(cur, entrega_id):
    cur.execute(f"""SELECT e.*, u.nombre AS comprador_nombre, u.username AS comprador_username
                    FROM caja_entregas e LEFT JOIN usuarios u ON u.id = e.comprador_id
                    WHERE e.id = {p()}""", (entrega_id,))
    r = cur.fetchone()
    if not r:
        return None
    e = dict(r)
    e["comprador"] = e.get("comprador_nombre") or e.get("comprador_username") or "—"
    return e
