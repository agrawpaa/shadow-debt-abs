# Resamples the FRED, Google Trends and CFPB complaint series to Month-End (ME) and exports the modeling dataset.
# The CFPB database stopped publishing in Aug 2026, so complaint counts come from a frozen archive
# (the existing production file) rather than cfpb_client.py, and the series ends before the freeze.

from pathlib import Path
import pandas as pd

root_dir = Path(__file__).resolve().parent.parent.parent

FRED_RAW_PATH = root_dir / "data" / "raw" / "fred_macro_metrics.csv"
TRENDS_PATH = root_dir / "data" / "processed" / "trends_features.csv"
OUTPUT_PATH = root_dir / "data" / "processed" / "macro_features.csv"
# Complaint history is frozen: CFPB publication stopped Aug 2026, so the last published counts are the archive
CFPB_ARCHIVE_PATH = OUTPUT_PATH

SERIES_START = "2021-01-01"

FRED_COLS = ["psavert", "dspic96", "cpi_yoy", "totalsl_yoy"]
CFPB_COLS = ["cfpb_bnpl_complaints", "cfpb_narrative_count"]
TRENDS_COL = "distress_search_index"

# Low-volume, high-severity terms ("cannot pay Affirm") are mostly zero but still carry signal
MIN_NONZERO_SHARE = 0.05
LAPLACE_ALPHA = 1.0
STD_FLOOR = 1e-6

FRED_COLUMN_MAP = {
    "personal_savings_rate": "psavert",
    "total_consumer_credit": "totalsl",
    "real_disposable_income": "dspic96",
    "cpi_living_expenses": "cpiaucsl",
}


def load_from_production(cols: list, path: Path = OUTPUT_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No production file to fall back on: {path}")
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"{path.name} is missing {missing}; restore it with `git checkout` before re-running")
    return df[cols]


def load_fred_features(path: Path = FRED_RAW_PATH) -> pd.DataFrame:
    if not path.exists():
        print(f"FRED: {path.name} not found, using values from {OUTPUT_PATH.name}")
        return load_from_production(FRED_COLS)

    print(f"FRED: {path}")
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.rename(columns=FRED_COLUMN_MAP).set_index("date")
    df = df.resample("ME").last()

    # 12 period lag on month-end data is a clean YoY: (Xt - Xt-12) / Xt-12
    df["cpi_yoy"] = df["cpiaucsl"].pct_change(periods=12)
    df["totalsl_yoy"] = df["totalsl"].pct_change(periods=12)

    return df[FRED_COLS]


def load_complaint_archive(path: Path = CFPB_ARCHIVE_PATH) -> pd.DataFrame:
    print(f"CFPB: frozen archive {path.name}")
    return load_from_production(CFPB_COLS, path).astype(int)


def load_distress_search_index(path: Path = TRENDS_PATH) -> pd.DataFrame:
    print(f"Trends: {path}")
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")

    usable = df.loc[:, df.ne(0).mean() >= MIN_NONZERO_SHARE]

    # Trends scales a keyword batch to one shared 0-100 peak, so z-score each keyword
    # before averaging to stop the highest-volume term dominating the index.
    # The Laplace alpha alone doesn't change a z-score (it's shift-invariant); the std floor
    # is what keeps a flat, all-zero term from dividing by zero.
    smoothed = usable + LAPLACE_ALPHA
    zscores = (smoothed - smoothed.mean()) / smoothed.std().clip(lower=STD_FLOOR)

    return zscores.mean(axis=1).rename(TRENDS_COL).to_frame()


def build_macro_features(fred_path: Path = FRED_RAW_PATH, trends_path: Path = TRENDS_PATH,
                         cfpb_path: Path = CFPB_ARCHIVE_PATH) -> pd.DataFrame:
    df = (
        load_fred_features(fred_path)
        .join(load_complaint_archive(cfpb_path), how="left")
        .join(load_distress_search_index(trends_path), how="left")
    )

    # Trim the YoY lookback window off the front
    df = df.loc[df.index >= SERIES_START]

    # Skipped releases (e.g. the Oct 2025 CPI lost to the government shutdown) are bridged
    # between neighbours; trailing gaps are left for the truncation below
    gaps = df[FRED_COLS].isna() & df[FRED_COLS].bfill().notna()
    for col, months in gaps.items():
        for month in months[months].index:
            print(f"FRED: interpolating {col} for {month:%Y-%m}")
    df[FRED_COLS] = df[FRED_COLS].interpolate(method="time", limit_area="inside")

    # End on the last month with complete macro reporting and published narratives (pre-freeze)
    complete = df.notna().all(axis=1) & df["cfpb_narrative_count"].gt(0)
    df = df.loc[:complete[complete].index.max()]
    assert not df.isna().any().any(), f"Interior gaps in harmonized data:\n{df[df.isna().any(axis=1)]}"

    df[CFPB_COLS] = df[CFPB_COLS].astype(int)
    df.index.name = "date"
    return df[FRED_COLS + CFPB_COLS + [TRENDS_COL]].reset_index()


if __name__ == "__main__":
    # Built fully in memory before writing, so the CFPB archive is read before the output is overwritten
    features = build_macro_features()

    print("\n--- Harmonized Sample Preview (Most Recent Window) ---")
    print(features.tail(5).to_string(index=False))
    print(f"\n{len(features)} months: {features['date'].min():%Y-%m-%d} to {features['date'].max():%Y-%m-%d}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(OUTPUT_PATH, index=False)
    print(f"Macro features at: {OUTPUT_PATH}")
