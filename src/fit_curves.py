"""
Fit a zero curve to every trading day and store the results.

Universe: Treasury bills plus post-DDEP government bonds (DDEP and new GoG).
Pre-DDEP "old GoG" bonds are excluded by default: they barely trade and their
carried-over quotes sit far off the traded curve (20.2% median against 14.5%
for DDEP bonds on 30 September 2026), which would drag the long end.

Per date:
  - drop quotes more than 3 robust deviations from the local median yield
  - weight each quote by inverse duration, and quotes that did not trade by 0.35
  - fit Nelson-Siegel and Svensson with a soft-L1 loss, and bootstrap a
    piecewise-linear curve
  - record parameters, fit diagnostics and the zero rate at standard maturities

Usage:  python fit_curves.py [start_index] [end_index]
Writes: fits_<start>.pkl, merged later by the analysis script.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

import curves as C

UNIVERSE = ["bills", "ddep", "gog_new", "gog"]
GRID = C.GRID
UNTRADED_WEIGHT = 0.35
OUTLIER_K = 3.0


def clean_quotes(rows: pd.DataFrame, k: float = OUTLIER_K) -> pd.DataFrame:
    """Drop quotes far from the local median yield across the maturity ladder."""
    r = rows.sort_values("ttm_years").copy()
    y = r["yield_pct"].to_numpy(dtype=float)
    med = pd.Series(y).rolling(9, center=True, min_periods=3).median().to_numpy()
    resid = np.abs(y - med)
    mad = np.nanmedian(resid)
    if not np.isfinite(mad):
        return r
    return r[resid <= (k * 1.4826 * mad + 0.75)]


def weighted_fit(instruments, weights, model, x0, bounds):
    def residuals(p):
        fn = lambda t: model(t, *p)
        return np.array([(i.model_price(fn) - i.price) * i.weight * w
                         for i, w in zip(instruments, weights)])

    sol = least_squares(residuals, x0, bounds=bounds, loss="soft_l1",
                        f_scale=0.3, max_nfev=3000)
    return sol.x, (lambda t: model(t, *sol.x))


def fit_one_day(date, rows):
    rows = rows[rows["segment"].isin(UNIVERSE)]
    if len(rows) < 8:
        return None
    cleaned = clean_quotes(rows)
    inst = C.build_instruments(cleaned, date)
    if len(inst) < 8:
        return None

    bills = [i for i in inst if i.kind == "BILL"]
    bonds = [i for i in inst if i.kind == "BOND"]
    if not bills or not bonds:
        return None

    w = [1.0 if i.traded else UNTRADED_WEIGHT for i in inst]
    short = np.median([i.yield_pct for i in inst if i.ttm < 1] or [15.0])
    long = np.median([i.yield_pct for i in inst if i.ttm > 5] or [short])

    rec = {"report_date": date, "n": len(inst), "n_bills": len(bills),
           "n_bonds": len(bonds), "n_traded": sum(i.traded for i in inst),
           "max_ttm": max(i.ttm for i in inst),
           "dropped_outliers": len(rows) - len(cleaned)}

    # Nelson-Siegel
    try:
        p_ns, f_ns = weighted_fit(inst, w, C.ns_zero,
                                  [long, short - long, 0.0, 1.5],
                                  ([-50, -100, -100, 0.05], [200, 100, 100, 30]))
        d = C.fit_diagnostics(inst, f_ns)
        rec.update({"ns_beta0": p_ns[0], "ns_beta1": p_ns[1], "ns_beta2": p_ns[2],
                    "ns_tau": p_ns[3], "ns_rmse_bp": d["rmse_yield_bp"],
                    "ns_rmse_price": d["rmse_price"]})
        tr = [i for i in inst if i.traded]
        rec["ns_rmse_bp_traded"] = (C.fit_diagnostics(tr, f_ns)["rmse_yield_bp"]
                                    if tr else np.nan)
        for t, z in zip(GRID, f_ns(GRID)):
            rec[f"ns_z{t:g}y"] = float(z)
        rec["ns_fwd_1y1y"] = C.forward_rate(f_ns, 1.0, 2.0)
        rec["ns_fwd_2y3y"] = C.forward_rate(f_ns, 2.0, 5.0)
        rec["ns_fwd_5y5y"] = C.forward_rate(f_ns, 5.0, 10.0)
    except Exception as e:                                   # noqa: BLE001
        rec["ns_error"] = str(e)[:80]

    # Svensson
    try:
        base = [rec.get("ns_beta0", long), rec.get("ns_beta1", 0.0),
                rec.get("ns_beta2", 0.0), 0.0, rec.get("ns_tau", 1.5),
                max(rec.get("ns_tau", 1.5) * 3, 5.0)]
        p_sv, f_sv = weighted_fit(
            inst, w, C.svensson_zero, base,
            ([-50, -100, -100, -100, 0.05, 0.05], [200, 100, 100, 100, 30, 30]))
        d = C.fit_diagnostics(inst, f_sv)
        rec.update({"sv_beta0": p_sv[0], "sv_beta1": p_sv[1], "sv_beta2": p_sv[2],
                    "sv_beta3": p_sv[3], "sv_tau1": p_sv[4], "sv_tau2": p_sv[5],
                    "sv_rmse_bp": d["rmse_yield_bp"]})
        for t, z in zip(GRID, f_sv(GRID)):
            rec[f"sv_z{t:g}y"] = float(z)
    except Exception as e:                                   # noqa: BLE001
        rec["sv_error"] = str(e)[:80]

    # bootstrap
    try:
        kt, kz, f_bs = C.bootstrap_curve(inst)
        d = C.fit_diagnostics(inst, f_bs)
        rec.update({"bs_knots": len(kt), "bs_rmse_bp": d["rmse_yield_bp"],
                    "bs_max_ttm": float(kt.max()) if len(kt) else np.nan})
        for t, z in zip(GRID, f_bs(GRID)):
            rec[f"bs_z{t:g}y"] = float(z)
    except Exception as e:                                   # noqa: BLE001
        rec["bs_error"] = str(e)[:80]

    return rec


def main():
    processed = os.environ.get("GFIM_PROCESSED", "data/processed")
    q = pd.read_pickle(os.path.join(processed, "curve_inputs.pkl"))
    q = q[q["use_for_fitting"] == "TRUE"]
    dates = sorted(q["report_date"].unique())

    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    end = int(sys.argv[2]) if len(sys.argv) > 2 else len(dates)
    chunk = dates[start:end]
    print(f"fitting {len(chunk)} dates ({start}:{end})")

    by_date = {d: g for d, g in q.groupby("report_date")}
    out = []
    for i, d in enumerate(chunk, 1):
        rec = fit_one_day(d, by_date[d])
        if rec:
            out.append(rec)
        if i % 50 == 0:
            print(f"  {i}/{len(chunk)}")

    df = pd.DataFrame(out)
    name = "fits_all.pkl" if (start == 0 and end >= len(dates)) else f"fits_{start:04d}.pkl"
    path = os.path.join(processed, name)
    df.to_pickle(path)
    print(f"saved {path}  rows={len(df)}")


if __name__ == "__main__":
    main()
