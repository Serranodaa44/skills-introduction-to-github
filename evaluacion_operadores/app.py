"""Aplicación web de Evaluación de Operadores y Tabuladores de RH.

Ejecutar:
    pip install -r requirements.txt
    python app.py            # crea la base de datos y abre http://localhost:5000
"""
from __future__ import annotations

import csv
import io
import os
import secrets
from datetime import date
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import calculos
import db

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)


# ------------------------------------------------------------------ utilidades

def con():
    if "con" not in g:
        g.con = db.conectar(app.config.get("DB_PATH"))
    return g.con


@app.before_request
def proteger_csrf():
    """Token anti-CSRF simple para todos los formularios POST."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST" and request.form.get("csrf") != session["csrf"]:
        abort(400, "Token de formulario inválido. Recarga la página.")


@app.teardown_appcontext
def cerrar(_exc):
    c = g.pop("con", None)
    if c is not None:
        c.close()


def usuario_actual():
    uid = session.get("uid")
    if not uid:
        return None
    return con().execute("SELECT * FROM usuarios WHERE id = ? AND activo = 1", (uid,)).fetchone()


def requiere_login(rol: str | None = None):
    def deco(fn):
        @wraps(fn)
        def envoltura(*a, **kw):
            u = usuario_actual()
            if u is None:
                return redirect(url_for("login", next=request.path))
            if rol and u["rol"] != rol:
                abort(403)
            g.usuario = u
            return fn(*a, **kw)
        return envoltura
    return deco


@app.context_processor
def contexto():
    u = usuario_actual()
    cfg = db.obtener_config(con())
    return {"usuario": u, "cfg": cfg, "csrf": session.get("csrf", "")}


def num(valor, defecto=None):
    if valor is None or str(valor).strip() == "":
        return defecto
    return float(str(valor).replace(",", "").replace("$", "").strip())


@app.template_filter("dinero")
def f_dinero(v):
    return "—" if v is None else f"${v:,.2f}"


@app.template_filter("n")
def f_num(v, dec=1):
    if v is None:
        return "—"
    return f"{v:,.{dec}f}"


@app.template_filter("pct")
def f_pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def periodo_seleccionado():
    cfg = db.obtener_config(con())
    return request.args.get("periodo") or cfg.get("periodo_actual") or default_periodo()


def default_periodo():
    hoy = date.today()
    return f"{hoy.year}-S{1 if hoy.month <= 6 else 2}"


def periodos_existentes():
    filas = con().execute(
        "SELECT periodo FROM evaluaciones UNION SELECT periodo FROM productividad ORDER BY 1 DESC"
    ).fetchall()
    lista = [f["periodo"] for f in filas]
    actual = db.obtener_config(con()).get("periodo_actual") or default_periodo()
    if actual not in lista:
        lista.insert(0, actual)
    return lista


# ------------------------------------------------------------------ cálculo

def bandas():
    cfg = db.obtener_config(con())
    grados = [(r["letra"], r["pct"]) for r in con().execute("SELECT * FROM grados")]
    return calculos.bandas_grado(float(cfg["puntos_max"]), grados)


def productividad_empleado(emp, periodo):
    fila = con().execute(
        "SELECT p.*, m.tiros_turno, m.horas_turno FROM productividad p"
        " LEFT JOIN maquinas m ON m.clave = COALESCE(p.maquina, ?)"
        " WHERE p.empleado_id = ? AND p.periodo = ?",
        (emp["maquina"], emp["id"], periodo),
    ).fetchone()
    if not fila:
        return None, None
    prod = calculos.Productividad(fila["produccion_total"], fila["hr_prod"], fila["hr_turno"])
    return fila, prod


def calcular_empleado(emp, periodo, bandas_cache=None):
    c = con()
    cfg = db.obtener_config(c)
    puesto = c.execute(
        "SELECT pu.*, pl.etiquetas, pl.grado_minimo, pl.grado_maximo FROM puestos pu JOIN plantillas pl ON pl.id = pu.plantilla_id"
        " WHERE pu.id = ?",
        (emp["puesto_id"],),
    ).fetchone()
    categorias = db.criterios_plantilla(c, puesto["plantilla_id"])
    promedios = {
        r["subcategoria_id"]: r["prom"]
        for r in c.execute(
            "SELECT r.subcategoria_id, AVG(r.valor) AS prom FROM respuestas r"
            " JOIN evaluaciones e ON e.id = r.evaluacion_id"
            " WHERE e.empleado_id = ? AND e.periodo = ? GROUP BY r.subcategoria_id",
            (emp["id"], periodo),
        )
    }
    n_eval = c.execute(
        "SELECT COUNT(*) FROM evaluaciones WHERE empleado_id = ? AND periodo = ?", (emp["id"], periodo)
    ).fetchone()[0]
    fila, prod = productividad_empleado(emp, periodo)
    pct = prod.porcentaje(fila["tiros_turno"], fila["horas_turno"]) if prod else None
    res = calculos.evaluar_empleado(
        categorias=categorias,
        escala=float(cfg["escala"]),
        escala_respuesta=float(cfg["escala_respuesta"]),
        etiquetas=puesto["etiquetas"].split(","),
        promedios=promedios,
        num_evaluaciones=n_eval,
        productividad_pct=pct,
        bandas=bandas_cache or bandas(),
        grado_minimo=puesto["grado_minimo"],
        grado_maximo=puesto["grado_maximo"],
        sueldo_min=puesto["sueldo_min"],
        sueldo_max=puesto["sueldo_max"],
        pts_base=puesto["pts_base"],
        pts_tope=puesto["pts_tope"],
    )
    return res, puesto


def empleados_con_puesto(solo_activos=True):
    filtro = "WHERE e.activo = 1" if solo_activos else ""
    return con().execute(
        "SELECT e.*, p.area, p.puesto FROM empleados e JOIN puestos p ON p.id = e.puesto_id "
        f"{filtro} ORDER BY p.area, p.puesto, e.nombre"
    ).fetchall()


# ------------------------------------------------------------------ sesión

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = con().execute(
            "SELECT * FROM usuarios WHERE usuario = ? AND activo = 1", (request.form["usuario"].strip(),)
        ).fetchone()
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session.clear()
            session["uid"] = u["id"]
            destino = request.args.get("next") or ""
            if not destino.startswith("/") or destino.startswith("//"):
                destino = url_for("inicio")
            return redirect(destino)
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/cuenta", methods=["GET", "POST"])
@requiere_login()
def cuenta():
    if request.method == "POST":
        if not check_password_hash(g.usuario["password_hash"], request.form["actual"]):
            flash("La contraseña actual no es correcta.", "error")
        elif len(request.form["nueva"]) < 6:
            flash("La nueva contraseña debe tener al menos 6 caracteres.", "error")
        else:
            con().execute(
                "UPDATE usuarios SET password_hash = ? WHERE id = ?",
                (generate_password_hash(request.form["nueva"]), g.usuario["id"]),
            )
            con().commit()
            flash("Contraseña actualizada.", "ok")
            return redirect(url_for("inicio"))
    return render_template("cuenta.html")


# ------------------------------------------------------------------ inicio / resultados

@app.route("/")
@requiere_login()
def inicio():
    if g.usuario["rol"] != "admin":
        return redirect(url_for("evaluar"))
    return redirect(url_for("resultados"))


@app.route("/resultados")
@requiere_login("admin")
def resultados():
    periodo = periodo_seleccionado()
    area = request.args.get("area", "")
    b = bandas()
    filas = []
    for emp in empleados_con_puesto():
        if area and emp["area"] != area:
            continue
        res, puesto = calcular_empleado(emp, periodo, b)
        dif = None
        if res.sueldo_sugerido is not None and emp["sueldo_actual"]:
            dif = res.sueldo_sugerido - emp["sueldo_actual"]
        filas.append({"emp": emp, "res": res, "puesto": puesto, "dif": dif})
    areas = [r["area"] for r in con().execute("SELECT DISTINCT area FROM puestos ORDER BY area")]
    return render_template(
        "resultados.html", filas=filas, periodo=periodo, periodos=periodos_existentes(),
        areas=areas, area=area, bandas=b,
    )


@app.route("/empleado/<int:eid>")
@requiere_login("admin")
def detalle_empleado(eid):
    periodo = periodo_seleccionado()
    emp = con().execute(
        "SELECT e.*, p.area, p.puesto FROM empleados e JOIN puestos p ON p.id = e.puesto_id WHERE e.id = ?", (eid,)
    ).fetchone() or abort(404)
    res, puesto = calcular_empleado(emp, periodo)
    evals = con().execute(
        "SELECT ev.*, u.nombre AS evaluador FROM evaluaciones ev JOIN usuarios u ON u.id = ev.evaluador_id"
        " WHERE ev.empleado_id = ? AND ev.periodo = ? ORDER BY ev.fecha",
        (eid, periodo),
    ).fetchall()
    resp = {}
    for r in con().execute(
        "SELECT r.* FROM respuestas r JOIN evaluaciones e ON e.id = r.evaluacion_id"
        " WHERE e.empleado_id = ? AND e.periodo = ?", (eid, periodo)
    ):
        resp.setdefault(r["evaluacion_id"], {})[r["subcategoria_id"]] = r["valor"]
    fila_prod, prod = productividad_empleado(emp, periodo)
    return render_template(
        "empleado.html", emp=emp, res=res, puesto=puesto, periodo=periodo, periodos=periodos_existentes(),
        evals=evals, resp=resp, prod=prod, fila_prod=fila_prod, bandas=bandas(),
    )


# ------------------------------------------------------------------ evaluar (todos los usuarios)

@app.route("/evaluar", methods=["GET", "POST"])
@requiere_login()
def evaluar():
    c = con()
    cfg = db.obtener_config(c)
    periodo = request.values.get("periodo") or cfg.get("periodo_actual") or default_periodo()
    eid = request.values.get("empleado_id", type=int)
    emp = None
    categorias = []
    previas = {}
    comentarios = ""
    if eid:
        emp = c.execute(
            "SELECT e.*, p.area, p.puesto, p.plantilla_id FROM empleados e JOIN puestos p ON p.id = e.puesto_id"
            " WHERE e.id = ? AND e.activo = 1", (eid,)
        ).fetchone() or abort(404)
        categorias = db.criterios_plantilla(c, emp["plantilla_id"])
        ev = c.execute(
            "SELECT * FROM evaluaciones WHERE empleado_id = ? AND evaluador_id = ? AND periodo = ?"
            " AND origen = 'app'", (eid, g.usuario["id"], periodo),
        ).fetchone()
        if ev:
            comentarios = ev["comentarios"]
            previas = {r["subcategoria_id"]: r["valor"] for r in c.execute(
                "SELECT * FROM respuestas WHERE evaluacion_id = ?", (ev["id"],))}

    if request.method == "POST" and emp:
        escala = int(float(cfg["escala_respuesta"]))
        valores = {}
        for cat in categorias:
            for sub in cat["subcategorias"]:
                v = request.form.get(f"sub_{sub['id']}")
                if v in (None, ""):
                    continue
                v = float(v)
                if not 1 <= v <= escala:
                    abort(400)
                valores[sub["id"]] = v
        if not valores:
            flash("Captura al menos una calificación.", "error")
        else:
            c.execute(
                "INSERT INTO evaluaciones (empleado_id, evaluador_id, periodo, comentarios) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(empleado_id, evaluador_id, periodo) WHERE origen = 'app' DO UPDATE SET"
                " comentarios = excluded.comentarios, fecha = datetime('now', 'localtime')",
                (emp["id"], g.usuario["id"], periodo, request.form.get("comentarios", "").strip()),
            )
            ev_id = c.execute(
                "SELECT id FROM evaluaciones WHERE empleado_id = ? AND evaluador_id = ? AND periodo = ?"
                " AND origen = 'app'", (emp["id"], g.usuario["id"], periodo),
            ).fetchone()["id"]
            c.execute("DELETE FROM respuestas WHERE evaluacion_id = ?", (ev_id,))
            c.executemany(
                "INSERT INTO respuestas (evaluacion_id, subcategoria_id, valor) VALUES (?, ?, ?)",
                [(ev_id, k, v) for k, v in valores.items()],
            )
            c.commit()
            flash(f"Evaluación de {emp['nombre']} guardada.", "ok")
            return redirect(url_for("evaluar", periodo=periodo))

    hechas = {r["empleado_id"] for r in c.execute(
        "SELECT empleado_id FROM evaluaciones WHERE evaluador_id = ? AND periodo = ?", (g.usuario["id"], periodo))}
    return render_template(
        "evaluar.html", empleados=empleados_con_puesto(), emp=emp, categorias=categorias, periodo=periodo,
        previas=previas, comentarios=comentarios, hechas=hechas, escala=int(float(cfg["escala_respuesta"])),
    )


@app.route("/evaluaciones/<int:ev_id>/borrar", methods=["POST"])
@requiere_login("admin")
def borrar_evaluacion(ev_id):
    ev = con().execute("SELECT * FROM evaluaciones WHERE id = ?", (ev_id,)).fetchone() or abort(404)
    con().execute("DELETE FROM evaluaciones WHERE id = ?", (ev_id,))
    con().commit()
    flash("Evaluación eliminada.", "ok")
    return redirect(url_for("detalle_empleado", eid=ev["empleado_id"], periodo=ev["periodo"]))


# ------------------------------------------------------------------ empleados

@app.route("/empleados", methods=["GET", "POST"])
@requiere_login("admin")
def empleados():
    c = con()
    if request.method == "POST":
        datos = (
            request.form["nombre"].strip(), request.form.get("puesto_id", type=int),
            request.form.get("maquina") or None, num(request.form.get("antiguedad")),
            num(request.form.get("sueldo_actual")), 1 if request.form.get("activo") else 0,
        )
        if not datos[0] or not datos[1]:
            flash("Nombre y puesto son obligatorios.", "error")
        elif request.form.get("id"):
            c.execute(
                "UPDATE empleados SET nombre=?, puesto_id=?, maquina=?, antiguedad=?, sueldo_actual=?, activo=?"
                " WHERE id=?", (*datos, int(request.form["id"])),
            )
            flash("Empleado actualizado.", "ok")
        else:
            c.execute(
                "INSERT INTO empleados (nombre, puesto_id, maquina, antiguedad, sueldo_actual, activo)"
                " VALUES (?, ?, ?, ?, ?, ?)", datos,
            )
            flash("Empleado agregado.", "ok")
        c.commit()
        return redirect(url_for("empleados"))
    editar = None
    if request.args.get("editar"):
        editar = c.execute("SELECT * FROM empleados WHERE id = ?", (request.args["editar"],)).fetchone()
    return render_template(
        "empleados.html", empleados=empleados_con_puesto(solo_activos=False), editar=editar,
        puestos=c.execute("SELECT * FROM puestos ORDER BY area, puesto").fetchall(),
        maquinas=c.execute("SELECT * FROM maquinas ORDER BY clave").fetchall(),
    )


# ------------------------------------------------------------------ productividad

@app.route("/productividad", methods=["GET", "POST"])
@requiere_login("admin")
def productividad():
    c = con()
    periodo = request.values.get("periodo") or periodo_seleccionado()
    if request.method == "POST":
        try:
            c.execute(
                "INSERT INTO productividad (empleado_id, periodo, produccion_total, hr_prod, hr_turno, maquina)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(empleado_id, periodo) DO UPDATE SET"
                " produccion_total = excluded.produccion_total, hr_prod = excluded.hr_prod,"
                " hr_turno = excluded.hr_turno, maquina = excluded.maquina",
                (
                    request.form.get("empleado_id", type=int), periodo, num(request.form["produccion_total"]),
                    num(request.form["hr_prod"]), num(request.form["hr_turno"]), request.form.get("maquina") or None,
                ),
            )
            c.commit()
            flash("Productividad guardada.", "ok")
        except (TypeError, ValueError):
            flash("Revisa los valores numéricos.", "error")
        return redirect(url_for("productividad", periodo=periodo))
    filas = []
    for r in c.execute(
        "SELECT p.*, e.nombre, e.maquina AS maquina_emp, pu.area, pu.puesto FROM productividad p"
        " JOIN empleados e ON e.id = p.empleado_id JOIN puestos pu ON pu.id = e.puesto_id"
        " WHERE p.periodo = ? ORDER BY p.produccion_total DESC", (periodo,)
    ).fetchall():
        clave = r["maquina"] or r["maquina_emp"]
        maq = c.execute("SELECT * FROM maquinas WHERE clave = ?", (clave,)).fetchone()
        prod = calculos.Productividad(r["produccion_total"], r["hr_prod"], r["hr_turno"])
        pct = prod.porcentaje(maq["tiros_turno"], maq["horas_turno"]) if maq else None
        filas.append({"r": r, "prod": prod, "pct": pct, "maquina": clave, "maq": maq})
    return render_template(
        "productividad.html", filas=filas, periodo=periodo, periodos=periodos_existentes(),
        empleados=empleados_con_puesto(), maquinas=c.execute("SELECT * FROM maquinas ORDER BY clave").fetchall(),
    )


@app.route("/productividad/<int:pid>/borrar", methods=["POST"])
@requiere_login("admin")
def borrar_productividad(pid):
    fila = con().execute("SELECT periodo FROM productividad WHERE id = ?", (pid,)).fetchone() or abort(404)
    con().execute("DELETE FROM productividad WHERE id = ?", (pid,))
    con().commit()
    return redirect(url_for("productividad", periodo=fila["periodo"]))


# ------------------------------------------------------------------ configuración

@app.route("/config", methods=["GET", "POST"])
@requiere_login("admin")
def configuracion():
    c = con()
    if request.method == "POST":
        seccion = request.form["seccion"]
        try:
            if seccion == "general":
                for clave in ("empresa", "escala", "escala_respuesta", "puntos_max", "periodo_actual"):
                    db.guardar_config(c, clave, request.form[clave].strip())
                for letra in [r["letra"] for r in c.execute("SELECT letra FROM grados")]:
                    c.execute("UPDATE grados SET pct = ? WHERE letra = ?",
                              (num(request.form[f"grado_{letra}"]) / 100, letra))
            elif seccion == "puestos":
                for p in c.execute("SELECT id FROM puestos").fetchall():
                    pid = p["id"]
                    if request.form.get(f"borrar_{pid}"):
                        if c.execute("SELECT 1 FROM empleados WHERE puesto_id = ?", (pid,)).fetchone():
                            flash("No se puede borrar un puesto con empleados asignados.", "error")
                        else:
                            c.execute("DELETE FROM puestos WHERE id = ?", (pid,))
                        continue
                    c.execute(
                        "UPDATE puestos SET area=?, puesto=?, sueldo_min=?, sueldo_max=?, pts_base=?, pts_tope=?,"
                        " plantilla_id=? WHERE id=?",
                        (request.form[f"area_{pid}"].strip(), request.form[f"puesto_{pid}"].strip(),
                         num(request.form[f"min_{pid}"]), num(request.form[f"max_{pid}"]),
                         num(request.form[f"base_{pid}"]), num(request.form[f"tope_{pid}"]),
                         int(request.form[f"plantilla_{pid}"]), pid),
                    )
                if request.form.get("nuevo_area", "").strip():
                    sig = (c.execute("SELECT MAX(codigo) FROM puestos").fetchone()[0] or 999) + 1
                    c.execute(
                        "INSERT INTO puestos (codigo, area, puesto, sueldo_min, sueldo_max, pts_base, pts_tope,"
                        " plantilla_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (sig, request.form["nuevo_area"].strip(), request.form["nuevo_puesto"].strip(),
                         num(request.form["nuevo_min"], 0), num(request.form["nuevo_max"], 0),
                         num(request.form["nuevo_base"], 150), num(request.form["nuevo_tope"], 300),
                         int(request.form["nuevo_plantilla"])),
                    )
            elif seccion == "maquinas":
                for m in c.execute("SELECT clave FROM maquinas").fetchall():
                    k = m["clave"]
                    if request.form.get(f"borrar_{k}"):
                        c.execute("UPDATE empleados SET maquina = NULL WHERE maquina = ?", (k,))
                        c.execute("UPDATE productividad SET maquina = NULL WHERE maquina = ?", (k,))
                        c.execute("DELETE FROM maquinas WHERE clave = ?", (k,))
                        continue
                    c.execute(
                        "UPDATE maquinas SET descripcion=?, tiros_turno=?, horas_turno=? WHERE clave=?",
                        (request.form[f"desc_{k}"].strip(), num(request.form[f"tiros_{k}"]),
                         num(request.form[f"horas_{k}"], 12), k),
                    )
                nueva = request.form.get("nueva_clave", "").strip()
                if nueva:
                    c.execute(
                        "INSERT INTO maquinas (clave, descripcion, tiros_turno, horas_turno) VALUES (?, ?, ?, ?)",
                        (nueva, request.form.get("nueva_desc", "").strip(), num(request.form.get("nueva_tiros")),
                         num(request.form.get("nueva_horas"), 12)),
                    )
            c.commit()
            flash("Configuración guardada.", "ok")
        except (TypeError, ValueError, KeyError):
            c.rollback()
            flash("Revisa los valores capturados.", "error")
        return redirect(url_for("configuracion") + "#" + seccion)

    plantillas = c.execute("SELECT * FROM plantillas ORDER BY id").fetchall()
    return render_template(
        "config.html",
        grados=c.execute("SELECT * FROM grados ORDER BY pct DESC").fetchall(),
        puestos=c.execute(
            "SELECT pu.*, pl.nombre AS plantilla FROM puestos pu JOIN plantillas pl ON pl.id = pu.plantilla_id"
            " ORDER BY pu.codigo").fetchall(),
        maquinas=c.execute("SELECT * FROM maquinas ORDER BY clave").fetchall(),
        plantillas=plantillas, bandas=bandas(),
        usuarios=c.execute("SELECT * FROM usuarios ORDER BY rol, nombre").fetchall(),
    )


@app.route("/plantillas/<int:pid>", methods=["GET", "POST"])
@requiere_login("admin")
def plantilla(pid):
    c = con()
    pl = c.execute("SELECT * FROM plantillas WHERE id = ?", (pid,)).fetchone() or abort(404)
    if request.method == "POST":
        accion = request.form.get("accion", "guardar")
        try:
            if accion == "duplicar":
                datos = {
                    "nombre": request.form["nuevo_nombre"].strip() or f"{pl['nombre']} (copia)",
                    "etiquetas": pl["etiquetas"], "grado_minimo": pl["grado_minimo"],
                    "grado_maximo": pl["grado_maximo"],
                    "categorias": [
                        (cat["clave"], cat["nombre"], cat["peso"],
                         [(s["nombre"], s["descripcion"], s["peso"], s["fuente"]) for s in cat["subcategorias"]])
                        for cat in db.criterios_plantilla(c, pid)
                    ],
                }
                nuevo = db.crear_plantilla(c, datos)
                c.commit()
                flash("Plantilla duplicada. Asígnala a un puesto en Configuración > Tabulador.", "ok")
                return redirect(url_for("plantilla", pid=nuevo))
            etiquetas = [e.strip() for e in request.form["etiquetas"].split(",") if e.strip()]
            if len(etiquetas) != calculos.NIVELES_POR_SUBCATEGORIA:
                raise ValueError("Se requieren 3 etiquetas")
            c.execute(
                "UPDATE plantillas SET nombre=?, etiquetas=?, grado_minimo=?, grado_maximo=? WHERE id=?",
                (request.form["nombre"].strip(), ",".join(etiquetas), request.form.get("grado_minimo") or None,
                 request.form.get("grado_maximo") or None, pid),
            )
            for cat in db.criterios_plantilla(c, pid):
                if request.form.get(f"cborrar_{cat['id']}"):
                    c.execute("DELETE FROM categorias WHERE id = ?", (cat["id"],))
                    continue
                c.execute("UPDATE categorias SET nombre=?, peso=? WHERE id=?",
                          (request.form[f"cnombre_{cat['id']}"].strip(), num(request.form[f"cpeso_{cat['id']}"]) / 100,
                           cat["id"]))
                for s in cat["subcategorias"]:
                    if request.form.get(f"sborrar_{s['id']}"):
                        c.execute("DELETE FROM subcategorias WHERE id = ?", (s["id"],))
                        continue
                    c.execute(
                        "UPDATE subcategorias SET nombre=?, descripcion=?, peso=?, fuente=? WHERE id=?",
                        (request.form[f"snombre_{s['id']}"].strip(), request.form[f"sdesc_{s['id']}"].strip(),
                         num(request.form[f"speso_{s['id']}"]) / 100, request.form[f"sfuente_{s['id']}"], s["id"]),
                    )
                nueva = request.form.get(f"nueva_sub_{cat['id']}", "").strip()
                if nueva:
                    c.execute(
                        "INSERT INTO subcategorias (categoria_id, nombre, peso, fuente, orden) VALUES (?, ?, ?, 'evaluacion', 99)",
                        (cat["id"], nueva, num(request.form.get(f"nueva_speso_{cat['id']}"), 0) / 100),
                    )
            if request.form.get("nueva_cat", "").strip():
                n = c.execute("SELECT COUNT(*) FROM categorias WHERE plantilla_id = ?", (pid,)).fetchone()[0]
                c.execute(
                    "INSERT INTO categorias (plantilla_id, clave, nombre, peso, orden) VALUES (?, ?, ?, ?, ?)",
                    (pid, chr(ord("A") + n), request.form["nueva_cat"].strip(),
                     num(request.form.get("nueva_cat_peso"), 0) / 100, n),
                )
            c.commit()
            flash("Plantilla guardada.", "ok")
        except (TypeError, ValueError, KeyError) as e:
            c.rollback()
            flash(f"Revisa los valores capturados ({e}).", "error")
        return redirect(url_for("plantilla", pid=pid))

    cfg = db.obtener_config(c)
    cats = db.criterios_plantilla(c, pid)
    escala = float(cfg["escala"])
    for cat in cats:
        cat["suma_sub"] = sum(s["peso"] for s in cat["subcategorias"])
        for s in cat["subcategorias"]:
            s["limites"] = calculos.puntos_nivel(escala, cat["peso"], s["peso"])
    return render_template(
        "plantilla.html", pl=pl, cats=cats, suma_cat=sum(x["peso"] for x in cats),
        total_pts=sum(s["limites"][2] for x in cats for s in x["subcategorias"]),
        letras=[r["letra"] for r in c.execute("SELECT letra FROM grados ORDER BY pct DESC")],
        puestos=c.execute("SELECT * FROM puestos WHERE plantilla_id = ?", (pid,)).fetchall(),
    )


@app.route("/usuarios", methods=["POST"])
@requiere_login("admin")
def usuarios():
    c = con()
    f = request.form
    if f.get("accion") == "crear":
        if not f["usuario"].strip() or len(f["password"]) < 6:
            flash("Usuario obligatorio y contraseña de al menos 6 caracteres.", "error")
        elif c.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (f["usuario"].strip(),)).fetchone():
            flash("Ese usuario ya existe.", "error")
        else:
            c.execute(
                "INSERT INTO usuarios (usuario, nombre, password_hash, rol) VALUES (?, ?, ?, ?)",
                (f["usuario"].strip(), f["nombre"].strip() or f["usuario"].strip(),
                 generate_password_hash(f["password"]), "admin" if f.get("rol") == "admin" else "evaluador"),
            )
            flash("Usuario creado.", "ok")
    elif f.get("accion") in ("activar", "desactivar", "reset"):
        uid = int(f["id"])
        if uid == g.usuario["id"] and f["accion"] == "desactivar":
            flash("No puedes desactivar tu propio usuario.", "error")
        elif f["accion"] == "reset":
            if len(f.get("password", "")) < 6:
                flash("La contraseña debe tener al menos 6 caracteres.", "error")
            else:
                c.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                          (generate_password_hash(f["password"]), uid))
                flash("Contraseña restablecida.", "ok")
        else:
            c.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (1 if f["accion"] == "activar" else 0, uid))
    c.commit()
    return redirect(url_for("configuracion") + "#usuarios")


# ------------------------------------------------------------------ exportación

def respuesta_csv(nombre, encabezados, filas):
    buf = io.StringIO()
    buf.write("﻿")  # BOM para que Excel abra bien los acentos
    w = csv.writer(buf)
    w.writerow(encabezados)
    w.writerows(filas)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.route("/exportar/resultados.csv")
@requiere_login("admin")
def exportar_resultados():
    periodo = periodo_seleccionado()
    b = bandas()
    filas = []
    for emp in empleados_con_puesto():
        res, _ = calcular_empleado(emp, periodo, b)
        subs = "; ".join(f"{s.nombre}={s.puntos:.1f}({s.grado})" for s in res.subcategorias if s.puntos is not None)
        filas.append([
            periodo, emp["nombre"], emp["area"], emp["puesto"], res.num_evaluaciones,
            "" if res.productividad_pct is None else round(res.productividad_pct * 100, 1),
            round(res.total, 1), res.grado,
            "" if res.sueldo_sugerido is None else round(res.sueldo_sugerido, 2),
            emp["sueldo_actual"] or "", emp["antiguedad"] or "", subs,
        ])
    return respuesta_csv(
        f"resultados_{periodo}.csv",
        ["Periodo", "Nombre", "Área", "Puesto", "Evaluaciones", "Productividad %", "Puntos", "Grado final",
         "Sueldo sugerido", "Sueldo actual", "Antigüedad (años)", "Detalle"],
        filas,
    )


@app.route("/exportar/respuestas.csv")
@requiere_login("admin")
def exportar_respuestas():
    periodo = periodo_seleccionado()
    filas = []
    for ev in con().execute(
        "SELECT ev.*, e.nombre AS empleado, u.nombre AS evaluador FROM evaluaciones ev"
        " JOIN empleados e ON e.id = ev.empleado_id JOIN usuarios u ON u.id = ev.evaluador_id"
        " WHERE ev.periodo = ? ORDER BY ev.fecha", (periodo,)
    ):
        for r in con().execute(
            "SELECT s.nombre, r.valor FROM respuestas r JOIN subcategorias s ON s.id = r.subcategoria_id"
            " WHERE r.evaluacion_id = ?", (ev["id"],)
        ):
            filas.append([ev["fecha"], periodo, ev["empleado"], ev["evaluador"], r["nombre"], r["valor"]])
    return respuesta_csv(
        f"respuestas_{periodo}.csv",
        ["Marca temporal", "Periodo", "Empleado", "Evaluador", "Criterio", "Calificación"], filas,
    )


# ------------------------------------------------------------------ arranque

def preparar_db(ruta=None):
    c = db.conectar(ruta)
    cargado = db.inicializar(c, admin_password=os.environ.get("ADMIN_PASSWORD", "admin"))
    if not os.environ.get("SECRET_KEY"):
        # Clave de sesión persistente para que los usuarios no pierdan la sesión al reiniciar.
        clave = db.obtener_config(c).get("secret_key")
        if not clave:
            clave = secrets.token_hex(32)
            db.guardar_config(c, "secret_key", clave)
            c.commit()
        app.config["SECRET_KEY"] = clave
    c.close()
    return cargado


if __name__ == "__main__":
    if preparar_db():
        print("Base de datos creada con la configuración del Excel.")
        print("Usuario inicial: admin / contraseña:", os.environ.get("ADMIN_PASSWORD", "admin"),
              "(cámbiala en 'Mi cuenta').")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)),
            debug=bool(os.environ.get("DEBUG")))
