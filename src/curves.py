"""
Zero-coupon curve construction for the Ghana cedi sovereign market.

Three methods, all producing continuously compounded zero rates in percent:

  bootstrap_curve   piecewise-linear zeros, solved maturity by maturity
  fit_nelson_siegel 4 parameters
  fit_svensson      6 parameters, second hump for the long end

Bills enter as zero-coupon points. Bonds are priced off the curve from their
full cash flow schedule, so the fit is to dirty prices, weighted by inverse
duration so that a price error maps to roughly the same yield error at every
maturity.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares, brentq

import pricing as P

DAY_COUNT = "act/365"
FREQ = 2


# --------------------------------------------------------------- instruments
@dataclass
class Instrument:
    isin: str
    kind: str                 # "BILL" or "BOND"
    ttm: float                # years to maturity
    price: float              # dirty price per 100
    times: np.ndarray         # cash flow times in years
    flows: np.ndarray         # cash flow amounts per 100
    yield_pct: float
    traded: bool = False

    def model_price(self, zero_fn) -> float:
        z = zero_fn(self.times) / 100.0
        return float(np.sum(self.flows * np.exp(-z * self.times)))

    @property
    def weight(self) -> float:
        """Inverse duration: turns price errors into comparable yield errors."""
        return 1.0 / max(self.ttm, 0.1)


def build_instruments(day_rows, settlement: dt.date,
                      traded_only: bool = False) -> list[Instrument]:
    """
    day_rows: DataFrame of Curve_Inputs rows for one report date
              (instrument, isin, coupon_pct, maturity_date, closing_price,
               yield_pct, ttm_years, traded).
    """
    out = []
    for r in day_rows.itertuples():
        if traded_only and getattr(r, "traded", "FALSE") != "TRUE":
            continue
        ttm = r.ttm_years
        if ttm is None or ttm <= 0 or not np.isfinite(ttm):
            continue

        if r.instrument == "BILL":
            price = r.closing_price
            if not price or price <= 0:
                if r.yield_pct is None or not np.isfinite(r.yield_pct):
                    continue
                price = P.bill_price(r.yield_pct, int(round(ttm * 365)))
            out.append(Instrument(r.isin, "BILL", ttm, float(price),
                                  np.array([ttm]), np.array([100.0]),
                                  float(r.yield_pct),
                                  getattr(r, "traded", "FALSE") == "TRUE"))
            continue

        # bond: clean price plus accrued, with its full coupon schedule
        if (r.closing_price is None or not np.isfinite(r.closing_price)
                or r.coupon_pct is None or r.maturity_date is None):
            continue
        dates = P.coupon_dates(settlement, r.maturity_date, FREQ)
        if not dates:
            continue
        times = np.array([P.year_fraction(settlement, d, DAY_COUNT) for d in dates])
        flows = np.full(len(dates), r.coupon_pct / FREQ)
        flows[-1] += 100.0
        ai = P.accrued_interest(settlement, r.maturity_date, r.coupon_pct,
                                FREQ, DAY_COUNT)
        out.append(Instrument(r.isin, "BOND", ttm, float(r.closing_price) + ai,
                              times, flows, float(r.yield_pct),
                              getattr(r, "traded", "FALSE") == "TRUE"))
    return out


# ------------------------------------------------------------ Nelson-Siegel
def ns_zero(t, b0, b1, b2, tau):
    t = np.maximum(np.asarray(t, dtype=float), 1e-6)
    x = t / tau
    decay = (1.0 - np.exp(-x)) / x
    return b0 + b1 * decay + b2 * (decay - np.exp(-x))


def svensson_zero(t, b0, b1, b2, b3, tau1, tau2):
    t = np.maximum(np.asarray(t, dtype=float), 1e-6)
    x1, x2 = t / tau1, t / tau2
    d1 = (1.0 - np.exp(-x1)) / x1
    d2 = (1.0 - np.exp(-x2)) / x2
    return b0 + b1 * d1 + b2 * (d1 - np.exp(-x1)) + b3 * (d2 - np.exp(-x2))


def _fit(instruments, model, x0, bounds):
    def residuals(p):
        fn = lambda t: model(t, *p)
        return np.array([(i.model_price(fn) - i.price) * i.weight
                         for i in instruments])

    sol = least_squares(residuals, x0, bounds=bounds, method="trf",
                        max_nfev=4000, xtol=1e-10, ftol=1e-10)
    fn = lambda t: model(t, *sol.x)
    return sol.x, fn, sol


def fit_nelson_siegel(instruments, x0=None):
    short = np.median([i.yield_pct for i in instruments if i.ttm < 1] or [15.0])
    long = np.median([i.yield_pct for i in instruments if i.ttm > 5] or [short])
    x0 = x0 or [long, short - long, 0.0, 1.5]
    bounds = ([-50, -100, -100, 0.05], [200, 100, 100, 30])
    return _fit(instruments, ns_zero, x0, bounds)


def fit_svensson(instruments, x0=None):
    p_ns, _, _ = fit_nelson_siegel(instruments)
    x0 = x0 or [p_ns[0], p_ns[1], p_ns[2], 0.0, p_ns[3], max(p_ns[3] * 3, 5.0)]
    bounds = ([-50, -100, -100, -100, 0.05, 0.05], [200, 100, 100, 100, 30, 30])
    return _fit(instruments, svensson_zero, x0, bounds)


# --------------------------------------------------------------- bootstrap
def bootstrap_curve(instruments):
    """
    Piecewise-linear zero curve in continuously compounded rates.
    Bills give their zero directly; bonds are solved in maturity order so that
    the model price matches the market price given the curve already built.
    """
    bills = sorted([i for i in instruments if i.kind == "BILL"], key=lambda i: i.ttm)
    bonds = sorted([i for i in instruments if i.kind == "BOND"], key=lambda i: i.ttm)

    knots_t, knots_z = [], []
    for b in bills:
        z = -np.log(b.price / 100.0) / b.ttm * 100.0
        knots_t.append(b.ttm)
        knots_z.append(z)

    def curve(t, kt=None, kz=None):
        kt = knots_t if kt is None else kt
        kz = knots_z if kz is None else kz
        if not kt:
            return np.zeros_like(np.asarray(t, dtype=float))
        return np.interp(np.asarray(t, dtype=float), kt, kz,
                         left=kz[0], right=kz[-1])

    for b in bonds:
        if knots_t and b.ttm <= knots_t[-1]:
            continue                      # keep the curve strictly increasing

        def price_error(z_last):
            kt = knots_t + [b.ttm]
            kz = knots_z + [z_last]
            return b.model_price(lambda t: curve(t, kt, kz)) - b.price

        try:
            z = brentq(price_error, -20.0, 300.0, xtol=1e-8, maxiter=200)
        except ValueError:
            continue
        knots_t.append(b.ttm)
        knots_z.append(z)

    return np.array(knots_t), np.array(knots_z), (lambda t: curve(t))


# ------------------------------------------------------------------ quality
def fit_diagnostics(instruments, zero_fn) -> dict:
    """Price errors and the implied yield errors in basis points."""
    perr, yerr = [], []
    for i in instruments:
        pm = i.model_price(zero_fn)
        perr.append(pm - i.price)
        yerr.append((pm - i.price) * i.weight)
    perr, yerr = np.array(perr), np.array(yerr)
    return {"n": len(instruments),
            "rmse_price": float(np.sqrt(np.mean(perr ** 2))),
            "mae_price": float(np.mean(np.abs(perr))),
            "rmse_yield_bp": float(np.sqrt(np.mean(yerr ** 2)) * 100),
            "max_abs_price": float(np.max(np.abs(perr))) if len(perr) else np.nan}


def forward_rate(zero_fn, t1: float, t2: float) -> float:
    """Continuously compounded forward rate (%) between two maturities."""
    z1, z2 = float(zero_fn(t1)), float(zero_fn(t2))
    return (z2 * t2 - z1 * t1) / (t2 - t1)


GRID = np.array([0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 12.0, 15.0])
