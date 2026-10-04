# Resamples the FRED and Google Trends extracts to Month-End (ME) and exports the modeling dataset.
# cfpb_client.py and download_narratives.py are deprecated and no longer called here.

from pathlib import Path
import pandas as pd

root_dir = Path(__file__).resolve().parent.parent.parent

FRED_RAW_PATH = root_dir / "data" / "raw" / "fred_macro_metrics.csv"
TRENDS_PATH = root_dir / "data" / "processed" / "trends_features.csv"
OUTPUT_PATH = root_dir / "data" / "processed" / "macro_features.csv"

SERIES_START = "2021-01-01"

# Keywords that read zero in more than half the months carry no signal once z-scored
MIN_NONZERO_SHARE = 0.5

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


def load_distress_search_index(path: Path = TRENDS_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")

    # Trends scales a keyword batch to one shared 0-100 peak, so z-score each keyword
    # before averaging to stop the highest-volume term dominating the index
    usable = df.loc[:, df.ne(0).mean() >= MIN_NONZERO_SHARE]
    zscores = (usable - usable.mean()) / usable.std()

    return zscores.mean(axis=1).rename("distress_search_index").to_frame()


def build_macro_features(fred_path: Path = FRED_RAW_PATH, trends_path: Path = TRENDS_PATH) -> pd.DataFrame:
    df = load_fred_features(fred_path).join(load_distress_search_index(trends_path), how="inner")

    # Trim the YoY lookback window off the front
    df = df.loc[df.index >= SERIES_START]
    df.index.name = "date"
    return df.reset_index()


if __name__ == "__main__":
    missing = [p for p in (FRED_RAW_PATH, TRENDS_PATH) if not p.exists()]
    if missing:
        print("Missing extracts, run fred_client.py and trends_client.py first:")
        for path in missing:
            print(f" -> {path}")
    else:
        features = build_macro_features()

        print("\n--- Harmonized Sample Preview (Most Recent Window) ---")
        print(features.tail(5).to_string(index=False))

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        features.to_csv(OUTPUT_PATH, index=False)
        print(f"\nMacro features at: {OUTPUT_PATH}")
