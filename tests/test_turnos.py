"""Pruebas del modulo de turnos de fin de semana."""
from collections import Counter
from datetime import date, timedelta

import bcrypt
import pytest

import turnos_logica as tl
from turnos_logica import repartir, sugerir_reemplazos

HOY = date(2026, 10, 7)          # miercoles
SAB = date(2026, 10, 10)


def _fines(n, desde=SAB, personas=2):
    dias = []
    for w in range(n):
        for k in (0, 1):
            dias.append({"fecha": desde + timedelta(days=7 * w + k), "personas": personas})
    return dias


def _tec(*uids):
    return [{"uid": u, "activo": True, "orden": i} for i, u in enumerate(uids)]


# ── Algoritmo puro ────────────────────────────────────────────────
def test_tres_tecnicos_reparto_parejo_y_doble_rota():
    dias = _fines(9)
    r = repartir(dias, [], _tec("A", "B", "C"))
    conteo = Counter(u for d in dias for u in r[d["fecha"]])
    assert conteo == {"A": 12, "B": 12, "C": 12}
    dobles = []
    for w in range(9):
        sab, dom = SAB + timedelta(days=7 * w), SAB + timedelta(days=7 * w + 1)
        assert len(r[sab]) == 2 and len(r[dom]) == 2
        repite = set(r[sab]) & set(r[dom])
        assert len(repite) == 1          # con 3 personas, uno hace los dos dias
        dobles.append(repite.pop())
    assert Counter(dobles) == {"A": 3, "B": 3, "C": 3}


def test_cinco_tecnicos_no_hay_dobles():
    dias = _fines(5)
    r = repartir(dias, [], _tec("A", "B", "C", "D", "E"))
    for w in range(5):
        sab, dom = SAB + timedelta(days=7 * w), SAB + timedelta(days=7 * w + 1)
        assert not set(r[sab]) & set(r[dom])
    conteo = Counter(u for d in dias for u in r[d["fecha"]])
    assert max(conteo.values()) - min(conteo.values()) <= 1


def test_dia_con_mas_personas_por_exceso_de_trabajo():
    dias = _fines(3)
    dias[0]["personas"] = 3
    r = repartir(dias, [], _tec("A", "B", "C", "D"))
    assert len(r[SAB]) == 3
    conteo = Counter(u for d in dias for u in r[d["fecha"]])
    assert max(conteo.values()) - min(conteo.values()) <= 1


def test_ausencia_se_respeta_y_se_compensa_despues():
    dias = _fines(6)
    aus = [("A", SAB, SAB + timedelta(days=1))]
    r = repartir(dias, [], _tec("A", "B", "C"), aus)
    assert "A" not in r[SAB] and "A" not in r[SAB + timedelta(days=1)]
    conteo = Counter(u for d in dias for u in r[d["fecha"]])
    assert max(conteo.values()) - min(conteo.values()) <= 1


def test_fijos_no_se_mueven():
    dias = _fines(2)
    dias[0]["fijos"] = ["C"]
    r = repartir(dias, [], _tec("A", "B", "C"))
    assert "C" in r[SAB] and len(r[SAB]) == 2


def test_faltan_personas_si_no_alcanza():
    dias = [{"fecha": SAB, "personas": 4}]
    r = repartir(dias, [], _tec("A", "B"))
    assert r["_faltan"][SAB] == 2


def test_ocasional_con_ajuste_no_acapara_turnos():
    historial = [(SAB - timedelta(days=7 * w + k), u) for w in range(1, 4) for k in (0, 1)
                 for u in ("A", "B")]
    tec = _tec("A", "B", "C") + [{"uid": "D", "activo": True, "orden": 9, "ajuste_dias": 6}]
    r = repartir(_fines(1), historial, tec)
    # C no tiene historial (0) y D parte en el promedio (6): C debe ir primero
    assert "C" in r[SAB]


def test_sugerir_reemplazo_elige_al_que_lleva_menos():
    historial = [(SAB - timedelta(days=1), "A"), (SAB - timedelta(days=2), "A"), (SAB - timedelta(days=2), "B")]
    sug = sugerir_reemplazos(SAB, {"D"}, historial, _tec("A", "B", "C", "D"))
    assert sug[0] == "C" and "D" not in sug


def test_horizonte_confirmado():
    assert tl.fin_horizonte_confirmado(HOY, 2) == date(2026, 10, 18)
    assert tl.fin_horizonte_confirmado(date(2026, 10, 10), 2) == date(2026, 10, 18)
    assert tl.fin_horizonte_confirmado(date(2026, 10, 11), 1) == date(2026, 10, 11)


# ── Flujo completo en la app ──────────────────────────────────────
@pytest.fixture()
def app_turnos(admin_client, monkeypatch):
    monkeypatch.setattr(tl, "hoy", lambda: HOY)
    from db import get_db_connection, p
    conn = get_db_connection()
    cur = conn.cursor()
    ids = {}
    clave = bcrypt.hashpw(b"Clave123!", bcrypt.gensalt()).decode()
    for u in ("ana", "beto", "carla", "dani"):
        cur.execute(f"INSERT INTO usuarios (username, password, nombre, role) VALUES ({p()},{p()},{p()},'solicitante')",
                    (u, clave, u.capitalize()))
        ids[u] = cur.lastrowid
    conn.commit()
    conn.close()
    for u in ("ana", "beto", "carla"):
        r = admin_client.post("/turnos/tecnicos/agregar", data={"usuario_id": ids[u], "tipo": "fijo"})
        assert r.status_code == 302
    admin_client.post("/turnos/tecnicos/agregar", data={"usuario_id": ids["dani"], "tipo": "ocasional"})
    return admin_client, ids


def _asignaciones():
    from db import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""SELECT d.fecha, d.estado, d.personas, a.usuario_id, a.origen FROM turnos_dias d
                   LEFT JOIN turnos_asignaciones a ON a.dia_id = d.id ORDER BY d.fecha""")
    filas = [dict(r) for r in cur.fetchall()]
    conn.close()
    return filas


def _planificar(client, fines=4, extra=None):
    data = {"desde": SAB.isoformat()}
    for w in range(fines):
        for k in (0, 1):
            f = (SAB + timedelta(days=7 * w + k)).isoformat()
            data[f"trabaja_{f}"] = "1"
            data[f"personas_{f}"] = "2"
    data.update(extra or {})
    return client.post("/turnos/planificacion", data=data)


def test_flujo_planificar_pedir_cambio_y_aprobar(app_turnos):
    client, ids = app_turnos
    assert _planificar(client).status_code == 302
    filas = _asignaciones()
    conteo = Counter(f["usuario_id"] for f in filas)
    assert set(conteo) == {ids["ana"], ids["beto"], ids["carla"]}     # dani (ocasional inactivo) no entra
    assert sorted(conteo.values()) == [5, 5, 6]
    estados = {f["fecha"]: f["estado"] for f in filas}
    assert estados["2026-10-10"] == "confirmado" and estados["2026-10-18"] == "confirmado"
    assert estados["2026-10-24"] == "provisorio"

    for ruta in ("/turnos", "/turnos/planificacion", "/turnos/tecnicos", "/turnos/equidad",
                 "/turnos/cambios", "/notificaciones", "/turnos?mes=2026-11"):
        assert client.get(ruta).status_code == 200, ruta

    # Un tecnico asignado el sabado 10 pide cambio
    sabado = [f for f in filas if f["fecha"] == "2026-10-10"]
    uid_pide = sabado[0]["usuario_id"]
    nombre = {v: k for k, v in ids.items()}[uid_pide]
    client.get("/logout")
    client.post("/login", data={"username": nombre, "password": "Clave123!"})
    html = client.get("/turnos?mes=2026-10").get_data(as_text=True)
    assert "No puedo ir" in html
    from db import get_db_connection
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM turnos_dias WHERE fecha = '2026-10-10'")
    dia_id = cur.fetchone()["id"]; conn.close()
    r = client.post(f"/turnos/dia/{dia_id}/no-puedo", data={"motivo": "Control médico"})
    assert r.status_code == 302
    client.get("/logout")

    # El admin ve el aviso y aprueba con el reemplazo sugerido
    client.post("/login", data={"username": "admin", "password": "TestAdmin123!"})
    assert "Control médico" in client.get("/notificaciones").get_data(as_text=True)
    html = client.get("/turnos/cambios").get_data(as_text=True)
    assert "recomendado" in html
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM turnos_cambios WHERE estado = 'pendiente'")
    cid = cur.fetchone()["id"]; conn.close()
    otro = [u for u in (ids["ana"], ids["beto"], ids["carla"])
            if u not in {f["usuario_id"] for f in sabado}][0]
    r = client.post(f"/turnos/cambios/{cid}/resolver", data={"accion": "aprobar", "reemplazo": str(otro)})
    assert r.status_code == 302

    filas2 = _asignaciones()
    sab2 = {f["usuario_id"] for f in filas2 if f["fecha"] == "2026-10-10"}
    assert uid_pide not in sab2 and otro in sab2 and len(sab2) == 2
    # El reparto se reajusta: la diferencia sigue siendo de a lo mas 1 dia
    conteo2 = Counter(f["usuario_id"] for f in filas2)
    assert max(conteo2.values()) - min(conteo2.values()) <= 1

    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(f"SELECT mensaje FROM notificaciones WHERE usuario_id = {uid_pide}")
    assert any("Aprobado" in r["mensaje"] for r in cur.fetchall())
    cur.execute(f"SELECT mensaje FROM notificaciones WHERE usuario_id = {otro}")
    assert any("Cubres" in r["mensaje"] for r in cur.fetchall())
    conn.close()


def test_ocasional_activado_parte_en_el_promedio(app_turnos):
    client, ids = app_turnos
    _planificar(client, fines=3)
    from db import get_db_connection
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(f"SELECT id FROM turnos_tecnicos WHERE usuario_id = {ids['dani']}")
    tid = cur.fetchone()["id"]; conn.close()
    client.post(f"/turnos/tecnicos/{tid}/actualizar", data={"accion": "activar"})
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(f"SELECT ajuste_dias, activo FROM turnos_tecnicos WHERE id = {tid}")
    t = dict(cur.fetchone()); conn.close()
    # 2 fines confirmados (8 plazas) entre 3 personas -> promedio ~3
    assert t["activo"] == 1 and t["ajuste_dias"] == 3
    filas = _asignaciones()
    dani = [f for f in filas if f["usuario_id"] == ids["dani"]]
    assert dani and all(f["estado"] == "provisorio" for f in dani)   # no toca lo confirmado


def test_editar_dia_a_mano_y_dia_extra(app_turnos):
    client, ids = app_turnos
    _planificar(client, fines=3)
    from db import get_db_connection
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM turnos_dias WHERE fecha = '2026-10-24'")
    dia_id = cur.fetchone()["id"]; conn.close()
    # Exceso de trabajo: se sube a 3 personas dejando marcados a los que ya iban
    actuales = [str(f["usuario_id"]) for f in _asignaciones() if f["fecha"] == "2026-10-24"]
    r = client.post(f"/turnos/dia/{dia_id}/editar", data={
        "accion": "guardar", "personas": "3", "estado": "provisorio", "nota": "Exceso de trabajo",
        "asignados": actuales})
    assert r.status_code == 302
    dia = [f for f in _asignaciones() if f["fecha"] == "2026-10-24"]
    assert len(dia) == 3 and dia[0]["personas"] == 3          # el sistema completo la tercera plaza
    assert sum(1 for f in dia if f["origen"] == "manual") == 2

    # Desmarcar a alguien: no vuelve a ser asignado ese dia
    quedan = [str(f["usuario_id"]) for f in dia][:2]
    sacado = [f["usuario_id"] for f in dia][2]
    client.post(f"/turnos/dia/{dia_id}/editar", data={
        "accion": "guardar", "personas": "3", "estado": "provisorio", "asignados": quedan})
    dia = [f for f in _asignaciones() if f["fecha"] == "2026-10-24"]
    assert sacado not in {f["usuario_id"] for f in dia} and len(dia) == 2

    # "Automatico" libera el dia y vuelve a completarlo
    client.post(f"/turnos/dia/{dia_id}/editar", data={"accion": "automatico"})
    dia = [f for f in _asignaciones() if f["fecha"] == "2026-10-24"]
    assert len(dia) == 3 and all(f["origen"] == "auto" for f in dia)

    # Dia suelto (feriado)
    r = client.post("/turnos/dia", data={"fecha": "2026-10-12", "personas": "1", "nota": "Feriado"})
    assert r.status_code == 302
    assert len([f for f in _asignaciones() if f["fecha"] == "2026-10-12"]) == 1

    # Quitar un fin de semana de la planificacion
    _planificar(client, fines=2)
    assert not [f for f in _asignaciones() if f["fecha"] == "2026-10-24"]


def test_tecnico_no_puede_entrar_a_planificacion(app_turnos):
    client, ids = app_turnos
    client.get("/logout")
    client.post("/login", data={"username": "ana", "password": "Clave123!"})
    r = client.get("/turnos/planificacion")
    assert r.status_code == 302
    assert client.get("/turnos").status_code == 200


# ── Correo de solicitud de ingreso ────────────────────────────────
import turnos_correo as tc


def test_frase_de_fechas_como_el_correo_original():
    assert tc.frase_fechas([date(2026, 10, 3)]) == "sábado 03 de Octubre del 2026"
    assert tc.frase_fechas([date(2026, 10, 10), date(2026, 10, 11)]) == "sábado 10 y domingo 11 de Octubre del 2026"
    assert tc.frase_fechas([date(2026, 10, 31), date(2026, 11, 1)]) == \
        "sábado 31 de Octubre y domingo 01 de Noviembre del 2026"
    assert tc.frase_fechas([date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)]) == \
        "sábado 10, domingo 11 y lunes 12 de Octubre del 2026"


def _grupo_demo():
    def a(aid, uid, nombre, linea=None, habitual=None, tarea=None):
        return {"id": aid, "usuario_id": uid, "nombre": nombre, "linea": linea,
                "linea_habitual": habitual, "tarea": tarea}
    sab = {"fecha_obj": date(2026, 10, 10), "asignados": [a(1, 1, "Willy Barrios", habitual="Americana"),
                                                          a(2, 2, "Enrique Villegas", habitual="Europea")]}
    dom = {"fecha_obj": date(2026, 10, 11), "asignados": [a(3, 2, "Enrique Villegas", habitual="Europea"),
                                                          a(4, 3, "Juan Pérez", linea="Termopanel")]}
    return [sab, dom]


def test_redaccion_del_correo():
    from datetime import datetime
    cfg = dict(tc.CONFIG_DEFECTO)
    red = tc.redactar(_grupo_demo(), cfg, momento=datetime(2026, 10, 9, 9, 0))
    t = red["texto"]
    assert t.startswith("Buenos días,")
    assert "solicito gestionar el acceso del personal de mantenimiento para los siguientes días: " \
           "sábado 10 y domingo 11 de Octubre del 2026, de acuerdo con la siguiente planificación:" in t
    assert "• Willy Barrios – Sábado 10.\nEstará a cargo de hacer mantenimiento correctivo de la línea Americana." in t
    assert "• Enrique Villegas – Sábado 10 y Domingo 11.\nEstará a cargo de hacer mantenimiento correctivo de la línea Europea." in t
    assert "• Juan Pérez – Domingo 11.\nEstará a cargo de hacer mantenimiento correctivo de la línea Termopanel." in t
    assert t.rstrip().endswith("Saludos cordiales,")
    assert "<b>Willy Barrios</b>" in red["html"] and "<b>sábado 10 y domingo 11 de Octubre del 2026</b>" in red["html"]
    assert red["para"] == ["lgomez@wintec.cl"] and red["cc"] == ["evidal@wintec.cl", "planta@wintec.cl"]
    assert red["asunto"] == "Solicitud de ingreso personal de mantenimiento – 10/10 y 11/10"
    url = tc.url_outlook(red)
    assert url.startswith("https://outlook.office.com/mail/deeplink/compose?to=lgomez@wintec.cl&cc=evidal@wintec.cl,planta@wintec.cl&subject=")
    assert "%0A" in url and " " not in url


def test_linea_distinta_por_dia_y_sin_linea():
    grupo = _grupo_demo()
    grupo[1]["asignados"][0]["linea"] = "Corte de cristales"   # Enrique cambia el domingo
    grupo[0]["asignados"][0]["linea"] = ""                       # Willy sin linea ese dia
    red = tc.redactar(grupo, dict(tc.CONFIG_DEFECTO))
    assert "• Willy Barrios – Sábado 10.\nEstará a cargo de hacer mantenimiento correctivo." in red["texto"]
    assert "Sábado 10: estará a cargo de hacer mantenimiento correctivo de la línea Europea." in red["texto"]
    assert "Domingo 11: estará a cargo de hacer mantenimiento correctivo de la línea Corte de cristales." in red["texto"]


def test_flujo_correo_enviado_y_desactualizado(app_turnos):
    client, ids = app_turnos
    _planificar(client, fines=2)
    html = client.get("/turnos/correo?clave=2026-10-10").get_data(as_text=True)
    assert "Abrir en Outlook" in html and "Por enviar" in html

    # Linea habitual de ana y linea especial de un dia
    from db import get_db_connection
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(f"SELECT id FROM turnos_tecnicos WHERE usuario_id = {ids['ana']}")
    tid = cur.fetchone()["id"]
    cur.execute("""SELECT a.id, a.usuario_id FROM turnos_asignaciones a JOIN turnos_dias d ON d.id = a.dia_id
                   WHERE d.fecha = '2026-10-10'""")
    asig = [dict(r) for r in cur.fetchall()]; conn.close()
    client.post(f"/turnos/tecnicos/{tid}/actualizar", data={"accion": "linea", "linea": "Americana"})
    client.post("/turnos/correo/2026-10-10/lineas", data={
        f"linea_{asig[0]['id']}": "Termopanel", f"tarea_{asig[0]['id']}": "hacer mantenimiento correctivo"})
    html = client.get("/turnos/correo?clave=2026-10-10").get_data(as_text=True)
    assert "línea Termopanel" in html

    # Recordatorio del viernes: hay un correo por enviar
    import turnos
    assert turnos.enviar_recordatorio_correo() >= 1

    client.post("/turnos/correo/2026-10-10/enviado", data={})
    html = client.get("/turnos/correo?clave=2026-10-10").get_data(as_text=True)
    assert "Enviado el" in html

    # Cambia el personal despues de enviado -> queda desactualizado y avisa
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM turnos_dias WHERE fecha = '2026-10-11'")
    dia_id = cur.fetchone()["id"]; conn.close()
    client.post(f"/turnos/dia/{dia_id}/editar", data={"accion": "guardar", "personas": "2", "estado": "confirmado",
                                                      "asignados": [str(ids["ana"]), str(ids["beto"])]})
    html = client.get("/turnos/correo?clave=2026-10-10").get_data(as_text=True)
    if "Cambió, reenviar" not in html:
        # si la seleccion coincidia con la anterior, forzamos otra distinta
        client.post(f"/turnos/dia/{dia_id}/editar", data={"accion": "guardar", "personas": "2", "estado": "confirmado",
                                                          "asignados": [str(ids["beto"]), str(ids["carla"])]})
        html = client.get("/turnos/correo?clave=2026-10-10").get_data(as_text=True)
    assert "Cambió, reenviar" in html
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT mensaje FROM notificaciones WHERE mensaje LIKE '%ya se había enviado%'")
    assert cur.fetchall()
    conn.close()

    # Pantallas generales siguen funcionando
    for ruta in ("/", "/turnos", "/turnos/tecnicos", "/manifest.json"):
        assert client.get(ruta).status_code == 200, ruta
