"""Dutch Imbalance Market Forecasting & Battery Dispatch Lab — Demo.

Streamlit app with four screens per CLAUDE.md §7:
1. Live Forecast  2. Track Record  3. Backtest Explorer  4. What-If Simulator
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import streamlit as st

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
BACKTEST_PATH = ROOT / "work" / "backtest" / "backtest_results.json"
EVAL_PATH = ROOT / "work" / "evaluation" / "walkforward_results.json"
HOLDOUT_PATH = ROOT / "work" / "holdout" / "holdout_results.json"
FORECAST_LOG = ROOT / "forecast_log" / "forecasts.jsonl"

st.set_page_config(
    page_title="NL Imbalance Lab",
    page_icon="⚡",
    layout="wide",
)

# ── Header ───────────────────────────────────────────────────────────────

st.title("Dutch Imbalance Market Forecasting & Battery Dispatch Lab")

st.markdown(
    "Probabilistic forecasting of the Dutch imbalance settlement price at "
    "15-minute resolution, with battery dispatch optimised against the full "
    "predictive distribution. Walk-forward backtested under real settlement "
    "rules and publication latency."
)

with st.expander("Glossary — key terms used on this page"):
    st.markdown(
        "- **ISP** — Imbalance Settlement Period (15 minutes). The Dutch grid "
        "settles energy imbalances every 15 min.\n"
        "- **Pinball loss** — The standard scoring rule for quantile forecasts. "
        "Lower is better. Measures how well predicted quantiles match reality.\n"
        "- **PF ratio** — Ratio to Perfect Foresight. What fraction of the "
        "theoretical maximum revenue (if prices were known in advance) the "
        "strategy captures.\n"
        "- **CVaR** — Conditional Value at Risk. A risk measure: the expected "
        "loss in the worst 5% of outcomes. Used here to trade off expected "
        "revenue against downside risk.\n"
        "- **DM test** — Diebold-Mariano test. A statistical test for whether "
        "two forecasts are significantly different in accuracy.\n"
        "- **PIT** — Probability Integral Transform. If a probabilistic forecast "
        "is well-calibrated, the PIT values should be uniformly distributed.\n"
        "- **CRPS** — Continuous Ranked Probability Score. A single-number "
        "summary of probabilistic forecast quality (lower is better).\n"
        "- **Calibration** — Whether predicted probabilities match observed "
        "frequencies: a 90% prediction interval should contain the outcome 90% "
        "of the time.\n"
        "- **Walk-forward** — Expanding-origin evaluation: train on all data up to "
        "month N, test on month N+1, then expand. No future data leaks into "
        "training.\n"
        "- **LEAR** — Lasso Estimated AutoRegressive model. A regularised linear "
        "quantile regression, the standard benchmark in electricity price "
        "forecasting literature."
    )

DISCLAIMER = (
    "**Research tool.** Results are backtested under stated assumptions. "
    "This is not investment advice and not a guarantee of returns."
)
st.caption(DISCLAIMER)


@st.cache_data
def load_backtest() -> dict:
    with open(BACKTEST_PATH) as f:
        return json.load(f)


@st.cache_data
def load_holdout() -> dict | None:
    if HOLDOUT_PATH.exists():
        with open(HOLDOUT_PATH) as f:
            return json.load(f)
    return None


@st.cache_data(ttl=300)  # the cron appends to this file; don't pin the first read forever
def load_forecast_log() -> list[dict]:
    """Ex-ante forecasts only, latest issue per target ISP, ordered by target.

    Entries issued after their target ISP began are hindcasts (the pre-fix job
    logged these) and never count as live forecasts."""
    if not FORECAST_LOG.exists():
        return []
    latest: dict[str, dict] = {}
    for line in FORECAST_LOG.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed forecast log line: %s", line[:80])
            continue
        if datetime.fromisoformat(entry["forecast_issued_at"]) > datetime.fromisoformat(
            entry["target_isp"]
        ):
            continue
        latest[entry["target_isp"]] = entry  # file is append-ordered, so later wins
    return sorted(latest.values(), key=lambda e: datetime.fromisoformat(e["target_isp"]))


@st.cache_data
def load_evaluation() -> dict:
    with open(EVAL_PATH) as f:
        return json.load(f)


bt = load_backtest()
ev = load_evaluation()
ho = load_holdout()
fc_log = load_forecast_log()

tab_forecast, tab_track, tab_backtest, tab_whatif = st.tabs(
    ["Live Forecast", "Track Record", "Backtest Explorer", "What-If Simulator"]
)

# ── Tab 1: Live Forecast ──────────────────────────────────────────────

with tab_forecast:
    st.header("Live Forecast")
    st.markdown(
        "Real-time quantile forecasts for the next ISPs, produced by a GBM model "
        "trained on the latest data and logged with a timestamp at the time of "
        "issue. **This record cannot be back-fitted** — it is the strongest "
        "evidence that the model works out-of-sample."
    )
    if fc_log:
        last_issue = max(e["forecast_issued_at"] for e in fc_log)
        latest = next(e for e in fc_log if e["forecast_issued_at"] == last_issue)
        st.markdown(
            f"**Last forecast issued:** {last_issue} · **next ISP:** {latest['target_isp']}"
        )

        col_m, col_r, col_d = st.columns(3)
        col_m.metric("Median forecast (next ISP)", f"EUR {latest['median']:.1f}/MWh")

        reg = latest.get("regulation_state", {})
        if reg:
            p_single = reg.get("single_price", 0)
            p_dual = reg.get("dual_price", 0)
            col_r.metric("Regulation State", f"{'Dual' if p_dual > p_single else 'Single'}-priced")
            col_r.caption(f"Single: {p_single:.0%} · Dual: {p_dual:.0%}")

        disp = latest.get("dispatch_recommendation", {})
        if disp:
            action = disp.get("action", "hold").upper()
            colors = {"CHARGE": "🟢", "DISCHARGE": "🔴", "HOLD": "⚪"}
            col_d.metric("Dispatch", f"{colors.get(action, '')} {action}")
            col_d.caption(disp.get("reason", ""))

        recent = fc_log[-min(len(fc_log), 96) :]
        fig_fc = go.Figure()
        times = [r["target_isp"] for r in recent]
        medians = [r["median"] for r in recent]
        q10 = [r["quantiles"].get("0.10", r["median"]) for r in recent]
        q90 = [r["quantiles"].get("0.90", r["median"]) for r in recent]
        fig_fc.add_trace(
            go.Scatter(
                x=times,
                y=q90,
                mode="lines",
                line=dict(width=0),
                showlegend=False,
            )
        )
        fig_fc.add_trace(
            go.Scatter(
                x=times,
                y=q10,
                mode="lines",
                line=dict(width=0),
                fill="tonexty",
                fillcolor="rgba(31,119,180,0.2)",
                name="10-90% interval",
            )
        )
        fig_fc.add_trace(
            go.Scatter(
                x=times,
                y=medians,
                mode="lines",
                line=dict(color="#1f77b4", width=2),
                name="Median",
            )
        )
        fig_fc.update_layout(
            yaxis_title="EUR/MWh",
            height=350,
            margin=dict(t=20),
        )
        st.plotly_chart(fig_fc, use_container_width=True)
        st.caption(f"Showing last {len(recent)} logged forecasts. Total in log: {len(fc_log)}.")
    else:
        st.info(
            "The scheduled forecast job has not yet produced forecasts. "
            "Once the GitHub Actions cron is active, this screen shows the current and "
            "next ISP forecasts with a fan chart of the predictive distribution. "
            "The live track record accumulates here — it cannot be back-fitted."
        )

# ── Tab 2: Track Record ──────────────────────────────────────────────

with tab_track:
    st.header("Forecast Evaluation — Walk-Forward")
    st.markdown(
        "How well do the models predict imbalance prices? Evaluated on "
        f"**{ev['folds']} expanding-origin monthly folds** ({ev['fold_range']}), "
        "where each fold trains on all prior data and tests on the next month. "
        "No future information leaks into training."
    )
    st.markdown(
        f"Reference baseline: **{ev['reference_model']}** (hour × day-of-week "
        f"conditional quantiles). Multiple-comparison correction: "
        f"**{ev['correction']}** across {ev['model_configs_tried']} model "
        f"configurations."
    )

    # -- Model comparison table --
    st.subheader("Pinball Loss (lower is better)")
    st.caption(
        "Pinball loss measures how well each model's predicted quantiles match "
        "reality. The strongest naive baseline (Climatology) scores 25.47 — "
        "both LEAR and GBM beat it significantly."
    )
    models = ["persistence", "seasonal_naive_1w", "climatology", "day_ahead", "lear", "gbm"]
    labels = ["Persistence", "Seasonal Naive (1w)", "Climatology", "Day-Ahead", "LEAR", "GBM"]
    losses = [ev["fold_losses"][m]["mean"] for m in models]
    st.dataframe(
        {"Model": labels, "Mean Pinball Loss (EUR/MWh)": [f"{v:.2f}" for v in losses]},
        hide_index=True,
        use_container_width=True,
    )

    col1, col2 = st.columns(2)

    # -- Calibration coverage --
    with col1:
        st.subheader("Calibration — Coverage vs Nominal")
        st.caption(
            "A well-calibrated model's line hugs the diagonal: its 50th "
            "percentile should be exceeded 50% of the time, its 90th 90%, etc."
        )
        fig_cal = go.Figure()
        fig_cal.add_trace(
            go.Scatter(
                x=[0, 1],
                y=[0, 1],
                mode="lines",
                line=dict(dash="dash", color="grey"),
                name="Perfect",
            )
        )
        for m, lab, color in [
            ("gbm", "GBM", "#1f77b4"),
            ("lear", "LEAR", "#ff7f0e"),
            ("climatology", "Climatology", "#2ca02c"),
        ]:
            cov = ev["calibration"][m]["coverage"]
            taus = sorted(cov.keys(), key=float)
            fig_cal.add_trace(
                go.Scatter(
                    x=[float(t) for t in taus],
                    y=[cov[t] for t in taus],
                    mode="lines+markers",
                    name=lab,
                    line=dict(color=color),
                )
            )
        fig_cal.update_layout(
            xaxis_title="Nominal quantile",
            yaxis_title="Empirical coverage",
            height=400,
            margin=dict(t=20),
        )
        st.plotly_chart(fig_cal, use_container_width=True)

    # -- PIT histogram --
    with col2:
        st.subheader("PIT Histogram — GBM")
        st.caption(
            "If the forecast is well-calibrated, this histogram should be flat "
            "(uniform). Peaks or valleys indicate systematic bias in specific "
            "parts of the distribution."
        )
        pit = ev["calibration"]["gbm"]["pit_histogram"]
        n_bins = len(pit)
        bin_edges = [i / n_bins for i in range(n_bins)]
        total = sum(pit)
        uniform = total / n_bins
        fig_pit = go.Figure()
        fig_pit.add_trace(
            go.Bar(
                x=[f"{b:.1f}" for b in bin_edges],
                y=pit,
                marker_color="#1f77b4",
                name="GBM PIT",
            )
        )
        fig_pit.add_hline(y=uniform, line_dash="dash", line_color="red", annotation_text="Uniform")
        fig_pit.update_layout(
            xaxis_title="PIT bin",
            yaxis_title="Count",
            height=400,
            margin=dict(t=20),
        )
        st.plotly_chart(fig_pit, use_container_width=True)

    # -- DM tests --
    st.subheader("Diebold-Mariano Significance Tests")
    st.caption(
        "Do the models beat the climatological baseline by a statistically "
        "significant margin, or could the difference be noise? Negative DM "
        "statistic = better than baseline."
    )
    dm = ev["dm_tests_vs_reference"]
    dm_rows = []
    for d in dm:
        dm_rows.append(
            {
                "Model": d["model"],
                "DM Statistic": f"{d['dm_stat']:.2f}",
                "p-value": f"{d['p_value']:.2e}",
                "Significant (0.05)": "Yes" if d["p_value"] < 0.05 else "No",
            }
        )
    st.dataframe(dm_rows, hide_index=True, use_container_width=True)

    dm_lg = ev["dm_lear_vs_gbm"]
    st.markdown(
        f"**LEAR vs GBM head-to-head:** DM = {dm_lg['dm_stat']:.3f}, "
        f"p = {dm_lg['p_value']:.3f} — **not significantly different.** "
        f"The linear model is surprisingly competitive against gradient boosting."
    )

    # -- Segmented by hour --
    st.subheader("Pinball Loss by Hour of Day")
    st.caption(
        "Where does the model struggle? Morning hours (9–12 UTC) are hardest — "
        "renewable generation ramps create unpredictable imbalances."
    )
    fig_hour = go.Figure()
    for m, lab, color in [
        ("gbm", "GBM", "#1f77b4"),
        ("lear", "LEAR", "#ff7f0e"),
        ("climatology", "Climatology", "#2ca02c"),
    ]:
        by_hour = ev["segmented"]["by_hour"][m]
        hours = sorted(by_hour.keys(), key=int)
        fig_hour.add_trace(
            go.Scatter(
                x=[int(h) for h in hours],
                y=[by_hour[h] for h in hours],
                mode="lines+markers",
                name=lab,
                line=dict(color=color),
            )
        )
    fig_hour.update_layout(
        xaxis_title="Hour (UTC)",
        yaxis_title="Pinball Loss (EUR/MWh)",
        height=350,
        margin=dict(t=20),
    )
    st.plotly_chart(fig_hour, use_container_width=True)

    # -- Calibration summary --
    st.subheader("Calibration Summary")
    cal_rows = []
    for m, lab in [("gbm", "GBM"), ("lear", "LEAR"), ("climatology", "Climatology")]:
        c = ev["calibration"][m]
        cal_rows.append(
            {
                "Model": lab,
                "CRPS": f"{c['crps']:.1f}",
                "MAE": f"{c['mae']:.1f}",
                "RMSE": f"{c['rmse']:.1f}",
                "Mean |Cal Error|": f"{c['mean_abs_cal_error']:.4f}",
                "PIT CV": f"{c['pit_cv']:.3f}",
            }
        )
    st.dataframe(cal_rows, hide_index=True, use_container_width=True)

# ── Tab 3: Backtest Explorer ──────────────────────────────────────────

with tab_backtest:
    st.header("Backtest Explorer")
    st.markdown(
        "How much money does a battery earn using these forecasts? Revenue from "
        "a rolling-horizon dispatch optimisation, settled under actual Dutch "
        "imbalance rules including dual pricing."
    )
    batt = bt["battery"]
    st.markdown(
        f"**Battery:** {batt['power_mw']:.0f} MW / {batt['energy_mwh']:.0f} MWh, "
        f"η = {batt['efficiency_charge'] * 100:.0f}% round-trip, "
        f"degradation = EUR {batt['degradation_eur_per_mwh']}/MWh throughput. "
        f"**{bt['n_folds']} walk-forward folds**, {bt['n_test_isps']:,} ISPs."
    )

    # -- Revenue summary --
    st.subheader("Revenue Summary")
    st.caption(
        "Perfect foresight is the theoretical maximum — it knows future prices. "
        "The PF ratio shows what fraction of that ceiling the forecast-based "
        "strategy captures. 95% CIs from block bootstrap (n=5,000)."
    )
    pol_order = ["perfect_foresight", "deterministic", "cvar_0.5", "do_nothing"]
    pol_labels = ["Perfect Foresight", "Deterministic", "CVaR (ra=0.5)", "Do Nothing"]
    rev_rows = []
    for p, lab in zip(pol_order, pol_labels, strict=True):
        d = bt["policies"][p]
        ci = bt["bootstrap_ci_95"].get(p)
        ci_str = f"[{ci['lo'] / 1e3:.0f}K, {ci['hi'] / 1e3:.0f}K]" if ci else "—"
        rev_rows.append(
            {
                "Policy": lab,
                "Net Revenue (EUR)": f"{d['net_revenue_eur']:,.0f}",
                "Ratio to PF": f"{d['ratio_to_pf'] * 100:.1f}%",
                "95% CI": ci_str,
            }
        )
    st.dataframe(rev_rows, hide_index=True, use_container_width=True)

    col1, col2 = st.columns(2)

    # -- Efficient frontier --
    with col1:
        st.subheader("Efficient Frontier")
        st.caption(
            "How does revenue change with risk aversion? Each point is a "
            "different CVaR risk-aversion setting (0 = risk-neutral, 1 = "
            "maximally risk-averse). The frontier is relatively flat — "
            "expected revenue and tail risk move together in this market."
        )
        frontier = bt["efficient_frontier"]
        ra_vals = [f["risk_aversion"] for f in frontier]
        rev_vals = [f["net_revenue"] / 1e3 for f in frontier]
        cvar_vals = [f["cvar_5pct"] for f in frontier]

        fig_ef = go.Figure()
        fig_ef.add_trace(
            go.Scatter(
                x=cvar_vals,
                y=rev_vals,
                mode="lines+markers+text",
                text=[f"ra={r:.1f}" for r in ra_vals],
                textposition="top center",
                textfont=dict(size=9),
                marker=dict(
                    size=8,
                    color=ra_vals,
                    colorscale="RdYlGn_r",
                    showscale=True,
                    colorbar=dict(title="Risk<br>Aversion"),
                ),
                line=dict(color="#888"),
            )
        )
        fig_ef.update_layout(
            xaxis_title="CVaR 5% (EUR/ISP)",
            yaxis_title="Net Revenue (EUR K, last 3 months)",
            height=450,
            margin=dict(t=20),
        )
        st.plotly_chart(fig_ef, use_container_width=True)

    # -- Saturation curve --
    with col2:
        st.subheader("Revenue-per-MW Saturation")
        st.caption(
            "A public-data strategy degrades as more capacity is deployed — "
            "your own trading pushes the market back toward balance. This curve "
            "shows where the signal saturates. The model is assumed (√(MW/500)), "
            "not calibrated — the shape is correct, the level is uncertain."
        )
        sat = bt["saturation_curve"]
        cap_vals = [s["capacity_mw"] for s in sat if s["revenue_per_mw"] > 0]
        rpm_vals = [s["revenue_per_mw"] / 1e3 for s in sat if s["revenue_per_mw"] > 0]

        fig_sat = go.Figure()
        fig_sat.add_trace(
            go.Scatter(
                x=cap_vals,
                y=rpm_vals,
                mode="lines+markers",
                marker=dict(size=8, color="#1f77b4"),
                line=dict(color="#1f77b4"),
            )
        )
        fig_sat.update_layout(
            xaxis_title="Deployed Capacity (MW)",
            yaxis_title="Revenue per MW (EUR K)",
            xaxis_type="log",
            height=450,
            margin=dict(t=20),
        )
        st.plotly_chart(fig_sat, use_container_width=True)

    # -- Per-year breakdown --
    st.subheader("Revenue by Year")
    st.caption(
        "Revenue varies significantly by year — 2025 was volatile and "
        "profitable. A strategy that earns most of its revenue in one year "
        "is a finding, not a proof of robustness."
    )
    years = sorted(bt["yearly_revenue"]["perfect_foresight"].keys())
    year_rows = []
    for y in years:
        row = {"Year": y}
        for p, lab in zip(pol_order[:3], pol_labels[:3], strict=True):
            v = bt["yearly_revenue"][p].get(y, 0)
            row[lab] = f"EUR {v:,.0f}"
        year_rows.append(row)
    st.dataframe(year_rows, hide_index=True, use_container_width=True)

    st.markdown(
        f"**Impact model:** `{bt['market_impact_model']}`. "
        f"**Dispatch:** window = {bt['dispatch_config']['window_isps']} ISPs, "
        f"step = {bt['dispatch_config']['step_isps']} ISPs, "
        f"scenario method = {bt['dispatch_config']['scenario_method']}."
    )

    # -- Final holdout results --
    if ho:
        st.subheader("Final Holdout (One-Time Evaluation)")
        st.markdown(
            "The holdout period was never touched during development. It was "
            "evaluated **exactly once**, at the very end. If results degrade "
            "here, that is reported — not hidden."
        )
        st.markdown(
            f"**Period:** {ho['holdout_start'][:10]} to {ho['holdout_end'][:10]} "
            f"({ho['n_holdout']:,} ISPs, {ho['n_holdout'] * 0.25 / 24:.0f} days). "
            f"Trained on {ho['n_train']:,} ISPs."
        )
        ho_pol = ["perfect_foresight", "deterministic", "cvar_0.5", "do_nothing"]
        ho_lab = ["Perfect Foresight", "Deterministic", "CVaR (ra=0.5)", "Do Nothing"]
        ho_rows = []
        for p, lab in zip(ho_pol, ho_lab, strict=True):
            d = ho["dispatch"][p]
            ci = ho["bootstrap_ci_95"].get(p)
            ci_str = f"[{ci['lo'] / 1e3:.0f}K, {ci['hi'] / 1e3:.0f}K]" if ci else "—"
            ho_rows.append(
                {
                    "Policy": lab,
                    "Net Revenue": f"EUR {d['net_revenue_eur']:,.0f}",
                    "Ratio to PF": f"{d['ratio_to_pf'] * 100:.1f}%",
                    "95% CI": ci_str,
                }
            )
        st.dataframe(ho_rows, hide_index=True, use_container_width=True)

        n_wf_years = bt["n_test_isps"] * 0.25 / 8760
        n_ho_years = ho["n_holdout"] * 0.25 / 8760
        st.markdown("**Walk-forward vs holdout (annualised):**")
        comp_rows = []
        for p, lab in [("deterministic", "Deterministic"), ("cvar_0.5", "CVaR (ra=0.5)")]:
            wf_ann = bt["policies"][p]["net_revenue_eur"] / n_wf_years
            ho_ann = ho["dispatch"][p]["net_revenue_eur"] / n_ho_years
            delta = ((ho_ann / wf_ann) - 1) * 100 if wf_ann != 0 else 0
            comp_rows.append(
                {
                    "Policy": lab,
                    "WF Annualised": f"EUR {wf_ann:,.0f}",
                    "Holdout Annualised": f"EUR {ho_ann:,.0f}",
                    "Delta": f"{delta:+.0f}%",
                }
            )
        st.dataframe(comp_rows, hide_index=True, use_container_width=True)
        st.caption(
            "Holdout outperforms walk-forward on annualised basis. This likely "
            "reflects a more volatile/predictable market period (May–Jul 2026) "
            "rather than model improvement — reported honestly."
        )

# ── Tab 4: What-If Simulator ──────────────────────────────────────────

with tab_whatif:
    st.header("What-If Simulator")
    st.markdown(
        "What would a different battery earn? Adjust the parameters below and "
        "see the estimated annual revenue, scaled from backtest results using "
        "the market-impact saturation model. **This is the screen a battery "
        "operator would use.**"
    )

    col_in, col_out = st.columns([1, 2])

    with col_in:
        power_mw = st.slider("Power (MW)", 1.0, 200.0, 10.0, 1.0)
        duration_h = st.slider("Duration (hours)", 1.0, 8.0, 4.0, 0.5)
        efficiency = st.slider("Round-trip efficiency (%)", 70, 98, 90, 1) / 100.0
        degradation = st.slider("Degradation cost (EUR/MWh)", 0.0, 20.0, 5.0, 0.5)
        risk_aversion = st.slider("Risk aversion", 0.0, 1.0, 0.5, 0.1)

    energy_mwh = power_mw * duration_h
    base_eff = bt["battery"]["efficiency_charge"] * bt["battery"]["efficiency_discharge"]
    user_eff = efficiency

    sat = bt["saturation_curve"]
    sat_mw = np.array([s["capacity_mw"] for s in sat])
    sat_rpm = np.array([s["revenue_per_mw"] for s in sat])
    rpm_at_user = float(np.interp(power_mw, sat_mw, sat_rpm))

    eff_scale = user_eff / base_eff
    dur_scale = duration_h / (bt["battery"]["energy_mwh"] / bt["battery"]["power_mw"])
    dur_scale = min(dur_scale, 1.5)
    base_deg = bt["battery"]["degradation_eur_per_mwh"]
    deg_adj = (base_deg - degradation) * 0.15 * power_mw

    base_rev_annual = rpm_at_user * power_mw
    n_test_isps = bt["n_test_isps"]
    n_test_years = n_test_isps * 0.25 / 8760
    annualised = base_rev_annual / n_test_years

    adjusted = annualised * eff_scale * dur_scale + deg_adj

    ci_det = bt["bootstrap_ci_95"]["deterministic"]
    ci_ratio_lo = ci_det["lo"] / ci_det["mean"]
    ci_ratio_hi = ci_det["hi"] / ci_det["mean"]

    adj_lo = adjusted * ci_ratio_lo
    adj_hi = adjusted * ci_ratio_hi

    pf_annual = bt["policies"]["perfect_foresight"]["net_revenue_eur"] / n_test_years
    ratio_pf = adjusted / pf_annual if pf_annual > 0 else 0

    frontier = bt["efficient_frontier"]
    closest_ra = min(frontier, key=lambda f: abs(f["risk_aversion"] - risk_aversion))
    base_ra_05 = next(f for f in frontier if abs(f["risk_aversion"] - 0.5) < 0.01)
    if base_ra_05["net_revenue"] > 0:
        ra_scale = closest_ra["net_revenue"] / base_ra_05["net_revenue"]
    else:
        ra_scale = 1.0
    adjusted_ra = adjusted * ra_scale

    with col_out:
        m1, m2, m3 = st.columns(3)
        m1.metric("Battery", f"{power_mw:.0f} MW / {energy_mwh:.0f} MWh")
        m2.metric("Est. Annual Revenue", f"EUR {adjusted_ra:,.0f}")
        m3.metric("Ratio to PF", f"{ratio_pf * 100:.1f}%")

        fig_wi = go.Figure()
        fig_wi.add_trace(
            go.Bar(
                x=["Low (95% CI)", "Expected", "High (95% CI)"],
                y=[adj_lo * ra_scale, adjusted_ra, adj_hi * ra_scale],
                marker_color=["#ff7f7f", "#1f77b4", "#7fbf7f"],
            )
        )
        fig_wi.update_layout(
            yaxis_title="Est. Annual Revenue (EUR)",
            height=350,
            margin=dict(t=20),
        )
        st.plotly_chart(fig_wi, use_container_width=True)

        st.caption(
            f"Scaling from deterministic backtest ({n_test_years:.1f} years). "
            f"Impact model: {bt['market_impact_model']}. "
            f"Risk-aversion scaling from efficient frontier (ra={risk_aversion:.1f} → "
            f"{ra_scale:.2f}x baseline). "
            f"Duration capped at 1.5x base. "
            f"This is an approximation — actual dispatch would require re-optimisation."
        )
