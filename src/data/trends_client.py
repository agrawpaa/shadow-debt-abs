"""
trends_client.py
Fetches keyword search interest from Google Trends in a single batch,
resamples weekly series to month-end averages, and saves the output.
"""

from datetime import datetime
from pathlib import Path
import pandas as pd
from pytrends.request import TrendReq #make sure to run: pip install pytrends

# Configuration & Constants
KEYWORDS = [
    "cannot pay Affirm",
    "Klarna late fee",
    "Afterpay customer service",
    "overdraft fee",
    "hardship program",
]

START_DATE = "2021-01-01"
OUTPUT_FILE = Path("data/processed/trends_features.csv")


def fetch_and_process_trends() -> pd.DataFrame:
    """
    Fetches raw weekly Google Trends data for all target keywords in a single call,
    resamples to month-end mean values, and exports to disk.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    timeframe = f"{START_DATE} {today_str}"

    # Initialize client (tz=360 corresponds to US Central Standard Time)
    pytrends = TrendReq(hl="en-US", tz=360)

    # Batch build payload for all 5 keywords to avoid multiple calls / rate-limiting
    pytrends.build_payload(kw_list=KEYWORDS, timeframe=timeframe, geo="US")

    # Fetch weekly interest over time
    raw_df = pytrends.interest_over_time()

    if raw_df.empty:
        raise ValueError("No data returned from Google Trends for the specified keywords.")

    # Retain keyword columns and discard Google's 'isPartial' status flag
    feature_df = raw_df[KEYWORDS].copy()

    # Resample weekly data to monthly average indexed at month-end ('ME')
    monthly_df = feature_df.resample("ME").mean()
    monthly_df.index.name = "date"

    # Ensure target output directory exists and save
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    monthly_df.to_csv(OUTPUT_FILE)
    print(f"Successfully processed {len(monthly_df)} month-end rows to {OUTPUT_FILE}")

    return monthly_df


if __name__ == "__main__":
    fetch_and_process_trends()
