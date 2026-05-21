"""Modelo de socios: altas, churn, ocupacion, estacionalidad e ingresos.

Recurrencia mes a mes (solo desde la apertura del centro):
  socios_inicio = socios_fin del mes anterior (0 el primer mes)
  bajas         = round((socios_inicio + altas) * churn% / 100)
                  (o bajas manuales si se indican)
  socios_fin    = max(0, socios_inicio + altas - bajas)
  ocupacion     = socios_fin / aforo
  indice        = estacionalidad del mes calendario (base 100)
  ingresos      = socios_fin * ticket * indice / 100
"""

from __future__ import annotations

from typing import Dict, List, Optional

CONCEPTS = [
    "Socios inicio mes", "Altas brutas", "Churn (%)", "Bajas",
    "Socios fin mes", "% Ocupacion (%)", "Indice estacionalidad",
    "Ingresos cuotas (EUR)",
]
EDITABLE = ["Altas brutas", "Churn (%)", "Bajas"]


def compute_trajectory(periods: List[str], opening: str,
                        aforo: float, ticket: float,
                        season12: List[float],
                        plan: Dict[str, dict]) -> List[dict]:
    """Devuelve una fila (dict) por periodo con todos los conceptos."""
    out = []
    socios_prev = 0
    for p in sorted(periods):
        mes = int(p[5:7])
        idx = float(season12[mes - 1]) if 1 <= mes <= 12 else 100.0
        if p < opening:
            out.append({"periodo": p, "Socios inicio mes": 0,
                        "Altas brutas": 0, "Churn (%)": 0.0, "Bajas": 0,
                        "Socios fin mes": 0, "% Ocupacion (%)": 0.0,
                        "Indice estacionalidad": idx,
                        "Ingresos cuotas (EUR)": None})
            continue
        pl = plan.get(p, {})
        altas = int(round(float(pl.get("altas", 0) or 0)))
        churn = float(pl.get("churn", 0) or 0)
        manual_bajas = float(pl.get("bajas", 0) or 0)
        inicio = socios_prev
        # Bajas manual (> 0) manda; si no, churn sobre (inicio + altas)
        if manual_bajas > 0:
            bajas = int(round(manual_bajas))
        else:
            bajas = int(round((inicio + altas) * churn / 100.0))
        fin = max(0, inicio + altas - bajas)
        ocup = (100.0 * fin / aforo) if aforo and aforo > 0 else 0.0
        ingresos = round(fin * ticket * idx / 100.0, 2)
        out.append({
            "periodo": p, "Socios inicio mes": inicio,
            "Altas brutas": altas, "Churn (%)": churn, "Bajas": bajas,
            "Socios fin mes": fin, "% Ocupacion (%)": round(ocup, 1),
            "Indice estacionalidad": idx,
            "Ingresos cuotas (EUR)": ingresos,
        })
        socios_prev = fin
    return out


def ingresos_por_periodo(periods: List[str], opening: str,
                         aforo: float, ticket: float,
                         season12: List[float],
                         plan: Dict[str, dict]) -> Dict[str, float]:
    """{periodo: ingreso_cuotas} (solo periodos con ingreso > 0)."""
    res = {}
    for row in compute_trajectory(periods, opening, aforo, ticket,
                                  season12, plan):
        v = row["Ingresos cuotas (EUR)"]
        if v:
            res[row["periodo"]] = float(v)
    return res
