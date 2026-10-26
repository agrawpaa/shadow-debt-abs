import pytest

from src.models.waterfall_engine import WaterfallEngine
from src.models.waterfall_cashflows import run_waterfall, tranche_summary


# Expected values come from Waterfall Model - Week 1 Deliverable 1 (Excel base case)

@pytest.fixture(scope="module")
def base():
    engine = WaterfallEngine()
    wf = run_waterfall(engine)
    return engine, wf, tranche_summary(engine, wf)


def test_period_1_payments(base):
    _, wf, _ = base
    p1 = wf.loc[1]
    assert p1["serv_fee"] == pytest.approx(208_333, abs=1)
    assert p1["a_int"] == pytest.approx(1_796_875, abs=1)
    assert p1["b_int"] == pytest.approx(584_375, abs=1)
    assert p1["a_prin"] == pytest.approx(12_963_338, abs=1)
    assert p1["residual"] == pytest.approx(4_663_850, abs=1)
    assert p1["eq_end"] == pytest.approx(37_749_576, abs=1)


def test_equity_wiped_in_period_29(base):
    _, wf, _ = base
    assert wf.loc[28, "eq_end"] == pytest.approx(551_249, abs=1)
    assert wf.loc[29, "eq_loss"] == pytest.approx(551_249, abs=1)
    assert wf.loc[29, "b_loss"] == pytest.approx(113_951, abs=1)
    assert wf.loc[29, "eq_end"] == pytest.approx(0, abs=1)


def test_class_a_retired_in_period_40(base):
    _, wf, _ = base
    assert wf.loc[40, "a_prin"] == pytest.approx(1_772_156, abs=1)
    assert wf.loc[40, "b_prin"] == pytest.approx(4_924_688, abs=1)
    assert wf.loc[40, "b_end"] == pytest.approx(75_157_795, abs=1)


def test_tranche_summary(base):
    _, _, s = base
    assert s.loc["Class A", "principal_repaid"] == pytest.approx(375_000_000, abs=1)
    assert s.loc["Class A", "interest_residual_paid"] == pytest.approx(32_304_168, abs=1)
    assert s.loc["Class B", "principal_written_down"] == pytest.approx(5_919_017, abs=1)
    assert s.loc["Class B", "impairment_pct"] == pytest.approx(0.0696, abs=1e-4)
    assert s.loc["Equity", "impairment_pct"] == pytest.approx(1.0)
    assert s.loc["Equity", "interest_residual_paid"] == pytest.approx(85_650_498, abs=1)


def test_losses_never_exceed_balances(base):
    _, wf, _ = base
    assert (wf[["a_end", "b_end", "eq_end"]] >= -1e-6).all().all()


@pytest.mark.parametrize("mult, sev, expected", [
    (1.20, 0.55, 0.071),
    (1.40, 0.65, 0.266),
    (2.20, 0.75, 0.795),
])
def test_sensitivity_matches_excel(mult, sev, expected):
    s = tranche_summary(WaterfallEngine(default_mult=mult, sev=sev))
    assert s.loc["Class B", "impairment_pct"] == pytest.approx(expected, abs=0.001)
