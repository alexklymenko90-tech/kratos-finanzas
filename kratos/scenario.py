"""Exportar/importar 'escenarios' completos como JSON.

Un escenario contiene TODO lo configurable desde la Tabla de mando
(modelo de cada centro, socios, personal, gastos) + ajustes globales
(tasa de IS, clave de prorrateo HQ, aperturas).

NO contiene el libro diario real (ese se sube por separado desde el
boton 📥 Libro diario). Tampoco contiene el cash flow: este se deriva
automaticamente de la P&L cuando se importa un escenario.

Uso tipico: varios socios bajan el JSON del escenario base, cada uno
juega con su propia version en local/cloud, y comparten su JSON entre
ellos sin tocar la data principal.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
from typing import Any, Dict

from sqlalchemy import text

from . import config, store

SCHEMA_VERSION = 1


def export_scenario() -> bytes:
    """Devuelve un JSON (bytes) con toda la configuracion editable."""
    eng = store.get_engine()
    out: Dict[str, Any] = {
        "_schema": SCHEMA_VERSION,
        "_exported_at": _dt.datetime.utcnow().isoformat() + "Z",
        "centers": {},
        "globals": {},
    }
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
        c_rows = conn.execute(text(
            "SELECT centro, apertura FROM centers")).fetchall()
        m_rows = conn.execute(text(
            "SELECT clave, valor FROM meta WHERE clave IN "
            "('is_rate', 'proration_key')")).fetchall()

    # Agrupacion por centro
    for c, lbl, _ in [(c, config.CENTER_LABELS.get(c, c), None)
                      for c in config.ALL_CENTERS]:
        out["centers"][c] = {
            "label": lbl,
            "apertura": "",
            "model_keys": {},
            "socios_plan": {},
            "personal": [],
            "gastos": [],
        }

    for centro, clave, valor in cm_rows:
        if centro in out["centers"]:
            out["centers"][centro]["model_keys"][clave] = valor

    for centro, periodo, altas, churn, bajas in sp_rows:
        if centro in out["centers"]:
            out["centers"][centro]["socios_plan"][periodo] = {
                "altas": float(altas or 0),
                "churn": float(churn or 0),
                "bajas": float(bajas or 0),
            }

    for centro, rol, nombre, bruto, mi, dest, ssp in pp_rows:
        if centro in out["centers"]:
            out["centers"][centro]["personal"].append({
                "rol": rol, "nombre": nombre or "",
                "bruto": float(bruto or 0),
                "mes_inicio": int(mi or 1),
                "destino": dest or "",
                "ss_pct": float(ssp or 0),
            })

    for centro, idx, partida, imp, itv, ap, mi in gp_rows:
        if centro in out["centers"]:
            out["centers"][centro]["gastos"].append({
                "partida": partida or "",
                "importe": float(imp or 0),
                "intervalo": int(itv or 1),
                "apartado": ap or "",
                "mes_inicio": int(mi or 1),
            })

    for centro, apertura in c_rows:
        if centro in out["centers"]:
            out["centers"][centro]["apertura"] = apertura or ""

    for clave, valor in m_rows:
        try:
            out["globals"][clave] = json.loads(valor)
        except (ValueError, TypeError):
            out["globals"][clave] = valor

    return json.dumps(out, indent=2, ensure_ascii=False).encode("utf-8")


def import_scenario(data: bytes) -> dict:
    """Aplica un escenario a la base. Devuelve un dict con un resumen
    de lo importado. Lanza ValueError si el JSON es invalido."""
    try:
        scenario = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("JSON invalido: %s" % e)

    if not isinstance(scenario, dict) or "centers" not in scenario:
        raise ValueError("El JSON no es un escenario valido "
                         "(falta la clave 'centers').")
    if scenario.get("_schema") != SCHEMA_VERSION:
        raise ValueError("Version de esquema incompatible "
                         "(esperado %d, recibido %r). Genera un nuevo "
                         "export desde la version actual de la app."
                         % (SCHEMA_VERSION, scenario.get("_schema")))

    eng = store.get_engine()
    summary = {"centros": 0, "gastos": 0, "personal": 0,
               "socios": 0, "aperturas": 0, "model_keys": 0}

    with eng.begin() as conn:
        # 1) center_model: borrar y reinsertar por cada centro presente
        for centro, data_c in scenario["centers"].items():
            if centro not in config.CENTERS and centro != config.HQ:
                continue
            summary["centros"] += 1
            conn.execute(
                text("DELETE FROM center_model WHERE centro=:c"),
                {"c": centro})
            model_keys = data_c.get("model_keys", {})
            for k, v in model_keys.items():
                conn.execute(text(
                    "INSERT INTO center_model (centro, clave, valor) "
                    "VALUES (:c, :k, :v)"),
                    {"c": centro, "k": str(k), "v": float(v)})
                summary["model_keys"] += 1

            # 2) socios_plan
            conn.execute(
                text("DELETE FROM socios_plan WHERE centro=:c"),
                {"c": centro})
            for periodo, plan in data_c.get("socios_plan", {}).items():
                conn.execute(text(
                    "INSERT INTO socios_plan "
                    "(centro, periodo, altas, churn, bajas) "
                    "VALUES (:c, :p, :a, :ch, :b)"),
                    {"c": centro, "p": str(periodo),
                     "a": float(plan.get("altas", 0) or 0),
                     "ch": float(plan.get("churn", 0) or 0),
                     "b": float(plan.get("bajas", 0) or 0)})
                summary["socios"] += 1

            # 3) personal
            conn.execute(
                text("DELETE FROM personal_plan WHERE centro=:c"),
                {"c": centro})
            for p in data_c.get("personal", []):
                conn.execute(text(
                    "INSERT INTO personal_plan "
                    "(centro, rol, nombre, bruto, mes_inicio, "
                    "destino, ss_pct) "
                    "VALUES (:c, :r, :n, :b, :m, :d, :s)"),
                    {"c": centro, "r": str(p.get("rol", "")),
                     "n": str(p.get("nombre", "") or ""),
                     "b": float(p.get("bruto", 0) or 0),
                     "m": int(p.get("mes_inicio", 1) or 1),
                     "d": str(p.get("destino", "") or ""),
                     "s": float(p.get("ss_pct", 0) or 0)})
                summary["personal"] += 1

            # 4) gastos
            conn.execute(
                text("DELETE FROM gastos_plan WHERE centro=:c"),
                {"c": centro})
            for i, g in enumerate(data_c.get("gastos", [])):
                conn.execute(text(
                    "INSERT INTO gastos_plan "
                    "(centro, idx, partida, importe, intervalo, "
                    "apartado, mes_inicio) "
                    "VALUES (:c, :i, :p, :imp, :it, :ap, :mi)"),
                    {"c": centro, "i": i,
                     "p": str(g.get("partida", "") or ""),
                     "imp": float(g.get("importe", 0) or 0),
                     "it": int(g.get("intervalo", 1) or 1),
                     "ap": str(g.get("apartado", "") or ""),
                     "mi": int(g.get("mes_inicio", 1) or 1)})
                summary["gastos"] += 1

            # 5) apertura (si viene)
            apertura = data_c.get("apertura")
            if apertura:
                conn.execute(text(
                    "UPDATE centers SET apertura=:a WHERE centro=:c"),
                    {"a": str(apertura), "c": centro})
                summary["aperturas"] += 1

        # 6) globals (is_rate, proration_key)
        for k, v in (scenario.get("globals") or {}).items():
            if k not in ("is_rate", "proration_key"):
                continue
            conn.execute(text("DELETE FROM meta WHERE clave=:k"),
                         {"k": k})
            conn.execute(text(
                "INSERT INTO meta (clave, valor) VALUES (:k, :v)"),
                {"k": k, "v": json.dumps(v)})

    return summary


def suggest_filename() -> str:
    ts = _dt.datetime.utcnow().strftime("%Y%m%d_%H%M")
    return "Kratos_escenario_%s.json" % ts
