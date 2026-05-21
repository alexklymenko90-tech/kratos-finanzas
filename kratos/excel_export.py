"""Excel de resultados: P&L analitica y cash flow por centro + consolidado.

Replica el estilo de la app: Ventas en azul, subtotales en rojo Kratos,
Beneficio Neto resaltado, lineas de margen en cursiva pequena con %.
"""

from __future__ import annotations

import io

import xlsxwriter

from . import analytic, cashflow, config, store


def _write_pnl(wb, ws, df, klist, periods, title, f):
    """P&L analitica con estilo por tipo de fila (segun `_kind`).
    `klist` es una lista posicional con el kind de cada fila (mismo orden
    que df). Posicional para soportar etiquetas duplicadas (% s/ ingresos)."""
    ws.write(0, 0, title, f["title"])
    ws.write(2, 0, "Concepto", f["hdr"])
    for j, p in enumerate(periods):
        ws.write(2, 1 + j, p, f["hdr"])
    ws.set_column(0, 0, 36)
    ws.set_column(1, len(periods), 12)
    ws.freeze_panes(3, 1)
    r = 3
    for i, (concepto, row) in enumerate(df.iterrows()):
        k = klist[i] if i < len(klist) else ""
        lab_f, num_f = f["lbl"], f["num"]
        if k == "secchead":
            lab_f, num_f = f["h_lbl"], f["h_lbl"]
        elif k == "total":
            lab_f, num_f = f["t_lbl"], f["t_num"]
        elif k == "subtotal":
            lab_f, num_f = f["s_lbl"], f["s_num"]
        elif k == "ebitda":
            lab_f, num_f = f["e_lbl"], f["e_num"]
        elif k == "neto":
            lab_f, num_f = f["n_lbl"], f["n_num"]
        elif k == "pct":
            lab_f, num_f = f["m_lbl"], f["m_num"]
        ws.write(r, 0, concepto, lab_f)
        for j, p in enumerate(periods):
            if k == "secchead":
                ws.write_blank(r, 1 + j, None, lab_f)
            else:
                ws.write_number(r, 1 + j, float(row.get(p, 0.0)), num_f)
        r += 1


def build_workbook(model) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    NUM = "#,##0"
    PCT = '0"%"'
    f = {
        "title": wb.add_format({"bold": True, "font_size": 14}),
        "hdr": wb.add_format({"bold": True, "bg_color": "#1F4E78",
                              "font_color": "white", "border": 1,
                              "align": "center"}),
        "lbl": wb.add_format({"border": 1}),
        "num": wb.add_format({"border": 1, "num_format": NUM}),
        "bold": wb.add_format({"bold": True, "border": 1,
                               "bg_color": "#D6E4F0"}),
        "bold_num": wb.add_format({"bold": True, "border": 1,
                                   "bg_color": "#D6E4F0",
                                   "num_format": NUM}),
        # Cabecera de seccion (1. INGRESOS, ...)
        "h_lbl": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#2A2F3A",
                                "font_color": "white"}),
        # TOTAL de seccion
        "t_lbl": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#3A3F4B",
                                "font_color": "white"}),
        "t_num": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#3A3F4B",
                                "font_color": "white", "num_format": NUM}),
        # Subtotales (EBIT / EBT)
        "s_lbl": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#4A3F1F",
                                "font_color": "white"}),
        "s_num": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#4A3F1F",
                                "font_color": "white", "num_format": NUM}),
        # EBITDA (resaltado amarillo)
        "e_lbl": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#FFE08A"}),
        "e_num": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#FFE08A", "num_format": NUM}),
        # Resultado neto (verde)
        "n_lbl": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#1F7A4D",
                                "font_color": "white"}),
        "n_num": wb.add_format({"bold": True, "border": 1,
                                "bg_color": "#1F7A4D",
                                "font_color": "white", "num_format": NUM}),
        # % s/ ingresos y Variacion (cursiva pequena, con %)
        "m_lbl": wb.add_format({"italic": True, "border": 1,
                                "font_size": 8,
                                "font_color": "#9AA0AA"}),
        "m_num": wb.add_format({"italic": True, "border": 1,
                                "font_size": 8, "font_color": "#9AA0AA",
                                "num_format": PCT}),
    }
    targets = [(c, config.CENTER_LABELS.get(c, c))
               for c in config.ALL_CENTERS]
    targets.append(("CONSOLIDADO", "Consolidado"))

    def _per(code):
        return (model.periods if code == "CONSOLIDADO"
                else model.center_periods.get(code, model.periods))

    is_rate = float(store.get_meta("is_rate", config.IS_RATE_DEFAULT))
    for code, label in targets:
        ws = wb.add_worksheet(("PL " + code)[:31])
        per = _per(code)
        tbl = analytic.pivot_analytic(model.pnl_long, code, per,
                                      is_rate=is_rate)
        klist = (tbl["_kind"].tolist() if "_kind" in tbl.columns else [])
        vals = (tbl.drop(columns=["_kind"]) if "_kind" in tbl.columns
                else tbl)
        _write_pnl(wb, ws, vals, klist, per,
                   "P&L — %s  (real hasta %s, luego proyectado)"
                   % (label, model.last_actual_period), f)

    def _saldo(code):
        cs = (config.ALL_CENTERS if code == "CONSOLIDADO" else [code])
        return sum(float(store.get_center_params(x).get(
            "saldo_inicial", 0) or 0) for x in cs)

    for code, label in targets:
        ws = wb.add_worksheet(("CF " + code)[:31])
        per = _per(code)
        tbl = cashflow.pivot_cf(model.cf_long, code, per,
                                saldo_inicial=_saldo(code),
                                is_rate=is_rate)
        klist = (tbl["_kind"].tolist() if "_kind" in tbl.columns else [])
        vals = (tbl.drop(columns=["_kind"]) if "_kind" in tbl.columns
                else tbl)
        _write_pnl(wb, ws, vals, klist, per,
                   "Cash Flow — %s" % label, f)

    wb.close()
    buf.seek(0)
    return buf.read()
