import pandas as pd


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
            eq_share: float = 0.08
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
# Sensitivity Matrix. What if?
# --------------------------------------------------------------------------

# place holder 
def run_waterfall(self):
    return None

def class_b_impairment(self):

    df = self.run_waterfall()

    total = df["b_wd"].sum()

    return total / (self.balance * self.b_share)

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
            )

    return engine

def sensitivity(self, mults=None, sevs=None):
    if mults is None: 
        mults = [1.0, 1.2, 1.4, 1.6, 1.8, 2.00, 2.2]
    if sevs is None: 
        sevs = [0.55, 0.60, 0.65, 0.70, 0.75]

    rows = []

    for m in mults: 
        row = []
        for s in sevs:
            scenario = self.with_params(m, s)
            row.append(scenario.class_b_impairment())
        rows.append(row)

    return pd.DataFrame(rows, index=mults, columns=sevs)

    