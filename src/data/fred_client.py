# API Docs: https://fred.stlouisfed.org/docs/api/fred/


import os
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
from fredapi import Fred

# Force find the .env file in the root directory
root_dir = Path(__file__).resolve().parent.parent.parent
env_path = root_dir / '.env'
load_dotenv(dotenv_path=env_path)
#print(env_path)

class FredDataExtractor:
    def __init__(self, api_key: str = None): # type: ignore
        raw_key = api_key or os.getenv("FRED_API_KEY")
        if not raw_key:
            raise ValueError("An active FRED API Key is required to initialize extraction pipelines.")
        
        # SANITIZATION GUARD: Automatically strip extra spaces, quotes, or newlines
        self.api_key = raw_key.strip().replace('"', '').replace("'", "")
        
        # Verify length requirements set by St. Louis Fed
        if len(self.api_key) != 32:
            print(f"[CRITICAL WARNING] Your FRED API key is {len(self.api_key)} chars long, but FRED requires exactly 32 chars.")
            print(f"Inspected key token value: '{self.api_key}'")
            
        self.fred = Fred(api_key=self.api_key)

    def fetch_consumer_risk_metrics(self, start_date: str = "2021-01-01") -> pd.DataFrame:
        series_map = {
            "PSAVERT": "personal_savings_rate",
            "TOTALSL": "total_consumer_credit",
            "DSPIC96": "real_disposable_income",
            "CPIAUCSL": "cpi_living_expenses"
        }
        series_data = {}
        
        print("Commencing batch pull from FRED Production API Cluster...")
        for series_id, clean_name in series_map.items():
            try:
                raw_series = self.fred.get_series(series_id, observation_start=start_date)
                series_data[clean_name] = raw_series
                print(f" -> Successfully acquired metric node: {series_id}")
            except Exception as e:
                print(f" -> [ERROR] Failed to query series {series_id}: {e}")

        if not series_data:
            return pd.DataFrame(columns=["date"])

        df = pd.DataFrame(series_data)
        df.index = pd.to_datetime(df.index)
        df = df.resample('ME').first()
        df.index.name = "date"
        return df.reset_index()

if __name__ == "__main__":
    try:
        extractor = FredDataExtractor()
        # Pull a year before the target window so harmonize.py can compute YoY from Jan 2021
        macro_df = extractor.fetch_consumer_risk_metrics(start_date="2020-01-01")
        
        if not macro_df.empty and len(macro_df.columns) > 1:
            print("\n--- Data Sample Preview (Most Recent Window) ---")
            print(macro_df.tail(5).to_string(index=False))
            
            output_path = root_dir / "data" / "raw" /"fred_macro_metrics.csv"
            os.makedirs(output_path.parent, exist_ok=True)
            macro_df.to_csv(output_path, index=False)
            print(f"\nFRED data at: {output_path}")
        else:
            print("\nFailed to save data, check API?")
            
    except ValueError as e:
        print(f"\nInitialization Guard Blocked: {e}")
