"""App local (Streamlit) — Plan financiero multi-centro Kratos / Palavian.

Navegacion por centro (pestanas tipo boton) y sub-pestanas por centro.
Arranca con doble clic en run.command (macOS) o run.bat (Windows).
"""

from __future__ import annotations

import base64
import os
import tempfile

import pandas as pd
import streamlit as st

from kratos import (analytic, assumptions_io, cashflow, config,
                    excel_export, pipeline, pnl, socios, store)

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


def _require_login():
    users = _get_users()
    if not users:
        return                                # modo desarrollo (sin login)
    if st.session_state.get("auth_user"):
        return                                # ya autenticado

    st.markdown(
        """
        <style>
          /* Oculta el menu superior y el footer en la pantalla de login */
          [data-testid="stToolbar"] { display: none; }
          [data-testid="stDecoration"] { display: none; }
          footer { display: none; }
          /* Centra el contenido vertical y horizontalmente */
          section.main > div.block-container {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
            max-width: 100%% !important;
          }
          .kr-login-wrap {
            min-height: 88vh;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
          }
          .kr-login-card {
            background: linear-gradient(180deg,#161b25 0%%,#0E1117 100%%);
            border: 1px solid #232733;
            border-radius: 12px;
            padding: 0 0 26px 0;
            width: 100%%;
            max-width: 420px;
            box-shadow: 0 24px 64px rgba(0,0,0,0.55);
            overflow: hidden;
          }
          .kr-login-banner {
            background: %s;
            padding: 26px 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 18px;
          }
          .kr-login-banner img { height: 48px; width: auto; }
          .kr-login-banner .kr-title {
            color: #fff; font-weight: 800; letter-spacing: 12px;
            font-size: 26px;
          }
          .kr-login-sub {
            text-align: center; color: #9aa0aa; font-size: 12px;
            letter-spacing: 2px; text-transform: uppercase;
            margin: 18px 0 6px 0;
          }
          .kr-login-body { padding: 8px 26px 4px 26px; }
          .kr-login-footer {
            text-align: center; color: #6b7280; font-size: 11px;
            margin-top: 18px;
          }
        </style>
        """ % BRAND_RED, unsafe_allow_html=True)

    _lb = _login_logo_b64()
    logo = ("<img src='data:image/png;base64,%s'/>" % _lb) if _lb else ""

    st.markdown('<div class="kr-login-wrap">', unsafe_allow_html=True)
    st.markdown(
        '<div class="kr-login-card">'
        '<div class="kr-login-banner">%s'
        '<span class="kr-title">KRATOS</span></div>'
        '<div class="kr-login-sub">Plan financiero · multi-centro</div>'
        '<div class="kr-login-body">' % logo,
        unsafe_allow_html=True)

    with st.form("login_form", clear_on_submit=False):
        u = st.text_input("Usuario", key="login_u",
                          placeholder="tu usuario")
        p = st.text_input("Contraseña", type="password", key="login_p",
                          placeholder="••••••••")
        ok = st.form_submit_button("Entrar  →", type="primary",
                                   use_container_width=True)

    st.markdown(
        '</div>'
        '<div class="kr-login-footer">Acceso restringido · '
        'sesión cifrada</div>'
        '</div>'
        '</div>', unsafe_allow_html=True)

    if ok:
        if u in users and str(users[u]) == p:
            st.session_state["auth_user"] = u
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")
    st.stop()


_require_login()

store.init_db()


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
    "HQ": ["Tabla de mando", "PyL"],
    "K1": ["Tabla de mando", "PyL", "Cashflow"],
    "K2": ["Tabla de mando", "PyL", "Cashflow"],
    "K3": ["Tabla de mando", "PyL", "Cashflow"],
    "K4": ["Tabla de mando", "PyL", "Cashflow"],
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
                st.success("Importados %d valores en %d centros."
                           % (len(adf), adf["centro"].nunique()))
                st.button("Cerrar", on_click=st.rerun)

    st.divider()
    st.markdown("##### Excel de resultados (P&L + Cash flow)")
    m = pipeline.build_model()
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


@st.dialog("Ajustes")
def dlg_settings():
    st.markdown("##### Mes de apertura por centro")
    cdf = store.get_centers()
    ed = st.data_editor(cdf, use_container_width=True, key="c_ed",
                        disabled=["centro", "label"])
    if st.button("Guardar aperturas"):
        for _, r in ed.iterrows():
            store.set_opening(r["centro"], str(r["apertura"]).strip())
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
        st.success("Guardado.")

    st.markdown("##### Impuesto de Sociedades")
    isr = float(store.get_meta("is_rate", config.IS_RATE_DEFAULT))
    new_isr = st.number_input(
        "Tasa de IS (%) — se aplica sobre el EBT positivo",
        min_value=0.0, max_value=100.0, step=1.0, value=isr)
    if st.button("Guardar tasa IS"):
        store.set_meta("is_rate", float(new_isr))
        st.success("Guardado.")

    st.markdown("##### Mapeo de cuentas → (seccion, partida)")
    mdf = pd.DataFrame(store.load_mapping())
    med = st.data_editor(mdf, use_container_width=True, num_rows="dynamic",
                         key="m_ed", height=240)
    if st.button("Guardar mapeo"):
        store.save_mapping(med.to_dict("records"))
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

if store.get_meta("last_file"):
    st.caption("Datos reales: **%s** (%s → %s)"
               % (store.get_meta("last_file"), store.get_meta("date_min"),
                  store.get_meta("date_max")))
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


def _render_table(tbl, per):
    df_all = tbl.reset_index()                     # Concepto + periodos + _kind
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
    st.markdown('<div class="kpl-wrap">%s</div>' % sty.to_html(),
                unsafe_allow_html=True)


def _table_pnl(model, target):
    if model is None:
        st.info("Sube el libro diario con el botón **📥 Libro diario** para ver la P&L.")
        return
    st.caption("Real hasta **%s**; después, proyectado según los supuestos."
               % model.last_actual_period)
    per = _periods_for(model, target)
    is_rate = float(store.get_meta("is_rate", config.IS_RATE_DEFAULT))
    tbl = analytic.pivot_analytic(model.pnl_long, target, per,
                                  is_rate=is_rate)
    _render_table(tbl, per)


def _table_pnl_v2(model):
    """P&L v2 del Consolidado: detalle por centro en lugar de por partida."""
    if model is None:
        st.info("Sube el libro diario con el botón **📥 Libro diario** para ver la P&L.")
        return
    st.caption("Misma estructura que la P&L, con el detalle de cada "
               "sección desglosado por centro (K1 ... K4 + HQ). "
               "Real hasta **%s**." % model.last_actual_period)
    per = _periods_for(model, "CONSOLIDADO")
    is_rate = float(store.get_meta("is_rate", config.IS_RATE_DEFAULT))
    tbl = analytic.pivot_analytic_v2(model.pnl_long, per, is_rate=is_rate)
    _render_table(tbl, per)


def _saldo_inicial(target):
    if target == "CONSOLIDADO":
        return sum(float(store.get_center_params(c).get("saldo_inicial", 0)
                         or 0) for c in config.ALL_CENTERS)
    return float(store.get_center_params(target).get("saldo_inicial", 0)
                 or 0)


def _table_cf(model, target):
    if model is None:
        st.info("Sube el libro diario con el botón **📥 Libro diario** "
                "para ver el cash flow.")
        return
    st.caption("Derivado de la P&L (caja = devengo). Inversión y "
               "financiación, de los movimientos reales del libro diario.")
    per = _periods_for(model, target)
    is_rate = float(store.get_meta("is_rate", config.IS_RATE_DEFAULT))
    tbl = cashflow.pivot_cf(model.cf_long, target, per,
                            saldo_inicial=_saldo_inicial(target),
                            is_rate=is_rate)
    _render_table(tbl, per)


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


def _cb_model_cfg(code):
    store.set_center_model_config(code,
                                  st.session_state["cfg_sm_%s" % code],
                                  st.session_state["cfg_sy_%s" % code],
                                  st.session_state["cfg_hz_%s" % code])


def _cb_socios(code):
    store.set_center_params(code, {
        "iva": st.session_state["soc_iva_%s" % code],
        "aforo": st.session_state["soc_afo_%s" % code],
        "ticket": st.session_state["soc_tkt_%s" % code]})


def _cb_saldo(code):
    store.set_center_params(
        code, {"saldo_inicial": st.session_state["sld_%s" % code]})


def _tabla_de_mando(code, sel_label):
    cfg = store.get_center_model_config(code)
    st.markdown("##### ⚙ Configuración del modelo · **%s**" % sel_label)
    st.caption("Independiente por centro: estos cambios afectan **solo** a "
               "la P&L y el Cash flow de **%s**. Se aplican automáticamente."
               % sel_label)
    c1, c2, c3, c4 = st.columns([1.2, 1, 1.3, 1.6])
    sm = c1.selectbox("Mes de inicio", list(range(1, 13)),
                      index=cfg["start_month"] - 1, key="cfg_sm_%s" % code,
                      on_change=_cb_model_cfg, args=(code,),
                      format_func=lambda i: "%s (mes %d)" % (MESES[i - 1], i))
    years = list(range(2026, 2031))
    sy = c2.selectbox("Año de inicio", years, key="cfg_sy_%s" % code,
                      on_change=_cb_model_cfg, args=(code,),
                      index=years.index(cfg["start_year"])
                      if cfg["start_year"] in years else 0)
    hors = [12, 24, 36, 48, 60]
    hz = c3.selectbox("Meses de proyección", hors, key="cfg_hz_%s" % code,
                      on_change=_cb_model_cfg, args=(code,),
                      index=hors.index(cfg["horizon"])
                      if cfg["horizon"] in hors else 2,
                      format_func=lambda h: "%d meses" % h)
    c4.markdown("&nbsp;", unsafe_allow_html=True)
    c4.markdown("**Período:** %s" % _periodo_label(sm, sy, hz))

    sld = float(store.get_center_params(code).get("saldo_inicial", 0) or 0)
    st.number_input(
        "Saldo de caja inicial (€) — punto de partida del saldo acumulado",
        value=sld, step=100.0, format="%.0f",
        key="sld_%s" % code, on_change=_cb_saldo, args=(code,))

    if code in config.CENTERS:
        st.divider()
        st.markdown("##### 📈 Ingresos — modelo de socios")
        p = store.get_center_params(code)
        e1, e2, e3 = st.columns(3)
        iva = e1.number_input("IVA servicios fitness (%)", min_value=0.0,
                              max_value=100.0, step=1.0,
                              value=float(p.get("iva", 21.0)),
                              key="soc_iva_%s" % code,
                              on_change=_cb_socios, args=(code,))
        aforo = e2.number_input("Aforo máximo (nº socios)", min_value=0,
                                step=1, value=int(p.get("aforo", 0)),
                                key="soc_afo_%s" % code,
                                on_change=_cb_socios, args=(code,))
        ticket = e3.number_input("Ticket medio (€/mes)", min_value=0.0,
                                 step=1.0, value=float(p.get("ticket", 0.0)),
                                 key="soc_tkt_%s" % code,
                                 on_change=_cb_socios, args=(code,))
        st.caption("El ingreso de cuotas se calcula con el modelo de abajo "
                   "(socios × ticket × estacionalidad). Se vuelca solo en "
                   "la P&L (meses futuros, desde la apertura).")

        # --- Estacionalidad -------------------------------------------
        st.markdown("##### 📅 Estacionalidad — índice por mes calendario "
                    "(base 100)")
        st.caption("Afecta al ingreso de cuotas: socios × ticket × "
                   "(índice/100).")
        season = store.get_season(code)
        scols = st.columns(12)
        for i in range(12):
            scols[i].number_input(
                MESES[i], min_value=0.0, step=5.0,
                value=float(season[i]), key="seas_%d_%s" % (i + 1, code),
                on_change=_cb_season, args=(code,))

        # --- Socios, altas y churn (mini-tabla entrada + tabla color)--
        st.markdown("##### 👥 Socios, altas y churn — mes a mes")
        mc = store.get_center_model_config(code)
        periods = pnl.month_range(
            "%04d-%02d" % (mc["start_year"], mc["start_month"]),
            mc["horizon"])
        ap = store.opening_months().get(code) or config.DEFAULT_START_MONTH
        plan = store.get_socios_plan(code)
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
        if _norm(newplan) != _norm(plan):
            store.set_socios_plan(code, newplan)
            st.rerun()

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
    pers = store.get_personal(code)
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

        if _norm_p(new_pers) != _norm_p(pers["rows"]):
            store.set_personal(code, new_pers)
            st.rerun()

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
    gplan = store.get_gastos_plan(code)
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
            def _v(col):
                x = r[col]
                return None if pd.isna(x) else x
            part = ("" if _v("Partida") is None
                    else str(_v("Partida")).strip())
            if not part:
                continue
            imp_raw = _v("€/periodo")
            imp = float(imp_raw) if imp_raw is not None else 0.0
            freq = _v("Frecuencia") or "Mensual"
            ml = _v("Mes inicio")
            mi = (config.MESES_CORTOS.index(ml) + 1
                  if ml in config.MESES_CORTOS else 1)
            all_g.append({
                "partida": part, "importe": imp,
                "intervalo": config.FREQ_MONTHS.get(freq, 1),
                "apartado": sec_key, "mes_inicio": mi})
    store.set_gastos_plan(code, all_g)


if code in DISABLED:
    st.info("El centro **%s** todavía no está disponible. Se activará "
            "cuando exista la sociedad/centro." % sel_label)
elif code == "DASHBOARD":
    st.subheader("Dashboard")
    _placeholder("Dashboard de negocio")
else:
    subs = SUBTABS[code]
    model = pipeline.build_model()
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
