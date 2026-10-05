"""
turnos_logica.py -- Reparto justo de turnos de fin de semana.

Este archivo NO depende de Flask: contiene
  1) las tablas del modulo (SQLite y PostgreSQL),
  2) el algoritmo de reparto (funcion pura `repartir`, facil de probar),
  3) las operaciones contra la base de datos que usa turnos.py.

Regla de reparto (para cada dia trabajado, en orden de fecha):
  se eligen las personas con MENOS dias trabajados; si hay empate, se
  prefiere a quien NO trabajo el dia pegado (para no generar un doble
  sabado+domingo si no hace falta), luego a quien ha hecho menos dobles,
  luego a quien ha hecho menos veces ese mismo dia de la semana, y por
  ultimo a quien trabajo hace mas tiempo.

Estados de un dia:
  - 'confirmado': no se mueve solo (solo cambia por solicitud aprobada o
    edicion manual). Los proximos FINES_CONFIRMADOS fines de semana se
    confirman automaticamente.
  - 'provisorio': se recalcula cada vez que hay un cambio.
"""

import os
from datetime import date, timedelta

from db import get_db_connection, p, USE_POSTGRES, ahora, ahora_str

FINES_CONFIRMADOS = int(os.environ.get("TURNOS_FINES_CONFIRMADOS", 2))
PERSONAS_POR_DEFECTO = 2

DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ═══════════════════════════════════════════════════════════════
# 1. TABLAS
# ═══════════════════════════════════════════════════════════════
# Fechas guardadas como texto 'YYYY-MM-DD' y marcas de tiempo como texto
# 'YYYY-MM-DD HH:MM:SS' en ambos motores: asi la logica es identica en
# SQLite y Postgres, sin conversiones de zona horaria.

def _pk():
    return "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"


def ddl_turnos():
    pk = _pk()
    return [
        f"""CREATE TABLE IF NOT EXISTS turnos_tecnicos (
            id            {pk},
            usuario_id    INTEGER NOT NULL UNIQUE,
            tipo          TEXT NOT NULL DEFAULT 'fijo',
            activo        INTEGER NOT NULL DEFAULT 1,
            ajuste_dias   INTEGER NOT NULL DEFAULT 0,
            ajuste_dobles INTEGER NOT NULL DEFAULT 0,
            orden         INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS turnos_dias (
            id         {pk},
            fecha      TEXT NOT NULL UNIQUE,
            personas   INTEGER NOT NULL DEFAULT 2,
            estado     TEXT NOT NULL DEFAULT 'provisorio',
            nota       TEXT,
            created_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS turnos_asignaciones (
            id         {pk},
            dia_id     INTEGER NOT NULL,
            usuario_id INTEGER NOT NULL,
            origen     TEXT NOT NULL DEFAULT 'auto',
            created_at TEXT,
            UNIQUE (dia_id, usuario_id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS turnos_ausencias (
            id         {pk},
            usuario_id INTEGER NOT NULL,
            desde      TEXT NOT NULL,
            hasta      TEXT NOT NULL,
            motivo     TEXT,
            created_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS turnos_cambios (
            id            {pk},
            dia_id        INTEGER NOT NULL,
            fecha         TEXT NOT NULL,
            usuario_id    INTEGER NOT NULL,
            motivo        TEXT,
            estado        TEXT NOT NULL DEFAULT 'pendiente',
            reemplazo_id  INTEGER,
            respuesta     TEXT,
            resuelto_por  TEXT,
            created_at    TEXT,
            resuelto_at   TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS notificaciones (
            id         {pk},
            usuario_id INTEGER NOT NULL,
            mensaje    TEXT NOT NULL,
            url        TEXT,
            leida      INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS turnos_correos (
            id             {pk},
            clave          TEXT NOT NULL UNIQUE,
            fechas         TEXT,
            firma          TEXT,
            enviado_at     TEXT,
            enviado_por    TEXT,
            avisado_firma  TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_turnos_dias_fecha ON turnos_dias(fecha)",
        "CREATE INDEX IF NOT EXISTS idx_turnos_asig_dia ON turnos_asignaciones(dia_id)",
        "CREATE INDEX IF NOT EXISTS idx_turnos_asig_usuario ON turnos_asignaciones(usuario_id)",
        "CREATE INDEX IF NOT EXISTS idx_turnos_cambios_estado ON turnos_cambios(estado)",
        "CREATE INDEX IF NOT EXISTS idx_notif_usuario ON notificaciones(usuario_id, leida)",
    ]


TABLAS_TURNOS = ["turnos_tecnicos", "turnos_dias", "turnos_asignaciones",
                 "turnos_ausencias", "turnos_cambios", "notificaciones", "turnos_correos"]

# Columnas agregadas despues de la primera version (se crean solas).
COLUMNAS_NUEVAS = [
    ("turnos_tecnicos", "linea", "TEXT"),        # linea habitual del tecnico
    ("turnos_asignaciones", "linea", "TEXT"),    # linea de ese dia (si cambia)
    ("turnos_asignaciones", "tarea", "TEXT"),    # tarea de ese dia (si cambia)
]


def crear_tablas_turnos(conn):
    cur = conn.cursor()
    for stmt in ddl_turnos():
        try:
            cur.execute(stmt)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    for tabla, col, tipo in COLUMNAS_NUEVAS:
        try:
            if USE_POSTGRES:
                # Se consulta antes: un ALTER TABLE (aunque no cambie nada)
                # bloquea la tabla completa, y esto corre en cada visita.
                cur.execute("""SELECT 1 FROM information_schema.columns
                               WHERE table_name = %s AND column_name = %s""", (tabla, col))
                if cur.fetchone():
                    conn.commit()
                    continue
                cur.execute(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {col} {tipo}")
            else:
                cur.execute(f"PRAGMA table_info({tabla})")
                if col in [r[1] for r in cur.fetchall()]:
                    continue
                cur.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# 2. ALGORITMO (puro, sin base de datos)
# ═══════════════════════════════════════════════════════════════

def a_fecha(valor):
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def hoy():
    return ahora().date()


def fin_horizonte_confirmado(desde=None, fines=FINES_CONFIRMADOS):
    """Domingo del ultimo fin de semana que queda confirmado. Si hoy es
    sabado o domingo, el fin de semana en curso cuenta como el primero."""
    d = desde or hoy()
    dias_al_domingo = (6 - d.weekday()) % 7
    primer_domingo = d + timedelta(days=dias_al_domingo)
    return primer_domingo + timedelta(days=7 * (max(fines, 1) - 1))


class Estadistica:
    """Contador de un tecnico mientras se reparte."""

    def __init__(self, uid, ajuste_dias=0, ajuste_dobles=0):
        self.uid = uid
        self.fechas = set()
        self.ajuste_dias = ajuste_dias or 0
        self.ajuste_dobles = ajuste_dobles or 0

    def agregar(self, f):
        self.fechas.add(f)

    @property
    def dias(self):
        return len(self.fechas)

    @property
    def dobles(self):
        return sum(1 for f in self.fechas if (f - timedelta(days=1)) in self.fechas)

    def por_dia_semana(self, wd):
        return sum(1 for f in self.fechas if f.weekday() == wd)

    @property
    def ultimo(self):
        return max(self.fechas) if self.fechas else None

    def crea_doble(self, f):
        return (f - timedelta(days=1)) in self.fechas or (f + timedelta(days=1)) in self.fechas

    def clave(self, f, orden=0):
        ultimo = self.ultimo
        return (
            self.dias + self.ajuste_dias,
            1 if self.crea_doble(f) else 0,
            self.dobles + self.ajuste_dobles,
            self.por_dia_semana(f.weekday()),
            ultimo.toordinal() if ultimo else 0,
            orden,
            self.uid,
        )


def repartir(dias, historial, tecnicos, ausencias=None):
    """Calcula quien va cada dia.

    dias:      lista de dicts {fecha, personas, fijos: [uid, ...]} en
               cualquier orden. 'fijos' son personas que ya estan puestas
               y no se mueven (dias confirmados, ediciones manuales,
               reemplazos).
    historial: lista de (fecha, uid) ya trabajados antes del primer dia
               a repartir (cuentan para la equidad).
    tecnicos:  lista de dicts {uid, activo, ajuste_dias, ajuste_dobles, orden}
    ausencias: lista de (uid, desde, hasta) -> esa persona no se asigna.

    Devuelve {fecha: [uid, ...]} con fijos + elegidos, y para cada fecha
    cuantas plazas quedaron sin cubrir en la clave especial '_faltan'.
    """
    ausencias = [(u, a_fecha(d), a_fecha(h)) for (u, d, h) in (ausencias or [])]
    stats = {}
    orden = {}
    activos = set()
    for t in tecnicos:
        stats[t["uid"]] = Estadistica(t["uid"], t.get("ajuste_dias", 0), t.get("ajuste_dobles", 0))
        orden[t["uid"]] = t.get("orden", 0)
        if t.get("activo", True):
            activos.add(t["uid"])

    for f, uid in historial:
        if uid not in stats:
            stats[uid] = Estadistica(uid)
        stats[uid].agregar(a_fecha(f))

    dias = sorted(({**d, "fecha": a_fecha(d["fecha"])} for d in dias), key=lambda d: d["fecha"])

    # Los fijos de todos los dias se conocen de antemano: cuentan para
    # saber si alguien ya trabaja el dia pegado.
    fijos_por_fecha = {d["fecha"]: list(dict.fromkeys(d.get("fijos") or [])) for d in dias}

    def ausente(uid, f):
        return any(u == uid and d <= f <= h for (u, d, h) in ausencias)

    resultado = {}
    faltan = {}
    for d in dias:
        f = d["fecha"]
        elegidos = list(fijos_por_fecha[f])
        for uid in elegidos:
            stats.setdefault(uid, Estadistica(uid)).agregar(f)
        necesarios = max(int(d.get("personas") or 0) - len(elegidos), 0)
        candidatos = [u for u in activos if u not in elegidos and not ausente(u, f)]
        # Para "crea_doble" se consideran tambien los fijos del dia siguiente.
        siguiente = set(fijos_por_fecha.get(f + timedelta(days=1), []))
        for _ in range(necesarios):
            if not candidatos:
                break

            def clave(u):
                k = list(stats[u].clave(f, orden.get(u, 0)))
                if u in siguiente:
                    k[1] = 1
                return tuple(k)

            mejor = min(candidatos, key=clave)
            candidatos.remove(mejor)
            elegidos.append(mejor)
            stats[mejor].agregar(f)
        resultado[f] = elegidos
        faltan[f] = max(int(d.get("personas") or 0) - len(elegidos), 0)
    resultado["_faltan"] = faltan
    return resultado


def sugerir_reemplazos(fecha, excluir, historial, tecnicos, ausencias=None):
    """Lista de uids ordenada de mejor a peor candidato para cubrir `fecha`."""
    f = a_fecha(fecha)
    ausencias = [(u, a_fecha(d), a_fecha(h)) for (u, d, h) in (ausencias or [])]
    stats = {t["uid"]: Estadistica(t["uid"], t.get("ajuste_dias", 0), t.get("ajuste_dobles", 0))
             for t in tecnicos}
    for fh, uid in historial:
        if uid in stats:
            stats[uid].agregar(a_fecha(fh))
    candidatos = [t for t in tecnicos
                  if t.get("activo", True) and t["uid"] not in excluir
                  and not any(u == t["uid"] and d <= f <= h for (u, d, h) in ausencias)]
    candidatos.sort(key=lambda t: stats[t["uid"]].clave(f, t.get("orden", 0)))
    return [t["uid"] for t in candidatos]


# ═══════════════════════════════════════════════════════════════
# 3. OPERACIONES CONTRA LA BASE DE DATOS
# ═══════════════════════════════════════════════════════════════

def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def insertar(cur, sql, params):
    """INSERT que devuelve el id nuevo en ambos motores."""
    if USE_POSTGRES:
        cur.execute(sql + " RETURNING id", params)
        return cur.fetchone()["id"]
    cur.execute(sql, params)
    return cur.lastrowid


def leer_tecnicos(cur, solo_activos=False):
    sql = """SELECT t.*, u.nombre, u.username, u.activo AS usuario_activo
             FROM turnos_tecnicos t JOIN usuarios u ON u.id = t.usuario_id"""
    if solo_activos:
        sql += " WHERE t.activo = 1"
    sql += " ORDER BY t.tipo, t.orden, u.nombre"
    cur.execute(sql)
    filas = _rows(cur)
    for f in filas:
        f["nombre"] = f.get("nombre") or f.get("username")
    return filas


def _tecnicos_para_algoritmo(tecnicos):
    return [{"uid": t["usuario_id"], "activo": bool(t["activo"]),
             "ajuste_dias": t["ajuste_dias"] or 0, "ajuste_dobles": t["ajuste_dobles"] or 0,
             "orden": t["orden"] or 0} for t in tecnicos]


def leer_ausencias(cur):
    cur.execute("SELECT usuario_id, desde, hasta FROM turnos_ausencias")
    return [(r["usuario_id"], r["desde"], r["hasta"]) for r in cur.fetchall()]


def leer_historial(cur, antes_de):
    ph = p()
    cur.execute(f"""SELECT d.fecha, a.usuario_id FROM turnos_asignaciones a
                    JOIN turnos_dias d ON d.id = a.dia_id
                    WHERE d.fecha < {ph}""", (antes_de.isoformat(),))
    return [(r["fecha"], r["usuario_id"]) for r in cur.fetchall()]


def leer_todas_asignaciones(cur):
    cur.execute("""SELECT d.fecha, a.usuario_id FROM turnos_asignaciones a
                   JOIN turnos_dias d ON d.id = a.dia_id""")
    return [(r["fecha"], r["usuario_id"]) for r in cur.fetchall()]


def notificar(cur, usuario_id, mensaje, url=None):
    insertar(cur, f"INSERT INTO notificaciones (usuario_id, mensaje, url, leida, created_at) VALUES ({p()},{p()},{p()},0,{p()})",
             (usuario_id, mensaje[:500], url, ahora_str()))


def notificar_admins(cur, mensaje, url=None):
    activo = "TRUE" if USE_POSTGRES else "1"
    cur.execute(f"SELECT id FROM usuarios WHERE role = 'admin' AND activo = {activo}")
    for r in cur.fetchall():
        notificar(cur, r["id"], mensaje, url)


def fecha_larga(f):
    f = a_fecha(f)
    return f"{DIAS_SEMANA[f.weekday()].lower()} {f.day} de {MESES[f.month]}"


def confirmar_horizonte(cur, url_calendario=None):
    """Pasa a 'confirmado' los dias provisorios que entraron en los proximos
    FINES_CONFIRMADOS fines de semana, y avisa a quienes quedaron asignados.
    Devuelve cuantos dias se confirmaron."""
    ph = p()
    limite = fin_horizonte_confirmado()
    cur.execute(f"""SELECT id, fecha FROM turnos_dias
                    WHERE estado = 'provisorio' AND fecha >= {ph} AND fecha <= {ph}""",
                (hoy().isoformat(), limite.isoformat()))
    dias = _rows(cur)
    for d in dias:
        cur.execute(f"UPDATE turnos_dias SET estado = 'confirmado' WHERE id = {ph}", (d["id"],))
        cur.execute(f"SELECT usuario_id FROM turnos_asignaciones WHERE dia_id = {ph}", (d["id"],))
        for r in cur.fetchall():
            notificar(cur, r["usuario_id"],
                      f"Turno confirmado: {fecha_larga(d['fecha'])}.", url_calendario)
    return len(dias)


def recalcular(cur, url_calendario=None):
    """Recalcula los dias provisorios desde hoy y completa plazas vacias de
    los confirmados (sin mover a nadie que ya este en un confirmado).
    Devuelve {fecha: faltan} para los dias con plazas sin cubrir."""
    ph = p()
    h = hoy()
    # Primero se confirma lo que haya entrado en el horizonte, para que esos
    # dias se traten como fijos.
    confirmar_horizonte(cur, url_calendario)

    cur.execute(f"""DELETE FROM turnos_asignaciones
                    WHERE origen = 'auto' AND dia_id IN (
                        SELECT id FROM turnos_dias WHERE estado = 'provisorio' AND fecha >= {ph})""",
                (h.isoformat(),))

    cur.execute(f"SELECT * FROM turnos_dias WHERE fecha >= {ph} ORDER BY fecha", (h.isoformat(),))
    dias_db = _rows(cur)
    if not dias_db:
        return {}
    cur.execute(f"""SELECT a.dia_id, a.usuario_id FROM turnos_asignaciones a
                    JOIN turnos_dias d ON d.id = a.dia_id WHERE d.fecha >= {ph}""", (h.isoformat(),))
    fijos = {}
    for r in cur.fetchall():
        fijos.setdefault(r["dia_id"], []).append(r["usuario_id"])

    dias = [{"fecha": d["fecha"], "personas": d["personas"], "fijos": fijos.get(d["id"], [])}
            for d in dias_db]
    tecnicos = _tecnicos_para_algoritmo(leer_tecnicos(cur))
    res = repartir(dias, leer_historial(cur, h), tecnicos, leer_ausencias(cur))

    for d in dias_db:
        f = a_fecha(d["fecha"])
        ya = set(fijos.get(d["id"], []))
        nuevos = [u for u in res.get(f, []) if u not in ya]
        for uid in nuevos:
            insertar(cur, f"""INSERT INTO turnos_asignaciones (dia_id, usuario_id, origen, created_at)
                              VALUES ({ph},{ph},'auto',{ph})""", (d["id"], uid, ahora_str()))
            if d["estado"] == "confirmado":
                notificar(cur, uid, f"Se te asignó turno el {fecha_larga(f)}.", url_calendario)
    return {f.isoformat(): n for f, n in res["_faltan"].items() if n > 0}


def estadisticas(cur):
    """Resumen de equidad por tecnico (incluye a los inactivos)."""
    h = hoy()
    tecnicos = leer_tecnicos(cur)
    todas = leer_todas_asignaciones(cur)
    salida = []
    for t in tecnicos:
        uid = t["usuario_id"]
        pasadas = Estadistica(uid)
        futuras = 0
        proximo = None
        for f, u in todas:
            if u != uid:
                continue
            f = a_fecha(f)
            if f < h:
                pasadas.agregar(f)
            else:
                futuras += 1
                proximo = f if proximo is None or f < proximo else proximo
        salida.append({
            **t,
            "dias": pasadas.dias,
            "dias_efectivos": pasadas.dias + (t["ajuste_dias"] or 0),
            "sabados": pasadas.por_dia_semana(5),
            "domingos": pasadas.por_dia_semana(6),
            "otros": pasadas.dias - pasadas.por_dia_semana(5) - pasadas.por_dia_semana(6),
            "dobles": pasadas.dobles,
            "ultimo": pasadas.ultimo,
            "programados": futuras,
            "proximo": proximo,
        })
    return salida


def calcular_ajuste_ingreso(cur, usuario_id):
    """Al activar a alguien (tipico: un ocasional) parte con el promedio del
    grupo activo, para que el reparto no le cargue todos los turnos de golpe.
    Considera todo lo asignado (pasado y programado)."""
    # Cuenta lo ya trabajado y lo confirmado; lo provisorio se recalcula
    # despues igual para todos, asi que no se considera.
    cur.execute(f"""SELECT d.fecha, a.usuario_id FROM turnos_asignaciones a
                    JOIN turnos_dias d ON d.id = a.dia_id
                    WHERE d.fecha < {p()} OR d.estado = 'confirmado'""", (hoy().isoformat(),))
    todas = [(r["fecha"], r["usuario_id"]) for r in cur.fetchall()]
    tecnicos = leer_tecnicos(cur)
    stats = {t["usuario_id"]: Estadistica(t["usuario_id"]) for t in tecnicos}
    for f, u in todas:
        if u in stats:
            stats[u].agregar(a_fecha(f))
    otros = [t for t in tecnicos if t["activo"] and t["usuario_id"] != usuario_id]
    propio = stats.get(usuario_id) or Estadistica(usuario_id)
    if not otros:
        return 0, 0
    prom_dias = sum(stats[t["usuario_id"]].dias + (t["ajuste_dias"] or 0) for t in otros) / len(otros)
    prom_dobles = sum(stats[t["usuario_id"]].dobles + (t["ajuste_dobles"] or 0) for t in otros) / len(otros)
    return int(round(prom_dias - propio.dias)), int(round(prom_dobles - propio.dobles))
