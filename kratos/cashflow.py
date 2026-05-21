"""Cash flow por centro / mes.

Real: metodo directo sobre las cuentas de tesoreria (57x). El signo del
movimiento de banco (importe = debe - haber) ya indica entrada (+) o
salida (-). Cada asiento se clasifica en Operativo / Inversion /
Financiacion segun su contrapartida, y se etiqueta con la misma partida
que la P&L. Los traspasos entre bancos propios se excluyen.

Proyectado: derivado de la P&L proyectada con desfases simples de cobro
y pago (MVP, deliberadamente sencillo).
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from . import config, pl_mapping

BLOQUES = ["Operativo", "Inversion", "Financiacion"]


def _shift_period(periodo: str, months: int) -> str:
    y, m = int(periodo[:4]), int(periodo[5:7])
    m += months
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return "%04d-%02d" % (y, m)


def build_account_nature(df: pd.DataFrame) -> dict:
    """cuenta -> (bloque, partida).

    Las cuentas 6xx/7xx (gasto/ingreso), 2xx (CAPEX) y 16x (deuda) tienen
    naturaleza directa. Las cuentas puente (proveedores 40x/41x, clientes
    43x, TPV 44x...) no dicen para que es el pago: se resuelven mirando la
    factura/asiento donde aparecen junto a una cuenta de naturaleza directa.
    """
    nat = {}
    for cuenta, sub in df.groupby("cuenta"):
        cuenta = str(cuenta)
        p2 = cuenta[:2]
        if p2 in config.CAPEX_PREFIXES:
            nat[cuenta] = ("Inversion", "Inversion CAPEX")
        elif p2 in config.FINANCING_PREFIXES or cuenta[:3] in config.FINANCING_PREFIXES:
            nat[cuenta] = ("Financiacion", "Prestamos / deuda")
        elif cuenta[:1] in ("6", "7"):
            pser = sub[sub["partida"].astype(bool)]
            if not pser.empty:
                dom = (pser.assign(a=pser["importe"].abs())
                          .groupby("partida")["a"].sum().idxmax())
            else:
                dom = "Otros"
            nat[cuenta] = ("Operativo", dom)
    # cuentas puente (4x) via asientos de factura
    bridge = {}
    for _, g in df.groupby("asiento"):
        drivers = [(nat[c], abs(float(v)))
                   for c, v in zip(g["cuenta"], g["importe"])
                   if str(c) in nat]
        if not drivers:
            continue
        for c in g["cuenta"]:
            c = str(c)
            if c[:1] == "4":
                acc = bridge.setdefault(c, {})
                for bp, w in drivers:
                    acc[bp] = acc.get(bp, 0.0) + w
    for c, cnt in bridge.items():
        nat[c] = max(cnt, key=cnt.get)
    return nat


def build_actual_cashflow(df: pd.DataFrame) -> pd.DataFrame:
    """long df: centro, periodo, bloque, partida, valor, origen='real'."""
    cols = ["centro", "periodo", "bloque", "partida", "valor", "origen"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    nat = build_account_nature(df)
    out = []
    for asiento, g in df.groupby("asiento"):
        tes = g[g["tipo_mov"] == "TESORERIA"]
        if tes.empty:
            continue
        # traspaso entre bancos propios -> excluir
        if (tes["cuenta"].nunique() >= 2
                and (tes["importe"] > 0).any() and (tes["importe"] < 0).any()):
            continue
        cp = g[g["tipo_mov"] != "TESORERIA"]
        bloque, partida, best = "Operativo", "Otros movimientos", -1.0
        for c, v in zip(cp["cuenta"], cp["importe"]):
            bp = nat.get(str(c))
            if bp and abs(float(v)) > best:
                best = abs(float(v))
                bloque, partida = bp
        for _, t in tes.iterrows():
            if t["periodo"] is None:
                continue
            out.append({
                "centro": t["centro"], "periodo": t["periodo"],
                "bloque": bloque, "partida": partida,
                "valor": round(float(t["importe"]), 2), "origen": "real",
            })
    if not out:
        return pd.DataFrame(columns=cols)
    res = pd.DataFrame(out)
    res = (res.groupby(["centro", "periodo", "bloque", "partida"], as_index=False)
              ["valor"].sum())
    res["valor"] = res["valor"].round(2)
    res["origen"] = "real"
    return res


# Secciones de coste de explotacion (mismas que la P&L)
_OPEX = ["Aprovisionamientos", "Gastos de Personal",
         "Otros gastos de explotacion", "Gastos de estructura HQ"]
_OPEX_LABEL = {
    "Aprovisionamientos": "APROVISIONAMIENTOS",
    "Gastos de Personal": "GASTOS DE PERSONAL",
    "Otros gastos de explotacion": "OTROS GASTOS DE EXPLOTACIÓN",
    "Gastos de estructura HQ": "GASTOS DE ESTRUCTURA · HQ",
}
COBROS = "Cobros de explotacion"
FINC = "Gastos financieros"
INV = "Inversion (CAPEX)"
FINA = "Financiacion (prestamos)"
# Secciones internas para acumular IVA y retencion por centro/periodo
_IVA_REP = "__IVA_REP"
_IVA_SOP = "__IVA_SOP"
_RET = "__RET"
CF_COLS = ["centro", "periodo", "seccion", "partida", "valor"]


def build_cf_long(pnl_long: pd.DataFrame, df_ledger: pd.DataFrame,
                  last_actual: str, learned_tax: dict = None) -> pd.DataFrame:
    """Cash flow derivado de la P&L con IVA y retenciones aplicados por
    partida. Cobros y pagos se muestran 'en bruto' (con IVA, menos
    retencion); la liquidacion trimestral con Hacienda se calcula en
    pivot_cf a partir de los acumuladores __IVA_REP, __IVA_SOP y __RET."""
    from . import pipeline as _pl
    learned_tax = learned_tax or {}
    rows = []
    if pnl_long is not None and not pnl_long.empty:
        for ap, part, c, p, v in zip(
                pnl_long["apartado"], pnl_long["partida"],
                pnl_long["centro"], pnl_long["periodo"],
                pnl_long["valor"]):
            v = float(v)
            tax = _pl.effective_tax(str(part), learned_tax)
            iva = float(tax.get("iva", 0)) / 100.0
            ret = float(tax.get("ret", 0)) / 100.0
            if ap == "Ingresos":
                # cobro bruto con IVA repercutido
                rows.append((c, p, COBROS, "Cobros de explotación",
                             v * (1 + iva)))
                if iva:
                    rows.append((c, p, _IVA_REP, "", v * iva))
            elif ap in _OPEX:
                # pago bruto (con IVA soportado) menos retencion retenida
                cash = v * (1 + iva) + abs(v) * ret      # v<0 -> ret resta
                rows.append((c, p, ap, str(part), cash))
                if iva:
                    rows.append((c, p, _IVA_SOP, "", abs(v) * iva))
                if ret:
                    rows.append((c, p, _RET, "", abs(v) * ret))
            elif ap == "Gastos financieros":
                rows.append((c, p, FINC, str(part), v))
            # Amortizaciones: no es caja -> se excluye
        # El IS se calcula en pivot_cf sobre el EBT del objetivo.

    if df_ledger is not None and not df_ledger.empty:
        real = build_actual_cashflow(df_ledger)
        if not real.empty:
            inv_fin = real[real["bloque"].isin(["Inversion",
                                                "Financiacion"])]
            for _, r in inv_fin.iterrows():
                if r["periodo"] and r["periodo"] <= last_actual:
                    sec = INV if r["bloque"] == "Inversion" else FINA
                    rows.append((r["centro"], r["periodo"], sec,
                                 str(r["partida"]), float(r["valor"])))

    if not rows:
        return pd.DataFrame(columns=CF_COLS)
    res = pd.DataFrame(rows, columns=CF_COLS)
    res = (res.groupby(["centro", "periodo", "seccion", "partida"],
                       as_index=False)["valor"].sum())
    res["valor"] = res["valor"].round(2)
    return res


def pivot_cf(cf_long: pd.DataFrame, target: str, periods: List[str],
             saldo_inicial: float = 0.0,
             is_rate: float = None) -> pd.DataFrame:
    """Tabla de cash flow alineada con la P&L. El IS se calcula sobre el
    EBT (op + financieros) del objetivo, anual, pagado en diciembre."""
    if is_rate is None:
        is_rate = config.IS_RATE_DEFAULT
    data = (cf_long if target == "CONSOLIDADO"
            else cf_long[cf_long["centro"] == target]).copy()

    def s(sec):
        return data[data["seccion"] == sec].groupby("periodo")["valor"].sum()

    def row(label, ser, kind):
        d = {"Concepto": label, "_kind": kind}
        for p in periods:
            d[p] = round(float(ser.get(p, 0.0)), 2)
        return d

    cob = s(COBROS)
    fin = s(FINC)
    inv, fina = s(INV), s(FINA)

    rows = [row("Cobros de explotación", cob, "detail")]

    # Pagos de explotacion: mismo desglose que la P&L (orden canonico)
    from . import analytic as _an
    _PERSONAL_KEEP = {"Sueldos y salarios", "Seguridad Social",
                      "Otros gastos de personal"}
    pag = pd.Series(dtype=float)
    for sec in _OPEX:
        # En CONSOLIDADO, la seccion HQ es redundante: la imputacion a K
        # y la recuperacion en HQ se cancelan (los gastos reales de HQ ya
        # estan en Aprovisionamientos/Personal/Otros por su naturaleza).
        if target == "CONSOLIDADO" and sec == "Gastos de estructura HQ":
            continue
        sub = data[data["seccion"] == sec]
        if sub.empty:
            continue
        # En CONSOLIDADO, agrupar todos los empleados (cada nombre es una
        # partida en la proyeccion) bajo "Sueldos y salarios" para que no
        # se vea una linea por persona en la vista consolidada.
        if target == "CONSOLIDADO" and sec == "Gastos de Personal":
            sub = sub.copy()
            mask = ~sub["partida"].isin(_PERSONAL_KEEP)
            sub.loc[mask, "partida"] = "Sueldos y salarios"
        rows.append(row(_OPEX_LABEL[sec], pd.Series(dtype=float),
                        "secchead"))
        canon = _an.GASTOS_PARTIDAS_POR_SECCION.get(sec, [])
        present = list(sub["partida"].unique())
        order = [p for p in canon if p in present]
        extras = [p for p in present if p not in canon]
        if sec == "Gastos de Personal" and "Seguridad Social" in extras:
            extras.remove("Seguridad Social")
            extras.append("Seguridad Social")
        order = order + extras
        for part in order:
            ps = sub[sub["partida"] == part].groupby("periodo")["valor"].sum()
            if ps.abs().sum() > 0:
                rows.append(row("    " + str(part), ps, "detail"))
        sec_tot = sub.groupby("periodo")["valor"].sum()
        rows.append(row("TOTAL " + _OPEX_LABEL[sec], sec_tot, "total"))
        pag = pag.add(sec_tot, fill_value=0)

    rows.append(row("PAGOS DE EXPLOTACIÓN", pag, "total"))
    op = cob.add(pag, fill_value=0)        # operativo bruto (con IVA y -ret)

    iva_rep = s(_IVA_REP)
    iva_sop = s(_IVA_SOP)
    ret_acum = s(_RET)

    pset = set(periods)

    def _liq(ser):
        """Acumula trimestralmente y coloca el saldo en el mes siguiente
        al cierre del trimestre (Q1->abr, Q2->jul, Q3->oct, Q4->ene+1)."""
        by_q = {}
        for p, v in ser.items():
            y = int(p[:4])
            q = (int(p[5:7]) - 1) // 3
            by_q[(y, q)] = by_q.get((y, q), 0.0) + float(v)
        out = {}
        for (y, q), tot in by_q.items():
            tgt = ("%04d-01" % (y + 1) if q == 3
                   else "%04d-%02d" % (y, (q + 1) * 3 + 1))
            if tgt in pset:
                out[tgt] = out.get(tgt, 0.0) + tot
        return pd.Series(out)

    # Liquidacion IVA: -(repercutido - soportado) en el mes siguiente
    iva_net = iva_rep.sub(iva_sop, fill_value=0)
    liq_iva = -_liq(iva_net)
    # Liquidacion retenciones IRPF: -ret_acum (pago a Hacienda)
    liq_ret = -_liq(ret_acum)

    # EBT neto (sin IVA ni retencion) para calcular el IS sobre la base
    # contable, no sobre el flujo de caja bruto
    ebt_net = (op.sub(iva_net, fill_value=0)
                 .sub(ret_acum, fill_value=0)
                 .add(fin, fill_value=0))
    ann = {}
    for p in periods:
        ann[p[:4]] = ann.get(p[:4], 0.0) + float(ebt_net.get(p, 0.0))
    imp = pd.Series({
        p: (round(-ann[p[:4]] * is_rate / 100.0, 2)
            if p[5:7] == "12" and ann.get(p[:4], 0.0) > 0 else 0.0)
        for p in periods})

    neto = (op.add(liq_iva, fill_value=0)
              .add(liq_ret, fill_value=0)
              .add(fin, fill_value=0).add(imp, fill_value=0)
              .add(inv, fill_value=0).add(fina, fill_value=0))

    rows += [
        row("FLUJO DE CAJA OPERATIVO", op, "subtotal"),
        row("Liquidación IVA (Hacienda)", liq_iva, "detail"),
        row("Liquidación retenciones IRPF (Hacienda)", liq_ret, "detail"),
        row("Gastos financieros", fin, "detail"),
        row("Impuesto de Sociedades (dic)", imp, "detail"),
        row("Inversión (CAPEX)", inv, "detail"),
        row("Financiación (préstamos)", fina, "detail"),
        row("FLUJO DE CAJA NETO", neto, "subtotal"),
    ]
    saldo, acc = {}, float(saldo_inicial or 0.0)
    for p in periods:
        acc += float(neto.get(p, 0.0))
        saldo[p] = round(acc, 2)
    sd = {"Concepto": "SALDO DE CAJA ACUMULADO", "_kind": "neto"}
    sd.update(saldo)
    rows.append(sd)
    return pd.DataFrame(rows).set_index("Concepto")


def project_cashflow(pnl_proj: pd.DataFrame,
                      lags: Dict[str, Dict[str, int]]) -> pd.DataFrame:
    """Cash flow proyectado a partir de la P&L proyectada.

    lags[centro] = {"cobro": meses, "pago": meses}. Ingresos se desplazan
    por el lag de cobro; gastos por el de pago. CAPEX (Amortizaciones no es
    caja) se ignora aqui: la salida de CAPEX se modela como inversion.
    """
    cols = ["centro", "periodo", "bloque", "partida", "valor", "origen"]
    if pnl_proj is None or pnl_proj.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for _, r in pnl_proj.iterrows():
        sec = r["seccion"]
        if sec == "Amortizaciones":
            continue  # no es caja
        c = r["centro"]
        lag = lags.get(c, {})
        if sec == "Ingresos":
            per = _shift_period(r["periodo"], int(lag.get("cobro", 0)))
        else:
            per = _shift_period(r["periodo"], int(lag.get("pago", 0)))
        rows.append({
            "centro": c, "periodo": per, "bloque": "Operativo",
            "partida": r["partida"], "valor": round(float(r["valor"]), 2),
            "origen": "proyectado",
        })
    res = pd.DataFrame(rows)
    res = (res.groupby(["centro", "periodo", "bloque", "partida"], as_index=False)
              ["valor"].sum())
    res["valor"] = res["valor"].round(2)
    res["origen"] = "proyectado"
    return res


def pivot_cashflow(cf_long: pd.DataFrame, centro: str,
                   periods: List[str], saldo_inicial: float = 0.0) -> pd.DataFrame:
    """Tabla cash flow: bloques + partidas + flujo neto + saldo acumulado."""
    data = (cf_long if centro == "CONSOLIDADO"
            else cf_long[cf_long["centro"] == centro]).copy()
    out_rows = []
    for bloque in BLOQUES:
        sub = data[data["bloque"] == bloque]
        if sub.empty:
            continue
        for partida in sorted(sub["partida"].unique()):
            ser = (sub[sub["partida"] == partida]
                   .groupby("periodo")["valor"].sum())
            out_rows.append(_row("  " + partida, ser, periods))
        out_rows.append(_row("FLUJO " + bloque.upper(),
                              sub.groupby("periodo")["valor"].sum(),
                              periods))
    neto = data.groupby("periodo")["valor"].sum()
    out_rows.append(_row("FLUJO NETO", neto, periods))
    # saldo acumulado
    saldo, acc = {}, saldo_inicial
    for p in periods:
        acc += float(neto.get(p, 0.0))
        saldo[p] = round(acc, 2)
    out_rows.append({"Concepto": "SALDO ACUMULADO", **saldo})
    return pd.DataFrame(out_rows).set_index("Concepto")


def _row(name: str, ser: pd.Series, periods: List[str]):
    d = {"Concepto": name}
    for p in periods:
        d[p] = round(float(ser.get(p, 0.0)), 2)
    return d
