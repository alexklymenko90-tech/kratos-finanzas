"""Construye la P&L real por centro / mes / partida.

Convencion de signo:  valor = haber - debe
  -> Ingresos (7xx, al haber)  => valor positivo
  -> Gastos   (6xx, al debe)   => valor negativo
  -> Resultado neto = suma de todos los valores

Incluye el prorrateo de los gastos de HQ a K1..K4 (linea sintetica
"Imputacion gastos HQ") y la amortizacion del CAPEX como supuesto.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from . import config, pl_mapping


def month_range(start: str, n: int) -> List[str]:
    y, m = int(start[:4]), int(start[5:7])
    out = []
    for _ in range(n):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _is_open(centro: str, periodo: str, opening: Dict[str, str]) -> bool:
    ap = opening.get(centro) or config.DEFAULT_START_MONTH
    return periodo >= ap


def build_actual_pnl(df: pd.DataFrame, opening: Dict[str, str]) -> pd.DataFrame:
    """Devuelve long df: centro, periodo, seccion, partida, valor, origen."""
    op = df[(df["tipo_mov"] == "OPERATIVO") & df["periodo"].notna()
            & df["centro"].isin(config.ALL_CENTERS)].copy()
    if op.empty:
        return pd.DataFrame(
            columns=["centro", "periodo", "seccion", "partida", "valor", "origen"])
    op = op[[_is_open(c, p, opening)
             for c, p in zip(op["centro"], op["periodo"])]]
    op["valor"] = op["haber"] - op["debe"]
    g = (op.groupby(["centro", "periodo", "seccion", "partida"], as_index=False)
           ["valor"].sum())
    g["valor"] = g["valor"].round(2)
    g["origen"] = "real"
    return g


def _revenue_by_center_period(pnl_long: pd.DataFrame) -> Dict[tuple, float]:
    inc = pnl_long[pnl_long["seccion"] == "Ingresos"]
    s = inc.groupby(["centro", "periodo"])["valor"].sum()
    return {k: float(v) for k, v in s.items()}


def hq_proration(pnl_long: pd.DataFrame,
                  opening: Dict[str, str],
                  weights: Optional[Dict[tuple, float]] = None,
                  key: str = config.PRORATION_KEY) -> pd.DataFrame:
    """Anade la linea 'Imputacion gastos HQ' a cada K (valor negativo) por
    cada periodo en que HQ tiene gasto. HQ conserva su P&L completa, asi que
    en el consolidado las imputaciones netean con el gasto de HQ.

    weights: {(centro, periodo): pct 0..100} si key == 'manual'.
    """
    if pnl_long.empty:
        return pnl_long
    hq = pnl_long[pnl_long["centro"] == config.HQ]
    if hq.empty:
        return pnl_long

    hq_cost = hq.groupby("periodo")["valor"].sum()  # negativo (gasto)
    rev = _revenue_by_center_period(pnl_long) if key == "revenue" else None
    extra = []
    for periodo, total in hq_cost.items():
        if round(total, 2) == 0:
            continue
        open_k = [c for c in config.CENTERS if _is_open(c, periodo, opening)]
        if not open_k:
            continue
        w = {}
        if key == "manual" and weights:
            w = {c: max(0.0, float(weights.get((c, periodo), 0.0)))
                 for c in open_k}
        elif key == "revenue" and rev:
            w = {c: max(0.0, rev.get((c, periodo), 0.0)) for c in open_k}
        if not w or sum(w.values()) <= 0:
            w = {c: 1.0 for c in open_k}          # guarda: reparto igualitario
        tot = sum(w.values())
        for c in open_k:
            share = total * (w[c] / tot)          # total<0 -> share<0 (gasto)
            if round(share, 2) == 0:
                continue
            extra.append({
                "centro": c, "periodo": periodo,
                "seccion": "Gastos de explotacion",
                "partida": pl_mapping.PARTIDA_HQ,
                "valor": round(share, 2), "origen": "imputacion",
            })
    if not extra:
        return pnl_long
    return pd.concat([pnl_long, pd.DataFrame(extra)], ignore_index=True)


def capex_amortization(df_ledger: pd.DataFrame,
                       assumptions: pd.DataFrame,
                       opening: Dict[str, str],
                       horizon: List[str]) -> pd.DataFrame:
    """Amortizacion lineal del CAPEX (real + proyectado) por centro.

    Base CAPEX = inversion real (tipo_mov==CAPEX) + CAPEX proyectado del
    Excel de supuestos (concepto '__capex__'). Cuota mensual = base /
    (anos * 12), desde el mes de apertura del centro.
    """
    rows = []
    # CAPEX real por centro
    capex_real = {}
    cx = df_ledger[df_ledger["tipo_mov"] == "CAPEX"]
    if not cx.empty:
        for c, v in cx.groupby("centro")["importe"].sum().items():
            if c in config.CENTERS:
                capex_real[c] = capex_real.get(c, 0.0) + float(v)
    # CAPEX proyectado y anos de amortizacion (de supuestos)
    capex_proj, anos = {}, {}
    if assumptions is not None and not assumptions.empty:
        for _, r in assumptions.iterrows():
            if r["concepto"] == "__capex__":
                capex_proj[r["centro"]] = capex_proj.get(r["centro"], 0.0) + float(r["valor"] or 0)
            elif r["concepto"] == "__capex_amort_anos__":
                anos[r["centro"]] = float(r["valor"] or 0)
    for c in config.CENTERS:
        base = capex_real.get(c, 0.0) + capex_proj.get(c, 0.0)
        yrs = anos.get(c, 0.0)
        if base <= 0 or yrs <= 0:
            continue
        months = int(round(yrs * 12))
        cuota = round(base / months, 2)
        ap = opening.get(c) or config.DEFAULT_START_MONTH
        cnt = 0
        for periodo in horizon:
            if periodo < ap or cnt >= months:
                continue
            rows.append({
                "centro": c, "periodo": periodo,
                "seccion": "Amortizaciones",
                "partida": pl_mapping.PARTIDA_AMORT_CAPEX,
                "valor": -cuota, "origen": "amortizacion",
            })
            cnt += 1
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["centro", "periodo", "seccion", "partida", "valor", "origen"])


# Subtotales del P&L (signo ya incorporado: ingresos +, gastos -)
SUBTOTALS = [
    ("Ingresos", ["Ingresos"]),
    ("Coste de ventas", ["Coste de ventas"]),
    ("Margen bruto", ["Ingresos", "Coste de ventas"]),
    ("Gastos de personal", ["Gastos de personal"]),
    ("Gastos de explotacion", ["Gastos de explotacion"]),
    ("EBITDA", ["Ingresos", "Coste de ventas",
                "Gastos de personal", "Gastos de explotacion"]),
    ("Amortizaciones", ["Amortizaciones"]),
    ("Resultado financiero", ["Resultado financiero"]),
    ("Resultado neto", pl_mapping.SECTION_ORDER),
]


def pivot_pnl(pnl_long: pd.DataFrame, centro: str,
              periods: List[str]) -> pd.DataFrame:
    """Tabla P&L lista para mostrar: filas = secciones/partidas/subtotales,
    columnas = meses. Para `centro` (o 'CONSOLIDADO')."""
    if centro == "CONSOLIDADO":
        data = pnl_long.copy()
    else:
        data = pnl_long[pnl_long["centro"] == centro].copy()
    out_rows = []
    for sec in pl_mapping.SECTION_ORDER:
        sub = data[data["seccion"] == sec]
        if sub.empty:
            continue
        for partida in sorted(sub["partida"].unique()):
            ser = (sub[sub["partida"] == partida]
                   .groupby("periodo")["valor"].sum())
            out_rows.append(_row("  " + partida, ser, periods))
        ser_sec = sub.groupby("periodo")["valor"].sum()
        out_rows.append(_row(sec.upper(), ser_sec, periods, bold=True))
    # subtotales clave
    for label, secs in [("MARGEN BRUTO", ["Ingresos", "Coste de ventas"]),
                        ("EBITDA", ["Ingresos", "Coste de ventas",
                                    "Gastos de personal", "Gastos de explotacion"]),
                        ("RESULTADO NETO", pl_mapping.SECTION_ORDER)]:
        ser = data[data["seccion"].isin(secs)].groupby("periodo")["valor"].sum()
        out_rows.append(_row(label, ser, periods, bold=True))
    return pd.DataFrame(out_rows).set_index("Concepto")


def _row(name: str, ser: pd.Series, periods: List[str], bold: bool = False):
    d = {"Concepto": name}
    for p in periods:
        d[p] = round(float(ser.get(p, 0.0)), 2)
    return d
