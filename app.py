"""App local (Streamlit) — Plan financiero multi-centro Kratos / Palavian.

Navegacion por centro (pestanas tipo boton) y sub-pestanas por centro.
Arranca con doble clic en run.command (macOS) o run.bat (Windows).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile

import pandas as pd
import streamlit as st
from sqlalchemy import text

from kratos import (analytic, assumptions_io, cashflow, config,
                    excel_export, pipeline, pnl, scenario, socios, store)

# Color de marca Kratos (tomado de kratoscalisthenics.com)
BRAND_RED = "#E30613"

st.set_page_config(page_title="Kratos — Plan financiero",
                   layout="wide", initial_sidebar_state="collapsed")


# --- Login -----------------------------------------------------------------
# En local, si no hay .streamlit/secrets.toml con usuarios, NO pide login
# (flujo de desarrollo). En la nube, los secretos definen los usuarios y
# el login es obligatorio.
def _get_users() -> dict:
    try:
        users = st.secrets.get("users", {})
    except (AttributeError, FileNotFoundError, Exception):
        users = {}
    return dict(users) if users else {}


def _login_logo_b64():
    p = os.path.join(config.PROJECT_DIR, "assets", "logo.png")
    try:
        with open(p, "rb") as fh:
            return base64.b64encode(fh.read()).decode()
    except OSError:
        return ""


def _auth_secret() -> str:
    """Clave secreta para firmar tokens de sesion. Si no esta en secrets,
    usa un fallback estable para dev (no romper si falta)."""
    try:
        s = st.secrets.get("auth_secret")
        if s:
            return str(s)
    except Exception:  # noqa: BLE001
        pass
    return "kratos-default-change-in-secrets-toml"


def _make_token(user: str) -> str:
    return hmac.new(_auth_secret().encode(),
                    user.encode(), hashlib.sha256).hexdigest()


def _require_login():
    users = _get_users()
    if not users:
        return                                # modo desarrollo (sin login)

    # Restaurar sesion desde query params si el usuario refresca el
    # navegador. El token es un HMAC del usuario con la clave secreta,
    # asi que nadie puede forjarlo.
    if not st.session_state.get("auth_user"):
        q = st.query_params
        qu = q.get("u")
        qt = q.get("t")
        if (qu and qt and qu in users
                and hmac.compare_digest(_make_token(qu), str(qt))):
            st.session_state["auth_user"] = qu

    if st.session_state.get("auth_user"):
        return                                # ya autenticado

    st.markdown(
        """
        <style>
          [data-testid="stToolbar"] { display: none; }
          [data-testid="stDecoration"] { display: none; }
          footer { display: none; }
          .kr-login-banner {
            background: %s;
            padding: 22px 22px;
            display: flex; align-items: center; justify-content: center;
            gap: 16px;
            border-radius: 10px;
            margin: 8vh 0 4px 0;
          }
          .kr-login-banner img { height: 44px; width: auto; }
          .kr-login-banner .kr-title {
            color: #fff; font-weight: 800; letter-spacing: 12px;
            font-size: 24px;
          }
          .kr-login-sub {
            text-align: center; color: #9aa0aa; font-size: 11px;
            letter-spacing: 2px; text-transform: uppercase;
            margin: 0 0 16px 0;
          }
          .kr-login-footer {
            text-align: center; color: #6b7280; font-size: 11px;
            margin-top: 16px;
          }
        </style>
        """ % BRAND_RED, unsafe_allow_html=True)

    _lb = _login_logo_b64()
    logo = ("<img src='data:image/png;base64,%s'/>" % _lb) if _lb else ""

    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown(
            '<div class="kr-login-banner">%s'
            '<span class="kr-title">KRATOS</span></div>'
            '<div class="kr-login-sub">Plan financiero · multi-centro</div>'
            % logo, unsafe_allow_html=True)
        with st.form("login_form", clear_on_submit=False):
            u = st.text_input("Usuario", key="login_u",
                              placeholder="tu usuario")
            p = st.text_input("Contraseña", type="password",
                              key="login_p", placeholder="••••••••")
            ok = st.form_submit_button("Entrar  →", type="primary",
                                       use_container_width=True)
        st.markdown(
            '<div class="kr-login-footer">Acceso restringido · '
            'sesión cifrada</div>', unsafe_allow_html=True)

    if ok:
        if u in users and str(users[u]) == p:
            st.session_state["auth_user"] = u
            # Token firmado en URL -> al refrescar, se mantiene la sesion
            st.query_params["u"] = u
            st.query_params["t"] = _make_token(u)
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")
    st.stop()


_require_login()


# init_db hace ~14 consultas a Supabase (CREATE TABLE IF NOT EXISTS,
# ALTER, SELECT COUNT, semillas). Si se ejecuta en cada rerun, añade
# ~400-700 ms por interacción. Lo aislamos en cache_resource para que
# corra UNA SOLA VEZ por proceso de Streamlit Cloud.
@st.cache_resource
def _init_once():
    store.init_db()
    return True


_init_once()


# --- Cache del modelo (rapido en cloud) -----------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_ledger(_lv: int):
    """Cache separado del libro diario (transferir ~3500 filas tarda ~1s).
    Solo se invalida cuando sube un nuevo libro diario, NO en cada
    edicion de Tabla de mando."""
    return store.load_ledger()


@st.cache_data(ttl=600, show_spinner=False)
def _build_model_cached(_version: int, _lv: int):
    """Construye el modelo (lectura BBDD + calculos). `_version` cambia
    en cada edicion (invalida proyecciones); `_lv` solo cambia al subir
    nuevo libro diario (mantiene el ledger en cache entre ediciones)."""
    df = _cached_ledger(_lv)
    return pipeline.build_model(ledger_df=df)


def get_model():
    v = st.session_state.get("_model_version", 0)
    lv = st.session_state.get("_ledger_version", 0)
    return _build_model_cached(v, lv)


def invalidate_model():
    """Llamar tras editar Tabla de mando, mapeo, etc. (NO afecta al
    libro diario, que se mantiene en cache para acelerar el rebuild)."""
    st.session_state["_model_version"] = (
        st.session_state.get("_model_version", 0) + 1)
    _build_model_cached.clear()
    _bulk_cache.clear()
    for fn_name in ("_c_pnl_html", "_c_pnl_v2_html", "_c_cf_html"):
        fn = globals().get(fn_name)
        if fn is not None:
            try:
                fn.clear()
            except Exception:  # noqa: BLE001
                pass


def invalidate_ledger():
    """Llamar tras subir un nuevo libro diario. Invalida TODO."""
    st.session_state["_ledger_version"] = (
        st.session_state.get("_ledger_version", 0) + 1)
    _cached_ledger.clear()
    _c_load_ledger.clear()
    invalidate_model()


def _v():
    return st.session_state.get("_model_version", 0)


# Bulk cache: en una sola llamada (6 SELECT a Supabase) trae TODA la
# configuracion de los 5 centros (center_model, socios_plan, personal_plan,
# gastos_plan, meta, centers). Despues, cambiar de pestaña entre K's es
# instantaneo porque todo se lee de RAM local. Se invalida tras cualquier
# escritura via invalidate_model().
@st.cache_data(ttl=600, show_spinner=False)
def _bulk_cache(_v):
    eng = store.get_engine()
    with eng.begin() as conn:
        cm_rows = conn.execute(text(
            "SELECT centro, clave, valor FROM center_model")).fetchall()
        sp_rows = conn.execute(text(
            "SELECT centro, periodo, altas, churn, bajas "
            "FROM socios_plan")).fetchall()
        pp_rows = conn.execute(text(
            "SELECT centro, rol, nombre, bruto, mes_inicio, destino, "
            "ss_pct FROM personal_plan")).fetchall()
        gp_rows = conn.execute(text(
            "SELECT centro, idx, partida, importe, intervalo, apartado, "
            "mes_inicio FROM gastos_plan ORDER BY centro, idx")).fetchall()
        m_rows = conn.execute(text(
            "SELECT clave, valor FROM meta")).fetchall()
        c_rows = conn.execute(text(
            "SELECT centro, label, apertura FROM centers")).fetchall()

    cm = {}
    for centro, clave, valor in cm_rows:
        cm.setdefault(centro, {})[clave] = valor

    sp = {}
    for centro, per, a, c, b in sp_rows:
        sp.setdefault(centro, {})[per] = {
            "altas": a or 0, "churn": c or 0, "bajas": b or 0}

    pp = {}
    for centro, rol, nombre, bruto, mi, dest, ssp in pp_rows:
        pp.setdefault(centro, {})[rol] = (rol, nombre, bruto, mi, dest, ssp)

    gp = {}
    for centro, idx, partida, imp, itv, ap, mi in gp_rows:
        gp.setdefault(centro, []).append({
            "partida": partida or "",
            "importe": float(imp or 0),
            "intervalo": int(itv or 1),
            "apartado": ap or config.COST_APARTADOS[0],
            "mes_inicio": int(mi or 1)})

    meta = {}
    for clave, valor in m_rows:
        try:
            meta[clave] = json.loads(valor)
        except (ValueError, TypeError):
            meta[clave] = valor

    centers_df = pd.DataFrame(
        [{"centro": c, "label": lbl, "apertura": a}
         for c, lbl, a in c_rows])

    return {
        "center_model": cm,
        "socios_plan": sp,
        "personal_plan": pp,
        "gastos_plan": gp,
        "meta": meta,
        "centers_df": centers_df,
    }


def _c_center_model_config(_v, code):
    raw = _bulk_cache(_v)["center_model"].get(code, {})
    d = {"start_month": 1, "start_year": 2026,
         "horizon": config.HORIZON_MONTHS}
    for k, val in raw.items():
        if k in d:
            d[k] = int(val)
    return d


def _c_center_params(_v, code):
    raw = _bulk_cache(_v)["center_model"].get(code, {})
    d = {"iva": 21.0, "aforo": 0.0, "ticket": 0.0}
    d.update(raw)
    return d


def _c_season(_v, code):
    raw = _bulk_cache(_v)["center_model"].get(code, {})
    s = [100.0] * 12
    for k, val in raw.items():
        if isinstance(k, str) and k.startswith("season_"):
            try:
                i = int(k.split("_")[1])
                if 1 <= i <= 12:
                    s[i - 1] = float(val)
            except (ValueError, IndexError):
                pass
    return s


def _c_socios_plan(_v, code):
    return dict(_bulk_cache(_v)["socios_plan"].get(code, {}))


def _c_personal(_v, code):
    saved = _bulk_cache(_v)["personal_plan"].get(code, {})
    out = []
    for rol in config.PERSONAL_ROLES:
        r = saved.get(rol)
        dest = (r[4] if r and len(r) > 4 and r[4] else None)
        if not dest:
            dest = ("Sueldos Directos" if str(rol).startswith("Entrenador")
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
    return {"rows": out}


_GASTOS_SEED_LOCAL = [
    ("Compras mercaderías", "Aprovisionamientos", 1, 1),
    ("Arrendamiento local", "Otros gastos de explotacion", 1, 1),
    ("Limpieza", "Otros gastos de explotacion", 1, 1),
    ("Suministros", "Otros gastos de explotacion", 1, 1),
    ("Servicios profesionales", "Otros gastos de explotacion", 1, 1),
    ("Intereses préstamo", "Gastos financieros", 1, 1),
]


def _c_gastos_plan(_v, code):
    bulk = _bulk_cache(_v)
    rows = bulk["gastos_plan"].get(code)
    if rows:
        return list(rows)
    if bulk["meta"].get("gastos_init_%s" % code):
        return []
    return [{"partida": p, "importe": 0.0, "intervalo": itv,
             "apartado": ap, "mes_inicio": mi}
            for p, ap, itv, mi in _GASTOS_SEED_LOCAL]


def _c_opening_months(_v):
    df = _bulk_cache(_v)["centers_df"]
    if df.empty:
        return {}
    return dict(zip(df["centro"], df["apertura"]))


def _c_meta(_v, clave, default=None):
    val = _bulk_cache(_v)["meta"].get(clave)
    return val if val is not None else default


@st.cache_data(ttl=600, show_spinner=False)
def _c_load_ledger(_v):
    return store.load_ledger()


def _logo_b64():
    p = os.path.join(config.PROJECT_DIR, "assets", "logo.png")
    try:
        with open(p, "rb") as fh:
            return base64.b64encode(fh.read()).decode()
    except OSError:
        return ""


_LOGO = _logo_b64()
_logo_html = ("<img src='data:image/png;base64,%s' class='kratos-logo'/>"
              % _LOGO) if _LOGO else ""

st.markdown(
    """
    <style>
      .kratos-banner{background:%s;color:#fff;
        display:flex;align-items:center;
        padding:8px 18px;border-radius:6px;margin:-10px 0 4px 0;}
      .kratos-logo{height:54px;width:auto;margin-right:18px;}
      .kratos-title{font-weight:800;letter-spacing:14px;font-size:30px;
        flex:1;text-align:center;padding-right:72px;}
      .kratos-sub{color:#9aa0aa;font-size:13px;margin-bottom:2px;}
      div[data-testid="stDialogContent"]{min-width:640px;}
      header[data-testid="stHeader"]{height:0;}
      .kpl-wrap{overflow:auto;max-height:640px;border:1px solid #2a2f3a;
        border-radius:6px;}
      .kpl{border-collapse:collapse;font-size:13px;width:100%%;
        color:#e6e6e6;}
      .kpl td,.kpl th{padding:3px 10px;white-space:nowrap;
        text-align:right;border-bottom:1px solid #232733;}
      .kpl td:first-child,.kpl th:first-child{text-align:left;
        position:sticky;left:0;background:#0E1117;min-width:230px;}
      .kpl thead th{position:sticky;top:0;background:#0E1117;
        z-index:3;font-weight:700;}
      .kpl thead th:first-child{z-index:4;}
    </style>
    <div class="kratos-banner">%s
      <span class="kratos-title">KRATOS</span>
    </div>
    """ % (BRAND_RED, _logo_html),
    unsafe_allow_html=True,
)

# --- Estructura de navegacion ---------------------------------------------
NAV = [
    ("Dashboard", "DASHBOARD"),
    ("Consolidado", "CONSOLIDADO"),
    ("HQ", "HQ"),
    ("K1 · Paris", "K1"),
    ("K2 · Sant Joan", "K2"),
    ("K3 · Poble Nou", "K3"),
    ("K4 · Valencia", "K4"),
    ("K5 · —", "K5"),
    ("K6 · —", "K6"),
]
LABEL_BY_CODE = {c: l for l, c in NAV}
DISABLED = {"K5", "K6"}
SUBTABS = {
    "CONSOLIDADO": ["PyL", "PyL v2 · por centro", "Cashflow", "Resumen"],
    "DASHBOARD": [],
    "HQ": ["PyL", "Tabla de mando"],
    "K1": ["PyL", "Cashflow", "Tabla de mando"],
    "K2": ["PyL", "Cashflow", "Tabla de mando"],
    "K3": ["PyL", "Cashflow", "Tabla de mando"],
    "K4": ["PyL", "Cashflow", "Tabla de mando"],
}


# --- Dialogos de accion (botones de la cabecera) --------------------------
@st.dialog("Exportar / Importar")
def dlg_export():
    opening = store.opening_months()
    existing = store.load_assumptions()
    # ventana = union de todos los centros (para que el cliente pueda
    # rellenar cualquiera)
    allp = set()
    for c in config.ALL_CENTERS:
        mc = store.get_center_model_config(c)
        allp.update(pnl.month_range(
            "%04d-%02d" % (mc["start_year"], mc["start_month"]),
            mc["horizon"]))
    allp = sorted(allp) or [config.DEFAULT_START_MONTH]
    a, b = allp[0], allp[-1]
    span = (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7])) + 1

    st.markdown("##### Plantilla de supuestos (proyección del cliente)")
    tpl = assumptions_io.export_template(
        opening, start_month=a, horizon=span,
        last_actual_period=store.get_meta("last_actual_period"),
        existing=existing if not existing.empty else None)
    st.download_button("⬇ Descargar plantilla (para enviar al cliente)",
                       data=tpl, file_name="Kratos_supuestos.xlsx",
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet", use_container_width=True)
    upa = st.file_uploader("Subir plantilla rellenada por el cliente",
                           type=["xlsx"], key="exp_up_a")
    if upa is not None and st.button("Importar supuestos",
                                     use_container_width=True):
        try:
            adf = assumptions_io.import_template(upa.getvalue())
        except Exception as e:  # noqa: BLE001
            st.error("No se pudo leer: %s" % e)
        else:
            if adf.empty:
                st.warning("La plantilla no tenía valores.")
            else:
                store.save_assumptions(adf)
                invalidate_model()
                st.success("Importados %d valores en %d centros."
                           % (len(adf), adf["centro"].nunique()))
                st.button("Cerrar", on_click=st.rerun)

    st.divider()
    st.markdown("##### Excel de resultados (P&L + Cash flow)")
    m = get_model()
    if m is None:
        st.info("Sube el libro diario (pestaña **Libro diario**) para "
                "poder exportar resultados.")
    else:
        st.download_button("⬇ Descargar resultados",
                           data=excel_export.build_workbook(m),
                           file_name="Kratos_PL_CashFlow.xlsx",
                           mime="application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet",
                           use_container_width=True)

    st.divider()
    st.markdown("##### 🎯 Escenarios (JSON) — Tabla de mando")
    st.caption("Exporta toda la configuración de **Tabla de mando** "
               "(modelo, socios, personal, gastos) en un único archivo "
               ".json. Cada socio puede bajárselo, jugar con su propio "
               "escenario y compartir el suyo. La P&L y el Cash flow se "
               "recalculan automáticamente al importar.")
    st.caption("ℹ️ **Importante**: el escenario solo afecta a los "
               "**meses futuros** (a partir del último mes con datos "
               "reales en el libro diario). Los meses ya cerrados con "
               "lo real de Holded nunca se pisan, sea lo que sea lo "
               "que digas aquí o en la Tabla de mando.")

    se1, se2 = st.columns(2)
    with se1:
        try:
            sc_bytes = scenario.export_scenario()
            st.download_button(
                "⬇ Descargar escenario actual",
                data=sc_bytes, file_name=scenario.suggest_filename(),
                mime="application/json", use_container_width=True,
                key="dl_scenario")
        except Exception as e:  # noqa: BLE001
            st.error("No se pudo exportar el escenario: %s" % e)
    with se2:
        up_sc = st.file_uploader("Importar escenario (.json)",
                                 type=["json"], key="exp_up_sc")
        if up_sc is not None and st.button(
                "Aplicar escenario", use_container_width=True,
                key="apply_sc", type="primary"):
            try:
                summary = scenario.import_scenario(up_sc.getvalue())
            except Exception as e:  # noqa: BLE001
                st.error("No se pudo importar: %s" % e)
            else:
                invalidate_model()
                st.success(
                    "✓ Escenario aplicado · %d centros · %d gastos · "
                    "%d personal · %d socios · %d aperturas"
                    % (summary["centros"], summary["gastos"],
                       summary["personal"], summary["socios"],
                       summary["aperturas"]))
                st.button("Cerrar", on_click=st.rerun,
                          key="close_sc")

    st.divider()
    st.markdown("##### 📊 P&L (JSON) — cifras directas por partida/mes")
    n_ov = store.count_pnl_overrides()
    st.caption("Descarga la P&L tal cual la ves (todos los meses, por "
               "centro/partida). El cliente edita los **meses futuros** "
               "en su herramienta y reimporta: esas cifras **mandan sobre "
               "el modelo** de Tabla de mando para esos meses. Lo real "
               "del libro diario nunca se pisa.")
    if n_ov:
        st.info("Hay **%d overrides** de P&L activos ahora mismo." % n_ov)

    pl1, pl2 = st.columns(2)
    with pl1:
        if m is None:
            st.caption("Sube el libro diario para exportar la P&L.")
        else:
            try:
                pnl_bytes = scenario.export_pnl(m)
                st.download_button(
                    "⬇ Descargar P&L (para editar fuera)",
                    data=pnl_bytes,
                    file_name=scenario.suggest_pnl_filename(),
                    mime="application/json", use_container_width=True,
                    key="dl_pnl")
            except Exception as e:  # noqa: BLE001
                st.error("No se pudo exportar la P&L: %s" % e)
    with pl2:
        up_pnl = st.file_uploader("Importar P&L editada (.json)",
                                  type=["json"], key="exp_up_pnl")
        if up_pnl is not None and st.button(
                "Aplicar cifras de P&L", use_container_width=True,
                key="apply_pnl", type="primary"):
            la = m.last_actual_period if m is not None else \
                store.get_meta("last_actual_period")
            try:
                summary = scenario.import_pnl_overrides(
                    up_pnl.getvalue(), la)
            except Exception as e:  # noqa: BLE001
                st.error("No se pudo importar: %s" % e)
            else:
                invalidate_model()
                st.success(
                    "✓ %d cifras aplicadas en %d centros (solo meses "
                    "futuros)." % (summary["overrides"],
                                   summary["centros"]))
                st.button("Cerrar", on_click=st.rerun, key="close_pnl")

    if n_ov:
        if st.button("🗑 Quitar todos los overrides de P&L "
                     "(volver al modelo)", use_container_width=True,
                     key="clear_pnl_ov"):
            store.clear_pnl_overrides()
            invalidate_model()
            st.success("Overrides eliminados. La P&L vuelve a calcularse "
                       "100%% desde la Tabla de mando.")
            st.button("Cerrar", on_click=st.rerun, key="close_clear_ov")


@st.dialog("Ajustes")
def dlg_settings():
    st.markdown("##### Mes de apertura por centro")
    cdf = store.get_centers()
    ed = st.data_editor(cdf, use_container_width=True, key="c_ed",
                        disabled=["centro", "label"])
    if st.button("Guardar aperturas"):
        for _, r in ed.iterrows():
            store.set_opening(r["centro"], str(r["apertura"]).strip())
        invalidate_model()
        st.success("Guardado.")

    st.markdown("##### Prorrateo de gastos de HQ")
    key = store.get_meta("proration_key", config.PRORATION_KEY)
    nk = st.radio("Clave", ["manual", "revenue"],
                  index=0 if key == "manual" else 1,
                  format_func=lambda k: ("Pesos % manuales (Excel supuestos)"
                                         if k == "manual"
                                         else "Proporcional a ingresos"))
    if st.button("Guardar prorrateo"):
        store.set_meta("proration_key", nk)
        invalidate_model()
        st.success("Guardado.")

    st.markdown("##### Impuesto de Sociedades")
    isr = float(store.get_meta("is_rate", config.IS_RATE_DEFAULT))
    new_isr = st.number_input(
        "Tasa de IS (%) — se aplica sobre el EBT positivo",
        min_value=0.0, max_value=100.0, step=1.0, value=isr)
    if st.button("Guardar tasa IS"):
        store.set_meta("is_rate", float(new_isr))
        invalidate_model()
        st.success("Guardado.")

    st.markdown("##### Mapeo de cuentas → (seccion, partida)")
    mdf = pd.DataFrame(store.load_mapping())
    med = st.data_editor(mdf, use_container_width=True, num_rows="dynamic",
                         key="m_ed", height=240)
    if st.button("Guardar mapeo"):
        store.save_mapping(med.to_dict("records"))
        invalidate_model()
        st.success("Guardado. Reprocesa la carga (Importar) para aplicarlo.")

    df = store.load_ledger()
    if df is not None:
        sin = df[(df["centro"] == config.UNASSIGNED)
                 & (df["tipo_mov"] == "OPERATIVO")]
        st.markdown("##### Corregir centro de 'Sin asignar' (%d)" % len(sin))
        if not sin.empty:
            v = sin[["asiento", "linea", "fecha", "cuenta",
                     "cuenta_nombre", "importe"]].copy()
            v["nuevo_centro"] = ""
            ved = st.data_editor(
                v, use_container_width=True, height=240, key="s_ed",
                column_config={"nuevo_centro": st.column_config.SelectboxColumn(
                    options=[""] + config.ALL_CENTERS)})
            if st.button("Guardar correcciones"):
                n = 0
                for _, r in ved.iterrows():
                    if r["nuevo_centro"]:
                        store.set_override(int(r["asiento"]),
                                           int(r["linea"]), r["nuevo_centro"])
                        n += 1
                if n:
                    invalidate_model()
                st.success("%d guardadas. Reprocesa la carga." % n)


@st.dialog("Libro diario de Holded", width="large")
def dlg_carga():
    """Diálogo para subir el libro diario de Holded."""
    st.caption("Cada semana exporta de Holded el libro diario completo "
               "del año en XLSX y súbelo aquí. La P&L y el cash flow de "
               "todos los centros (K1–K4 + HQ) se actualizan automáticamente.")

    # --- Estado actual ----------------------------------------------------
    last_file = store.get_meta("last_file")
    last_actual = store.get_meta("last_actual_period")
    date_min = store.get_meta("date_min")
    date_max = store.get_meta("date_max")
    df = store.load_ledger()
    n_rows = 0 if df is None else len(df)

    if n_rows == 0:
        st.info("Aún no hay libro diario cargado. Sube el archivo XLSX "
                "abajo para empezar.")
    else:
        st.markdown(
            "**Estado actual** — %s asientos · %s → %s · último mes real "
            "**%s**\n\n*Archivo:* `%s`"
            % ("{:,}".format(n_rows),
               date_min or "—", date_max or "—",
               last_actual or "—",
               (last_file or "—")))

    st.divider()

    # --- Subida -----------------------------------------------------------
    st.markdown("##### Subir libro diario")
    up = st.file_uploader(
        "Arrastra el XLSX de Holded aquí o haz clic para seleccionar",
        type=["xlsx"], key="carga_up",
        accept_multiple_files=False)
    replace = st.toggle(
        "Modo reemplazar (recomendado)", value=True, key="carga_rep",
        help="Cada export de Holded es el YTD completo: reemplazar "
             "evita duplicar importes.")

    if up is not None and st.button("Procesar carga", type="primary",
                                    key="carga_btn",
                                    use_container_width=True):
        with tempfile.NamedTemporaryFile(suffix=".xlsx",
                                         delete=False) as tf:
            tf.write(up.getbuffer())
            tmp = tf.name
        try:
            s = pipeline.process_upload(tmp, source_name=up.name,
                                        replace=replace)
        except Exception as e:  # noqa: BLE001
            st.error("No se pudo procesar el archivo: %s" % e)
        else:
            invalidate_ledger()
            st.success(
                "Carga procesada · %s asientos · %s → %s · cobertura %.1f%%"
                % ("{:,}".format(s.n_rows), s.date_min, s.date_max,
                   s.coverage["cobertura_pct"]))
            if not s.cuadra:
                st.warning("**Debe y Haber no cuadran**: "
                           "Debe=%.2f, Haber=%.2f, diferencia=%.2f."
                           % (s.sum_debe, s.sum_haber,
                              s.sum_debe - s.sum_haber))
            if not s.unmapped.empty:
                st.warning("Hay **%d cuentas sin mapear**. Revísalas en "
                           "*Ajustes*." % len(s.unmapped))
            st.button("Ver resultados",
                      on_click=st.rerun, type="primary",
                      key="carga_done")

    st.divider()

    # --- Formato esperado -------------------------------------------------
    with st.expander("Formato esperado del libro diario de Holded"):
        st.markdown(
            "**Cómo exportar el libro diario en Holded:**\n"
            "1. En Holded ve a *Contabilidad → Libro diario*.\n"
            "2. Filtra por **el año completo en curso** (p. ej. "
            "01/01/2026 → 31/12/2026).\n"
            "3. Pulsa *Exportar → XLSX*.\n"
            "4. Sube ese archivo aquí sin abrirlo (no toquemos celdas).")
        st.markdown(
            "**Estructura que la herramienta espera dentro del XLSX:**")
        st.markdown(
            "- Hoja preferida: **`Holded`** (si no, lee la primera hoja).\n"
            "- Las primeras filas de metadatos no importan: la app "
            "detecta automáticamente la fila de cabecera buscando estas "
            "columnas obligatorias.")
        st.markdown("**Columnas obligatorias** (cualquier orden):")
        cols_req = pd.DataFrame([
            {"Columna": "Asiento", "Descripción":
                "Nº de asiento contable."},
            {"Columna": "Fecha", "Descripción":
                "Fecha del asiento (dd/mm/aaaa o datetime de Excel)."},
            {"Columna": "Cuenta", "Descripción":
                "Cuenta PGC de 8 dígitos (p. ej. 70500001)."},
            {"Columna": "Debe", "Descripción":
                "Importe al debe (€). Coma o punto decimal."},
            {"Columna": "Haber", "Descripción":
                "Importe al haber (€). Coma o punto decimal."},
        ])
        st.dataframe(cols_req, use_container_width=True, hide_index=True)
        st.markdown("**Columnas opcionales (recomendadas)** — mejoran la "
                    "clasificación:")
        cols_opt = pd.DataFrame([
            {"Columna": "Línea", "Descripción":
                "Nº de línea dentro del asiento (para los overrides)."},
            {"Columna": "Nombre de la cuenta", "Descripción":
                "Descripción de la cuenta — se usa para clasificar por "
                "centro si no hay tag (p. ej. \"LOCAL PARIS\")."},
            {"Columna": "Tags", "Descripción":
                "Etiquetas operativas separadas por coma. Reconoce: "
                "`localparis` → K1, `localsantjoan` → K2, "
                "`localpoblenou` → K3, `localvalencia` → K4, `hq` → HQ. "
                "Tags `k3`/`k4` solos (sin operativo) y cuenta de activo "
                "(2xx) marcan CAPEX."},
            {"Columna": "Tipo, Descripción, Documento, Punteado",
             "Descripción": "Se leen pero no son obligatorias para el "
                            "cálculo."},
        ])
        st.dataframe(cols_opt, use_container_width=True, hide_index=True)
        st.markdown("**Importes:** se aceptan formato europeo "
                    "(`1.234,56`) o americano (`1,234.56`); con o sin "
                    "símbolo `€`. La app lo normaliza solo.")
        st.markdown("**Modo reemplazar (por defecto):** cada export de "
                    "Holded contiene el YTD completo del año, así que la "
                    "carga **reemplaza** lo cargado antes. No duplica "
                    "importes. Tus mapeos, overrides y supuestos del "
                    "cliente **se conservan**.")

    # --- Resumen por centro / cobertura -----------------------------------
    if n_rows:
        from kratos import classify as _classify, pl_mapping as _plm
        cov = _classify.coverage(df)
        with st.expander("Resumen del libro diario cargado"):
            st.markdown("**Cobertura de clasificación a centro:** "
                        "%.1f%%" % cov.get("cobertura_pct", 0))
            st.caption("Una cobertura ~50%% es esperable: las cuentas de "
                       "IVA/bancos/balance no llevan centro pero tampoco "
                       "entran en la P&L.")
            rows = []
            for c, n in cov.get("por_centro", {}).items():
                rows.append({"Centro": config.CENTER_LABELS.get(c, c),
                             "Filas": int(n)})
            if rows:
                covdf = pd.DataFrame(rows).sort_values("Filas",
                                                       ascending=False)
                st.markdown("**Filas por centro:**")
                st.dataframe(covdf, use_container_width=True,
                             hide_index=True)
            unmap = _plm.unmapped_accounts(df)
            if not unmap.empty:
                st.markdown("**Cuentas sin mapear (%d):**" % len(unmap))
                st.dataframe(unmap, use_container_width=True,
                             hide_index=True)


@st.dialog("Diagnóstico del libro diario", width="large")
def dlg_diagnostico():
    """Comprobaciones que afectan al cuadre P&L y Cash flow."""
    from kratos import classify as _classify, pl_mapping as _plm
    df = store.load_ledger()
    if df is None or df.empty:
        st.info("Aún no hay libro diario cargado.")
        return

    if st.button("🔁 Reclasificar con las reglas actuales",
                 use_container_width=True,
                 help="Re-aplica clasificación de centro y mapeo a la "
                      "data cargada (sin necesidad de re-subir el XLSX). "
                      "Útil tras editar Ajustes o tras un cambio de reglas."):
        drop = ["centro", "center_method", "multi_tag", "tipo_mov",
                "seccion", "partida", "cuenta_sin_mapear"]
        d = df.drop(columns=[c for c in drop if c in df.columns])
        d = _classify.classify(d)
        d = _classify.apply_overrides(d, store.load_overrides())
        d = _plm.apply_mapping(d, store.load_mapping())
        store.save_ledger(d)
        invalidate_ledger()
        st.success("Reclasificado. Cierra el diálogo y abre de nuevo "
                   "para ver los cambios.")
        st.rerun()

    cov = _classify.coverage(df)
    unmap = _plm.unmapped_accounts(df)
    sin = df[(df["centro"] == config.UNASSIGNED)
             & (df["tipo_mov"] == "OPERATIVO")]
    ds = (df.groupby("asiento")[["debe", "haber"]].sum()
            .assign(dif=lambda d: (d["debe"] - d["haber"]).round(2)))
    descuadres = ds[ds["dif"].abs() > 0.5].reset_index()
    # Solo los tags OPERATIVOS de centro cuentan para el aviso multi-tag.
    # Los cortos (k1/k2/k3/k4) marcan CAPEX y no deben disparar el aviso
    # aunque coexistan con uno operativo.
    _OP_TAGS = {"localparis", "localsantjoan", "localpoblenou",
                "localvalencia", "hq"}

    def _multi(t):
        if not isinstance(t, list):
            return False
        op_centers = {config.TAG_TO_CENTER.get(x) for x in t
                      if x in _OP_TAGS}
        return len([c for c in op_centers if c]) > 1
    multi = df[df["tags"].apply(_multi)]
    capex_sin = df[(df["tipo_mov"] == "CAPEX")
                   & (df["centro"] == config.UNASSIGNED)]

    # --- Banner resumen ---
    items = [
        ("Cuentas sin mapear", len(unmap)),
        ("Apuntes operativos sin centro", len(sin)),
        ("Asientos descuadrados (Debe ≠ Haber)", len(descuadres)),
        ("Apuntes con varios tags de centro", len(multi)),
        ("CAPEX sin asignar a centro", len(capex_sin)),
    ]
    bad = sum(1 for _, n in items if n)
    if bad == 0:
        st.success("✅ Sin problemas. Cobertura de centros: %.1f%%."
                   % cov.get("cobertura_pct", 0))
    else:
        st.warning("Hay **%d puntos** que revisar para que el libro "
                   "diario cuadre con la P&L y el Cash flow:" % bad)
        for lbl, n in items:
            if n:
                st.markdown("- **%s:** %d" % (lbl, n))

    st.divider()

    # --- 1) Cuentas sin mapear ---
    st.markdown("##### 1 · Cuentas sin mapear a partida P&L")
    if unmap.empty:
        st.success("Todas las cuentas del libro diario tienen partida "
                   "asignada.")
    else:
        st.caption("Estas cuentas existen pero no caen en ninguna "
                   "partida — sus importes **no aparecen en la P&L**. "
                   "Mapéalas desde **⚙ Ajustes**.")
        st.dataframe(unmap, use_container_width=True, hide_index=True)

    st.divider()

    # --- 2) Apuntes sin centro ---
    st.markdown("##### 2 · Apuntes operativos sin centro asignado")
    if sin.empty:
        st.success("Todos los apuntes operativos tienen centro.")
    else:
        st.caption("Estos importes están en la P&L del **\"Sin asignar\"** "
                   "(no aparecen en ningún K ni en HQ). Hay que decidir "
                   "a qué centro pertenece cada cuenta — asígnalo en "
                   "**⚙ Ajustes → Corregir centro de 'Sin asignar'**.")
        agg = (sin.groupby(["cuenta", "cuenta_nombre"])
                  .agg(filas=("importe", "size"),
                       importe_total=("importe", "sum"))
                  .reset_index()
                  .sort_values("filas", ascending=False))
        agg["importe_total"] = agg["importe_total"].round(2)
        st.dataframe(agg, use_container_width=True, hide_index=True)

    st.divider()

    # --- 3) Descuadres Debe vs Haber ---
    st.markdown("##### 3 · Asientos donde Debe ≠ Haber")
    if descuadres.empty:
        st.success("Todos los asientos cuadran Debe = Haber.")
    else:
        st.caption("Estos asientos rompen la regla básica de partida "
                   "doble. Revisa el original en Holded.")
        st.dataframe(descuadres, use_container_width=True, hide_index=True)

    st.divider()

    # --- 4) Multi-tag ---
    st.markdown("##### 4 · Apuntes con varios tags de centro")
    if multi.empty:
        st.success("No hay apuntes con tags de centro conflictivos.")
    else:
        st.caption("Estos apuntes llevan dos o más tags de K en la "
                   "misma línea. Se asignan al de mayor precedencia "
                   "(K1 > K2 > K3 > K4 > HQ). Revisa que el centro "
                   "elegido sea correcto.")
        show = multi[["asiento", "fecha", "cuenta", "cuenta_nombre",
                      "tags", "centro", "importe"]].copy()
        show["tags"] = show["tags"].apply(
            lambda t: ", ".join(t) if isinstance(t, list) else str(t))
        st.dataframe(show, use_container_width=True, hide_index=True,
                     height=240)

    st.divider()

    # --- 5) CAPEX sin centro ---
    st.markdown("##### 5 · CAPEX sin centro asignado")
    if capex_sin.empty:
        st.success("Todo el CAPEX detectado tiene centro.")
    else:
        st.caption("Inversión (CAPEX) que no se ha asignado a ningún K. "
                   "Suele significar que faltó el tag de centro en el "
                   "asiento. Corrígelo en **⚙ Ajustes**.")
        show = capex_sin[["asiento", "fecha", "cuenta", "cuenta_nombre",
                          "importe", "tags"]].copy()
        show["tags"] = show["tags"].apply(
            lambda t: ", ".join(t) if isinstance(t, list) else str(t))
        st.dataframe(show, use_container_width=True, hide_index=True,
                     height=240)

    st.divider()

    # --- 6) Resumen por centro ---
    st.markdown("##### 6 · Resumen por centro (filas del libro diario)")
    st.caption("Total de filas del libro diario que han caído en cada "
               "bucket. La fila **Sin asignar** suele ser grande pero "
               "**no entra en la P&L**: son cuentas de balance (IVA "
               "472/477, proveedores 4xx, clientes 43x, retenciones, "
               "caja general 570) que no son ingreso ni gasto — no "
               "necesitan centro. Lo que sí afecta al cuadre son los "
               "*operativos* sin centro (punto 2).")
    by_centro = pd.DataFrame([
        {"Centro": config.CENTER_LABELS.get(c, c),
         "Filas": int(n),
         "Importe neto":
            round(float(df[df["centro"] == c]["importe"].sum()), 2)}
        for c, n in df["centro"].value_counts().to_dict().items()
    ]).sort_values("Filas", ascending=False)
    st.dataframe(by_centro, use_container_width=True, hide_index=True)


# --- Cabecera: titulo + acciones ------------------------------------------
htitle, hsp, b0, b3, b1, b2 = st.columns([4.6, 1.5, 1.5, 1.4, 1.2, 1.2])
_auth_user = st.session_state.get("auth_user")
if _auth_user:
    htitle.markdown(
        "<div class='kratos-sub'>Plan financiero · Multi-centro · "
        "<span style='color:#9aa0aa'>%s</span> "
        "<a href='?logout=1' style='color:#9aa0aa;font-size:11px;"
        "text-decoration:none'>[salir]</a></div>" % _auth_user,
        unsafe_allow_html=True)
    if st.query_params.get("logout"):
        st.session_state.pop("auth_user", None)
        st.query_params.clear()
        st.rerun()
else:
    htitle.markdown("<div class='kratos-sub'>Plan financiero · Multi-centro"
                    "</div>", unsafe_allow_html=True)
if b0.button("📥 Libro diario", use_container_width=True,
             help="Subir el XLSX de Holded (lo real). Cada carga "
                  "reemplaza la anterior; los supuestos y mapeos se "
                  "conservan."):
    dlg_carga()
if b3.button("🔍 Diagnóstico", use_container_width=True,
             help="Comprobaciones sobre el libro diario que afectan "
                  "al cuadre con P&L y Cash flow"):
    dlg_diagnostico()
if b1.button("📤 Exportar", use_container_width=True,
             help="Plantilla de supuestos para el cliente + Excel "
                  "de resultados (P&L y Cash flow)"):
    dlg_export()
if b2.button("⚙ Ajustes", use_container_width=True,
             help="Aperturas de centros, mapeo de cuentas, tasa de IS, "
                  "correcciones de centro"):
    dlg_settings()

# --- Navegacion por centro ------------------------------------------------
sel_label = st.pills(
    "Centro", [l for l, _ in NAV], default="Dashboard",
    selection_mode="single", key="nav_center",
    label_visibility="collapsed")
if not sel_label:
    sel_label = "Dashboard"
code = dict(NAV)[sel_label]

_v_ = _v()
_last_file = _c_meta(_v_, "last_file")
if _last_file:
    st.caption("Datos reales: **%s** (%s → %s)"
               % (_last_file, _c_meta(_v_, "date_min"),
                  _c_meta(_v_, "date_max")))
else:
    st.caption("Aún no hay datos reales. Usa el botón "
               "**📥 Libro diario** para subir el XLSX de Holded.")


# --- Render de contenido ---------------------------------------------------
def _periods_for(model, code):
    if code == "CONSOLIDADO":
        return model.periods
    return model.center_periods.get(code, model.periods)


def _css_kind(k):
    if k == "secchead":
        return "background-color:#2a2f3a;color:#fff;font-weight:700;"
    if k == "total":
        return "background-color:#3a3f4b;color:#fff;font-weight:700;"
    if k == "subtotal":
        return "background-color:#4a3f1f;color:#fff;font-weight:700;"
    if k == "ebitda":
        return "background-color:#FFE08A;color:#111;font-weight:800;"
    if k == "neto":
        return "background-color:#1F7A4D;color:#fff;font-weight:800;"
    if k == "pct":
        return ("color:#9aa0aa;font-style:italic;font-size:80%;"
                "font-weight:300;")
    return ""                                      # detail / cost


def _tbl_to_html(tbl, per):
    """Genera el HTML estilizado de la tabla. Costoso (Styler.to_html)."""
    df_all = tbl.reset_index()
    klist = list(df_all["_kind"]) if "_kind" in df_all.columns \
        else [""] * len(df_all)
    df2 = (df_all.drop(columns=["_kind"])
           if "_kind" in df_all.columns else df_all)

    def _row_style(row):
        return [_css_kind(klist[row.name])] * len(row)

    pct_pos = [i for i, k in enumerate(klist) if k == "pct"]
    head_pos = [i for i, k in enumerate(klist) if k == "secchead"]
    sty = (df2.style.apply(_row_style, axis=1)
              .format("{:,.0f}", subset=pd.IndexSlice[:, per]))
    if pct_pos:
        sty = sty.format("{:,.0f}%", subset=pd.IndexSlice[pct_pos, per])
    if head_pos:
        sty = sty.format(lambda _v: "",
                         subset=pd.IndexSlice[head_pos, per])
    sty = sty.hide(axis="index").set_table_attributes('class="kpl"')
    return sty.to_html()


def _render_table(tbl, per):
    st.markdown('<div class="kpl-wrap">%s</div>' % _tbl_to_html(tbl, per),
                unsafe_allow_html=True)


# HTML cacheados: una vez generados, cambiar de K es solo lectura de RAM.
@st.cache_data(ttl=600, show_spinner=False)
def _c_pnl_html(_v, target):
    m = get_model()
    if m is None:
        return None
    per = _periods_for(m, target)
    is_rate = float(_c_meta(_v, "is_rate", config.IS_RATE_DEFAULT))
    tbl = analytic.pivot_analytic(m.pnl_long, target, per, is_rate=is_rate)
    return _tbl_to_html(tbl, per), m.last_actual_period


@st.cache_data(ttl=600, show_spinner=False)
def _c_pnl_v2_html(_v):
    m = get_model()
    if m is None:
        return None
    per = _periods_for(m, "CONSOLIDADO")
    is_rate = float(_c_meta(_v, "is_rate", config.IS_RATE_DEFAULT))
    tbl = analytic.pivot_analytic_v2(m.pnl_long, per, is_rate=is_rate)
    return _tbl_to_html(tbl, per), m.last_actual_period


@st.cache_data(ttl=600, show_spinner=False)
def _c_cf_html(_v, target, saldo_inicial):
    m = get_model()
    if m is None:
        return None
    per = _periods_for(m, target)
    is_rate = float(_c_meta(_v, "is_rate", config.IS_RATE_DEFAULT))
    tbl = cashflow.pivot_cf(m.cf_long, target, per,
                            saldo_inicial=saldo_inicial, is_rate=is_rate)
    return _tbl_to_html(tbl, per)


def _table_pnl(model, target):
    if model is None:
        st.info("Sube el libro diario con el botón **📥 Libro diario** para ver la P&L.")
        return
    out = _c_pnl_html(_v(), target)
    if out is None:
        return
    html, last_actual = out
    st.caption("Real hasta **%s**; después, proyectado según los supuestos."
               % last_actual)
    st.markdown('<div class="kpl-wrap">%s</div>' % html,
                unsafe_allow_html=True)


def _table_pnl_v2(model):
    """P&L v2 del Consolidado: detalle por centro en lugar de por partida."""
    if model is None:
        st.info("Sube el libro diario con el botón **📥 Libro diario** para ver la P&L.")
        return
    out = _c_pnl_v2_html(_v())
    if out is None:
        return
    html, last_actual = out
    st.caption("Misma estructura que la P&L, con el detalle de cada "
               "sección desglosado por centro (K1 ... K4 + HQ). "
               "Real hasta **%s**." % last_actual)
    st.markdown('<div class="kpl-wrap">%s</div>' % html,
                unsafe_allow_html=True)


def _saldo_inicial(target):
    v = _v()
    if target == "CONSOLIDADO":
        return sum(float(_c_center_params(v, c).get("saldo_inicial", 0)
                         or 0) for c in config.ALL_CENTERS)
    return float(_c_center_params(v, target).get("saldo_inicial", 0)
                 or 0)


def _table_cf(model, target):
    if model is None:
        st.info("Sube el libro diario con el botón **📥 Libro diario** "
                "para ver el cash flow.")
        return
    html = _c_cf_html(_v(), target, _saldo_inicial(target))
    if html is None:
        return
    st.caption("Derivado de la P&L (caja = devengo). Inversión y "
               "financiación, de los movimientos reales del libro diario.")
    st.markdown('<div class="kpl-wrap">%s</div>' % html,
                unsafe_allow_html=True)


def _placeholder(nombre):
    st.info("**%s** — pendiente de definir. Me dirás qué métricas e "
            "información mostrar aquí." % nombre)


MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
         "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _periodo_label(sm, sy, horizon):
    y, m = sy, sm
    m2 = m + horizon - 1
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    return "%s %02d → %s %02d" % (MESES[m - 1], y % 100,
                                  MESES[m2 - 1], y2 % 100)


def _per_label(p):
    return "%s %s" % (MESES[int(p[5:7]) - 1], p[2:4])


def _cb_season(code):
    store.set_season(code, [st.session_state["seas_%d_%s" % (i, code)]
                            for i in range(1, 13)])
    invalidate_model()


def _cb_model_cfg(code):
    store.set_center_model_config(code,
                                  st.session_state["cfg_sm_%s" % code],
                                  st.session_state["cfg_sy_%s" % code],
                                  st.session_state["cfg_hz_%s" % code])
    invalidate_model()


def _cb_socios(code):
    store.set_center_params(code, {
        "iva": st.session_state["soc_iva_%s" % code],
        "aforo": st.session_state["soc_afo_%s" % code],
        "ticket": st.session_state["soc_tkt_%s" % code]})
    invalidate_model()


def _cb_saldo(code):
    store.set_center_params(
        code, {"saldo_inicial": st.session_state["sld_%s" % code]})
    invalidate_model()


def _tabla_de_mando(code, sel_label):
    vv = _v()
    cfg = _c_center_model_config(vv, code)
    # Cabecera + boton GUARDAR
    cH, cBtn = st.columns([5, 1.4])
    with cH:
        st.markdown("##### ⚙ Configuración del modelo · **%s**" % sel_label)
        st.caption("Edita los campos que quieras y pulsa **💾 Guardar** "
                   "arriba a la derecha. Los cambios solo afectan a los "
                   "**meses futuros**: lo que ya esté cerrado con datos "
                   "reales del libro diario nunca se pisa.")
    with cBtn:
        save_clicked = st.button(
            "💾 Guardar cambios", key="save_tdm_%s" % code,
            use_container_width=True, type="primary")

    c1, c2, c3, c4 = st.columns([1.2, 1, 1.3, 1.6])
    sm = c1.selectbox("Mes de inicio", list(range(1, 13)),
                      index=cfg["start_month"] - 1, key="cfg_sm_%s" % code,
                      format_func=lambda i: "%s (mes %d)" % (MESES[i - 1], i))
    years = list(range(2026, 2031))
    sy = c2.selectbox("Año de inicio", years, key="cfg_sy_%s" % code,
                      index=years.index(cfg["start_year"])
                      if cfg["start_year"] in years else 0)
    hors = [12, 24, 36, 48, 60]
    hz = c3.selectbox("Meses de proyección", hors, key="cfg_hz_%s" % code,
                      index=hors.index(cfg["horizon"])
                      if cfg["horizon"] in hors else 2,
                      format_func=lambda h: "%d meses" % h)
    c4.markdown("&nbsp;", unsafe_allow_html=True)
    c4.markdown("**Período:** %s" % _periodo_label(sm, sy, hz))

    sld = float(_c_center_params(vv, code).get("saldo_inicial", 0) or 0)
    sld_val = st.number_input(
        "Saldo de caja inicial (€) — punto de partida del saldo acumulado",
        value=sld, step=100.0, format="%.0f",
        key="sld_%s" % code)

    iva = aforo = ticket = None
    newplan = {}
    if code in config.CENTERS:
        st.divider()
        st.markdown("##### 📈 Ingresos — modelo de socios")
        p = _c_center_params(vv, code)
        e1, e2, e3 = st.columns(3)
        iva = e1.number_input("IVA servicios fitness (%)", min_value=0.0,
                              max_value=100.0, step=1.0,
                              value=float(p.get("iva", 21.0)),
                              key="soc_iva_%s" % code)
        aforo = e2.number_input("Aforo máximo (nº socios)", min_value=0,
                                step=1, value=int(p.get("aforo", 0)),
                                key="soc_afo_%s" % code)
        ticket = e3.number_input("Ticket medio (€/mes)", min_value=0.0,
                                 step=1.0, value=float(p.get("ticket", 0.0)),
                                 key="soc_tkt_%s" % code)
        st.caption("El ingreso de cuotas se calcula con el modelo de abajo "
                   "(socios × ticket × estacionalidad). Se vuelca solo en "
                   "la P&L (meses futuros, desde la apertura).")

        # --- Estacionalidad -------------------------------------------
        st.markdown("##### 📅 Estacionalidad — índice por mes calendario "
                    "(base 100)")
        st.caption("Afecta al ingreso de cuotas: socios × ticket × "
                   "(índice/100).")
        season = _c_season(vv, code)
        scols = st.columns(12)
        for i in range(12):
            scols[i].number_input(
                MESES[i], min_value=0.0, step=5.0,
                value=float(season[i]),
                key="seas_%d_%s" % (i + 1, code))

        # --- Socios, altas y churn (mini-tabla entrada + tabla color)--
        st.markdown("##### 👥 Socios, altas y churn — mes a mes")
        mc = _c_center_model_config(vv, code)
        periods = pnl.month_range(
            "%04d-%02d" % (mc["start_year"], mc["start_month"]),
            mc["horizon"])
        ap = _c_opening_months(vv).get(code) or config.DEFAULT_START_MONTH
        plan = _c_socios_plan(vv, code)
        lbl2per = {_per_label(p): p for p in periods}

        st.markdown("**Entradas** — escribe Altas brutas y Churn (%) "
                    "(un mes por columna):")
        edf = pd.DataFrame(
            {_per_label(p): {
                "Altas brutas": float(plan.get(p, {}).get("altas", 0) or 0),
                "Churn (%) - Bajas": float(
                    plan.get(p, {}).get("churn", 0) or 0)}
             for p in periods},
            index=["Altas brutas", "Churn (%) - Bajas"])
        ed = st.data_editor(edf, use_container_width=True,
                            key="socedit_%s" % code, height=110)

        def _norm(d):
            return {p: (round(float(v.get("altas", 0) or 0), 4),
                        round(float(v.get("churn", 0) or 0), 4))
                    for p, v in d.items()
                    if (v.get("altas", 0) or v.get("churn", 0))}

        newplan = {}
        for lbl, per in lbl2per.items():
            a = ed.loc["Altas brutas", lbl]
            ch = ed.loc["Churn (%) - Bajas", lbl]
            if (a or 0) or (ch or 0):
                newplan[per] = {"altas": float(a or 0),
                                "churn": float(ch or 0)}
        # (Guardado se hace al pulsar el botón, no aquí.)

        # Tabla calculada con colores de marca Kratos (solo lectura,
        # sin repetir Altas/Churn: esas son las entradas de arriba)
        st.markdown("**Resultado calculado:**")
        traj = socios.compute_trajectory(periods, ap, float(aforo),
                                         float(ticket), season, plan)
        _fmt = {
            "Socios inicio mes": lambda v: "{:,.0f}".format(v),
            "Bajas": lambda v: "{:,.0f}".format(v),
            "Socios fin mes": lambda v: "{:,.0f}".format(v),
            "% Ocupacion (%)": lambda v: "{:,.0f}%".format(v),
            "Indice estacionalidad": lambda v: "{:.0f}".format(v),
            "Ingresos cuotas (EUR)": (
                lambda v: "-" if v is None else "{:,.0f}".format(v)),
        }
        calc_rows = [c for c in socios.CONCEPTS
                     if c not in ("Altas brutas", "Churn (%)")]
        disp = {}
        for r in traj:
            disp[_per_label(r["periodo"])] = {
                k: _fmt[k](r[k]) for k in calc_rows}
        ddf = pd.DataFrame(disp, index=calc_rows)[
            [_per_label(p) for p in periods]]
        ddf = ddf.rename(index={"Bajas": "Churn (%) - Bajas"})
        rowcss = {
            "Ingresos cuotas (EUR)":
                "background-color:%s;color:#fff;font-weight:700;" % BRAND_RED,
            "Socios fin mes":
                "background-color:#151515;color:#fff;font-weight:700;",
            "% Ocupacion (%)": "background-color:#2b2f36;color:#fff;",
            "Socios inicio mes": "background-color:#1b1f27;color:#cfd4db;",
            "Churn (%) - Bajas": "background-color:#1b1f27;color:#cfd4db;",
            "Indice estacionalidad":
                "background-color:#3a0d10;color:#f3c3c6;",
        }
        st.dataframe(
            ddf.style.apply(
                lambda row: [rowcss.get(row.name, "")] * len(row), axis=1),
            use_container_width=True,
            height=38 * (len(calc_rows) + 1))
    elif code == "HQ":
        st.caption("HQ es un centro de estructura: sin modelo de socios.")

    # --- Personal (Gastos de personal) — todos los centros, incluido HQ
    st.divider()
    st.markdown("##### 👤 Personal")
    _hq = (code == "HQ")
    st.caption(
        ("Sueldos brutos mensuales del equipo de estructura (CEO/CFO/…); "
         "renombra cada fila en **Nombre**. Se vuelca en la P&L de HQ como "
         "Gastos de personal desde el mes de inicio.") if _hq else
        ("Sueldos brutos mensuales por persona y % de SS de la empresa. "
         "Se vuelca en la P&L como Gastos de personal desde el mes de "
         "inicio de cada uno."))
    pers = _c_personal(vv, code)
    by_rol = {r["rol"]: r for r in pers["rows"]}
    ents = config.PERSONAL_ROLES[:-1]          # primeros 5 roles

    def _mlbl(r):
        return config.MESES_CORTOS[
            max(1, min(12, int(by_rol[r]["mes_inicio"]))) - 1]

    col_in, col_tot = st.columns([3, 2])

    with col_in:
        st.markdown("**Entradas** (editables):")
        order = list(config.PERSONAL_ROLES)
        pdf = pd.DataFrame({
            "Nombre": [by_rol[r]["nombre"] for r in order],
            "Bruto €/mes": [by_rol[r]["bruto"] for r in order],
            "Seg. Social (%)": [by_rol[r]["ss_pct"] for r in order],
            "Mes inicio": [_mlbl(r) for r in order],
        }, index=order)
        pdf.index.name = "Parámetro"
        ped = st.data_editor(
            pdf, use_container_width=True, key="pers_%s" % code,
            height=38 * (len(order) + 1),
            column_config={
                "Parámetro": st.column_config.TextColumn(
                    "Parámetro", width="medium"),
                "Nombre": st.column_config.TextColumn(
                    "Nombre", width="medium"),
                "Bruto €/mes": st.column_config.NumberColumn(
                    "Bruto €/mes", min_value=0.0, step=50.0,
                    format="%.0f", width="small"),
                "Seg. Social (%)": st.column_config.NumberColumn(
                    "Seg. Social (%)", min_value=0.0,
                    max_value=100.0, step=1.0, format="%.0f",
                    width="small"),
                "Mes inicio": st.column_config.SelectboxColumn(
                    "Mes inicio",
                    options=["—"] + config.MESES_CORTOS,
                    required=True, width="small"),
            })

        new_pers = []
        for rol in order:
            ml = ped.loc[rol, "Mes inicio"]
            mi = (config.MESES_CORTOS.index(ml) + 1
                  if ml in config.MESES_CORTOS else 1)
            new_pers.append({
                "rol": rol,
                "nombre": str(ped.loc[rol, "Nombre"] or ""),
                "bruto": float(ped.loc[rol, "Bruto €/mes"] or 0),
                "ss_pct": float(ped.loc[rol, "Seg. Social (%)"] or 0),
                "mes_inicio": mi})

        def _norm_p(rows):
            return tuple(
                (r["rol"], (r["nombre"] or "").strip(),
                 round(float(r["bruto"]), 2),
                 round(float(r.get("ss_pct", 0)), 2),
                 int(r["mes_inicio"]))
                for r in rows)

        # (Guardado se hace al pulsar el botón, no aquí.)

    with col_tot:
        st.markdown("**Totales** (no editables):")
        tot_ent = sum(by_rol[r]["bruto"] for r in ents)
        ss_ent = sum(by_rol[r]["bruto"] * by_rol[r]["ss_pct"] / 100.0
                     for r in ents)
        resp_b = by_rol["Responsable/Admin"]["bruto"]
        ss_resp = resp_b * by_rol["Responsable/Admin"]["ss_pct"] / 100.0
        coste = tot_ent + ss_ent + resp_b + ss_resp

        def _eur(v):
            return "%s €" % "{:,.0f}".format(v) if v else "- €"

        if _hq:
            lbl_top = "TOTAL BRUTO EQUIPO ESTRUCTURA"
            lbl_ss = "SS EQUIPO ESTRUCTURA"
            lbl_resp = "SS RESPONSABLE/ADMIN"
        else:
            lbl_top = "TOTAL BRUTO ENTRENADORES"
            lbl_ss = "SS ENTRENADORES"
            lbl_resp = "SS RESPONSABLE/ADMIN"
        cdf = pd.DataFrame(
            {"€/mes": [_eur(tot_ent), _eur(ss_ent), _eur(ss_resp),
                       _eur(coste)]},
            index=[lbl_top, lbl_ss, lbl_resp,
                   "COSTE TOTAL EMPRESA/MES"])
        rowcss = {
            lbl_top:
                "background-color:#1b1f27;color:#cfd4db;"
                "font-weight:600;",
            lbl_ss:
                "background-color:#264653;color:#ffffff;",
            lbl_resp:
                "background-color:#3a0d10;color:#f3c3c6;",
            "COSTE TOTAL EMPRESA/MES":
                "background-color:%s;color:#ffffff;"
                "font-weight:700;" % BRAND_RED,
        }
        st.dataframe(
            cdf.style.apply(
                lambda r: [rowcss.get(r.name, "")] * len(r), axis=1),
            use_container_width=True, height=38 * 5)

    # --- Gastos por sección de la P&L (partidas fijas) — todos
    st.divider()
    st.markdown("##### 🧾 Gastos por sección de la P&L")
    st.caption("Las partidas son las mismas que aparecen en la P&L. "
               "Solo se editan **€/periodo**, **Frecuencia** y "
               "**Mes inicio** de cada partida.")
    gplan = _c_gastos_plan(vv, code)
    inv_freq = {v: k for k, v in config.FREQ_MONTHS.items()}
    _sec_keys = [k for k, _ in config.GASTOS_SECCIONES]

    def _norm_ap(a):
        a = str(a or "")
        if a in _sec_keys:
            return a
        if a in ("Compras", "Aprovisionamientos"):
            return "Aprovisionamientos"
        if a in ("Resultado Financiero", "Gastos financieros"):
            return "Gastos financieros"
        return "Otros gastos de explotacion"

    all_g = []
    for sec_key, sec_lbl in config.GASTOS_SECCIONES:
        st.markdown("**%s**" % sec_lbl)
        partidas = analytic.GASTOS_PARTIDAS_POR_SECCION.get(sec_key, [])
        # mapa partida -> fila guardada (si existe) en esta seccion
        stored = {r["partida"]: r for r in gplan
                  if _norm_ap(r["apartado"]) == sec_key}

        def _row_for(part):
            r = stored.get(part, {})
            imp = float(r.get("importe", 0) or 0)
            itv = int(r.get("intervalo", 1) or 1)
            mi = int(r.get("mes_inicio", 1) or 1)
            return {
                "Partida": part,
                "€/periodo": imp,
                "Frecuencia": inv_freq.get(itv, "Mensual"),
                "Mes inicio": config.MESES_CORTOS[
                    max(1, min(12, mi)) - 1],
            }

        sdf = pd.DataFrame([_row_for(p) for p in partidas])
        sdf = sdf.astype({
            "Partida": "object", "€/periodo": "float64",
            "Frecuencia": "object", "Mes inicio": "object"})
        sed = st.data_editor(
            sdf, use_container_width=True,
            key="gastos_%s_%s" % (sec_key, code),
            hide_index=True,
            column_config={
                "Partida": st.column_config.TextColumn(
                    "Partida", width="medium", disabled=True),
                "€/periodo": st.column_config.NumberColumn(
                    "€/periodo", min_value=0.0, step=10.0,
                    format="%.0f", width="small"),
                "Frecuencia": st.column_config.SelectboxColumn(
                    "Frecuencia", options=config.FREQ_LABELS,
                    required=False, width="small"),
                "Mes inicio": st.column_config.SelectboxColumn(
                    "Mes inicio", options=config.MESES_CORTOS,
                    required=False, width="small"),
            })
        for _, r in sed.iterrows():
            def _cell(col):
                x = r[col]
                return None if pd.isna(x) else x
            part = ("" if _cell("Partida") is None
                    else str(_cell("Partida")).strip())
            if not part:
                continue
            imp_raw = _cell("€/periodo")
            imp = float(imp_raw) if imp_raw is not None else 0.0
            freq = _cell("Frecuencia") or "Mensual"
            ml = _cell("Mes inicio")
            mi = (config.MESES_CORTOS.index(ml) + 1
                  if ml in config.MESES_CORTOS else 1)
            all_g.append({
                "partida": part, "importe": imp,
                "intervalo": config.FREQ_MONTHS.get(freq, 1),
                "apartado": sec_key, "mes_inicio": mi})

    # --- GUARDAR TODO al pulsar el botón ---
    if save_clicked:
        from concurrent.futures import ThreadPoolExecutor
        with st.spinner("Guardando cambios…"):
            params = {"start_month": int(sm), "start_year": int(sy),
                      "horizon": int(hz),
                      "saldo_inicial": float(sld_val or 0)}
            if iva is not None:
                params["iva"] = float(iva)
            if aforo is not None:
                params["aforo"] = float(aforo)
            if ticket is not None:
                params["ticket"] = float(ticket)
            if code in config.CENTERS:
                for i in range(1, 13):
                    params["season_%d" % i] = float(
                        st.session_state["seas_%d_%s" % (i, code)])

            tasks = [
                ("center_params", lambda: store.set_center_params(
                    code, params)),
                ("personal", lambda: store.set_personal(code, new_pers)),
                ("gastos", lambda: store.set_gastos_plan(code, all_g)),
            ]
            if code in config.CENTERS:
                tasks.append(("socios", lambda: store.set_socios_plan(
                    code, newplan)))

            with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
                futs = {ex.submit(fn): name for name, fn in tasks}
                for f in futs:
                    f.result()      # propaga errores si los hay
            invalidate_model()
            # Redirigir a la P&L tras guardar
            st.session_state["sub_%s" % code] = "PyL"
        st.success("✓ Cambios guardados.")
        st.rerun()


# Pre-calentar caché de todos los centros + consolidado SOLO la primera
# vez de la sesión (no se re-ejecuta aunque alguna escritura invalide
# el modelo). Las invalidaciones limpian _build_model_cached y los HTML
# cacheados; la siguiente lectura los recalcula on-demand (no necesita
# warm-up para todos).
if not st.session_state.get("_warmed_once"):
    with st.spinner("⚡ Preparando todos los centros (solo la primera vez)…"):
        _m_w = get_model()
        if _m_w is not None:
            for _t in (config.ALL_CENTERS + ["CONSOLIDADO"]):
                try:
                    _c_pnl_html(_v(), _t)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    _c_cf_html(_v(), _t, _saldo_inicial(_t))
                except Exception:  # noqa: BLE001
                    pass
            try:
                _c_pnl_v2_html(_v())
            except Exception:  # noqa: BLE001
                pass
    st.session_state["_warmed_once"] = True


if code in DISABLED:
    st.info("El centro **%s** todavía no está disponible. Se activará "
            "cuando exista la sociedad/centro." % sel_label)
elif code == "DASHBOARD":
    st.subheader("Dashboard")
    _placeholder("Dashboard de negocio")
else:
    subs = SUBTABS[code]
    model = get_model()
    tabs = st.tabs(subs)
    for i, name in enumerate(subs):
        with tabs[i]:
            if name == "PyL":
                _table_pnl(model, "CONSOLIDADO" if code == "CONSOLIDADO"
                           else code)
            elif name == "PyL v2 · por centro":
                _table_pnl_v2(model)
            elif name == "Cashflow":
                _table_cf(model, "CONSOLIDADO" if code == "CONSOLIDADO"
                          else code)
            elif name == "Resumen":
                _placeholder("Resumen consolidado")
            elif name == "Tabla de mando":
                _tabla_de_mando(code, sel_label)
