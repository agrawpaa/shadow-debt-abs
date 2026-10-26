import pandas as pd

from src.models.waterfall_engine import WaterfallEngine


# --------------------------------------------------------------------------
# Priority of payments  (Waterfall_Cashflows sheet, tranche columns)
# --------------------------------------------------------------------------

def run_waterfall(engine: WaterfallEngine) -> pd.DataFrame:
    """
    Runs the monthly priority of payments on the engine's pool schedule.

    Order each month:
        1. Servicing fee (on beginning pool balance)
        2. Class A interest
        3. Class B interest
        4. Principal, sequential: Class A until retired, then Class B
        5. Net losses written down in reverse seniority: Equity -> B -> A
        6. Whatever cash is left goes to Equity as residual / excess spread

    Returns a 49-row DataFrame for periods 0-48.
    """

    pool = engine.build_pool_schedule()

    a_bal = engine.balance * engine.a_share
    b_bal = engine.balance * engine.b_share
    eq_bal = engine.balance * engine.eq_share

    rows = [{
        "period": 0,
        "a_beg": 0, "b_beg": 0, "eq_beg": 0,
        "serv_fee": 0, "cash_after_fees": 0,
        "a_int": 0, "cash_after_a_int": 0, "b_int": 0,
        "avail_prin": 0, "a_prin": 0, "b_prin": 0,
        "eq_loss": 0, "b_loss": 0, "a_loss": 0,
        "residual": 0,
        "a_end": a_bal, "b_end": b_bal, "eq_end": eq_bal,
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
        avail_prin = min(p["total_prin"], cash_after_b_int)
        a_prin = min(avail_prin, a_beg)
        b_prin = min(avail_prin - a_prin, b_beg)

        # 5. Reverse-seniority write-downs
        loss = p["net_loss"]
        eq_loss = min(loss, eq_beg)
        b_loss = min(loss - eq_loss, b_beg - b_prin)
        a_loss = min(loss - eq_loss - b_loss, a_beg - a_prin)

        # 6. Residual to equity
        residual = cash_after_b_int - a_prin - b_prin

        rows.append({
            "period": t,
            "a_beg": a_beg, "b_beg": b_beg, "eq_beg": eq_beg,
            "serv_fee": serv_fee, "cash_after_fees": cash_after_fees,
            "a_int": a_int, "cash_after_a_int": cash_after_a_int, "b_int": b_int,
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

def tranche_summary(engine: WaterfallEngine, wf: pd.DataFrame = None) -> pd.DataFrame:
    """Principal repaid, written down, interest/residual paid and impairment % per tranche."""

    if wf is None:
        wf = run_waterfall(engine)

    tranches = {
        "Class A": (engine.a_share, "a_prin", "a_loss", "a_int"),
        "Class B": (engine.b_share, "b_prin", "b_loss", "b_int"),
        "Equity": (engine.eq_share, None, "eq_loss", "residual"),
    }

    rows = []
    for name, (share, prin_col, loss_col, cash_col) in tranches.items():
        original = engine.balance * share
        written_down = wf[loss_col].sum()
        rows.append({
            "tranche": name,
            "original_balance": original,
            "principal_repaid": wf[prin_col].sum() if prin_col else 0,
            "principal_written_down": written_down,
            "interest_residual_paid": wf[cash_col].sum(),
            "impairment_pct": written_down / original,
        })

    return pd.DataFrame(rows).set_index("tranche")
