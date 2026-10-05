"""
portal.py -- Estructura del Portal Mantenimiento Wintec.

Define las 5 AREAS del portal (Bodega, Compras, Personal, Reportes,
Administracion), que secciones tiene cada una, quien las ve segun su rol,
y a que area pertenece cada pantalla de la app. Con eso base.html arma:
  - la barra superior con las areas,
  - la cabecera de cada pantalla con sus pestanas,
  - las tarjetas de la pantalla de bienvenida.

Tambien trae dos pantallas nuevas: /reportes y /escanear (QR).
"""

from functools import wraps

from flask import Blueprint, redirect, render_template, request, session, url_for

bp = Blueprint("portal", __name__)

TODOS = ("admin", "solicitante", "viewer", "comprador")
NO_COMPRADOR = ("admin", "solicitante", "viewer")
ADMIN = ("admin",)

# Cada seccion: (etiqueta, endpoint, roles, solo_super_admin, endpoints/prefijos que "le pertenecen", badge)
AREAS = [
    {
        "clave": "bodega", "num": "01", "nombre": "Bodega", "icono": "bi-box-seam",
        "descripcion": "Inventario de repuestos, movimientos y ubicaciones",
        "roles": NO_COMPRADOR,
        "secciones": [
            ("Resumen", "bodega_resumen", NO_COMPRADOR, False, ["bodega_resumen"], None),
            ("Inventario", "listar_productos", NO_COMPRADOR, False,
             ["producto", "etiqueta_producto", "etiquetas_lote"], None),
            ("Movimientos", "listar_movimientos", NO_COMPRADOR, False, ["movimiento"], None),
            ("Ubicaciones", "listar_ubicaciones", NO_COMPRADOR, False,
             ["listar_ubicaciones", "ver_ubicacion", "etiqueta_ubicacion", "etiquetas_ubicaciones_lote",
              "cambiar_planta_ubicacion"], None),
        ],
    },
    {
        "clave": "compras", "num": "02", "nombre": "Compras", "icono": "bi-clipboard-check",
        "descripcion": "Solicitudes del equipo, cotizaciones y órdenes de compra",
        "roles": TODOS,
        "secciones": [
            ("Solicitudes", "listar_solicitudes", TODOS, False, ["solicitud"], None),
            ("Cotizaciones", "listar_cotizaciones", ADMIN, False, ["cotizacion"], None),
            ("Órdenes de compra", "listar_ordenes_compra", ADMIN, False, ["orden"], None),
        ],
    },
    {
        "clave": "personal", "num": "03", "nombre": "Personal", "nombre_largo": "Personal · Turnos",
        "icono": "bi-calendar-week",
        "descripcion": "Turnos de fin de semana, cambios y correo de ingreso",
        "roles": TODOS,
        "secciones": [
            ("Calendario", "turnos.calendario", TODOS, False, ["turnos.calendario", "turnos.pedir_cambio",
                                                             "turnos.cancelar_cambio"], None),
            ("Planificación", "turnos.planificacion", ADMIN, False,
             ["turnos.planificacion", "turnos.guardar_planificacion", "turnos.agregar_dia",
              "turnos.editar_dia", "turnos.recalcular_ahora"], None),
            ("Cambios", "turnos.cambios", ADMIN, False, ["turnos.cambios", "turnos.resolver_cambio"],
             "turnos_cambios_pendientes"),
            ("Correo de ingreso", "turnos.correo_ingreso", ADMIN, False, ["turnos.correo"],
             "turnos_correos_pendientes"),
            ("Técnicos y ausencias", "turnos.tecnicos", ADMIN, False,
             ["turnos.tecnicos", "turnos.agregar_tecnico", "turnos.actualizar_tecnico",
              "turnos.agregar_ausencia", "turnos.eliminar_ausencia"], None),
            ("Reparto", "turnos.equidad", TODOS, False, ["turnos.equidad"], None),
        ],
    },
    {
        "clave": "reportes", "num": "04", "nombre": "Reportes", "nombre_largo": "Reportes Excel",
        "icono": "bi-bar-chart-line",
        "descripcion": "Stock bajo, pedidos, valorización, rotación y clasificación ABC",
        "roles": ADMIN,
        "secciones": [
            ("Reportes", "portal.reportes", ADMIN, False, ["portal.reportes", "exportar_"], None),
        ],
    },
    {
        "clave": "admin", "num": "05", "nombre": "Administración", "icono": "bi-gear",
        "descripcion": "Categorías, equipos, proveedores y ubicaciones",
        "descripcion_super": "Usuarios, catálogos, auditoría y respaldos",
        "roles": ADMIN,
        "secciones": [
            ("Usuarios", "admin_usuarios", ADMIN, True,
             ["admin_usuarios", "crear_usuario", "editar_usuario", "toggle_usuario", "cambiar_planta_usuario"], None),
            ("Categorías", "admin_categorias", ADMIN, False, ["admin_categorias", "admin_nuevo_catalogo",
                                                          "admin_eliminar_catalogo"], None),
            ("Equipos", "admin_equipos", ADMIN, False, ["admin_equipos"], None),
            ("Proveedores", "admin_proveedores", ADMIN, False, ["admin_proveedores", "nuevo_proveedor",
                                                            "eliminar_proveedor"], None),
            ("Unificar nombres", "admin_unificar_catalogos", ADMIN, True, ["admin_unificar_catalogos"], None),
            ("Ubicaciones", "admin_ubicaciones", ADMIN, False, ["admin_ubicaciones"], None),
            ("Tokens de API", "admin_api_tokens", ADMIN, False,
             ["admin_api_tokens", "crear_api_token_route", "revocar_token_route"], None),
            ("Auditoría", "admin_auditoria", ADMIN, True, ["admin_auditoria"], None),
            ("Accesos", "admin_accesos", ADMIN, True, ["admin_accesos"], None),
            ("Sistema", "admin_sistema", ADMIN, True,
             ["admin_sistema", "ejecutar_backup", "descargar_backup", "verificar_fotos",
              "enviar_alerta_manual"], None),
        ],
    },
]


def _puede(roles, solo_super):
    rol = session.get("role")
    if rol not in roles:
        return False
    if solo_super and not session.get("super_admin"):
        return False
    return True


def _pertenece(endpoint, patrones):
    """'exportar_' o 'turnos.correo' = empieza con; 'producto' = contiene."""
    for pat in patrones:
        if pat.endswith("_") or "." in pat:
            if endpoint.startswith(pat):
                return True
        elif pat in endpoint:
            return True
    return False


def areas_visibles():
    salida = []
    for a in AREAS:
        if session.get("role") not in a["roles"]:
            continue
        secciones = [
            {"etiqueta": s[0], "endpoint": s[1], "badge": s[5], "patrones": s[4]}
            for s in a["secciones"] if _puede(s[2], s[3])
        ]
        if not secciones:
            continue
        desc = a.get("descripcion_super") if (a.get("descripcion_super") and session.get("super_admin")) else a["descripcion"]
        salida.append({**a, "secciones": secciones, "inicio": secciones[0]["endpoint"], "descripcion": desc})
    return salida


def ubicar(endpoint, areas):
    """Area y seccion a la que pertenece la pantalla actual."""
    if not endpoint:
        return None, None
    # Primero coincidencias exactas (evita que 'admin_ubicaciones' caiga en Bodega)
    for a in areas:
        for s in a["secciones"]:
            if endpoint == s["endpoint"] or endpoint in s["patrones"]:
                return a, s
    for a in areas:
        for s in a["secciones"]:
            if _pertenece(endpoint, s["patrones"]):
                return a, s
    return None, None


@bp.app_context_processor
def _inyectar_portal():
    if not session.get("logged_in"):
        return {}
    areas = areas_visibles()
    area, seccion = ubicar(request.endpoint or "", areas)
    return {"portal_areas": areas, "portal_area": area, "portal_seccion": seccion}


DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


@bp.app_template_global()
def saludo_portal():
    """Saludo y fecha de hoy (hora de Chile) para la bienvenida."""
    from db import ahora
    t = ahora()
    saludo = "Buenos días" if t.hour < 12 else ("Buenas tardes" if t.hour < 20 else "Buenas noches")
    return {"saludo": saludo, "fecha": f"{DIAS[t.weekday()]} {t.day:02d} · {MESES[t.month]} {t.year}"}


def _login(f):
    @wraps(f)
    def deco(*a, **k):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*a, **k)
    return deco


def _admin(f):
    @wraps(f)
    def deco(*a, **k):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            return redirect(url_for("index"))
        return f(*a, **k)
    return deco


REPORTES = [
    ("exportar_stock_bajo", "Stock bajo", "Repuestos bajo el mínimo, para reponer.", "bi-exclamation-triangle"),
    ("exportar_pedidos", "Pedidos por proveedor", "Lo que falta, agrupado por proveedor, listo para pedir.", "bi-bag-check"),
    ("exportar_valorizacion", "Valorización", "Valor del inventario por categoría y repuesto.", "bi-cash-stack"),
    ("exportar_rotacion", "Rotación", "Qué repuestos se mueven más y cuáles están quietos.", "bi-arrow-repeat"),
    ("exportar_abc", "Clasificación ABC", "Repuestos según su importancia por consumo.", "bi-bar-chart-steps"),
]


@bp.route("/reportes")
@_admin
def reportes():
    return render_template("portal/reportes.html", reportes=REPORTES)


@bp.route("/admin/sistema/probar-correo", methods=["POST"])
@_admin
def probar_correo():
    """Envia un correo de prueba y muestra el resultado real (o el error)."""
    from flask import flash
    from services import enviar_email, registrar_auditoria
    destino = (request.form.get("destino") or "").strip()
    if not destino or "@" not in destino:
        flash("Escribe un correo de destino válido.", "warning")
        return redirect(url_for("admin_sistema"))
    ok, detalle = enviar_email([destino], "Prueba de correo — Portal Mantenimiento Wintec",
                               "<p>Este es un correo de prueba del Portal Mantenimiento Wintec.</p>"
                               "<p>Si lo recibiste, la recuperación de contraseña y los avisos por correo funcionan.</p>")
    registrar_auditoria("sistema", None, "probar_correo", session.get("user_id"), session.get("nombre"), detalle)
    flash(("Correo de prueba enviado. Revisa la bandeja de " + destino + " (y la carpeta de spam).") if ok
          else ("No se pudo enviar: " + detalle), "success" if ok else "danger")
    return redirect(url_for("admin_sistema"))


@bp.route("/escanear")
@_login
def escanear():
    return render_template("portal/escanear.html")
