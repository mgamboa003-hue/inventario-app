"""
services.py -- Modulos de soporte para Inventario Wintec v3:
- Auditoria (log de cambios)
- Lockout de login por intentos fallidos
- Tokens de API
- Alertas de stock bajo por email (SMTP configurable)
- Almacenamiento de imagenes/documentos (local o S3/R2 opcional)
- Numeracion y generacion de ordenes de compra y cotizaciones

Todo lo que depende de credenciales externas (SMTP, S3) se activa solo si
las variables de entorno correspondientes estan configuradas; si no,
la app sigue funcionando igual que antes (modo local).
"""

import base64
import hashlib
import io
import os
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from db import get_db_connection, p, USE_POSTGRES, ahora

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# AUDITORIA
def registrar_auditoria(tabla, registro_id, accion, usuario_id=None, usuario=None, detalle=""):
    """Inserta una fila en auditoria. Nunca debe romper la operacion principal."""
    try:
        conn = get_db_connection()
        ph = p()
        conn.cursor().execute(
            f"""INSERT INTO auditoria (tabla, registro_id, accion, usuario_id, usuario, detalle)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph})""",
            (tabla, registro_id, accion, usuario_id, usuario, str(detalle)[:1000]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# LOGIN LOCKOUT
MAX_INTENTOS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", 5))
BLOQUEO_MINUTOS = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", 15))


def usuario_bloqueado(usuario_row):
    """Devuelve (bloqueado: bool, minutos_restantes: int)."""
    locked_until = usuario_row["locked_until"] if "locked_until" in usuario_row.keys() else None
    if not locked_until:
        return False, 0
    try:
        if isinstance(locked_until, str):
            hasta = datetime.fromisoformat(locked_until.replace("Z", ""))
        else:
            hasta = locked_until.replace(tzinfo=None)
    except Exception:
        return False, 0
    ahora_local = ahora()
    if ahora_local < hasta:
        restante = int((hasta - ahora_local).total_seconds() // 60) + 1
        return True, restante
    return False, 0


def registrar_intento_fallido(user_id, intentos_actuales):
    nuevos_intentos = (intentos_actuales or 0) + 1
    conn = get_db_connection()
    ph = p()
    if nuevos_intentos >= MAX_INTENTOS:
        hasta = (ahora() + timedelta(minutes=BLOQUEO_MINUTOS))
        hasta_str = hasta.strftime("%Y-%m-%d %H:%M:%S")
        conn.cursor().execute(
            f"UPDATE usuarios SET failed_attempts = {ph}, locked_until = {ph} WHERE id = {ph}",
            (nuevos_intentos, hasta_str, user_id),
        )
    else:
        conn.cursor().execute(
            f"UPDATE usuarios SET failed_attempts = {ph} WHERE id = {ph}",
            (nuevos_intentos, user_id),
        )
    conn.commit()
    conn.close()
    return nuevos_intentos


def resetear_intentos(user_id):
    conn = get_db_connection()
    ph = p()
    conn.cursor().execute(
        f"UPDATE usuarios SET failed_attempts = 0, locked_until = NULL WHERE id = {ph}",
        (user_id,),
    )
    conn.commit()
    conn.close()


# TOKENS DE API
def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def generar_api_token(usuario_id, nombre):
    """Crea un token nuevo y devuelve el token en texto plano (solo se ve una vez)."""
    token_plano = "wtc_" + secrets.token_urlsafe(32)
    token_hash = _hash_token(token_plano)
    conn = get_db_connection()
    ph = p()
    conn.cursor().execute(
        f"INSERT INTO api_tokens (usuario_id, nombre, token_hash) VALUES ({ph},{ph},{ph})",
        (usuario_id, nombre, token_hash),
    )
    conn.commit()
    conn.close()
    return token_plano


def verificar_api_token(token_plano):
    """Devuelve la fila del token si es valido y activo, o None."""
    if not token_plano:
        return None
    token_hash = _hash_token(token_plano)
    conn = get_db_connection()
    cur = conn.cursor()
    ph = p()
    activo_val = "TRUE" if USE_POSTGRES else "1"
    cur.execute(
        f"SELECT * FROM api_tokens WHERE token_hash = {ph} AND activo = {activo_val}",
        (token_hash,),
    )
    row = cur.fetchone()
    if row:
        ahora_str = ahora().strftime("%Y-%m-%d %H:%M:%S")
        conn.cursor().execute(f"UPDATE api_tokens SET last_used_at = {ph} WHERE id = {ph}", (ahora_str, row["id"]))
        conn.commit()
    conn.close()
    return row


def revocar_api_token(token_id):
    conn = get_db_connection()
    ph = p()
    conn.cursor().execute(f"UPDATE api_tokens SET activo = 0 WHERE id = {ph}" if not USE_POSTGRES
                 else f"UPDATE api_tokens SET activo = FALSE WHERE id = {ph}", (token_id,))
    conn.commit()
    conn.close()


# ALERTAS DE STOCK BAJO POR EMAIL
def email_configurado():
    return bool(os.environ.get("SMTP_HOST")) and bool(os.environ.get("SMTP_TO"))


def enviar_alertas_stock_bajo():
    """
    Envia un correo con los productos bajo stock minimo.
    Requiere variables de entorno: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
    SMTP_FROM, SMTP_TO (uno o varios separados por coma).
    Si no esta configurado, no hace nada y devuelve (False, motivo).
    """
    if not email_configurado():
        return False, "SMTP no configurado (faltan SMTP_HOST / SMTP_TO en variables de entorno)."

    conn = get_db_connection()
    cur = conn.cursor()
    activo_val = "TRUE" if USE_POSTGRES else "1"
    cur.execute(f"""
        SELECT codigo, nombre, stock_actual, stock_minimo, proveedor
        FROM productos WHERE stock_actual < stock_minimo AND activo = {activo_val}
        ORDER BY (stock_minimo - stock_actual) DESC
    """)
    filas = cur.fetchall()
    conn.close()

    if not filas:
        return False, "No hay productos bajo el stock minimo."

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER", "")
    pwd = os.environ.get("SMTP_PASS", "")
    remitente = os.environ.get("SMTP_FROM", user or "inventario@wintec.local")
    destinatarios = [d.strip() for d in os.environ.get("SMTP_TO", "").split(",") if d.strip()]

    filas_html = "".join(
        f"<tr><td>{f['codigo'] or ''}</td><td>{f['nombre']}</td>"
        f"<td style='text-align:center'>{f['stock_actual']}</td>"
        f"<td style='text-align:center'>{f['stock_minimo']}</td>"
        f"<td>{f['proveedor'] or ''}</td></tr>"
        for f in filas
    )
    html = f"""
    <h2>Alerta de stock bajo -- Inventario Wintec</h2>
    <p>{len(filas)} producto(s) estan bajo el stock minimo al {ahora().strftime('%d-%m-%Y %H:%M')}:</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
        <tr style="background:#1B4F8A;color:#fff">
            <th>Codigo</th><th>Producto</th><th>Stock actual</th><th>Stock minimo</th><th>Proveedor</th>
        </tr>
        {filas_html}
    </table>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Inventario Wintec] {len(filas)} producto(s) con stock bajo"
    msg["From"] = remitente
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            if user:
                server.login(user, pwd)
            server.sendmail(remitente, destinatarios, msg.as_string())
        return True, f"Correo enviado a {', '.join(destinatarios)} ({len(filas)} productos)."
    except Exception as e:
        return False, f"Error enviando correo: {e}"


# ALMACENAMIENTO DE ARCHIVOS (local o S3/R2 opcional)
def s3_configurado():
    return bool(os.environ.get("S3_BUCKET")) and bool(os.environ.get("S3_ACCESS_KEY"))


def _s3_client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT") or None,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        region_name=os.environ.get("S3_REGION", "auto"),
    )


def subir_imagen_bytes(data_bytes, filename, content_type="image/webp", carpeta="uploads"):
    """
    Sube el archivo a S3/R2 si esta configurado, si no lo deja en disco local
    (dentro de static/<carpeta>). Sirve tanto para fotos de productos como para
    documentos de cotizaciones (PDF/imagenes), usando 'carpeta' para separarlos.
    Devuelve la URL publica (relativa si es local, absoluta si es S3/R2).
    """
    if s3_configurado():
        try:
            bucket = os.environ.get("S3_BUCKET")
            public_base = os.environ.get("S3_PUBLIC_URL", "").rstrip("/")
            client = _s3_client()
            key = f"{carpeta}/{filename}"
            client.put_object(Bucket=bucket, Key=key, Body=data_bytes,
                               ContentType=content_type, ACL="public-read")
            if public_base:
                return f"{public_base}/{key}"
            endpoint = os.environ.get("S3_ENDPOINT", "").rstrip("/")
            return f"{endpoint}/{bucket}/{key}"
        except Exception:
            pass  # fallback a disco local si algo falla

    upload_folder = os.path.join(BASE_DIR, "static", carpeta)
    os.makedirs(upload_folder, exist_ok=True)
    filepath = os.path.join(upload_folder, filename)
    with open(filepath, "wb") as f:
        f.write(data_bytes)
    return f"/static/{carpeta}/{filename}"


# ETIQUETAS QR PARA REPUESTOS
def asegurar_codigo_producto(producto_id, codigo_actual):
    """Si el producto no tiene codigo, genera uno correlativo (WTC-00001) y lo guarda.
    Devuelve el codigo final (el existente o el recien generado)."""
    if codigo_actual and str(codigo_actual).strip():
        return codigo_actual
    nuevo_codigo = f"WTC-{producto_id:05d}"
    conn = get_db_connection()
    ph = p()
    conn.cursor().execute(f"UPDATE productos SET codigo = {ph} WHERE id = {ph}", (nuevo_codigo, producto_id))
    conn.commit()
    conn.close()
    return nuevo_codigo


def generar_qr_base64(data, box_size=8):
    """Genera un QR para 'data' y devuelve un data-URI PNG listo para <img src="...">."""
    import qrcode
    img = qrcode.make(data, box_size=box_size, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ORDENES DE COMPRA
def _siguiente_numero_oc(cur):
    cur.execute("SELECT COUNT(*) AS n FROM ordenes_compra")
    row = cur.fetchone()
    n = (row["n"] if hasattr(row, "__getitem__") else row[0]) or 0
    anio = ahora().strftime("%y")
    return f"OC-{anio}-{n + 1:04d}"


def generar_ordenes_compra_sugeridas(usuario_id=None, usuario_nombre=None):
    """
    Agrupa los productos bajo stock minimo por proveedor y crea una orden de
    compra (encabezado + items) por cada proveedor. Devuelve la lista de IDs creados.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    ph = p()
    activo_val = "TRUE" if USE_POSTGRES else "1"
    cur.execute(f"""
        SELECT id, codigo, nombre, proveedor, precio, (stock_minimo - stock_actual) AS cantidad
        FROM productos
        WHERE stock_actual < stock_minimo AND activo = {activo_val}
        AND (stock_minimo - stock_actual) > 0
        ORDER BY proveedor, nombre
    """)
    filas = cur.fetchall()

    from collections import defaultdict
    por_proveedor = defaultdict(list)
    for f in filas:
        por_proveedor[f["proveedor"] or "Sin proveedor"].append(f)

    ids_creados = []
    for proveedor, items in por_proveedor.items():
        numero = _siguiente_numero_oc(cur)
        total = sum((it["cantidad"] or 0) * (it["precio"] or 0) for it in items)
        cur.execute(
            f"""INSERT INTO ordenes_compra (numero, proveedor_nombre, estado, creado_por, creado_por_id, total_estimado)
                VALUES ({ph},{ph},'pendiente',{ph},{ph},{ph})""",
            (numero, proveedor, usuario_nombre, usuario_id, total),
        )
        if USE_POSTGRES:
            cur.execute("SELECT lastval() AS id")
            orden_id = cur.fetchone()["id"]
        else:
            orden_id = cur.lastrowid

        for it in items:
            subtotal = (it["cantidad"] or 0) * (it["precio"] or 0)
            cur.execute(
                f"""INSERT INTO ordenes_compra_items
                    (orden_id, producto_id, codigo, nombre, cantidad, precio_unitario, subtotal)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                (orden_id, it["id"], it["codigo"], it["nombre"], it["cantidad"], it["precio"], subtotal),
            )
        ids_creados.append(orden_id)

    conn.commit()
    conn.close()
    return ids_creados


# SOLICITUDES DEL EQUIPO
def dias_atraso_solicitud():
    return int(os.environ.get("DIAS_ATRASO_SOLICITUD", 5))


def dias_gracia_historial_solicitud():
    """Dias que una solicitud rechazada/cancelada sigue visible en la lista activa
    antes de pasar solo al historial."""
    return int(os.environ.get("DIAS_GRACIA_SOLICITUD", 7))


def enviar_notificacion_solicitud(solicitud):
    """
    Avisa por correo al comprador que llego una solicitud nueva.
    Requiere SMTP_HOST y SMTP_TO (mismas variables que las alertas de stock).
    Si no esta configurado, no hace nada (no rompe la creacion de la solicitud).
    """
    if not email_configurado():
        return False, "SMTP no configurado."

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER", "")
    pwd = os.environ.get("SMTP_PASS", "")
    remitente = os.environ.get("SMTP_FROM", user or "inventario@wintec.local")
    destinatarios = [d.strip() for d in os.environ.get("SMTP_TO", "").split(",") if d.strip()]

    html = f"""
    <h2>Nueva solicitud de repuesto/herramienta</h2>
    <p><b>{solicitud.get('solicitado_por') or 'Alguien del equipo'}</b> solicito:</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
        <tr><td><b>Item</b></td><td>{solicitud.get('nombre_item')}</td></tr>
        <tr><td><b>Cantidad</b></td><td>{solicitud.get('cantidad')}</td></tr>
        <tr><td><b>Urgencia</b></td><td>{solicitud.get('urgencia')}</td></tr>
        <tr><td><b>Descripcion</b></td><td>{solicitud.get('descripcion') or '-'}</td></tr>
    </table>
    <p>Revisa el detalle y el estado en la app, seccion Solicitudes.</p>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Inventario Wintec] Nueva solicitud: {solicitud.get('nombre_item')}"
    msg["From"] = remitente
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            if user:
                server.login(user, pwd)
            server.sendmail(remitente, destinatarios, msg.as_string())
        return True, "Notificacion enviada."
    except Exception as e:
        return False, f"Error enviando notificacion: {e}"


# COTIZACIONES
def _siguiente_numero_cotizacion(cur):
    cur.execute("SELECT COUNT(*) AS n FROM cotizaciones")
    row = cur.fetchone()
    n = (row["n"] if hasattr(row, "__getitem__") else row[0]) or 0
    anio = ahora().strftime("%y")
    return f"COT-{anio}-{n + 1:04d}"


def generar_orden_desde_cotizacion(cotizacion_id, usuario_id=None, usuario_nombre=None):
    """
    Crea una orden de compra (encabezado + items) a partir de una cotizacion
    aceptada, y guarda el vinculo en cotizaciones.orden_compra_id.
    Devuelve el id de la orden creada, o None si la cotizacion no existe o ya
    tenia una orden asociada.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    ph = p()

    cur.execute(f"SELECT * FROM cotizaciones WHERE id = {ph}", (cotizacion_id,))
    cot = cur.fetchone()
    if not cot or cot["orden_compra_id"]:
        conn.close()
        return None

    cur.execute(f"SELECT * FROM cotizacion_items WHERE cotizacion_id = {ph}", (cotizacion_id,))
    items = cur.fetchall()

    numero = _siguiente_numero_oc(cur)
    total = cot["monto_total"] or sum((it["cantidad"] or 0) * (it["precio_unitario"] or 0) for it in items)

    cur.execute(
        f"""INSERT INTO ordenes_compra (numero, proveedor_nombre, estado, creado_por, creado_por_id, total_estimado)
            VALUES ({ph},{ph},'pendiente',{ph},{ph},{ph})""",
        (numero, cot["proveedor_nombre"], usuario_nombre, usuario_id, total),
    )
    if USE_POSTGRES:
        cur.execute("SELECT lastval() AS id")
        orden_id = cur.fetchone()["id"]
    else:
        orden_id = cur.lastrowid

    for it in items:
        subtotal = it["subtotal"] if it["subtotal"] is not None else (it["cantidad"] or 0) * (it["precio_unitario"] or 0)
        cur.execute(
            f"""INSERT INTO ordenes_compra_items
                (orden_id, producto_id, codigo, nombre, cantidad, precio_unitario, subtotal)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
            (orden_id, it["producto_id"], it["codigo"], it["nombre"], it["cantidad"], it["precio_unitario"], subtotal),
        )

    cur.execute(f"UPDATE cotizaciones SET orden_compra_id = {ph} WHERE id = {ph}", (orden_id, cotizacion_id))
    conn.commit()
    conn.close()
    return orden_id


# CONTROL DE ACCESOS (sesiones de usuario)
def sesion_activa_minutos():
    """Minutos de inactividad tras los cuales una sesion deja de considerarse 'en linea'."""
    return int(os.environ.get("SESION_ACTIVA_MINUTOS", 5))


def formatear_duracion(segundos):
    """Convierte segundos a un texto legible tipo '2h 15m' o '45m' o '30s'."""
    if segundos is None:
        return "—"
    try:
        segundos = int(segundos)
    except (TypeError, ValueError):
        return "—"
    if segundos < 0:
        segundos = 0
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    if horas > 0:
        return f"{horas}h {minutos}m"
    if minutos > 0:
        return f"{minutos}m"
    return f"{segs}s"


# RESPALDO AUTOMATICO DE LA BASE DE DATOS (a S3/R2, privado)
BACKUPS_A_MANTENER = int(os.environ.get("BACKUPS_A_MANTENER", 14))
PREFIJO_BACKUPS_REMOTOS = "backups-db"


def respaldo_automatico():
    """Genera un dump completo de la base (ver db.generar_dump_sql) y lo sube
    a S3/R2 como archivo PRIVADO (nunca con ACL publica: contiene contrasenas
    hasheadas y tokens). Si S3/R2 no esta configurado, no hace nada util --
    guardar el dump en disco local del contenedor no sirve como respaldo real
    porque Railway (y similares) usan un filesystem efimero que se borra en
    cada deploy.
    Devuelve (ok: bool, mensaje: str).
    """
    from db import generar_dump_sql
    import gzip

    if not s3_configurado():
        return False, ("S3/R2 no esta configurado, asi que no se puede generar un respaldo remoto real. "
                        "Configura S3_BUCKET/S3_ACCESS_KEY para habilitar los respaldos automaticos.")

    try:
        dump = generar_dump_sql()
        comprimido = gzip.compress(dump)
        timestamp = ahora().strftime("%Y%m%d_%H%M%S")
        filename = f"inventario_{timestamp}.sql.gz"
        key = f"{PREFIJO_BACKUPS_REMOTOS}/{filename}"

        client = _s3_client()
        bucket = os.environ.get("S3_BUCKET")
        client.put_object(Bucket=bucket, Key=key, Body=comprimido, ContentType="application/gzip")

        _limpiar_backups_remotos_antiguos(client, bucket)
        return True, f"Respaldo remoto creado: {filename} ({len(comprimido) // 1024} KB)"
    except Exception as e:
        return False, f"Error generando el respaldo remoto: {e}"


def listar_backups_remotos():
    """Lista los respaldos guardados en S3/R2, mas recientes primero.
    Devuelve una lista de dicts con key, nombre, tamano y fecha."""
    if not s3_configurado():
        return []
    try:
        client = _s3_client()
        bucket = os.environ.get("S3_BUCKET")
        resp = client.list_objects_v2(Bucket=bucket, Prefix=f"{PREFIJO_BACKUPS_REMOTOS}/")
        items = resp.get("Contents", [])
        items.sort(key=lambda o: o["LastModified"], reverse=True)
        resultado = []
        for obj in items:
            resultado.append({
                "key": obj["Key"],
                "nombre": obj["Key"].split("/")[-1],
                "tamano_kb": max(1, obj["Size"] // 1024),
                "fecha": obj["LastModified"],
            })
        return resultado
    except Exception:
        return []


def _limpiar_backups_remotos_antiguos(client, bucket):
    """Elimina respaldos remotos mas antiguos, dejando solo los ultimos
    BACKUPS_A_MANTENER."""
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=f"{PREFIJO_BACKUPS_REMOTOS}/")
        items = resp.get("Contents", [])
        items.sort(key=lambda o: o["LastModified"], reverse=True)
        for obj in items[BACKUPS_A_MANTENER:]:
            client.delete_object(Bucket=bucket, Key=obj["Key"])
    except Exception:
        pass


def url_descarga_backup_remoto(key, minutos_validez=10):
    """Genera un link temporal y privado para descargar un respaldo desde
    S3/R2 (el bucket de fotos es publico, pero los respaldos no deben serlo)."""
    try:
        client = _s3_client()
        bucket = os.environ.get("S3_BUCKET")
        return client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=minutos_validez * 60,
        )
    except Exception:
        return None


# PROYECCION DE QUIEBRE DE STOCK (segun consumo real, no solo el umbral fijo)
DIAS_HISTORIAL_CONSUMO = int(os.environ.get("DIAS_HISTORIAL_CONSUMO", 60))


def calcular_dias_restantes_por_producto(producto_ids, dias_historial=None):
    """Para cada producto de la lista, estima cuantos dias de stock quedan
    segun el promedio de consumo real (salidas) de los ultimos N dias --
    en vez de solo comparar contra el stock minimo fijo.
    Devuelve un dict {producto_id: {"dias_restantes": int|None, "consumo_diario": float}}.
    dias_restantes es None si no hay consumo registrado en el periodo (no se
    puede proyectar) o si el producto no tiene salidas recientes."""
    producto_ids = [pid for pid in producto_ids if pid]
    if not producto_ids:
        return {}
    if dias_historial is None:
        dias_historial = DIAS_HISTORIAL_CONSUMO

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        ph = p()
        placeholders = ", ".join([ph] * len(producto_ids))

        if USE_POSTGRES:
            cur.execute(
                f"""SELECT producto_id, SUM(cantidad) AS total_salidas
                    FROM movimientos
                    WHERE tipo = 'salida' AND producto_id IN ({placeholders})
                      AND fecha >= NOW() - INTERVAL '{int(dias_historial)} days'
                    GROUP BY producto_id""",
                producto_ids,
            )
        else:
            cur.execute(
                f"""SELECT producto_id, SUM(cantidad) AS total_salidas
                    FROM movimientos
                    WHERE tipo = 'salida' AND producto_id IN ({placeholders})
                      AND fecha >= datetime('now', '-{int(dias_historial)} days')
                    GROUP BY producto_id""",
                producto_ids,
            )
        consumo_por_producto = {r["producto_id"]: r["total_salidas"] for r in cur.fetchall()}

        cur.execute(
            f"SELECT id, stock_actual FROM productos WHERE id IN ({placeholders})",
            producto_ids,
        )
        stock_por_producto = {r["id"]: r["stock_actual"] for r in cur.fetchall()}
    finally:
        conn.close()

    resultado = {}
    for pid in producto_ids:
        total_salidas = consumo_por_producto.get(pid, 0) or 0
        stock_actual = stock_por_producto.get(pid, 0) or 0
        consumo_diario = total_salidas / dias_historial if total_salidas else 0
        if consumo_diario > 0:
            dias_restantes = int(stock_actual / consumo_diario)
        else:
            dias_restantes = None
        resultado[pid] = {"dias_restantes": dias_restantes, "consumo_diario": round(consumo_diario, 2)}
    return resultado
