# Kratos — Herramienta de P&L y Cash Flow por centro

App para proyectar y seguir la cuenta de resultados (P&L) y la tesorería
(cash flow) de cada centro (K1 París, K2 Sant Joan, K3 Poble Nou, K4
Valencia + HQ) a 36 meses, combinando lo **real** del libro diario de
Holded con la **proyección** del cliente.

Funciona en dos modos:

- **Local** (en tu Mac): los datos se guardan en `data/kratos.db`
  (SQLite). Sin login. Para desarrollo y para uso del CFE solo.
- **Cloud** (Streamlit Cloud + Supabase Postgres): los datos están en
  una base remota; el acceso requiere login con usuario y contraseña.
  Pensado para que CFE y cliente trabajen juntos sobre el mismo modelo.

---

## Local (Mac) — uso del día a día

### Abrir la app
1. **Doble clic** en `run.command` (Mac) o `run.bat` (Windows). La
   primera vez tarda 1–2 minutos preparando el entorno; luego es
   instantáneo. Se abre sola en el navegador.
2. Para cerrarla: cierra la ventana negra o pulsa `Ctrl+C` en ella.

> Si Mac dice que no puede abrir `run.command`: clic derecho → Abrir →
> Abrir. Solo hace falta la primera vez.

### Flujo semanal del CFE
- **📥 Libro diario** → sube el XLSX de Holded (reemplaza la carga
  anterior, no duplica).
- **🔍 Diagnóstico** → revisa cuentas sin mapear, apuntes sin centro,
  descuadres Debe/Haber, multi-tag, CAPEX, etc.
- **📤 Exportar / Importar** → descarga la plantilla de supuestos para
  el cliente; sube su Excel cuando lo devuelve; descarga el Excel de
  resultados (P&L + Cash flow).
- **⚙ Ajustes** → mes de apertura por centro, mapeo de cuentas, tasa
  de IS, correcciones de "Sin asignar".

---

## Cloud (Streamlit Cloud + Supabase) — uso compartido

### Despliegue inicial (una vez)

1. **GitHub**: subir el código al repo (privado).
2. **Supabase**: crear proyecto, copiar la URL de conexión Postgres.
3. **Streamlit Cloud**: conectar el repo, pegar `DATABASE_URL` y los
   `[users]` como secrets (ver `.streamlit/secrets.toml.example`).
4. La URL pública te la genera Streamlit. Tú y el cliente la usáis con
   vuestro login.

### Mantenimiento

- **Cambios en el código**: editar local → `git push` → Streamlit Cloud
  redespliega solo en 1–2 minutos.
- **Cambios en la base de datos**: los datos viven en Supabase, no se
  pierden al redesplegar.
- **Añadir/quitar usuarios**: editar los secrets en Streamlit Cloud.

---

## Variables de entorno (cloud)

| Variable | Cuándo | Qué |
|---|---|---|
| `DATABASE_URL` | Cloud (Streamlit Cloud) | URL Postgres de Supabase |

Si `DATABASE_URL` no está, la app usa SQLite local (`data/kratos.db`).

---

## Notas técnicas

- **Stack**: Streamlit 1.40 · pandas 2.2 · SQLAlchemy 2.0 · XlsxWriter
  · psycopg2 (cloud) / sqlite3 (local).
- **Tests**: `.venv/bin/python -m pytest tests/`
- **Estructura**:
  - `app.py` — entrada Streamlit y orquestación de la UI.
  - `kratos/` — módulos (ingest, classify, pnl, analytic, cashflow,
    pipeline, store, etc.).
  - `data/kratos.db` — base SQLite local (no se sube al repo).
  - `.streamlit/secrets.toml` — secretos de cloud (no se sube al repo).
  - `.streamlit/secrets.toml.example` — plantilla de ejemplo (sí se
    sube).

---

## Privacidad

- En **local**, los datos del cliente no salen de tu equipo.
- En **cloud**, los datos están en Supabase (encriptados en reposo y en
  tránsito). El acceso requiere login. El repo en GitHub debe ser
  **privado** porque, aunque no contenga datos, contiene la lógica de
  negocio.
