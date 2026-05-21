"""Configuracion central: centros, aperturas, tags, prefijos contables.

Todo lo que el usuario podria querer ajustar a futuro vive aqui o en la
base de datos (tabla `centers` para el mes de apertura, `pl_mapping` para
el mapeo de cuentas). Este modulo solo define los valores por defecto.
"""

from __future__ import annotations

import os

# --- Rutas -----------------------------------------------------------------
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "kratos.db")

# --- Sociedad (fase 1: una sola) ------------------------------------------
SOCIEDAD = "PALAVIAN INTERACTIVE ESTATE SL"

# --- Centros ---------------------------------------------------------------
# Codigo interno -> etiqueta visible
CENTERS = ["K1", "K2", "K3", "K4"]
HQ = "HQ"
UNASSIGNED = "Sin asignar"
ALL_CENTERS = CENTERS + [HQ]            # centros con P&L propia
ALL_BUCKETS = CENTERS + [HQ, UNASSIGNED]

CENTER_LABELS = {
    "K1": "K1 Paris",
    "K2": "K2 Sant Joan",
    "K3": "K3 Poble Nou",
    "K4": "K4 Valencia",
    "HQ": "HQ Estructura",
    UNASSIGNED: "Sin asignar",
}

# Mes de apertura por defecto (formato AAAA-MM). Editable en la tabla `centers`.
# K1/K2 operan desde enero 2026; K3 abre 18/05/2026; K4 a definir.
DEFAULT_OPENING_MONTH = {
    "K1": "2026-01",
    "K2": "2026-01",
    "K3": "2026-05",
    "K4": "2026-09",   # placeholder; el usuario lo cambia para "jugar"
}

# Horizonte de proyeccion
HORIZON_MONTHS = 36
DEFAULT_START_MONTH = "2026-01"

# --- Tags de Holded --------------------------------------------------------
# Tag operativo -> centro
TAG_TO_CENTER = {
    "localparis": "K1",
    "localsantjoan": "K2",
    "localpoblenou": "K3",
    "localvalencia": "K4",
    # etiquetas cortas que el cliente tambien usa
    "k1": "K1",
    "k2": "K2",
    "k3": "K3",
    "k4": "K4",
    "hq": "HQ",
}
# Orden de precedencia cuando hay varios tags de centro en una misma linea
CENTER_PRECEDENCE = ["K1", "K2", "K3", "K4", "HQ"]

# Tags que NO identifican centro (ruido de gestoria)
NOISE_TAGS = {"revisadogestoria", "trimestreanterior", "trimestreactual"}

# --- Fallback por nombre de cuenta (solo lineas sin tag) -------------------
# Se busca el texto (en mayusculas, sin acentos) dentro del nombre de cuenta.
ACCOUNT_NAME_KEYWORDS = [
    ("POBLE NOU", "K3"),
    ("SANT JOAN", "K2"),
    ("PARIS", "K1"),
    ("VALENCIA", "K4"),
]

# Cuentas de banco/cobro -> centro (fallback fuerte)
ACCOUNT_CODE_TO_CENTER = {
    "57200001": "K1",   # SABADELL - Local Paris
    "57200002": "K2",   # CAIXA - Local Sant Joan
    "57200003": "K3",   # Caixa - Local Poble Nou
    "55500001": "K1",   # Partidas de credito PARIS
    "55500002": "K2",   # Partidas de credito SANT JOAN
    "44100001": "K2",   # TPV Caixa (Local Sant Joan)
    "44100002": "K2",   # ADYEN - Caixa (Local Sant Joan)
    "44100003": "K1",   # TPV Sabadell (Local Paris)
    "44100004": "K1",   # ADYEN - Sabadell (Local Paris)
    "43000005": "K1",   # Clientes - LOCAL PARIS
    "43000006": "K2",   # LOCAL SANT JOAN
    "70000001": "K1",
    "70000002": "K2",
    "70500001": "K1",
    "70500002": "K2",
}

# --- Familias de cuentas (prefijos PGC) -----------------------------------
# CAPEX = inmovilizado (cuentas de activo). Lo dijo el usuario: "pagos
# imputados a cuentas de activos = inversion". Es mas robusto que el tag k3.
CAPEX_PREFIXES = ("20", "21", "22", "23")
# Tesoreria (caja/bancos) para el cash flow real (metodo directo).
TREASURY_PREFIXES = ("570", "571", "572", "573", "574")
# Financiacion (deuda a largo / partes vinculadas).
FINANCING_PREFIXES = ("16", "17", "52")

# --- Prorrateo de gastos de HQ a los centros ------------------------------
# "manual"  -> pesos % por centro/mes definidos en el Excel de supuestos
# "revenue" -> proporcional a los ingresos del centro en el periodo
PRORATION_KEY = "manual"

# --- Frecuencia de gastos (tabla maestra de gastos) -----------------------
# etiqueta -> cada cuantos meses se repite el gasto
FREQ_MONTHS = {
    "Mensual": 1, "Bimestral": 2, "Trimestral": 3,
    "Semestral": 6, "Anual": 12,
}
FREQ_LABELS = list(FREQ_MONTHS.keys())

# --- Personal -------------------------------------------------------------
PERSONAL_ROLES = ["Entrenador 1", "Entrenador 2", "Entrenador 3",
                  "Entrenador 4", "Entrenador 5", "Responsable/Admin"]
SS_PCT_DEFAULT = 33.0

# --- P&L (cuenta de resultados) -------------------------------------------
VENTAS = "Ingresos"
VENTAS_PARTIDAS = ["Cuotas", "Productos", "ClassPass Spain",
                   "Urban Sports Club", "Otros ingresos"]
# Secciones de gasto que se editan como bloques en Tabla de mando
# (clave interna = apartado de la P&L, etiqueta visible)
GASTOS_SECCIONES = [
    ("Aprovisionamientos", "Aprovisionamientos"),
    ("Otros gastos de explotacion", "Otros gastos de explotación"),
    ("Gastos financieros", "Gastos financieros"),
]
COST_APARTADOS = [k for k, _ in GASTOS_SECCIONES]
IS_RATE_DEFAULT = 25.0

# Perfil fiscal por defecto por partida (IVA % y retencion IRPF %).
# La proyeccion usa estos valores; cuando se importa el libro diario
# el sistema "aprende" el IVA medio realmente observado por partida y
# sobrescribe estos defaults para los meses futuros.
PARTIDA_TAX = {
    # Ingresos
    "Cuotas": {"iva": 21, "ret": 0},
    "Productos": {"iva": 21, "ret": 0},
    "ClassPass Spain": {"iva": 21, "ret": 0},
    "Urban Sports Club": {"iva": 21, "ret": 0},
    "Otros ingresos": {"iva": 21, "ret": 0},
    # Coste de ventas
    "Compras mercaderias": {"iva": 21, "ret": 0},
    # Otros gastos de explotacion
    "Arrendamiento local": {"iva": 21, "ret": 19},   # alquiler urbano
    "Renting": {"iva": 21, "ret": 0},                # leasing operativo
    "Limpieza": {"iva": 21, "ret": 0},
    "Suministros agua": {"iva": 10, "ret": 0},
    "Suministros electrico": {"iva": 21, "ret": 0},
    "Suministros telefonia / internet": {"iva": 21, "ret": 0},
    "Suministros": {"iva": 21, "ret": 0},
    "Mantenimiento": {"iva": 21, "ret": 0},
    "Seguros": {"iva": 0, "ret": 0},                  # exento
    "Marketing": {"iva": 21, "ret": 0},
    "Servicios profesionales": {"iva": 21, "ret": 0},
    "Herramientas de gestion": {"iva": 21, "ret": 0},
    "Dietas y viajes": {"iva": 10, "ret": 0},
    "Servicios bancarios": {"iva": 0, "ret": 0},      # exento
    "Tributos": {"iva": 0, "ret": 0},                 # exento
    "Otros gastos de explotacion": {"iva": 21, "ret": 0},
    # Financieros
    "Intereses prestamo": {"iva": 0, "ret": 0},
    # Personal (sin IVA)
    "Sueldos y salarios": {"iva": 0, "ret": 0},
    "Seguridad Social": {"iva": 0, "ret": 0},
    "Otros gastos de personal": {"iva": 0, "ret": 0},
}
MESES_CORTOS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


# --- Carga del libro diario -----------------------------------------------
# Cada export de Holded es el YTD completo -> reemplazar (no acumular).
REPLACE_MODE_DEFAULT = True

SHEET_CANDIDATES = ["Holded"]   # se intenta primero; si no, la primera hoja
HEADER_REQUIRED_FIELDS = {"asiento", "fecha", "cuenta", "debe", "haber"}
