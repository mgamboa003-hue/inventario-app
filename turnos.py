"""
turnos.py -- Modulo de turnos de fin de semana (Blueprint de Flask).

Pantallas:
  /turnos                 Calendario (todos los usuarios)
  /turnos/planificacion   Marcar dias trabajados y personas (admin)
  /turnos/cambios         Solicitudes de cambio (admin)
  /turnos/tecnicos        Quienes rotan, ocasionales y ausencias (admin)
  /turnos/equidad         Resumen de cuantos turnos lleva cada uno
  /notificaciones         Avisos dentro de la app (todos)

La logica de reparto vive en turnos_logica.py.
"""

from datetime import date, timedelta
from functools import wraps

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)

from db import get_db_connection, p, USE_POSTGRES, ahora_str
from services import registrar_auditoria
import turnos_logica as tl
import turnos_correo as tc

bp = Blueprint("turnos", __name__)


# ── Permisos ─────────────────────────────────────────────────────
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
            flash("No tienes permiso para realizar esta acción.", "danger")
            return redirect(url_for("turnos.calendario"))
        return f(*a, **k)
    return deco


def _es_admin():
    return session.get("role") == "admin"


def _uid():
    return session.get("user_id")


def _auditar(registro_id, accion, detalle):
    registrar_auditoria("turnos", registro_id, accion, _uid(), session.get("nombre"), detalle)


class _Conn:
    """with _Conn() as (conn, cur): ... -> commit al salir sin error."""
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


def _url_cal():
    return url_for("turnos.calendario")


def _recalcular(cur):
    faltan = tl.recalcular(cur, _url_cal())
    revisar_correos_enviados(cur)
    if faltan:
        dias = ", ".join(tl.fecha_larga(f) for f in sorted(faltan))
        flash(f"Atención: no hay suficientes técnicos disponibles para: {dias}.", "warning")
    return faltan


def _nombres(cur):
    cur.execute("SELECT id, nombre, username FROM usuarios")
    return {r["id"]: (r["nombre"] or r["username"]) for r in cur.fetchall()}


def _dias_con_asignados(cur, desde, hasta):
    """Dias trabajados en [desde, hasta] con sus personas asignadas."""
    ph = p()
    cur.execute(f"SELECT * FROM turnos_dias WHERE fecha >= {ph} AND fecha <= {ph} ORDER BY fecha",
                (desde.isoformat(), hasta.isoformat()))
    dias = [dict(r) for r in cur.fetchall()]
    if not dias:
        return []
    ids = [d["id"] for d in dias]
    marcas = ",".join([ph] * len(ids))
    cur.execute(f"""SELECT a.*, u.nombre, u.username, t.linea AS linea_habitual
                    FROM turnos_asignaciones a
                    JOIN usuarios u ON u.id = a.usuario_id
                    LEFT JOIN turnos_tecnicos t ON t.usuario_id = a.usuario_id
                    WHERE a.dia_id IN ({marcas}) ORDER BY a.id""", ids)
    por_dia = {}
    for r in cur.fetchall():
        r = dict(r)
        r["nombre"] = r["nombre"] or r["username"]
        r["linea_efectiva"] = tc.linea_efectiva(r)
        por_dia.setdefault(r["dia_id"], []).append(r)
    cur.execute(f"""SELECT * FROM turnos_cambios WHERE estado = 'pendiente'
                    AND dia_id IN ({marcas})""", ids)
    pendientes = {}
    for r in cur.fetchall():
        pendientes.setdefault(r["dia_id"], {})[r["usuario_id"]] = dict(r)
    cur.execute(f"""SELECT usuario_id, desde, hasta FROM turnos_ausencias
                    WHERE hasta >= {ph} AND desde <= {ph}""", (desde.isoformat(), hasta.isoformat()))
    ausencias = [dict(r) for r in cur.fetchall()]
    for d in dias:
        f = tl.a_fecha(d["fecha"])
        d["fecha_obj"] = f
        d["dia_semana"] = tl.DIAS_SEMANA[f.weekday()]
        d["asignados"] = por_dia.get(d["id"], [])
        d["pendientes"] = pendientes.get(d["id"], {})
        d["faltan"] = max(d["personas"] - len(d["asignados"]), 0)
        d["sobran"] = max(len(d["asignados"]) - d["personas"], 0)
        d["conflictos"] = [a for a in d["asignados"]
                           if any(x["usuario_id"] == a["usuario_id"] and x["desde"] <= d["fecha"] <= x["hasta"]
                                  for x in ausencias)]
    return dias


def _agrupar_por_semana(dias):
    """Agrupa sabado+domingo del mismo fin de semana; un dia de semana
    (feriado u otro) va en su propio grupo."""
    grupos = []
    for d in dias:
        f = d["fecha_obj"]
        if f.weekday() >= 5:
            clave = f - timedelta(days=f.weekday() - 5)   # sabado de ese fin de semana
        else:
            clave = f
        if not grupos or grupos[-1]["clave"] != clave:
            grupos.append({"clave": clave, "dias": []})
        grupos[-1]["dias"].append(d)
    for g in grupos:
        fs = [d["fecha_obj"] for d in g["dias"]]
        g["titulo"] = _titulo_rango(min(fs), max(fs))
        g["es_fin_de_semana"] = all(f.weekday() >= 5 for f in fs)
    return grupos


def _titulo_rango(a, b):
    if a == b:
        return f"{a.day} de {tl.MESES[a.month]}"
    if a.month == b.month:
        return f"{a.day} y {b.day} de {tl.MESES[a.month]}"
    return f"{a.day} de {tl.MESES[a.month]} y {b.day} de {tl.MESES[b.month]}"


def _parse_fecha(valor):
    try:
        return date.fromisoformat((valor or "").strip()[:10])
    except ValueError:
        return None


def _entero(valor, defecto, minimo=0, maximo=20):
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return defecto


@bp.app_template_filter("fecha_larga")
def _f_fecha_larga(v):
    if not v:
        return ""
    texto = tl.fecha_larga(v)
    return texto[0].upper() + texto[1:]


@bp.app_template_filter("primera_mayuscula")
def _f_primera_mayuscula(v):
    v = str(v or "")
    return v[:1].upper() + v[1:]


@bp.app_template_filter("fecha_corta")
def _f_fecha_corta(v):
    if not v:
        return ""
    f = tl.a_fecha(v)
    return f"{tl.DIAS_SEMANA[f.weekday()][:3]} {f.day:02d}/{f.month:02d}/{f.year}"


@bp.app_context_processor
def _inyectar_notificaciones():
    """Contador de la campanita (avisos sin leer) para el menu superior."""
    if not session.get("logged_in") or not session.get("user_id"):
        return {}
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        ph = p()
        cur.execute(f"SELECT COUNT(*) AS n FROM notificaciones WHERE usuario_id = {ph} AND leida = 0",
                    (session.get("user_id"),))
        n = cur.fetchone()["n"]
        datos = {"notif_sin_leer": n}
        if session.get("role") == "admin":
            cur.execute("SELECT COUNT(*) AS n FROM turnos_cambios WHERE estado = 'pendiente'")
            datos["turnos_cambios_pendientes"] = cur.fetchone()["n"]
            datos["turnos_correos_pendientes"] = len(correos_pendientes(cur))
        return datos
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {}
    finally:
        if conn is not None:
            conn.close()


# ═══════════════════════════════════════════════════════════════
# CALENDARIO (todos)
# ═══════════════════════════════════════════════════════════════
@bp.route("/turnos")
@_login
def calendario():
    hoy = tl.hoy()
    mes_txt = request.args.get("mes", "")
    try:
        anio, mes = [int(x) for x in mes_txt.split("-")]
        primero = date(anio, mes, 1)
    except (ValueError, TypeError):
        primero = date(hoy.year, hoy.month, 1)
    siguiente = (primero + timedelta(days=32)).replace(day=1)
    anterior = (primero - timedelta(days=1)).replace(day=1)

    with _Conn() as (conn, cur):
        tl.confirmar_horizonte(cur, _url_cal())
        # Se incluyen los dias de la semana que cruza el inicio/fin de mes
        # para no partir un fin de semana en dos.
        desde = primero - timedelta(days=primero.weekday())
        fin_mes = siguiente - timedelta(days=1)
        hasta = fin_mes + timedelta(days=6 - fin_mes.weekday())
        dias = _dias_con_asignados(cur, desde, hasta)
        grupos = [g for g in _agrupar_por_semana(dias)
                  if any(d["fecha_obj"].month == primero.month for d in g["dias"])]

        uid = _uid()
        ph = p()
        cur.execute(f"SELECT * FROM turnos_tecnicos WHERE usuario_id = {ph}", (uid,))
        soy_tecnico = cur.fetchone() is not None
        mis_proximos = []
        mis_cambios = []
        if soy_tecnico:
            cur.execute(f"""SELECT d.* FROM turnos_asignaciones a JOIN turnos_dias d ON d.id = a.dia_id
                            WHERE a.usuario_id = {ph} AND d.fecha >= {ph} ORDER BY d.fecha LIMIT 6""",
                        (uid, hoy.isoformat()))
            mis_proximos = [dict(r) for r in cur.fetchall()]
            cur.execute(f"""SELECT c.*, u.nombre AS reemplazo_nombre FROM turnos_cambios c
                            LEFT JOIN usuarios u ON u.id = c.reemplazo_id
                            WHERE c.usuario_id = {ph} ORDER BY c.id DESC LIMIT 5""", (uid,))
            mis_cambios = [dict(r) for r in cur.fetchall()]

    estado_correo = {}
    if _es_admin():
        with _Conn() as (conn, cur):
            for g in grupos_correo(cur, desde, hasta):
                for d in g["dias"]:
                    estado_correo[d["fecha"]] = {"clave": g["clave"], "estado": g["estado"]}
    return render_template(
        "turnos/calendario.html", estado_correo=estado_correo,
        grupos=grupos, primero=primero, anterior=anterior, siguiente=siguiente,
        nombre_mes=f"{tl.MESES[primero.month].capitalize()} {primero.year}",
        hoy=hoy, soy_tecnico=soy_tecnico, mis_proximos=mis_proximos, mis_cambios=mis_cambios,
        limite_confirmado=tl.fin_horizonte_confirmado(),
    )


@bp.route("/turnos/dia/<int:dia_id>/no-puedo", methods=["POST"])
@_login
def pedir_cambio(dia_id):
    uid = _uid()
    motivo = (request.form.get("motivo") or "").strip()[:500]
    with _Conn() as (conn, cur):
        ph = p()
        cur.execute(f"""SELECT d.* FROM turnos_dias d JOIN turnos_asignaciones a ON a.dia_id = d.id
                        WHERE d.id = {ph} AND a.usuario_id = {ph}""", (dia_id, uid))
        dia = cur.fetchone()
        if not dia:
            flash("Ese turno no está asignado a ti.", "danger")
            return redirect(url_for("turnos.calendario"))
        if tl.a_fecha(dia["fecha"]) < tl.hoy():
            flash("Ese turno ya pasó.", "warning")
            return redirect(url_for("turnos.calendario"))
        cur.execute(f"""SELECT id FROM turnos_cambios WHERE dia_id = {ph} AND usuario_id = {ph}
                        AND estado = 'pendiente'""", (dia_id, uid))
        if cur.fetchone():
            flash("Ya tienes una solicitud pendiente para ese día.", "info")
            return redirect(url_for("turnos.calendario"))
        if not motivo:
            flash("Cuéntale al jefe el motivo, aunque sea breve.", "warning")
            return redirect(url_for("turnos.calendario", mes=dia["fecha"][:7]))
        cid = tl.insertar(cur, f"""INSERT INTO turnos_cambios (dia_id, fecha, usuario_id, motivo, estado, created_at)
                                   VALUES ({ph},{ph},{ph},{ph},'pendiente',{ph})""",
                          (dia_id, dia["fecha"], uid, motivo, ahora_str()))
        tl.notificar_admins(cur, f"{session.get('nombre')} no puede ir el {tl.fecha_larga(dia['fecha'])}: {motivo}",
                            url_for("turnos.cambios"))
    _auditar(cid, "solicitar_cambio", f"{dia['fecha']}: {motivo}")
    flash("Solicitud enviada. Te avisaremos cuando se resuelva.", "success")
    return redirect(url_for("turnos.calendario", mes=dia["fecha"][:7]))


@bp.route("/turnos/cambios/<int:cid>/cancelar", methods=["POST"])
@_login
def cancelar_cambio(cid):
    with _Conn() as (conn, cur):
        ph = p()
        cur.execute(f"""UPDATE turnos_cambios SET estado = 'cancelada', resuelto_at = {ph}
                        WHERE id = {ph} AND usuario_id = {ph} AND estado = 'pendiente'""",
                    (ahora_str(), cid, _uid()))
        ok = cur.rowcount
    flash("Solicitud cancelada: el turno sigue a tu nombre." if ok else "No se pudo cancelar.",
          "info" if ok else "warning")
    return redirect(url_for("turnos.calendario"))


# ═══════════════════════════════════════════════════════════════
# PLANIFICACION (admin)
# ═══════════════════════════════════════════════════════════════
SEMANAS_PLAN = 12


def _sabado_base(valor=None):
    f = _parse_fecha(valor) if valor else None
    f = f or tl.hoy()
    # sabado de la semana de f (si es domingo, el sabado anterior)
    return f + timedelta(days=(5 - f.weekday())) if f.weekday() <= 5 else f - timedelta(days=1)


@bp.route("/turnos/planificacion")
@_admin
def planificacion():
    sabado0 = _sabado_base(request.args.get("desde"))
    hasta = sabado0 + timedelta(days=7 * SEMANAS_PLAN)
    with _Conn() as (conn, cur):
        tl.confirmar_horizonte(cur, _url_cal())
        dias = _dias_con_asignados(cur, sabado0 - timedelta(days=5), hasta)
        tecnicos = tl.leer_tecnicos(cur)
    por_fecha = {d["fecha_obj"]: d for d in dias}
    filas = []
    for i in range(SEMANAS_PLAN):
        sab = sabado0 + timedelta(days=7 * i)
        dom = sab + timedelta(days=1)
        filas.append({"sabado": sab, "domingo": dom,
                      "dia_sab": por_fecha.get(sab), "dia_dom": por_fecha.get(dom)})
    especiales = [d for d in dias if d["fecha_obj"].weekday() < 5 and d["fecha_obj"] >= tl.hoy()]
    activos = [t for t in tecnicos if t["activo"]]
    return render_template(
        "turnos/planificacion.html",
        filas=filas, especiales=especiales, tecnicos=tecnicos, activos=activos,
        hoy=tl.hoy(), sabado0=sabado0,
        anterior=sabado0 - timedelta(days=7 * SEMANAS_PLAN // 2),
        siguiente=sabado0 + timedelta(days=7 * SEMANAS_PLAN // 2),
        limite_confirmado=tl.fin_horizonte_confirmado(),
        defecto=tl.PERSONAS_POR_DEFECTO,
    )


def _crear_dia(cur, fecha, personas, nota=None):
    ph = p()
    return tl.insertar(cur, f"""INSERT INTO turnos_dias (fecha, personas, estado, nota, created_at)
                                VALUES ({ph},{ph},'provisorio',{ph},{ph})""",
                       (fecha.isoformat(), personas, nota, ahora_str()))


def _eliminar_dia(cur, dia):
    ph = p()
    if dia["estado"] == "confirmado":
        cur.execute(f"SELECT usuario_id FROM turnos_asignaciones WHERE dia_id = {ph}", (dia["id"],))
        for r in cur.fetchall():
            tl.notificar(cur, r["usuario_id"],
                         f"Se canceló el turno del {tl.fecha_larga(dia['fecha'])}: ya no se trabaja ese día.",
                         url_for("turnos.calendario"))
    cur.execute(f"DELETE FROM turnos_asignaciones WHERE dia_id = {ph}", (dia["id"],))
    cur.execute(f"""UPDATE turnos_cambios SET estado = 'cancelada', respuesta = 'El día se quitó del calendario',
                    resuelto_at = {ph} WHERE dia_id = {ph} AND estado = 'pendiente'""", (ahora_str(), dia["id"]))
    cur.execute(f"DELETE FROM turnos_dias WHERE id = {ph}", (dia["id"],))


def _ajustar_cupo(cur, dia, personas):
    """Si bajan las personas de un dia, se quitan primero los asignados
    automaticos mas recientes."""
    ph = p()
    cur.execute(f"UPDATE turnos_dias SET personas = {ph} WHERE id = {ph}", (personas, dia["id"]))
    cur.execute(f"SELECT id, usuario_id, origen FROM turnos_asignaciones WHERE dia_id = {ph} ORDER BY id", (dia["id"],))
    asig = [dict(r) for r in cur.fetchall()]
    sobran = len(asig) - personas
    if sobran <= 0:
        return
    quitables = [a for a in reversed(asig) if a["origen"] == "auto"] + \
                [a for a in reversed(asig) if a["origen"] != "auto"]
    for a in quitables[:sobran]:
        cur.execute(f"DELETE FROM turnos_asignaciones WHERE id = {ph}", (a["id"],))
        if dia["estado"] == "confirmado":
            tl.notificar(cur, a["usuario_id"],
                         f"Ya no es necesario que vayas el {tl.fecha_larga(dia['fecha'])}.",
                         url_for("turnos.calendario"))


@bp.route("/turnos/planificacion", methods=["POST"])
@_admin
def guardar_planificacion():
    sabado0 = _sabado_base(request.form.get("desde"))
    hoy = tl.hoy()
    cambios = 0
    with _Conn() as (conn, cur):
        ph = p()
        for i in range(SEMANAS_PLAN):
            for f in (sabado0 + timedelta(days=7 * i), sabado0 + timedelta(days=7 * i + 1)):
                if f < hoy:
                    continue
                clave = f.isoformat()
                marcado = request.form.get(f"trabaja_{clave}") == "1"
                personas = _entero(request.form.get(f"personas_{clave}"), tl.PERSONAS_POR_DEFECTO, 1, 20)
                cur.execute(f"SELECT * FROM turnos_dias WHERE fecha = {ph}", (clave,))
                dia = cur.fetchone()
                dia = dict(dia) if dia else None
                if marcado and not dia:
                    _crear_dia(cur, f, personas)
                    cambios += 1
                elif marcado and dia and dia["personas"] != personas:
                    _ajustar_cupo(cur, dia, personas)
                    cambios += 1
                elif not marcado and dia:
                    _eliminar_dia(cur, dia)
                    cambios += 1
        _recalcular(cur)
    _auditar(None, "planificar", f"{cambios} cambio(s) desde {sabado0}")
    flash(f"Planificación guardada ({cambios} cambio{'s' if cambios != 1 else ''}). "
          "El reparto se recalculó.", "success")
    return redirect(url_for("turnos.planificacion", desde=sabado0.isoformat()))


@bp.route("/turnos/dia", methods=["POST"])
@_admin
def agregar_dia():
    f = _parse_fecha(request.form.get("fecha"))
    personas = _entero(request.form.get("personas"), tl.PERSONAS_POR_DEFECTO, 1, 20)
    nota = (request.form.get("nota") or "").strip()[:200] or None
    if not f or f < tl.hoy():
        flash("Elige una fecha de hoy en adelante.", "warning")
        return redirect(url_for("turnos.planificacion"))
    with _Conn() as (conn, cur):
        cur.execute(f"SELECT id FROM turnos_dias WHERE fecha = {p()}", (f.isoformat(),))
        if cur.fetchone():
            flash("Ese día ya está en el calendario.", "info")
            return redirect(url_for("turnos.planificacion", desde=f.isoformat()))
        did = _crear_dia(cur, f, personas, nota)
        _recalcular(cur)
    _auditar(did, "agregar_dia", f"{f} ({personas} personas) {nota or ''}")
    flash(f"Se agregó el {tl.fecha_larga(f)}.", "success")
    return redirect(url_for("turnos.planificacion", desde=f.isoformat()))


@bp.route("/turnos/dia/<int:dia_id>/editar", methods=["POST"])
@_admin
def editar_dia(dia_id):
    accion = request.form.get("accion", "guardar")
    with _Conn() as (conn, cur):
        ph = p()
        cur.execute(f"SELECT * FROM turnos_dias WHERE id = {ph}", (dia_id,))
        dia = cur.fetchone()
        if not dia:
            flash("Ese día no existe.", "danger")
            return redirect(url_for("turnos.planificacion"))
        dia = dict(dia)
        volver = redirect(url_for("turnos.planificacion", desde=dia["fecha"]))
        if tl.a_fecha(dia["fecha"]) < tl.hoy():
            flash("No se pueden modificar días que ya pasaron.", "warning")
            return volver

        if accion == "eliminar":
            _eliminar_dia(cur, dia)
            _recalcular(cur)
            _auditar(dia_id, "eliminar_dia", dia["fecha"])
            flash(f"Se quitó el {tl.fecha_larga(dia['fecha'])} del calendario.", "info")
            return volver

        if accion == "automatico":
            cur.execute(f"UPDATE turnos_asignaciones SET origen = 'auto' WHERE dia_id = {ph}", (dia_id,))
            _limpiar_quitados(cur, dia["fecha"])
            cur.execute(f"UPDATE turnos_dias SET estado = 'provisorio' WHERE id = {ph}", (dia_id,))
            if dia["estado"] == "confirmado":
                # vuelve a quedar confirmado si esta dentro del horizonte, pero
                # recalculado; se avisa a quien salga o entre.
                cur.execute(f"SELECT usuario_id FROM turnos_asignaciones WHERE dia_id = {ph}", (dia_id,))
                antes = {r["usuario_id"] for r in cur.fetchall()}
            else:
                antes = None
            # recalcular() reconfirma lo que este en el horizonte antes de
            # repartir; para que este dia se reparta de verdad se borran
            # primero sus automaticos.
            cur.execute(f"DELETE FROM turnos_asignaciones WHERE dia_id = {ph} AND origen = 'auto'", (dia_id,))
            _recalcular(cur)
            if antes is not None:
                cur.execute(f"SELECT usuario_id FROM turnos_asignaciones WHERE dia_id = {ph}", (dia_id,))
                despues = {r["usuario_id"] for r in cur.fetchall()}
                for u in antes - despues:
                    tl.notificar(cur, u, f"Ya no tienes turno el {tl.fecha_larga(dia['fecha'])}.", _url_cal())
            _auditar(dia_id, "dia_automatico", dia["fecha"])
            flash("El día volvió a reparto automático.", "success")
            return volver

        personas = _entero(request.form.get("personas"), dia["personas"], 1, 20)
        nota = (request.form.get("nota") or "").strip()[:200] or None
        estado = request.form.get("estado") if request.form.get("estado") in ("provisorio", "confirmado") else dia["estado"]
        try:
            elegidos = list(dict.fromkeys(int(x) for x in request.form.getlist("asignados")))
        except ValueError:
            elegidos = []
        cur.execute(f"UPDATE turnos_dias SET personas = {ph}, nota = {ph}, estado = {ph} WHERE id = {ph}",
                    (personas, nota, estado, dia_id))
        cur.execute(f"SELECT usuario_id FROM turnos_asignaciones WHERE dia_id = {ph}", (dia_id,))
        actuales = {r["usuario_id"] for r in cur.fetchall()}
        avisar = estado == "confirmado"
        for u in actuales - set(elegidos):
            cur.execute(f"DELETE FROM turnos_asignaciones WHERE dia_id = {ph} AND usuario_id = {ph}", (dia_id, u))
            if avisar:
                tl.notificar(cur, u, f"Ya no tienes turno el {tl.fecha_larga(dia['fecha'])}.", _url_cal())
            # Para que el recalculo no lo vuelva a poner en ese dia
            tl.insertar(cur, f"""INSERT INTO turnos_ausencias (usuario_id, desde, hasta, motivo, created_at)
                                 VALUES ({ph},{ph},{ph},{ph},{ph})""",
                        (u, dia["fecha"], dia["fecha"], MOTIVO_QUITADO, ahora_str()))
        cur.execute(f"UPDATE turnos_asignaciones SET origen = 'manual' WHERE dia_id = {ph}", (dia_id,))
        for u in elegidos:
            if u in actuales:
                continue
            _limpiar_quitados(cur, dia["fecha"], u)
            tl.insertar(cur, f"""INSERT INTO turnos_asignaciones (dia_id, usuario_id, origen, created_at)
                                 VALUES ({ph},{ph},'manual',{ph})""", (dia_id, u, ahora_str()))
            if avisar:
                tl.notificar(cur, u, f"Se te asignó turno el {tl.fecha_larga(dia['fecha'])}.", _url_cal())
        if len(elegidos) > personas:
            cur.execute(f"UPDATE turnos_dias SET personas = {ph} WHERE id = {ph}", (len(elegidos), dia_id))
        _recalcular(cur)
    _auditar(dia_id, "editar_dia", f"{dia['fecha']}: {elegidos}")
    flash(f"Turno del {tl.fecha_larga(dia['fecha'])} actualizado.", "success")
    return volver


MOTIVO_QUITADO = "Quitado a mano en planificación"


def _limpiar_quitados(cur, fecha, usuario_id=None):
    """Borra las marcas de 'quitado a mano' de un dia (todas, o solo de una persona)."""
    ph = p()
    sql = f"DELETE FROM turnos_ausencias WHERE motivo = {ph} AND desde = {ph} AND hasta = {ph}"
    params = [MOTIVO_QUITADO, fecha, fecha]
    if usuario_id is not None:
        sql += f" AND usuario_id = {ph}"
        params.append(usuario_id)
    cur.execute(sql, params)


@bp.route("/turnos/recalcular", methods=["POST"])
@_admin
def recalcular_ahora():
    with _Conn() as (conn, cur):
        _recalcular(cur)
    _auditar(None, "recalcular", "manual")
    flash("Reparto recalculado.", "success")
    return redirect(request.referrer or url_for("turnos.planificacion"))


# ═══════════════════════════════════════════════════════════════
# SOLICITUDES DE CAMBIO (admin)
# ═══════════════════════════════════════════════════════════════
@bp.route("/turnos/cambios")
@_admin
def cambios():
    with _Conn() as (conn, cur):
        nombres = _nombres(cur)
        tecnicos = tl.leer_tecnicos(cur)
        tec_alg = tl._tecnicos_para_algoritmo(tecnicos)
        ausencias = tl.leer_ausencias(cur)
        cur.execute("SELECT * FROM turnos_cambios WHERE estado = 'pendiente' ORDER BY fecha, id")
        pendientes = [dict(r) for r in cur.fetchall()]
        for c in pendientes:
            cur.execute(f"SELECT usuario_id FROM turnos_asignaciones WHERE dia_id = {p()}", (c["dia_id"],))
            ya = {r["usuario_id"] for r in cur.fetchall()}
            historial = tl.leer_historial(cur, tl.a_fecha(c["fecha"]))
            c["sugeridos"] = tl.sugerir_reemplazos(c["fecha"], ya | {c["usuario_id"]}, historial,
                                                   tec_alg, ausencias)
            c["companeros"] = [nombres.get(u) for u in ya if u != c["usuario_id"]]
            c["vigente"] = c["usuario_id"] in ya
        cur.execute("SELECT * FROM turnos_cambios WHERE estado <> 'pendiente' ORDER BY id DESC LIMIT 40")
        historial_cambios = [dict(r) for r in cur.fetchall()]
    return render_template("turnos/cambios.html", pendientes=pendientes,
                           historial=historial_cambios, nombres=nombres, hoy=tl.hoy())


@bp.route("/turnos/cambios/<int:cid>/resolver", methods=["POST"])
@_admin
def resolver_cambio(cid):
    accion = request.form.get("accion")
    respuesta = (request.form.get("respuesta") or "").strip()[:300] or None
    with _Conn() as (conn, cur):
        ph = p()
        cur.execute(f"SELECT * FROM turnos_cambios WHERE id = {ph} AND estado = 'pendiente'", (cid,))
        c = cur.fetchone()
        if not c:
            flash("Esa solicitud ya fue resuelta.", "info")
            return redirect(url_for("turnos.cambios"))
        c = dict(c)
        fecha_txt = tl.fecha_larga(c["fecha"])

        if accion == "rechazar":
            cur.execute(f"""UPDATE turnos_cambios SET estado = 'rechazada', respuesta = {ph},
                            resuelto_por = {ph}, resuelto_at = {ph} WHERE id = {ph}""",
                        (respuesta, session.get("nombre"), ahora_str(), cid))
            tl.notificar(cur, c["usuario_id"],
                         f"Tu solicitud para el {fecha_txt} fue rechazada" + (f": {respuesta}" if respuesta else "."),
                         _url_cal())
            _auditar(cid, "rechazar_cambio", c["fecha"])
            flash("Solicitud rechazada. Se le avisó al técnico.", "info")
            return redirect(url_for("turnos.cambios"))

        # Aprobar
        reemplazo = request.form.get("reemplazo", "auto")
        reemplazo_id = int(reemplazo) if reemplazo.isdigit() else None
        if reemplazo == "ninguno":
            # Ese dia va una persona menos: se baja el cupo para que el
            # recalculo no complete la plaza.
            cur.execute(f"""UPDATE turnos_dias SET personas = CASE WHEN personas > 0 THEN personas - 1 ELSE 0 END
                            WHERE id = {ph}""", (c["dia_id"],))
        cur.execute(f"DELETE FROM turnos_asignaciones WHERE dia_id = {ph} AND usuario_id = {ph}",
                    (c["dia_id"], c["usuario_id"]))
        # El que no puede queda marcado ausente ese dia: ningun recalculo lo vuelve a poner.
        tl.insertar(cur, f"""INSERT INTO turnos_ausencias (usuario_id, desde, hasta, motivo, created_at)
                             VALUES ({ph},{ph},{ph},{ph},{ph})""",
                    (c["usuario_id"], c["fecha"], c["fecha"], f"Solicitud de cambio #{cid}", ahora_str()))
        if reemplazo_id:
            cur.execute(f"SELECT id FROM turnos_asignaciones WHERE dia_id = {ph} AND usuario_id = {ph}",
                        (c["dia_id"], reemplazo_id))
            if not cur.fetchone():
                tl.insertar(cur, f"""INSERT INTO turnos_asignaciones (dia_id, usuario_id, origen, created_at)
                                     VALUES ({ph},{ph},'reemplazo',{ph})""",
                            (c["dia_id"], reemplazo_id, ahora_str()))
        # Recalcula: si no se eligio reemplazo, el sistema completa la plaza
        # con quien corresponda; ademas se reajustan los provisorios.
        cur.execute(f"SELECT estado FROM turnos_dias WHERE id = {ph}", (c["dia_id"],))
        estado_dia = cur.fetchone()["estado"]
        cur.execute(f"SELECT usuario_id FROM turnos_asignaciones WHERE dia_id = {ph}", (c["dia_id"],))
        antes = {r["usuario_id"] for r in cur.fetchall()}
        _recalcular(cur)
        cur.execute(f"SELECT usuario_id FROM turnos_asignaciones WHERE dia_id = {ph}", (c["dia_id"],))
        despues = {r["usuario_id"] for r in cur.fetchall()}
        if not reemplazo_id and reemplazo != "ninguno":
            nuevos = despues - antes
            reemplazo_id = next(iter(nuevos), None)
        cur.execute(f"""UPDATE turnos_cambios SET estado = 'aprobada', reemplazo_id = {ph}, respuesta = {ph},
                        resuelto_por = {ph}, resuelto_at = {ph} WHERE id = {ph}""",
                    (reemplazo_id, respuesta, session.get("nombre"), ahora_str(), cid))
        nombres = _nombres(cur)
        tl.notificar(cur, c["usuario_id"],
                     f"Aprobado: ya no vas el {fecha_txt}."
                     + (f" Te reemplaza {nombres.get(reemplazo_id)}." if reemplazo_id else ""),
                     _url_cal())
        # recalcular() ya avisa a los agregados en dias confirmados; a un
        # reemplazo elegido a mano (o en dia provisorio) se le avisa aqui.
        if reemplazo_id and (reemplazo_id in antes or estado_dia != "confirmado"):
            tl.notificar(cur, reemplazo_id,
                         f"Cubres el turno del {fecha_txt} (reemplazo de {nombres.get(c['usuario_id'])}).",
                         _url_cal())
    _auditar(cid, "aprobar_cambio", f"{c['fecha']} reemplazo={reemplazo_id}")
    flash("Cambio aprobado. El calendario se recalculó y se avisó a los involucrados.", "success")
    return redirect(url_for("turnos.cambios"))


# ═══════════════════════════════════════════════════════════════
# TECNICOS Y AUSENCIAS (admin)
# ═══════════════════════════════════════════════════════════════
@bp.route("/turnos/tecnicos")
@_admin
def tecnicos():
    with _Conn() as (conn, cur):
        lista = tl.leer_tecnicos(cur)
        en_rotacion = {t["usuario_id"] for t in lista}
        activo_sql = "TRUE" if USE_POSTGRES else "1"
        cur.execute(f"SELECT id, nombre, username, role FROM usuarios WHERE activo = {activo_sql} ORDER BY nombre")
        disponibles = [dict(r) for r in cur.fetchall() if r["id"] not in en_rotacion]
        cur.execute(f"""SELECT a.*, u.nombre, u.username FROM turnos_ausencias a
                        JOIN usuarios u ON u.id = a.usuario_id
                        WHERE a.hasta >= {p()} ORDER BY a.desde""", (tl.hoy().isoformat(),))
        ausencias = [dict(r) for r in cur.fetchall()]
        lineas = tc.leer_config(cur)["lista_lineas"]
    return render_template("turnos/tecnicos.html", tecnicos=lista, disponibles=disponibles,
                           ausencias=ausencias, hoy=tl.hoy(), lineas=lineas)


@bp.route("/turnos/tecnicos/agregar", methods=["POST"])
@_admin
def agregar_tecnico():
    try:
        usuario_id = int(request.form.get("usuario_id", ""))
    except ValueError:
        flash("Elige un usuario.", "warning")
        return redirect(url_for("turnos.tecnicos"))
    tipo = "ocasional" if request.form.get("tipo") == "ocasional" else "fijo"
    activo = 0 if tipo == "ocasional" else 1
    with _Conn() as (conn, cur):
        ph = p()
        cur.execute(f"SELECT id FROM turnos_tecnicos WHERE usuario_id = {ph}", (usuario_id,))
        if cur.fetchone():
            flash("Ese usuario ya está en la rotación.", "info")
            return redirect(url_for("turnos.tecnicos"))
        aj_d, aj_b = tl.calcular_ajuste_ingreso(cur, usuario_id) if activo else (0, 0)
        cur.execute("SELECT COALESCE(MAX(orden), 0) AS m FROM turnos_tecnicos")
        orden = (cur.fetchone()["m"] or 0) + 1
        tid = tl.insertar(cur, f"""INSERT INTO turnos_tecnicos (usuario_id, tipo, activo, ajuste_dias, ajuste_dobles, orden, created_at)
                                   VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                          (usuario_id, tipo, activo, aj_d, aj_b, orden, ahora_str()))
        if activo:
            _recalcular(cur)
    _auditar(tid, "agregar_tecnico", f"usuario {usuario_id} ({tipo})")
    flash("Técnico agregado a la rotación." + (" Queda inactivo hasta que lo actives." if not activo else ""),
          "success")
    return redirect(url_for("turnos.tecnicos"))


@bp.route("/turnos/tecnicos/<int:tid>/actualizar", methods=["POST"])
@_admin
def actualizar_tecnico(tid):
    accion = request.form.get("accion")
    with _Conn() as (conn, cur):
        ph = p()
        cur.execute(f"SELECT * FROM turnos_tecnicos WHERE id = {ph}", (tid,))
        t = cur.fetchone()
        if not t:
            flash("No encontrado.", "danger")
            return redirect(url_for("turnos.tecnicos"))
        t = dict(t)
        if accion == "quitar":
            cur.execute(f"DELETE FROM turnos_tecnicos WHERE id = {ph}", (tid,))
            msg = "Se quitó de la rotación. Sus turnos pasados quedan en el historial."
        elif accion == "activar":
            aj_d, aj_b = tl.calcular_ajuste_ingreso(cur, t["usuario_id"])
            cur.execute(f"""UPDATE turnos_tecnicos SET activo = 1, ajuste_dias = {ph}, ajuste_dobles = {ph}
                            WHERE id = {ph}""", (aj_d, aj_b, tid))
            tl.notificar(cur, t["usuario_id"], "Entraste a la rotación de turnos de fin de semana.", _url_cal())
            msg = "Activado. Parte con el promedio del grupo para que el reparto sea parejo."
        elif accion == "desactivar":
            cur.execute(f"UPDATE turnos_tecnicos SET activo = 0 WHERE id = {ph}", (tid,))
            msg = ("Desactivado: no se le asignarán turnos nuevos. "
                   "Revisa si tenía turnos confirmados (aparecen en Planificación).")
        elif accion == "linea":
            linea = (request.form.get("linea") or "").strip()[:60] or None
            cur.execute(f"UPDATE turnos_tecnicos SET linea = {ph} WHERE id = {ph}", (linea, tid))
            msg = "Línea habitual actualizada."
        elif accion == "tipo":
            tipo = "ocasional" if request.form.get("tipo") == "ocasional" else "fijo"
            cur.execute(f"UPDATE turnos_tecnicos SET tipo = {ph} WHERE id = {ph}", (tipo, tid))
            msg = "Tipo actualizado."
        else:
            msg = "Sin cambios."
        if accion in ("quitar", "activar", "desactivar"):
            _recalcular(cur)
    _auditar(tid, f"tecnico_{accion}", f"usuario {t['usuario_id']}")
    flash(msg, "success")
    return redirect(url_for("turnos.tecnicos"))


@bp.route("/turnos/ausencias", methods=["POST"])
@_admin
def agregar_ausencia():
    desde = _parse_fecha(request.form.get("desde"))
    hasta = _parse_fecha(request.form.get("hasta")) or desde
    motivo = (request.form.get("motivo") or "").strip()[:200] or "Ausencia"
    try:
        usuario_id = int(request.form.get("usuario_id", ""))
    except ValueError:
        usuario_id = None
    if not usuario_id or not desde or hasta < desde:
        flash("Revisa la persona y las fechas.", "warning")
        return redirect(url_for("turnos.tecnicos"))
    with _Conn() as (conn, cur):
        ph = p()
        aid = tl.insertar(cur, f"""INSERT INTO turnos_ausencias (usuario_id, desde, hasta, motivo, created_at)
                                   VALUES ({ph},{ph},{ph},{ph},{ph})""",
                          (usuario_id, desde.isoformat(), hasta.isoformat(), motivo, ahora_str()))
        _recalcular(cur)
        # Dias confirmados en los que quedo asignado: hay que resolverlos a mano.
        cur.execute(f"""SELECT d.fecha FROM turnos_asignaciones a JOIN turnos_dias d ON d.id = a.dia_id
                        WHERE a.usuario_id = {ph} AND d.fecha >= {ph} AND d.fecha <= {ph}""",
                    (usuario_id, desde.isoformat(), hasta.isoformat()))
        choques = [r["fecha"] for r in cur.fetchall()]
    _auditar(aid, "agregar_ausencia", f"usuario {usuario_id} {desde}..{hasta} {motivo}")
    if choques:
        flash("Ausencia registrada, pero ya tenía turnos confirmados esos días: "
              + ", ".join(tl.fecha_larga(f) for f in choques)
              + ". Cámbialos en Planificación.", "warning")
    else:
        flash("Ausencia registrada y calendario recalculado.", "success")
    return redirect(url_for("turnos.tecnicos"))


@bp.route("/turnos/ausencias/<int:aid>/eliminar", methods=["POST"])
@_admin
def eliminar_ausencia(aid):
    with _Conn() as (conn, cur):
        cur.execute(f"DELETE FROM turnos_ausencias WHERE id = {p()}", (aid,))
        _recalcular(cur)
    _auditar(aid, "eliminar_ausencia", "")
    flash("Ausencia eliminada.", "info")
    return redirect(url_for("turnos.tecnicos"))


# ═══════════════════════════════════════════════════════════════
# EQUIDAD (todos pueden verla: transparencia)
# ═══════════════════════════════════════════════════════════════
@bp.route("/turnos/equidad")
@_login
def equidad():
    with _Conn() as (conn, cur):
        filas = tl.estadisticas(cur)
    activos = [f for f in filas if f["activo"]]
    maximo = max([f["dias_efectivos"] for f in activos] or [0])
    minimo = min([f["dias_efectivos"] for f in activos] or [0])
    for f in filas:
        f["pct"] = int(max(f["dias_efectivos"], 0) * 100 / maximo) if maximo > 0 else 0
    return render_template("turnos/equidad.html", filas=filas, maximo=maximo,
                           diferencia=maximo - minimo, hoy=tl.hoy())


# ═══════════════════════════════════════════════════════════════
# NOTIFICACIONES (todos)
# ═══════════════════════════════════════════════════════════════
@bp.route("/notificaciones")
@_login
def notificaciones():
    with _Conn() as (conn, cur):
        cur.execute(f"SELECT * FROM notificaciones WHERE usuario_id = {p()} ORDER BY id DESC LIMIT 60", (_uid(),))
        avisos = [dict(r) for r in cur.fetchall()]
    return render_template("turnos/notificaciones.html", avisos=avisos)


@bp.route("/notificaciones/<int:nid>/abrir")
@_login
def abrir_notificacion(nid):
    with _Conn() as (conn, cur):
        ph = p()
        cur.execute(f"SELECT url FROM notificaciones WHERE id = {ph} AND usuario_id = {ph}", (nid, _uid()))
        r = cur.fetchone()
        cur.execute(f"UPDATE notificaciones SET leida = 1 WHERE id = {ph} AND usuario_id = {ph}", (nid, _uid()))
    destino = (r["url"] if r else None) or url_for("turnos.notificaciones")
    if not destino.startswith("/"):
        destino = url_for("turnos.notificaciones")
    return redirect(destino)


@bp.route("/notificaciones/leer-todas", methods=["POST"])
@_login
def leer_todas():
    with _Conn() as (conn, cur):
        cur.execute(f"UPDATE notificaciones SET leida = 1 WHERE usuario_id = {p()}", (_uid(),))
    return redirect(url_for("turnos.notificaciones"))


# ═══════════════════════════════════════════════════════════════
# CORREO DE SOLICITUD DE INGRESO (admin)
# ═══════════════════════════════════════════════════════════════
DIAS_AVISO_CORREO = 7     # se considera "por enviar" lo que viene en la proxima semana


def grupos_correo(cur, desde, hasta, cfg=None):
    """Grupos de dias seguidos trabajados, con su redaccion y estado de envio."""
    cfg = cfg or tc.leer_config(cur)
    dias = _dias_con_asignados(cur, desde, hasta)
    salida = []
    for grupo in tc.agrupar_consecutivos(dias):
        red = tc.redactar(grupo, cfg)
        clave = tc.clave_grupo(grupo)
        envio = tc.leer_envio(cur, clave)
        salida.append({
            "clave": clave, "dias": grupo, "redaccion": red, "envio": envio,
            "estado": tc.estado_envio(envio, red["firma"]),
            "titulo": tc.frase_fechas(red["fechas"]),
            "confirmado": all(d["estado"] == "confirmado" for d in grupo),
        })
    return salida


def correos_pendientes(cur, dias=DIAS_AVISO_CORREO):
    """Grupos de los proximos `dias` dias cuyo correo falta o quedo desactualizado."""
    hoy = tl.hoy()
    return [g for g in grupos_correo(cur, hoy, hoy + timedelta(days=dias))
            if g["estado"] != "enviado" and g["dias"][0]["asignados"]]


def revisar_correos_enviados(cur, url_base="/turnos/correo"):
    """Si ya se envio un correo y despues cambio el personal (o la linea),
    avisa una sola vez a los administradores para que manden una correccion."""
    hoy = tl.hoy()
    try:
        cur.execute(f"SELECT * FROM turnos_correos WHERE enviado_at IS NOT NULL AND clave >= {p()}",
                    ((hoy - timedelta(days=3)).isoformat(),))
        enviados = [dict(r) for r in cur.fetchall()]
    except Exception:
        return
    if not enviados:
        return
    hasta = max(tl.a_fecha(e["clave"]) for e in enviados) + timedelta(days=10)
    actuales = {g["clave"]: g for g in grupos_correo(cur, hoy - timedelta(days=3), hasta)}
    ph = p()
    for e in enviados:
        g = actuales.get(e["clave"])
        firma_actual = g["redaccion"]["firma"] if g else "sin-dias"
        if firma_actual == e["firma"] or firma_actual == e.get("avisado_firma"):
            continue
        fechas = tl.fecha_larga(e["clave"])
        tl.notificar_admins(cur, f"Cambió el personal del {fechas} y la solicitud de ingreso ya se había enviado. "
                                 "Envía una corrección al correo.", f"{url_base}?clave={e['clave']}")
        cur.execute(f"UPDATE turnos_correos SET avisado_firma = {ph} WHERE id = {ph}", (firma_actual, e["id"]))


def enviar_recordatorio_correo():
    """Tarea programada (viernes 9:00): avisa en la campanita si falta
    enviar la solicitud de ingreso de los dias de la proxima semana."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        pendientes = correos_pendientes(cur)
        for g in pendientes:
            accion = "Reenvía (cambió el personal)" if g["estado"] == "desactualizado" else "Falta enviar"
            tl.notificar_admins(cur, f"{accion} la solicitud de ingreso para el {g['titulo']}.",
                                f"/turnos/correo?clave={g['clave']}")
        conn.commit()
        return len(pendientes)
    finally:
        conn.close()


@bp.app_template_global()
def resumen_turnos():
    """Datos para la tarjeta de turnos del dashboard."""
    if not session.get("logged_in"):
        return None
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        hoy = tl.hoy()
        dias = _dias_con_asignados(cur, hoy, hoy + timedelta(days=60))
        grupos = tc.agrupar_consecutivos(dias)
        proximo = grupos[0] if grupos else None
        datos = {"proximo": proximo,
                 "titulo": tc.frase_fechas([d["fecha_obj"] for d in proximo]) if proximo else None}
        uid = _uid()
        mio = None
        for d in dias:
            if any(a["usuario_id"] == uid for a in d["asignados"]):
                mio = d
                break
        datos["mi_proximo"] = mio
        if _es_admin():
            cur.execute("SELECT COUNT(*) AS n FROM turnos_cambios WHERE estado = 'pendiente'")
            datos["cambios"] = cur.fetchone()["n"]
            datos["correos"] = correos_pendientes(cur)
        return datos
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


@bp.route("/turnos/correo")
@_admin
def correo_ingreso():
    hoy = tl.hoy()
    with _Conn() as (conn, cur):
        cfg = tc.leer_config(cur)
        grupos = grupos_correo(cur, hoy - timedelta(days=7), hoy + timedelta(days=7 * 10), cfg)
        tecnicos = tl.leer_tecnicos(cur)
    clave = request.args.get("clave")
    actual = next((g for g in grupos if g["clave"] == clave), None)
    if actual is None:
        futuros = [g for g in grupos if g["dias"][-1]["fecha_obj"] >= hoy]
        actual = next((g for g in futuros if g["estado"] != "enviado"), None) or (futuros[0] if futuros else None)
    red = actual["redaccion"] if actual else None
    return render_template(
        "turnos/correo.html", grupos=grupos, actual=actual, red=red, cfg=cfg, hoy=hoy,
        limite_aviso=hoy + timedelta(days=DIAS_AVISO_CORREO),
        tecnicos=tecnicos,
        url_outlook=tc.url_outlook(red) if red else None,
        url_mailto=tc.url_mailto(red) if red else None,
    )


@bp.route("/turnos/correo/<clave>/lineas", methods=["POST"])
@_admin
def correo_guardar_lineas(clave):
    with _Conn() as (conn, cur):
        cfg = tc.leer_config(cur)
        ph = p()
        cambios = 0
        for k, v in request.form.items():
            if not k.startswith("linea_"):
                continue
            try:
                aid = int(k.split("_", 1)[1])
            except ValueError:
                continue
            cur.execute(f"""SELECT a.id, t.linea AS habitual FROM turnos_asignaciones a
                            LEFT JOIN turnos_tecnicos t ON t.usuario_id = a.usuario_id WHERE a.id = {ph}""", (aid,))
            fila = cur.fetchone()
            if not fila:
                continue
            linea = (v or "").strip()[:60]
            tarea = (request.form.get(f"tarea_{aid}") or "").strip()[:200]
            # Solo se guarda lo que difiere de lo habitual, asi un cambio en la
            # linea habitual del tecnico se refleja en los dias sin excepcion.
            linea_db = None if linea == (fila["habitual"] or "") else (linea or "")
            tarea_db = None if (not tarea or tarea == cfg["tarea"]) else tarea
            cur.execute(f"UPDATE turnos_asignaciones SET linea = {ph}, tarea = {ph} WHERE id = {ph}",
                        (linea_db, tarea_db, aid))
            cambios += 1
    _auditar(None, "correo_lineas", clave)
    flash("Líneas y tareas guardadas. La redacción se actualizó.", "success")
    return redirect(url_for("turnos.correo_ingreso", clave=clave))


@bp.route("/turnos/correo/<clave>/enviado", methods=["POST"])
@_admin
def correo_marcar_enviado(clave):
    deshacer = request.form.get("accion") == "deshacer"
    with _Conn() as (conn, cur):
        if deshacer:
            tc.desmarcar_enviado(cur, clave)
        else:
            f = tl.a_fecha(clave)
            g = next((x for x in grupos_correo(cur, f, f + timedelta(days=10)) if x["clave"] == clave), None)
            if not g:
                flash("No se encontró ese día.", "warning")
                return redirect(url_for("turnos.correo_ingreso"))
            tc.marcar_enviado(cur, clave, g["redaccion"]["fechas"], g["redaccion"]["firma"], session.get("nombre"))
            # El recordatorio de ese grupo ya no aplica
            cur.execute(f"""UPDATE notificaciones SET leida = 1 WHERE url = {p()} AND leida = 0""",
                        (f"/turnos/correo?clave={clave}",))
    _auditar(None, "correo_deshacer" if deshacer else "correo_enviado", clave)
    flash("Marcado como pendiente otra vez." if deshacer else "Listo, quedó registrado como enviado.",
          "info" if deshacer else "success")
    return redirect(url_for("turnos.correo_ingreso", clave=clave))


@bp.route("/turnos/correo/config", methods=["POST"])
@_admin
def correo_config():
    with _Conn() as (conn, cur):
        tc.guardar_config(cur, {k: request.form.get(k) for k in tc.CONFIG_DEFECTO})
    _auditar(None, "correo_config", "")
    flash("Configuración del correo guardada.", "success")
    return redirect(url_for("turnos.correo_ingreso", clave=request.form.get("clave") or None))
