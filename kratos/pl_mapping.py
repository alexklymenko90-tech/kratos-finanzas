"""Mapeo cuenta -> (seccion, partida) para la P&L y el cash flow.

Dos niveles:
  seccion : subtotal del P&L (Ingresos, Coste de ventas, Personal, ...)
  partida : tipologia de detalle (Alquiler, Marketing, Limpieza, ...)

Se mapea por codigo exacto (8 digitos) si existe, si no por prefijo de 3.
La tabla es editable por el usuario (se persiste en SQLite). Las cuentas
nuevas que no encajen caen en la partida "Otros ..." y se marcan para que
el usuario las reclasifique.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

# Orden de secciones en el P&L
SECTION_ORDER = [
    "Ingresos",
    "Coste de ventas",
    "Gastos de personal",
    "Gastos de explotacion",
    "Amortizaciones",
    "Resultado financiero",
]

# Codigo exacto (8 digitos) -> (seccion, partida)
EXACT: Dict[str, Tuple[str, str]] = {
    "70000001": ("Ingresos", "Venta de producto"),
    "70000002": ("Ingresos", "Venta de producto"),
    "70500001": ("Ingresos", "Cuotas socios"),
    "70500002": ("Ingresos", "Cuotas socios"),
    "70500003": ("Ingresos", "ClassPass"),
    "70500004": ("Ingresos", "Urban Sports"),
    "60200001": ("Coste de ventas", "Merchandising"),
    "62700001": ("Gastos de explotacion", "Marketing"),
    "62700002": ("Gastos de explotacion", "Marketing"),
    "62810000": ("Gastos de explotacion", "Suministros - Electricidad"),
    "62820000": ("Gastos de explotacion", "Suministros - Agua"),
    "62840000": ("Gastos de explotacion", "Suministros - Telefonia/Internet"),
    "62900004": ("Gastos de explotacion", "Limpieza"),
    "62900007": ("Gastos de explotacion", "Software/Plataformas"),
    "62900008": ("Gastos de explotacion", "Software/Plataformas"),
    "62900009": ("Gastos de explotacion", "Renting"),
    "62300002": ("Gastos de explotacion", "Servicios profesionales"),
    "62600001": ("Gastos de explotacion", "Servicios bancarios"),
}

# Prefijo de 3 digitos -> (seccion, partida)
PREFIX3: Dict[str, Tuple[str, str]] = {
    "700": ("Ingresos", "Venta de producto"),
    "701": ("Ingresos", "Venta de producto"),
    "705": ("Ingresos", "Cuotas socios"),
    "759": ("Ingresos", "Otros ingresos"),
    "760": ("Resultado financiero", "Ingresos financieros"),
    "769": ("Resultado financiero", "Ingresos financieros"),
    "600": ("Coste de ventas", "Aprovisionamientos"),
    "601": ("Coste de ventas", "Aprovisionamientos"),
    "602": ("Coste de ventas", "Aprovisionamientos"),
    "607": ("Coste de ventas", "Aprovisionamientos"),
    "640": ("Gastos de personal", "Sueldos y salarios"),
    "641": ("Gastos de personal", "Otros gastos de personal"),
    "642": ("Gastos de personal", "Seguridad Social"),
    "649": ("Gastos de personal", "Otros gastos de personal"),
    "621": ("Gastos de explotacion", "Alquiler"),
    "622": ("Gastos de explotacion", "Mantenimiento"),
    "623": ("Gastos de explotacion", "Servicios profesionales"),
    "624": ("Gastos de explotacion", "Otros gastos de explotacion"),
    "625": ("Gastos de explotacion", "Seguros"),
    "626": ("Gastos de explotacion", "Servicios bancarios"),
    "627": ("Gastos de explotacion", "Marketing"),
    "628": ("Gastos de explotacion", "Suministros - Otros"),
    "629": ("Gastos de explotacion", "Otros gastos de explotacion"),
    "631": ("Gastos de explotacion", "Tributos"),
    "634": ("Gastos de explotacion", "Tributos"),
    "659": ("Gastos de explotacion", "Otros gastos de explotacion"),
    "680": ("Amortizaciones", "Amortizacion inmovilizado"),
    "681": ("Amortizaciones", "Amortizacion inmovilizado"),
    "662": ("Resultado financiero", "Intereses y gastos financieros"),
    "663": ("Resultado financiero", "Intereses y gastos financieros"),
    "666": ("Resultado financiero", "Intereses y gastos financieros"),
    "669": ("Resultado financiero", "Intereses y gastos financieros"),
}

# Partida sintetica (no viene de Holded, la calcula pnl.py)
PARTIDA_HQ = "Imputacion gastos HQ"
PARTIDA_AMORT_CAPEX = "Amortizacion CAPEX"


def _fallback(cuenta: str) -> Tuple[str, str]:
    if cuenta[:1] == "7":
        return ("Ingresos", "Otros ingresos")
    if cuenta[:1] == "6":
        return ("Gastos de explotacion", "Otros gastos de explotacion")
    return ("Gastos de explotacion", "Otros gastos de explotacion")


def default_mapping_rows() -> List[dict]:
    """Filas semilla para la tabla editable `pl_mapping` (clave + seccion + partida)."""
    rows = []
    for code, (sec, par) in EXACT.items():
        rows.append({"clave": code, "tipo_clave": "codigo",
                     "seccion": sec, "partida": par})
    for pref, (sec, par) in PREFIX3.items():
        rows.append({"clave": pref, "tipo_clave": "prefijo3",
                     "seccion": sec, "partida": par})
    return rows


def build_lookups(mapping_rows: List[dict]):
    exact, pref = {}, {}
    for r in mapping_rows:
        key = str(r["clave"]).strip()
        val = (r["seccion"], r["partida"])
        if r.get("tipo_clave") == "codigo":
            exact[key] = val
        else:
            pref[key] = val
    return exact, pref


def apply_mapping(df: pd.DataFrame, mapping_rows: List[dict]) -> pd.DataFrame:
    """Anade columnas `seccion`, `partida`, `cuenta_sin_mapear` a las filas
    OPERATIVO. El resto de filas quedan con seccion/partida vacias."""
    exact, pref = build_lookups(mapping_rows or default_mapping_rows())
    df = df.copy()
    secs, pars, unmapped = [], [], []
    for cuenta, p3, tipo_mov in zip(df["cuenta"], df["prefijo3"], df["tipo_mov"]):
        if tipo_mov != "OPERATIVO":
            secs.append("")
            pars.append("")
            unmapped.append(False)
            continue
        if cuenta in exact:
            s, p = exact[cuenta]
            secs.append(s); pars.append(p); unmapped.append(False)
        elif p3 in pref:
            s, p = pref[p3]
            secs.append(s); pars.append(p); unmapped.append(False)
        else:
            s, p = _fallback(cuenta)
            secs.append(s); pars.append(p); unmapped.append(True)
    df["seccion"] = secs
    df["partida"] = pars
    df["cuenta_sin_mapear"] = unmapped
    return df


def unmapped_accounts(df: pd.DataFrame) -> pd.DataFrame:
    """Cuentas operativas que cayeron en fallback -> el usuario debe mapearlas."""
    sub = df[(df["tipo_mov"] == "OPERATIVO") & (df["cuenta_sin_mapear"])]
    if sub.empty:
        return pd.DataFrame(columns=["cuenta", "cuenta_nombre", "filas", "importe"])
    g = (sub.groupby(["cuenta", "cuenta_nombre"])
            .agg(filas=("importe", "size"), importe=("importe", "sum"))
            .reset_index()
            .sort_values("importe", key=lambda s: s.abs(), ascending=False))
    return g
