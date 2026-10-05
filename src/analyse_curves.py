"""
Phase 5 analysis of the fitted Ghana sovereign curves.

Reads   data/processed/{fits_all.pkl, curve_inputs.pkl, Ghana_Yield_Curve_Dataset_*.xlsx}
Writes  results/Ghana_Yield_Curve_Results_2023_2026.xlsx
        results/*.csv
        figures/*.png
"""

from __future__ import annotations

import datetime as dt
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import curves as C
import pricing as P
from fit_curves import clean_quotes, weighted_fit, UNIVERSE, UNTRADED_WEIGHT

PROCESSED = os.environ.get("GFIM_PROCESSED", "data/processed")
RESULTS = os.environ.get("GFIM_RESULTS", "results")
OUT_XLSX = os.path.join(RESULTS, "Ghana_Yield_Curve_Results_2023_2026.xlsx")
CHARTS = os.environ.get("GFIM_FIGURES", "figures")
DATASET = os.path.join(PROCESSED, "Ghana_Yield_Curve_Dataset_2023_2026.xlsx")

GRID = C.GRID
GRID_COLS = [f"ns_z{t:g}y" for t in GRID]
NAVY, GOLD, RED, GREY = "#1B2A4A", "#C8912A", "#B23A48", "#8A8F98"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9,
    "axes.edgecolor": "#444444", "axes.labelcolor": "#222222",
    "axes.titlesize": 11, "axes.titleweight": "bold",
    "figure.dpi": 150, "savefig.bbox": "tight",
})


# ------------------------------------------------------------------ loading
def load():
    fits = pd.read_pickle(os.path.join(PROCESSED, "fits_all.pkl")).sort_values("report_date").reset_index(drop=True)
    fits["date"] = pd.to_datetime(fits["report_date"])
    quotes = pd.read_pickle(os.path.join(PROCESSED, "curve_inputs.pkl"))
    quotes = quotes[quotes["use_for_fitting"] == "TRUE"]
    panel = pd.read_excel(DATASET, sheet_name="Monthly_Panel")
    return fits, quotes, panel


# --------------------------------------------------------------------- PCA
def run_pca(fits: pd.DataFrame):
    """PCA on weekly changes in the fitted zero curve."""
    z = fits.set_index("date")[GRID_COLS].resample("W-FRI").last().dropna()
    d = z.diff().dropna()
    x = d.to_numpy()
    x = x - x.mean(axis=0)
    cov = np.cov(x, rowvar=False)
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    explained = vals / vals.sum()

    loadings = pd.DataFrame(vecs[:, :3], index=[f"{t:g}y" for t in GRID],
                            columns=["PC1", "PC2", "PC3"]).reset_index()
    loadings = loadings.rename(columns={"index": "maturity"})
    summary = pd.DataFrame({
        "component": ["PC1", "PC2", "PC3", "PC4+"],
        "explained_share": list(np.round(explained[:3], 4)) + [round(explained[3:].sum(), 4)],
        "interpretation": ["level", "slope", "curvature", "residual"],
    })
    scores = pd.DataFrame(x @ vecs[:, :3], index=d.index,
                          columns=["PC1", "PC2", "PC3"]).reset_index()
    return loadings, summary, scores, explained


# -------------------------------------------------------------- rich/cheap
def rich_cheap(quotes: pd.DataFrame, date) -> pd.DataFrame:
    rows = quotes[(quotes["report_date"] == date)
                  & (quotes["segment"].isin(UNIVERSE))]
    cleaned = clean_quotes(rows)
    inst = C.build_instruments(cleaned, date)
    w = [1.0 if i.traded else UNTRADED_WEIGHT for i in inst]
    short = np.median([i.yield_pct for i in inst if i.ttm < 1] or [15.0])
    long = np.median([i.yield_pct for i in inst if i.ttm > 5] or [short])
    _, fn = weighted_fit(inst, w, C.ns_zero, [long, short - long, 0.0, 1.5],
                         ([-50, -100, -100, 0.05], [200, 100, 100, 30]))

    by_isin = rows.set_index("isin")
    out = []
    for i in inst:
        r = by_isin.loc[i.isin]
        if isinstance(r, pd.DataFrame):
            r = r.iloc[0]
        model_price = i.model_price(fn)
        if i.kind == "BILL":
            days = max(int(round(i.ttm * 365)), 1)
            model_yield = P.bill_yield(model_price, days)
            dv01 = P.bill_dv01(i.price, days)
        else:
            clean = model_price - P.accrued_interest(date, r["maturity_date"],
                                                     r["coupon_pct"], 2, "act/365")
            model_yield = P.bond_ytm(clean, date, r["maturity_date"], r["coupon_pct"])
            risk = P.duration_and_dv01(i.yield_pct, date, r["maturity_date"],
                                       r["coupon_pct"])
            dv01 = risk["dv01"]
        if model_yield is None:
            continue
        out.append({
            "isin": i.isin, "instrument": i.kind, "security": r["security"],
            "maturity_date": r["maturity_date"], "ttm_years": round(i.ttm, 3),
            "coupon_pct": r["coupon_pct"], "market_yield_pct": i.yield_pct,
            "curve_yield_pct": round(model_yield, 4),
            "spread_bp": round((i.yield_pct - model_yield) * 100, 1),
            "market_price": round(i.price, 4), "model_price": round(model_price, 4),
            "dv01_per_100": round(dv01, 5),
            "traded": "TRUE" if i.traded else "FALSE",
            "volume": r["volume"],
        })
    df = pd.DataFrame(out).sort_values("ttm_years")
    df["signal"] = np.where(df["spread_bp"] > 50, "cheap vs curve",
                   np.where(df["spread_bp"] < -50, "rich vs curve", "fair"))
    return df


# ------------------------------------------------------------------- risk
def risk_scenarios(rc: pd.DataFrame) -> pd.DataFrame:
    """A simple equally weighted portfolio of the traded bonds, shocked."""
    port = rc[(rc["instrument"] == "BOND") & (rc["traded"] == "TRUE")].copy()
    if port.empty:
        port = rc[rc["instrument"] == "BOND"].copy()
    notional = 1_000_000.0 / len(port)          # GHS 1m face, split evenly
    port["face"] = notional
    port["dv01_ghs"] = port["dv01_per_100"] * port["face"] / 100.0

    shocks = {
        "parallel +100bp": lambda t: np.full_like(t, 1.0),
        "parallel -100bp": lambda t: np.full_like(t, -1.0),
        "steepener (+50bp 10y, -50bp 1y)": lambda t: np.interp(t, [1, 10], [-0.5, 0.5]),
        "flattener (-50bp 10y, +50bp 1y)": lambda t: np.interp(t, [1, 10], [0.5, -0.5]),
        "front-end selloff (+200bp under 2y)": lambda t: np.where(t <= 2, 2.0, 0.0),
    }
    rows = []
    t = port["ttm_years"].to_numpy()
    for name, shape in shocks.items():
        bp = shape(t) * 100.0
        pnl = -(port["dv01_ghs"].to_numpy() * bp).sum()
        rows.append({"scenario": name, "portfolio_pnl_ghs": round(pnl, 2),
                     "pnl_pct_of_face": round(pnl / 1_000_000 * 100, 4)})
    summary = pd.DataFrame(rows)
    summary.loc[len(summary)] = {"scenario": "total DV01 (GHS per bp)",
                                 "portfolio_pnl_ghs": round(port["dv01_ghs"].sum(), 2),
                                 "pnl_pct_of_face": np.nan}
    return port[["isin", "security", "ttm_years", "coupon_pct", "market_yield_pct",
                 "face", "dv01_per_100", "dv01_ghs"]], summary


# ----------------------------------------------------------------- charts
def make_charts(fits, quotes, panel, loadings, explained, rc, snap_dates):
    os.makedirs(CHARTS, exist_ok=True)
    paths = []

    # 1. curve snapshots
    fig, ax = plt.subplots(figsize=(7, 4.2))
    colors = [RED, GOLD, GREY, NAVY]
    for (d, c) in zip(snap_dates, colors):
        row = fits[fits["report_date"] == d]
        if row.empty:
            continue
        ax.plot(GRID, row[GRID_COLS].to_numpy().ravel(), marker="o", ms=3,
                color=c, label=str(d))
    ax.set_xlabel("maturity (years)")
    ax.set_ylabel("zero rate (%, continuously compounded)")
    ax.set_title("Ghana sovereign zero curve, Nelson-Siegel fit")
    ax.legend(frameon=False)
    ax.grid(alpha=.25)
    p = f"{CHARTS}/01_curve_snapshots.png"
    fig.savefig(p); plt.close(fig); paths.append(p)

    # 2. surface heatmap
    z = fits.set_index("date")[GRID_COLS].resample("W-FRI").last().dropna()
    fig, ax = plt.subplots(figsize=(8, 4.2))
    im = ax.imshow(z.to_numpy().T, aspect="auto", origin="lower", cmap="viridis",
                   extent=[0, len(z) - 1, 0, len(GRID) - 1])
    ax.set_yticks(range(len(GRID)))
    ax.set_yticklabels([f"{t:g}y" for t in GRID])
    step = max(len(z) // 10, 1)
    ax.set_xticks(range(0, len(z), step))
    ax.set_xticklabels([d.strftime("%b %y") for d in z.index[::step]], rotation=45,
                       ha="right")
    ax.set_title("Zero rate (%) by maturity and week")
    fig.colorbar(im, ax=ax, label="%")
    p = f"{CHARTS}/02_curve_surface.png"
    fig.savefig(p); plt.close(fig); paths.append(p)

    # 3. factors vs macro
    m = fits.set_index("date")[["ns_z0.25y", "ns_z1y", "ns_z10y"]].resample("ME").mean()
    pan = panel.rename(columns={c: c.lower().replace(" ", "_") for c in panel.columns}).copy()
    pan["date"] = pd.PeriodIndex(pan["month"].astype(str), freq="M").to_timestamp("M")
    pan = pan.set_index("date")
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot(m.index, m["ns_z0.25y"], color=GOLD, label="3-month zero")
    ax.plot(m.index, m["ns_z1y"], color=NAVY, label="1-year zero")
    ax.plot(m.index, m["ns_z10y"], color=GREY, label="10-year zero")
    ax.plot(pan.index, pan["policy_rate_pct"], color=RED, ls="--", label="policy rate")
    ax.plot(pan.index, pan["headline_inflation_pct"], color="black", ls=":",
            label="headline inflation")
    ax.set_ylabel("%")
    ax.set_title("Fitted zero rates against policy rate and inflation")
    ax.legend(frameon=False, ncol=2, fontsize=8)
    ax.grid(alpha=.25)
    p = f"{CHARTS}/03_rates_vs_macro.png"
    fig.savefig(p); plt.close(fig); paths.append(p)

    # 4. PCA loadings
    fig, ax = plt.subplots(figsize=(6, 3.6))
    for col, c, lab in zip(["PC1", "PC2", "PC3"], [NAVY, GOLD, RED],
                           ["PC1 level", "PC2 slope", "PC3 curvature"]):
        ax.plot(GRID, loadings[col], marker="o", ms=3, color=c,
                label=f"{lab} ({explained[['PC1', 'PC2', 'PC3'].index(col)]:.0%})")
    ax.axhline(0, color="#999", lw=.8)
    ax.set_xlabel("maturity (years)")
    ax.set_ylabel("loading")
    ax.set_title("PCA of weekly zero-curve changes")
    ax.legend(frameon=False, fontsize=8)
    ax.grid(alpha=.25)
    p = f"{CHARTS}/04_pca_loadings.png"
    fig.savefig(p); plt.close(fig); paths.append(p)

    # 5. fit quality
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.plot(fits["date"], fits["ns_rmse_bp"], color=NAVY, lw=.8)
    ax.plot(fits["date"], fits["ns_rmse_bp"].rolling(21).median(), color=GOLD, lw=1.6,
            label="21-day median")
    ax.set_ylabel("RMSE (basis points)")
    ax.set_title("Curve fit error: quote dispersion falls as the market normalises")
    ax.legend(frameon=False)
    ax.grid(alpha=.25)
    p = f"{CHARTS}/05_fit_quality.png"
    fig.savefig(p); plt.close(fig); paths.append(p)

    # 6. rich/cheap scatter
    fig, ax = plt.subplots(figsize=(7, 3.8))
    tr = rc[rc["traded"] == "TRUE"]
    un = rc[rc["traded"] == "FALSE"]
    ax.scatter(un["ttm_years"], un["spread_bp"], s=16, color=GREY, alpha=.6,
               label="not traded")
    ax.scatter(tr["ttm_years"], tr["spread_bp"], s=26, color=NAVY, label="traded")
    ax.axhline(0, color=RED, lw=1)
    ax.axhline(50, color="#bbb", lw=.8, ls="--")
    ax.axhline(-50, color="#bbb", lw=.8, ls="--")
    ax.set_xlabel("maturity (years)")
    ax.set_ylabel("market minus curve (bp)")
    ax.set_title(f"Rich/cheap against the fitted curve, {rc.attrs.get('date', '')}")
    ax.legend(frameon=False)
    ax.grid(alpha=.25)
    p = f"{CHARTS}/06_rich_cheap.png"
    fig.savefig(p); plt.close(fig); paths.append(p)

    return paths


# ----------------------------------------------------------------- workbook
README = [
    ("Ghana sovereign yield curve: fitted curves and analysis, 2023-2026", ""),
    ("", ""),
    ("Method",
     "Each trading day is fitted with Nelson-Siegel, Svensson and a piecewise-linear "
     "bootstrap. Instruments are Treasury bills plus post-DDEP government bonds. Bills "
     "enter as zero-coupon points; bonds are priced from their full semi-annual cash "
     "flow schedule at act/365, which reproduces GFIM clean prices to a median of about "
     "0.05 per 100 face. Quotes more than three robust deviations from the local median "
     "yield are dropped, remaining quotes are weighted by inverse duration, and quotes "
     "that did not trade that day carry 35% weight."),
    ("Why old GoG bonds are excluded",
     "Pre-DDEP bonds rarely trade and their carried-over quotes sit far off the traded "
     "curve: on 30 September 2026 their median yield was 20.2% against 14.5% for DDEP "
     "bonds. Including them pulls the long end up by several points."),
    ("", ""),
    ("Sheets", ""),
    ("Zero_Curves", "Daily fitted zero rates at standard maturities, all three methods."),
    ("Fit_Params", "Daily Nelson-Siegel and Svensson parameters with fit diagnostics."),
    ("PCA_Loadings / PCA_Summary", "Principal components of weekly curve changes."),
    ("Rich_Cheap", "Latest date: market yield against the fitted curve, with DV01."),
    ("Portfolio / Scenarios", "Equally weighted traded-bond portfolio under rate shocks."),
    ("", ""),
    ("Reading the fit error", ""),
    ("RMSE in basis points",
     "This is the dispersion of quotes around a smooth curve, not just model error. It "
     "falls from a median of about 530bp in 2023 to about 80bp in 2026, which is itself "
     "a measure of how far the market has normalised since the debt exchange. Treat 2023 "
     "and 2024 curve levels as indicative rather than precise."),
    ("Continuous compounding",
     "Zero rates are continuously compounded. Bill money-market yields and bond yields "
     "to maturity elsewhere in the project are quoted on their market conventions, so "
     "the two differ slightly at the same maturity."),
]


def ensure_dirs():
    for d in (RESULTS, CHARTS):
        os.makedirs(d, exist_ok=True)


def write_workbook(fits, zero_curves, loadings, pca_summary, scores, rc, port,
                   scenarios):
    base = {"font_name": "Arial", "font_size": 10}
    with pd.ExcelWriter(OUT_XLSX, engine="xlsxwriter",
                        datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as xl:
        book = xl.book
        f_head = book.add_format({**base, "bold": True, "font_color": "#FFFFFF",
                                  "bg_color": "#1B2A4A", "text_wrap": True,
                                  "valign": "vcenter", "border": 1})
        f_title = book.add_format({**base, "bold": True, "font_size": 14})
        f_sub = book.add_format({**base, "bold": True})
        f_wrap = book.add_format({**base, "text_wrap": True, "valign": "top"})
        f_num = book.add_format({**base, "num_format": "#,##0.00"})
        f_num4 = book.add_format({**base, "num_format": "#,##0.0000"})
        f_int = book.add_format({**base, "num_format": "#,##0"})
        f_date = book.add_format({**base, "num_format": "yyyy-mm-dd"})
        f_text = book.add_format(base)

        ws = book.add_worksheet("README")
        ws.set_column(0, 0, 28, f_sub)
        ws.set_column(1, 1, 110, f_wrap)
        ws.write(0, 0, README[0][0], f_title)
        for r, (a, b) in enumerate(README[1:], start=2):
            ws.write(r, 0, a, f_sub)
            ws.write(r, 1, b, f_wrap)

        def dump(name, frame):
            frame.to_excel(xl, sheet_name=name, index=False, header=False, startrow=1)
            sh = xl.sheets[name]
            for c, col in enumerate(frame.columns):
                sh.write(0, c, str(col).replace("_", " "), f_head)
                s = frame[col]
                if "date" in str(col):
                    fmt, w = f_date, 12
                elif s.dtype.kind in "fi":
                    if any(k in str(col) for k in ("price", "dv01", "loading", "PC")):
                        fmt, w = f_num4, 13
                    elif any(k in str(col) for k in ("n", "face", "volume", "pnl")):
                        fmt, w = f_int, 14
                    else:
                        fmt, w = f_num, 12
                else:
                    fmt, w = f_text, 20
                sh.set_column(c, c, w, fmt)
            sh.freeze_panes(1, 0)
            sh.autofilter(0, 0, max(len(frame), 1), len(frame.columns) - 1)
            sh.set_row(0, 28)

        dump("Zero_Curves", zero_curves)
        dump("Fit_Params", fits)
        dump("PCA_Summary", pca_summary)
        dump("PCA_Loadings", loadings)
        dump("PCA_Scores", scores)
        dump("Rich_Cheap", rc)
        dump("Portfolio", port)
        dump("Scenarios", scenarios)


def main():
    ensure_dirs()
    fits, quotes, panel = load()

    keep = ["report_date", "n", "n_bills", "n_bonds", "n_traded", "dropped_outliers",
            "max_ttm", "ns_beta0", "ns_beta1", "ns_beta2", "ns_tau", "ns_rmse_bp",
            "ns_rmse_bp_traded", "sv_rmse_bp", "bs_rmse_bp",
            "ns_fwd_1y1y", "ns_fwd_2y3y", "ns_fwd_5y5y"]
    params = fits[[c for c in keep if c in fits.columns]].copy()

    zc_cols = ["report_date"] + GRID_COLS + [f"sv_z{t:g}y" for t in GRID] \
              + [f"bs_z{t:g}y" for t in GRID]
    zero_curves = fits[[c for c in zc_cols if c in fits.columns]].copy()

    loadings, pca_summary, scores, explained = run_pca(fits)

    last_date = max(quotes["report_date"])
    rc = rich_cheap(quotes, last_date)
    rc.attrs["date"] = str(last_date)
    port, scenarios = risk_scenarios(rc)

    snap = [dt.date(2023, 1, 3), dt.date(2024, 6, 28), dt.date(2025, 6, 30), last_date]
    charts = make_charts(fits, quotes, panel, loadings, explained, rc, snap)

    write_workbook(params, zero_curves, loadings, pca_summary, scores, rc, port,
                   scenarios)

    print("fits:", len(fits), "| PCA explained:", np.round(explained[:3], 3))
    print("rich/cheap date:", last_date, "rows:", len(rc))
    print(scenarios.to_string(index=False))
    zero_cols = ["report_date"] + GRID_COLS
    fits[zero_cols].to_csv(os.path.join(RESULTS, "ghana_zero_curves.csv"), index=False)
    rc.to_csv(os.path.join(RESULTS, f"rich_cheap_{last_date}.csv"), index=False)
    print("charts:", charts)
    print("workbook:", OUT_XLSX)


if __name__ == "__main__":
    main()
