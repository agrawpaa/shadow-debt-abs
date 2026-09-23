# Resamples the FRED and CFPB extracts to Month-End (ME) and exports the modeling dataset.

from pathlib import Path
import pandas as pd

root_dir = Path(__file__).resolve().parent.parent.parent

FRED_RAW_PATH = root_dir / "data" / "raw" / "fred_macro_metrics.csv"
CFPB_RAW_PATH = root_dir / "data" / "raw" / "cfpb_filtered_bnpl_installment.csv"
OUTPUT_PATH = root_dir / "data" / "processed" / "macro_features.csv"

SERIES_START = "2021-01-01"

FRED_COLUMN_MAP = {
    "personal_savings_rate": "psavert",
    "total_consumer_credit": "totalsl",
    "real_disposable_income": "dspic96",
    "cpi_living_expenses": "cpiaucsl",
}


def load_fred_features(path: Path = FRED_RAW_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.rename(columns=FRED_COLUMN_MAP).set_index("date")
    df = df.resample("ME").last()

    # 12 period lag on month-end data is a clean YoY: (Xt - Xt-12) / Xt-12
    df["cpi_yoy"] = df["cpiaucsl"].pct_change(periods=12)
    df["totalsl_yoy"] = df["totalsl"].pct_change(periods=12)

    return df[["psavert", "dspic96", "cpi_yoy", "totalsl_yoy"]]


def load_complaint_velocity(path: Path = CFPB_RAW_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["date_received", "consumer_complaint_narrative"])

    # CFPB stamps are tz-aware ISO strings; drop the offset so they align with FRED
    received = pd.to_datetime(df["date_received"], errors="coerce", utc=True)
    df = df.assign(date_received=received.dt.tz_localize(None)).dropna(subset=["date_received"])
    df = df.set_index("date_received")

    has_narrative = df["consumer_complaint_narrative"].fillna("").str.strip().ne("")
    monthly = pd.DataFrame({
        "cfpb_bnpl_complaints": df.resample("ME").size(),
        "cfpb_narrative_count": has_narrative.resample("ME").sum(),
    })
    return monthly.astype(int)


def build_macro_features(fred_path: Path = FRED_RAW_PATH, cfpb_path: Path = CFPB_RAW_PATH) -> pd.DataFrame:
    counts = ["cfpb_bnpl_complaints", "cfpb_narrative_count"]

    df = load_fred_features(fred_path).join(load_complaint_velocity(cfpb_path), how="left")
    df[counts] = df[counts].fillna(0).astype(int)

    # Trim the YoY lookback window off the front
    df = df.loc[df.index >= SERIES_START]
    df.index.name = "date"
    return df.reset_index()


if __name__ == "__main__":
    missing = [p for p in (FRED_RAW_PATH, CFPB_RAW_PATH) if not p.exists()]
    if missing:
        print("Missing raw extracts, run fred_client.py and cfpb_client.py first:")
        for path in missing:
            print(f" -> {path}")
    else:
        features = build_macro_features()

        print("\n--- Harmonized Sample Preview (Most Recent Window) ---")
        print(features.tail(5).to_string(index=False))

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        features.to_csv(OUTPUT_PATH, index=False)
        print(f"\nMacro features at: {OUTPUT_PATH}")
