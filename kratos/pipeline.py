"""Orquesta la carga semanal y la construccion del modelo (P&L + cash flow).

app.py llama a estas dos funciones; toda la logica vive en los modulos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from . import (analytic, cashflow, classify, config, ingest, pl_mapping,
               pnl, projections, socios, store)


@dataclass
class UploadSummary:
    n_rows: int
    sum_debe: float
    sum_haber: float
    cuadra: bool
    date_min: str
    date_max: str
    dropped_rows: int
    coverage: dict
    unmapped: pd.DataFrame


def process_upload(path: str, source_name: Optional[str] = None,
                   replace: bool = True) -> UploadSummary:
    store.init_db()
    res = ingest.load_ledger(path, source_name=source_name)
    df = classify.classify(res.df)
    df = classify.apply_overrides(df, store.load_overrides())
    df = pl_mapping.apply_mapping(df, store.load_mapping())

    if not replace:
        prev = store.load_ledger()
        if prev is not None:
            df = pd.concat([prev, df], ignore_index=True)
    store.save_ledger(df)

    # Aprender el IVA y la retencion realmente observados por partida
    # desde el libro diario (override del default de la P&L para la proyeccion).
    store.set_meta("learned_tax", _learn_tax(df))

    cov = classify.coverage(df)
    store.set_meta("last_file", source_name or path.rsplit("/", 1)[-1])
    store.set_meta("date_min", str(res.date_min))
    store.set_meta("date_max", str(res.date_max))
    store.set_meta("last_actual_period",
                   "%04d-%02d" % (res.date_max.year, res.date_max.month)
                   if res.date_max else None)
    store.set_meta("replace_mode", replace)

    return UploadSummary(
        n_rows=res.n_rows, sum_debe=res.sum_debe, sum_haber=res.sum_haber,
        cuadra=abs(res.sum_debe - res.sum_haber) < 0.5,
        date_min=str(res.date_min), date_max=str(res.date_max),
        dropped_rows=res.dropped_rows, coverage=cov,
        unmapped=pl_mapping.unmapped_accounts(df),
    )


@dataclass
class Model:
    pnl_long: pd.DataFrame        # columnas: centro,periodo,apartado,partida,valor,origen
    cf_long: pd.DataFrame
    periods: list                 # union (para Consolidado y generacion)
    center_periods: dict          # ventana propia de cada centro
    last_actual_period: Optional[str]
    opening: dict


def _learn_tax(df) -> dict:
    """Calcula IVA% y retencion% medios observados por partida a partir
    del libro diario. Para cada asiento, identifica la partida operativa
    dominante (linea 6xx/7xx con mayor |importe|) y suma el IVA (472/477)
    y la retencion (4751x) del asiento contra el neto. Devuelve
    {partida: {'iva': %, 'ret': %}} solo para partidas canonicas."""
    out = {}
    if df is None or df.empty:
        return out
    accum = {}
    for asi, g in df.groupby("asiento"):
        ops = g[g["tipo_mov"] == "OPERATIVO"]
        if ops.empty:
            continue
        idx = ops["importe"].abs().idxmax()
        cuenta = str(ops.at[idx, "cuenta"])
        p3 = str(ops.at[idx, "prefijo3"])
        m = analytic.map_real_account(cuenta, p3)
        if not m:
            continue
        partida = m[1]
        if partida not in config.PARTIDA_TAX:
            continue
        neto = float(ops["importe"].abs().sum())
        if neto <= 0:
            continue
        cuentas = g["cuenta"].astype(str)
        iva = float(g[cuentas.str.startswith(("472", "477"))]
                     ["importe"].abs().sum())
        ret = float(g[cuentas.str.startswith("4751")]
                     ["importe"].abs().sum())
        d = accum.setdefault(partida, {"iva": 0.0, "ret": 0.0, "neto": 0.0})
        d["iva"] += iva
        d["ret"] += ret
        d["neto"] += neto
    for partida, d in accum.items():
        if d["neto"] > 0:
            out[partida] = {
                "iva": round(100.0 * d["iva"] / d["neto"], 1),
                "ret": round(100.0 * d["ret"] / d["neto"], 1),
            }
    return out


_REVENUE_PARTIDAS = {"Cuotas", "Productos", "ClassPass Spain",
                     "Urban Sports Club", "Otros ingresos"}


def effective_tax(partida: str, learned: dict = None) -> dict:
    """Tipo IVA/retencion efectivo para una partida.
    Reglas:
      - Ingresos (ventas): IVA fijo 21% (no se aprende).
      - Arrendamiento local: retencion fija 19% (no se aprende).
      - Resto: aprendido del libro diario si existe; si no, default."""
    default = config.PARTIDA_TAX.get(partida, {"iva": 0, "ret": 0})
    L = (learned or {}).get(partida)
    if not L:
        return default
    iva = default["iva"] if partida in _REVENUE_PARTIDAS else L["iva"]
    ret = default["ret"] if partida == "Arrendamiento local" else L["ret"]
    return {"iva": iva, "ret": ret}


_APCOLS = ["centro", "periodo", "apartado", "partida", "valor", "origen"]


def _agg(rows):
    if not rows:
        return pd.DataFrame(columns=_APCOLS)
    d = pd.DataFrame(rows)
    g = (d.groupby(["centro", "periodo", "apartado", "partida"],
                   as_index=False)["valor"].sum())
    g["valor"] = g["valor"].round(2)
    g["origen"] = "x"
    return g[_APCOLS]


def _real_apartado(df, opening, last_actual):
    op = df[(df["tipo_mov"] == "OPERATIVO") & df["periodo"].notna()
            & df["centro"].isin(config.ALL_CENTERS)]
    rows = []
    for cuenta, p3, centro, periodo, debe, haber in zip(
            op["cuenta"], op["prefijo3"], op["centro"],
            op["periodo"], op["debe"], op["haber"]):
        if periodo > last_actual:
            continue
        if periodo < (opening.get(centro) or config.DEFAULT_START_MONTH):
            continue
        m = analytic.map_real_account(str(cuenta), str(p3))
        if m is None:
            continue
        apartado, sub = m            # etiqueta canonica del mapeo
        rows.append({"centro": centro, "periodo": periodo,
                     "apartado": apartado, "partida": sub,
                     "valor": haber - debe})
    return _agg(rows)


def _proj_apartado(assumptions, opening, center_periods, last_actual,
                   bundle=None):
    """`bundle` (opcional): dict pre-cargado con center_params, season,
    socios_plan, personal, gastos_plan por centro. Si se pasa, evita
    las llamadas individuales a store (1 query Frankfurt por cada una)."""
    rows = []
    rev_cli = set()
    base = projections.build_projected_pnl(assumptions, opening)
    if base is not None and not base.empty:
        for r in base.itertuples(index=False):
            if r.periodo <= last_actual:
                continue
            if r.seccion == "Ingresos":
                ap, part = config.VENTAS, analytic.proj_revenue_partida(
                    r.partida)
                rev_cli.add((r.centro, r.periodo, part))
            else:
                ap = analytic.seccion_to_apartado(r.seccion)
                part = r.partida or ap
            rows.append({"centro": r.centro, "periodo": r.periodo,
                         "apartado": ap, "partida": part,
                         "valor": float(r.valor)})

    for c in config.CENTERS:
        win = sorted(center_periods.get(c, []))
        if not win:
            continue
        ap_open = opening.get(c) or config.DEFAULT_START_MONTH

        if bundle is not None:
            params = bundle["center_params"].get(c, {"aforo": 0, "ticket": 0})
            season_c = bundle["season"].get(c, [100.0] * 12)
            socios_plan_c = bundle["socios_plan"].get(c, {})
            pers = bundle["personal"].get(c, {"rows": []})
            gastos_c = bundle["gastos_plan"].get(c, [])
        else:
            params = store.get_center_params(c)
            season_c = store.get_season(c)
            socios_plan_c = store.get_socios_plan(c)
            pers = store.get_personal(c)
            gastos_c = store.get_gastos_plan(c)

        # Ingresos: modelo de socios -> Ventas / Cuotas
        ingresos = socios.ingresos_por_periodo(
            win, ap_open, float(params.get("aforo", 0) or 0),
            float(params.get("ticket", 0) or 0),
            season_c, socios_plan_c)
        for periodo, val in ingresos.items():
            if (periodo <= last_actual or periodo < ap_open or val <= 0
                    or (c, periodo, "Cuotas") in rev_cli):
                continue
            rows.append({"centro": c, "periodo": periodo,
                         "apartado": config.VENTAS, "partida": "Cuotas",
                         "valor": round(val, 2)})

        # Personal: TODO el personal -> "Gastos de Personal"
        for person in pers["rows"]:
            b = float(person["bruto"] or 0)
            if b <= 0:
                continue
            ss = float(person.get("ss_pct", 0) or 0) / 100.0
            apdo = "Gastos de Personal"
            etiqueta = (str(person.get("nombre") or "").strip()
                        or str(person["rol"]))
            mi = int(person["mes_inicio"] or 1)
            anchor = next((i for i, p in enumerate(win)
                           if int(p[5:7]) == mi), 0)
            for p in win[anchor:]:
                if p <= last_actual or p < ap_open:
                    continue
                rows.append({"centro": c, "periodo": p, "apartado": apdo,
                             "partida": etiqueta, "valor": -abs(b)})
                if ss:
                    # SS agregada (no por empleado): una sola linea total
                    rows.append({"centro": c, "periodo": p,
                                 "apartado": apdo,
                                 "partida": "Seguridad Social",
                                 "valor": -abs(b * ss)})

        # Tabla maestra de gastos -> apartado elegido
        for g in gastos_c:
            imp = float(g["importe"] or 0)
            if imp <= 0:
                continue
            itv = max(1, int(g["intervalo"] or 1))
            mi = int(g["mes_inicio"] or 1)
            apdo = g["apartado"] or config.COST_APARTADOS[0]
            anchor = next((i for i, p in enumerate(win)
                           if int(p[5:7]) == mi), 0)
            etiqueta = str(g.get("partida") or apdo).strip() or apdo
            for i in range(anchor, len(win), itv):
                p = win[i]
                if p <= last_actual or p < ap_open:
                    continue
                rows.append({"centro": c, "periodo": p, "apartado": apdo,
                             "partida": etiqueta, "valor": -abs(imp)})
    return _agg(rows)


def _hq_proration(long_df, opening, weights, key):
    """Reparte el coste de HQ a K1..K4 como 'Costes Indirectos' y mete en
    HQ una recuperacion para que el consolidado netee."""
    if long_df.empty:
        return long_df
    hq = long_df[long_df["centro"] == config.HQ]
    if hq.empty:
        return long_df
    hq_tot = hq.groupby("periodo")["valor"].sum()
    extra = []
    for periodo, total in hq_tot.items():
        if round(total, 2) == 0:
            continue
        open_k = [c for c in config.CENTERS
                  if periodo >= (opening.get(c) or config.DEFAULT_START_MONTH)]
        if not open_k:
            continue
        if key == "manual" and weights:
            w = {c: max(0.0, float(weights.get((c, periodo), 0.0)))
                 for c in open_k}
        else:
            w = {}
        if not w or sum(w.values()) <= 0:
            w = {c: 1.0 for c in open_k}
        tot = sum(w.values())
        for c in open_k:
            extra.append({"centro": c, "periodo": periodo,
                          "apartado": "Gastos de estructura HQ",
                          "partida": "Gastos de estructura · HQ",
                          "valor": round(total * w[c] / tot, 2),
                          "origen": "imputacion"})
        extra.append({"centro": config.HQ, "periodo": periodo,
                       "apartado": "Gastos de estructura HQ",
                       "partida": "Recuperacion imputacion HQ",
                       "valor": round(-total, 2), "origen": "imputacion"})
    if not extra:
        return long_df
    return pd.concat([long_df, pd.DataFrame(extra)], ignore_index=True)


def _preload_bundle(engine):
    """Prefetcha en UNA sola conexion toda la configuracion necesaria
    para build_model. Reduce 25+ roundtrips a Postgres a 6 queries en
    una conexion. Devuelve dict con center_params/model_config/season/
    socios_plan/personal/gastos_plan por centro + meta + opening_months."""
    from sqlalchemy import text as _text
    cm, sp, pp, gp, m_rows, c_rows = {}, {}, {}, {}, [], []
    with engine.begin() as conn:
        cm_rows = conn.execute(_text(
            "SELECT centro, clave, valor FROM center_model")).fetchall()
        sp_rows = conn.execute(_text(
            "SELECT centro, periodo, altas, churn, bajas "
            "FROM socios_plan")).fetchall()
        pp_rows = conn.execute(_text(
            "SELECT centro, rol, nombre, bruto, mes_inicio, destino, "
            "ss_pct FROM personal_plan")).fetchall()
        gp_rows = conn.execute(_text(
            "SELECT centro, idx, partida, importe, intervalo, apartado, "
            "mes_inicio FROM gastos_plan ORDER BY centro, idx")).fetchall()
        m_rows = conn.execute(_text(
            "SELECT clave, valor FROM meta")).fetchall()
        c_rows = conn.execute(_text(
            "SELECT centro, label, apertura FROM centers")).fetchall()

    # Re-agrupar por centro
    raw_by_centro = {}
    for centro, clave, valor in cm_rows:
        raw_by_centro.setdefault(centro, {})[clave] = valor

    center_params = {}
    center_model_config = {}
    season = {}
    for centro, raw in raw_by_centro.items():
        # center_params (iva/aforo/ticket/saldo_inicial)
        cp = {"iva": 21.0, "aforo": 0.0, "ticket": 0.0}
        cp.update(raw)
        center_params[centro] = cp
        # center_model_config
        cmc = {"start_month": 1, "start_year": 2026,
               "horizon": config.HORIZON_MONTHS}
        for k, v in raw.items():
            if k in cmc:
                cmc[k] = int(v)
        center_model_config[centro] = cmc
        # season
        s = [100.0] * 12
        for k, v in raw.items():
            if isinstance(k, str) and k.startswith("season_"):
                try:
                    i = int(k.split("_")[1])
                    if 1 <= i <= 12:
                        s[i - 1] = float(v)
                except (ValueError, IndexError):
                    pass
        season[centro] = s

    for centro, per, a, c, b in sp_rows:
        sp.setdefault(centro, {})[per] = {
            "altas": a or 0, "churn": c or 0, "bajas": b or 0}

    pers_raw = {}
    for centro, rol, nombre, bruto, mi, dest, ssp in pp_rows:
        pers_raw.setdefault(centro, {})[rol] = (rol, nombre, bruto,
                                                mi, dest, ssp)
    personal = {}
    for centro, saved in pers_raw.items():
        out = []
        for rol in config.PERSONAL_ROLES:
            r = saved.get(rol)
            dest = (r[4] if r and len(r) > 4 and r[4] else None)
            if not dest:
                dest = ("Sueldos Directos"
                        if str(rol).startswith("Entrenador")
                        else "Sueldos Indirectos")
            ssp = (r[5] if r and len(r) > 5 and r[5] is not None else None)
            out.append({
                "rol": rol,
                "nombre": (r[1] if r and r[1] is not None else "") or "",
                "bruto": float(r[2]) if r and r[2] is not None else 0.0,
                "mes_inicio": int(r[3]) if r and r[3] is not None else 1,
                "destino": dest,
                "ss_pct": float(ssp) if ssp is not None
                else float(config.SS_PCT_DEFAULT)})
        personal[centro] = {"rows": out}

    for centro, idx, partida, imp, itv, ap, mi in gp_rows:
        gp.setdefault(centro, []).append({
            "partida": partida or "",
            "importe": float(imp or 0),
            "intervalo": int(itv or 1),
            "apartado": ap or config.COST_APARTADOS[0],
            "mes_inicio": int(mi or 1)})

    import json as _json
    meta = {}
    for clave, valor in m_rows:
        try:
            meta[clave] = _json.loads(valor)
        except (ValueError, TypeError):
            meta[clave] = valor

    opening_months = {c: a for c, _, a in c_rows}

    return {
        "center_params": center_params,
        "center_model_config": center_model_config,
        "season": season,
        "socios_plan": sp,
        "personal": personal,
        "gastos_plan": gp,
        "meta": meta,
        "opening_months": opening_months,
    }


def build_model(ledger_df=None) -> Optional[Model]:
    """`ledger_df` permite pasar el ledger ya cargado (cacheado por la
    app) para no re-hacer la query de ~3.500 filas a Supabase en cada
    edicion de Tabla de mando (~1.1s ahorrados)."""
    df = ledger_df if ledger_df is not None else store.load_ledger()
    if df is None:
        return None
    engine = store.get_engine()
    bundle = _preload_bundle(engine)
    assumptions = store.load_assumptions()

    opening = bundle["opening_months"]
    last_actual = bundle["meta"].get("last_actual_period")
    if not last_actual:
        valid = [p for p in df["periodo"].dropna().unique()]
        last_actual = max(valid) if valid else config.DEFAULT_START_MONTH

    center_periods = {}
    for c in config.ALL_CENTERS:
        mc = bundle["center_model_config"].get(
            c, {"start_month": 1, "start_year": 2026,
                "horizon": config.HORIZON_MONTHS})
        center_periods[c] = pnl.month_range(
            "%04d-%02d" % (mc["start_year"], mc["start_month"]),
            mc["horizon"])
    union = set()
    for lst in center_periods.values():
        union.update(lst)
    union.update(p for p in df["periodo"].dropna().unique())
    periods = sorted(union)

    real = _real_apartado(df, opening, last_actual)
    proj = _proj_apartado(assumptions, opening, center_periods,
                          last_actual, bundle=bundle)
    long_df = pd.concat([x for x in (real, proj) if not x.empty],
                        ignore_index=True) if (not real.empty
                                               or not proj.empty) \
        else pd.DataFrame(columns=_APCOLS)

    key = bundle["meta"].get("proration_key", config.PRORATION_KEY)
    weights = projections.weights_from_assumptions(assumptions)
    long_df = _hq_proration(long_df, opening, weights, key)

    # --- Cash flow (derivado de la P&L) ---
    learned = bundle["meta"].get("learned_tax", {}) or {}
    cf_long = cashflow.build_cf_long(long_df, df, last_actual,
                                     learned_tax=learned)

    return Model(pnl_long=long_df, cf_long=cf_long, periods=periods,
                 center_periods=center_periods,
                 last_actual_period=last_actual, opening=opening)
