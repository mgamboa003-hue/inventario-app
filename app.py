# app.py
import hmac
import os
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename

from db import get_db_connection, init_db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", 5))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

DEBUG = os.environ.get("DEBUG", "False").lower() == "true"

csrf = CSRFProtect(app)

# ----- Credenciales -----
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# ----- Configuración de subida de imágenes -----
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_image(archivo):
    """Guarda la imagen subida y retorna la URL relativa, o None si falla."""
    if not archivo or not archivo.filename:
        return None
    if not allowed_file(archivo.filename):
        flash("Tipo de archivo no permitido. Usa png, jpg, jpeg, gif o webp.", "warning")
        return None
    filename = secure_filename(archivo.filename)
    prefijo = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{prefijo}_{filename}"
    archivo.save(os.path.join(UPLOAD_FOLDER, filename))
    return f"/static/uploads/{filename}"


def safe_int(value, default=0):
    try:
        v = int(value)
        return max(0, min(v, 9_999_999))
    except (TypeError, ValueError):
        return default


def safe_float(value, default=0.0):
    try:
        v = float(value)
        return max(0.0, v)
    except (TypeError, ValueError):
        return default


# -------------------------
# AUTENTICACIÓN
# -------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        usuario_ok = hmac.compare_digest(username, ADMIN_USERNAME)
        password_ok = hmac.compare_digest(password, ADMIN_PASSWORD)

        if usuario_ok and password_ok:
            session.permanent = True
            session["logged_in"] = True
            session["username"] = username
            next_url = request.form.get("next") or url_for("index")
            return redirect(next_url)
        else:
            flash("Usuario o contraseña incorrectos.", "danger")

    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("login"))


# -------------------------
# MANEJADORES DE ERROR
# -------------------------
@app.errorhandler(413)
def archivo_muy_grande(e):
    flash(f"El archivo supera el límite de {MAX_UPLOAD_MB} MB.", "danger")
    return redirect(request.referrer or url_for("listar_productos")), 413


# -------------------------
# INICIALIZAR BASE DE DATOS
# -------------------------
@app.before_request
def setup_db():
    init_db()


# -------------------------
# RUTA: INICIO / DASHBOARD
# -------------------------
@app.route("/")
@login_required
def index():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS total FROM productos;")
    total_productos = cur.fetchone()["total"]

    cur.execute("SELECT IFNULL(SUM(stock_actual), 0) AS total_stock FROM productos;")
    total_stock = cur.fetchone()["total_stock"]

    cur.execute(
        """
        SELECT COUNT(*) AS bajos
        FROM productos
        WHERE stock_actual < stock_minimo;
        """
    )
    bajos = cur.fetchone()["bajos"]

    cur.execute(
        """
        SELECT p.nombre, SUM(m.cantidad) AS total_salidas
        FROM movimientos m
        JOIN productos p ON p.id = m.producto_id
        WHERE m.tipo = 'salida'
        GROUP BY p.id
        ORDER BY total_salidas DESC
        LIMIT 5;
        """
    )
    top_salidas = cur.fetchall()

    top_labels = [fila["nombre"] for fila in top_salidas]
    top_values = [fila["total_salidas"] for fila in top_salidas]

    cur.execute(
        """
        SELECT date(fecha) AS dia,
               SUM(CASE WHEN tipo='entrada' THEN cantidad ELSE 0 END) AS entradas,
               SUM(CASE WHEN tipo='salida' THEN cantidad ELSE 0 END) AS salidas
        FROM movimientos
        WHERE date(fecha) >= date('now', '-6 days')
        GROUP BY dia
        ORDER BY dia;
        """
    )
    filas_dias = cur.fetchall()
    conn.close()

    dias = [f["dia"] for f in filas_dias]
    entradas = [f["entradas"] for f in filas_dias]
    salidas = [f["salidas"] for f in filas_dias]

    return render_template(
        "index.html",
        total_productos=total_productos,
        total_stock=total_stock,
        bajos=bajos,
        top_labels=top_labels,
        top_values=top_values,
        dias=dias,
        entradas=entradas,
        salidas=salidas,
    )


# -------------------------
# RUTAS: PRODUCTOS
# -------------------------
@app.route("/productos")
@login_required
def listar_productos():
    q = request.args.get("q", "").strip()
    categoria_filtro = request.args.get("categoria", "").strip()
    proveedor_filtro = request.args.get("proveedor", "").strip()
    equipo_filtro = request.args.get("equipo", "").strip()
    stock_bajo_param = request.args.get("stock_bajo", "").strip()
    stock_bajo_activo = bool(stock_bajo_param)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT categoria FROM productos
        WHERE categoria IS NOT NULL AND categoria <> ''
        ORDER BY categoria;
    """)
    categorias = [fila["categoria"] for fila in cur.fetchall()]

    cur.execute("""
        SELECT DISTINCT proveedor FROM productos
        WHERE proveedor IS NOT NULL AND proveedor <> ''
        ORDER BY proveedor;
    """)
    proveedores = [fila["proveedor"] for fila in cur.fetchall()]

    cur.execute("""
        SELECT DISTINCT equipo FROM productos
        WHERE equipo IS NOT NULL AND equipo <> ''
        ORDER BY equipo;
    """)
    equipos = [fila["equipo"] for fila in cur.fetchall()]

    sql = "SELECT * FROM productos WHERE 1=1"
    params = []

    if q:
        sql += """ AND (
            codigo    LIKE ?
            OR nombre    LIKE ?
            OR categoria LIKE ?
            OR ubicacion LIKE ?
            OR proveedor LIKE ?
        )"""
        patron = f"%{q}%"
        params.extend([patron, patron, patron, patron, patron])

    if categoria_filtro:
        sql += " AND categoria = ?"
        params.append(categoria_filtro)

    if proveedor_filtro:
        sql += " AND proveedor = ?"
        params.append(proveedor_filtro)

    if equipo_filtro:
        sql += " AND equipo = ?"
        params.append(equipo_filtro)

    if stock_bajo_activo:
        sql += " AND stock_actual < stock_minimo"

    sql += " ORDER BY nombre;"

    cur.execute(sql, params)
    productos = cur.fetchall()
    conn.close()

    return render_template(
        "productos.html",
        productos=productos,
        q=q,
        categorias=categorias,
        proveedores=proveedores,
        equipos=equipos,
        categoria_filtro=categoria_filtro,
        proveedor_filtro=proveedor_filtro,
        equipo_filtro=equipo_filtro,
        stock_bajo=stock_bajo_param,
    )


@app.route("/exportar/stock_bajo")
@login_required
def exportar_stock_bajo_excel():
    import io

    from flask import send_file
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT codigo, nombre, proveedor, ubicacion,
               (stock_minimo - stock_actual) AS cantidad_a_comprar
        FROM productos
        WHERE stock_actual < stock_minimo AND (stock_minimo - stock_actual) > 0
        ORDER BY proveedor, nombre
    """)
    filas = cur.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Faltantes"

    encabezados = ["Código", "Producto", "Proveedor", "Ubicación", "Cantidad a comprar"]
    ws.append(encabezados)

    header_fill = PatternFill("solid", fgColor="D32F2F")
    header_font = Font(color="FFFFFF", bold=True)
    center = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = border

    for fila in filas:
        ws.append(list(fila))

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = border
        row[-1].font = Font(bold=True)
        row[-1].alignment = center

    for i, ancho in enumerate([15, 35, 20, 15, 18], start=1):
        ws.column_dimensions[chr(64 + i)].width = ancho

    nombre = f"stock_bajo_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"
    archivo = io.BytesIO()
    wb.save(archivo)
    archivo.seek(0)

    return send_file(
        archivo,
        download_name=nombre,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/exportar/pedidos_proveedor")
@login_required
def exportar_pedidos_proveedor():
    import io
    import zipfile
    from collections import defaultdict

    from flask import send_file
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT proveedor, codigo, nombre, ubicacion,
               (stock_minimo - stock_actual) AS cantidad
        FROM productos
        WHERE stock_actual < stock_minimo AND (stock_minimo - stock_actual) > 0
        ORDER BY proveedor, nombre
    """)
    filas = cur.fetchall()
    conn.close()

    pedidos = defaultdict(list)
    for f in filas:
        pedidos[f["proveedor"]].append(f)

    zip_buffer = io.BytesIO()
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for proveedor, items in pedidos.items():
            wb = Workbook()
            ws = wb.active
            ws.title = "Pedido"
            ws.append(["Código", "Producto", "Ubicación", "Cantidad"])

            for c in ws[1]:
                c.fill = PatternFill("solid", fgColor="2E7D32")
                c.font = Font(color="FFFFFF", bold=True)
                c.alignment = Alignment(horizontal="center")
                c.border = border

            for it in items:
                ws.append([it["codigo"], it["nombre"], it["ubicacion"], it["cantidad"]])

            for row in ws.iter_rows(min_row=2):
                for c in row:
                    c.border = border
                row[-1].alignment = Alignment(horizontal="center")
                row[-1].font = Font(bold=True)

            for col, w in zip(["A", "B", "C", "D"], [15, 40, 20, 15]):
                ws.column_dimensions[col].width = w

            archivo_excel = io.BytesIO()
            wb.save(archivo_excel)
            archivo_excel.seek(0)
            zipf.writestr(f"Pedido_{proveedor}.xlsx", archivo_excel.read())

    zip_buffer.seek(0)
    nombre_zip = f"Pedidos_{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"

    return send_file(
        zip_buffer,
        download_name=nombre_zip,
        as_attachment=True,
        mimetype="application/zip",
    )


@app.route("/productos/nuevo", methods=["GET", "POST"])
@login_required
def nuevo_producto():
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre del producto es obligatorio.", "danger")
            return render_template("producto_form.html", producto=None)

        codigo = (request.form.get("codigo") or "").strip() or None
        categoria = (request.form.get("categoria") or "").strip()
        equipo = (request.form.get("equipo") or "").strip()
        linea = (request.form.get("linea") or "").strip()
        stock_minimo = safe_int(request.form.get("stock_minimo"))
        stock_actual = safe_int(request.form.get("stock_actual"))
        ubicacion = (request.form.get("ubicacion") or "").strip()
        proveedor = (request.form.get("proveedor") or "").strip()
        precio = safe_float(request.form.get("precio"))

        imagen_url = (request.form.get("imagen_url") or "").strip() or None
        archivo = request.files.get("imagen_archivo")
        url_subida = save_uploaded_image(archivo)
        if url_subida:
            imagen_url = url_subida

        try:
            conn = get_db_connection()
            conn.execute(
                """
                INSERT INTO productos
                (codigo, nombre, categoria, equipo, linea, stock_minimo, stock_actual,
                 ubicacion, proveedor, precio, imagen_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (codigo, nombre, categoria, equipo, linea,
                 stock_minimo, stock_actual, ubicacion, proveedor, precio, imagen_url),
            )
            conn.commit()
            flash("Producto creado correctamente.", "success")
            return redirect(url_for("listar_productos"))
        except Exception as e:
            app.logger.error("Error al crear producto: %s", e)
            flash("No se pudo crear el producto. Verifica que el código no esté duplicado.", "danger")
            return redirect(url_for("listar_productos"))
        finally:
            conn.close()

    return render_template("producto_form.html", producto=None)


@app.route("/productos/<int:producto_id>/editar", methods=["GET", "POST"])
@login_required
def editar_producto(producto_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM productos WHERE id = ?;", (producto_id,))
    producto = cur.fetchone()

    if not producto:
        conn.close()
        flash("Producto no encontrado.", "warning")
        return redirect(url_for("listar_productos"))

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            conn.close()
            flash("El nombre del producto es obligatorio.", "danger")
            return redirect(url_for("editar_producto", producto_id=producto_id))

        codigo = (request.form.get("codigo") or "").strip() or None
        categoria = (request.form.get("categoria") or "").strip()
        equipo = (request.form.get("equipo") or "").strip()
        linea = (request.form.get("linea") or "").strip()
        stock_minimo = safe_int(request.form.get("stock_minimo"))
        stock_actual = safe_int(request.form.get("stock_actual"))
        ubicacion = (request.form.get("ubicacion") or "").strip()
        proveedor = (request.form.get("proveedor") or "").strip()
        precio = safe_float(request.form.get("precio"))

        imagen_url_texto = (request.form.get("imagen_url") or "").strip() or None
        archivo = request.files.get("imagen_archivo")
        imagen_url = imagen_url_texto or producto["imagen_url"]
        url_subida = save_uploaded_image(archivo)
        if url_subida:
            imagen_url = url_subida

        # Validar código único
        cur.execute(
            "SELECT id FROM productos WHERE codigo = ? AND id != ?",
            (codigo, producto_id),
        )
        if cur.fetchone():
            conn.close()
            flash("Ya existe otro producto con ese código.", "danger")
            return redirect(url_for("editar_producto", producto_id=producto_id))

        try:
            cur.execute(
                """
                UPDATE productos
                SET codigo = ?, nombre = ?, categoria = ?, equipo = ?, linea = ?,
                    stock_minimo = ?, stock_actual = ?, ubicacion = ?,
                    proveedor = ?, precio = ?, imagen_url = ?
                WHERE id = ?;
                """,
                (codigo, nombre, categoria, equipo, linea,
                 stock_minimo, stock_actual, ubicacion, proveedor, precio,
                 imagen_url, producto_id),
            )
            conn.commit()
            flash("Producto actualizado correctamente.", "success")
        except Exception as e:
            app.logger.error("Error al actualizar producto: %s", e)
            flash("No se pudo actualizar el producto.", "danger")
        finally:
            conn.close()

        return redirect(url_for("listar_productos"))

    conn.close()
    return render_template("producto_form.html", producto=producto)


@app.route("/productos/<int:producto_id>/eliminar", methods=["POST"])
@login_required
def eliminar_producto(producto_id):
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM productos WHERE id = ?;", (producto_id,))
        conn.commit()
        flash("Producto eliminado.", "info")
    finally:
        conn.close()
    return redirect(url_for("listar_productos"))


# -------------------------
# RUTAS: MOVIMIENTOS
# -------------------------
@app.route("/movimientos")
@login_required
def listar_movimientos():
    tipo = request.args.get("tipo", "")
    producto_id = request.args.get("producto_id", "")
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, nombre, codigo FROM productos ORDER BY nombre;")
    productos = cur.fetchall()

    sql = """
        SELECT m.*, p.nombre AS nombre_producto, p.codigo AS codigo_producto
        FROM movimientos m
        JOIN productos p ON p.id = m.producto_id
        WHERE 1=1
    """
    params = []

    if tipo in ("entrada", "salida"):
        sql += " AND m.tipo = ?"
        params.append(tipo)

    if producto_id:
        sql += " AND m.producto_id = ?"
        params.append(producto_id)

    if desde:
        sql += " AND date(m.fecha) >= date(?)"
        params.append(desde)

    if hasta:
        sql += " AND date(m.fecha) <= date(?)"
        params.append(hasta)

    sql += " ORDER BY m.fecha DESC, m.id DESC;"

    cur.execute(sql, params)
    movimientos = cur.fetchall()
    conn.close()

    return render_template(
        "movimientos.html",
        movimientos=movimientos,
        productos=productos,
        tipo=tipo,
        producto_id=producto_id,
        desde=desde,
        hasta=hasta,
    )


@app.route("/movimientos/nuevo/<tipo>", methods=["GET", "POST"])
@login_required
def nuevo_movimiento(tipo):
    if tipo not in ("entrada", "salida"):
        flash("Tipo de movimiento no válido.", "danger")
        return redirect(url_for("listar_movimientos"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nombre, codigo, stock_actual, stock_minimo
        FROM productos ORDER BY nombre;
    """)
    productos = cur.fetchall()

    if request.method == "POST":
        try:
            producto_id = int(request.form["producto_id"])
            cantidad = int(request.form["cantidad"])
        except (KeyError, ValueError):
            conn.close()
            flash("Datos del formulario inválidos.", "danger")
            return redirect(url_for("nuevo_movimiento", tipo=tipo))

        if cantidad <= 0:
            conn.close()
            flash("La cantidad debe ser mayor a cero.", "danger")
            return redirect(url_for("nuevo_movimiento", tipo=tipo))

        usuario = request.form.get("usuario", "").strip()[:100]
        motivo = request.form.get("motivo", "").strip()[:500]

        producto = conn.execute(
            "SELECT stock_actual FROM productos WHERE id = ?", (producto_id,)
        ).fetchone()

        if not producto:
            conn.close()
            flash("Producto no encontrado.", "danger")
            return redirect(url_for("nuevo_movimiento", tipo=tipo))

        stock_actual = producto["stock_actual"]

        if tipo == "salida" and cantidad > stock_actual:
            conn.close()
            flash("No hay stock suficiente para realizar la salida.", "danger")
            return redirect(url_for("nuevo_movimiento", tipo=tipo))

        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nuevo_stock = stock_actual + cantidad if tipo == "entrada" else stock_actual - cantidad

        conn.execute(
            "INSERT INTO movimientos (producto_id, tipo, cantidad, fecha, usuario, motivo) VALUES (?, ?, ?, ?, ?, ?)",
            (producto_id, tipo, cantidad, fecha, usuario, motivo),
        )
        conn.execute(
            "UPDATE productos SET stock_actual = ? WHERE id = ?",
            (nuevo_stock, producto_id),
        )
        conn.commit()
        conn.close()

        flash("Movimiento registrado correctamente.", "success")
        return redirect(url_for("listar_movimientos"))

    conn.close()
    return render_template("movimiento_form.html", tipo=tipo, productos=productos)


@app.route("/productos/<int:producto_id>")
@login_required
def detalle_producto(producto_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM productos WHERE id = ?;", (producto_id,))
    producto = cur.fetchone()
    if not producto:
        conn.close()
        flash("Producto no encontrado.", "warning")
        return redirect(url_for("listar_productos"))

    cur.execute(
        """
        SELECT * FROM movimientos
        WHERE producto_id = ?
        ORDER BY fecha DESC, id DESC
        LIMIT 50;
        """,
        (producto_id,),
    )
    movimientos = cur.fetchall()
    conn.close()

    return render_template(
        "producto_detalle.html",
        producto=producto,
        movimientos=movimientos,
    )


# -------------------------
# API REST (sin CSRF, sin login obligatorio)
# -------------------------
from flask import jsonify


@csrf.exempt
@app.route("/api/productos", methods=["GET"])
def api_productos():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM productos ORDER BY nombre;")
    productos = [dict(p) for p in cur.fetchall()]
    conn.close()
    return jsonify(productos)


@csrf.exempt
@app.route("/api/movimientos", methods=["POST"])
def api_nuevo_movimiento():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON requerido"}), 400

    tipo = data.get("tipo")
    producto_id = data.get("producto_id")
    usuario = str(data.get("usuario", ""))[:100]
    motivo = str(data.get("motivo", ""))[:500]

    try:
        cantidad = int(data.get("cantidad", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Cantidad inválida"}), 400

    if tipo not in ("entrada", "salida") or not producto_id or cantidad <= 0:
        return jsonify({"error": "Datos inválidos"}), 400

    conn = get_db_connection()
    producto = conn.execute(
        "SELECT stock_actual FROM productos WHERE id = ?", (producto_id,)
    ).fetchone()

    if not producto:
        conn.close()
        return jsonify({"error": "Producto no encontrado"}), 404

    stock_actual = producto["stock_actual"]

    if tipo == "salida" and cantidad > stock_actual:
        conn.close()
        return jsonify({"error": "Stock insuficiente"}), 400

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nuevo_stock = stock_actual + cantidad if tipo == "entrada" else stock_actual - cantidad

    conn.execute(
        "INSERT INTO movimientos (producto_id, tipo, cantidad, fecha, usuario, motivo) VALUES (?, ?, ?, ?, ?, ?)",
        (producto_id, tipo, cantidad, fecha, usuario, motivo),
    )
    conn.execute("UPDATE productos SET stock_actual = ? WHERE id = ?", (nuevo_stock, producto_id))
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "nuevo_stock": nuevo_stock})


@csrf.exempt
@app.route("/api/productos", methods=["POST"])
def api_nuevo_producto():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON requerido"}), 400

    nombre = str(data.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400

    conn = get_db_connection()
    try:
        conn.execute(
            """INSERT INTO productos (codigo, nombre, categoria, equipo, linea,
               stock_minimo, stock_actual, ubicacion, proveedor, precio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("codigo"), nombre, data.get("categoria"),
                data.get("equipo"), data.get("linea"),
                safe_int(data.get("stock_minimo")),
                safe_int(data.get("stock_actual")),
                data.get("ubicacion"), data.get("proveedor"),
                safe_float(data.get("precio")),
            ),
        )
        conn.commit()
        producto_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return jsonify({"ok": True, "id": producto_id})
    except Exception as e:
        app.logger.error("Error al crear producto via API: %s", e)
        return jsonify({"error": "Error interno"}), 500
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=DEBUG, port=5002)
