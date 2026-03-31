"""
mortgage_math.py — Financial math helpers for Canadian Mortgage Analyzer
Canadian semi-annual compounding (Interest Act).
"""
import math
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta

# ── Payment frequency lookup ──────────────────────────────────────────────────
FREQ = {
    "Monthly":               {"n": 12, "accel": False},
    "Semi-Monthly":          {"n": 24, "accel": False},
    "Bi-Weekly":             {"n": 26, "accel": False},
    "Accelerated Bi-Weekly": {"n": 26, "accel": True},
    "Weekly":                {"n": 52, "accel": False},
    "Accelerated Weekly":    {"n": 52, "accel": True},
}


def periodic_rate(annual_pct: float, n: int) -> float:
    """Canadian semi-annual compounding → periodic rate.
    Two-step: eff = (1 + r/200)^2 ; periodic = eff^(1/n) - 1
    """
    eff = (1 + annual_pct / 200) ** 2
    return eff ** (1 / n) - 1


def calc_pmt(principal: float, annual_pct: float, n: int,
             amort_years: float, accel: bool = False) -> float:
    """Calculate regular payment amount."""
    if annual_pct == 0:
        t = amort_years * n
        return principal / t if t else 0
    r = periodic_rate(annual_pct, n)
    np_ = amort_years * n
    pmt = principal * r * (1 + r) ** np_ / ((1 + r) ** np_ - 1)
    if accel:
        rm = periodic_rate(annual_pct, 12)
        nm = amort_years * 12
        pmt = (principal * rm * (1 + rm) ** nm / ((1 + rm) ** nm - 1)) / (n / 12)
    return pmt


def cmhc_premium(price: float, down: float):
    """Return (premium, HST) or (None, None) if not eligible."""
    dp = down / price * 100
    if dp >= 20:
        return 0.0, 0.0
    if price > 1_500_000 or dp < 5:
        return None, None
    ins = price - down
    rt = 0.04 if dp < 10 else (0.031 if dp < 15 else 0.028)
    p = ins * rt
    return p, p * 0.13


def date_to_period(td, sd, n: int) -> int:
    if isinstance(td, str):
        td = date.fromisoformat(td)
    if isinstance(sd, str):
        sd = date.fromisoformat(sd)
    return max(1, int(round((td - sd).days / 365.25 * n)))


def period_to_date(period: int, sd, n: int) -> date:
    if isinstance(sd, str):
        sd = date.fromisoformat(sd)
    if n == 12:
        return sd + relativedelta(months=int(period - 1))
    if n == 24:
        return sd + relativedelta(days=int((period - 1) * 15))
    if n == 26:
        return sd + relativedelta(weeks=int((period - 1) * 2))
    return sd + relativedelta(weeks=int(period - 1))


def calc_remaining_years(balance: float, rate_pct: float,
                         n: int, payment: float) -> float:
    """Calculate remaining amortization given current balance and payment."""
    if payment <= 0 or balance <= 0:
        return 0.0
    r = periodic_rate(rate_pct, n)
    if r == 0:
        return balance / (payment * n)
    denom = payment - balance * r
    if denom <= 0.01:
        return 999.0
    return math.log(payment / denom) / math.log(1 + r) / n


def _year_of(d) -> int:
    if hasattr(d, "year"):
        return d.year
    try:
        return pd.Timestamp(d).year
    except Exception:
        return 0


def build_amortization(principal: float, annual_pct: float, n: int,
                       amort_years: float, accel: bool = False,
                       start_date=None, extra_payments=None,
                       rate_changes=None, term_periods=None,
                       fixed_pmt: float = 0, fixed_pmt_from: int = 1):
    """Build full amortization schedule DataFrame + summary dict.

    fixed_pmt      – when > 0, used as the payment for all periods >=
                     fixed_pmt_from instead of recalculating at rate renewals.
    fixed_pmt_from – first period that uses fixed_pmt (default 1 = always).
    """
    if start_date is None:
        start_date = date.today().replace(day=1)
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)

    pmt = calc_pmt(principal, annual_pct, n, amort_years, accel)
    if fixed_pmt > 0 and 1 >= fixed_pmt_from:
        pmt = fixed_pmt
    r = periodic_rate(annual_pct, n)

    em = {}
    if extra_payments:
        for ep in extra_payments:
            em[int(ep["period"])] = em.get(int(ep["period"]), 0) + float(ep["amount"])

    rm = {}
    if rate_changes:
        for rc in rate_changes:
            rm[int(rc["period"])] = float(rc["new_rate"])

    rows = []
    bal = float(principal)
    tp = amort_years * n
    cr = float(annual_pct)
    cur_r = r
    ci = cp = cprep = 0.0
    pd_ = start_date

    for i in range(1, int(tp) + 1):
        if bal <= 0.005:
            break
        if term_periods and i > term_periods:
            break
        if i in rm:
            cr = rm[i]
            cur_r = periodic_rate(cr, n)
            # Only recalculate pmt at renewals when NOT using a fixed payment
            if not (fixed_pmt > 0 and i >= fixed_pmt_from):
                pmt = calc_pmt(bal, cr, n, (tp - i + 1) / n, accel)
        # Apply fixed payment starting from fixed_pmt_from (for the first period)
        if fixed_pmt > 0 and i == fixed_pmt_from:
            pmt = fixed_pmt

        int_c = bal * cur_r
        princ = min(max(pmt - int_c, 0), bal)
        extra = min(em.get(i, 0), max(bal - princ, 0))
        bal -= princ + extra
        ci += int_c
        cp += pmt
        cprep += extra

        rows.append({
            "Period": i,
            "Date": pd_,
            "Year": ((i - 1) // n) + 1,
            "CalYear": _year_of(pd_),
            "Payment": round(pmt, 2),
            "Interest": round(int_c, 2),
            "Principal": round(princ, 2),
            "Prepayment": round(extra, 2),
            "Total Paid": round(pmt + extra, 2),
            "Balance": round(max(bal, 0), 2),
            "Rate (%)": round(cr, 3),
            "Cum Interest": round(ci, 2),
            "Cum Principal": round(cp - ci, 2),
            "Cum Prepayment": round(cprep, 2),
        })

        if n == 12:
            pd_ += relativedelta(months=1)
        elif n == 24:
            pd_ += relativedelta(days=15)
        elif n == 26:
            pd_ += relativedelta(weeks=2)
        else:
            pd_ += relativedelta(weeks=1)

    df = pd.DataFrame(rows)
    if df.empty:
        return df, {}

    ti = df["Interest"].sum()
    tprep = df["Prepayment"].sum()
    tt = df["Payment"].sum() + tprep
    last_pmt = float(df["Payment"].iloc[-1])

    return df, {
        "payment": round(last_pmt, 2),
        "total_paid": round(tt, 2),
        "total_interest": round(ti, 2),
        "total_principal": round(df["Principal"].sum(), 2),
        "total_prepaid": round(tprep, 2),
        "end_balance": round(df["Balance"].iloc[-1], 2),
        "payoff_periods": len(df),
        "payoff_years": round(len(df) / n, 2),
        "interest_pct": round(ti / tt * 100, 1) if tt else 0,
    }


def get_today_metrics(df, n: int) -> dict:
    today = date.today()
    if df.empty:
        return {}

    def tod(v):
        return v if isinstance(v, date) else (
            v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
        )

    past = df[df["Date"].apply(tod) <= today]
    if past.empty:
        return {
            "balance_today": float(df.iloc[0]["Balance"]),
            "principal_paid_today": 0.0,
            "interest_paid_today": 0.0,
            "period_today": 0,
            "remaining_periods": len(df),
            "remaining_years": round(len(df) / n, 1),
            "as_of_date": today.strftime("%b %d, %Y"),
        }

    row = past.iloc[-1]
    remaining_periods = len(df) - int(row["Period"])
    remaining_years = round(remaining_periods / n, 1)
    remaining_end = today + relativedelta(
        years=int(remaining_years),
        months=int((remaining_years % 1) * 12)
    )

    return {
        "balance_today": float(row["Balance"]),
        "principal_paid_today": float(row["Cum Principal"]),
        "interest_paid_today": float(row["Cum Interest"]),
        "period_today": int(row["Period"]),
        "remaining_periods": remaining_periods,
        "remaining_years": remaining_years,
        "remaining_end_date": remaining_end.strftime("%b %Y"),
        "as_of_date": tod(row["Date"]).strftime("%b %d, %Y"),
    }


def calc_break_penalty(bal: float, rate: float, mtype: str,
                       orig_p: float, curr_p: float, months_left: int) -> dict:
    mr = periodic_rate(rate, 12)
    tmo = bal * mr * 3
    if mtype == "Variable":
        return {
            "3_months_interest": round(tmo, 2),
            "ird": None,
            "calc_penalty": round(tmo, 2),
            "method": "3 months interest (variable)",
        }
    ird = max(bal * (orig_p - curr_p) / 100 * months_left / 12, 0)
    pen = max(tmo, ird)
    return {
        "3_months_interest": round(tmo, 2),
        "ird": round(ird, 2),
        "calc_penalty": round(pen, 2),
        "method": "IRD" if ird > tmo else "3 months interest",
    }
