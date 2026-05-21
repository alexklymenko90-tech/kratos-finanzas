"""Persistencia: SQLite local (data/kratos.db) o Postgres si DATABASE_URL.

Si la variable de entorno DATABASE_URL esta puesta (cloud), la usa
(normalmente Postgres en Supabase). Si no, cae a SQLite local en
data/kratos.db (flujo de desarrollo en el equipo del CFE).

Sobrevive entre cargas semanales. Guarda: el libro normalizado vigente,
el mapeo de cuentas editable, los overrides de centro, los supuestos del
cliente, el mes de apertura por centro y metadatos.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from . import config, pl_mapping

_LEDGER_COLS = [
    "asiento", "linea", "fecha", "periodo", "tipo", "descripcion", "documento",
    "cuenta", "prefijo2", "prefijo3", "cuenta_nombre", "nombre_norm",
    "debe", "haber", "importe", "tags", "punteado", "source_file",
    "centro", "center_method", "multi_tag", "tipo_mov",
    "seccion", "partida", "cuenta_sin_mapear",
]

_engine_cache: Optional[Engine] = None


def _database_url() -> str:
    """URL de SQLAlchemy. DATABASE_URL (cloud) > SQLite local."""
    url = os.environ.get("DATABASE_URL")
    if url:
        # Algunos hosts dan "postgres://"; SQLAlchemy requiere "postgresql://"
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url
    os.makedirs(config.DATA_DIR, exist_ok=True)
    return "sqlite:///" + config.DB_PATH


def get_engine() -> Engine:
    global _engine_cache
    if _engine_cache is None:
        # pool_pre_ping=False evita un ping antes de cada query (ahorra
        # 30-50 ms por consulta). En su lugar reciclamos conexiones
        # cada 10 min para que no caduquen.
        _engine_cache = create_engine(_database_url(), future=True,
                                      pool_pre_ping=False,
                                      pool_recycle=600)
        if _engine_cache.dialect.name == "sqlite":
            with _engine_cache.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    return _engine_cache


def _dialect() -> str:
    return get_engine().dialect.name


def _upsert(conn, table: str, columns: List[str],
            pk_columns: List[str], rows: List[dict]) -> None:
    """UPSERT compatible con SQLite y Postgres."""
    if not rows:
        return
    cols_sql = ", ".join(columns)
    placeholders = ", ".join(":" + c for c in columns)
    d = _dialect()
    if d == "sqlite":
        sql = "INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (
            table, cols_sql, placeholders)
    elif d == "postgresql":
        update_cols = [c for c in columns if c not in pk_columns]
        update_set = ", ".join("%s = EXCLUDED.%s" % (c, c)
                               for c in update_cols)
        pk_sql = ", ".join(pk_columns)
        sql = ("INSERT INTO %s (%s) VALUES (%s) "
               "ON CONFLICT (%s) DO UPDATE SET %s" % (
                   table, cols_sql, placeholders, pk_sql, update_set))
    else:
        raise RuntimeError("Dialecto no soportado: %s" % d)
    conn.execute(text(sql), rows)


# Compatibilidad: algunas partes de la app antigua llamaban a get_conn().
# Devolvemos una conexion DBAPI cruda para esos casos puntuales. Internamente
# preferimos engine.begin() y SQLAlchemy text().
def get_conn():
    return get_engine().raw_connection()


def init_db() -> None:
    engine = get_engine()
    ddl = [
        """CREATE TABLE IF NOT EXISTS pl_mapping (
            clave TEXT PRIMARY KEY, tipo_clave TEXT,
            seccion TEXT, partida TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS center_overrides (
            asiento INTEGER, linea INTEGER, centro TEXT,
            PRIMARY KEY (asiento, linea)
        )""",
        """CREATE TABLE IF NOT EXISTS assumptions (
            centro TEXT, periodo TEXT, concepto TEXT, valor REAL,
            PRIMARY KEY (centro, periodo, concepto)
        )""",
        """CREATE TABLE IF NOT EXISTS centers (
            centro TEXT PRIMARY KEY, label TEXT, apertura TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS center_model (
            centro TEXT, clave TEXT, valor REAL,
            PRIMARY KEY (centro, clave)
        )""",
        """CREATE TABLE IF NOT EXISTS socios_plan (
            centro TEXT, periodo TEXT, altas REAL, churn REAL,
            bajas REAL DEFAULT 0,
            PRIMARY KEY (centro, periodo)
        )""",
        """CREATE TABLE IF NOT EXISTS personal_plan (
            centro TEXT, rol TEXT, nombre TEXT, bruto REAL,
            mes_inicio INTEGER, destino TEXT, ss_pct REAL,
            PRIMARY KEY (centro, rol)
        )""",
        """CREATE TABLE IF NOT EXISTS gastos_plan (
            centro TEXT, idx INTEGER, partida TEXT, importe REAL,
            intervalo INTEGER, apartado TEXT, mes_inicio INTEGER,
            PRIMARY KEY (centro, idx)
        )""",
        """CREATE TABLE IF NOT EXISTS meta (
            clave TEXT PRIMARY KEY, valor TEXT
        )""",
    ]
    with engine.begin() as conn:
        for stmt in ddl:
            conn.execute(text(stmt))
    # Migraciones para BBDD de versiones previas. Cada una en su propia
    # transaccion porque Postgres aborta toda la transaccion al primer
    # error (a diferencia de SQLite).
    for alter in (
        "ALTER TABLE socios_plan ADD COLUMN bajas REAL DEFAULT 0",
        "ALTER TABLE personal_plan ADD COLUMN destino TEXT",
        "ALTER TABLE personal_plan ADD COLUMN ss_pct REAL",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(alter))
        except SQLAlchemyError:
            pass

    # Semillas (solo si vacias)
    with engine.begin() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM pl_mapping")).scalar()
        if n == 0:
            rows = pl_mapping.default_mapping_rows()
            _upsert(conn, "pl_mapping",
                    ["clave", "tipo_clave", "seccion", "partida"],
                    ["clave"],
                    [{"clave": r["clave"], "tipo_clave": r["tipo_clave"],
                      "seccion": r["seccion"], "partida": r["partida"]}
                     for r in rows])
        n = conn.execute(text("SELECT COUNT(*) FROM centers")).scalar()
        if n == 0:
            seed = [{"centro": c, "label": config.CENTER_LABELS[c],
                     "apertura": config.DEFAULT_OPENING_MONTH.get(c, "")}
                    for c in config.CENTERS]
            seed.append({"centro": config.HQ,
                         "label": config.CENTER_LABELS[config.HQ],
                         "apertura": config.DEFAULT_START_MONTH})
            _upsert(conn, "centers", ["centro", "label", "apertura"],
                    ["centro"], seed)


# --- Libro diario ----------------------------------------------------------
def save_ledger(df: pd.DataFrame) -> None:
    out = df.copy()
    out["fecha"] = out["fecha"].astype(str)
    out["tags"] = out["tags"].apply(
        lambda t: ",".join(t) if isinstance(t, (list, tuple)) else (t or ""))
    for c in _LEDGER_COLS:
        if c not in out.columns:
            out[c] = None
    out = out[_LEDGER_COLS]
    out.to_sql("ledger_normalized", get_engine(),
               if_exists="replace", index=False)


def load_ledger() -> Optional[pd.DataFrame]:
    engine = get_engine()
    try:
        df = pd.read_sql(text("SELECT * FROM ledger_normalized"),
                         engine.connect())
    except SQLAlchemyError:
        return None
    if df.empty:
        return None
    df["tags"] = df["tags"].apply(
        lambda s: [x for x in str(s).split(",") if x] if s else [])
    df["multi_tag"] = df["multi_tag"].astype(bool)
    df["cuenta_sin_mapear"] = df["cuenta_sin_mapear"].astype(bool)
    df["cuenta"] = df["cuenta"].astype(str)
    df["prefijo3"] = df["prefijo3"].astype(str)
    return df


# --- Mapeo cuenta -> partida ----------------------------------------------
def load_mapping() -> List[dict]:
    df = pd.read_sql(text("SELECT * FROM pl_mapping"),
                     get_engine().connect())
    return df.to_dict("records")


def save_mapping(rows: List[dict]) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM pl_mapping"))
        _upsert(conn, "pl_mapping",
                ["clave", "tipo_clave", "seccion", "partida"],
                ["clave"],
                [{"clave": str(r["clave"]).strip(),
                  "tipo_clave": r.get("tipo_clave", "prefijo3"),
                  "seccion": r["seccion"], "partida": r["partida"]}
                 for r in rows])


# --- Overrides de centro ---------------------------------------------------
def load_overrides() -> Dict[tuple, str]:
    with get_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT asiento, linea, centro FROM center_overrides")
        ).fetchall()
    return {(int(a), int(l)): c for a, l, c in rows}


def set_override(asiento: int, linea: int, centro: str) -> None:
    with get_engine().begin() as conn:
        _upsert(conn, "center_overrides",
                ["asiento", "linea", "centro"],
                ["asiento", "linea"],
                [{"asiento": int(asiento), "linea": int(linea),
                  "centro": centro}])


# --- Centros / apertura ----------------------------------------------------
def get_centers() -> pd.DataFrame:
    return pd.read_sql(text("SELECT * FROM centers"),
                       get_engine().connect())


def opening_months() -> Dict[str, str]:
    df = get_centers()
    return dict(zip(df["centro"], df["apertura"]))


def set_opening(centro: str, apertura: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE centers SET apertura=:ap WHERE centro=:c"),
            {"ap": apertura, "c": centro})


# --- Supuestos del cliente -------------------------------------------------
def load_assumptions() -> pd.DataFrame:
    try:
        return pd.read_sql(text("SELECT * FROM assumptions"),
                           get_engine().connect())
    except SQLAlchemyError:
        return pd.DataFrame(columns=["centro", "periodo", "concepto", "valor"])


def save_assumptions(df: pd.DataFrame) -> None:
    keep = df[["centro", "periodo", "concepto", "valor"]].copy()
    keep["valor"] = pd.to_numeric(keep["valor"], errors="coerce").fillna(0.0)
    # Borrar e insertar (preserva el esquema y la PK; un 'replace' con
    # to_sql recrearia la tabla SIN la PK y rompería los upserts).
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM assumptions"))
    if not keep.empty:
        keep.to_sql("assumptions", get_engine(),
                    if_exists="append", index=False)


# --- Metadatos -------------------------------------------------------------
def set_meta(clave: str, valor) -> None:
    with get_engine().begin() as conn:
        _upsert(conn, "meta", ["clave", "valor"], ["clave"],
                [{"clave": clave, "valor": json.dumps(valor)}])


def get_meta(clave: str, default=None):
    with get_engine().begin() as conn:
        row = conn.execute(
            text("SELECT valor FROM meta WHERE clave=:c"),
            {"c": clave}).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return row[0]


# --- Parametros por centro (modelo de socios + config del modelo) ---------
def get_center_params(centro: str) -> dict:
    with get_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT clave, valor FROM center_model "
                 "WHERE centro=:c"), {"c": centro}).fetchall()
    d = {"iva": 21.0, "aforo": 0.0, "ticket": 0.0}
    d.update({k: v for k, v in rows})
    return d


def set_center_params(centro: str, params: dict) -> None:
    with get_engine().begin() as conn:
        _upsert(conn, "center_model",
                ["centro", "clave", "valor"],
                ["centro", "clave"],
                [{"centro": centro, "clave": k, "valor": float(v)}
                 for k, v in params.items()])


def get_center_model_config(centro: str) -> dict:
    """Configuracion del modelo POR CENTRO (mes/ano inicio, horizonte)."""
    with get_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT clave, valor FROM center_model "
                 "WHERE centro=:c"), {"c": centro}).fetchall()
    d = {"start_month": 1, "start_year": 2026,
         "horizon": config.HORIZON_MONTHS}
    for k, v in rows:
        if k in d:
            d[k] = int(v)
    return d


def set_center_model_config(centro: str, start_month: int,
                            start_year: int, horizon: int) -> None:
    set_center_params(centro, {"start_month": int(start_month),
                               "start_year": int(start_year),
                               "horizon": int(horizon)})


def center_start_period(centro: str) -> str:
    c = get_center_model_config(centro)
    return "%04d-%02d" % (c["start_year"], c["start_month"])


# --- Estacionalidad (indice por mes calendario, base 100) -----------------
def get_season(centro: str) -> list:
    with get_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT clave, valor FROM center_model "
                 "WHERE centro=:c AND clave LIKE 'season_%'"),
            {"c": centro}).fetchall()
    s = [100.0] * 12
    for k, v in rows:
        try:
            i = int(k.split("_")[1])
            if 1 <= i <= 12:
                s[i - 1] = float(v)
        except (ValueError, IndexError):
            pass
    return s


def set_season(centro: str, season12: list) -> None:
    set_center_params(centro, {"season_%d" % (i + 1): float(season12[i])
                               for i in range(12)})


# --- Plan de socios (altas / churn por periodo) ---------------------------
def get_socios_plan(centro: str) -> dict:
    with get_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT periodo, altas, churn, bajas FROM socios_plan "
                 "WHERE centro=:c"), {"c": centro}).fetchall()
    return {p: {"altas": a or 0, "churn": c or 0, "bajas": b or 0}
            for p, a, c, b in rows}


def set_socios_plan(centro: str, plan: dict) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM socios_plan WHERE centro=:c"),
                     {"c": centro})
        _upsert(conn, "socios_plan",
                ["centro", "periodo", "altas", "churn", "bajas"],
                ["centro", "periodo"],
                [{"centro": centro, "periodo": p,
                  "altas": float(v.get("altas", 0) or 0),
                  "churn": float(v.get("churn", 0) or 0),
                  "bajas": float(v.get("bajas", 0) or 0)}
                 for p, v in plan.items()])


# --- Tabla maestra de Gastos (lista libre con Apartado PL) ----------------
_GASTOS_SEED = [
    ("Compras mercaderías", "Aprovisionamientos", 1, 1),
    ("Arrendamiento local", "Otros gastos de explotacion", 1, 1),
    ("Limpieza", "Otros gastos de explotacion", 1, 1),
    ("Suministros", "Otros gastos de explotacion", 1, 1),
    ("Servicios profesionales", "Otros gastos de explotacion", 1, 1),
    ("Intereses préstamo", "Gastos financieros", 1, 1),
]


def get_gastos_plan(centro: str) -> list:
    """Lista libre de gastos. Set de ejemplo SOLO la primera vez (antes de
    guardar nada para ese centro); despues respeta lo guardado, incluso si
    queda vacio."""
    with get_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT partida, importe, intervalo, apartado, mes_inicio "
                 "FROM gastos_plan WHERE centro=:c ORDER BY idx"),
            {"c": centro}).fetchall()
    if rows:
        return [{"partida": p or "", "importe": float(imp or 0),
                 "intervalo": int(itv or 1),
                 "apartado": ap or config.COST_APARTADOS[0],
                 "mes_inicio": int(mi or 1)}
                for p, imp, itv, ap, mi in rows]
    if get_meta("gastos_init_%s" % centro):
        return []                       # el usuario ya guardo (o vacio)
    return [{"partida": p, "importe": 0.0, "intervalo": itv,
             "apartado": ap, "mes_inicio": mi}
            for p, ap, itv, mi in _GASTOS_SEED]


def set_gastos_plan(centro: str, rows: list) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM gastos_plan WHERE centro=:c"),
                     {"c": centro})
        _upsert(conn, "gastos_plan",
                ["centro", "idx", "partida", "importe",
                 "intervalo", "apartado", "mes_inicio"],
                ["centro", "idx"],
                [{"centro": centro, "idx": i,
                  "partida": str(r.get("partida", "") or ""),
                  "importe": float(r.get("importe", 0) or 0),
                  "intervalo": int(r.get("intervalo", 1) or 1),
                  "apartado": str(r.get("apartado", "")
                                  or config.COST_APARTADOS[0]),
                  "mes_inicio": int(r.get("mes_inicio", 1) or 1)}
                 for i, r in enumerate(rows)])
    set_meta("gastos_init_%s" % centro, True)


# --- Personal (Gastos de personal proyectado) -----------------------------
def get_personal(centro: str) -> dict:
    """{'rows': [{rol,nombre,bruto,mes_inicio,destino,ss_pct}, ...]}."""
    with get_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT rol, nombre, bruto, mes_inicio, destino, ss_pct "
                 "FROM personal_plan WHERE centro=:c"),
            {"c": centro}).fetchall()
    saved = {r[0]: r for r in rows}
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


def set_personal(centro: str, rows: list) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM personal_plan WHERE centro=:c"),
                     {"c": centro})
        _upsert(conn, "personal_plan",
                ["centro", "rol", "nombre", "bruto",
                 "mes_inicio", "destino", "ss_pct"],
                ["centro", "rol"],
                [{"centro": centro, "rol": r["rol"],
                  "nombre": str(r.get("nombre", "") or ""),
                  "bruto": float(r.get("bruto", 0) or 0),
                  "mes_inicio": int(r.get("mes_inicio", 1) or 1),
                  "destino": str(r.get("destino", "") or ""),
                  "ss_pct": float(r.get("ss_pct",
                                        config.SS_PCT_DEFAULT) or 0)}
                 for r in rows])


# --- Reset -----------------------------------------------------------------
def reset_ledger() -> None:
    """Borra solo lo real cargado (libro diario). Mantiene supuestos,
    mapeo, correcciones y aperturas."""
    with get_engine().begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS ledger_normalized"))
        for k in ("last_file", "date_min", "date_max", "last_actual_period"):
            conn.execute(text("DELETE FROM meta WHERE clave=:c"), {"c": k})


def reset_all() -> None:
    """Borra TODO: datos reales, supuestos, correcciones y mapeo (vuelve
    a los valores por defecto)."""
    engine = get_engine()
    with engine.begin() as conn:
        for t in ("ledger_normalized", "pl_mapping", "center_overrides",
                  "assumptions", "centers", "center_model",
                  "socios_plan", "personal_plan", "gastos_plan", "meta"):
            conn.execute(text("DROP TABLE IF EXISTS %s" % t))
    # Recrear con semillas
    init_db()
