"""
turnos_correo.py -- Correo de "Solicitud de ingreso personal de mantenimiento".

La app no envia el correo (no tiene la contrasena del correo de empresa):
lo redacta con el formato de siempre y lo deja listo para abrir en Outlook
con un clic. Aqui vive:
  - la configuracion (destinatarios, lineas, tarea por defecto),
  - la redaccion (texto plano para Outlook y HTML con negritas para pegar),
  - el registro de "ya lo envie" y la deteccion de cambios posteriores.
"""

import hashlib
import json
from datetime import timedelta
from urllib.parse import quote

from db import p, USE_POSTGRES, ahora, ahora_str
import turnos_logica as tl

MESES_CAP = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
             "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

CONFIG_DEFECTO = {
    "para": "lgomez@wintec.cl",
    "cc": "evidal@wintec.cl, planta@wintec.cl",
    "asunto": "Solicitud de ingreso personal de mantenimiento",
    "lineas": "Europea\nAmericana\nTermopanel\nCorte de cristales",
    "tarea": "hacer mantenimiento correctivo",
}
_PREFIJO = "turnos_correo_"


# ── Configuracion (tabla sistema_meta) ───────────────────────────
def leer_config(cur):
    cfg = dict(CONFIG_DEFECTO)
    try:
        cur.execute(f"SELECT clave, valor FROM sistema_meta WHERE clave LIKE {p()}", (_PREFIJO + "%",))
        for r in cur.fetchall():
            k = r["clave"][len(_PREFIJO):]
            if k in cfg and r["valor"] is not None:
                cfg[k] = r["valor"]
    except Exception:
        pass
    cfg["lista_lineas"] = [x.strip() for x in cfg["lineas"].splitlines() if x.strip()]
    return cfg


def guardar_config(cur, datos):
    ph = p()
    for k in CONFIG_DEFECTO:
        if k not in datos:
            continue
        valor = (datos[k] or "").strip()
        if k == "lineas":
            valor = "\n".join(x.strip() for x in valor.replace(",", "\n").splitlines() if x.strip())
        if not valor:
            valor = CONFIG_DEFECTO[k]
        cur.execute(f"DELETE FROM sistema_meta WHERE clave = {ph}", (_PREFIJO + k,))
        cur.execute(f"INSERT INTO sistema_meta (clave, valor) VALUES ({ph},{ph})", (_PREFIJO + k, valor))


def separar_correos(texto):
    return [x.strip() for x in (texto or "").replace(";", ",").split(",") if x.strip()]


# ── Agrupacion ───────────────────────────────────────────────────
def agrupar_consecutivos(dias):
    """Dias trabajados seguidos van en un mismo correo (sabado+domingo,
    o un fin de semana largo con lunes feriado)."""
    grupos = []
    for d in sorted(dias, key=lambda x: x["fecha_obj"]):
        if grupos and (d["fecha_obj"] - grupos[-1][-1]["fecha_obj"]).days <= 1:
            grupos[-1].append(d)
        else:
            grupos.append([d])
    return grupos


def clave_grupo(grupo):
    return grupo[0]["fecha_obj"].isoformat()


# ── Redaccion ────────────────────────────────────────────────────
def _y(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]


def _dia_corto(f, mayus=True):
    nombre = tl.DIAS_SEMANA[f.weekday()]
    nombre = nombre if mayus else nombre.lower()
    return f"{nombre} {f.day:02d}"


def frase_fechas(fechas):
    """[sab 10, dom 11] -> 'sábado 10 y domingo 11 de Octubre del 2026'."""
    fechas = sorted(fechas)
    por_mes = []
    for f in fechas:
        if por_mes and (por_mes[-1][0].year, por_mes[-1][0].month) == (f.year, f.month):
            por_mes[-1].append(f)
        else:
            por_mes.append([f])
    partes = [f"{_y(_dia_corto(f, False) for f in grupo)} de {MESES_CAP[grupo[0].month]}" for grupo in por_mes]
    if len({f.year for f in fechas}) == 1:
        return f"{_y(partes)} del {fechas[0].year}"
    return _y(f"{parte} del {grupo[0].year}" for parte, grupo in zip(partes, por_mes))


def personas_del_grupo(grupo, cfg):
    """Lista ordenada de personas con sus dias, linea y tarea efectivas."""
    personas = {}
    for d in grupo:
        for a in d["asignados"]:
            info = personas.setdefault(a["usuario_id"], {"usuario_id": a["usuario_id"], "nombre": a["nombre"], "dias": []})
            info["dias"].append({
                "fecha": d["fecha_obj"],
                "asignacion_id": a["id"],
                "linea": linea_efectiva(a),
                "tarea": (a.get("tarea") or cfg["tarea"]).strip(),
                "linea_propia": a.get("linea"),
                "tarea_propia": a.get("tarea"),
            })
    return list(personas.values())


def linea_efectiva(a):
    """La linea del dia si se definio (aunque sea vacia = sin linea);
    si no, la linea habitual del tecnico."""
    if a.get("linea") is not None:
        return a["linea"].strip()
    return (a.get("linea_habitual") or "").strip()


def _frase_tarea(tarea, linea):
    texto = f"estará a cargo de {tarea}"
    if linea:
        texto += f" de la línea {linea}"
    return texto + "."


def _esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def redactar(grupo, cfg, momento=None):
    """Devuelve asunto, texto plano, html, destinatarios y firma."""
    momento = momento or ahora()
    fechas = [d["fecha_obj"] for d in grupo]
    personas = personas_del_grupo(grupo, cfg)
    saludo = "Buenos días," if momento.hour < 12 else "Buenas tardes,"
    intro = ("Junto con saludar, por medio del presente solicito gestionar el acceso del personal "
             "de mantenimiento para los siguientes días: ")
    fechas_txt = frase_fechas(fechas)
    cierre = ["Agradezco desde ya su apoyo para la correcta coordinación de los accesos.",
              "Quedo atento a cualquier comentario o requerimiento adicional.",
              "Saludos cordiales,"]

    texto = [saludo, "", f"{intro}{fechas_txt}, de acuerdo con la siguiente planificación:", ""]
    html = [f"<p>{saludo}</p>",
            f"<p>{_esc(intro)}<b>{_esc(fechas_txt)}</b>, de acuerdo con la siguiente planificación:</p>"]
    for per in personas:
        dias_txt = _y(_dia_corto(x["fecha"]) for x in per["dias"]) + "."
        combos = {(x["tarea"], x["linea"]) for x in per["dias"]}
        if len(combos) == 1:
            tarea, linea = combos.pop()
            frase = _frase_tarea(tarea, linea)
            detalle = [frase[0].upper() + frase[1:]]
        else:
            detalle = [f"{_dia_corto(x['fecha'])}: {_frase_tarea(x['tarea'], x['linea'])}" for x in per["dias"]]
        texto.append(f"• {per['nombre']} – {dias_txt}")
        texto.extend(detalle)
        texto.append("")
        html.append("<p>• <b>" + _esc(per["nombre"]) + "</b> – <i>" + _esc(dias_txt) + "</i><br>"
                    + "<br>".join(_esc(x) for x in detalle) + "</p>")
    texto.extend([cierre[0], "", cierre[1], "", cierre[2]])
    html.extend(f"<p>{_esc(x)}</p>" for x in cierre)

    dias_asunto = _y(f"{f.day:02d}/{f.month:02d}" for f in sorted(fechas))
    return {
        "asunto": f"{cfg['asunto']} – {dias_asunto}",
        "texto": "\n".join(texto),
        "html": "".join(html),
        "para": separar_correos(cfg["para"]),
        "cc": separar_correos(cfg["cc"]),
        "personas": personas,
        "fechas": fechas,
        "firma": firma(grupo, cfg),
    }


def firma(grupo, cfg):
    """Huella del contenido (quien, que dia, linea y tarea). Si cambia
    despues de enviado, el correo quedo desactualizado."""
    datos = sorted(
        (x["fecha"].isoformat(), per["usuario_id"], x["linea"], x["tarea"])
        for per in personas_del_grupo(grupo, cfg) for x in per["dias"]
    )
    return hashlib.sha1(json.dumps(datos, ensure_ascii=False).encode()).hexdigest()[:16]


def url_outlook(redaccion):
    """Abre un correo nuevo en Outlook web ya completado."""
    partes = [
        "to=" + quote(",".join(redaccion["para"]), safe="@,"),
        "cc=" + quote(",".join(redaccion["cc"]), safe="@,"),
        "subject=" + quote(redaccion["asunto"], safe=""),
        "body=" + quote(redaccion["texto"], safe=""),
    ]
    return "https://outlook.office.com/mail/deeplink/compose?" + "&".join(partes)


def url_mailto(redaccion):
    return ("mailto:" + quote(",".join(redaccion["para"]), safe="@,")
            + "?cc=" + quote(",".join(redaccion["cc"]), safe="@,")
            + "&subject=" + quote(redaccion["asunto"], safe="")
            + "&body=" + quote(redaccion["texto"], safe=""))


# ── Registro de envio ────────────────────────────────────────────
def leer_envio(cur, clave):
    cur.execute(f"SELECT * FROM turnos_correos WHERE clave = {p()}", (clave,))
    r = cur.fetchone()
    return dict(r) if r else None


def estado_envio(envio, firma_actual):
    if not envio or not envio.get("enviado_at"):
        return "pendiente"
    return "enviado" if envio.get("firma") == firma_actual else "desactualizado"


def marcar_enviado(cur, clave, fechas, firma_actual, usuario):
    ph = p()
    cur.execute(f"DELETE FROM turnos_correos WHERE clave = {ph}", (clave,))
    tl.insertar(cur, f"""INSERT INTO turnos_correos (clave, fechas, firma, enviado_at, enviado_por, avisado_firma)
                         VALUES ({ph},{ph},{ph},{ph},{ph},{ph})""",
                (clave, ",".join(f.isoformat() for f in fechas), firma_actual, ahora_str(), usuario, firma_actual))


def desmarcar_enviado(cur, clave):
    cur.execute(f"DELETE FROM turnos_correos WHERE clave = {p()}", (clave,))
