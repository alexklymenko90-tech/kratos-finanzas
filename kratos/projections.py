"""Catalogo de partidas, proyeccion desde supuestos y mezcla real+proyectado."""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from . import config, pl_mapping

# Partida -> seccion (catalogo canonico, derivado del mapeo por defecto)
_INCOME = ["Cuotas socios", "ClassPass", "Urban Sports",
           "Venta de producto", "Otros ingresos"]
_EXPENSE = {
    "Coste de ventas": ["Aprovisionamientos", "Merchandising"],
    "Gastos de personal": ["Sueldos y salarios", "Seguridad Social",
                           "Otros gastos de personal"],
    "Gastos de explotacion": [
        "Alquiler", "Marketing", "Suministros - Electricidad",
        "Suministros - Agua", "Suministros - Telefonia/Internet",
        "Suministros - Otros", "Limpieza", "Servicios profesionales",
        "Seguros", "Servicios bancarios", "Software/Plataformas",
        "Renting", "Mantenimiento", "Tributos",
        "Otros gastos de explotacion"],
    "Resultado financiero": ["Intereses y gastos financieros"],
}

PARTIDA_SECCION: Dict[str, str] = {p: "Ingresos" for p in _INCOME}
for _sec, _ps in _EXPENSE.items():
    for _p in _ps:
        PARTIDA_SECCION[_p] = _sec

# Conceptos especiales del Excel de supuestos
SPECIALS = ["__capex__", "__capex_amort_anos__", "__hq_peso_pct__",
            "__lag_cobro__", "__lag_pago__"]
SPECIAL_LABELS = {
    "__capex__": "Inversion CAPEX prevista (importe)",
    "__capex_amort_anos__": "Anos de amortizacion del CAPEX",
    "__hq_peso_pct__": "Peso % de gastos HQ (0-100)",
    "__lag_cobro__": "Desfase de cobro (meses)",
    "__lag_pago__": "Desfase de pago (meses)",
}


def income_partidas() -> List[str]:
    return list(_INCOME)


def expense_sections() -> Dict[str, List[str]]:
    return {k: list(v) for k, v in _EXPENSE.items()}


def build_projected_pnl(assumptions: pd.DataFrame,
                        opening: Dict[str, str]) -> pd.DataFrame:
    """long df proyectado: centro, periodo, seccion, partida, valor.

    El cliente escribe ingresos y gastos en POSITIVO. Aqui los gastos se
    pasan a negativo (convencion de signo de la P&L).
    """
    cols = ["centro", "periodo", "seccion", "partida", "valor", "origen"]
    if assumptions is None or assumptions.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for _, r in assumptions.iterrows():
        concepto = r["concepto"]
        if concepto in SPECIALS or concepto not in PARTIDA_SECCION:
            continue
        c, periodo = r["centro"], r["periodo"]
        if periodo < (opening.get(c) or config.DEFAULT_START_MONTH):
            continue
        val = float(r["valor"] or 0.0)
        if val == 0:
            continue
        sec = PARTIDA_SECCION[concepto]
        if sec != "Ingresos":
            val = -abs(val)
        rows.append({"centro": c, "periodo": periodo, "seccion": sec,
                     "partida": concepto, "valor": round(val, 2),
                     "origen": "proyectado"})
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)


def blend(actual: pd.DataFrame, projected: pd.DataFrame,
          last_actual_period: str) -> pd.DataFrame:
    """Real hasta `last_actual_period` (incluido); proyectado despues."""
    parts = []
    if actual is not None and not actual.empty:
        parts.append(actual[actual["periodo"] <= last_actual_period])
    if projected is not None and not projected.empty:
        parts.append(projected[projected["periodo"] > last_actual_period])
    if not parts:
        return pd.DataFrame(
            columns=["centro", "periodo", "seccion", "partida", "valor", "origen"])
    return pd.concat(parts, ignore_index=True)


def weights_from_assumptions(assumptions: pd.DataFrame) -> Dict[tuple, float]:
    if assumptions is None or assumptions.empty:
        return {}
    w = assumptions[assumptions["concepto"] == "__hq_peso_pct__"]
    return {(r["centro"], r["periodo"]): float(r["valor"] or 0.0)
            for _, r in w.iterrows()}


def lags_from_assumptions(assumptions: pd.DataFrame) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    if assumptions is None or assumptions.empty:
        return out
    for key, name in (("__lag_cobro__", "cobro"), ("__lag_pago__", "pago")):
        sub = assumptions[assumptions["concepto"] == key]
        for c, grp in sub.groupby("centro"):
            vals = [int(round(float(v))) for v in grp["valor"] if pd.notna(v)]
            out.setdefault(c, {})[name] = vals[0] if vals else 0
    return out
