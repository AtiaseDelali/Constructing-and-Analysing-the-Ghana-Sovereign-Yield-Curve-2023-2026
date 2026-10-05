"""
Consolidate every source into one workbook for the project
"Constructing and Analysing the Ghana Sovereign Yield Curve, 2023-2026".

Inputs
  all.pkl                             parsed GFIM daily reports (172,771 rows)
  New_GoG_Notes__Bonds.csv            DDEP/new bond static data
  Old_GoG_Notes_and_Bonds.xlsx        pre-DDEP bond static data
  Corporate_Bonds_GoG.csv             corporate static data (reference only)
  ACTIVE_SECURITIES_-_Government.xlsx three extra GoG bonds
  Treasury_Bill_Rates.xlsx            BoG primary auction results
  Historical_Policy_Rate_Decisions.xlsx
  Time_Series_Monthly.xlsx            headline inflation
"""

import datetime as dt
import os
import re

import numpy as np
import pandas as pd

UP = os.environ.get("GFIM_REFERENCE", "data/raw/reference") + "/"
PROCESSED = os.environ.get("GFIM_PROCESSED", "data/processed")
OUT = os.path.join(PROCESSED, "Ghana_Yield_Curve_Dataset_2023_2026.xlsx")
BASIS = 364.0                      # GFIM bill convention, verified against reports


# ------------------------------------------------------------------ helpers
def pct(x):
    """Coupon fields arrive as '10.00%', 0.215, 21.5 or '1.900,00'."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    if isinstance(x, str):
        s = x.strip().replace("%", "")
        if re.fullmatch(r"\d{1,2}\.\d{3},\d{2}", s):      # 1.900,00 -> 19.00
            return float(s.replace(".", "").replace(",", ".")) / 100
        s = s.replace(",", "")
        try:
            x = float(s)
        except ValueError:
            return None
    x = float(x)
    if x <= 1.0:                                           # 0.215 -> 21.5
        return round(x * 100, 4)
    if x > 100:                                            # 1900.00 -> 19.00
        return round(x / 100, 4)
    return round(x, 4)


def to_date(v, dayfirst=True):
    if isinstance(v, (dt.datetime, pd.Timestamp)):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and v.strip():
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y"):
            try:
                return dt.datetime.strptime(v.strip()[:11], fmt).date()
            except ValueError:
                pass
        ts = pd.to_datetime(v.strip(), dayfirst=dayfirst, errors="coerce")
        return ts.date() if pd.notna(ts) else None
    return None


def coupon_from_description(s):
    """GOG-BD-17/08/27-A6139-1838-10.00 -> 10.00"""
    if not isinstance(s, str):
        return None
    m = re.search(r"-(\d{1,2}(?:\.\d{1,2})?)\s*$", s.strip())
    return float(m.group(1)) if m else None


def maturity_from_description(s):
    """GOG-BD-17/08/27-... -> 2027-08-17"""
    if not isinstance(s, str):
        return None
    m = re.search(r"-(\d{2})/(\d{2})/(\d{2})-", s)
    if not m:
        return None
    d, mo, y = (int(g) for g in m.groups())
    try:
        return dt.date(2000 + y, mo, d)
    except ValueError:
        return None


# ------------------------------------------------------------------ sources
def load_static():
    frames = []

    new = pd.read_csv(UP + "New_GoG_Notes__Bonds.csv")
    frames.append(pd.DataFrame({
        "isin": new["ISIN"], "static_tenor": new["TENOR"],
        "static_maturity": new["MATURITY DATE"].map(to_date),
        "coupon_pct": new["COUPON RATE"].map(pct),
        "coupon_type": new["COUPON TYPE"], "currency": new["CURRENCY TYPE"],
        "issuer_code": new["ISSUER CODE"], "static_source": "New GoG notes & bonds"}))

    old = pd.read_excel(UP + "Old_GoG_Notes_and_Bonds.xlsx")
    frames.append(pd.DataFrame({
        "isin": old["ISIN"], "static_tenor": old["TENOR"],
        "static_maturity": old["MATURITY DATE"].map(to_date),
        "coupon_pct": old["COUPON RATE"].map(pct),
        "coupon_type": old["COUPON TYPE"], "currency": old["CURRENCY TYPE"],
        "issuer_code": old["ISSUER CODE"], "static_source": "Old GoG notes & bonds"}))

    act = pd.read_excel(UP + "ACTIVE_SECURITIES_-_Government.xlsx")
    frames.append(pd.DataFrame({
        "isin": act["ISIN"], "static_tenor": act["NAME"],
        "static_maturity": act["MATURITY DATE"].map(to_date),
        "coupon_pct": act["COUPON_RATE"].map(pct),
        "coupon_type": act["COUPON_TYPE"], "currency": act["CURRENCY_TYPE"],
        "issuer_code": act["ISSUER_CODE"], "static_source": "Active securities"}))

    corp = pd.read_csv(UP + "Corporate_Bonds_GoG.csv")
    frames.append(pd.DataFrame({
        "isin": corp["ISIN"], "static_tenor": None,
        "static_maturity": corp["MATURITY DATE"].map(to_date),
        "coupon_pct": corp["COUPON RATE"].map(pct),
        "coupon_type": corp["COUPON TYPE"], "currency": corp["CURRENCY TYPE"],
        "issuer_code": corp["ISSUER CODE"], "static_source": "Corporate bonds"}))

    s = pd.concat(frames, ignore_index=True)
    return s.drop_duplicates(subset="isin", keep="first")


def load_auctions():
    a = pd.read_excel(UP + "Treasury_Bill_Rates.xlsx")
    a["issue_date"] = pd.to_datetime(a["Issue Date"], format="mixed",
                                     dayfirst=True).dt.date
    a = a.rename(columns={"Tender": "tender", "Security Type": "security_type",
                          "Discount Rate": "discount_rate_pct",
                          "Interest Rate": "interest_rate_pct"})
    a["is_bill"] = np.where(a["security_type"].str.contains("BILL"), "TRUE", "FALSE")
    a["tenor_days"] = a["security_type"].str.extract(r"(\d+)\s*DAY").astype(float)
    return a[["issue_date", "tender", "security_type", "tenor_days", "is_bill",
              "discount_rate_pct", "interest_rate_pct"]].sort_values("issue_date")


def load_policy():
    p = pd.read_excel(UP + "Historical_Policy_Rate_Decisions.xlsx")
    p["effective_date"] = pd.to_datetime(p["Effective Date"], format="mixed",
                                         dayfirst=True).dt.date
    p = p.rename(columns={"Meeting No.": "meeting_no", "MPC Dates": "mpc_dates",
                          "BOG Policy Rate": "policy_rate_pct"})
    return p[["meeting_no", "mpc_dates", "effective_date",
              "policy_rate_pct"]].sort_values("effective_date")


# ---------------------------------------------------------------- inflation
# Headline year-on-year inflation, %, compiled 3 Oct 2026.
# The BoG time-series export the user downloaded stops in April 2023, so the
# rest comes from Ghana Statistical Service CPI releases and GSS figures as
# reported in the press. CPI index (2021=100) is filled where a release table
# gave it. Jan-Apr 2023 appear in both sources and agree exactly.
# month: (yoy_pct, cpi_index or None, source)
INFLATION_FILL = {
    "2023-01": (53.6, 165.6, "GSS CPI release (May 2023 table)"),
    "2023-02": (52.8, 168.7, "GSS CPI release (May 2023 table)"),
    "2023-03": (45.0, 166.6, "GSS CPI release (May 2023 table)"),
    "2023-04": (41.2, 170.5, "GSS CPI release (May 2023 table)"),
    "2023-05": (42.2, 178.7, "GSS CPI release, May 2023"),
    "2023-06": (42.5, 184.4, "GSS CPI release, June 2023"),
    "2023-07": (43.1, None, "GSS, via August 2023 release (3.0 ppt drop to 40.1%)"),
    "2023-08": (40.1, 190.6, "GSS CPI release, August 2023"),
    "2023-09": (38.1, None, "GSS CPI release (March 2024 table)"),
    "2023-10": (35.2, 195.2, "GSS CPI release (March 2024 table)"),
    "2023-11": (26.4, 198.2, "GSS CPI release (March 2024 table)"),
    "2023-12": (23.2, 200.5, "GSS CPI release (March 2024 table)"),
    "2024-01": (23.5, 204.5, "GSS CPI release (March 2024 table)"),
    "2024-02": (23.2, 207.8, "GSS CPI release (March 2024 table)"),
    "2024-03": (25.8, 209.5, "GSS CPI release (March 2024 table)"),
    "2024-04": (25.0, 213.3, "GSS CPI release, April 2024"),
    "2024-05": (23.1, None, "GSS CPI bulletin, May 2024"),
    "2024-06": (22.8, None, "GSS CPI release, June 2024"),
    "2024-07": (20.9, 231.0, "GSS CPI release (July 2025 table)"),
    "2024-08": (20.4, 229.4, "GSS CPI release (July 2025 table)"),
    "2024-09": (21.5, 235.8, "GSS CPI release (July 2025 table)"),
    "2024-10": (22.1, 237.8, "GSS CPI release (July 2025 table)"),
    "2024-11": (23.0, 243.9, "GSS CPI release (July 2025 table)"),
    "2024-12": (23.8, 248.3, "GSS CPI release (July 2025 table)"),
    "2025-01": (23.5, 252.6, "GSS CPI release (July 2025 table)"),
    "2025-02": (23.1, 255.9, "GSS CPI release (July 2025 table)"),
    "2025-03": (22.4, 256.5, "GSS CPI release (July 2025 table)"),
    "2025-04": (21.2, 258.6, "GSS CPI release (July 2025 table)"),
    "2025-05": (18.4, 260.5, "GSS CPI release (July 2025 table)"),
    "2025-06": (13.7, 257.3, "GSS CPI release (July 2025 table)"),
    "2025-07": (12.1, 259.1, "GSS CPI release, July 2025"),
    "2025-08": (11.5, None, "GSS, via press reports"),
    "2025-09": (9.4, None, "GSS, via press reports"),
    "2025-10": (8.0, None, "GSS, via November 2025 release"),
    "2025-11": (6.3, None, "GSS CPI release, November 2025"),
    "2025-12": (5.4, None, "GSS, via press reports"),
    "2026-01": (3.8, None, "GSS, via press reports"),
    "2026-02": (3.3, 264.4, "GSS, via press reports"),
    "2026-03": (3.2, 264.8, "GSS, via press reports"),
    "2026-04": (3.4, None, "GSS, via press reports"),
    "2026-05": (3.7, None, "GSS, via press reports"),
    "2026-06": (5.3, None, "GSS, via press reports"),
    "2026-07": (4.6, None, "GSS, via press reports"),
    "2026-08": (5.0, None, "GSS highlights page, August 2026"),
}


def load_inflation():
    t = pd.read_excel(UP + "Time_Series_Monthly.xlsx")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    long = t.melt(id_vars=["Year", "Variables"], value_vars=months,
                  var_name="month_name", value_name="value")
    long["month"] = long["month_name"].map({m: i + 1 for i, m in enumerate(months)})
    long["date"] = pd.to_datetime(dict(year=long["Year"], month=long["month"],
                                       day=1)).dt.date
    long = long.rename(columns={"Year": "year", "Variables": "variable"})
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long.loc[long["value"] == 0, "value"] = np.nan        # 0.0 means not published
    long = long.dropna(subset=["value"]).copy()
    long["cpi_index"] = np.nan
    long["source"] = "BoG monthly time series export"
    long = long[["date", "year", "month", "variable", "value", "cpi_index", "source"]]

    # drop anything the BoG export covers from 2023 on, then splice in the
    # researched series so every month of the study period is present
    long = long[pd.to_datetime(long["date"]) < "2023-01-01"]

    rows = []
    for key, (yoy, cpi, src) in INFLATION_FILL.items():
        y, m = (int(x) for x in key.split("-"))
        rows.append({"date": dt.date(y, m, 1), "year": y, "month": m,
                     "variable": "Headline Inflation (%) Year-on-Year",
                     "value": yoy, "cpi_index": cpi, "source": src})
    filled = pd.DataFrame(rows)

    out = pd.concat([long, filled], ignore_index=True)
    return out.sort_values("date").reset_index(drop=True)


# ------------------------------------------------------------------ build
def build_quotes(static):
    df = pd.read_pickle(os.path.join(PROCESSED, "parsed.pkl"))
    df = df[df["segment"].isin(["gog_new", "gog_old", "gog", "ddep", "bills"])].copy()
    df["instrument"] = np.where(df["segment"] == "bills", "BILL", "BOND")

    df["maturity_from_desc"] = df["security"].map(maturity_from_description)
    df["coupon_from_desc"] = np.where(df["instrument"] == "BOND",
                                      df["security"].map(coupon_from_description),
                                      0.0)

    df = df.merge(static, on="isin", how="left", suffixes=("", "_static"))

    # maturity: report first, then static table, then the security description
    df["maturity_final"] = (df["maturity_date"]
                            .fillna(df["static_maturity"])
                            .fillna(df["maturity_from_desc"]))
    df["maturity_conflict"] = (
        df["maturity_date"].notna() & df["maturity_from_desc"].notna()
        & (df["maturity_date"] != df["maturity_from_desc"]))

    # coupon: static table first, then the description; bills are zero-coupon
    df["coupon_final"] = df["coupon_pct"].fillna(df["coupon_from_desc"])
    df["coupon_source"] = np.where(df["coupon_pct"].notna(), "static table",
                          np.where(df["coupon_from_desc"].notna(), "security description",
                                   "missing"))
    df.loc[df["instrument"] == "BILL", ["coupon_final", "coupon_source"]] = [0.0, "bill (zero coupon)"]

    rd = pd.to_datetime(df["report_date"])
    mt = pd.to_datetime(df["maturity_final"])
    df["ttm_days"] = (mt - rd).dt.days
    df["ttm_years"] = df["ttm_days"] / 365.25

    # bills: derive the money-market yield from price, act/364
    price = df["closing_price"]
    derived = ((100.0 / price) - 1.0) * BASIS / df["ttm_days"] * 100.0
    derived = derived.where((df["instrument"] == "BILL") & price.gt(0)
                            & df["ttm_days"].gt(0))
    df["derived_yield_pct"] = derived.round(6)

    df["yield_pct"] = df["closing_yield"]
    df["yield_source"] = np.where(df["closing_yield"].notna(), "reported closing yield", "")
    fill = df["yield_pct"].isna() & df["derived_yield_pct"].notna()
    df.loc[fill, "yield_pct"] = df.loc[fill, "derived_yield_pct"]
    df.loc[fill, "yield_source"] = "derived from price (act/364)"
    df.loc[df["yield_pct"].isna(), "yield_source"] = "none available"

    df["traded"] = (df["volume"].fillna(0) > 0)

    # what belongs in a curve fit
    reason = pd.Series("", index=df.index)
    reason = reason.mask(df["ttm_days"].isna(), "no maturity")
    reason = reason.mask(df["ttm_days"].le(0), "matured or same-day")
    reason = reason.mask(df["yield_pct"].isna(), "no yield")
    reason = reason.mask(df["yield_pct"].le(0) | df["yield_pct"].ge(100),
                         "yield outside 0-100%")
    reason = reason.mask(df["ttm_days"].lt(7) & df["ttm_days"].gt(0),
                         "under 7 days (annualising noise)")
    df["exclude_reason"] = reason
    df["use_for_fitting"] = reason.eq("")

    cols = ["report_date", "instrument", "segment", "isin", "security",
            "tenor", "static_tenor", "coupon_final", "coupon_source",
            "maturity_final", "maturity_from_desc", "maturity_conflict",
            "ttm_days", "ttm_years", "opening_price", "closing_price",
            "day_low_price", "day_high_price", "opening_yield", "closing_yield",
            "derived_yield_pct", "yield_pct", "yield_source", "volume",
            "num_trades", "traded", "use_for_fitting", "exclude_reason",
            "source_file"]
    out = df[cols].rename(columns={"coupon_final": "coupon_pct",
                                   "maturity_final": "maturity_date"})
    for b in ("maturity_conflict", "traded", "use_for_fitting"):
        out[b] = np.where(out[b].astype(bool), "TRUE", "FALSE")
    return out.sort_values(["report_date", "instrument", "ttm_days"])


def build_master(quotes, static):
    quotes = quotes.assign(traded=quotes["traded"].eq("TRUE"))
    g = quotes.groupby("isin")
    m = pd.DataFrame({
        "instrument": g["instrument"].first(),
        "security": g["security"].last(),
        "tenor": g["tenor"].last(),
        "coupon_pct": g["coupon_pct"].last(),
        "coupon_source": g["coupon_source"].last(),
        "maturity_date": g["maturity_date"].last(),
        "first_seen": g["report_date"].min(),
        "last_seen": g["report_date"].max(),
        "quote_days": g.size(),
        "traded_days": g["traded"].sum(),
        "total_volume": g["volume"].sum(),
    }).reset_index()
    m = m.merge(static[["isin", "static_source", "coupon_type", "currency",
                        "issuer_code"]], on="isin", how="left")
    m["in_static_table"] = np.where(m["static_source"].notna(), "TRUE", "FALSE")
    return m.sort_values(["instrument", "maturity_date"])


def build_daily(quotes):
    q = quotes[quotes["use_for_fitting"].eq("TRUE")].copy()
    q["traded"] = q["traded"].eq("TRUE")
    rows = []
    for d, g in q.groupby("report_date"):
        bills, bonds = g[g.instrument == "BILL"], g[g.instrument == "BOND"]
        short = bills.nsmallest(1, "ttm_days")
        long = bonds.nlargest(1, "ttm_days")
        y364 = bills[bills.ttm_days.between(300, 400)]["yield_pct"]
        t = g[g["traded"]]
        tb = t[t.instrument == "BILL"]
        y364t = tb[tb.ttm_days.between(300, 400)]["yield_pct"]
        tlong = t[t.instrument == "BOND"].nlargest(1, "ttm_days")
        rows.append({
            "report_date": d,
            "n_securities": len(g), "n_bills": len(bills), "n_bonds": len(bonds),
            "n_traded": int(g["traded"].sum()),
            "volume_total": g["volume"].sum(skipna=True),
            "min_ttm_years": g["ttm_years"].min(), "max_ttm_years": g["ttm_years"].max(),
            "shortest_bill_yield": short["yield_pct"].iloc[0] if len(short) else np.nan,
            "yield_near_1y": y364.mean() if len(y364) else np.nan,
            "longest_bond_yield": long["yield_pct"].iloc[0] if len(long) else np.nan,
            "longest_bond_ttm_years": long["ttm_years"].iloc[0] if len(long) else np.nan,
            "yield_near_1y_traded": y364t.mean() if len(y364t) else np.nan,
            "longest_traded_bond_yield": (tlong["yield_pct"].iloc[0]
                                          if len(tlong) else np.nan),
        })
    daily = pd.DataFrame(rows)
    daily["slope_long_minus_1y"] = daily["longest_bond_yield"] - daily["yield_near_1y"]
    return daily


def build_monthly(daily, auctions, policy, inflation):
    d = daily.copy()
    d["month"] = pd.to_datetime(d["report_date"]).dt.to_period("M")
    g = d.groupby("month").agg(
        trading_days=("report_date", "count"),
        avg_yield_near_1y=("yield_near_1y", "mean"),
        avg_longest_bond_yield=("longest_bond_yield", "mean"),
        avg_slope=("slope_long_minus_1y", "mean"),
        volume_total=("volume_total", "sum")).reset_index()

    a = auctions[auctions["is_bill"].eq("TRUE")].copy()
    a["month"] = pd.to_datetime(a["issue_date"]).dt.to_period("M")
    piv = (a.pivot_table(index="month", columns="tenor_days",
                         values="interest_rate_pct", aggfunc="mean")
             .rename(columns={91.0: "auction_91d_pct", 182.0: "auction_182d_pct",
                              364.0: "auction_364d_pct", 56.0: "auction_56d_pct"}))
    keep = [c for c in ["auction_91d_pct", "auction_182d_pct", "auction_364d_pct"]
            if c in piv.columns]
    g = g.merge(piv[keep].reset_index(), on="month", how="left")

    p = policy.dropna(subset=["policy_rate_pct"]).copy()
    p["month"] = pd.to_datetime(p["effective_date"]).dt.to_period("M")
    months = pd.period_range(g["month"].min(), g["month"].max(), freq="M")
    pr = (p.set_index("month")["policy_rate_pct"].groupby(level=0).last()
            .reindex(months).ffill())
    g = g.merge(pr.rename("policy_rate_pct").reset_index()
                  .rename(columns={"index": "month"}), on="month", how="left")

    inf = inflation.copy()
    inf["month"] = pd.to_datetime(inf["date"]).dt.to_period("M")
    g = g.merge(inf.groupby("month")[["value", "cpi_index"]].mean()
                   .rename(columns={"value": "headline_inflation_pct"})
                   .reset_index(), on="month", how="left")

    g["month"] = g["month"].astype(str)
    return g


README = [
    ("Ghana Sovereign Yield Curve dataset, 2023-2026", ""),
    ("", ""),
    ("Purpose",
     "Every input needed to build and analyse the GHS sovereign curve, merged from the GFIM "
     "daily trading reports, the GFIM/CSD security static tables, Bank of Ghana primary "
     "auction results, the MPC policy rate history and headline inflation."),
    ("", ""),
    ("Sheets", ""),
    ("Curve_Inputs",
     "One row per sovereign security per trading day: price, yield, time to maturity, coupon, "
     "volume. This is the sheet you fit curves to. Filter on use_for_fitting = TRUE."),
    ("Security_Master",
     "One row per ISIN: coupon, maturity, first and last quote date, days quoted, days "
     "traded, total volume, and whether a static table covered it."),
    ("Daily_Curve_Stats",
     "One row per trading day: how many securities were quoted and traded, the maturity "
     "range, the shortest bill yield, the yield near one year, the longest bond yield and "
     "the slope between them."),
    ("Monthly_Panel",
     "Month-end panel joining secondary-market yields to auction rates, the policy rate and "
     "inflation. This is the sheet for the macro side of the analysis."),
    ("TBill_Auctions", "Bank of Ghana primary auction results, 2013 to December 2025."),
    ("Policy_Rate", "Every MPC decision from 2002 to September 2026."),
    ("Inflation_Monthly", "Headline year-on-year inflation, long format."),
    ("Summary", "Coverage counts as live formulas."),
    ("", ""),
    ("Method notes", ""),
    ("Bill yields verified",
     "GFIM's reported bill yields are reproduced exactly by ((100/price)-1) x 364/days, so "
     "that convention is used to derive yields for the 2023-2024 reports, where the bill "
     "sheets carried prices only. Check yield_source to see which rows are derived."),
    ("Time to maturity",
     "Recomputed as maturity minus report date. The days-to-maturity field in the source "
     "files is an Excel formula against TODAY(), so it is wrong for historical dates."),
    ("Maturity hierarchy",
     "Report maturity first, then the static table, then the date embedded in the security "
     "description. maturity_conflict = TRUE flags rows where the first and last disagree."),
    ("Coupon hierarchy",
     "Static table first, then the coupon suffix in the security description "
     "(GOG-BD-17/08/27-A6139-1838-10.00 implies 10.00%). Bills are zero-coupon."),
    ("Fitting filter",
     "use_for_fitting excludes rows with no maturity, no yield, a non-positive time to "
     "maturity, under seven days to maturity (annualising a tiny price gap explodes the "
     "yield), or a yield outside 0-100%. exclude_reason says which."),
    ("", ""),
    ("Known gaps", ""),
    ("Sell/buy-back excluded",
     "Repo financing trades are not in this dataset. They are in the GFIM trading workbook "
     "if you want them, but their yields are not market yields."),
    ("Inflation filled to August 2026",
     "The BoG export stopped at April 2023, so May 2023 to August 2026 was compiled from "
     "Ghana Statistical Service CPI releases and GSS figures reported in the press "
     "(compiled 3 October 2026). Every row carries its source, and the CPI index "
     "(2021=100) is filled wherever a release table published it. January to April 2023 "
     "appear in both the BoG export and the GSS tables and agree exactly. September 2026 "
     "was not yet published. Verify against the GSS bulletins before publishing results."),
    ("Auctions stop in December 2025",
     "The uploaded BoG file ends 29 December 2025, so 2026 primary rates are missing."),
    ("Static tables are partial",
     "They cover 71 of the 96 sovereign bond ISINs seen in trading. The description fallback "
     "covers most of the rest; check coupon_source and in_static_table."),
    ("Stale quotes",
     "A security with no volume still carries a closing price from an earlier session. Use "
     "traded = TRUE when you want only genuinely traded levels."),
]


def main():
    os.makedirs(PROCESSED, exist_ok=True)
    static = load_static()
    quotes = build_quotes(static)
    quotes.to_pickle(os.path.join(PROCESSED, "curve_inputs.pkl"))
    master = build_master(quotes, static)
    daily = build_daily(quotes)
    auctions = load_auctions()
    policy = load_policy()
    inflation = load_inflation()
    monthly = build_monthly(daily, auctions, policy, inflation)

    print("quotes", len(quotes), "fit rows", int(quotes["use_for_fitting"].eq("TRUE").sum()))
    print("master", len(master), "daily", len(daily), "monthly", len(monthly))

    with pd.ExcelWriter(OUT, engine="xlsxwriter", datetime_format="yyyy-mm-dd",
                        date_format="yyyy-mm-dd") as xl:
        book = xl.book
        base = {"font_name": "Arial", "font_size": 10}
        f_head = book.add_format({**base, "bold": True, "font_color": "#FFFFFF",
                                  "bg_color": "#1B2A4A", "text_wrap": True,
                                  "valign": "vcenter", "border": 1})
        f_title = book.add_format({**base, "bold": True, "font_size": 14})
        f_sub = book.add_format({**base, "bold": True})
        f_wrap = book.add_format({**base, "text_wrap": True, "valign": "top"})
        f_num2 = book.add_format({**base, "num_format": "#,##0.00"})
        f_num4 = book.add_format({**base, "num_format": "#,##0.0000"})
        f_int = book.add_format({**base, "num_format": "#,##0"})
        f_date = book.add_format({**base, "num_format": "yyyy-mm-dd"})
        f_text = book.add_format(base)

        ws = book.add_worksheet("README")
        ws.set_column(0, 0, 26, f_sub)
        ws.set_column(1, 1, 110, f_wrap)
        ws.write(0, 0, README[0][0], f_title)
        for r, (a, b) in enumerate(README[1:], start=2):
            ws.write(r, 0, a, f_sub)
            ws.write(r, 1, b, f_wrap)

        def dump(name, frame, widths=None):
            frame.to_excel(xl, sheet_name=name, index=False, header=False, startrow=1)
            sh = xl.sheets[name]
            for c, col in enumerate(frame.columns):
                sh.write(0, c, col.replace("_", " ").capitalize(), f_head)
                sample = frame[col]
                if "date" in col or col in ("first_seen", "last_seen"):
                    fmt, w = f_date, 12
                elif sample.dtype.kind in "fi":
                    if col in ("volume", "volume_total", "total_volume", "ttm_days",
                               "num_trades", "quote_days", "traded_days",
                               "n_securities", "n_bills", "n_bonds", "n_traded",
                               "trading_days", "tender", "meeting_no", "tenor_days",
                               "year", "month"):
                        fmt, w = f_int, 13
                    elif "price" in col or col == "ttm_years":
                        fmt, w = f_num4, 13
                    else:
                        fmt, w = f_num2, 13
                else:
                    fmt, w = f_text, 18
                sh.set_column(c, c, (widths or {}).get(col, w), fmt)
            sh.freeze_panes(1, 0)
            sh.autofilter(0, 0, max(len(frame), 1), len(frame.columns) - 1)
            sh.set_row(0, 30)

        dump("Curve_Inputs", quotes, {"security": 34, "isin": 15, "source_file": 36,
                                      "exclude_reason": 20, "yield_source": 26,
                                      "coupon_source": 20, "tenor": 16,
                                      "static_tenor": 14})
        dump("Security_Master", master, {"security": 34, "isin": 15,
                                         "static_source": 24, "coupon_source": 20})
        dump("Daily_Curve_Stats", daily)
        dump("Monthly_Panel", monthly, {"month": 10})
        dump("TBill_Auctions", auctions, {"security_type": 16})
        dump("Policy_Rate", policy, {"mpc_dates": 30})
        dump("Inflation_Monthly", inflation, {"variable": 32})

        s = book.add_worksheet("Summary")
        s.set_column(0, 0, 34, f_text)
        s.set_column(1, 1, 18, f_int)
        s.write(0, 0, "Coverage", f_title)
        n = len(quotes)
        items = [
            ("Quote rows (Curve_Inputs)", f"=COUNTA(Curve_Inputs!$D$2:$D${n+1})"),
            ("Rows usable for fitting", f'=COUNTIF(Curve_Inputs!$AA$2:$AA${n+1},"TRUE")'),
            ("Bill rows", f'=COUNTIF(Curve_Inputs!$B$2:$B${n+1},"BILL")'),
            ("Bond rows", f'=COUNTIF(Curve_Inputs!$B$2:$B${n+1},"BOND")'),
            ("Rows actually traded", f'=COUNTIF(Curve_Inputs!$Z$2:$Z${n+1},"TRUE")'),
            ("Securities (Security_Master)", f"=COUNTA(Security_Master!$A$2:$A${len(master)+1})"),
            ("Trading days", f"=COUNTA(Daily_Curve_Stats!$A$2:$A${len(daily)+1})"),
            ("Months in panel", f"=COUNTA(Monthly_Panel!$A$2:$A${len(monthly)+1})"),
            ("Auction records", f"=COUNTA(TBill_Auctions!$A$2:$A${len(auctions)+1})"),
            ("MPC decisions", f"=COUNTA(Policy_Rate!$C$2:$C${len(policy)+1})"),
            ("Inflation observations", f"=COUNTA(Inflation_Monthly!$A$2:$A${len(inflation)+1})"),
        ]
        for i, (label, formula) in enumerate(items, start=2):
            s.write(i, 0, label, f_text)
            s.write_formula(i, 1, formula, f_int)

    print("written:", OUT)


if __name__ == "__main__":
    main()
