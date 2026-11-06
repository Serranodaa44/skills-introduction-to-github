"""Pruebas: los cálculos reproducen los valores del Excel original y el flujo web funciona."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calculos  # noqa: E402
import db  # noqa: E402


# ------------------------------------------------------------------ cálculos vs Excel

def test_limites_subcategoria_como_hoja_pegado():
    # Pegado!H3:J3 -> 19.5 / 39 / 58.5 (Experiencia en el Proceso)
    assert calculos.puntos_nivel(100, 0.3, 0.65) == pytest.approx((19.5, 39, 58.5))
    # Pegado!H9:J9 -> 20 / 40 / 60 (Productividad)
    assert calculos.puntos_nivel(100, 0.4, 0.5) == pytest.approx((20, 40, 60))


def test_total_de_puntos_es_300():
    total = 0
    for _clave, _nombre, peso, subs in db.PLANTILLA_OPERADOR["categorias"]:
        total += sum(calculos.puntos_nivel(100, peso, s[2])[2] for s in subs)
    assert total == pytest.approx(300)


def test_bandas_de_grado_como_hoja_pegado():
    b = calculos.bandas_grado(300, db.GRADOS_INICIALES)
    # Pegado!B51:D56
    assert [(l, round(d), round(h)) for l, d, h in b] == [
        ("A", 270, 300), ("B", 210, 269), ("C", 150, 209), ("D", 120, 149), ("E", 75, 119), ("F", 0, 74)]


def test_grado_final():
    b = calculos.bandas_grado(300, db.GRADOS_INICIALES)
    assert calculos.grado_final(0, b, "C") == "-"
    assert calculos.grado_final(149, b, "C") == "C-"
    assert calculos.grado_final(150, b, "C") == "C"
    assert calculos.grado_final(269.5, b, "C") == "B"
    assert calculos.grado_final(280, b, "C") == "A"
    assert calculos.grado_final(100, b, None) == "E"
    # Ayudante: su grado más alto es D
    assert calculos.grado_final(216, b, None, "D") == "D"
    assert calculos.grado_final(100, b, None, "D") == "E"


def test_grado_subcategoria():
    lim = (19.5, 39, 58.5)
    et = ["C", "B", "A"]
    assert calculos.grado_subcategoria(0.5, lim, et) == "-"
    assert calculos.grado_subcategoria(19.5, lim, et) == "C"
    assert calculos.grado_subcategoria(30, lim, et) == "B"
    assert calculos.grado_subcategoria(50, lim, et) == "A"
    assert calculos.grado_subcategoria(60, lim, et) == "#$"


def test_costo_por_punto_como_tabulador():
    # Tabulador!J27 (Pegado Operador) = 4.6667 ; J23 (Troquelado Ayudante) = 5.3333
    assert calculos.costo_por_punto(2500, 3200, 150, 300) == pytest.approx(4.6666667)
    assert calculos.costo_por_punto(1400, 1800, 75, 150) == pytest.approx(5.3333333)


def test_sueldo_sugerido():
    assert calculos.sueldo_sugerido(225, 2500, 3200, 150, 300) == pytest.approx(2850)
    assert calculos.sueldo_sugerido(100, 2500, 3200, 150, 300) == pytest.approx(2500)  # debajo de la base
    assert calculos.sueldo_sugerido(0, 2500, 3200, 150, 300) is None


def test_productividad_como_calculo_de_p():
    # Calculo de P% fila 4: 9,104,668 / 1421 / 2253.6 -> desempeño 6407, neta 4040
    p = calculos.Productividad(9104668, 1421, 2253.6)
    assert round(p.desempeno) == 6407
    assert round(p.produccion_neta) == 4040
    # Máquina VB: 45000 tiros / 12 h = 3750 por hora
    assert p.porcentaje(45000, 12) == pytest.approx(4040.1 / 3750, rel=1e-3)
    assert p.porcentaje(None, 12) is None


def test_puntos_desde_formulario():
    # Evaluacion de form: (Fuente / Escala) * Promedio -> 58.5/5*4 = 46.8
    assert calculos.puntos_desde_respuestas(4, 5, 58.5) == pytest.approx(46.8)


# ------------------------------------------------------------------ flujo web

@pytest.fixture()
def cliente(tmp_path):
    import app as modulo
    ruta = str(tmp_path / "prueba.db")
    modulo.app.config.update(TESTING=True, DB_PATH=ruta)
    modulo.preparar_db(ruta)
    with modulo.app.test_client() as c:
        yield c


def token(cliente, url):
    html = cliente.get(url).get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def entrar(cliente, usuario, password):
    t = token(cliente, "/login")
    return cliente.post("/login", data={"usuario": usuario, "password": password, "csrf": t})


def test_flujo_completo(cliente):
    assert entrar(cliente, "admin", "admin").status_code == 302

    # Alta de empleado en Pegado / Operador con máquina VB (45000 tiros/turno)
    con = db.conectar(cliente.application.config["DB_PATH"])
    puesto = con.execute("SELECT id FROM puestos WHERE area='Pegado' AND puesto='Operador'").fetchone()["id"]
    t = token(cliente, "/empleados")
    cliente.post("/empleados", data={"csrf": t, "nombre": "Persona Prueba", "puesto_id": puesto,
                                     "maquina": "VB", "sueldo_actual": "2600", "activo": "1"})
    eid = con.execute("SELECT id FROM empleados").fetchone()["id"]

    # Crear un evaluador
    t = token(cliente, "/config")
    cliente.post("/usuarios", data={"csrf": t, "accion": "crear", "usuario": "supervisor",
                                    "nombre": "Supervisor", "password": "secreto1", "rol": "evaluador"})

    # Productividad: neta 3750 = 100% de la máquina VB
    t = token(cliente, "/productividad?periodo=P1")
    cliente.post("/productividad", data={"csrf": t, "periodo": "P1", "empleado_id": eid,
                                         "produccion_total": "45000", "hr_prod": "10", "hr_turno": "12"})

    # El evaluador califica todo con 5
    cliente.get("/logout")
    entrar(cliente, "supervisor", "secreto1")
    assert cliente.get("/resultados").status_code == 403
    pagina = cliente.get(f"/evaluar?empleado_id={eid}&periodo=P1").get_data(as_text=True)
    subs = set(re.findall(r'name="(sub_\d+)"', pagina))
    assert len(subs) == 11
    t = re.search(r'name="csrf" value="([^"]+)"', pagina).group(1)
    datos = {s: "5" for s in subs} | {"csrf": t, "empleado_id": eid, "periodo": "P1"}
    assert cliente.post("/evaluar", data=datos).status_code == 302
    # Volver a enviar actualiza (no duplica)
    cliente.post("/evaluar", data=datos)
    assert con.execute("SELECT COUNT(*) FROM evaluaciones").fetchone()[0] == 1

    # Resultados: 300 pts, grado A, sueldo máximo 3200
    cliente.get("/logout")
    entrar(cliente, "admin", "admin")
    html = cliente.get("/resultados?periodo=P1").get_data(as_text=True)
    assert "Persona Prueba" in html and "300.0" in html and "$3,200.00" in html
    detalle = cliente.get(f"/empleado/{eid}?periodo=P1").get_data(as_text=True)
    assert "productividad" in detalle and "100.0%" in detalle
    csv = cliente.get("/exportar/resultados.csv?periodo=P1").get_data(as_text=True)
    assert "Persona Prueba" in csv and ",A," in csv
    assert cliente.get("/config").status_code == 200


def test_post_sin_csrf_es_rechazado(cliente):
    r = cliente.post("/login", data={"usuario": "admin", "password": "admin"})
    assert r.status_code == 400


def test_plantilla_duplicar_y_editar(cliente):
    entrar(cliente, "admin", "admin")
    t = token(cliente, "/plantillas/1")
    r = cliente.post("/plantillas/1", data={"csrf": t, "accion": "duplicar", "nuevo_nombre": "Operador (Impresión)"})
    assert r.status_code == 302
    html = cliente.get(r.headers["Location"]).get_data(as_text=True)
    assert "Operador (Impresión)" in html and "300.0" in html
