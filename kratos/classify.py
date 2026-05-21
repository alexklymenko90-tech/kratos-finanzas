"""Clasifica cada apunte por centro y por tipo de movimiento.

centro      : K1..K4 / HQ / "Sin asignar"
tipo_mov    : OPERATIVO  -> entra en la P&L (6xx gasto / 7xx ingreso)
              CAPEX       -> inversion (cuentas de activo 2xx). Fuera de P&L.
              FINANCIACION-> deuda (16x/17x). Fuera de P&L.
              TESORERIA   -> caja/bancos (57x). Base del cash flow.
              BALANCE     -> proveedores/IVA/clientes... Fuera de P&L.
center_method: como se asigno el centro (para el panel de calidad).
"""

from __future__ import annotations

import pandas as pd

from . import config


# Tags OPERATIVOS de centro: los unicos que cuentan para el flag
# multi_tag. Los cortos (k1..k4) marcan CAPEX/activos de ese K y no
# generan aviso de multi-tag aunque coexistan con uno operativo.
_OPERATIONAL_TAGS = {"localparis", "localsantjoan", "localpoblenou",
                     "localvalencia", "hq"}


def _center_from_tags(tags):
    found = []
    for t in tags:
        c = config.TAG_TO_CENTER.get(t)
        if c and c not in found:
            found.append(c)
    if not found:
        return None, False
    # multi_tag = True solo si la misma linea trae >1 tag OPERATIVO
    op_centers = {config.TAG_TO_CENTER[t] for t in tags
                  if t in _OPERATIONAL_TAGS}
    is_multi = len(op_centers) > 1
    for c in config.CENTER_PRECEDENCE:
        if c in found:
            return c, is_multi
    return found[0], is_multi


def _center_from_name(nombre_norm: str):
    for kw, c in config.ACCOUNT_NAME_KEYWORDS:
        if kw in nombre_norm:
            return c
    return None


def _tipo_mov(cuenta: str, p2: str, p3: str) -> str:
    if p2 in config.CAPEX_PREFIXES:
        return "CAPEX"
    if p2 in config.FINANCING_PREFIXES or p3 in config.FINANCING_PREFIXES:
        return "FINANCIACION"
    if p3 in config.TREASURY_PREFIXES:
        return "TESORERIA"
    if cuenta[:1] in ("6", "7"):
        return "OPERATIVO"
    return "BALANCE"


def _propagate_bank_center(df: pd.DataFrame) -> pd.DataFrame:
    """Hereda el centro desde la cuenta bancaria del mismo asiento.

    Si un apunte operativo o CAPEX queda 'Sin asignar' tras las reglas
    anteriores y en su mismo asiento hay una cuenta de tesoreria 572xx
    cuyo centro conocemos (config.ACCOUNT_CODE_TO_CENTER), asumimos
    que el gasto/inversion va al centro del banco que lo pago.
    Solo se aplica cuando todos los bancos del asiento apuntan al mismo
    centro (si hay conflicto se deja Sin asignar para revision manual).
    """
    asi_bank = {}
    for asi, cuenta in zip(df["asiento"], df["cuenta"]):
        cs = str(cuenta)
        if cs.startswith("572"):
            c = config.ACCOUNT_CODE_TO_CENTER.get(cs)
            if c:
                asi_bank.setdefault(int(asi), set()).add(c)
    if not asi_bank:
        return df
    df = df.copy()
    col_centro = df.columns.get_loc("centro")
    col_method = df.columns.get_loc("center_method")
    for i, (asi, c, tm) in enumerate(
            zip(df["asiento"], df["centro"], df["tipo_mov"])):
        if c != config.UNASSIGNED:
            continue
        if tm not in ("OPERATIVO", "CAPEX"):
            continue
        cs = asi_bank.get(int(asi))
        if cs and len(cs) == 1:
            df.iat[i, col_centro] = next(iter(cs))
            df.iat[i, col_method] = "banco_asiento"
    return df


def classify(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    centros, methods, multi = [], [], []
    for tags, code, nombre_norm, p2, p3 in zip(
        df["tags"], df["cuenta"], df["nombre_norm"], df["prefijo2"], df["prefijo3"]
    ):
        c, is_multi = _center_from_tags(tags)
        method = "tag"
        if c is None:
            c = config.ACCOUNT_CODE_TO_CENTER.get(code)
            method = "cuenta" if c else method
        if c is None:
            c = _center_from_name(nombre_norm)
            method = "nombre" if c else method
        if c is None:
            c, method = config.UNASSIGNED, "sin_asignar"
        centros.append(c)
        methods.append(method)
        multi.append(is_multi)

    df["centro"] = centros
    df["center_method"] = methods
    df["multi_tag"] = multi
    df["tipo_mov"] = [
        _tipo_mov(c, p2, p3)
        for c, p2, p3 in zip(df["cuenta"], df["prefijo2"], df["prefijo3"])
    ]
    df = _propagate_bank_center(df)
    return df


def apply_overrides(df: pd.DataFrame, overrides: dict) -> pd.DataFrame:
    """overrides: {(asiento, linea): centro} -> fuerza el centro manualmente."""
    if not overrides:
        return df
    df = df.copy()
    for i, (a, l) in enumerate(zip(df["asiento"], df["linea"])):
        key = (int(a), int(l))
        if key in overrides:
            df.iat[i, df.columns.get_loc("centro")] = overrides[key]
            df.iat[i, df.columns.get_loc("center_method")] = "manual"
    return df


def coverage(df: pd.DataFrame) -> dict:
    """Metricas de calidad de la clasificacion (para mostrar tras la carga)."""
    op = df[df["tipo_mov"] == "OPERATIVO"]
    n_op = len(op)
    sin = int((op["centro"] == config.UNASSIGNED).sum()) if n_op else 0
    return {
        "filas_total": len(df),
        "operativas": n_op,
        "operativas_sin_centro": sin,
        "cobertura_pct": round(100.0 * (n_op - sin) / n_op, 1) if n_op else 100.0,
        "capex_filas": int((df["tipo_mov"] == "CAPEX").sum()),
        "capex_importe": round(float(
            df.loc[df["tipo_mov"] == "CAPEX", "importe"].sum()), 2),
        "multi_tag": int(df["multi_tag"].sum()),
        "por_centro": df.groupby("centro").size().to_dict(),
    }
