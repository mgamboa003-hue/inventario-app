"""
caja.py -- Modulo de Caja Chica (Blueprint de Flask).

Flujo:
  1. Admin entrega el fondo al comprador        -> /caja/entregas/nueva
  2. Se registran los documentos del dia         -> /caja/entregas/<id>/gastos/nuevo
     (foto de la factura/boleta: el timbre del SII se lee solo)
  3. El comprador rinde: ingresa el saldo que le queda y el sistema cuadra
  4. Admin revisa y aprueba                      -> queda cerrada
  5. Rendicion en PDF o Excel, por entrega o por periodo

Quien ve que:
  - admin: todas las entregas, crea, aprueba y reabre.
  - comprador: solo las entregas que le hicieron a el.

La logica (cuadre, timbre, fotos) vive en caja_logica.py y la exportacion en
caja_export.py.
"""

from datetime import date
from functools import wraps

from flask import (Blueprint, Response, abort, flash, jsonify, redirect,
                   render_template, request, send_file, session, url_for)

import caja_logica as cl
from db import ahora, ahora_str, get_db_connection, p
from services import registrar_auditoria
from turnos_logica import insertar, notificar, notificar_admins

bp = Blueprint("caja", __name__, url_prefix="/caja")

ROLES_CAJA = ("admin", "comprador")
MAX_FOTO_BYTES = 15 * 1024 * 1024


# ── Permisos y utilidades ────────────────────────────────────────
def _login_caja(f):
    @wraps(f)
    def deco(*a, **k):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        if session.get("role") not in ROLES_CAJA:
            flash("No tienes acceso a Caja chica.", "danger")
            return redirect(url_for("index"))
        return f(*a, **k)
    return deco


def _admin(f):
    @wraps(f)
    def deco(*a, **k):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("Solo un administrador puede hacer esto.", "danger")
            return redirect(url_for("caja.inicio"))
        return f(*a, **k)
    return deco


def _es_admin():
    return session.get("role") == "admin"


def _uid():
    return session.get("user_id")


def _yo():
    return session.get("nombre") or session.get("username") or "—"


def _auditar(registro_id, accion, detalle):
    registrar_auditoria("caja", registro_id, accion, _uid(), _yo(), detalle)


class _Conn:
    def __enter__(self):
        self.conn = get_db_connection()
        return self.conn, self.conn.cursor()

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
        return False


def _entrega_o_404(cur, entrega_id):
    e = cl.entrega_con_nombres(cur, entrega_id)
    if not e:
        abort(404)
    if not _es_admin() and e["comprador_id"] != _uid():
        abort(404)
    return e


def _gasto_o_404(cur, gasto_id):
    cur.execute(f"SELECT * FROM caja_gastos WHERE id = {p()}", (gasto_id,))
    g = cur.fetchone()
    if not g:
        abort(404)
    g = dict(g)
    e = _entrega_o_404(cur, g["entrega_id"])
    return g, e


def _fecha_iso(texto, por_defecto=None):
    try:
        return date.fromisoformat((texto or "").strip()[:10]).isoformat()
    except ValueError:
        return por_defecto


def _compradores(cur):
    activo = "TRUE" if _usa_pg() else "1"
    cur.execute(f"""SELECT id, nombre, username, role FROM usuarios
                    WHERE activo = {activo} AND role IN ('comprador', 'admin')
                    ORDER BY CASE WHEN role = 'comprador' THEN 0 ELSE 1 END, nombre""")
    return [dict(r) for r in cur.fetchall()]


def _usa_pg():
    from db import USE_POSTGRES
    return USE_POSTGRES


@bp.app_template_filter("clp")
def _filtro_clp(n):
    return cl.clp(n)


@bp.app_template_filter("rut")
def _filtro_rut(r):
    return cl.formatear_rut(r) or "—"


@bp.app_template_filter("fecha_cl")
def _filtro_fecha_cl(f):
    """'2026-11-18' -> '18-11-2026'"""
    if not f:
        return "—"
    s = str(f)[:10]
    try:
        return date.fromisoformat(s).strftime("%d-%m-%Y")
    except ValueError:
        return s


@bp.app_context_processor
def _ctx():
    return {"caja_tipos": cl.TIPOS_DOC, "caja_estados": cl.ESTADOS}


@bp.app_template_global()
def caja_pendientes():
    """Para el badge del portal: entregas rendidas por revisar (admin) o
    entregas abiertas propias (comprador)."""
    if not session.get("logged_in") or session.get("role") not in ROLES_CAJA:
        return 0
    try:
        with _Conn() as (conn, cur):
            if _es_admin():
                cur.execute("SELECT COUNT(*) AS n FROM caja_entregas WHERE estado = 'rendida'")
            else:
                cur.execute(f"SELECT COUNT(*) AS n FROM caja_entregas WHERE estado = 'abierta' AND comprador_id = {p()}",
                            (_uid(),))
            return cur.fetchone()["n"]
    except Exception:
        return 0


def _con_cuadre(cur, entregas):
    """Agrega el cuadre a cada entrega de la lista (una sola consulta de totales)."""
    if not entregas:
        return entregas
    ph = p()
    ids = [e["id"] for e in entregas]
    marcas = ",".join([ph] * len(ids))
    cur.execute(f"SELECT entrega_id, tipo_doc, monto FROM caja_gastos WHERE entrega_id IN ({marcas})", ids)
    por = {}
    for r in cur.fetchall():
        por.setdefault(r["entrega_id"], []).append(dict(r))
    for e in entregas:
        e["cuadre"] = cl.cuadre(e["monto_entregado"], por.get(e["id"], []), e.get("saldo_devuelto"))
    return entregas


def _listar_entregas(cur, desde=None, hasta=None, estado=None, comprador_id=None):
    ph = p()
    sql = """SELECT e.*, COALESCE(u.nombre, u.username) AS comprador
             FROM caja_entregas e LEFT JOIN usuarios u ON u.id = e.comprador_id WHERE 1=1"""
    params = []
    if desde:
        sql += f" AND e.fecha >= {ph}"
        params.append(desde)
    if hasta:
        sql += f" AND e.fecha <= {ph}"
        params.append(hasta)
    if estado:
        sql += f" AND e.estado = {ph}"
        params.append(estado)
    if comprador_id:
        sql += f" AND e.comprador_id = {ph}"
        params.append(comprador_id)
    sql += " ORDER BY e.fecha DESC, e.id DESC"
    cur.execute(sql, params)
    return _con_cuadre(cur, [dict(r) for r in cur.fetchall()])


def _rango(args):
    hoy = ahora().date()
    desde = _fecha_iso(args.get("desde"), (hoy.replace(day=1)).isoformat())
    hasta = _fecha_iso(args.get("hasta"), hoy.isoformat())
    if desde > hasta:
        desde, hasta = hasta, desde
    return desde, hasta


# ── Inicio ───────────────────────────────────────────────────────
@bp.route("/")
@_login_caja
def inicio():
    desde, hasta = _rango(request.args)
    comprador_f = request.args.get("comprador", type=int) if _es_admin() else _uid()
    with _Conn() as (conn, cur):
        if _es_admin():
            abiertas = _listar_entregas(cur, estado="abierta")
            por_revisar = _listar_entregas(cur, estado="rendida")
        else:
            abiertas = _listar_entregas(cur, estado="abierta", comprador_id=_uid())
            por_revisar = _listar_entregas(cur, estado="rendida", comprador_id=_uid())
        historial = _listar_entregas(cur, desde=desde, hasta=hasta, comprador_id=comprador_f)
        compradores = _compradores(cur) if _es_admin() else []
    tot = {
        "entregado": sum(e["monto_entregado"] for e in historial),
        "gastado": sum(e["cuadre"]["total_documentos"] for e in historial),
        "documentos": sum(e["cuadre"]["n_documentos"] for e in historial),
        "diferencias": sum(1 for e in historial if e["cuadre"]["cuadra"] is False),
    }
    return render_template("caja/inicio.html", abiertas=abiertas, por_revisar=por_revisar,
                           historial=historial, tot=tot, desde=desde, hasta=hasta,
                           compradores=compradores, comprador_f=comprador_f if _es_admin() else None)


# ── Entregas ─────────────────────────────────────────────────────
@bp.route("/entregas/nueva", methods=["GET", "POST"])
@_admin
def nueva_entrega():
    with _Conn() as (conn, cur):
        compradores = _compradores(cur)
        if request.method == "POST":
            comprador_id = request.form.get("comprador_id", type=int)
            monto = cl.a_entero(request.form.get("monto"))
            fecha = _fecha_iso(request.form.get("fecha"), ahora().date().isoformat())
            nota = (request.form.get("nota") or "").strip()[:300]
            if not comprador_id or comprador_id not in [c["id"] for c in compradores]:
                flash("Elige a quién le entregas el dinero.", "warning")
            elif not monto or monto <= 0:
                flash("Escribe el monto entregado.", "warning")
            else:
                eid = insertar(cur, f"""INSERT INTO caja_entregas
                    (fecha, comprador_id, monto_entregado, nota, estado, entregado_por, created_at)
                    VALUES ({p()},{p()},{p()},{p()},'abierta',{p()},{p()})""",
                    (fecha, comprador_id, monto, nota, _yo(), ahora_str()))
                if comprador_id != _uid():
                    notificar(cur, comprador_id,
                              f"Caja chica: {_yo()} te entregó {cl.clp(monto)}. Registra ahí tus boletas y facturas.",
                              url_for("caja.ver_entrega", entrega_id=eid))
                conn.commit()
                _auditar(eid, "crear", f"Entrega de {cl.clp(monto)} a usuario {comprador_id}")
                flash(f"Entrega de {cl.clp(monto)} registrada.", "success")
                return redirect(url_for("caja.ver_entrega", entrega_id=eid))
    hoy = ahora().date().isoformat()
    return render_template("caja/entrega_form.html", compradores=compradores, hoy=hoy, form=request.form)


@bp.route("/entregas/<int:entrega_id>")
@_login_caja
def ver_entrega(entrega_id):
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        gastos = cl.gastos_de(cur, entrega_id)
    c = cl.cuadre(e["monto_entregado"], gastos, e.get("saldo_devuelto"))
    return render_template("caja/entrega_detalle.html", e=e, gastos=gastos, c=c,
                           es_admin=_es_admin(), puede_editar=e["estado"] == "abierta")


@bp.route("/entregas/<int:entrega_id>/editar", methods=["POST"])
@_admin
def editar_entrega(entrega_id):
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        if e["estado"] != "abierta":
            flash("Solo se puede editar una entrega abierta. Reábrela primero.", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))
        monto = cl.a_entero(request.form.get("monto"))
        fecha = _fecha_iso(request.form.get("fecha"), e["fecha"])
        nota = (request.form.get("nota") or "").strip()[:300]
        if not monto or monto <= 0:
            flash("El monto debe ser mayor a cero.", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))
        cur.execute(f"UPDATE caja_entregas SET monto_entregado = {p()}, fecha = {p()}, nota = {p()} WHERE id = {p()}",
                    (monto, fecha, nota, entrega_id))
    _auditar(entrega_id, "editar", f"Monto {cl.clp(e['monto_entregado'])} -> {cl.clp(monto)}, fecha {fecha}")
    flash("Entrega actualizada.", "success")
    return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))


@bp.route("/entregas/<int:entrega_id>/eliminar", methods=["POST"])
@_admin
def eliminar_entrega(entrega_id):
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        if cl.gastos_de(cur, entrega_id):
            flash("Esta entrega ya tiene documentos. Elimínalos primero si de verdad quieres borrarla.", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))
        cur.execute(f"DELETE FROM caja_entregas WHERE id = {p()}", (entrega_id,))
    _auditar(entrega_id, "eliminar", f"Entrega de {cl.clp(e['monto_entregado'])} del {e['fecha']}")
    flash("Entrega eliminada.", "success")
    return redirect(url_for("caja.inicio"))


@bp.route("/entregas/<int:entrega_id>/rendir", methods=["POST"])
@_login_caja
def rendir(entrega_id):
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        if e["estado"] != "abierta":
            flash("Esta entrega ya fue rendida.", "info")
            return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))
        saldo = cl.a_entero(request.form.get("saldo_devuelto"))
        obs = (request.form.get("obs_cierre") or "").strip()[:500]
        if saldo is None or saldo < 0:
            flash("Escribe el saldo que queda en efectivo (si no queda nada, pon 0).", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id) + "#cierre")
        gastos = cl.gastos_de(cur, entrega_id)
        c = cl.cuadre(e["monto_entregado"], gastos, saldo)
        if not c["cuadra"] and not obs:
            flash(f"El cuadre tiene una diferencia de {cl.clp(c['diferencia'])}. "
                  "Explica la diferencia en la observación para poder rendir.", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id, saldo=saldo) + "#cierre")
        cur.execute(f"""UPDATE caja_entregas SET estado = 'rendida', saldo_devuelto = {p()}, diferencia = {p()},
                        obs_cierre = {p()}, rendida_por = {p()}, rendida_at = {p()} WHERE id = {p()}""",
                    (saldo, c["diferencia"], obs, _yo(), ahora_str(), entrega_id))
        if not _es_admin():
            txt = "cuadra" if c["cuadra"] else f"diferencia de {cl.clp(c['diferencia'])}"
            notificar_admins(cur, f"Caja chica: {_yo()} rindió la entrega del {e['fecha']} ({txt}).",
                             url_for("caja.ver_entrega", entrega_id=entrega_id))
    _auditar(entrega_id, "rendir", f"Saldo {cl.clp(saldo)}, diferencia {cl.clp(c['diferencia'])}")
    if c["cuadra"]:
        flash("Rendición lista: la caja cuadra perfecto.", "success")
    else:
        flash(f"Rendición enviada con diferencia de {cl.clp(c['diferencia'])}.", "warning")
    return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))


@bp.route("/entregas/<int:entrega_id>/aprobar", methods=["POST"])
@_admin
def aprobar(entrega_id):
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        if e["estado"] != "rendida":
            flash("Solo se aprueban entregas rendidas.", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))
        obs = (request.form.get("obs_aprobacion") or "").strip()[:500]
        cur.execute(f"""UPDATE caja_entregas SET estado = 'aprobada', aprobada_por = {p()}, aprobada_at = {p()},
                        obs_aprobacion = {p()} WHERE id = {p()}""", (_yo(), ahora_str(), obs, entrega_id))
        if e["comprador_id"] != _uid():
            notificar(cur, e["comprador_id"], f"Caja chica: tu rendición del {e['fecha']} fue aprobada.",
                      url_for("caja.ver_entrega", entrega_id=entrega_id))
    _auditar(entrega_id, "aprobar", obs)
    flash("Rendición aprobada.", "success")
    return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))


@bp.route("/entregas/<int:entrega_id>/reabrir", methods=["POST"])
@_admin
def reabrir(entrega_id):
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        cur.execute(f"""UPDATE caja_entregas SET estado = 'abierta', saldo_devuelto = NULL, diferencia = NULL,
                        aprobada_por = NULL, aprobada_at = NULL WHERE id = {p()}""", (entrega_id,))
    _auditar(entrega_id, "reabrir", f"Estaba {e['estado']}")
    flash("Entrega reabierta: ya se pueden corregir documentos.", "info")
    return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))


# ── Documentos (gastos) ──────────────────────────────────────────
def _datos_gasto_form(form):
    tipo = form.get("tipo_doc") or "33"
    if tipo not in cl.TIPOS_DOC:
        tipo = "33"
    return {
        "tipo_doc": tipo,
        "folio": (form.get("folio") or "").strip()[:20] or None,
        "rut_emisor": cl.normalizar_rut(form.get("rut_emisor")) or None,
        "proveedor": (form.get("proveedor") or "").strip()[:120],
        "fecha_doc": _fecha_iso(form.get("fecha_doc"), ahora().date().isoformat()),
        "monto": cl.a_entero(form.get("monto")),
        "categoria": form.get("categoria") if form.get("categoria") in cl.CATEGORIAS else "Otros",
        "motivo": (form.get("motivo") or "").strip()[:300],
        "foto": form.get("foto") if cl.referencia_valida(form.get("foto")) else None,
        "origen": "timbre" if form.get("origen") == "timbre" else "manual",
    }


def _validar_gasto(cur, d, excluir_id=None):
    if not d["monto"] or d["monto"] <= 0:
        return "Falta el monto del documento."
    if d["tipo_doc"] != "VALE" and not d["folio"]:
        return "Falta el número (folio) del documento."
    if d["rut_emisor"] and not cl.rut_valido(d["rut_emisor"]):
        return f"El RUT {d['rut_emisor']} no es válido. Revísalo."
    if d["tipo_doc"] == "VALE" and not d["motivo"]:
        return "Para un vale sin documento, explica en qué se gastó."
    rep = cl.documento_repetido(cur, d["rut_emisor"], d["tipo_doc"], d["folio"], excluir_id)
    if rep:
        return (f"Ese documento ya está rendido (entrega del {rep['fecha']}, N° {rep['entrega_id']}). "
                "No se puede rendir dos veces.")
    return None


@bp.route("/entregas/<int:entrega_id>/gastos/nuevo", methods=["GET", "POST"])
@_login_caja
def nuevo_gasto(entrega_id):
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        if e["estado"] != "abierta":
            flash("La entrega ya fue rendida; no se pueden agregar documentos.", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))
        gastos = cl.gastos_de(cur, entrega_id)
        c = cl.cuadre(e["monto_entregado"], gastos)
        if request.method == "POST":
            d = _datos_gasto_form(request.form)
            error = _validar_gasto(cur, d)
            if error:
                flash(error, "warning")
            else:
                gid = insertar(cur, f"""INSERT INTO caja_gastos (entrega_id, tipo_doc, folio, rut_emisor, proveedor,
                        fecha_doc, monto, categoria, motivo, foto, origen, creado_por, created_at)
                        VALUES ({','.join([p()] * 13)})""",
                    (entrega_id, d["tipo_doc"], d["folio"], d["rut_emisor"], d["proveedor"], d["fecha_doc"],
                     d["monto"], d["categoria"], d["motivo"], d["foto"], d["origen"], _yo(), ahora_str()))
                cl.recordar_emisor(cur, d["rut_emisor"], d["proveedor"], ahora_str())
                conn.commit()
                _auditar(entrega_id, "agregar_documento",
                         f"{cl.TIPOS_DOC[d['tipo_doc']]} {d['folio'] or ''} {cl.clp(d['monto'])} (doc {gid})")
                nuevo_disp = c["disponible"] - cl.signo(d["tipo_doc"]) * d["monto"]
                flash(f"Documento guardado: {cl.clp(d['monto'])}. Quedan {cl.clp(nuevo_disp)} por rendir.",
                      "success" if nuevo_disp >= 0 else "warning")
                if request.form.get("otro"):
                    return redirect(url_for("caja.nuevo_gasto", entrega_id=entrega_id))
                return redirect(url_for("caja.ver_entrega", entrega_id=entrega_id))
    return render_template("caja/gasto_form.html", e=e, c=c, g=request.form if request.method == "POST" else {},
                           categorias=cl.CATEGORIAS, tipos=cl.TIPOS_MANUALES, editando=False)


@bp.route("/gastos/<int:gasto_id>/editar", methods=["GET", "POST"])
@_login_caja
def editar_gasto(gasto_id):
    with _Conn() as (conn, cur):
        g, e = _gasto_o_404(cur, gasto_id)
        if e["estado"] != "abierta":
            flash("La entrega ya fue rendida; pide que la reabran para corregir.", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=e["id"]))
        gastos = cl.gastos_de(cur, e["id"])
        c = cl.cuadre(e["monto_entregado"], [x for x in gastos if x["id"] != gasto_id])
        if request.method == "POST":
            d = _datos_gasto_form(request.form)
            if not d["foto"]:
                d["foto"] = g["foto"] if not request.form.get("quitar_foto") else None
            if g["origen"] == "timbre" and request.form.get("origen") != "manual":
                d["origen"] = "timbre"
            error = _validar_gasto(cur, d, excluir_id=gasto_id)
            if error:
                flash(error, "warning")
                g = {**g, **{k: v for k, v in request.form.items()}}
            else:
                cur.execute(f"""UPDATE caja_gastos SET tipo_doc={p()}, folio={p()}, rut_emisor={p()}, proveedor={p()},
                                fecha_doc={p()}, monto={p()}, categoria={p()}, motivo={p()}, foto={p()}, origen={p()}
                                WHERE id = {p()}""",
                            (d["tipo_doc"], d["folio"], d["rut_emisor"], d["proveedor"], d["fecha_doc"], d["monto"],
                             d["categoria"], d["motivo"], d["foto"], d["origen"], gasto_id))
                cl.recordar_emisor(cur, d["rut_emisor"], d["proveedor"], ahora_str())
                conn.commit()
                _auditar(e["id"], "editar_documento", f"Doc {gasto_id}: {cl.clp(g['monto'])} -> {cl.clp(d['monto'])}")
                flash("Documento actualizado.", "success")
                return redirect(url_for("caja.ver_entrega", entrega_id=e["id"]))
    return render_template("caja/gasto_form.html", e=e, c=c, g=g, categorias=cl.CATEGORIAS,
                           tipos=cl.TIPOS_MANUALES, editando=True)


@bp.route("/gastos/<int:gasto_id>/eliminar", methods=["POST"])
@_login_caja
def eliminar_gasto(gasto_id):
    with _Conn() as (conn, cur):
        g, e = _gasto_o_404(cur, gasto_id)
        if e["estado"] != "abierta":
            flash("La entrega ya fue rendida; pide que la reabran para corregir.", "warning")
            return redirect(url_for("caja.ver_entrega", entrega_id=e["id"]))
        cur.execute(f"DELETE FROM caja_gastos WHERE id = {p()}", (gasto_id,))
    _auditar(e["id"], "eliminar_documento",
             f"{cl.TIPOS_DOC.get(g['tipo_doc'], g['tipo_doc'])} {g['folio'] or ''} {cl.clp(g['monto'])}")
    flash("Documento eliminado.", "success")
    return redirect(url_for("caja.ver_entrega", entrega_id=e["id"]))


@bp.route("/gastos/<int:gasto_id>/foto")
@_login_caja
def foto_gasto(gasto_id):
    with _Conn() as (conn, cur):
        g, e = _gasto_o_404(cur, gasto_id)
    data = cl.leer_foto(g["foto"])
    if not data:
        abort(404)
    resp = Response(data, mimetype="image/webp")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.route("/foto")
@_login_caja
def foto_ref():
    """Foto recien subida que aun no se guarda en un documento (vista previa)."""
    data = cl.leer_foto(request.args.get("ref", ""))
    if not data:
        abort(404)
    resp = Response(data, mimetype="image/webp")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@bp.route("/leer-documento", methods=["POST"])
@_login_caja
def leer_documento():
    """Recibe la foto (desde el celular), busca el timbre del SII y la guarda.
    Con guardar=0 solo lee (foto de cerca del timbre, no se guarda)."""
    archivo = request.files.get("foto")
    if not archivo:
        return jsonify(ok=False, error="No llegó la foto."), 400
    data = archivo.read(MAX_FOTO_BYTES + 1)
    if len(data) > MAX_FOTO_BYTES:
        return jsonify(ok=False, error="La foto es demasiado grande (máx. 15 MB)."), 400
    try:
        datos = cl.leer_timbre(data)
    except Exception:
        datos = None
    ref = None
    if request.form.get("guardar", "1") == "1":
        try:
            ref = cl.guardar_foto(cl.preparar_foto(data), prefijo="doc_")
        except Exception:
            return jsonify(ok=False, error="No pude abrir la imagen. Prueba con otra foto (JPG o PNG)."), 400
    salida = {"ok": True, "foto": ref, "leido": bool(datos)}
    if datos:
        with _Conn() as (conn, cur):
            conocido = cl.nombre_emisor(cur, datos["rut_emisor"])
            rep = cl.documento_repetido(cur, datos["rut_emisor"], datos["tipo_doc"], datos["folio"],
                                        request.form.get("excluir", type=int))
        datos["proveedor"] = conocido or datos.get("proveedor") or ""
        datos["tipo_nombre"] = cl.TIPOS_DOC.get(datos["tipo_doc"], f"Documento tipo {datos['tipo_doc']}")
        if datos["tipo_doc"] not in cl.TIPOS_DOC:
            datos["tipo_doc"] = "33"
        salida["datos"] = datos
        if datos["tipo_doc"] == "52":
            salida["aviso"] = ("Esto es una guía de despacho: no es comprobante de pago y su monto puede no ser "
                               "el valor real. Si existe, rinde la factura o boleta de esa compra.")
        if rep:
            salida["repetido"] = (f"¡Ojo! Este documento ya está rendido en la entrega N° {rep['entrega_id']} "
                                  f"del {rep['fecha']}.")
    return jsonify(salida)


@bp.route("/emisor")
@_login_caja
def buscar_emisor():
    rut = cl.normalizar_rut(request.args.get("rut"))
    with _Conn() as (conn, cur):
        nombre = cl.nombre_emisor(cur, rut)
    return jsonify(rut=rut, nombre=nombre, valido=cl.rut_valido(rut))


# ── Exportar ─────────────────────────────────────────────────────
@bp.route("/entregas/<int:entrega_id>/pdf")
@_login_caja
def pdf_entrega(entrega_id):
    import caja_export as cx
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        gastos = cl.gastos_de(cur, entrega_id)
    datos = cx.pdf_rendicion(e, gastos, con_fotos=request.args.get("fotos", "1") == "1")
    return send_file(datos, mimetype="application/pdf", as_attachment=request.args.get("ver") != "1",
                     download_name=f"rendicion_caja_chica_N{entrega_id}_{e['fecha']}.pdf")


@bp.route("/entregas/<int:entrega_id>/excel")
@_login_caja
def excel_entrega(entrega_id):
    import caja_export as cx
    with _Conn() as (conn, cur):
        e = _entrega_o_404(cur, entrega_id)
        gastos = cl.gastos_de(cur, entrega_id)
    datos = cx.excel_rendicion(e, gastos)
    return send_file(datos, as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     download_name=f"rendicion_caja_chica_N{entrega_id}_{e['fecha']}.xlsx")


def _datos_periodo(cur, desde, hasta, comprador_id):
    entregas = _listar_entregas(cur, desde=desde, hasta=hasta, comprador_id=comprador_id)
    ph = p()
    gastos = []
    if entregas:
        ids = [e["id"] for e in entregas]
        cur.execute(f"""SELECT g.*, e.fecha AS fecha_entrega FROM caja_gastos g
                        JOIN caja_entregas e ON e.id = g.entrega_id
                        WHERE g.entrega_id IN ({','.join([ph] * len(ids))})
                        ORDER BY e.fecha, g.entrega_id, g.fecha_doc, g.id""", ids)
        gastos = [dict(r) for r in cur.fetchall()]
    return sorted(entregas, key=lambda x: (x["fecha"], x["id"])), gastos


@bp.route("/reporte/<formato>")
@_login_caja
def reporte(formato):
    import caja_export as cx
    desde, hasta = _rango(request.args)
    comprador_id = request.args.get("comprador", type=int) if _es_admin() else _uid()
    with _Conn() as (conn, cur):
        entregas, gastos = _datos_periodo(cur, desde, hasta, comprador_id)
    nombre = f"caja_chica_{desde}_a_{hasta}"
    if formato == "pdf":
        return send_file(cx.pdf_periodo(entregas, gastos, desde, hasta), mimetype="application/pdf",
                         as_attachment=True, download_name=nombre + ".pdf")
    if formato == "excel":
        return send_file(cx.excel_periodo(entregas, gastos, desde, hasta), as_attachment=True,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         download_name=nombre + ".xlsx")
    abort(404)
