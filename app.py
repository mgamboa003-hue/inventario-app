# app.py
from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import datetime
from db import get_db_connection, init_db
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "inventario_wintec_123"  # solo para mensajes flash

# ----- Configuración de subida de imágenes -----
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS



# -------------------------
# INICIALIZAR BASE DE DATOS (Flask 3.x)
# -------------------------
@app.before_request
def setup_db():
    init_db()


# -------------------------
# RUTA: INICIO / DASHBOARD
# -------------------------
@app.route("/")
def index():
    conn = get_db_connection()
    cur = conn.cursor()

    # Total productos
    cur.execute("SELECT COUNT(*) AS total FROM productos;")
    total_productos = cur.fetchone()["total"]

    # Stock total
    cur.execute("SELECT IFNULL(SUM(stock_actual), 0) AS total_stock FROM productos;")
    total_stock = cur.fetchone()["total_stock"]

    # Productos con stock bajo
    cur.execute(
        """
        SELECT COUNT(*) AS bajos
        FROM productos
        WHERE stock_actual < stock_minimo;
        """
    )
    bajos = cur.fetchone()["bajos"]

    # Top 5 productos con más salidas
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

    # Movimientos últimos 7 días
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
def listar_productos():
    from flask import request

    q = request.args.get("q", "").strip()
    categoria_filtro = request.args.get("categoria", "").strip()
    proveedor_filtro = request.args.get("proveedor", "").strip()
    equipo_filtro = request.args.get("equipo", "").strip()

    # cualquier valor distinto de vacío cuenta como "stock bajo activado"
    stock_bajo_param = request.args.get("stock_bajo", "").strip()
    stock_bajo_activo = bool(stock_bajo_param)

    conn = get_db_connection()
    cur = conn.cursor()

    # ----- combos de categoría, proveedor y equipo -----
    cur.execute("""
        SELECT DISTINCT categoria
        FROM productos
        WHERE categoria IS NOT NULL AND categoria <> ''
        ORDER BY categoria;
    """)
    categorias = [fila["categoria"] for fila in cur.fetchall()]

    cur.execute("""
        SELECT DISTINCT proveedor
        FROM productos
        WHERE proveedor IS NOT NULL AND proveedor <> ''
        ORDER BY proveedor;
    """)
    proveedores = [fila["proveedor"] for fila in cur.fetchall()]

    cur.execute("""
        SELECT DISTINCT equipo
        FROM productos
        WHERE equipo IS NOT NULL AND equipo <> ''
        ORDER BY equipo;
    """)
    equipos = [fila["equipo"] for fila in cur.fetchall()]

    # ----- WHERE dinámico -----
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
        # para el template alcanza con saber si viene algo en stock_bajo
        stock_bajo=stock_bajo_param,
    )


@app.route("/exportar/stock_bajo")
def exportar_stock_bajo_excel():

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            codigo,
            nombre,
            proveedor,
            ubicacion,
            (stock_minimo - stock_actual) AS cantidad_a_comprar
        FROM productos
        WHERE stock_actual < stock_minimo
        AND (stock_minimo - stock_actual) > 0
        ORDER BY proveedor, nombre
    """)

    filas = cur.fetchall()
    conn.close()

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    import io
    from datetime import datetime
    from flask import send_file

    wb = Workbook()
    ws = wb.active
    ws.title = "Faltantes"

    encabezados = [
        "Código",
        "Producto",
        "Proveedor",
        "Ubicación",
        "Cantidad a comprar"
    ]

    ws.append(encabezados)

    for col in ws[1]:
        col.font = Font(bold=True)
        col.fill = PatternFill("solid", fgColor="FF9999")

    for fila in filas:
        ws.append(list(fila))

    from openpyxl.styles import Alignment, Border, Side

    # Ajustar ancho de columnas
    anchos = [15, 35, 20, 15, 18]
    for i, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[chr(64 + i)].width = ancho

    # Estilos
    header_fill = PatternFill("solid", fgColor="D32F2F")
    header_font = Font(color="FFFFFF", bold=True)
    center = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Encabezados
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = border

    # Filas de datos
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = border

        # Columna "Cantidad a comprar" (última)
        row[-1].font = Font(bold=True)
        row[-1].alignment = center


    nombre = f"stock_bajo_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"

    archivo = io.BytesIO()
    wb.save(archivo)
    archivo.seek(0)

    return send_file(
        archivo,
        download_name=nombre,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route("/exportar/pedidos_proveedor")
def exportar_pedidos_proveedor():

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            proveedor,
            codigo,
            nombre,
            ubicacion,
            (stock_minimo - stock_actual) AS cantidad
        FROM productos
        WHERE stock_actual < stock_minimo
        AND (stock_minimo - stock_actual) > 0
        ORDER BY proveedor, nombre
    """)

    filas = cur.fetchall()
    conn.close()

    from collections import defaultdict
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    import zipfile
    import io
    from datetime import datetime
    from flask import send_file

    pedidos = defaultdict(list)
    for f in filas:
        pedidos[f["proveedor"]].append(f)

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:

        for proveedor, items in pedidos.items():

            wb = Workbook()
            ws = wb.active
            ws.title = "Pedido"

            ws.append(["Código", "Producto", "Ubicación", "Cantidad"])

            header_fill = PatternFill("solid", fgColor="2E7D32")
            header_font = Font(color="FFFFFF", bold=True)
            center = Alignment(horizontal="center")
            thin = Side(style="thin")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for c in ws[1]:
                c.fill = header_fill
                c.font = header_font
                c.alignment = center
                c.border = border

            for it in items:
                ws.append([
                    it["codigo"],
                    it["nombre"],
                    it["ubicacion"],
                    it["cantidad"]
                ])

            for row in ws.iter_rows(min_row=2):
                for c in row:
                    c.border = border
                row[-1].alignment = center
                row[-1].font = Font(bold=True)

            for col, w in zip(["A", "B", "C", "D"], [15, 40, 20, 15]):
                ws.column_dimensions[col].width = w

            archivo_excel = io.BytesIO()
            wb.save(archivo_excel)
            archivo_excel.seek(0)

            nombre_excel = f"Pedido_{proveedor}.xlsx"
            zipf.writestr(nombre_excel, archivo_excel.read())

    zip_buffer.seek(0)

    nombre_zip = f"Pedidos_{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"

    return send_file(
        zip_buffer,
        download_name=nombre_zip,
        as_attachment=True,
        mimetype="application/zip"
    )


@app.route("/productos/nuevo", methods=["GET", "POST"])
def nuevo_producto():
    if request.method == "POST":
        codigo = request.form.get("codigo") or None
        nombre = request.form.get("nombre")
        categoria = request.form.get("categoria")
        equipo = request.form.get("equipo")
        linea = request.form.get("linea")
        stock_minimo = request.form.get("stock_minimo") or 0
        stock_actual = request.form.get("stock_actual") or 0
        ubicacion = request.form.get("ubicacion")
        proveedor = request.form.get("proveedor")
        precio = request.form.get("precio") or 0

        # URL escrita a mano (opcional)
        imagen_url_texto = request.form.get("imagen_url") or None

        # Archivo subido (opcional)
        archivo = request.files.get("imagen_archivo")

        # Por defecto usamos lo que escribió el usuario
        imagen_url = imagen_url_texto

        # Si subió archivo y es válido, lo guardamos
        if archivo and archivo.filename:
            if allowed_file(archivo.filename):
                filename = secure_filename(archivo.filename)
                # Para evitar nombres repetidos, le agregamos un prefijo simple
                from datetime import datetime
                prefijo = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = f"{prefijo}_{filename}"

                ruta_fisica = os.path.join(UPLOAD_FOLDER, filename)
                archivo.save(ruta_fisica)

                # Ruta que usará el navegador
                imagen_url = f"/static/uploads/{filename}"
            else:
                flash(
                    "Tipo de archivo no permitido. Usa png, jpg, jpeg, gif o webp.",
                    "warning",
                )

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO productos
                (codigo, nombre, categoria, equipo, linea, stock_minimo, stock_actual,
                 ubicacion, proveedor, precio, imagen_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    codigo,
                    nombre,
                    categoria,
                    equipo,
                    linea,
                    int(stock_minimo),
                    int(stock_actual),
                    ubicacion,
                    proveedor,
                    float(precio),
                    imagen_url,
                ),
            )
            conn.commit()
            conn.close()
            flash("Producto creado correctamente.", "success")
            return redirect(url_for("listar_productos"))
        except Exception as e:
            flash(f"Error al crear producto: {e}", "danger")
            return redirect(url_for("listar_productos"))

    # GET
    return render_template("producto_form.html", producto=None)



@app.route("/productos/<int:producto_id>/editar", methods=["GET", "POST"])
def editar_producto(producto_id):
    conn = get_db_connection()
    cur = conn.cursor()

    # Obtener producto actual
    cur.execute("SELECT * FROM productos WHERE id = ?;", (producto_id,))
    producto = cur.fetchone()

    if not producto:
        conn.close()
        flash("Producto no encontrado.", "warning")
        return redirect(url_for("listar_productos"))

    if request.method == "POST":
        # 🔹 PRIMERO obtener datos del formulario
        codigo = request.form.get("codigo") or None
        nombre = request.form.get("nombre")
        categoria = request.form.get("categoria")
        equipo = request.form.get("equipo")
        linea = request.form.get("linea")
        stock_minimo = request.form.get("stock_minimo") or 0
        stock_actual = request.form.get("stock_actual") or 0
        ubicacion = request.form.get("ubicacion")
        proveedor = request.form.get("proveedor")
        precio = request.form.get("precio") or 0

        imagen_url_texto = request.form.get("imagen_url") or None
        archivo = request.files.get("imagen_archivo")

        # Mantener imagen actual si no se cambia
        imagen_url = imagen_url_texto or producto["imagen_url"]

        # Subida de imagen
        if archivo and archivo.filename:
            if allowed_file(archivo.filename):
                filename = secure_filename(archivo.filename)
                from datetime import datetime
                prefijo = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = f"{prefijo}_{filename}"
                ruta_fisica = os.path.join(UPLOAD_FOLDER, filename)
                archivo.save(ruta_fisica)
                imagen_url = f"/static/uploads/{filename}"
            else:
                flash("Tipo de archivo no permitido. Usa png, jpg, jpeg, gif o webp.", "warning")

        # 🔥 VALIDAR CODIGO UNICO (DESPUÉS de definir codigo)
        cur.execute("""
            SELECT id FROM productos 
            WHERE codigo = ? AND id != ?
        """, (codigo, producto_id))

        existe = cur.fetchone()

        if existe:
            conn.close()
            flash("Ya existe otro producto con ese código.", "danger")
            return redirect(url_for("editar_producto", producto_id=producto_id))

        # 🔹 UPDATE
        try:
            cur.execute(
                """
                UPDATE productos
                SET codigo = ?, nombre = ?, categoria = ?, equipo = ?, linea = ?,
                    stock_minimo = ?, stock_actual = ?, ubicacion = ?,
                    proveedor = ?, precio = ?, imagen_url = ?
                WHERE id = ?;
                """,
                (
                    codigo,
                    nombre,
                    categoria,
                    equipo,
                    linea,
                    int(stock_minimo),
                    int(stock_actual),
                    ubicacion,
                    proveedor,
                    float(precio),
                    imagen_url,
                    producto_id,
                ),
            )
            conn.commit()
            flash("Producto actualizado correctamente.", "success")

        except Exception as e:
            flash(f"Error al actualizar producto: {e}", "danger")

        finally:
            conn.close()

        return redirect(url_for("listar_productos"))

    # GET
    conn.close()
    return render_template("producto_form.html", producto=producto)

@app.route("/productos/<int:producto_id>/eliminar", methods=["POST"])
def eliminar_producto(producto_id):
    conn = get_db_connection()
    cur = conn.cursor()

    # Ojo: podrías validar si tiene movimientos antes
    cur.execute("DELETE FROM productos WHERE id = ?;", (producto_id,))
    conn.commit()
    conn.close()
    flash("Producto eliminado.", "info")
    return redirect(url_for("listar_productos"))


# -------------------------
# RUTAS: MOVIMIENTOS (ENTRADAS / SALIDAS)
# -------------------------
@app.route("/movimientos")
def listar_movimientos():
    from flask import request

    tipo = request.args.get("tipo", "")
    producto_id = request.args.get("producto_id", "")
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")

    conn = get_db_connection()
    cur = conn.cursor()

    # Para combo de productos
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
def nuevo_movimiento(tipo):
    if tipo not in ("entrada", "salida"):
        flash("Tipo de movimiento no válido.", "danger")
        return redirect(url_for("listar_movimientos"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            id,
            nombre,
            codigo,
            stock_actual,
            stock_minimo
        FROM productos
        ORDER BY nombre;
    """)
    productos = cur.fetchall()

    if request.method == "POST":
        producto_id = int(request.form["producto_id"])
        cantidad = int(request.form["cantidad"])
        usuario = request.form.get("usuario", "").strip()
        motivo = request.form.get("motivo", "").strip()

        producto = conn.execute(
            "SELECT stock_actual FROM productos WHERE id = ?",
            (producto_id,)
        ).fetchone()

        if not producto:
            conn.close()
            flash("Producto no encontrado.", "danger")
            return redirect(url_for("nuevo_movimiento", tipo=tipo))

        stock_actual = producto["stock_actual"]

        # VALIDACIÓN SOLO PARA SALIDAS
        if tipo == "salida" and cantidad > stock_actual:
            conn.close()
            flash("No hay stock suficiente para realizar la salida.", "danger")
            return redirect(url_for("nuevo_movimiento", tipo=tipo))

        # FECHA DEL MOVIMIENTO
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # REGISTRAR MOVIMIENTO
        conn.execute("""
            INSERT INTO movimientos (producto_id, tipo, cantidad, fecha, usuario, motivo)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (producto_id, tipo, cantidad, fecha, usuario, motivo))

        # ACTUALIZAR STOCK
        if tipo == "entrada":
            nuevo_stock = stock_actual + cantidad
        else:
            nuevo_stock = stock_actual - cantidad

        conn.execute(
            "UPDATE productos SET stock_actual = ? WHERE id = ?",
            (nuevo_stock, producto_id)
        )

        conn.commit()
        conn.close()

        flash("Movimiento registrado correctamente.", "success")
        return redirect(url_for("listar_movimientos"))

    # GET
    conn.close()
    return render_template(
        "movimiento_form.html",
        tipo=tipo,
        productos=productos,
    )

@app.route("/productos/<int:producto_id>")
def detalle_producto(producto_id):
    conn = get_db_connection()
    cur = conn.cursor()

    # Datos del producto
    cur.execute("SELECT * FROM productos WHERE id = ?;", (producto_id,))
    producto = cur.fetchone()
    if not producto:
        conn.close()
        flash("Producto no encontrado.", "warning")
        return redirect(url_for("listar_productos"))

    # Últimos movimientos de este producto
    cur.execute(
        """
        SELECT *
        FROM movimientos
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
# API REST PARA APP MÓVIL
# -------------------------
from flask import jsonify

@app.route("/api/productos", methods=["GET"])
def api_productos():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM productos ORDER BY nombre;")
    productos = [dict(p) for p in cur.fetchall()]
    conn.close()
    return jsonify(productos)

@app.route("/api/movimientos", methods=["POST"])
def api_nuevo_movimiento():
    data = request.get_json()
    tipo = data.get("tipo")
    producto_id = data.get("producto_id")
    cantidad = int(data.get("cantidad", 0))
    usuario = data.get("usuario", "")
    motivo = data.get("motivo", "")

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
        (producto_id, tipo, cantidad, fecha, usuario, motivo)
    )
    conn.execute("UPDATE productos SET stock_actual = ? WHERE id = ?", (nuevo_stock, producto_id))
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "nuevo_stock": nuevo_stock})

@app.route("/api/productos", methods=["POST"])
def api_nuevo_producto():
    data = request.get_json()
    nombre = data.get("nombre")
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
                int(data.get("stock_minimo", 0)), int(data.get("stock_actual", 0)),
                data.get("ubicacion"), data.get("proveedor"),
                float(data.get("precio", 0))
            )
        )
        conn.commit()
        producto_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({"ok": True, "id": producto_id})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5002)
    
