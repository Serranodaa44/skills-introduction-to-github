"""Importa empleados, productividad y respuestas desde el archivo Excel original.

Uso:
    pip install openpyxl
    python importar_excel.py EVALUACION_OPERADORES_PEG_Tabuladores_RH.xlsm --periodo 2019-S1

Lee la hoja "Calculo de P%":
  * Columna A (Nombre Operador) + G (máquina) + B:D (producción) + K:L (antigüedad, sueldo) -> Pegado / Operador
  * Columna M (Nombre Ayudante) -> Pegado / Ayudante
Y la hoja "DB Respuestas" (si tiene filas) como evaluaciones del usuario "formulario".
Las macros del archivo no se ejecutan; sólo se leen valores.
"""
from __future__ import annotations

import argparse
import secrets

import openpyxl
from werkzeug.security import generate_password_hash

import db


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def obtener_empleado(con, nombre, area, puesto, maquina=None, antiguedad=None, sueldo=None):
    pu = con.execute("SELECT id FROM puestos WHERE area = ? AND puesto = ?", (area, puesto)).fetchone()
    fila = con.execute("SELECT id FROM empleados WHERE nombre = ?", (nombre,)).fetchone()
    if fila:
        con.execute(
            "UPDATE empleados SET maquina = COALESCE(?, maquina), antiguedad = COALESCE(?, antiguedad),"
            " sueldo_actual = COALESCE(?, sueldo_actual) WHERE id = ?",
            (maquina, antiguedad, sueldo, fila["id"]),
        )
        return fila["id"], False
    cur = con.execute(
        "INSERT INTO empleados (nombre, puesto_id, maquina, antiguedad, sueldo_actual) VALUES (?, ?, ?, ?, ?)",
        (nombre, pu["id"], maquina, antiguedad, sueldo),
    )
    return cur.lastrowid, True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivo")
    ap.add_argument("--periodo", required=True, help="Periodo al que se asignan productividad y respuestas")
    ap.add_argument("--db", help="Ruta de la base de datos (por defecto evaluacion.db)")
    args = ap.parse_args()

    con = db.conectar(args.db)
    db.inicializar(con)
    wb = openpyxl.load_workbook(args.archivo, data_only=True, read_only=True)
    hoja = wb["Calculo de P%"]

    nuevos = prod = 0
    maquinas = {r["clave"] for r in con.execute("SELECT clave FROM maquinas")}
    for fila in hoja.iter_rows(min_row=4, max_col=13, values_only=True):
        nombre, total, hr_prod, hr_turno = fila[0], num(fila[1]), num(fila[2]), num(fila[3])
        maquina = str(fila[6]).strip() if fila[6] else None
        if maquina and maquina not in maquinas:
            con.execute("INSERT INTO maquinas (clave) VALUES (?)", (maquina,))
            maquinas.add(maquina)
        if nombre and str(nombre).strip():
            eid, nuevo = obtener_empleado(con, str(nombre).strip(), "Pegado", "Operador", maquina,
                                          num(fila[10]), num(fila[11]))
            nuevos += nuevo
            if total and hr_prod and hr_turno:
                con.execute(
                    "INSERT INTO productividad (empleado_id, periodo, produccion_total, hr_prod, hr_turno)"
                    " VALUES (?, ?, ?, ?, ?) ON CONFLICT(empleado_id, periodo) DO UPDATE SET"
                    " produccion_total = excluded.produccion_total, hr_prod = excluded.hr_prod,"
                    " hr_turno = excluded.hr_turno",
                    (eid, args.periodo, total, hr_prod, hr_turno),
                )
                prod += 1
        ayudante = fila[12]
        if ayudante and str(ayudante).strip():
            nuevos += obtener_empleado(con, str(ayudante).strip(), "Pegado", "Ayudante")[1]

    respuestas = 0
    if "DB Respuestas" in wb.sheetnames:
        filas = list(wb["DB Respuestas"].iter_rows(values_only=True))
        if len(filas) > 1:
            encabezados = [str(h).strip() if h else "" for h in filas[0]]
            u = con.execute("SELECT id FROM usuarios WHERE usuario = 'formulario'").fetchone()
            if not u:
                con.execute(
                    "INSERT INTO usuarios (usuario, nombre, password_hash, rol, activo)"
                    " VALUES ('formulario', 'Formulario (importado)', ?, 'evaluador', 0)",
                    (generate_password_hash(secrets.token_hex(16)),),
                )
                u = con.execute("SELECT id FROM usuarios WHERE usuario = 'formulario'").fetchone()
            for fila in filas[1:]:
                if not fila[1]:
                    continue
                emp = con.execute("SELECT e.id, p.plantilla_id FROM empleados e JOIN puestos p ON p.id = e.puesto_id"
                                  " WHERE e.nombre = ?", (str(fila[1]).strip(),)).fetchone()
                if not emp:
                    continue
                subs = {}
                for cat in db.criterios_plantilla(con, emp["plantilla_id"]):
                    for s in cat["subcategorias"]:
                        subs[s["nombre"].lower().replace(" ", "")] = s["id"]
                # Cada fila del formulario original es una evaluación distinta.
                ev = con.execute(
                    "INSERT INTO evaluaciones (empleado_id, evaluador_id, periodo, fecha, comentarios, origen)"
                    " VALUES (?, ?, ?, ?, 'Importado de DB Respuestas', 'importado')",
                    (emp["id"], u["id"], args.periodo, str(fila[0] or "")),
                ).lastrowid
                for h, v in zip(encabezados[2:], fila[2:]):
                    sid = subs.get(h.lower().replace(" ", ""))
                    if sid and num(v) is not None:
                        con.execute("INSERT INTO respuestas VALUES (?, ?, ?)", (ev, sid, num(v)))
                respuestas += 1
    con.commit()
    print(f"Empleados nuevos: {nuevos} · Registros de productividad: {prod} · Respuestas importadas: {respuestas}")


if __name__ == "__main__":
    main()
