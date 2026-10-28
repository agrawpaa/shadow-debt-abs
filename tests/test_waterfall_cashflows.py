"""Tie-out tests: Python waterfall vs "Waterfall Model - Week 1 Deliverable 1" (Excel base case)."""
import pytest

from src.models.waterfall_engine import WaterfallEngine
from src.models.waterfall_cashflows import key_outputs, run_waterfall, sensitivity_grid, tranche_summary

# Excel Sensitivity sheet: Class B impairment %, rows = default multiplier, columns = severity
EXCEL_GRID = {
    1.00: (0.000, 0.028, 0.070, 0.111, 0.153),
    1.20: (0.071, 0.120, 0.169, 0.218, 0.267),
    1.40: (0.153, 0.209, 0.266, 0.322, 0.379),
    1.60: (0.232, 0.296, 0.360, 0.424, 0.488),
    1.80: (0.309, 0.380, 0.451, 0.522, 0.593),
    2.00: (0.385, 0.462, 0.540, 0.618, 0.696),
    2.20: (0.458, 0.542, 0.627, 0.711, 0.795),
}
EXCEL_SEVERITIES = (0.55, 0.60, 0.65, 0.70, 0.75)


@pytest.fixture(scope="module")
def base():
    engine = WaterfallEngine()
    wf = run_waterfall(engine)
    return engine, wf, tranche_summary(engine, wf)


@pytest.fixture(scope="module")
def excel_exact():
    engine = WaterfallEngine()
    wf = run_waterfall(engine, cap_principal_to_cash=False)
    return engine, wf, tranche_summary(engine, wf)


# --------------------------------------------------------------------------
# Monthly cash flows
# --------------------------------------------------------------------------

def test_period_1_payments(base):
    _, wf, _ = base
    p1 = wf.loc[1]
    assert p1["serv_fee"] == pytest.approx(208_333, abs=1)
    assert p1["cash_after_fees"] == pytest.approx(20_008_438, abs=1)
    assert p1["a_int"] == pytest.approx(1_796_875, abs=1)
    assert p1["cash_after_a_int"] == pytest.approx(18_211_563, abs=1)
    assert p1["b_int"] == pytest.approx(584_375, abs=1)
    assert p1["a_prin"] == pytest.approx(12_963_338, abs=1)
    assert p1["eq_loss"] == pytest.approx(2_250_424, abs=1)
    assert p1["residual"] == pytest.approx(4_663_850, abs=1)
    assert p1["a_end"] == pytest.approx(362_036_662, abs=1)
    assert p1["eq_end"] == pytest.approx(37_749_576, abs=1)


def test_equity_wiped_in_period_29(base):
    _, wf, _ = base
    assert wf.loc[28, "eq_end"] == pytest.approx(551_249, abs=1)
    assert wf.loc[29, "eq_loss"] == pytest.approx(551_249, abs=1)
    assert wf.loc[29, "b_loss"] == pytest.approx(113_951, abs=1)
    assert wf.loc[29, "b_end"] == pytest.approx(84_886_049, abs=1)
    assert wf.loc[30, "b_int"] == pytest.approx(583_592, abs=1)


def test_class_a_retired_in_period_40(base):
    _, wf, _ = base
    assert wf.loc[40, "a_int"] == pytest.approx(8_492, abs=1)
    assert wf.loc[40, "a_prin"] == pytest.approx(1_772_156, abs=1)
    assert wf.loc[40, "b_prin"] == pytest.approx(4_924_688, abs=1)
    assert wf.loc[40, "b_end"] == pytest.approx(75_157_795, abs=1)


def test_excel_exact_mode_ties_to_final_period(excel_exact):
    _, wf, _ = excel_exact
    assert wf.loc[48, "b_int"] == pytest.approx(209_528, abs=1)
    assert wf.loc[48, "b_prin"] == pytest.approx(5_765_217, abs=1)
    assert wf.loc[48, "b_end"] == pytest.approx(24_685_704, abs=1)


def test_capped_principal_never_exceeds_cash(base):
    _, wf, _ = base
    paid = wf["serv_fee"] + wf["a_int"] + wf["b_int"] + wf["a_prin"] + wf["b_prin"] + wf["residual"]
    cash_in = wf["cash_after_fees"] + wf["serv_fee"]
    assert (paid <= cash_in + 1e-6).all()


def test_balances_never_negative(base):
    _, wf, _ = base
    assert (wf[["a_end", "b_end", "eq_end"]] >= -1e-6).all().all()


# --------------------------------------------------------------------------
# Tranche summary
# --------------------------------------------------------------------------

def test_tranche_summary_matches_excel(excel_exact):
    _, _, s = excel_exact
    assert s.loc["Class A", "principal_repaid"] == pytest.approx(375_000_000, abs=1)
    assert s.loc["Class A", "principal_written_down"] == pytest.approx(0, abs=1)
    assert s.loc["Class A", "interest_residual_paid"] == pytest.approx(32_304_168, abs=1)
    assert s.loc["Class B", "principal_repaid"] == pytest.approx(54_395_279, abs=1)
    assert s.loc["Class B", "principal_written_down"] == pytest.approx(5_919_017, abs=1)
    assert s.loc["Class B", "interest_residual_paid"] == pytest.approx(26_051_355, abs=1)
    assert s.loc["Class B", "ending_balance"] == pytest.approx(24_685_704, abs=1)
    assert s.loc["Equity", "principal_written_down"] == pytest.approx(40_000_000, abs=1)
    assert s.loc["Equity", "interest_residual_paid"] == pytest.approx(85_650_498, abs=1)


def test_impairment_unaffected_by_principal_cap(base, excel_exact):
    assert base[2]["impairment_pct"].values == pytest.approx(excel_exact[2]["impairment_pct"].values)


def test_key_outputs():
    out = key_outputs(WaterfallEngine())
    assert out["class_b_impairment"] == pytest.approx(0.0696, abs=1e-4)
    assert out["equity_impairment"] == pytest.approx(1.0)
    assert out["pool_ending_balance"] == pytest.approx(0, abs=1)
    assert out["equity_wipeout_period"] == 29
    assert out["class_a_payoff_period"] == 40


# --------------------------------------------------------------------------
# Sensitivity grid
# --------------------------------------------------------------------------

def test_sensitivity_grid_matches_excel():
    grid = sensitivity_grid(multipliers=tuple(EXCEL_GRID), severities=EXCEL_SEVERITIES)
    for mult, row in EXCEL_GRID.items():
        for sev, expected in zip(EXCEL_SEVERITIES, row):
            assert grid.loc[mult, sev] == pytest.approx(expected, abs=0.001), f"M={mult}, SEV={sev}"


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"a_share": 0.80},           # shares sum to 1.05
    {"sev": 1.5},
    {"default_mult": 13.0},      # stressed CDR above 100%
])
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(ValueError):
        run_waterfall(WaterfallEngine(**kwargs))
