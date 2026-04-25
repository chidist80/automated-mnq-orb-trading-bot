"""
Independent re-implementation of the Tier 1 OR-retest rule.

Imports nothing from `backtest/causal_or_retest.py`. Built from the prose spec
in edge_decision_plan.md and the rule_id parameters. Goal: confirm the frozen
result (95 trades, $2,186.70, 61.05% win, PF 1.6845) is reproducible
independently, then probe edge soundness with bootstrap, permutation,
walk-forward, slippage stress, regime profiling, and roll-proximity check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

PROJECT_ROOT = Path(__file__).parent.parent
ET = "US/Eastern"
MULT = 2.0
COMM = 1.34
SLIP_RT = 5.0       # round-trip points
HALF_SLIP = SLIP_RT / 2.0  # 2.5 pts each side

STOP_PTS = 40.0
RR = 1.25
TARGET_PTS = STOP_PTS * RR  # 50

OR_START = "09:30"
OR_END = "09:44"           # 15 bars 09:30..09:44 inclusive
RETEST_START = "10:00"
RETEST_END = "11:00"        # inclusive
TIME_EXIT = "15:55"
TOUCH_TOL = 5.0
VWAP_DELTA_CAP = 65.0
OR_CLASSES_ALLOWED = {"normal"}


def load_1m(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize(ET)
    else:
        df.index = df.index.tz_convert(ET)
    df = df.sort_index()
    return df[["open", "high", "low", "close", "volume"]].copy()


def classify_or(or_size: float, history: list[float]) -> str:
    if len(history) < 5:
        return "normal"
    pct = sum(1 for v in history if v <= or_size) / len(history) * 100.0
    if pct <= 25.0:
        return "tight"
    if pct >= 80.0:
        return "wide"
    return "normal"


def day_clean(day: pd.DataFrame) -> tuple[bool, str]:
    if day.empty:
        return False, "missing"
    if day.index[0].time() != pd.Timestamp("09:30").time():
        return False, "late_start"
    orw = day.between_time(OR_START, OR_END)
    if len(orw) != 15:
        return False, "incomplete_or"
    if (orw["volume"] == 0).any():
        return False, "zero_volume_or"
    if float(orw["high"].max() - orw["low"].min()) <= 0:
        return False, "zero_range_or"
    return True, "clean"


def cumulative_vwap(day: pd.DataFrame) -> pd.Series:
    typ = (day["high"] + day["low"] + day["close"]) / 3.0
    cv = (typ * day["volume"]).cumsum()
    vol = day["volume"].cumsum().replace(0, np.nan)
    return cv / vol


def run_tier1(one_min: pd.DataFrame) -> pd.DataFrame:
    rth = one_min.between_time("09:30", "15:59")
    or_history: list[float] = []
    rows: list[dict] = []

    for date_obj, day in rth.groupby(rth.index.date):
        day = day.between_time("09:30", "15:59")
        clean, quality = day_clean(day)
        row: dict = {"date": str(date_obj), "clean": clean, "quality": quality}

        if not clean:
            row.update({"or_class": "unknown", "eligible": False, "reason": quality})
            rows.append(row)
            continue

        orw = day.between_time(OR_START, OR_END)
        or_high = float(orw["high"].max())
        or_low = float(orw["low"].min())
        or_size = or_high - or_low
        or_class = classify_or(or_size, or_history)
        or_history.append(or_size)
        if len(or_history) > 20:
            or_history = or_history[-20:]

        row["or_class"] = or_class
        row["or_high"] = or_high
        row["or_low"] = or_low
        row["or_size"] = or_size

        if or_class not in OR_CLASSES_ALLOWED:
            row.update({"eligible": False, "reason": f"or_class_{or_class}"})
            rows.append(row)
            continue

        # Build features for the day
        day = day.copy()
        day["vwap"] = cumulative_vwap(day)
        day["prev_high"] = day["high"].shift(1)

        # First completed upside breakout strictly after the OR window (>= 09:45)
        bo_start = pd.Timestamp(str(date_obj)).tz_localize(ET) + pd.Timedelta(hours=9, minutes=45)
        post = day[day.index >= bo_start]
        bo_hits = post[post["close"] > or_high]
        if bo_hits.empty:
            row.update({"eligible": False, "reason": "no_upside_breakout"})
            rows.append(row)
            continue
        breakout_ts = bo_hits.index[0]

        # Retest signals: completed bars in [10:00, 11:00], strictly AFTER breakout_ts
        rstart = pd.Timestamp(f"{date_obj} {RETEST_START}").tz_localize(ET)
        rend = pd.Timestamp(f"{date_obj} {RETEST_END}").tz_localize(ET)
        candidates = day[(day.index >= rstart) & (day.index <= rend) & (day.index > breakout_ts)]
        mask = (
            (candidates["low"] <= or_high + TOUCH_TOL)
            & (candidates["close"] > or_high)
            & (candidates["close"] > candidates["prev_high"])
        )
        sigs = candidates[mask]
        if sigs.empty:
            row.update({"eligible": False, "reason": "no_retest_signal"})
            rows.append(row)
            continue

        # Apply VWAP-delta cap, take first that passes
        chosen_signal = None
        for sig_ts, sig in sigs.iterrows():
            if not np.isfinite(sig["vwap"]):
                continue
            if (sig["close"] - sig["vwap"]) > VWAP_DELTA_CAP:
                continue
            chosen_signal = (sig_ts, sig)
            break
        if chosen_signal is None:
            row.update({"eligible": False, "reason": "signal_filtered"})
            rows.append(row)
            continue
        sig_ts, sig = chosen_signal

        # Entry = next bar's open
        next_bars = day[day.index > sig_ts]
        if next_bars.empty:
            row.update({"eligible": False, "reason": "no_next_open"})
            rows.append(row)
            continue
        entry_ts = next_bars.index[0]
        raw_entry = float(next_bars.iloc[0]["open"])
        entry_price = raw_entry + HALF_SLIP   # long
        stop_price = entry_price - STOP_PTS
        target_price = entry_price + TARGET_PTS

        # Walk forward bars until 15:55, stop-first ambiguity
        end_ts = pd.Timestamp(f"{date_obj} {TIME_EXIT}").tz_localize(ET)
        path = day[(day.index >= entry_ts) & (day.index <= end_ts)]
        if path.empty:
            row.update({"eligible": False, "reason": "no_exit_path"})
            rows.append(row)
            continue
        exit_ts = exit_reason = raw_exit = None
        for ts, bar in path.iterrows():
            stop_hit = bar["low"] <= stop_price
            target_hit = bar["high"] >= target_price
            if stop_hit:
                exit_ts, raw_exit, exit_reason = ts, stop_price, "stop"
                break
            if target_hit:
                exit_ts, raw_exit, exit_reason = ts, target_price, "target"
                break
        if exit_ts is None:
            last = path.iloc[-1]
            exit_ts, raw_exit, exit_reason = last.name, float(last["close"]), "time_exit"

        exit_price = raw_exit - HALF_SLIP
        gross_pts = exit_price - entry_price
        net_pnl = gross_pts * MULT - COMM

        row.update({
            "eligible": True,
            "reason": "",
            "breakout_ts": breakout_ts,
            "signal_ts": sig_ts,
            "entry_ts": entry_ts,
            "raw_entry": raw_entry,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "exit_ts": exit_ts,
            "raw_exit": raw_exit,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "net_pnl": net_pnl,
            "vwap_at_signal": float(sig["vwap"]),
            "close_minus_vwap": float(sig["close"] - sig["vwap"]),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(label: str, pnl: pd.Series) -> dict:
    if len(pnl) == 0:
        return {}
    wins = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl <= 0].sum())
    pf = float("inf") if losses == 0 else wins / losses
    eq = pnl.cumsum().values
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    print(f"{label}: n={len(pnl)} sum=${pnl.sum():.2f} avg=${pnl.mean():.2f} "
          f"wr={(pnl>0).mean():.4f} pf={pf:.4f} maxDD=${dd.min():.2f}")
    return {"n": len(pnl), "sum": pnl.sum(), "avg": pnl.mean(),
            "wr": (pnl > 0).mean(), "pf": pf, "maxDD": dd.min()}


def bootstrap_ci(pnl: np.ndarray, stat_fn, B=10000, seed=42):
    rng = np.random.default_rng(seed)
    n = len(pnl)
    samples = np.array([stat_fn(rng.choice(pnl, n, replace=True)) for _ in range(B)])
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def permutation_test_pnl_vs_zero(pnl: np.ndarray, B=10000, seed=42):
    """Two-sided sign-flip permutation: under H0 of zero expected value, sign of
    each trade is symmetric. Compute p-value for observed mean."""
    rng = np.random.default_rng(seed)
    obs = pnl.mean()
    n = len(pnl)
    perm_means = np.empty(B)
    for i in range(B):
        signs = rng.choice([-1, 1], size=n)
        perm_means[i] = (signs * pnl).mean()
    p_two_sided = float((np.abs(perm_means) >= np.abs(obs)).mean())
    return obs, p_two_sided


def main() -> int:
    one_min = load_1m(PROJECT_ROOT / "data" / "mnq_1m.parquet")
    log = run_tier1(one_min)
    log.to_csv(PROJECT_ROOT / "research" / "human_edge_replay" / "phase6_independent_verify.csv", index=False)

    print("=== Tier 1 independent re-run (long-only, normal OR, VWAP cap 65, 5pt RT slip) ===")
    elig = log[log.eligible]
    pnl = elig["net_pnl"].astype(float)
    summarize("frozen_replay", pnl)
    print(f"  expected from doc: n=95, sum=$2,186.70, wr=0.6105, PF=1.6845, maxDD=$-417.06")
    print()

    # --- Statistical sanity ---
    print("=== Statistical sanity ===")
    if len(pnl) > 1:
        m = pnl.mean()
        s = pnl.std(ddof=1)
        sharpe = m / s
        se_sharpe = np.sqrt((1 + sharpe**2 / 2) / len(pnl))
        ci_sharpe = (sharpe - 1.96*se_sharpe, sharpe + 1.96*se_sharpe)
        ci_mean = (m - 1.96*s/np.sqrt(len(pnl)), m + 1.96*s/np.sqrt(len(pnl)))
        t, p = stats.ttest_1samp(pnl, 0)
        print(f"  mean=${m:.2f}  std=${s:.2f}  Sharpe/trade={sharpe:.3f}  CI[{ci_sharpe[0]:.3f},{ci_sharpe[1]:.3f}]")
        print(f"  95% CI mean: [${ci_mean[0]:.2f}, ${ci_mean[1]:.2f}]")
        print(f"  t-test mean!=0: t={t:.3f}  p={p:.4f}")
        print(f"  ann Sharpe @ 40 trades/yr (sqrt(40)): {sharpe*np.sqrt(40):.2f}  CI [{ci_sharpe[0]*np.sqrt(40):.2f}, {ci_sharpe[1]*np.sqrt(40):.2f}]")

        boot_lo, boot_hi = bootstrap_ci(pnl.values, np.mean, B=10000)
        print(f"  bootstrap 95% CI on mean: [${boot_lo:.2f}, ${boot_hi:.2f}]")

        obs, p_perm = permutation_test_pnl_vs_zero(pnl.values, B=10000)
        print(f"  permutation test mean!=0: obs=${obs:.2f}  p={p_perm:.4f}")

        # Multiple-comparison context: doc states 1,152 variants tried in Phase 5.
        # Bonferroni-corrected alpha for one chosen rule: 0.05/1152 = 4.34e-5
        print(f"  Bonferroni-corrected alpha (1152 variants): {0.05/1152:.6f}")
        print(f"  Verdict on edge after multi-comparison: p_perm > Bonferroni? {p_perm > 0.05/1152}")
    print()

    # --- Walk-forward ---
    print("=== Walk-forward (chronological) ===")
    elig_sorted = elig.sort_values("entry_ts").reset_index(drop=True)
    pnl_sorted = elig_sorted["net_pnl"].astype(float)
    n = len(pnl_sorted)
    for split in [0.50, 0.60, 0.70]:
        cut = int(n * split)
        is_p = pnl_sorted.iloc[:cut]
        oos_p = pnl_sorted.iloc[cut:]
        print(f"  split {split:.0%}: IS n={len(is_p)} avg=${is_p.mean():.2f} wr={(is_p>0).mean():.3f}  "
              f"OOS n={len(oos_p)} avg=${oos_p.mean():.2f} wr={(oos_p>0).mean():.3f}")
        # Welch t-test of OOS vs IS means
        t2, p2 = stats.ttest_ind(is_p, oos_p, equal_var=False)
        print(f"    Welch t-test IS vs OOS: t={t2:.3f}  p={p2:.4f}  (large p = no detectable degradation, but small n)")
    print()

    # --- Slippage stress ---
    print("=== Slippage stress (re-run with different RT slippage) ===")
    for slip in [0.0, 1.0, 1.5, 3.0, 5.0, 7.0, 10.0]:
        global HALF_SLIP
        HALF_SLIP = slip / 2.0
        log_s = run_tier1(one_min)
        e = log_s[log_s.eligible]["net_pnl"].astype(float)
        wins_s = e[e > 0].sum(); losses_s = abs(e[e <= 0].sum())
        pf_s = float("inf") if losses_s == 0 else wins_s / losses_s
        print(f"  RT={slip:5.1f} pts: n={len(e)} sum=${e.sum():9.2f}  avg=${e.mean():6.2f}  wr={(e>0).mean():.4f}  PF={pf_s:.4f}")
    HALF_SLIP = SLIP_RT / 2.0  # restore
    print()

    # --- Regime profiling ---
    print("=== Regime profiling ===")
    elig2 = elig.copy()
    elig2["entry_dt"] = pd.to_datetime(elig2["entry_ts"])
    elig2["year"] = elig2["entry_dt"].dt.year
    elig2["month"] = elig2["entry_dt"].dt.to_period("M").astype(str)
    elig2["dow"] = elig2["entry_dt"].dt.day_name()
    print("  by year:")
    print(elig2.groupby("year").agg(n=("net_pnl","count"), sum=("net_pnl","sum"), avg=("net_pnl","mean"),
                                     wr=("net_pnl", lambda s: (s>0).mean())).round(3).to_string())
    print()
    print("  by day-of-week:")
    print(elig2.groupby("dow").agg(n=("net_pnl","count"), sum=("net_pnl","sum"), avg=("net_pnl","mean"),
                                    wr=("net_pnl", lambda s: (s>0).mean())).round(3).to_string())
    print()
    # By exit reason
    print("  by exit_reason:")
    print(elig2.groupby("exit_reason").agg(n=("net_pnl","count"), sum=("net_pnl","sum"), avg=("net_pnl","mean")).round(2).to_string())
    print()

    # --- Concentration / robustness ---
    print("=== Robustness probe ===")
    pnl_arr = pnl.values
    sorted_pnl = np.sort(pnl_arr)[::-1]
    total = sorted_pnl.sum()
    for k in [1, 3, 5, 10, 15, 20]:
        share = sorted_pnl[:k].sum() / total * 100
        print(f"  top {k} trades: ${sorted_pnl[:k].sum():.2f}  ({share:.1f}% of total)")
    # Drop top 5 winners — does edge survive?
    sorted_idx = np.argsort(pnl_arr)[::-1]
    keep = np.ones(len(pnl_arr), dtype=bool)
    keep[sorted_idx[:5]] = False
    pnl_minus_top5 = pnl_arr[keep]
    print(f"  drop top 5 winners: n={len(pnl_minus_top5)} sum=${pnl_minus_top5.sum():.2f} avg=${pnl_minus_top5.mean():.2f}")
    print(f"  drop top 10 winners: n={len(pnl_arr)-10} sum=${np.sort(pnl_arr)[:-10].sum():.2f}")

    # --- Quality breakdown ---
    print()
    print("=== Day quality breakdown across full tape ===")
    print(log["quality"].value_counts().to_string())
    print()
    print("=== OR class on clean days ===")
    print(log[log.clean]["or_class"].value_counts().to_string())

    # --- Selection-funnel transparency ---
    print()
    print("=== Tier1 selection funnel (clean & or_class=normal days) ===")
    norm = log[(log.clean) & (log.or_class == "normal")]
    print(f"  normal-OR days: {len(norm)}")
    print(norm["reason"].value_counts(dropna=False).to_string())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
