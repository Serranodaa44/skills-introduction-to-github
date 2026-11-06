"""Base de datos SQLite: esquema y datos iniciales tomados del archivo Excel."""
from __future__ import annotations

import os
import sqlite3

from werkzeug.security import generate_password_hash

RUTA_DB = os.environ.get("EVAL_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluacion.db"))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY,
    usuario TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('admin', 'evaluador')),
    activo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS config (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- Escalones porcentuales (hoja Pegado, B59:D65)
CREATE TABLE IF NOT EXISTS grados (
    letra TEXT PRIMARY KEY,
    pct REAL NOT NULL
);

-- Plantillas de criterios (hoja Pegado: Operadores / Ayudantes)
CREATE TABLE IF NOT EXISTS plantillas (
    id INTEGER PRIMARY KEY,
    nombre TEXT UNIQUE NOT NULL,
    etiquetas TEXT NOT NULL,          -- niveles por subcategoría, p. ej. "C,B,A"
    grado_minimo TEXT,                -- grado más bajo del puesto, p. ej. "C" -> "C-" si queda debajo
    grado_maximo TEXT                 -- grado más alto posible, p. ej. Ayudante = "D"
);

CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY,
    plantilla_id INTEGER NOT NULL REFERENCES plantillas(id) ON DELETE CASCADE,
    clave TEXT NOT NULL,
    nombre TEXT NOT NULL,
    peso REAL NOT NULL,
    orden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subcategorias (
    id INTEGER PRIMARY KEY,
    categoria_id INTEGER NOT NULL REFERENCES categorias(id) ON DELETE CASCADE,
    nombre TEXT NOT NULL,
    descripcion TEXT NOT NULL DEFAULT '',
    peso REAL NOT NULL,
    fuente TEXT NOT NULL DEFAULT 'evaluacion' CHECK (fuente IN ('evaluacion', 'productividad')),
    orden INTEGER NOT NULL DEFAULT 0
);

-- Hoja Tabulador
CREATE TABLE IF NOT EXISTS puestos (
    id INTEGER PRIMARY KEY,
    codigo INTEGER,
    area TEXT NOT NULL,
    puesto TEXT NOT NULL,
    sueldo_min REAL NOT NULL,
    sueldo_max REAL NOT NULL,
    pts_base REAL NOT NULL,
    pts_tope REAL NOT NULL,
    plantilla_id INTEGER NOT NULL REFERENCES plantillas(id),
    UNIQUE (area, puesto)
);

-- Tabulador: Cve de máquina / Tiros requeridos / Horas turno
CREATE TABLE IF NOT EXISTS maquinas (
    clave TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL DEFAULT '',
    tiros_turno REAL,
    horas_turno REAL NOT NULL DEFAULT 12
);

CREATE TABLE IF NOT EXISTS empleados (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    puesto_id INTEGER NOT NULL REFERENCES puestos(id),
    maquina TEXT REFERENCES maquinas(clave),
    antiguedad REAL,
    sueldo_actual REAL,
    activo INTEGER NOT NULL DEFAULT 1
);

-- Hoja "Calculo de P%"
CREATE TABLE IF NOT EXISTS productividad (
    id INTEGER PRIMARY KEY,
    empleado_id INTEGER NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
    periodo TEXT NOT NULL,
    produccion_total REAL NOT NULL,
    hr_prod REAL NOT NULL,
    hr_turno REAL NOT NULL,
    maquina TEXT REFERENCES maquinas(clave),
    UNIQUE (empleado_id, periodo)
);

-- Hoja "DB Respuestas" (respuestas del formulario)
CREATE TABLE IF NOT EXISTS evaluaciones (
    id INTEGER PRIMARY KEY,
    empleado_id INTEGER NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
    evaluador_id INTEGER NOT NULL REFERENCES usuarios(id),
    periodo TEXT NOT NULL,
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    comentarios TEXT NOT NULL DEFAULT '',
    origen TEXT NOT NULL DEFAULT 'app'   -- 'app' o 'importado'
);

-- Cada usuario tiene una sola evaluación por empleado y periodo (las importadas pueden repetirse).
CREATE UNIQUE INDEX IF NOT EXISTS ux_evaluacion_usuario
    ON evaluaciones (empleado_id, evaluador_id, periodo) WHERE origen = 'app';

CREATE TABLE IF NOT EXISTS respuestas (
    evaluacion_id INTEGER NOT NULL REFERENCES evaluaciones(id) ON DELETE CASCADE,
    subcategoria_id INTEGER NOT NULL REFERENCES subcategorias(id) ON DELETE CASCADE,
    valor REAL NOT NULL,
    PRIMARY KEY (evaluacion_id, subcategoria_id)
);
"""

# ---------------------------------------------------------------- datos semilla

CONFIG_INICIAL = {
    "empresa": "Impresos Tecnográficos",
    "escala": "100",            # Pegado!D26 "Escala"
    "escala_respuesta": "5",    # Evaluacion de form!D3
    "puntos_max": "300",        # Pegado!D51 (grado A "Hasta")
    "periodo_actual": "",
}

GRADOS_INICIALES = [("A", 0.9), ("B", 0.7), ("C", 0.5), ("D", 0.4), ("E", 0.25), ("F", 0.0)]

PLANTILLA_OPERADOR = {
    "nombre": "Operador (Pegado)",
    "etiquetas": "C,B,A",
    "grado_minimo": "C",
    "grado_maximo": None,
    "categorias": [
        ("A", "Capacidad", 0.3, [
            ("Experiencia en el Proceso", "Arreglos, fondo automático, 4 y 6 esquinas", 0.65, "evaluacion"),
            ("Conocimiento Avanzado", "Sistema de engomado, diferentes adhesivos", 0.25, "evaluacion"),
            ("Versatilidad", "Manejo de distintas máquinas y cuidado de equipo", 0.10, "evaluacion"),
        ]),
        ("B", "Disponibilidad", 0.2, [
            ("Puntualidad", "Entrada y salida a su hora", 0.3, "evaluacion"),
            ("Asistencia", "Lunes - Sábado, si aplica", 0.5, "evaluacion"),
            ("Apoyo Extra", "Disponibilidad, horas extras, sábados en la tarde, domingos", 0.2, "evaluacion"),
        ]),
        ("C", "Resultados", 0.4, [
            ("Productividad %", "Se toma del cálculo de productividad; si no hay datos, de la evaluación", 0.5, "productividad"),
            ("Calidad en su Trabajo", "", 0.5, "evaluacion"),
        ]),
        ("D", "Personalidad", 0.1, [
            ("Actitud", "Comunicación, dinamismo, proactividad", 0.5, "evaluacion"),
            ("Orden-Limpieza", "", 0.2, "evaluacion"),
            ("Desarrollo", "Orientación al reto, sentido de innovación, aprendizaje", 0.3, "evaluacion"),
        ]),
    ],
}

PLANTILLA_AYUDANTE = {
    "nombre": "Ayudante (Pegado)",
    "etiquetas": "F,E,D",
    "grado_minimo": None,
    "grado_maximo": "D",
    "categorias": [
        ("A", "Capacidad", 0.3, [
            ("Experiencia en el Proceso", "", 0.65, "evaluacion"),
            ("Conocimiento Avanzado", "Stripping, despicado, espadas", 0.25, "evaluacion"),
            ("Versatilidad", "Manejo de distintas máquinas", 0.10, "evaluacion"),
        ]),
        ("B", "Disponibilidad", 0.2, [
            ("Puntualidad", "Entrada y salida a su hora", 0.3, "evaluacion"),
            ("Asistencia", "Lunes - Sábado, si aplica", 0.5, "evaluacion"),
            ("Apoyo Extra", "Horas extras, sábados en la tarde, domingos", 0.2, "evaluacion"),
        ]),
        ("C", "Resultados", 0.4, [
            ("Productividad %", "Se toma del cálculo de productividad; si no hay datos, de la evaluación", 0.5, "productividad"),
            ("Calidad en su Trabajo", "", 0.5, "evaluacion"),
        ]),
        ("D", "Personalidad", 0.1, [
            ("Actitud - Dinamismo", "", 0.5, "evaluacion"),
            ("Orden-Limpieza", "", 0.2, "evaluacion"),
            ("Desarrollo", "", 0.3, "evaluacion"),
        ]),
    ],
}

# (código, área, puesto, mínimo, máximo) — hoja Tabulador, vigencia febrero 2019
PUESTOS_INICIALES = [
    (1000, "Hojeado", "Operador", 1800, 2400),
    (1001, "Impresión", "Operador", 3000, 4000),
    (1002, "Impresión", "Ayudante", 1600, 2000),
    (1003, "Corrugado", "Operador", 2200, 2800),
    (1004, "Corrugado", "Ayudante", 1400, 1800),
    (1005, "Laminado", "Operador", 2100, 2500),
    (1006, "Laminado", "Ayudante", 1400, 1800),
    (1007, "Troquelado", "Operador", 2200, 3000),
    (1008, "Troquelado", "Ayudante", 1400, 1800),
    (1009, "Pegado", "Operador", 2500, 3200),
    (1010, "Pegado", "Ayudante", 1400, 1800),
    (1011, "General", "Ayudante", 1300, 1300),
    (1012, "Despique", "Ayudante", 1300, 1300),
]

# (clave, tiros requeridos por turno, horas turno) — hoja Tabulador L:N
MAQUINAS_INICIALES = [
    ("H", 35000, 12), ("K", None, 12), ("C", None, 12), ("I", None, 12),
    ("L", None, 12), ("VB", 45000, 12), ("M", 4500, 12), ("900", 30000, 12),
    ("V", None, 12), ("FM", None, 12), ("G", None, 12),
]


def conectar(ruta: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(ruta or RUTA_DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def crear_plantilla(con: sqlite3.Connection, datos: dict) -> int:
    cur = con.execute(
        "INSERT INTO plantillas (nombre, etiquetas, grado_minimo, grado_maximo) VALUES (?, ?, ?, ?)",
        (datos["nombre"], datos["etiquetas"], datos["grado_minimo"], datos.get("grado_maximo")),
    )
    pid = cur.lastrowid
    for i, (clave, nombre, peso, subs) in enumerate(datos["categorias"]):
        cid = con.execute(
            "INSERT INTO categorias (plantilla_id, clave, nombre, peso, orden) VALUES (?, ?, ?, ?, ?)",
            (pid, clave, nombre, peso, i),
        ).lastrowid
        for j, (snombre, desc, speso, fuente) in enumerate(subs):
            con.execute(
                "INSERT INTO subcategorias (categoria_id, nombre, descripcion, peso, fuente, orden) VALUES (?, ?, ?, ?, ?, ?)",
                (cid, snombre, desc, speso, fuente, j),
            )
    return pid


def inicializar(con: sqlite3.Connection, admin_usuario: str = "admin", admin_password: str = "admin") -> bool:
    """Crea el esquema y, si la base está vacía, carga la configuración del Excel.

    Devuelve True si se cargaron datos iniciales.
    """
    con.executescript(ESQUEMA)
    if con.execute("SELECT COUNT(*) FROM plantillas").fetchone()[0]:
        return False

    con.executemany("INSERT INTO config (clave, valor) VALUES (?, ?)", CONFIG_INICIAL.items())
    con.executemany("INSERT INTO grados (letra, pct) VALUES (?, ?)", GRADOS_INICIALES)
    op_id = crear_plantilla(con, PLANTILLA_OPERADOR)
    ay_id = crear_plantilla(con, PLANTILLA_AYUDANTE)

    pts_max = float(CONFIG_INICIAL["puntos_max"])
    pct = dict(GRADOS_INICIALES)
    for codigo, area, puesto, mn, mx in PUESTOS_INICIALES:
        if puesto == "Operador":
            # Operador: de la base del grado C (150 pts) al máximo (300 pts)
            base, tope, plantilla = pts_max * pct["C"], pts_max, op_id
        else:
            # Ayudante: de la base del grado E (75 pts) a la base del grado C (150 pts)
            base, tope, plantilla = pts_max * pct["E"], pts_max * pct["C"], ay_id
        con.execute(
            "INSERT INTO puestos (codigo, area, puesto, sueldo_min, sueldo_max, pts_base, pts_tope, plantilla_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (codigo, area, puesto, mn, mx, base, tope, plantilla),
        )
    con.executemany(
        "INSERT INTO maquinas (clave, tiros_turno, horas_turno) VALUES (?, ?, ?)", MAQUINAS_INICIALES
    )
    if not con.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (admin_usuario,)).fetchone():
        con.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol) VALUES (?, ?, ?, 'admin')",
            (admin_usuario, "Administrador RH", generate_password_hash(admin_password)),
        )
    con.commit()
    return True


def obtener_config(con: sqlite3.Connection) -> dict:
    return {r["clave"]: r["valor"] for r in con.execute("SELECT clave, valor FROM config")}


def guardar_config(con: sqlite3.Connection, clave: str, valor: str) -> None:
    con.execute(
        "INSERT INTO config (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (clave, valor),
    )


def criterios_plantilla(con: sqlite3.Connection, plantilla_id: int) -> list[dict]:
    """Categorías con sus subcategorías, en orden."""
    cats = []
    for c in con.execute(
        "SELECT * FROM categorias WHERE plantilla_id = ? ORDER BY orden, id", (plantilla_id,)
    ):
        subs = [
            dict(s)
            for s in con.execute(
                "SELECT * FROM subcategorias WHERE categoria_id = ? ORDER BY orden, id", (c["id"],)
            )
        ]
        cats.append({**dict(c), "subcategorias": subs})
    return cats
