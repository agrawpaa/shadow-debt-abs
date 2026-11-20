"""
Functional wrappers around WaterfallEngine (kept so tests and README examples keep working).

    python3 -m src.models.waterfall_cashflows   # prints base case + sensitivity, writes CSVs
"""
from pathlib import Path

from src.models.waterfall_engine import WaterfallEngine

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"


def run_waterfall(engine: WaterfallEngine, cap_principal_to_cash: bool = True):
    return engine.run_waterfall(cap_principal_to_cash)


def tranche_summary(engine: WaterfallEngine, wf=None, cap_principal_to_cash: bool = True):
    return engine.tranche_summary(wf, cap_principal_to_cash)


def key_outputs(engine: WaterfallEngine, cap_principal_to_cash: bool = True):
    return engine.key_outputs(cap_principal_to_cash)


def sensitivity_grid(multipliers=(1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2),
                     severities=(0.55, 0.60, 0.65, 0.70, 0.75),
                     cap_principal_to_cash: bool = True, **engine_kwargs):
    return WaterfallEngine(**engine_kwargs).sensitivity(list(multipliers), list(severities),
                                                        cap_principal_to_cash)


if __name__ == "__main__":
    engine = WaterfallEngine()
    wf = run_waterfall(engine)
    summary = tranche_summary(engine, wf)
    print("=== Tranche summary (base case) ===\n", summary)
    print("\n=== Class B impairment sensitivity ===\n", sensitivity_grid().round(4))
    wf.to_csv(DATA_DIR / "waterfall_cashflows_base.csv", index=False)
    summary.to_csv(DATA_DIR / "waterfall_tranche_summary_base.csv")
    sensitivity_grid().to_csv(DATA_DIR / "waterfall_sensitivity_class_b.csv")
