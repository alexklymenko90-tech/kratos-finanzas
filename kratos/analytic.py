"""P&L (cuenta de resultados) por secciones.

  1. INGRESOS                 (Cuotas/Productos/ClassPass/Urban + Otros)
     TOTAL INGRESOS · Variacion vs mes anterior
  2. APROVISIONAMIENTOS       + % s/ ingresos
  3. GASTOS DE PERSONAL       + % s/ ingresos
  4. OTROS GASTOS DE EXPLOTACION + % s/ ingresos
  5. GASTOS DE ESTRUCTURA . HQ
  EBITDA                      + % s/ ingresos (margen EBITDA)
  6. AMORTIZACIONES -> EBIT (EBITDA - Amortizaciones)
  7. GASTOS FINANCIEROS
  RESULTADO ANTES DE IMPUESTOS (EBT)
  Impuesto sobre sociedades (tasa% sobre EBT positivo)
  RESULTADO NETO DEL EJERCICIO

`apartado` lo llevan lo real (mapeo de cuentas) y lo proyectado
(socios, personal y la tabla maestra de gastos).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from . import config

# Nombres internos de seccion
ING = config.VENTAS                       # "Ingresos"
APROV = "Aprovisionamientos"
PERS = "Gastos de Personal"
OTROS = "Otros gastos de explotacion"
HQ = "Gastos de estructura HQ"
AMORT = "Amortizaciones"
FIN = "Gastos financieros"

# Cuenta exacta de ingreso -> sub-partida
_REV_EXACT = {
    "70500003": "ClassPass Spain",
    "70500004": "Urban Sports Club",
    "70500001": "Cuotas",
    "70500002": "Cuotas",
    "70000001": "Productos",
    "70000002": "Productos",
}
# Cuenta exacta de gasto -> (seccion, partida)
_EXACT = {
    "62300002": (OTROS, "Servicios profesionales"),
    "62810000": (OTROS, "Suministros electrico"),
    "62820000": (OTROS, "Suministros agua"),
    "62840000": (OTROS, "Suministros telefonia / internet"),
    "62900004": (OTROS, "Limpieza"),
    "62900006": (OTROS, "Dietas y viajes"),
    "62900007": (OTROS, "Herramientas de gestion"),
    "62900008": (OTROS, "Herramientas de gestion"),
    "62900009": (OTROS, "Renting"),
    "62700001": (OTROS, "Marketing"),
    "62700002": (OTROS, "Marketing"),
}
# Prefijo de 3 digitos -> (seccion, partida)
_PREFIX = {
    "600": (APROV, "Compras mercaderias"),
    "601": (APROV, "Compras mercaderias"),
    "602": (APROV, "Compras mercaderias"),
    "607": (APROV, "Compras mercaderias"),
    "640": (PERS, "Sueldos y salarios"),
    "642": (PERS, "Seguridad Social"),
    "641": (PERS, "Otros gastos de personal"),
    "649": (PERS, "Otros gastos de personal"),
    "621": (OTROS, "Arrendamiento local"),
    "622": (OTROS, "Mantenimiento"),
    "623": (OTROS, "Servicios profesionales"),
    "625": (OTROS, "Seguros"),
    "626": (OTROS, "Servicios bancarios"),
    "627": (OTROS, "Marketing"),
    "628": (OTROS, "Suministros"),
    "629": (OTROS, "Otros gastos de explotacion"),
    "631": (OTROS, "Tributos"),
    "634": (OTROS, "Tributos"),
}
_PROJ_REV = {
    "Cuotas socios": "Cuotas", "Cuotas": "Cuotas",
    "ClassPass": "ClassPass Spain", "ClassPass Spain": "ClassPass Spain",
    "Urban Sports": "Urban Sports Club",
    "Urban Sports Club": "Urban Sports Club",
    "Venta de producto": "Productos", "Productos": "Productos",
}
_SECCION_APARTADO = {
    "Coste de ventas": APROV,
    "Gastos de personal": PERS,
    "Gastos de explotacion": OTROS,
    "Amortizaciones": AMORT,
    "Resultado financiero": FIN,
}
_FIN_INCOME_PREF = ("760", "761", "762", "766", "768", "769")

# Lista canonica de partidas por seccion (las mismas que aparecen en la
# P&L). La Tabla de mando muestra estas partidas como filas fijas; el
# mapeo de cuentas al importar el libro diario tambien usa estas etiquetas.
GASTOS_PARTIDAS_POR_SECCION = {
    APROV: ["Compras mercaderias"],
    OTROS: [
        "Arrendamiento local",
        "Renting",
        "Limpieza",
        "Suministros agua",
        "Suministros electrico",
        "Suministros telefonia / internet",
        "Suministros",
        "Mantenimiento",
        "Seguros",
        "Marketing",
        "Servicios profesionales",
        "Herramientas de gestion",
        "Dietas y viajes",
        "Servicios bancarios",
        "Tributos",
        "Otros gastos de explotacion",
    ],
    FIN: ["Intereses prestamo"],
}


def map_real_account(cuenta: str, prefijo3: str) -> Optional[Tuple[str, str]]:
    """(seccion, partida) para una cuenta real OPERATIVA, o None si queda
    fuera de la P&L (IS se calcula aparte; deterioros fuera)."""
    c0 = cuenta[:1]
    if c0 == "7":
        if prefijo3 in _FIN_INCOME_PREF:
            return (FIN, "Ingresos financieros")
        sub = _REV_EXACT.get(cuenta)
        if sub is None:
            sub = ("Productos" if prefijo3 in ("700", "701")
                   else "Cuotas" if prefijo3 == "705"
                   else "Otros ingresos")
        return (ING, sub)
    if c0 == "6":
        p2 = prefijo3[:2]
        if prefijo3 == "630":
            return None                       # IS: se calcula sobre EBT
        if p2 == "66":
            return (FIN, "Intereses prestamo")
        if p2 == "68":
            return (AMORT, "Amortizaciones")
        if p2 == "69":
            return None
        if cuenta in _EXACT:
            return _EXACT[cuenta]
        if prefijo3 in _PREFIX:
            return _PREFIX[prefijo3]
        return (OTROS, "Otros gastos de explotacion")
    return None


def proj_revenue_partida(partida: str) -> str:
    return _PROJ_REV.get(partida, "Otros ingresos")


def seccion_to_apartado(seccion: str) -> str:
    return _SECCION_APARTADO.get(seccion, OTROS)


# --- Construccion de la P&L -----------------------------------------------
def _row(label, ser, periods, kind):
    d = {"Concepto": label, "_kind": kind}
    for p in periods:
        d[p] = round(float(ser.get(p, 0.0)), 2)
    return d


def _blank(label, periods, kind):
    d = {"Concepto": label, "_kind": kind}
    for p in periods:
        d[p] = 0.0
    return d


def _pct_row(label, num, den, periods, kind="pct"):
    d = {"Concepto": label, "_kind": kind}
    for p in periods:
        v = den.get(p, 0.0)
        d[p] = round(100.0 * num.get(p, 0.0) / v, 1) if v else 0.0
    return d


def _var_row(label, ser, periods):
    """Variacion % vs el periodo anterior de la lista."""
    d = {"Concepto": label, "_kind": "pct"}
    prev = None
    for p in periods:
        cur = float(ser.get(p, 0.0))
        if prev is None or prev == 0:
            d[p] = 0.0
        else:
            d[p] = round(100.0 * (cur - prev) / abs(prev), 1)
        prev = cur
    return d


def pivot_analytic(long_df: pd.DataFrame, target: str,
                   periods: List[str], is_rate: float = None) -> pd.DataFrame:
    """Tabla P&L. `target` = centro o 'CONSOLIDADO'. `is_rate` = % IS."""
    if is_rate is None:
        is_rate = config.IS_RATE_DEFAULT
    data = (long_df if target == "CONSOLIDADO"
            else long_df[long_df["centro"] == target]).copy()

    # En CONSOLIDADO, agrupar todos los empleados (cada nombre es una
    # partida en la proyeccion) bajo "Sueldos y salarios".
    if target == "CONSOLIDADO" and not data.empty:
        _keep = {"Sueldos y salarios", "Seguridad Social",
                 "Otros gastos de personal"}
        m = ((data["apartado"] == PERS)
             & (~data["partida"].isin(_keep)))
        if m.any():
            data.loc[m, "partida"] = "Sueldos y salarios"

    def grp(apartado=None, partida=None):
        d = data
        if apartado is not None:
            d = d[d["apartado"] == apartado]
        if partida is not None:
            d = d[d["partida"] == partida]
        return d.groupby("periodo")["valor"].sum()

    def detail(apartado, ss_last=False):
        sub = data[data["apartado"] == apartado]
        out = []
        if sub.empty:
            return out
        # Orden: el canonico de la P&L (orden fijo) y luego cualquier
        # partida fuera del canonico al final.
        canon = GASTOS_PARTIDAS_POR_SECCION.get(apartado, [])
        present = list(sub["partida"].unique())
        order = [p for p in canon if p in present]
        extras = [p for p in present if p not in canon]
        if ss_last and "Seguridad Social" in extras:
            extras.remove("Seguridad Social")
            extras.append("Seguridad Social")
        order = order + extras
        for part in order:
            s = sub[sub["partida"] == part].groupby("periodo")["valor"].sum()
            if s.abs().sum() > 0:                  # solo si hay importe
                out.append(_row("    " + str(part), s, periods, "detail"))
        return out

    rows = []
    ventas = grp(ING)

    rows.append(_blank("1. INGRESOS", periods, "secchead"))
    rows.extend(detail(ING))
    rows.append(_row("TOTAL INGRESOS", ventas, periods, "total"))
    rows.append(_var_row("  Variacion vs mes anterior", ventas, periods))

    def seccion(num_title, ap, ss_last=False):
        rows.append(_blank(num_title, periods, "secchead"))
        rows.extend(detail(ap, ss_last=ss_last))
        tot = grp(ap)
        rows.append(_row("TOTAL " + num_title.split(". ", 1)[-1],
                         tot, periods, "total"))
        rows.append(_pct_row("  % s/ ingresos", tot, ventas, periods))
        return tot

    aprov = seccion("2. APROVISIONAMIENTOS", APROV)
    pers = seccion("3. GASTOS DE PERSONAL", PERS, ss_last=True)
    otros = seccion("4. OTROS GASTOS DE EXPLOTACION", OTROS)

    rows.append(_blank("5. GASTOS DE ESTRUCTURA · HQ", periods,
                       "secchead"))
    hq = grp(HQ)
    rows.append(_row("Gastos de estructura · HQ", hq, periods,
                     "detail"))
    rows.append(_pct_row("  % s/ ingresos", hq, ventas, periods))

    ebitda = (ventas.add(aprov, fill_value=0).add(pers, fill_value=0)
              .add(otros, fill_value=0).add(hq, fill_value=0))
    rows.append(_row("EBITDA", ebitda, periods, "ebitda"))
    rows.append(_pct_row("  % s/ ingresos (margen EBITDA)",
                         ebitda, ventas, periods, "pct"))

    rows.append(_blank("6. AMORTIZACIONES", periods, "secchead"))
    amort = grp(AMORT)
    rows.append(_row("Amortizaciones", amort, periods, "detail"))
    ebit = ebitda.add(amort, fill_value=0)
    rows.append(_row("EBIT (EBITDA - Amortizaciones)", ebit, periods,
                     "subtotal"))
    rows.append(_pct_row("  % s/ ingresos", ebit, ventas, periods))

    rows.append(_blank("7. GASTOS FINANCIEROS", periods, "secchead"))
    rows.extend(detail(FIN))
    fin = grp(FIN)
    rows.append(_row("TOTAL GASTOS FINANCIEROS", fin, periods, "total"))
    rows.append(_pct_row("  % s/ ingresos", fin, ventas, periods))

    ebt = ebit.add(fin, fill_value=0)
    rows.append(_row("RESULTADO ANTES DE IMPUESTOS (EBT)", ebt, periods,
                     "subtotal"))
    rows.append(_pct_row("  % s/ ingresos", ebt, ventas, periods))

    # IS anual: se calcula sobre el EBT del ano y se imputa en diciembre
    ebt_year = {}
    for p in periods:
        ebt_year[p[:4]] = ebt_year.get(p[:4], 0.0) + float(ebt.get(p, 0.0))
    is_ser = {}
    for p in periods:
        if p[5:7] == "12":
            ann = ebt_year.get(p[:4], 0.0)
            is_ser[p] = round(-ann * is_rate / 100.0, 2) if ann > 0 else 0.0
        else:
            is_ser[p] = 0.0
    is_ser = pd.Series(is_ser)
    rows.append(_row("Impuesto sobre sociedades (%d%% anual, dic)"
                     % round(is_rate), is_ser, periods, "cost"))

    neto = ebt.add(is_ser, fill_value=0)
    rows.append(_row("RESULTADO NETO DEL EJERCICIO", neto, periods, "neto"))
    rows.append(_pct_row("  % s/ ingresos", neto, ventas, periods))

    return pd.DataFrame(rows).set_index("Concepto")


def pivot_analytic_v2(long_df: pd.DataFrame, periods: List[str],
                      is_rate: float = None) -> pd.DataFrame:
    """P&L v2 del Consolidado: misma estructura que pivot_analytic pero el
    detalle de cada seccion se desglosa POR CENTRO (K1 ... K4 + HQ) en vez
    de por partida. Solo aplica al consolidado; las P&L individuales por
    centro siguen usando pivot_analytic."""
    if is_rate is None:
        is_rate = config.IS_RATE_DEFAULT
    data = long_df.copy()

    def grp(apartado=None):
        d = data
        if apartado is not None:
            d = d[d["apartado"] == apartado]
        return d.groupby("periodo")["valor"].sum()

    def detail_by_center(apartado, section_label):
        sub = data[data["apartado"] == apartado]
        out = []
        if sub.empty:
            return out
        for c in config.CENTERS + [config.HQ]:
            s = (sub[sub["centro"] == c]
                 .groupby("periodo")["valor"].sum())
            if s.abs().sum() > 0:
                lbl = "    %s . %s" % (
                    section_label, config.CENTER_LABELS.get(c, c))
                out.append(_row(lbl, s, periods, "detail"))
        return out

    rows = []
    ventas = grp(ING)

    rows.append(_blank("1. INGRESOS", periods, "secchead"))
    rows.extend(detail_by_center(ING, "Ingresos"))
    rows.append(_row("TOTAL INGRESOS", ventas, periods, "total"))
    rows.append(_var_row("  Variacion vs mes anterior", ventas, periods))

    def seccion(num_title, ap, cons_label):
        rows.append(_blank(num_title, periods, "secchead"))
        rows.extend(detail_by_center(ap, cons_label))
        tot = grp(ap)
        rows.append(_row("TOTAL " + num_title.split(". ", 1)[-1],
                         tot, periods, "total"))
        rows.append(_pct_row("  % s/ ingresos", tot, ventas, periods))
        return tot

    aprov = seccion("2. APROVISIONAMIENTOS", APROV, "Aprovisionamientos")
    pers = seccion("3. GASTOS DE PERSONAL", PERS, "Gastos de personal")
    otros = seccion("4. OTROS GASTOS DE EXPLOTACION", OTROS,
                    "Otros gastos")

    rows.append(_blank("5. GASTOS DE ESTRUCTURA . HQ", periods,
                       "secchead"))
    hq = grp(HQ)
    rows.append(_row("Gastos de estructura . HQ", hq, periods, "detail"))
    rows.append(_pct_row("  % s/ ingresos", hq, ventas, periods))

    ebitda = (ventas.add(aprov, fill_value=0).add(pers, fill_value=0)
              .add(otros, fill_value=0).add(hq, fill_value=0))
    rows.append(_row("EBITDA", ebitda, periods, "ebitda"))
    rows.append(_pct_row("  % s/ ingresos (margen EBITDA)",
                         ebitda, ventas, periods, "pct"))

    rows.append(_blank("6. AMORTIZACIONES", periods, "secchead"))
    amort = grp(AMORT)
    rows.extend(detail_by_center(AMORT, "Amortizaciones"))
    ebit = ebitda.add(amort, fill_value=0)
    rows.append(_row("EBIT (EBITDA - Amortizaciones)", ebit, periods,
                     "subtotal"))
    rows.append(_pct_row("  % s/ ingresos", ebit, ventas, periods))

    rows.append(_blank("7. GASTOS FINANCIEROS", periods, "secchead"))
    rows.extend(detail_by_center(FIN, "Gastos financieros"))
    fin = grp(FIN)
    rows.append(_row("TOTAL GASTOS FINANCIEROS", fin, periods, "total"))
    rows.append(_pct_row("  % s/ ingresos", fin, ventas, periods))

    ebt = ebit.add(fin, fill_value=0)
    rows.append(_row("RESULTADO ANTES DE IMPUESTOS (EBT)", ebt, periods,
                     "subtotal"))
    rows.append(_pct_row("  % s/ ingresos", ebt, ventas, periods))

    ebt_year = {}
    for p in periods:
        ebt_year[p[:4]] = ebt_year.get(p[:4], 0.0) + float(ebt.get(p, 0.0))
    is_ser = {}
    for p in periods:
        if p[5:7] == "12":
            ann = ebt_year.get(p[:4], 0.0)
            is_ser[p] = round(-ann * is_rate / 100.0, 2) if ann > 0 else 0.0
        else:
            is_ser[p] = 0.0
    is_ser = pd.Series(is_ser)
    rows.append(_row("Impuesto sobre sociedades (%d%% anual, dic)"
                     % round(is_rate), is_ser, periods, "cost"))

    neto = ebt.add(is_ser, fill_value=0)
    rows.append(_row("RESULTADO NETO DEL EJERCICIO", neto, periods, "neto"))
    rows.append(_pct_row("  % s/ ingresos", neto, ventas, periods))

    return pd.DataFrame(rows).set_index("Concepto")
