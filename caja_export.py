"""
caja_export.py -- Rendicion de caja chica en PDF (fpdf2) y Excel (openpyxl).

  pdf_rendicion(e, gastos)      Una entrega: cuadre, detalle, firmas y fotos.
  excel_rendicion(e, gastos)    Lo mismo en Excel, con formulas de cuadre.
  pdf_periodo(...) / excel_periodo(...)   Resumen de un rango de fechas.
"""

import io
import os
from collections import OrderedDict
from datetime import date

import caja_logica as cl
from db import BASE_DIR, ahora

LOGO = os.path.join(BASE_DIR, "static", "logo_wintec.png")
VERDE = (21, 128, 61)          # color de la barra del portal
GRIS = (100, 116, 139)
GRIS_CLARO = (241, 245, 249)
ROJO = (180, 35, 24)


def _fecha(f):
    if not f:
        return "-"
    try:
        return date.fromisoformat(str(f)[:10]).strftime("%d-%m-%Y")
    except ValueError:
        return str(f)


def _tipo(g):
    return cl.TIPOS_DOC.get(g["tipo_doc"], g["tipo_doc"])


def _monto_firmado(g):
    return cl.signo(g["tipo_doc"]) * int(g["monto"] or 0)


# ═══════════════════════════════ PDF ═══════════════════════════════
def _t(texto):
    """Las fuentes base del PDF son Latin-1: se cambian los caracteres que no caben."""
    s = "" if texto is None else str(texto)
    for a, b in (("—", "-"), ("–", "-"), ("“", '"'), ("”", '"'), ("’", "'"), ("‘", "'"), ("…", "...")):
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def _pdf_base(titulo, subtitulo, horizontal=False):
    from fpdf import FPDF

    class PDF(FPDF):
        def header(self):
            if os.path.exists(LOGO):
                self.image(LOGO, x=self.l_margin, y=9, h=11)
            self.set_xy(self.l_margin, 9)
            self.set_font("Helvetica", "B", 13)
            self.set_text_color(*VERDE)
            self.cell(0, 6, _t(titulo), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_font("Helvetica", "", 9)
            self.set_text_color(*GRIS)
            self.cell(0, 5, _t(subtitulo), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_draw_color(*VERDE)
            self.set_line_width(0.6)
            self.line(self.l_margin, 23, self.w - self.r_margin, 23)
            self.set_line_width(0.2)
            self.set_draw_color(203, 213, 225)
            self.set_text_color(0, 0, 0)
            self.set_y(27)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*GRIS)
            self.cell(0, 5, _t(f"Portal Mantenimiento Wintec · Caja chica · generado el {ahora().strftime('%d-%m-%Y %H:%M')}"),
                      align="L")
            self.cell(0, 5, f"Página {self.page_no()} de {{nb}}", align="R")

    pdf = PDF(orientation="L" if horizontal else "P", unit="mm", format="A4")
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(10, 10, 10)
    pdf.set_title(_t(titulo))
    pdf.set_author("Portal Mantenimiento Wintec")
    pdf.alias_nb_pages()
    pdf.add_page()
    return pdf


def _etiqueta_valor(pdf, pares, ancho_col):
    """Bloque de datos en 2 columnas: etiqueta gris + valor."""
    x0 = pdf.l_margin
    for i in range(0, len(pares), 2):
        y = pdf.get_y()
        for j, (et, val) in enumerate(pares[i:i + 2]):
            pdf.set_xy(x0 + j * ancho_col, y)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*GRIS)
            pdf.cell(30, 5.5, _t(et.upper()))
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(ancho_col - 30, 5.5, _t(val))
        pdf.set_y(y + 6)


def _caja_cuadre(pdf, c):
    ancho = (pdf.w - pdf.l_margin - pdf.r_margin) / 4
    celdas = [
        ("Entregado", cl.clp(c["entregado"]), None),
        (f"Documentos ({c['n_documentos']})", cl.clp(c["total_documentos"]), None),
        ("Saldo devuelto" if c["saldo_devuelto"] is not None else "Por rendir",
         cl.clp(c["saldo_devuelto"] if c["saldo_devuelto"] is not None else c["disponible"]), None),
    ]
    if c["diferencia"] is None:
        celdas.append(("Diferencia", "Pendiente", GRIS_CLARO))
    elif c["cuadra"]:
        celdas.append(("Diferencia", "$0 · CUADRA", (227, 245, 236)))
    else:
        txt = ("falta " if c["diferencia"] > 0 else "sobra ") + cl.clp(abs(c["diferencia"]))
        celdas.append(("Diferencia", txt, (253, 236, 235)))
    y = pdf.get_y()
    for i, (et, val, fondo) in enumerate(celdas):
        x = pdf.l_margin + i * ancho
        pdf.set_fill_color(*(fondo or (255, 255, 255)))
        pdf.rect(x, y, ancho, 16, style="DF")
        pdf.set_xy(x + 3, y + 2)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*GRIS)
        pdf.cell(ancho - 6, 4, _t(et.upper()))
        pdf.set_xy(x + 3, y + 7)
        pdf.set_font("Helvetica", "B", 12)
        color = (0, 0, 0)
        if i == 3 and c["diferencia"] is not None:
            color = VERDE if c["cuadra"] else ROJO
        pdf.set_text_color(*color)
        pdf.cell(ancho - 6, 7, _t(val))
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 16 + 5)
    if c["diferencia"] is not None:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*GRIS)
        pdf.cell(0, 4, _t("Cuadre: Entregado - Documentos - Saldo devuelto = Diferencia (debe ser $0)"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)


def _tabla(pdf, columnas, filas, alto=6.5, total=None, zebra=True):
    """columnas: [(titulo, ancho, alineacion)]; filas: listas de textos."""
    def encabezado():
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(*VERDE)
        pdf.set_text_color(255, 255, 255)
        for tit, an, al in columnas:
            pdf.cell(an, 7, _t(tit), border=0, align=al if al != "L" else "L", fill=True)
        pdf.ln()
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 8)

    encabezado()
    for n, fila in enumerate(filas):
        if pdf.get_y() + alto > pdf.page_break_trigger:
            pdf.add_page()
            encabezado()
        pdf.set_fill_color(*(GRIS_CLARO if zebra and n % 2 else (255, 255, 255)))
        for (tit, an, al), val in zip(columnas, fila):
            texto = base = _t(val)
            while len(base) > 1 and pdf.get_string_width(texto) > an - 2:
                base = base[:-1].rstrip()
                texto = base + "..."
            pdf.cell(an, alto, texto, border="B", align=al, fill=True)
        pdf.ln()
    if total:
        pdf.set_font("Helvetica", "B", 9)
        etiqueta, valor = total
        ancho_total = sum(c[1] for c in columnas)
        ultimo = columnas[-1][1]
        pdf.cell(ancho_total - ultimo, 8, _t(etiqueta), align="R")
        pdf.cell(ultimo, 8, _t(valor), align="R", border="T")
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)


def _firmas(pdf, nombres):
    if pdf.get_y() > pdf.page_break_trigger - 30:
        pdf.add_page()
    pdf.ln(16)
    ancho = (pdf.w - pdf.l_margin - pdf.r_margin) / len(nombres)
    y = pdf.get_y()
    for i, (rol, nombre) in enumerate(nombres):
        x = pdf.l_margin + i * ancho
        pdf.line(x + 8, y, x + ancho - 8, y)
        pdf.set_xy(x, y + 1.5)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(ancho, 4.5, _t(nombre or " "), align="C")
        pdf.set_xy(x, y + 6)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*GRIS)
        pdf.cell(ancho, 4, _t(rol), align="C")
        pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 12)


def pdf_rendicion(e, gastos, con_fotos=True):
    c = cl.cuadre(e["monto_entregado"], gastos, e.get("saldo_devuelto"))
    pdf = _pdf_base(f"RENDICIÓN DE CAJA CHICA N° {e['id']}", f"Entrega del {_fecha(e['fecha'])}")
    ancho_util = pdf.w - pdf.l_margin - pdf.r_margin

    _etiqueta_valor(pdf, [
        ("Comprador", e.get("comprador") or "-"),
        ("Fecha entrega", _fecha(e["fecha"])),
        ("Entregado por", e.get("entregado_por") or "-"),
        ("Estado", cl.ESTADOS.get(e["estado"], e["estado"])),
    ], ancho_util / 2)
    if e.get("nota"):
        _etiqueta_valor(pdf, [("Nota", e["nota"])], ancho_util)
    pdf.ln(2)
    _caja_cuadre(pdf, c)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, _t("Detalle de documentos"), new_x="LMARGIN", new_y="NEXT")
    columnas = [("#", 7, "C"), ("Fecha", 17, "L"), ("Documento", 27, "L"), ("Folio", 15, "L"),
                ("RUT", 21, "L"), ("Proveedor", 40, "L"), ("Motivo", 41, "L"), ("Monto", 22, "R")]
    filas = []
    for i, g in enumerate(gastos, 1):
        filas.append([str(i), _fecha(g["fecha_doc"]), _tipo(g), g.get("folio") or "-",
                      cl.formatear_rut(g.get("rut_emisor")) or "-", g.get("proveedor") or "-",
                      g.get("motivo") or g.get("categoria") or "-", cl.clp(_monto_firmado(g))])
    if not filas:
        filas.append(["", "", "Sin documentos", "", "", "", "", ""])
    _tabla(pdf, columnas, filas, total=("Total documentos", cl.clp(c["total_documentos"])))

    obs = [(t, v) for t, v in (("Observación del cierre", e.get("obs_cierre")),
                               ("Comentario de aprobación", e.get("obs_aprobacion"))) if v]
    if obs:
        pdf.ln(3)
        for t, v in obs:
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.cell(0, 5, _t(t), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            pdf.multi_cell(0, 5, _t(v), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS)
    marcas = []
    if e.get("rendida_at"):
        marcas.append(f"Rendida por {e.get('rendida_por') or '-'} el {_fecha(e['rendida_at'])} {str(e['rendida_at'])[11:16]}")
    if e.get("aprobada_at"):
        marcas.append(f"Aprobada por {e.get('aprobada_por') or '-'} el {_fecha(e['aprobada_at'])} {str(e['aprobada_at'])[11:16]}")
    if marcas:
        pdf.multi_cell(0, 4.5, _t(" · ".join(marcas)), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    _firmas(pdf, [("Comprador", e.get("comprador")), ("Entregado / revisado por", e.get("entregado_por")),
                  ("Aprobado por", e.get("aprobada_por"))])

    if con_fotos:
        _anexo_fotos(pdf, gastos)

    salida = io.BytesIO(bytes(pdf.output()))
    salida.seek(0)
    return salida


def _anexo_fotos(pdf, gastos):
    from PIL import Image
    con_foto = [(i, g) for i, g in enumerate(gastos, 1) if g.get("foto")]
    if not con_foto:
        return
    ancho_util = pdf.w - pdf.l_margin - pdf.r_margin
    alto_max = (pdf.h - 27 - 16 - 16) / 2       # 2 fotos por pagina
    pos = 0
    for i, g in con_foto:
        data = cl.leer_foto(g["foto"])
        if not data:
            continue
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            continue
        if pos % 2 == 0:
            pdf.add_page()
            if pos == 0:
                pdf.set_font("Helvetica", "B", 10)
                pdf.cell(0, 6, _t("Anexo: fotos de los documentos"), new_x="LMARGIN", new_y="NEXT")
        y0 = pdf.get_y()
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(0, 5, _t(f"#{i} · {_tipo(g)} {g.get('folio') or ''} · {g.get('proveedor') or ''} · "
                          f"{cl.clp(_monto_firmado(g))}"), new_x="LMARGIN", new_y="NEXT")
        w, h = img.size
        escala = min(ancho_util / w, (alto_max - 8) / h)
        an, al = w * escala, h * escala
        pdf.image(img.convert("RGB"), x=pdf.l_margin + (ancho_util - an) / 2, y=pdf.get_y() + 1, w=an, h=al)
        pdf.set_y(y0 + alto_max + 2)
        pos += 1


def pdf_periodo(entregas, gastos, desde, hasta):
    pdf = _pdf_base("RESUMEN DE CAJA CHICA", f"Del {_fecha(desde)} al {_fecha(hasta)}", horizontal=True)
    tot_ent = sum(e["monto_entregado"] for e in entregas)
    tot_gas = sum(e["cuadre"]["total_documentos"] for e in entregas)
    con_dif = [e for e in entregas if e["cuadre"]["cuadra"] is False]
    abiertas = [e for e in entregas if e["estado"] == "abierta"]

    ancho = (pdf.w - pdf.l_margin - pdf.r_margin) / 4
    y = pdf.get_y()
    for i, (et, val) in enumerate([("Entregas", str(len(entregas))), ("Total entregado", cl.clp(tot_ent)),
                                   ("Total en documentos", cl.clp(tot_gas)),
                                   ("Con diferencia / abiertas", f"{len(con_dif)} / {len(abiertas)}")]):
        x = pdf.l_margin + i * ancho
        pdf.rect(x, y, ancho, 15)
        pdf.set_xy(x + 3, y + 2)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*GRIS)
        pdf.cell(ancho - 6, 4, _t(et.upper()))
        pdf.set_xy(x + 3, y + 7)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(ancho - 6, 6, _t(val))
    pdf.set_y(y + 20)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Entregas", new_x="LMARGIN", new_y="NEXT")
    columnas = [("N°", 14, "C"), ("Fecha", 22, "L"), ("Comprador", 55, "L"), ("Docs", 14, "C"),
                ("Entregado", 30, "R"), ("Documentos", 30, "R"), ("Saldo devuelto", 30, "R"),
                ("Diferencia", 28, "R"), ("Estado", 54, "L")]
    filas = []
    for e in entregas:
        c = e["cuadre"]
        filas.append([str(e["id"]), _fecha(e["fecha"]), e.get("comprador") or "-", str(c["n_documentos"]),
                      cl.clp(e["monto_entregado"]), cl.clp(c["total_documentos"]),
                      cl.clp(e["saldo_devuelto"]) if e.get("saldo_devuelto") is not None else "-",
                      cl.clp(c["diferencia"]) if c["diferencia"] is not None else "-",
                      cl.ESTADOS.get(e["estado"], e["estado"])])
    if not filas:
        filas.append(["", "", "Sin entregas en el período", "", "", "", "", "", ""])
    _tabla(pdf, columnas, filas)

    por_cat = OrderedDict((k, 0) for k in cl.CATEGORIAS)
    for g in gastos:
        por_cat[g.get("categoria") or "Otros"] = por_cat.get(g.get("categoria") or "Otros", 0) + _monto_firmado(g)
    filas_cat = [[k, cl.clp(v), f"{(v / tot_gas * 100):.0f}%" if tot_gas else "-"] for k, v in por_cat.items() if v]
    if filas_cat:
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, _t("Gasto por categoría"), new_x="LMARGIN", new_y="NEXT")
        _tabla(pdf, [("Categoría", 70, "L"), ("Monto", 35, "R"), ("%", 20, "R")], filas_cat,
               total=("Total", cl.clp(tot_gas)))

    if gastos:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Detalle de documentos", new_x="LMARGIN", new_y="NEXT")
        cols = [("N° entr.", 16, "C"), ("Fecha", 20, "L"), ("Documento", 32, "L"), ("Folio", 18, "L"),
                ("RUT", 24, "L"), ("Proveedor", 55, "L"), ("Categoría", 36, "L"), ("Motivo", 51, "L"), ("Monto", 25, "R")]
        filas = [[str(g["entrega_id"]), _fecha(g["fecha_doc"]), _tipo(g), g.get("folio") or "-",
                  cl.formatear_rut(g.get("rut_emisor")) or "-", g.get("proveedor") or "-", g.get("categoria") or "-",
                  g.get("motivo") or "-", cl.clp(_monto_firmado(g))] for g in gastos]
        _tabla(pdf, cols, filas, total=("Total", cl.clp(sum(_monto_firmado(g) for g in gastos))))

    salida = io.BytesIO(bytes(pdf.output()))
    salida.seek(0)
    return salida


# ══════════════════════════════ EXCEL ══════════════════════════════
FMT_CLP = '"$"#,##0;[Red]-"$"#,##0'


def _estilos():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    fino = Side(style="thin", color="CBD5E1")
    return {
        "titulo": Font(bold=True, size=14, color="15803D"),
        "sub": Font(size=10, color="64748B"),
        "et": Font(size=9, color="64748B", bold=True),
        "bold": Font(bold=True),
        "head_font": Font(bold=True, color="FFFFFF"),
        "head_fill": PatternFill("solid", fgColor="15803D"),
        "zebra": PatternFill("solid", fgColor="F1F5F9"),
        "ok": PatternFill("solid", fgColor="E3F5EC"),
        "bad": PatternFill("solid", fgColor="FDECEB"),
        "borde": Border(bottom=fino),
        "centro": Alignment(horizontal="center", vertical="center"),
        "wrap": Alignment(wrap_text=True, vertical="top"),
    }


def _encabezado(ws, fila, titulos, st):
    for col, t in enumerate(titulos, 1):
        c = ws.cell(row=fila, column=col, value=t)
        c.font, c.fill, c.alignment = st["head_font"], st["head_fill"], st["centro"]


def _anchos(ws, anchos):
    from openpyxl.utils import get_column_letter
    for i, a in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(i)].width = a


def excel_rendicion(e, gastos):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule
    st = _estilos()
    wb = Workbook()
    ws = wb.active
    ws.title = "Rendición"
    ws.sheet_view.showGridLines = False

    ws["A1"] = f"Rendición de caja chica N° {e['id']}"
    ws["A1"].font = st["titulo"]
    ws["A2"] = f"Portal Mantenimiento Wintec · generado el {ahora().strftime('%d-%m-%Y %H:%M')}"
    ws["A2"].font = st["sub"]

    datos = [("Comprador", e.get("comprador") or "-"), ("Fecha entrega", _fecha(e["fecha"])),
             ("Entregado por", e.get("entregado_por") or "-"), ("Estado", cl.ESTADOS.get(e["estado"], e["estado"]))]
    if e.get("nota"):
        datos.append(("Nota", e["nota"]))
    fila = 4
    for et, val in datos:
        ws.cell(row=fila, column=1, value=et).font = st["et"]
        ws.cell(row=fila, column=2, value=val).font = st["bold"]
        fila += 1

    # Detalle
    fila += 1
    ini_tabla = fila
    _encabezado(ws, fila, ["#", "Fecha", "Documento", "Folio", "RUT proveedor", "Proveedor", "Categoría", "Motivo", "Monto"], st)
    primera = fila + 1
    for i, g in enumerate(gastos, 1):
        fila += 1
        f_doc = None
        try:
            f_doc = date.fromisoformat(str(g["fecha_doc"])[:10])
        except (TypeError, ValueError):
            pass
        valores = [i, f_doc, _tipo(g), g.get("folio") or "", cl.formatear_rut(g.get("rut_emisor")),
                   g.get("proveedor") or "", g.get("categoria") or "", g.get("motivo") or "", _monto_firmado(g)]
        for col, v in enumerate(valores, 1):
            c = ws.cell(row=fila, column=col, value=v)
            c.border = st["borde"]
            if i % 2 == 0:
                c.fill = st["zebra"]
        ws.cell(row=fila, column=2).number_format = "DD-MM-YYYY"
        ws.cell(row=fila, column=9).number_format = FMT_CLP
    ultima = max(fila, primera)
    if gastos:
        ws.auto_filter.ref = f"A{ini_tabla}:I{fila}"
    fila += 1
    ws.cell(row=fila, column=8, value="Total documentos").font = st["bold"]
    c_tot = ws.cell(row=fila, column=9, value=f"=SUM(I{primera}:I{ultima})" if gastos else 0)
    c_tot.font, c_tot.number_format = st["bold"], FMT_CLP
    fila_total = fila

    # Cuadre con formulas (si alguien corrige un monto en el Excel, se recalcula)
    fila += 2
    ws.cell(row=fila, column=1, value="CUADRE").font = st["titulo"]
    fila += 1
    f_ent = fila
    ws.cell(row=fila, column=1, value="Monto entregado").font = st["et"]
    ws.cell(row=fila, column=2, value=int(e["monto_entregado"])).number_format = FMT_CLP
    fila += 1
    f_doc_tot = fila
    ws.cell(row=fila, column=1, value="(-) Documentos").font = st["et"]
    ws.cell(row=fila, column=2, value=f"=I{fila_total}").number_format = FMT_CLP
    fila += 1
    f_sal = fila
    ws.cell(row=fila, column=1, value="(-) Saldo devuelto").font = st["et"]
    c_sal = ws.cell(row=fila, column=2, value=e.get("saldo_devuelto"))
    c_sal.number_format = FMT_CLP
    fila += 1
    ws.cell(row=fila, column=1, value="= Diferencia").font = st["bold"]
    c_dif = ws.cell(row=fila, column=2, value=f'=IF(B{f_sal}="","Pendiente",B{f_ent}-B{f_doc_tot}-B{f_sal})')
    c_dif.number_format, c_dif.font = FMT_CLP, st["bold"]
    ws.conditional_formatting.add(f"B{fila}", CellIsRule(operator="equal", formula=["0"], fill=st["ok"]))
    ws.conditional_formatting.add(f"B{fila}", CellIsRule(operator="notEqual", formula=["0"], fill=st["bad"]))
    ws.cell(row=fila, column=3, value="Debe dar $0 (positivo = falta dinero, negativo = sobra)").font = st["sub"]

    for et, v in (("Observación del cierre", e.get("obs_cierre")), ("Comentario de aprobación", e.get("obs_aprobacion"))):
        if v:
            fila += 2
            ws.cell(row=fila, column=1, value=et).font = st["et"]
            ws.cell(row=fila, column=2, value=v).alignment = st["wrap"]
    if e.get("rendida_at"):
        fila += 2
        ws.cell(row=fila, column=1, value="Rendida").font = st["et"]
        ws.cell(row=fila, column=2, value=f"{e.get('rendida_por') or '-'} · {str(e['rendida_at'])[:16]}")
    if e.get("aprobada_at"):
        fila += 1
        ws.cell(row=fila, column=1, value="Aprobada").font = st["et"]
        ws.cell(row=fila, column=2, value=f"{e.get('aprobada_por') or '-'} · {str(e['aprobada_at'])[:16]}")

    _anchos(ws, [18, 14, 20, 11, 15, 30, 18, 34, 14])
    ws.freeze_panes = f"A{ini_tabla + 1}"
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out


def excel_periodo(entregas, gastos, desde, hasta):
    from openpyxl import Workbook
    st = _estilos()
    wb = Workbook()

    # Hoja 1: entregas
    ws = wb.active
    ws.title = "Entregas"
    ws.sheet_view.showGridLines = False
    ws["A1"] = f"Caja chica del {_fecha(desde)} al {_fecha(hasta)}"
    ws["A1"].font = st["titulo"]
    ws["A2"] = f"Portal Mantenimiento Wintec · generado el {ahora().strftime('%d-%m-%Y %H:%M')}"
    ws["A2"].font = st["sub"]
    _encabezado(ws, 4, ["N°", "Fecha", "Comprador", "N° docs", "Entregado", "Documentos", "Saldo devuelto",
                        "Diferencia", "Estado", "Observación"], st)
    f = 4
    for e in entregas:
        f += 1
        c = e["cuadre"]
        valores = [e["id"], date.fromisoformat(e["fecha"][:10]), e.get("comprador"), c["n_documentos"],
                   e["monto_entregado"], c["total_documentos"], e.get("saldo_devuelto"),
                   f'=IF(G{f}="","",E{f}-F{f}-G{f})', cl.ESTADOS.get(e["estado"], e["estado"]), e.get("obs_cierre") or ""]
        for col, v in enumerate(valores, 1):
            cell = ws.cell(row=f, column=col, value=v)
            cell.border = st["borde"]
        ws.cell(row=f, column=2).number_format = "DD-MM-YYYY"
        for col in (5, 6, 7, 8):
            ws.cell(row=f, column=col).number_format = FMT_CLP
        if c["cuadra"] is False:
            ws.cell(row=f, column=8).fill = st["bad"]
    if entregas:
        ws.auto_filter.ref = f"A4:J{f}"
        f += 1
        ws.cell(row=f, column=4, value="Totales").font = st["bold"]
        for col, letra in ((5, "E"), (6, "F"), (7, "G")):
            c = ws.cell(row=f, column=col, value=f"=SUM({letra}5:{letra}{f - 1})")
            c.font, c.number_format = st["bold"], FMT_CLP
    _anchos(ws, [7, 12, 24, 9, 14, 14, 15, 13, 22, 40])
    ws.freeze_panes = "A5"

    # Hoja 2: documentos
    wd = wb.create_sheet("Documentos")
    wd.sheet_view.showGridLines = False
    _encabezado(wd, 1, ["N° entrega", "Fecha entrega", "Fecha doc.", "Documento", "Folio", "RUT proveedor",
                        "Proveedor", "Categoría", "Motivo", "Monto", "Leído del timbre"], st)
    r = 1
    for g in gastos:
        r += 1
        try:
            f_doc = date.fromisoformat(str(g["fecha_doc"])[:10])
        except (TypeError, ValueError):
            f_doc = None
        valores = [g["entrega_id"], date.fromisoformat(str(g["fecha_entrega"])[:10]), f_doc, _tipo(g),
                   g.get("folio") or "", cl.formatear_rut(g.get("rut_emisor")), g.get("proveedor") or "",
                   g.get("categoria") or "", g.get("motivo") or "", _monto_firmado(g),
                   "Sí" if g.get("origen") == "timbre" else "No"]
        for col, v in enumerate(valores, 1):
            wd.cell(row=r, column=col, value=v).border = st["borde"]
        wd.cell(row=r, column=2).number_format = "DD-MM-YYYY"
        wd.cell(row=r, column=3).number_format = "DD-MM-YYYY"
        wd.cell(row=r, column=10).number_format = FMT_CLP
    if gastos:
        wd.auto_filter.ref = f"A1:K{r}"
        r += 1
        wd.cell(row=r, column=9, value="Total").font = st["bold"]
        c = wd.cell(row=r, column=10, value=f"=SUM(J2:J{r - 1})")
        c.font, c.number_format = st["bold"], FMT_CLP
    _anchos(wd, [11, 13, 12, 20, 11, 15, 30, 22, 36, 14, 10])
    wd.freeze_panes = "A2"

    # Hoja 3: por categoria (con formulas sobre la hoja Documentos)
    wc = wb.create_sheet("Por categoría")
    wc.sheet_view.showGridLines = False
    _encabezado(wc, 1, ["Categoría", "Monto", "N° documentos"], st)
    ult = max(r - 1, 2)
    for i, cat in enumerate(cl.CATEGORIAS, 2):
        wc.cell(row=i, column=1, value=cat)
        wc.cell(row=i, column=2, value=f"=SUMIF(Documentos!H2:H{ult},A{i},Documentos!J2:J{ult})").number_format = FMT_CLP
        wc.cell(row=i, column=3, value=f"=COUNTIF(Documentos!H2:H{ult},A{i})")
    t = len(cl.CATEGORIAS) + 2
    wc.cell(row=t, column=1, value="Total").font = st["bold"]
    c = wc.cell(row=t, column=2, value=f"=SUM(B2:B{t - 1})")
    c.font, c.number_format = st["bold"], FMT_CLP
    wc.cell(row=t, column=3, value=f"=SUM(C2:C{t - 1})").font = st["bold"]
    _anchos(wc, [26, 16, 15])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out
