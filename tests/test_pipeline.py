"""Pruebas con el XLSX de muestra real."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kratos import (assumptions_io, cashflow, classify, config, ingest,
                    pipeline, pl_mapping, pnl, projections, socios, store)

SAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "PALAVIAN INTERACTIVE ESTATE SL - Libro diario 01_01_2026-31_12_2026.xlsx")


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    store.init_db()
    yield


# --- ingest ----------------------------------------------------------------
def test_ingest_basic():
    res = ingest.load_ledger(SAMPLE)
    assert res.n_rows == 3423
    assert res.dropped_rows >= 1                      # fila "Informe creado..."
    assert abs(res.sum_debe - res.sum_haber) < 0.5    # partida doble cuadra
    assert str(res.date_min) == "2026-01-01"
    assert str(res.date_max) == "2026-05-18"
    assert res.sheet == "Holded"


def test_to_float_locales():
    assert ingest.to_float("1.234,56") == 1234.56
    assert ingest.to_float("1,234.56") == 1234.56
    assert ingest.to_float("12,5 €") == 12.5
    assert ingest.to_float(None) == 0.0
    assert ingest.to_float(2800.0) == 2800.0


def test_clean_tags():
    assert classify_tags("localparis,revisadogestoria") == ["localparis"]
    assert classify_tags(None) == []


def classify_tags(v):
    return ingest.clean_tags(v)


# --- classify --------------------------------------------------------------
def test_capex_is_asset_accounts():
    res = ingest.load_ledger(SAMPLE)
    df = classify.classify(res.df)
    capex = df[df["tipo_mov"] == "CAPEX"]
    assert len(capex) > 30
    assert capex["prefijo2"].isin(["20", "21", "22", "23"]).all()
    # K3 (Poble Nou) tiene CAPEX de obras
    assert (capex["centro"] == "K3").any()
    # no entra ninguna cuenta 6xx/7xx como CAPEX
    assert not capex["cuenta"].str.startswith(("6", "7")).any()


def test_centers_assigned():
    res = ingest.load_ledger(SAMPLE)
    df = classify.classify(res.df)
    cov = classify.coverage(df)
    assert cov["por_centro"].get("K1", 0) > 100
    assert cov["por_centro"].get("K2", 0) > 100
    assert cov["cobertura_pct"] > 50


# --- pnl + proration -------------------------------------------------------
def _model_df():
    res = ingest.load_ledger(SAMPLE)
    df = classify.classify(res.df)
    return pl_mapping.apply_mapping(df, pl_mapping.default_mapping_rows())


def test_pnl_actual_and_opening():
    df = _model_df()
    opening = dict(config.DEFAULT_OPENING_MONTH)
    p = pnl.build_actual_pnl(df, opening)
    assert not p.empty
    k1_inc = p[(p["centro"] == "K1") & (p["seccion"] == "Ingresos")]
    assert k1_inc["valor"].sum() > 0          # ingresos positivos
    # K3 no debe tener P&L operativa antes de su apertura (2026-05)
    k3_before = p[(p["centro"] == "K3") & (p["periodo"] < "2026-05")]
    assert k3_before.empty


def test_hq_proration_nets_to_zero():
    df = _model_df()
    opening = dict(config.DEFAULT_OPENING_MONTH)
    p = pnl.build_actual_pnl(df, opening)
    pr = pnl.hq_proration(p, opening, weights=None, key="manual")
    imput = pr[pr["partida"] == pl_mapping.PARTIDA_HQ]["valor"].sum()
    hq_cost = p[p["centro"] == "HQ"]["valor"].sum()
    # la suma de imputaciones a los K = gasto de HQ (se netea en consolidado)
    assert abs(imput - hq_cost) < 1.0


def test_capex_amortization_respects_opening():
    df = _model_df()
    opening = dict(config.DEFAULT_OPENING_MONTH)
    horizon = pnl.month_range("2026-01", 36)
    a = pd.DataFrame([{"centro": "K3", "periodo": "2026-05",
                       "concepto": "__capex_amort_anos__", "valor": 5}])
    am = pnl.capex_amortization(df, a, opening, horizon)
    assert not am.empty
    assert (am[am["centro"] == "K3"]["periodo"] >= "2026-05").all()
    assert (am["valor"] <= 0).all()


# --- cash flow -------------------------------------------------------------
def test_cashflow_has_blocks():
    df = _model_df()
    cf = cashflow.build_actual_cashflow(df)
    assert not cf.empty
    assert set(cf["bloque"].unique()).issubset(set(cashflow.BLOQUES))
    assert (cf["bloque"] == "Inversion").any()      # CAPEX pagado


# --- assumptions round trip ------------------------------------------------
def test_assumptions_round_trip():
    opening = dict(config.DEFAULT_OPENING_MONTH)
    tpl = assumptions_io.export_template(opening)
    assert isinstance(tpl, (bytes, bytearray)) and len(tpl) > 1000
    seed = pd.DataFrame([{"centro": "K4", "concepto": "Cuotas socios",
                          "periodo": "2026-10", "valor": 9999.0}])
    tpl2 = assumptions_io.export_template(opening, existing=seed)
    back = assumptions_io.import_template(tpl2)
    row = back[(back["centro"] == "K4")
               & (back["concepto"] == "Cuotas socios")
               & (back["periodo"] == "2026-10")]
    assert not row.empty and abs(float(row["valor"].iloc[0]) - 9999.0) < 0.01


def test_socios_trajectory_and_seasonality():
    periods = pnl.month_range("2026-09", 4)   # sep,oct,nov,dic 2026
    season = [100.0] * 12
    season[9] = 50.0                          # octubre (mes 10) al 50%
    plan = {"2026-09": {"altas": 100, "churn": 0},
            "2026-10": {"altas": 0, "churn": 10}}
    rows = socios.compute_trajectory(periods, "2026-09", aforo=200,
                                     ticket=40, season12=season, plan=plan)
    by = {r["periodo"]: r for r in rows}
    # sep: inicio 0, +100 altas -> fin 100; ingreso 100*40*1.0
    assert by["2026-09"]["Socios fin mes"] == 100
    assert by["2026-09"]["Ingresos cuotas (EUR)"] == 4000.0
    # oct: inicio 100, churn 10% -> bajas 10, fin 90; indice 50 -> *0.5
    assert by["2026-10"]["Bajas"] == 10
    assert by["2026-10"]["Socios fin mes"] == 90
    assert by["2026-10"]["Ingresos cuotas (EUR)"] == 90 * 40 * 0.5
    # ocupacion = 90/200 = 45%
    assert abs(by["2026-10"]["% Ocupacion (%)"] - 45.0) < 1e-6


def test_churn_applies_same_month_with_altas():
    # churn debe afectar el mismo mes en que entran las altas
    rows = {r["periodo"]: r for r in socios.compute_trajectory(
        ["2026-09"], "2026-09", 100, 30, [100.0] * 12,
        {"2026-09": {"altas": 100, "churn": 10}})}
    assert rows["2026-09"]["Bajas"] == 10          # (0+100)*10%
    assert rows["2026-09"]["Socios fin mes"] == 90


def test_bajas_manual_override():
    periods = pnl.month_range("2026-09", 3)
    season = [100.0] * 12
    # mes1: +100 altas -> fin 100
    # mes2: churn 50% (=50) PERO bajas manual 10 -> manda 10 -> fin 90
    plan = {"2026-09": {"altas": 100, "churn": 0, "bajas": 0},
            "2026-10": {"altas": 0, "churn": 50, "bajas": 10}}
    rows = {r["periodo"]: r for r in socios.compute_trajectory(
        periods, "2026-09", 200, 40, season, plan)}
    assert rows["2026-10"]["Bajas"] == 10        # manual manda, no el 50%
    assert rows["2026-10"]["Socios fin mes"] == 90


def test_socios_feeds_pnl(tmp_db):
    pipeline.process_upload(SAMPLE, source_name="m.xlsx", replace=True)
    store.set_center_params("K4", {"aforo": 150, "ticket": 50, "iva": 21})
    store.set_center_model_config("K4", 1, 2026, 36)
    store.set_socios_plan("K4", {"2026-10": {"altas": 30, "churn": 0}})
    # K4 abre por defecto 2026-09; oct: fin 30 -> ingreso 30*50 = 1500
    m = pipeline.build_model()
    k4 = m.pnl_long[(m.pnl_long["centro"] == "K4")
                    & (m.pnl_long["apartado"] == "Ingresos")
                    & (m.pnl_long["partida"] == "Cuotas")
                    & (m.pnl_long["periodo"] == "2026-10")]
    assert not k4.empty and abs(float(k4["valor"].iloc[0]) - 1500.0) < 0.01


def test_gastos_master_projection(tmp_db):
    pipeline.process_upload(SAMPLE, source_name="m.xlsx", replace=True)
    store.set_center_model_config("K4", 1, 2026, 36)
    # Suplementos 300 €, trimestral, arranca enero, apartado Compras
    store.set_gastos_plan("K4", [
        {"partida": "Suplementos", "importe": 300, "intervalo": 3,
         "apartado": "Aprovisionamientos", "mes_inicio": 1}])
    m = pipeline.build_model()
    g = m.pnl_long[(m.pnl_long["centro"] == "K4")
                   & (m.pnl_long["apartado"] == "Aprovisionamientos")]
    oct26 = g[g["periodo"] == "2026-10"]
    assert not oct26.empty and abs(float(oct26["valor"].iloc[0]) + 300) < 1e-6
    assert g[g["periodo"] == "2026-07"].empty       # antes de apertura
    assert (g["valor"] <= 0).all()


def test_personal_projection(tmp_db):
    pipeline.process_upload(SAMPLE, source_name="m.xlsx", replace=True)
    store.set_center_model_config("K4", 1, 2026, 36)
    store.set_personal("K4", [
        {"rol": "Entrenador 1", "nombre": "Ana", "bruto": 2000,
         "ss_pct": 30, "mes_inicio": 1},
        {"rol": "Entrenador 2", "nombre": "", "bruto": 0, "ss_pct": 30,
         "mes_inicio": 1},
        {"rol": "Entrenador 3", "nombre": "", "bruto": 0, "ss_pct": 30,
         "mes_inicio": 1},
        {"rol": "Entrenador 4", "nombre": "", "bruto": 0, "ss_pct": 30,
         "mes_inicio": 1},
        {"rol": "Entrenador 5", "nombre": "", "bruto": 0, "ss_pct": 30,
         "mes_inicio": 1},
        {"rol": "Responsable/Admin", "nombre": "", "bruto": 0,
         "ss_pct": 30, "mes_inicio": 1}])
    m = pipeline.build_model()
    # Todo el personal -> "Gastos de Personal" (sueldo + SS)
    gp = m.pnl_long[(m.pnl_long["centro"] == "K4")
                    & (m.pnl_long["apartado"] == "Gastos de Personal")
                    & (m.pnl_long["periodo"] == "2026-10")]["valor"].sum()
    assert abs(gp + 2600) < 1e-6            # -(2000 + 600)
    assert m.pnl_long[(m.pnl_long["centro"] == "K4")
                      & (m.pnl_long["periodo"] == "2026-07")
                      & (m.pnl_long["apartado"] == "Gastos de Personal")
                      ].empty


def test_projected_pnl_sign():
    a = pd.DataFrame([
        {"centro": "K4", "periodo": "2026-10", "concepto": "Cuotas socios",
         "valor": 1000.0},
        {"centro": "K4", "periodo": "2026-10", "concepto": "Alquiler",
         "valor": 500.0}])
    opening = {"K4": "2026-09"}
    pj = projections.build_projected_pnl(a, opening)
    inc = pj[pj["partida"] == "Cuotas socios"]["valor"].iloc[0]
    gas = pj[pj["partida"] == "Alquiler"]["valor"].iloc[0]
    assert inc > 0 and gas < 0          # ingreso +, gasto -


# --- pipeline end to end + idempotencia ------------------------------------
def test_pipeline_idempotent(tmp_db):
    s1 = pipeline.process_upload(SAMPLE, source_name="muestra.xlsx",
                                 replace=True)
    m1 = pipeline.build_model()
    s2 = pipeline.process_upload(SAMPLE, source_name="muestra.xlsx",
                                 replace=True)
    m2 = pipeline.build_model()
    assert s1.n_rows == s2.n_rows
    assert abs(m1.pnl_long["valor"].sum() - m2.pnl_long["valor"].sum()) < 0.01
