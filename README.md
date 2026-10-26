# The Shadow Debt Ledger: Consumer ABS Risk
do later
Make sure to download the file below to get narritives
https://files.consumerfinance.gov/ccdb/complaints.csv.zip

## Waterfall Model (Week 1 Excel → Python)

Python port of `Waterfall Model - Week 1 Deliverable 1`. Every figure ties out to the Excel (see tests).

| File | Purpose |
|---|---|
| `src/models/waterfall_engine.py` | `WaterfallEngine`: inputs, SMM/MDR, 48-month pool amortization (`build_pool_schedule`) |
| `src/models/waterfall_cashflows.py` | `run_waterfall` (priority of payments + loss allocation), `tranche_summary`, `key_outputs`, `sensitivity_grid` |
| `tests/test_waterfall_cashflows.py` | Tie-out tests against the Excel base case and the 7 × 5 sensitivity grid |

```bash
pip install -r requirements.txt
python3 -m src.models.waterfall_cashflows     # prints base case + sensitivity, writes CSVs to data/processed/
python3 -m pytest tests/ -v
```

```python
from src.models.waterfall_engine import WaterfallEngine
from src.models.waterfall_cashflows import tranche_summary, sensitivity_grid

tranche_summary(WaterfallEngine(default_mult=1.5, sev=0.65))   # stress case
sensitivity_grid()                                              # Class B impairment, M × SEV
```

`cap_principal_to_cash=False` reproduces the Excel exactly. The default (`True`) stops the model paying more principal
than the cash collected, which the Excel does in months 46–48. Impairment % is the same either way.
