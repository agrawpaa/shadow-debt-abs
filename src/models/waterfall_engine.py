import numpy as np
import pandas as pd
from typing import Optional
from scipy.optimize import brentq

TRANCHES = {
    # name: (share attribute, principal column, loss column, interest/residual column, ending column)
    "Class A": ("a_share", "a_prin", "a_loss", "a_int", "a_end"),
    "Class B": ("b_share", "b_prin", "b_loss", "b_int", "b_end"),
    "Equity": ("eq_share", None, "eq_loss", "residual", "eq_end"),
}



class WaterfallEngine:
    """
    Cash flow waterfall engine for a consumer loan ABS pool.

    Projects monthly collateral amortization (scheduled principal,
    prepayments, defaults, recoveries) and allocates the cash
    sequentially through a three-tranche capital structure, with
    net losses written down in reverse order of seniority.

    Parameters
    ----------

    balance : float, default 500_000_000
        Initial collateral pool balance in dollars
    wam : int, default 48
        Weighted average maturity in months 
    wac : float, default 0.145
        Gross weighted average coupon, as an annual decimal
    cpr : float, default 0.12 
        Conditional prepay rate annual decimal
    cdr : float, default 0.08 
        Conditional default (baseline) rate annual decimal
    sev : float, default 0.65 
        Loss severity, the share of default that permanently lost (1 - sev) recovery rate
    servicing_fee : float, default 0.005 
        The fee for the service on the begining balance annual decimal
    default_mult : float, default 1.0
        Multiplier for stressed cdr
    a_share, b_share, eq_share : float
        The proportion for each type (must add up to 1)
    a_coupon, b_coupon : float
        The interest rate for A and B
    recoveries_to_principal : bool, default False
        False reproduces the Excel: recoveries on defaulted loans are collected as cash but are
        not paid as note principal, so they fall through to the Equity residual and the notes end
        up under-collateralized by cumulative recoveries. True adds recoveries to principal
        available for Class A / Class B (standard ABS treatment).
    """

    def __init__(
            self, 
            balance: float = 500_000_000, 
            wam: int = 48, 
            wac: float = 0.1450, 
            cpr: float = 0.12, 
            cdr: float = 0.08, 
            sev: float = 0.65, 
            servicing_fee: float = 0.005, 
            default_mult: float = 1.00, 
            a_share: float = 0.75, 
            a_coupon: float = 0.0575, 
            b_share: float = 0.17,
            b_coupon: float = 0.0825, 
            eq_share: float = 0.08,
            recoveries_to_principal: bool = False
            ):
        self.balance=balance
        self.wam=wam
        self.wac=wac
        self.cpr=cpr
        self.cdr=cdr
        self.sev=sev
        self.servicing_fee=servicing_fee
        self.default_mult=default_mult
        self.a_share=a_share          
        self.a_coupon=a_coupon
        self.b_share=b_share
        self.b_coupon=b_coupon
        self.eq_share=eq_share
        self.recoveries_to_principal=recoveries_to_principal

    def _validate(self):
        """Reject inputs that would produce a meaningless deal."""
        if abs(self.a_share + self.b_share + self.eq_share - 1.0) > 1e-9:
            raise ValueError("a_share + b_share + eq_share must equal 1")
        if not 0.0 <= self.sev <= 1.0:
            raise ValueError("sev must be between 0 and 1")
        if not 0.0 <= self.cdr_stressed < 1.0:
            raise ValueError("stressed CDR (cdr * default_mult) must be in [0, 1)")
        if not 0.0 <= self.cpr < 1.0:
            raise ValueError("cpr must be in [0, 1)")

    @property
    def cdr_stressed(self) -> float:
        return self.cdr * self.default_mult

    @property
    def smm(self) -> float:
        return 1 - (1 - self.cpr)**(1/12)

    @property
    def mdr(self) -> float:
        return 1 - (1 - self.cdr_stressed)**(1/12)

# --------------------------------------------------------------------------
# Waterfall
# --------------------------------------------------------------------------

    def build_pool_schedule(self):
        """Returns a 49-row DataFrame for periods 0-48"""

        self._validate()
        r = self.wac / 12
        smm = self.smm
        mdr = self.mdr  

        pool_schedule = []

        pool_schedule.append({
            "period": 0, 
            "beg_bal": 0,    
            "interest": 0, 
            "sched_prin": 0, 
            "prepay": 0, 
            "defaults": 0, 
            "net_loss": 0, 
            "recoveries": 0, 
            "total_prin": 0, 
            "total_cash": 0, 
            "end_bal": self.balance
        })

        for t in range(1, self.wam + 1):

            beg_bal = pool_schedule[t-1]["end_bal"]
            interest = beg_bal * r

            if beg_bal <= 0: 
                sched_prin = 0
            else:
                sched_prin = min(beg_bal, (r * beg_bal / (1 - (1 + r)**(-(self.wam - t + 1))) - interest))

            prepay = max(0, ((beg_bal - sched_prin) * smm))
            defaults = beg_bal * mdr
            net_loss = defaults * self.sev
            recovery = defaults * (1 - self.sev)
            total_prin = sched_prin + prepay
            total_cash = interest + sched_prin + prepay + recovery
            end_bal = max(0, beg_bal - sched_prin - prepay - defaults)

            pool_schedule.append({
                "period": t, 
                "beg_bal": beg_bal,    
                "interest": interest, 
                "sched_prin": sched_prin, 
                "prepay": prepay, 
                "defaults": defaults, 
                "net_loss": net_loss, 
                "recoveries": recovery, 
                "total_prin": total_prin, 
                "total_cash": total_cash, 
                "end_bal": end_bal
            })

        self.pool = pd.DataFrame(pool_schedule)
        
        return self.pool


# --------------------------------------------------------------------------
# Priority of payments  (Waterfall_Cashflows sheet, tranche columns)
# --------------------------------------------------------------------------

    def run_waterfall(self, cap_principal_to_cash: bool = False) -> pd.DataFrame:
        """
        Runs the monthly priority of payments on the engine's pool schedule.

        Parameters
        ----------
        cap_principal_to_cash : bool, default False
            Limit principal paid to cash remaining after fees and interest. False reproduces the Excel,
            which pays full principal collections even when cash runs short.

        Returns
        -------
        pd.DataFrame
            49 rows (periods 0-48): beginning/ending balances, fees, interest, principal,
            write-downs and residual for each tranche.
        """

        pool = self.build_pool_schedule()

        rows = [{
            "period": 0,
            "a_beg": 0.0, "b_beg": 0.0, "eq_beg": 0.0,
            "serv_fee": 0.0, "cash_after_fees": 0.0,
            "a_int": 0.0, "cash_after_a_int": 0.0, "b_int": 0.0, "cash_after_b_int": 0.0,
            "avail_prin": 0.0, "a_prin": 0.0, "b_prin": 0.0,
            "eq_loss": 0.0, "b_loss": 0.0, "a_loss": 0.0,
            "residual": 0.0,
            "a_end": self.balance * self.a_share,
            "b_end": self.balance * self.b_share,
            "eq_end": self.balance * self.eq_share,
        }]

        for t in range(1, self.wam + 1):
            p = pool.loc[t]
            a_beg, b_beg, eq_beg = rows[t-1]["a_end"], rows[t-1]["b_end"], rows[t-1]["eq_end"]

            # 1-3. Fees and interest, each paid only from cash still available
            serv_fee = min(p["beg_bal"] * self.servicing_fee / 12, p["total_cash"])
            cash_after_fees = p["total_cash"] - serv_fee

            a_int = min(a_beg * self.a_coupon / 12, cash_after_fees)
            cash_after_a_int = cash_after_fees - a_int

            b_int = min(b_beg * self.b_coupon / 12, cash_after_a_int)
            cash_after_b_int = cash_after_a_int - b_int

            # 4. Sequential principal (optionally including recoveries on defaulted loans)
            prin_collected = p["total_prin"] + (p["recoveries"] if self.recoveries_to_principal else 0.0)
            avail_prin = min(prin_collected, cash_after_b_int) if cap_principal_to_cash else prin_collected
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

    def tranche_summary(self, wf: Optional[pd.DataFrame] = None,
                        cap_principal_to_cash: bool = False) -> pd.DataFrame:
        """Original balance, principal repaid / written down, interest or residual paid,
        ending balance and impairment % for each tranche."""

        if wf is None:
            wf = self.run_waterfall(cap_principal_to_cash)

        rows = []
        for name, (share_attr, prin_col, loss_col, cash_col, end_col) in TRANCHES.items():
            original = self.balance * getattr(self, share_attr)
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


    def key_outputs(self, cap_principal_to_cash: bool = True) -> dict:
        """Headline results (Sensitivity sheet "Current Test Outputs" block)."""

        wf = self.run_waterfall(cap_principal_to_cash)
        summary = self.tranche_summary(wf)
        return {
            "class_b_impairment": float(summary.loc["Class B", "impairment_pct"]),
            "equity_impairment": float(summary.loc["Equity", "impairment_pct"]),
            "class_b_ending_balance": float(summary.loc["Class B", "ending_balance"]),
            "pool_ending_balance": float(self.pool["end_bal"].iloc[-1]),
            "equity_wipeout_period": _first_period(wf, wf["eq_end"] <= 1e-6),
            "class_a_payoff_period": _first_period(wf, wf["a_end"] <= 1e-6),
        }


# --------------------------------------------------------------------------
# Sensitivity Matrix. What if?
# --------------------------------------------------------------------------

    def with_params(self, default_mult, sev):

        engine = WaterfallEngine(
                balance=self.balance,
                wam=self.wam,
                wac=self.wac,
                cpr=self.cpr,
                cdr=self.cdr,
                sev=sev,
                servicing_fee=self.servicing_fee,
                default_mult=default_mult,
                a_share=self.a_share, 
                a_coupon=self.a_coupon,
                b_share=self.b_share,
                b_coupon=self.b_coupon,
                eq_share=self.eq_share,
                recoveries_to_principal=self.recoveries_to_principal,
                )

        return engine

    def sensitivity(self, mults=None, sevs=None, cap_principal_to_cash=False):

        if mults is None: 
            mults = [1.0, 1.2, 1.4, 1.6, 1.8, 2.00, 2.2]
        if sevs is None: 
            sevs = [0.55, 0.60, 0.65, 0.70, 0.75]

        rows = []

        for m in mults: 
            row = []
            for s in sevs:
                scenario = self.with_params(m, s)
                outputs = scenario.key_outputs(cap_principal_to_cash=cap_principal_to_cash)
                row.append(outputs["class_b_impairment"])
            rows.append(row)

        return pd.DataFrame(rows, index=mults, columns=sevs)


# --------------------------------------------------------------------------
# Returns: IRR, WAL, total loss per tranche
# --------------------------------------------------------------------------

def _tranche_returns(engine: "WaterfallEngine", wf: Optional[pd.DataFrame] = None,
                     cap_principal_to_cash: bool = True) -> pd.DataFrame:
    """
    Realized IRR, WAL and loss for each tranche, assuming each is bought at par at period 0.

    IRR: monthly rate i solving  -Face + sum_t CF_t / (1+i)^t = 0, annualized as (1+i)^12 - 1.
    WAL: sum_t (t * principal_t) / sum_t principal_t, in years (only principal actually received).
    Total loss counts both write-downs and any balance still unpaid after month 48.
    """
    if wf is None:
        wf = engine.run_waterfall(cap_principal_to_cash)

    rows = []
    for name, (share_attr, prin_col, loss_col, cash_col, end_col) in TRANCHES.items():
        face = engine.balance * getattr(engine, share_attr)               # original balance
        prin = wf[prin_col].to_numpy() if prin_col else np.zeros(len(wf))  # principal received
        cash = wf[cash_col].to_numpy() + prin                              # interest/residual + principal
        flows = cash.copy()
        flows[0] = -face                                                   # buy at par at t = 0
        periods = wf["period"].to_numpy()
        written_down = wf[loss_col].sum()                                  # reverse-seniority write-downs
        unpaid = wf[end_col].iloc[-1]                                      # balance left after month 48
        rows.append({
            "tranche": name,
            "original_balance": face,
            "irr_annual": _irr_annual(flows),
            "wal_years": (periods * prin).sum() / prin.sum() / 12 if prin.sum() > 0 else np.nan,
            "principal_written_down": written_down,
            "unpaid_at_maturity": unpaid,
            "total_principal_loss_pct": (written_down + unpaid) / face,
            "money_good": (written_down + unpaid) < 1.0,
            # month a write-down (not a repayment) drives the balance to zero
            "wipeout_period": _first_period(wf, (wf[end_col] <= 1e-6) & (wf[loss_col] > 1.0)),
        })
    return pd.DataFrame(rows).set_index("tranche")


def _irr_annual(flows) -> float:
    """Annualized IRR of monthly cash flows (BA II Plus CF worksheet, then (1+i)^12 - 1)."""
    flows = np.asarray(flows, dtype=float)
    t = np.arange(len(flows))
    if flows[1:].sum() <= 0:
        return -1.0
    npv = lambda i: (flows / (1 + i) ** t).sum()
    return (1 + brentq(npv, -0.99, 1.0)) ** 12 - 1


WaterfallEngine.tranche_returns = _tranche_returns


def _first_period(wf: pd.DataFrame, mask: pd.Series) -> Optional[int]:
    hits = wf.loc[mask & (wf["period"] > 0), "period"]
    return int(hits.iloc[0]) if not hits.empty else None

if __name__ == "__main__":
    engine = WaterfallEngine()

    print("=== Tranche summary (base case) ===")
    print(engine.tranche_summary(cap_principal_to_cash=False))

    print("\n=== Key outputs (base case) ===")
    for key, value in engine.key_outputs(cap_principal_to_cash=False).items():
        print(f"{key}: {value}")

    print("\n=== Class B impairment sensitivity ===")
    print(engine.sensitivity(cap_principal_to_cash=False).round(4))