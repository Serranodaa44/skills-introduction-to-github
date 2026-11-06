# Evaluación de Operadores y Tabuladores de RH

Versión web del archivo `EVALUACION_OPERADORES_PEG_Tabuladores_RH.xlsm`. Permite que **varios
usuarios (supervisores, jefes de área)** capturen evaluaciones desde la computadora o el celular, y que
**RH** vea automáticamente los puntos, el grado y el sueldo sugerido de cada persona.

## Qué hace (equivalencia con el Excel)

| Hoja del Excel | En la app |
|---|---|
| **Pegado** (categorías, sub-categorías, ponderaciones, grados C/B/A y D/E/F) | *Configuración → Plantillas de criterios* (editables, duplicables para otras áreas) |
| **Evaluacion de form** + **DB Respuestas** (Google Form) | *Evaluar*: cada usuario califica 1–5; se promedian todas las respuestas del periodo |
| **Calculo de P%** | *Productividad*: producción total, hr productivas y hr turno → desempeño, producción neta y % |
| **Tabulador** | *Configuración → Tabulador* y *Máquinas* |
| Columnas *Grado Final*, *Sueldo Sugerido*, *Sueldo Actual* | *Resultados* y detalle por empleado, exportable a CSV (abre en Excel) |

### Fórmulas

- Puntos máximos de una sub-categoría = Escala (100) × % categoría × % sub-categoría × 3.
  Los límites de nivel son 1/3, 2/3 y 3/3 de ese máximo (columnas C, B, A del Excel).
- Puntos obtenidos = (promedio de calificaciones ÷ 5) × puntos máximos.
- **Productividad %** = producción neta ÷ (tiros requeridos por turno ÷ horas turno); los puntos de
  *Productividad %* = % × puntos máximos. Si no hay datos de producción se usa la calificación del formulario.
- Grado final por rangos (puntos máximos 300): A 270–300, B 210–269, C 150–209, D 120–149, E 75–119, F 0–74.
  Operadores debajo de 150 pts → `C-`; Ayudantes tienen como máximo grado `D`.
- **Sueldo sugerido** = mínimo del puesto + $/pto × (puntos − pts base), sin pasar del máximo;
  $/pto = (máximo − mínimo) ÷ (pts tope − pts base). Operadores: 150–300 pts; Ayudantes: 75–150 pts.

Todo es configurable desde la app (escalas, porcentajes de grado, ponderaciones, sueldos, máquinas).

## Roles

- **Evaluador**: sólo ve la pantalla *Evaluar* (lista de personas, pendientes/evaluados). Una evaluación
  por persona y periodo; si la vuelve a enviar, se actualiza.
- **Admin (RH)**: además ve resultados y sueldos, da de alta empleados, productividad, usuarios y configuración.

## Instalación y uso

Requiere Python 3.10 o superior.

```bash
cd evaluacion_operadores
pip install -r requirements.txt
python app.py
```

Abre <http://localhost:5000> y entra con `admin` / `admin` (cámbiala en *Mi cuenta*; o define
`ADMIN_PASSWORD` antes del primer arranque). La primera vez se crea `evaluacion.db` con la
configuración del Excel (criterios, tabulador de feb-2019 y máquinas).

Pasos sugeridos:

1. *Configuración*: define el **periodo actual** (p. ej. `2026-S2`) y completa los *tiros requeridos por turno*
   de las máquinas que aún no lo tienen (en el Excel sólo H, VB, M y 900 tenían dato).
2. *Configuración → Usuarios*: crea un usuario **evaluador** para cada supervisor.
3. *Empleados*: da de alta a las personas (área/puesto, máquina, antigüedad, sueldo actual).
4. *Productividad*: captura la producción del periodo.
5. Los evaluadores entran a *Evaluar* desde su celular.
6. RH revisa *Resultados* y exporta a CSV.

### Que otros usuarios entren desde la red

```bash
HOST=0.0.0.0 PORT=5000 python app.py
```

y comparte `http://IP-DE-LA-PC:5000`. Para uso permanente conviene un servidor WSGI
(p. ej. `pip install waitress && waitress-serve --listen=0.0.0.0:5000 app:app`, tras ejecutar
`python app.py` una vez para crear la base) y un respaldo periódico de `evaluacion.db`.

### Importar datos del Excel original

```bash
pip install openpyxl
python importar_excel.py EVALUACION_OPERADORES_PEG_Tabuladores_RH.xlsm --periodo 2019-S1
```

Carga los operadores/ayudantes y la producción de la hoja *Calculo de P%*, y las respuestas de
*DB Respuestas* si las hay. Las macros no se ejecutan. El archivo `.xlsm` y la base de datos están
en `.gitignore` para no publicar datos personales ni sueldos.

### Agregar otra área

1. *Configuración → Plantillas* → abre *Operador (Pegado)* → **Duplicar** (p. ej. "Operador (Impresión)").
2. Ajusta sub-categorías, descripciones y ponderaciones (la app avisa si no suman 100%).
3. En *Tabulador* asigna la nueva plantilla al puesto, o agrega un puesto nuevo.

## Pruebas

```bash
pip install pytest
python -m pytest tests
```

Las pruebas comparan los cálculos con los valores del Excel (límites 19.5/39/58.5, rangos de grado,
$/pto 4.67 y 5.33, productividad de la hoja *Calculo de P%*) y recorren el flujo completo en la web.

## Diferencias con el Excel

- La columna *Sueldo Sugerido* de la hoja Pegado usaba por error el tabulador de **Troquelado**
  (`Tabulador!F22` y `J22`); aquí se usa el del puesto de cada persona.
- En el Excel, un puntaje mayor a 300 mostraba `---`; aquí el sueldo se limita al máximo del puesto.
- En el Tabulador, los ayudantes usaban rangos de puntos distintos entre sí (sólo Troquelado usaba 75–150);
  aquí todos los ayudantes usan 75–150 por defecto y se puede ajustar por puesto.
