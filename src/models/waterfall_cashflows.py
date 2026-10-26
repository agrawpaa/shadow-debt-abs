"""Waterfall cash flows: priority of payments, loss allocation and tranche results for the ABS pool.

Python port of "Waterfall Model - Week 1 Deliverable 1" (Waterfall_Cashflows, Tranche_Summary and
Sensitivity sheets). Pool amortization comes from WaterfallEngine.build_pool_schedule().

Monthly priority of payments (cash flows down):
    1. Servicing fee       = pool beginning balance * fee / 12
    2. Class A interest    = A beginning balance * A coupon / 12
    3. Class B interest    = B beginning balance * B coupon / 12
    4. Principal           = scheduled + prepaid principal, sequential: A until retired, then B
    5. Residual            = cash left after steps 1-4, released to Equity (excess spread)

Loss allocation (losses flow up, reverse seniority):
    net loss = defaults * SEV, written down Equity -> Class B -> Class A

Impairment % = cumulative principal written down / original tranche balance.

Known differences from the Excel (see cap_principal_to_cash):
  * In months 46-48 at base case the Excel pays more Class B principal than the cash collected.
    By default principal is capped at available cash; cap_principal_to_cash=False reproduces the Excel.
  * Class B can finish with an unpaid balance (base case ~$24.7M) that impairment % does not count,
    because recoveries and excess spread go to Equity rather than to Class B principal.
"""
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from src.models.waterfall_engine import WaterfallEngine

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "data" / "processed"

# Sensitivity axes from the Excel Sensitivity sheet
DEFAULT_MULTIPLIERS = (1.00, 1.20, 1.40, 1.60, 1.80, 2.00, 2.20)
DEFAULT_SEVERITIES = (0.55, 0.60, 0.65, 0.70, 0.75)

TRANCHES = {
    # name: (share attribute, principal column, loss column, interest/residual column, ending column)
    "Class A": ("a_share", "a_prin", "a_loss", "a_int", "a_end"),
    "Class B": ("b_share", "b_prin", "b_loss", "b_int", "b_end"),
    "Equity": ("eq_share", None, "eq_loss", "residual", "eq_end"),
}


def _validate(engine: WaterfallEngine) -> None:
    total_share = engine.a_share + engine.b_share + engine.eq_share
    if abs(total_share - 1.0) > 1e-9:
        raise ValueError(f"Tranche shares must sum to 1.0, got {total_share:.4f}")
    if not 0.0 <= engine.sev <= 1.0:
        raise ValueError(f"Loss severity must be between 0 and 1, got {engine.sev}")
    if engine.cdr_stressed >= 1.0:
        raise ValueError(f"Stressed CDR must be below 100%, got {engine.cdr_stressed:.2%}")


# --------------------------------------------------------------------------
# Priority of payments  (Waterfall_Cashflows sheet, tranche columns)
# --------------------------------------------------------------------------

def run_waterfall(engine: WaterfallEngine, cap_principal_to_cash: bool = True) -> pd.DataFrame:
    """
    Runs the monthly priority of payments on the engine's pool schedule.

    Parameters
    ----------
    engine : WaterfallEngine
        Pool and capital structure assumptions.
    cap_principal_to_cash : bool, default True
        Limit principal paid to cash remaining after fees and interest. False reproduces the Excel,
        which pays full principal collections even when cash runs short.

    Returns
    -------
    pd.DataFrame
        49 rows (periods 0-48): beginning/ending balances, fees, interest, principal,
        write-downs and residual for each tranche.
    """
    _validate(engine)
    pool = engine.build_pool_schedule()

    rows = [{
        "period": 0,
        "a_beg": 0.0, "b_beg": 0.0, "eq_beg": 0.0,
        "serv_fee": 0.0, "cash_after_fees": 0.0,
        "a_int": 0.0, "cash_after_a_int": 0.0, "b_int": 0.0, "cash_after_b_int": 0.0,
        "avail_prin": 0.0, "a_prin": 0.0, "b_prin": 0.0,
        "eq_loss": 0.0, "b_loss": 0.0, "a_loss": 0.0,
        "residual": 0.0,
        "a_end": engine.balance * engine.a_share,
        "b_end": engine.balance * engine.b_share,
        "eq_end": engine.balance * engine.eq_share,
    }]

    for t in range(1, engine.wam + 1):
        p = pool.loc[t]
        a_beg, b_beg, eq_beg = rows[t-1]["a_end"], rows[t-1]["b_end"], rows[t-1]["eq_end"]

        # 1-3. Fees and interest, each paid only from cash still available
        serv_fee = min(p["beg_bal"] * engine.servicing_fee / 12, p["total_cash"])
        cash_after_fees = p["total_cash"] - serv_fee

        a_int = min(a_beg * engine.a_coupon / 12, cash_after_fees)
        cash_after_a_int = cash_after_fees - a_int

        b_int = min(b_beg * engine.b_coupon / 12, cash_after_a_int)
        cash_after_b_int = cash_after_a_int - b_int

        # 4. Sequential principal
        avail_prin = min(p["total_prin"], cash_after_b_int) if cap_principal_to_cash else p["total_prin"]
        a_prin = min(avail_prin, a_beg)
        b_prin = min(avail_prin - a_prin, b_beg)

        # Reverse-seniority write-downs
        loss = p["net_loss"]
        eq_loss = min(loss, eq_beg)
        b_loss = min(loss - eq_loss, b_beg - b_prin)
        a_loss = min(loss - eq_loss - b_loss, a_beg - a_prin)

        # 5. Residual to equity
        residual = max(0.0, cash_after_b_int - a_prin - b_prin)

        rows.append({
            "period": t,
            "a_beg": a_beg, "b_beg": b_beg, "eq_beg": eq_beg,
            "serv_fee": serv_fee, "cash_after_fees": cash_after_fees,
            "a_int": a_int, "cash_after_a_int": cash_after_a_int,
            "b_int": b_int, "cash_after_b_int": cash_after_b_int,
            "avail_prin": avail_prin, "a_prin": a_prin, "b_prin": b_prin,
            "eq_loss": eq_loss, "b_loss": b_loss, "a_loss": a_loss,
            "residual": residual,
            "a_end": a_beg - a_prin - a_loss,
            "b_end": b_beg - b_prin - b_loss,
            "eq_end": eq_beg - eq_loss,
        })

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Tranche summary  (Tranche_Summary table)
# --------------------------------------------------------------------------

def tranche_summary(engine: WaterfallEngine, wf: Optional[pd.DataFrame] = None,
                    cap_principal_to_cash: bool = True) -> pd.DataFrame:
    """Original balance, principal repaid / written down, interest or residual paid,
    ending balance and impairment % for each tranche."""

    if wf is None:
        wf = run_waterfall(engine, cap_principal_to_cash)

    rows = []
    for name, (share_attr, prin_col, loss_col, cash_col, end_col) in TRANCHES.items():
        original = engine.balance * getattr(engine, share_attr)
        written_down = wf[loss_col].sum()
        rows.append({
            "tranche": name,
            "original_balance": original,
            "principal_repaid": wf[prin_col].sum() if prin_col else 0.0,
            "principal_written_down": written_down,
            "interest_residual_paid": wf[cash_col].sum(),
            "ending_balance": wf[end_col].iloc[-1],
            "impairment_pct": written_down / original,
        })

    return pd.DataFrame(rows).set_index("tranche")


def key_outputs(engine: WaterfallEngine, cap_principal_to_cash: bool = True) -> dict:
    """Headline results (Sensitivity sheet "Current Test Outputs" block)."""

    wf = run_waterfall(engine, cap_principal_to_cash)
    summary = tranche_summary(engine, wf)
    return {
        "class_b_impairment": float(summary.loc["Class B", "impairment_pct"]),
        "equity_impairment": float(summary.loc["Equity", "impairment_pct"]),
        "class_b_ending_balance": float(summary.loc["Class B", "ending_balance"]),
        "pool_ending_balance": float(engine.pool["end_bal"].iloc[-1]),
        "equity_wipeout_period": _first_period(wf, wf["eq_end"] <= 1e-6),
        "class_a_payoff_period": _first_period(wf, wf["a_end"] <= 1e-6),
    }


def _first_period(wf: pd.DataFrame, mask: pd.Series) -> Optional[int]:
    hits = wf.loc[mask & (wf["period"] > 0), "period"]
    return int(hits.iloc[0]) if not hits.empty else None


# --------------------------------------------------------------------------
# Sensitivity  (Class B impairment grid, Sensitivity sheet)
# --------------------------------------------------------------------------

def sensitivity_grid(multipliers: Sequence[float] = DEFAULT_MULTIPLIERS,
                     severities: Sequence[float] = DEFAULT_SEVERITIES,
                     tranche: str = "Class B",
                     cap_principal_to_cash: bool = True,
                     **engine_kwargs) -> pd.DataFrame:
    """Impairment % for one tranche across default multiplier (rows) x loss severity (columns).
    Any other WaterfallEngine input can be overridden through engine_kwargs."""

    grid = {
        sev: [
            tranche_summary(WaterfallEngine(default_mult=m, sev=sev, **engine_kwargs),
                            cap_principal_to_cash=cap_principal_to_cash).loc[tranche, "impairment_pct"]
            for m in multipliers
        ]
        for sev in severities
    }
    out = pd.DataFrame(grid, index=list(multipliers))
    out.index.name = "default_mult"
    out.columns.name = "sev"
    return out


if __name__ == "__main__":
    base = WaterfallEngine()
    waterfall = run_waterfall(base)
    summary = tranche_summary(base, waterfall)
    outputs = key_outputs(base)
    grid = sensitivity_grid()

    print("=" * 60)
    print("BASE CASE (CDR 8.00%, SEV 65%, M 1.00x)")
    print(summary.to_string(formatters={
        col: ("{:.2%}".format if col == "impairment_pct" else "${:,.0f}".format) for col in summary.columns
    }))
    print("-" * 60)
    print(f"Class B Impairment:      {outputs['class_b_impairment']:.2%}")
    print(f"Equity Impairment:       {outputs['equity_impairment']:.2%}")
    print(f"Class B Ending Balance:  ${outputs['class_b_ending_balance']:,.0f}")
    print(f"Equity wiped out:        month {outputs['equity_wipeout_period']}")
    print(f"Class A paid off:        month {outputs['class_a_payoff_period']}")
    print("=" * 60)
    print("CLASS B IMPAIRMENT SENSITIVITY (rows: default multiplier, cols: severity)")
    print((grid * 100).round(1).to_string())

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    waterfall.round(2).to_csv(OUTPUT_DIR / "waterfall_cashflows_base.csv", index=False)
    summary.round(6).to_csv(OUTPUT_DIR / "waterfall_tranche_summary_base.csv")
    grid.round(6).to_csv(OUTPUT_DIR / "waterfall_sensitivity_class_b.csv")
    print(f"\n[SUCCESS] Outputs exported to: {OUTPUT_DIR}")
