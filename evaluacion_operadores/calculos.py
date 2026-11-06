"""Lógica de cálculo de la evaluación de operadores y del tabulador de sueldos.

Replica las fórmulas del archivo EVALUACION_OPERADORES_PEG_Tabuladores_RH.xlsm:

* Hoja "Pegado": puntos máximos por subcategoría y grado por subcategoría.
* Hoja "Evaluacion de form": puntos = (promedio de respuestas / escala) * puntos máximos.
* Hoja "Calculo de P%": productividad % = producción neta / productividad neta de máquina.
* Hoja "Tabulador": sueldo sugerido = sueldo mínimo + ($/pto * puntos sobre la base).

Este módulo no depende de la base de datos para poder probarse de forma aislada.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Cada subcategoría tiene tres niveles (p. ej. C, B, A). El máximo del nivel 1
# equivale a 1/3 de los puntos máximos, el del nivel 2 a 2/3 y el del nivel 3 al total.
NIVELES_POR_SUBCATEGORIA = 3


def puntos_nivel(escala: float, peso_categoria: float, peso_subcategoria: float) -> tuple[float, float, float]:
    """Devuelve los límites (nivel bajo, medio, alto) de una subcategoría.

    En Excel: H = Escala * (D% * Ponderación); I = H * 2; J = I + H.
    """
    bajo = escala * (peso_subcategoria * peso_categoria)
    return bajo, bajo * 2, bajo * 3


def grado_subcategoria(puntos: float | None, limites: tuple[float, float, float], etiquetas: list[str]) -> str:
    """Grado de una subcategoría (fórmula de las filas "Grado" de la hoja Pegado).

    * Menos de 1 punto -> "-"
    * Más del máximo -> "#$" (dato fuera de rango)
    * > límite medio -> etiqueta alta; > límite bajo -> etiqueta media; si no, etiqueta baja.
    """
    bajo, medio, alto = limites
    if puntos is None or puntos < 1:
        return "-"
    if puntos > alto + 1e-9:
        return "#$"
    if puntos > medio:
        return etiquetas[2]
    if puntos > bajo:
        return etiquetas[1]
    return etiquetas[0]


def puntos_desde_respuestas(promedio: float | None, escala_respuesta: float, puntos_max: float) -> float | None:
    """Hoja "Evaluacion de form": Resultado = (Fuente / Escala) * Promedio."""
    if promedio is None:
        return None
    return (puntos_max / escala_respuesta) * promedio


@dataclass
class Productividad:
    produccion_total: float
    hr_prod: float
    hr_turno: float

    @property
    def desempeno(self) -> float:
        """Producción por hora productiva."""
        return self.produccion_total / self.hr_prod if self.hr_prod else 0.0

    @property
    def produccion_neta(self) -> float:
        """Producción por hora de turno."""
        return self.produccion_total / self.hr_turno if self.hr_turno else 0.0

    def porcentaje(self, tiros_turno: float | None, horas_turno: float | None) -> float | None:
        """Productividad % = producción neta / (tiros requeridos por turno / horas turno)."""
        if not tiros_turno or not horas_turno:
            return None
        neta_maquina = tiros_turno / horas_turno
        return self.produccion_neta / neta_maquina


def bandas_grado(puntos_max: float, grados: list[tuple[str, float]]) -> list[tuple[str, float, float]]:
    """Tabla "Grado / Desde / Hasta" a partir de los escalones porcentuales.

    ``grados`` es una lista (letra, % mínimo) p. ej. [("A", .9), ("B", .7), ...].
    Devuelve (letra, desde, hasta) ordenado del grado más alto al más bajo.
    """
    ordenados = sorted(grados, key=lambda g: g[1], reverse=True)
    bandas = []
    hasta = puntos_max
    for letra, pct in ordenados:
        desde = puntos_max * pct
        bandas.append((letra, desde, hasta))
        hasta = desde - 1
    return bandas


def grado_final(total: float, bandas: list[tuple[str, float, float]], grado_minimo: str | None = None,
                grado_maximo: str | None = None) -> str:
    """Grado final según los puntos totales (columna "Grado Final").

    Si el total queda debajo del grado mínimo del puesto devuelve p. ej. "C-".
    Si el puesto tiene grado máximo (p. ej. Ayudante = D) no se asigna un grado superior.
    """
    if not total:
        return "-"
    letras = [b[0] for b in bandas]
    if grado_maximo in letras:
        idx = letras.index(grado_maximo)
        if total >= bandas[idx][1] - 1e-9:
            return grado_maximo
    if grado_minimo in letras:
        idx = letras.index(grado_minimo)
        if total < bandas[idx][1]:
            return f"{grado_minimo}-"
    if total > bandas[0][2] + 1e-9:
        return "ERROR"
    for letra, desde, _hasta in bandas:
        if total >= desde - 1e-9:
            return letra
    return bandas[-1][0]


def costo_por_punto(sueldo_min: float, sueldo_max: float, pts_base: float, pts_tope: float) -> float:
    """$/pto ideal = (Máximo - Mínimo) / (Puntos tope - Puntos base)."""
    if pts_tope <= pts_base:
        return 0.0
    return (sueldo_max - sueldo_min) / (pts_tope - pts_base)


def sueldo_sugerido(total: float, sueldo_min: float, sueldo_max: float, pts_base: float, pts_tope: float) -> float | None:
    """Sueldo sugerido = Mínimo + $/pto * max(0, puntos - base), limitado al máximo."""
    if not total or total <= 0:
        return None
    cpp = costo_por_punto(sueldo_min, sueldo_max, pts_base, pts_tope)
    sueldo = sueldo_min + cpp * max(0.0, total - pts_base)
    return min(sueldo, sueldo_max)


@dataclass
class ResultadoSub:
    id: int
    categoria: str
    nombre: str
    fuente: str
    limites: tuple[float, float, float]
    promedio: float | None
    puntos: float | None
    grado: str
    origen: str  # "evaluación", "productividad" o "sin datos"

    @property
    def maximo(self) -> float:
        return self.limites[2]


@dataclass
class ResultadoEmpleado:
    subcategorias: list[ResultadoSub] = field(default_factory=list)
    total: float = 0.0
    grado: str = "-"
    sueldo_sugerido: float | None = None
    costo_por_punto: float = 0.0
    num_evaluaciones: int = 0
    productividad_pct: float | None = None
    completo: bool = False


def evaluar_empleado(
    *,
    categorias: list[dict],
    escala: float,
    escala_respuesta: float,
    etiquetas: list[str],
    promedios: dict[int, float],
    num_evaluaciones: int,
    productividad_pct: float | None,
    bandas: list[tuple[str, float, float]],
    grado_minimo: str | None,
    sueldo_min: float,
    sueldo_max: float,
    pts_base: float,
    pts_tope: float,
    grado_maximo: str | None = None,
) -> ResultadoEmpleado:
    """Calcula puntos, grados y sueldo sugerido para un empleado.

    ``categorias``: [{"nombre", "peso", "subcategorias": [{"id", "nombre", "peso", "fuente"}]}]
    ``promedios``: promedio de respuestas por id de subcategoría.
    Las subcategorías con fuente "productividad" usan ``productividad_pct`` si existe
    (igual que la hoja Pegado, que toma la productividad de "Calculo de P%");
    si no hay dato de productividad se usa el promedio de la evaluación.
    """
    res = ResultadoEmpleado(num_evaluaciones=num_evaluaciones, productividad_pct=productividad_pct)
    con_dato = 0
    total_subs = 0
    for cat in categorias:
        for sub in cat["subcategorias"]:
            total_subs += 1
            limites = puntos_nivel(escala, cat["peso"], sub["peso"])
            promedio = promedios.get(sub["id"])
            if sub["fuente"] == "productividad" and productividad_pct is not None:
                puntos = productividad_pct * limites[2]
                origen = "productividad"
            elif promedio is not None:
                puntos = puntos_desde_respuestas(promedio, escala_respuesta, limites[2])
                origen = "evaluación"
            else:
                puntos = None
                origen = "sin datos"
            grado = grado_subcategoria(puntos, limites, etiquetas)
            if puntos is not None:
                con_dato += 1
                # Un valor fuera de rango (#$) no suma más del máximo de la subcategoría.
                res.total += min(puntos, limites[2])
            res.subcategorias.append(
                ResultadoSub(sub["id"], cat["nombre"], sub["nombre"], sub["fuente"], limites, promedio, puntos, grado, origen)
            )
    res.completo = total_subs > 0 and con_dato == total_subs
    res.grado = grado_final(res.total, bandas, grado_minimo, grado_maximo)
    res.costo_por_punto = costo_por_punto(sueldo_min, sueldo_max, pts_base, pts_tope)
    res.sueldo_sugerido = sueldo_sugerido(res.total, sueldo_min, sueldo_max, pts_base, pts_tope)
    return res
