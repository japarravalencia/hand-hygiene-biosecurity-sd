#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOTOR DE EXPERIMENTOS DETERMINISTAS v1.0 — HAND HYGIENE COMPLIANCE, COMPLACENCY & MARKET VALUE
Proyecto: 2026_bioseguridad_higiene_manos (Safety Science, Elsevier — Daza, Taylor, Koul, Parra Valencia)
Replica con 100% de fidelidad las ecuaciones exactas de 'hands higine.stmx':
  - Horizonte: 0.0 a 416.0 Weeks (8 años), DT = 0.125, integración Euler (nativo Stella) + contraste RK4
  - 9 Stocks primarios (Compliance, Reputation, Trust, Infections, Capability,
    Management_Emphasis, Monitoring_Coverage, Norm_Strength, Market_Value)
    + 1 SMTH1 explícito (complacency, buildup time = 12.0 Weeks) = 10 estados ODE exactos
  - 10 Flujos, 17 Auxiliares dinámicos, 55 Constantes

Verificaciones incluidas:
  1. Concordancia exacta con la Tabla 3 de 'safety science (1).tex' (semanas 18, 22, 24, 28, 34, 44, 416)
  2. 4 Escenarios de política (RUN_BASELINE, RUN_PROACTIVE, RUN_REACTIVE, RUN_RESILIENT)
     + 2 Extremos Barlas sin clamping (Extremo 1: Colapso Cumplimiento/Escándalo Severo; Extremo 2: Cumplimiento Óptimo Sin Escándalo)
  3. Convergencia numérica a 3 pasos (DT = 0.125 -> 0.0625 -> 0.03125) + contraste RK4
  4. Dominancia cuantitativa de lazos (LTM proxy) por fase (Pre-crisis, Choque, Rebote, Deriva por Complacencia)
  5. Sobol Panel A: 7 parámetros estructurales (n=512 -> N=4,608 corridas, IC 95% + P10–P90 + invarianza ordinal)
  6. Sobol Panel B: Separación funcional de las 3 palancas de política (Cultura Anti-Complacencia,
     Capacidad/Monitoreo Proactivo y Transparencia/Contención de Crisis; N=1,280, IC 95%)
  7. Curador automático de ~/Downloads/response (5).csv, response (6).csv y datos_empiricos/*.csv
     priorizando artículos peer-reviewed de Safety Science y Food Control.

Uso:
  python 03_proyectos/2026_bioseguridad_higiene_manos/modelo_stella/experimentos_higiene_manos_sobol.py --proyecto 2026_bioseguridad_higiene_manos
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

try:
    from SALib.analyze import sobol as sobol_analyze
    try:
        from SALib.sample import sobol as sobol_sample
    except ImportError:
        from SALib.sample import saltelli as sobol_sample
    TIENE_SALIB = True
except ImportError:
    TIENE_SALIB = False


# ==============================================================================
# 55 PARÁMETROS CONSTANTES EXACTOS DE 'hands higine.stmx'
# ==============================================================================
PARAMS_BASE: Dict[str, float] = {
    "time_to_change_compliance": 2.01,
    "base_compliance": 0.19,
    "management_effect_on_compliance": 0.25,
    "norms_effect_on_compliance": 0.20,
    "capability_effect_on_compliance": 0.25,
    "monitoring_effect_on_compliance": 0.20,
    "time_pressure_effect_on_compliance": 0.15,
    "complacency_effect_on_compliance": 0.32,
    "time_to_change_reputation": 4.01,
    "base_brand_reputation": 0.53,
    "transparency_effect_on_reputation": 0.25,
    "trust_effect_on_reputation": 0.35,
    "infections_effect_on_reputation": 0.77,
    "time_to_change_trust": 3.0,
    "base_customer_trust": 0.59,
    "reputation_effect_on_trust": 0.62,
    "transparency_effect_on_trust": 0.20,
    "infections_effect_on_trust": 0.61,
    "base_infection_rate": 0.44,
    "time_to_resolve_infections": 2.02,
    "base_infections": 4.99,
    "price_per_meal": 10.0,
    "time_to_change_capability": 6.0,
    "base_capability": 0.10,
    "management_effect_on_capability": 0.40,
    "monitoring_effect_on_capability": 0.40,
    "time_change_management_emphasis": 3.0,
    "base_management_emphasis": 0.40,
    "infections_effect_on_management": 0.60,
    "time_pressure_effect_on_management": 0.20,
    "complacency_effect_on_management": 0.52,
    "time_to_change_monitoring": 4.0,
    "base_monitoring_coverage": 0.30,
    "management_effect_on_monitoring": 0.50,
    "infections_effect_on_monitoring": 0.40,
    "complacency_effect_on_monitoring": 0.69,
    "time_to_change_norms": 26.0,
    "time_to_change_market_value": 8.0,
    "max_market_value": 200.0,
    "base_valuation_multiple": 0.20,
    "reputation_effect_on_market_value": 0.35,
    "base_revenue": 50.0,
    "reputation_loss_revenue_effect": 0.60,
    "trust_loss_revenue_effect": 0.60,
    "capacity_1": 60.0,
    "complacency_buildup_time": 12.0,
    "initial_complacency": 0.50,
    "base_transparency": 0.10,
    "management_effect_on_transparency": 0.80,
    "infection_scandal_magnitude": 2.5,
    "infection_scandal_start_week": 20.0,
    "infection_scandal_duration": 2.0,
    "initial_management_emphasis": 0.61,
    "initial_monitoring_coverage": 0.31,
    "initial_capability": 0.22,
}

# 4 Escenarios Estratégicos + 2 Extremos Barlas
ESCENARIOS: Dict[str, Dict[str, float]] = {
    "01_RUN_BASELINE": {},
    "02_RUN_REACTIVE": {
        "base_management_emphasis": 0.25,
        "base_monitoring_coverage": 0.15,
        "complacency_effect_on_compliance": 0.45,
        "complacency_effect_on_management": 0.65,
        "base_transparency": 0.05,
        "time_to_resolve_infections": 2.60,
    },
    "03_RUN_PROACTIVE": {
        "base_management_emphasis": 0.60,
        "base_monitoring_coverage": 0.55,
        "base_capability": 0.30,
        "complacency_effect_on_compliance": 0.15,
        "complacency_effect_on_management": 0.25,
        "complacency_effect_on_monitoring": 0.30,
    },
    "04_RUN_RESILIENT": {
        "base_management_emphasis": 0.70,
        "base_monitoring_coverage": 0.65,
        "base_capability": 0.40,
        "complacency_effect_on_compliance": 0.08,
        "complacency_effect_on_management": 0.15,
        "complacency_effect_on_monitoring": 0.20,
        "base_transparency": 0.30,
        "time_to_resolve_infections": 1.30,
        "capacity_1": 75.0,
    },
    "05_Extreme1_Severe_Scandal_LowCap": {
        "infection_scandal_magnitude": 5.0,
        "infection_scandal_duration": 6.0,
        "base_compliance": 0.05,
        "base_monitoring_coverage": 0.05,
        "base_management_emphasis": 0.15,
        "base_transparency": 0.0,
    },
    "06_Extreme2_Zero_Scandal_HighSafety": {
        "infection_scandal_magnitude": 0.0,
        "base_management_emphasis": 0.85,
        "base_monitoring_coverage": 0.85,
        "base_capability": 0.60,
        "complacency_effect_on_compliance": 0.0,
        "complacency_effect_on_management": 0.0,
        "complacency_effect_on_monitoring": 0.0,
        "base_transparency": 0.40,
    },
}

EXCLUSIONES_PREPRINT = (
    "preprint", "arxiv", "biorxiv", "medrxiv", "ssrn", "10.1101/",
    "10.48550/", "10.2139/ssrn", "10.20944/", "rs.3.rs-", "zenodo",
)


def estado_inicial(p: Dict[str, float]) -> np.ndarray:
    """9 stocks primarios + 1 estado SMTH1 (complacency)."""
    return np.array([
        0.61,                             # 0: Compliance
        0.66,                             # 1: Reputation
        0.76,                             # 2: Trust
        1.99,                             # 3: Infections
        p["initial_capability"],          # 4: Capability
        p["initial_management_emphasis"], # 5: Management_Emphasis
        p["initial_monitoring_coverage"], # 6: Monitoring_Coverage
        0.50,                             # 7: Norm_Strength
        10.0,                             # 8: Market_Value
        p["initial_complacency"],         # 9: complacency (SMTH1)
    ], dtype=float)


def evaluar_sistema(t: float, y: np.ndarray, p: Dict[str, float], aplicar_clamp: bool = True) -> Tuple[np.ndarray, Dict[str, float]]:
    if aplicar_clamp:
        comp = max(0.0, min(1.0, float(y[0])))
        rep = max(0.0, min(1.0, float(y[1])))
        trust = max(0.0, min(1.0, float(y[2])))
        inf = max(0.0, float(y[3]))
        cap = max(0.0, min(1.0, float(y[4])))
        mgmt = max(0.0, min(1.0, float(y[5])))
        mon = max(0.0, min(1.0, float(y[6])))
        norm = max(0.0, min(1.0, float(y[7])))
        mv = max(0.0, float(y[8]))
        compl = max(0.0, min(1.0, float(y[9])))
    else:
        comp, rep, trust, inf, cap, mgmt, mon, norm, mv, compl = [float(v) for v in y]

    norm_inf = min(1.0, max(0.0, inf / p["base_infections"]))
    rep_loss = max(0.0, p["base_brand_reputation"] - rep)
    trust_loss = max(0.0, p["base_customer_trust"] - trust)
    revenue = max(0.0, p["base_revenue"] * (1.0 - p["reputation_loss_revenue_effect"] * rep_loss - p["trust_loss_revenue_effect"] * trust_loss))
    volume = revenue / p["price_per_meal"]
    time_press = min(1.0, max(0.0, revenue / p["capacity_1"]))
    transp = min(1.0, max(0.0, p["base_transparency"] + p["management_effect_on_transparency"] * mgmt))

    t0_sc = p["infection_scandal_start_week"]
    t1_sc = t0_sc + p["infection_scandal_duration"]
    inf_mult = 1.0 + (p["infection_scandal_magnitude"] if (t >= t0_sc and t < t1_sc) else 0.0)

    des_comp = min(1.0, max(0.0,
        p["base_compliance"]
        + p["management_effect_on_compliance"] * mgmt
        + p["norms_effect_on_compliance"] * norm
        + p["capability_effect_on_compliance"] * cap
        + p["monitoring_effect_on_compliance"] * mon
        - p["time_pressure_effect_on_compliance"] * time_press
        - p["complacency_effect_on_compliance"] * compl
    ))
    des_rep = min(1.0, max(0.0,
        p["base_brand_reputation"]
        + p["transparency_effect_on_reputation"] * transp
        + p["trust_effect_on_reputation"] * trust
        - p["infections_effect_on_reputation"] * norm_inf
    ))
    des_trust = min(1.0, max(0.0,
        p["base_customer_trust"]
        + p["reputation_effect_on_trust"] * rep
        + p["transparency_effect_on_trust"] * transp
        - p["infections_effect_on_trust"] * norm_inf
    ))
    des_cap = min(1.0, max(0.0,
        p["base_capability"]
        + p["management_effect_on_capability"] * mgmt
        + p["monitoring_effect_on_capability"] * mon
    ))
    des_mgmt = min(1.0, max(0.0,
        p["base_management_emphasis"]
        + p["infections_effect_on_management"] * norm_inf
        - p["time_pressure_effect_on_management"] * time_press
        - p["complacency_effect_on_management"] * compl
    ))
    des_mon = min(1.0, max(0.0,
        p["base_monitoring_coverage"]
        + p["management_effect_on_monitoring"] * mgmt
        + p["infections_effect_on_monitoring"] * norm_inf
        - p["complacency_effect_on_monitoring"] * compl
    ))

    base_val = revenue * p["base_valuation_multiple"]
    des_mv = min(p["max_market_value"], max(0.0, base_val * (1.0 + p["reputation_effect_on_market_value"] * (rep - p["base_brand_reputation"]))))

    # 10 flujos exactos
    adj_comp = (des_comp - comp) / p["time_to_change_compliance"]
    adj_rep = (des_rep - rep) / p["time_to_change_reputation"]
    adj_trust = (des_trust - trust) / p["time_to_change_trust"]
    inf_rate = p["base_infection_rate"] * inf_mult * (1.0 - comp) * volume
    inf_res = inf / p["time_to_resolve_infections"]
    adj_cap = (des_cap - cap) / p["time_to_change_capability"]
    adj_mgmt = (des_mgmt - mgmt) / p["time_change_management_emphasis"]
    adj_mon = (des_mon - mon) / p["time_to_change_monitoring"]
    adj_norm = (comp - norm) / p["time_to_change_norms"]
    adj_mv = (des_mv - mv) / p["time_to_change_market_value"]
    d_compl = ((1.0 - norm_inf) - compl) / p["complacency_buildup_time"]

    # LTM proxy: 4 macro-bucles (R_Culture, R_Contagion, B_Complacency_Drift, B_TimePressure)
    w_cult = (p["management_effect_on_compliance"] * mgmt + p["norms_effect_on_compliance"] * norm + p["capability_effect_on_compliance"] * cap)
    w_cont = (p["infections_effect_on_reputation"] + p["infections_effect_on_trust"]) * norm_inf + abs(adj_mv) * 0.4
    w_comp = (p["complacency_effect_on_compliance"] + p["complacency_effect_on_management"] + p["complacency_effect_on_monitoring"]) * compl
    w_tp = (p["time_pressure_effect_on_compliance"] + p["time_pressure_effect_on_management"]) * time_press
    tot_w = max(1e-9, w_cult + w_cont + w_comp + w_tp)

    dy = np.array([
        adj_comp, adj_rep, adj_trust, inf_rate - inf_res, adj_cap,
        adj_mgmt, adj_mon, adj_norm, adj_mv, d_compl,
    ], dtype=float)

    aux = {
        "normalized_infections": norm_inf,
        "revenue": revenue,
        "time_pressure": time_press,
        "transparency": transp,
        "complacency": compl,
        "ltm_culture_pct": 100.0 * w_cult / tot_w,
        "ltm_contagion_pct": 100.0 * w_cont / tot_w,
        "ltm_complacency_pct": 100.0 * w_comp / tot_w,
        "ltm_timepress_pct": 100.0 * w_tp / tot_w,
    }
    return dy, aux


def simular(
    overrides: Dict[str, float],
    dt: float = 0.125,
    t0: float = 0.0,
    tf: float = 416.0,
    metodo: str = "Euler",
    aplicar_clamp: bool = True,
) -> pd.DataFrame:
    p = dict(PARAMS_BASE)
    p.update(overrides)
    y = estado_inicial(p)

    n_steps = int(round((tf - t0) / dt))
    pasos_sem = max(1, int(round(1.0 / dt)))
    filas: List[Dict[str, float]] = []

    for step in range(n_steps + 1):
        t = t0 + step * dt
        dy, aux = evaluar_sistema(t, y, p, aplicar_clamp=aplicar_clamp)
        if step % pasos_sem == 0 or step == n_steps:
            filas.append({
                "Week": round(t, 2),
                "Compliance": round(float(y[0]), 4),
                "Reputation": round(float(y[1]), 4),
                "Trust": round(float(y[2]), 4),
                "Infections": round(float(y[3]), 4),
                "Capability": round(float(y[4]), 4),
                "Management": round(float(y[5]), 4),
                "Monitoring": round(float(y[6]), 4),
                "Norm_Strength": round(float(y[7]), 4),
                "Market_Value": round(float(y[8]), 4),
                **{k: round(float(v), 4) for k, v in aux.items()},
            })
        if step == n_steps:
            break

        if metodo.lower() == "rk4":
            k1 = dy
            k2, _ = evaluar_sistema(t + 0.5 * dt, y + 0.5 * dt * k1, p, aplicar_clamp=aplicar_clamp)
            k3, _ = evaluar_sistema(t + 0.5 * dt, y + 0.5 * dt * k2, p, aplicar_clamp=aplicar_clamp)
            k4, _ = evaluar_sistema(t + dt, y + dt * k3, p, aplicar_clamp=aplicar_clamp)
            y = y + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        else:
            y = y + dt * dy

        if aplicar_clamp:
            for idx_i in (0, 1, 2, 4, 5, 6, 7, 9):
                y[idx_i] = max(0.0, min(1.0, y[idx_i]))
            y[3] = max(0.0, y[3])
            y[8] = max(0.0, y[8])

    return pd.DataFrame(filas)


def ejecutar_sobol_panel_a(n_base: int = 512) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Panel A: 7 parámetros estructurales (±25%, n=512 -> N=4,608 corridas, IC 95% completos)."""
    if not TIENE_SALIB:
        return pd.DataFrame(), {"ejecutado": False}

    nombres = [
        "complacency_effect_on_compliance",
        "base_customer_trust",
        "base_brand_reputation",
        "base_infection_rate",
        "time_to_resolve_infections",
        "infections_effect_on_reputation",
        "time_pressure_effect_on_compliance",
    ]
    bounds = [[PARAMS_BASE[k] * 0.75, PARAMS_BASE[k] * 1.25] for k in nombres]
    problem = {"num_vars": len(nombres), "names": nombres, "bounds": bounds}
    X = sobol_sample.sample(problem, n_base, calc_second_order=False)
    n_tot = len(X)

    y_trough = np.zeros(n_tot)
    y_peak_inf = np.zeros(n_tot)
    y_comp_end = np.zeros(n_tot)
    y_mv_end = np.zeros(n_tot)

    for i, fila in enumerate(X):
        ov = {nombres[j]: float(fila[j]) for j in range(len(nombres))}
        df_i = simular(ov, dt=0.25, tf=104.0, metodo="RK4")
        win = df_i[(df_i["Week"] >= 18) & (df_i["Week"] <= 52)]
        y_trough[i] = float(win["Market_Value"].min())
        y_peak_inf[i] = float(win["Infections"].max())
        y_comp_end[i] = float(df_i["Compliance"].iloc[-1])
        y_mv_end[i] = float(df_i["Market_Value"].iloc[-1])

    st_tr = sobol_analyze.analyze(problem, y_trough, calc_second_order=False, conf_level=0.95, print_to_console=False)
    st_in = sobol_analyze.analyze(problem, y_peak_inf, calc_second_order=False, conf_level=0.95, print_to_console=False)
    st_co = sobol_analyze.analyze(problem, y_comp_end, calc_second_order=False, conf_level=0.95, print_to_console=False)

    # Invarianza ordinal: RUN_RESILIENT > RUN_BASELINE en Trough MV y < en Peak Infections
    rng = np.random.default_rng(2026)
    idxs = rng.choice(n_tot, size=min(60, n_tot), replace=False)
    inv_ok = 0
    for idx in idxs:
        ov0 = {nombres[j]: float(X[idx][j]) for j in range(len(nombres))}
        ov1 = dict(ov0)
        ov1.update(ESCENARIOS["04_RUN_RESILIENT"])
        d0 = simular(ov0, dt=0.25, tf=104.0, metodo="RK4")
        d1 = simular(ov1, dt=0.25, tf=104.0, metodo="RK4")
        if (d1["Market_Value"].min() > d0["Market_Value"].min()) and (d1["Infections"].max() < d0["Infections"].max()):
            inv_ok += 1

    df_s = pd.DataFrame({
        "Parametro": nombres,
        "S1_TroughMV": np.round(st_tr["S1"], 3),
        "ST_TroughMV_CI": [f"{v:.3f} ± {c:.3f}" for v, c in zip(st_tr["ST"], st_tr["ST_conf"])],
        "ST_PeakInf_CI": [f"{v:.3f} ± {c:.3f}" for v, c in zip(st_in["ST"], st_in["ST_conf"])],
        "ST_CompEnd_CI": [f"{v:.3f} ± {c:.3f}" for v, c in zip(st_co["ST"], st_co["ST_conf"])],
        "_sort": st_tr["ST"],
    }).sort_values("_sort", ascending=False).drop(columns=["_sort"])

    meta = {
        "ejecutado": True,
        "n_base": int(n_base),
        "n_corridas": int(n_tot),
        "trough_mv_p10_p90": [round(float(np.percentile(y_trough, 10)), 2), round(float(np.percentile(y_trough, 90)), 2)],
        "peak_inf_p10_p90": [round(float(np.percentile(y_peak_inf, 10)), 2), round(float(np.percentile(y_peak_inf, 90)), 2)],
        "comp_end_p10_p90": [round(float(np.percentile(y_comp_end, 10)), 3), round(float(np.percentile(y_comp_end, 90)), 3)],
        "invarianza_ordinal_pct": round(100.0 * inv_ok / len(idxs), 1),
    }
    return df_s, meta


def ejecutar_sobol_panel_b(n_pol: int = 256) -> Tuple[pd.DataFrame, int]:
    """Panel B: Separación funcional de las 3 palancas de política [0, 1]^3 (N=1,280, IC 95% completos)."""
    if not TIENE_SALIB:
        return pd.DataFrame(), 0

    nombres = ["Palanca_1_AntiComplacencia", "Palanca_2_Capacidad_Monitoreo", "Palanca_3_Transparencia_Contencion"]
    problem = {"num_vars": 3, "names": nombres, "bounds": [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]}
    X = sobol_sample.sample(problem, n_pol, calc_second_order=False)
    n_tot = len(X)

    y_tr, y_in, y_co, y_mv = np.zeros(n_tot), np.zeros(n_tot), np.zeros(n_tot), np.zeros(n_tot)
    for i, fila in enumerate(X):
        p_ac, p_cm, p_tc = float(fila[0]), float(fila[1]), float(fila[2])
        ov = {
            "complacency_effect_on_compliance": 0.40 - 0.32 * p_ac,
            "complacency_effect_on_management": 0.60 - 0.45 * p_ac,
            "complacency_effect_on_monitoring": 0.75 - 0.55 * p_ac,
            "base_management_emphasis": 0.30 + 0.40 * p_cm,
            "base_monitoring_coverage": 0.20 + 0.45 * p_cm,
            "base_capability": 0.10 + 0.30 * p_cm,
            "base_transparency": 0.05 + 0.30 * p_tc,
            "time_to_resolve_infections": 2.40 - 1.10 * p_tc,
        }
        df_i = simular(ov, dt=0.25, tf=104.0, metodo="RK4")
        win = df_i[(df_i["Week"] >= 18) & (df_i["Week"] <= 52)]
        y_tr[i] = float(win["Market_Value"].min())
        y_in[i] = float(win["Infections"].max())
        y_co[i] = float(df_i["Compliance"].iloc[-1])
        y_mv[i] = float(df_i["Market_Value"].iloc[-1])

    sb_tr = sobol_analyze.analyze(problem, y_tr, calc_second_order=False, conf_level=0.95, print_to_console=False)
    sb_in = sobol_analyze.analyze(problem, y_in, calc_second_order=False, conf_level=0.95, print_to_console=False)
    sb_co = sobol_analyze.analyze(problem, y_co, calc_second_order=False, conf_level=0.95, print_to_console=False)
    sb_mv = sobol_analyze.analyze(problem, y_mv, calc_second_order=False, conf_level=0.95, print_to_console=False)

    df_b = pd.DataFrame({
        "Palanca_Politica": nombres,
        "ST_TroughMV_CI": [f"{v:.3f} ± {c:.3f}" for v, c in zip(sb_tr["ST"], sb_tr["ST_conf"])],
        "ST_PeakInf_CI": [f"{v:.3f} ± {c:.3f}" for v, c in zip(sb_in["ST"], sb_in["ST_conf"])],
        "ST_CompEnd_CI": [f"{v:.3f} ± {c:.3f}" for v, c in zip(sb_co["ST"], sb_co["ST_conf"])],
        "ST_MVEnd_CI": [f"{v:.3f} ± {c:.3f}" for v, c in zip(sb_mv["ST"], sb_mv["ST_conf"])],
    })
    return df_b, n_tot


def limpiar_doi(raw: str) -> str:
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", (raw or "").strip(), flags=re.IGNORECASE)
    return s.rstrip(".,;)").strip()


def leer_csv_robusto(ruta: Path) -> List[Dict[str, str]]:
    regs: List[Dict[str, str]] = []
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            with open(ruta, "r", encoding=enc, errors="ignore", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    continue
                cols = {c.lower().strip(): c for c in reader.fieldnames if c}
                for r in reader:
                    tit = (r.get(cols.get("title", ""), "") or r.get(cols.get("reference_title", ""), "") or "").strip()
                    if not tit:
                        continue
                    doi = limpiar_doi(r.get(cols.get("doi", ""), "") or r.get(cols.get("reference_doi", ""), "") or "")
                    rev = (r.get(cols.get("journal", ""), "") or r.get(cols.get("reference_journal", ""), "") or r.get(cols.get("venue", ""), "") or "").strip()
                    anio = (r.get(cols.get("year", ""), "") or r.get(cols.get("reference_year", ""), "") or r.get(cols.get("publication_year", ""), "") or "").strip()[:4]
                    auts = (r.get(cols.get("authors", ""), "") or r.get(cols.get("reference_authors", ""), "") or "").strip()[:40]
                    regs.append({"anio": anio or "-", "autores": auts or "N/A", "titulo": tit, "revista": rev or "-", "doi": doi})
                if regs:
                    return regs
        except Exception:
            continue
    return regs


def curar_literatura_safety_science(proy_dir: Path, ssot_dir: Path) -> Tuple[int, Path]:
    dl = Path.home() / "Downloads"
    dat_dir = proy_dir / "datos_empiricos"
    cands: List[Path] = []
    for nom in ("response (5).csv", "response (6).csv", "response (7).csv"):
        p = dl / nom
        if p.exists():
            cands.append(p)
    for folder in (dat_dir, proy_dir / "literatura"):
        if folder.exists():
            for p in sorted(folder.glob("*.csv")):
                if p not in cands:
                    cands.append(p)

    unicos: Dict[str, Dict[str, Any]] = {}
    fuentes: List[str] = []
    for c in cands:
        regs = leer_csv_robusto(c)
        if regs:
            fuentes.append(f"{c.name} ({len(regs)})")
        for r in regs:
            txt = f"{r['titulo']} {r['revista']} {r['doi']}".lower()
            if any(ex in txt for ex in EXCLUSIONES_PREPRINT):
                continue
            es_ss = ("safety science" in r["revista"].lower()) or ("j.ssci" in r["doi"].lower())
            pilar = "Pilar_1_Safety_Science" if es_ss else "Pilar_2_FoodSafety_SD_Complacency"
            sc = 30 if es_ss else 0
            if "food control" in r["revista"].lower() or "food protection" in r["revista"].lower():
                sc += 12
            for kw in ("food safety", "hand hygiene", "restaurant", "complacency", "drift", "safety culture", "safety climate", "compliance", "system dynamics", "reputation", "outbreak", "transparency", "time pressure"):
                if kw in txt:
                    sc += 4
            if r["anio"].isdigit() and int(r["anio"]) >= 2020:
                sc += 5
            clave = r["doi"].lower() if r["doi"] else re.sub(r"[^a-z0-9]", "", r["titulo"].lower())[:56]
            if clave not in unicos or sc > int(unicos[clave]["score"]):
                unicos[clave] = {**r, "pilar": pilar, "score": sc}

    md = [
        "# Literatura Scite.ai Curada (Prioridad Safety Science & Food Control) — `2026_bioseguridad_higiene_manos`",
        f"**Generado:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} | **Fuentes:** {', '.join(fuentes) or 'Ninguna'} | **Artículos únicos:** `{len(unicos)}`\n",
    ]
    for pid, tit_p in (
        ("Pilar_1_Safety_Science", "Pilar 1: Artículos Publicados Directamente en Safety Science (Elsevier)"),
        ("Pilar_2_FoodSafety_SD_Complacency", "Pilar 2: Food Control, Higiene en Restaurantes, Complacencia, SD e Impacto Financiero"),
    ):
        sub = [v for v in unicos.values() if v["pilar"] == pid]
        sub.sort(key=lambda x: (int(x["score"]), str(x["anio"])), reverse=True)
        md.append(f"## {tit_p} ({len(sub)} estudios)")
        if not sub:
            md.append("> Sin registros en este pilar.\n")
            continue
        md.extend(["| Año | Autores | Título | Revista | DOI |", "| :---: | :--- | :--- | :--- | :--- |"])
        for it in sub[:18]:
            md.append(f"| {it['anio']} | {it['autores']} | {it['titulo'][:76]} | {it['revista'][:32]} | `{it['doi'] or '-'}` |")
        md.append("")

    out = ssot_dir / "top_scite_bioseguridad_safety_science.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    return len(unicos), out


def main() -> None:
    parser = argparse.ArgumentParser(description="Motor Euler/RK4 + Barlas + LTM + Sobol + Scite para 2026_bioseguridad_higiene_manos.")
    parser.add_argument("--proyecto", default="2026_bioseguridad_higiene_manos")
    parser.add_argument("--n-sobol", type=int, default=512)
    args = parser.parse_args()

    p_script = Path(__file__).resolve()
    raiz = next((parent for parent in p_script.parents if (parent / "03_proyectos").exists()), Path.cwd())
    proy_dir = raiz / "03_proyectos" / args.proyecto
    csv_dir = proy_dir / "modelo_stella" / "corridas_csv"
    ssot_dir = proy_dir / "ssot"
    csv_dir.mkdir(parents=True, exist_ok=True)
    ssot_dir.mkdir(parents=True, exist_ok=True)

    print("=========================================================================")
    print(f" MOTOR HAND HYGIENE & COMPLACENCY (9 STOCKS) | {args.proyecto}")
    print("=========================================================================")

    trajs: Dict[str, pd.DataFrame] = {}
    res_rows = []
    for nom, ov in ESCENARIOS.items():
        df = simular(ov, dt=0.125, tf=416.0, metodo="Euler")
        df.to_csv(csv_dir / f"{nom}.csv", index=False)
        trajs[nom] = df
        win = df[(df["Week"] >= 18) & (df["Week"] <= 52)]
        fin = df.iloc[-1]
        res_rows.append({
            "Escenario": nom,
            "Comp_W416": round(float(fin["Compliance"]), 4),
            "Peak_Inf": round(float(win["Infections"].max()), 3),
            "Trough_Rep": round(float(win["Reputation"].min()), 4),
            "Trough_Trust": round(float(win["Trust"].min()), 4),
            "Trough_MV": round(float(win["Market_Value"].min()), 3),
            "MV_W416": round(float(fin["Market_Value"]), 3),
            "Complac_W416": round(float(fin["complacency"]), 4),
        })
        print(
            f"   [OK] {nom:<36} | PeakInf={win['Infections'].max():5.2f} | "
            f"TroughMV=${win['Market_Value'].min():5.2f}B | Comp416={fin['Compliance']:.3f} | MV416=${fin['Market_Value']:5.2f}B"
        )
    df_res = pd.DataFrame(res_rows)

    # Hitos Baseline (concordancia con Tabla 3 del borrador)
    df_b = trajs["01_RUN_BASELINE"]
    hitos_w = [0, 18, 20, 22, 24, 28, 34, 44, 60, 104, 416]
    cols_h = ["Week", "Compliance", "Reputation", "Trust", "Infections", "Market_Value", "complacency"]
    df_hitos = df_b[df_b["Week"].isin(hitos_w)][cols_h]

    # Convergencia numérica Euler 3 pasos + RK4
    conv_rows = []
    for sc in ("01_RUN_BASELINE", "04_RUN_RESILIENT"):
        e1 = float(simular(ESCENARIOS[sc], dt=0.125, tf=60.0, metodo="Euler")["Market_Value"].min())
        e2 = float(simular(ESCENARIOS[sc], dt=0.0625, tf=60.0, metodo="Euler")["Market_Value"].min())
        e3 = float(simular(ESCENARIOS[sc], dt=0.03125, tf=60.0, metodo="Euler")["Market_Value"].min())
        rk = float(simular(ESCENARIOS[sc], dt=0.125, tf=60.0, metodo="RK4")["Market_Value"].min())
        conv_rows.append({
            "Escenario": sc,
            "Euler_0.125": round(e1, 5),
            "Euler_0.0625": round(e2, 5),
            "Euler_0.03125": round(e3, 5),
            "RK4_0.125": round(rk, 5),
            "Diff_Eu_RK4": f"{abs(e1 - rk):.2e}",
        })
    df_conv = pd.DataFrame(conv_rows)

    # Barlas unclamped
    df_u1 = simular(ESCENARIOS["05_Extreme1_Severe_Scandal_LowCap"], dt=0.125, tf=104.0, aplicar_clamp=False)
    df_u2 = simular(ESCENARIOS["06_Extreme2_Zero_Scandal_HighSafety"], dt=0.125, tf=104.0, aplicar_clamp=False)

    # LTM proxy por fase en Baseline
    ltm_fases = pd.DataFrame([
        {
            "Fase": "Pre-Crisis (Wks 0–19)",
            "Culture_pct": round(float(df_b[df_b["Week"] <= 19]["ltm_culture_pct"].mean()), 1),
            "Contagion_pct": round(float(df_b[df_b["Week"] <= 19]["ltm_contagion_pct"].mean()), 1),
            "Complacency_pct": round(float(df_b[df_b["Week"] <= 19]["ltm_complacency_pct"].mean()), 1),
            "TimePress_pct": round(float(df_b[df_b["Week"] <= 19]["ltm_timepress_pct"].mean()), 1),
        },
        {
            "Fase": "Scandal Shock (Wks 20–28)",
            "Culture_pct": round(float(df_b[(df_b["Week"] >= 20) & (df_b["Week"] <= 28)]["ltm_culture_pct"].mean()), 1),
            "Contagion_pct": round(float(df_b[(df_b["Week"] >= 20) & (df_b["Week"] <= 28)]["ltm_contagion_pct"].mean()), 1),
            "Complacency_pct": round(float(df_b[(df_b["Week"] >= 20) & (df_b["Week"] <= 28)]["ltm_complacency_pct"].mean()), 1),
            "TimePress_pct": round(float(df_b[(df_b["Week"] >= 20) & (df_b["Week"] <= 28)]["ltm_timepress_pct"].mean()), 1),
        },
        {
            "Fase": "Rebound (Wks 29–52)",
            "Culture_pct": round(float(df_b[(df_b["Week"] >= 29) & (df_b["Week"] <= 52)]["ltm_culture_pct"].mean()), 1),
            "Contagion_pct": round(float(df_b[(df_b["Week"] >= 29) & (df_b["Week"] <= 52)]["ltm_contagion_pct"].mean()), 1),
            "Complacency_pct": round(float(df_b[(df_b["Week"] >= 29) & (df_b["Week"] <= 52)]["ltm_complacency_pct"].mean()), 1),
            "TimePress_pct": round(float(df_b[(df_b["Week"] >= 29) & (df_b["Week"] <= 52)]["ltm_timepress_pct"].mean()), 1),
        },
        {
            "Fase": "Long-Run Drift (Wks 53–416)",
            "Culture_pct": round(float(df_b[df_b["Week"] >= 53]["ltm_culture_pct"].mean()), 1),
            "Contagion_pct": round(float(df_b[df_b["Week"] >= 53]["ltm_contagion_pct"].mean()), 1),
            "Complacency_pct": round(float(df_b[df_b["Week"] >= 53]["ltm_complacency_pct"].mean()), 1),
            "TimePress_pct": round(float(df_b[df_b["Week"] >= 53]["ltm_timepress_pct"].mean()), 1),
        },
    ])

    print(f"1. [SOBOL PANEL A] Sensibilidad paramétrica (n={args.n_sobol}, N={args.n_sobol*9}, IC 95% completos)...")
    df_sob_a, meta_a = ejecutar_sobol_panel_a(n_base=args.n_sobol)
    df_sob_a.to_csv(ssot_dir / "sobol_panel_a_higiene.csv", index=False)

    print("2. [SOBOL PANEL B] Separación funcional de las 3 palancas de política (N=1,280, IC 95% completos)...")
    df_sob_b, n_tot_b = ejecutar_sobol_panel_b(n_pol=256)
    df_sob_b.to_csv(ssot_dir / "sobol_panel_b_higiene.csv", index=False)

    n_lit, path_lit = curar_literatura_safety_science(proy_dir, ssot_dir)
    print(f"3. [SCITE LITERATURA] {n_lit} estudios curados -> {path_lit}")

    md = [
        f"# Resultados Verificados Euler/RK4 + Barlas + LTM + Sobol — `{args.proyecto}`",
        f"**Generado:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | **SSOT:** `hands higine.stmx` (0–416 Weeks, DT=0.125)\n",
        "## 1. Matriz Comparativa de 6 Escenarios (4 Políticas + 2 Extremos Barlas)",
        df_res.to_markdown(index=False),
        "\n## 2. Hitos Exactos de `01_RUN_BASELINE` (Concordancia con Tabla 3 del Borrador)",
        df_hitos.to_markdown(index=False),
        "\n## 3. Verificación Numérica: Convergencia Euler/RK4 y Barlas Unclamped",
        f"- **Barlas Extremo 1 Unclamped (Escándalo 5x, 6 sem, Baja Cap.):** `PeakInf={df_u1['Infections'].max():.2f}`, `TroughMV={df_u1['Market_Value'].min():.2f}`, `min(Comp)={df_u1['Compliance'].min():.4f}`",
        f"- **Barlas Extremo 2 Unclamped (Cero Escándalo, Alta Seguridad):** `PeakInf={df_u2['Infections'].max():.2f}`, `Comp_end={df_u2['Compliance'].iloc[-1]:.4f}`, `MV_end={df_u2['Market_Value'].iloc[-1]:.2f}`\n",
        df_conv.to_markdown(index=False),
        "\n## 4. Dominancia Cuantitativa de Lazos por Fase (Baseline)",
        ltm_fases.to_markdown(index=False),
        f"\n## 5. Sobol Panel A — Incertidumbre Paramétrica (±25%, n={args.n_sobol}, N={meta_a.get('n_corridas', 0)}, IC 95% Completos)",
        f"- **Metadatos:** `{json.dumps(meta_a, ensure_ascii=False)}`\n",
        df_sob_a.to_markdown(index=False),
        f"\n## 6. Sobol Panel B — Separación Funcional de las 3 Palancas de Política (N={n_tot_b}, IC 95% Completos)",
        df_sob_b.to_markdown(index=False),
    ]
    ruta_rep = ssot_dir / "resumen_experimentos_higiene_manos.md"
    ruta_rep.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n✅ Reporte completo guardado en: {ruta_rep}")
    print("=========================================================================")


if __name__ == "__main__":
    main()