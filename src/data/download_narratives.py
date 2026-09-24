import concurrent.futures
import os
import time
from pathlib import Path
import pandas as pd

TOTAL_SHARDS = 67
OUTPUT_PATH = Path("data/raw/cfpb_narratives.csv")

def extract_narratives_from_shard(shard_idx: int) -> pd.DataFrame:
    url = f"https://huggingface.co/datasets/Mouwiya/cfpb-consumer-complaints/resolve/main/data/complaints-{shard_idx:05d}.parquet"
    try:
        df = pd.read_parquet(url, columns=["complaint_id", "consumer_complaint_narrative"])
        df = df[df["consumer_complaint_narrative"].notna()]
        df = df[df["consumer_complaint_narrative"].astype(str).str.strip().ne("")]
        return df
    except Exception as e:
        print(f"Error on shard {shard_idx}: {e}")
        return pd.DataFrame(columns=["complaint_id", "consumer_complaint_narrative"])

def main():
    print(f"Starting extraction of consumer complaint narratives across {TOTAL_SHARDS} shards...")
    start_time = time.time()
    
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_dfs = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(extract_narratives_from_shard, i): i for i in range(TOTAL_SHARDS)}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            shard_idx = futures[future]
            try:
                res = future.result()
                if not res.empty:
                    all_dfs.append(res)
                completed += 1
                if completed % 10 == 0 or completed == TOTAL_SHARDS:
                    print(f"Progress: {completed}/{TOTAL_SHARDS} shards processed ({time.time() - start_time:.1f}s)")
            except Exception as exc:
                print(f"Shard {shard_idx} generated an exception: {exc}")

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        # Deduplicate by complaint_id
        combined = combined.drop_duplicates(subset=["complaint_id"])
        print(f"Total unique narratives extracted: {len(combined):,}")
        combined.to_csv(OUTPUT_PATH, index=False)
        print(f"Successfully saved narratives archive to: {OUTPUT_PATH}")
    else:
        print("No narratives extracted.")

if __name__ == "__main__":
    main()
