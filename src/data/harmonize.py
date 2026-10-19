# Builds the unified Track 1 modeling dataset by harmonizing three sources on a
# Month-End (ME) index:
#   1. FRED macro metrics            -> psavert, dspic96, cpi_yoy, totalsl_yoy
#   2. Google Trends distress index  -> distress_search_index (z-scored keyword composite)
#   3. CFPB complaint counts         -> cfpb_bnpl_complaints, cfpb_narrative_count
#
# The CFPB columns are a hard schema contract: synthetic_dti.py and 01_macro_eda.ipynb
# both read cfpb_bnpl_complaints. They are derived from the raw filtered complaint archive
# when present, and otherwise recovered from the existing production macro_features.csv so
# that re-running this script never drops the columns and never breaks Track 2.
#
# The output is truncated to the latest fully-reported month (no trailing NaNs from FRED's
# publication lag or the CFPB publication freeze).

from pathlib import Path

import pandas as pd

root_dir = Path(__file__).resolve().parent.parent.parent

FRED_RAW_PATH = root_dir / "data" / "raw" / "fred_macro_metrics.csv"
TRENDS_PATH = root_dir / "data" / "processed" / "trends_features.csv"
CFPB_RAW_PATH = root_dir / "data" / "raw" / "cfpb_filtered_bnpl_installment.csv"
OUTPUT_PATH = root_dir / "data" / "processed" / "macro_features.csv"

SERIES_START = "2021-01-01"

# Keywords that are flat-zero in almost every month add noise once z-scored, but low-volume
# high-severity brand-distress terms ("cannot pay Affirm" ~6%, "Klarna late fee" ~49%) carry
# real signal. Keep the threshold low enough to retain them.
MIN_NONZERO_SHARE = 0.04
# Laplace smoothing: added before standardizing so a near-constant (mostly-zero) keyword has a
# non-zero spread and never triggers divide-by-zero in the z-score.
LAPLACE_EPSILON = 1.0

FRED_COLUMN_MAP = {
    "personal_savings_rate": "psavert",
    "total_consumer_credit": "totalsl",
    "real_disposable_income": "dspic96",
    "cpi_living_expenses": "cpiaucsl",
}

# Columns that must always be present in the output (schema contract with Track 2).
CFPB_COLUMNS = ["cfpb_bnpl_complaints", "cfpb_narrative_count"]
MACRO_REQUIRED = ["psavert", "dspic96", "cpi_yoy", "totalsl_yoy"]


def load_fred_features(path: Path = FRED_RAW_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.rename(columns=FRED_COLUMN_MAP).set_index("date")
    df = df.resample("ME").last()

    # Fill single-month interior gaps in the source levels by time interpolation (e.g. FRED's
    # missing Oct-2025 CPI reading) so a lone missing index value does not blank out a YoY.
    # limit_area="inside" keeps the trailing publication-lag NaNs intact for truncation.
    level_cols = ["psavert", "dspic96", "totalsl", "cpiaucsl"]
    df[level_cols] = df[level_cols].interpolate(method="time", limit_area="inside")

    # 12 period lag on month-end data is a clean YoY: (Xt - Xt-12) / Xt-12
    df["cpi_yoy"] = df["cpiaucsl"].pct_change(periods=12)
    df["totalsl_yoy"] = df["totalsl"].pct_change(periods=12)

    return df[MACRO_REQUIRED]


def load_distress_search_index(path: Path = TRENDS_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")

    # Trends scales a keyword batch to one shared 0-100 peak, so z-score each keyword before
    # averaging to stop the highest-volume term dominating the composite index.
    usable = df.loc[:, df.ne(0).mean() >= MIN_NONZERO_SHARE]

    # Laplace smoothing guarantees a finite, non-zero std even for sparse terms.
    smoothed = usable + LAPLACE_EPSILON
    zscores = (smoothed - smoothed.mean()) / smoothed.std()

    return zscores.mean(axis=1).rename("distress_search_index").to_frame()


def load_cfpb_counts(
    raw_path: Path = CFPB_RAW_PATH, fallback_path: Path = OUTPUT_PATH
) -> pd.DataFrame:
    """Monthly BNPL complaint and narrative counts.

    Primary source is the raw filtered complaint archive. If it is absent (e.g. the large
    bulk download was not kept), fall back to the CFPB columns already stored in the current
    production macro_features.csv so the schema contract is never broken.
    """
    if raw_path.exists():
        raw = pd.read_csv(
            raw_path,
            usecols=["date_received", "consumer_complaint_narrative"],
            parse_dates=["date_received"],
        )
        raw = raw.set_index("date_received").sort_index()
        complaints = raw.resample("ME").size().rename("cfpb_bnpl_complaints")
        narratives = (
            raw["consumer_complaint_narrative"]
            .notna()
            .resample("ME")
            .sum()
            .astype(int)
            .rename("cfpb_narrative_count")
        )
        counts = pd.concat([complaints, narratives], axis=1)
        counts.index.name = "date"
        return counts

    if fallback_path.exists():
        prod = pd.read_csv(fallback_path, parse_dates=["date"]).set_index("date")
        missing = [c for c in CFPB_COLUMNS if c not in prod.columns]
        if missing:
            raise FileNotFoundError(
                f"CFPB raw archive missing and production fallback lacks columns {missing}. "
                f"Expected either {raw_path} or {CFPB_COLUMNS} in {fallback_path}."
            )
        return prod[CFPB_COLUMNS]

    raise FileNotFoundError(
        f"No CFPB source available. Provide the raw archive at {raw_path} "
        f"or an existing production file with {CFPB_COLUMNS} at {fallback_path}."
    )


def build_macro_features(
    fred_path: Path = FRED_RAW_PATH,
    trends_path: Path = TRENDS_PATH,
    cfpb_raw_path: Path = CFPB_RAW_PATH,
    cfpb_fallback_path: Path = OUTPUT_PATH,
) -> pd.DataFrame:
    fred = load_fred_features(fred_path)
    trends = load_distress_search_index(trends_path)
    cfpb = load_cfpb_counts(cfpb_raw_path, cfpb_fallback_path)

    # Outer-join everything onto a shared month-end index, then trim the YoY lookback window
    # off the front. Truncation of the incomplete tail is handled below.
    df = fred.join(trends, how="outer").join(cfpb, how="outer")
    df = df.loc[df.index >= SERIES_START]
    df = df.sort_index()

    # Drop trailing months with incomplete macro reporting (FRED publication lag) so the
    # dataset ends on the latest fully-reported month with zero nulls across every column.
    required = MACRO_REQUIRED + ["distress_search_index"] + CFPB_COLUMNS
    complete = df.dropna(subset=required)
    if complete.empty:
        raise ValueError("No fully-reported month found after harmonization.")
    last_complete = complete.index.max()
    df = df.loc[df.index <= last_complete]

    # Integer complaint/narrative counts (counts are whole numbers).
    for col in CFPB_COLUMNS:
        df[col] = df[col].astype("int64")

    df.index.name = "date"
    return df.reset_index()


if __name__ == "__main__":
    missing = [p for p in (FRED_RAW_PATH, TRENDS_PATH) if not p.exists()]
    if missing:
        print("Missing extracts, run fred_client.py and trends_client.py first:")
        for path in missing:
            print(f"  -> {path}")
    else:
        if not CFPB_RAW_PATH.exists():
            print(f"[INFO] CFPB raw archive not found at {CFPB_RAW_PATH}.")
            print(f"       Falling back to existing complaint counts in {OUTPUT_PATH}.")

        features = build_macro_features()

        print("\n--- Harmonized Sample Preview (Most Recent Window) ---")
        print(features.tail(5).to_string(index=False))

        null_total = int(features.isnull().sum().sum())
        print(f"\nRows: {len(features)} | Columns: {list(features.columns)}")
        print(f"Date range: {features['date'].min().date()} -> {features['date'].max().date()}")
        print(f"Total nulls: {null_total}")

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        features.to_csv(OUTPUT_PATH, index=False)
        print(f"\nMacro features written to: {OUTPUT_PATH}")
