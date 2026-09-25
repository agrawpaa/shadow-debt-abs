import csv
import io
import logging
import os
import zipfile
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class CFPBComplaintsClient:
    """
    As of August 14 2026 the CFPB stopped publishing consumer
    complaint narratives in the primary database export. Narratives for complaints
    as such will be handled in a diff file
    """

    BULK_CSV_URL = "https://files.consumerfinance.gov/ccdb/complaints.csv.zip"

    # Key BNPL and point-of-sale installment fintech companies
    BNPL_COMPANIES = [
        "affirm, inc.",
        "affirm holdings, inc",
        "klarna inc.",
        "afterpay us, inc.",
        #"paypal holdings, inc.",
        #"zip co",
        #"sezzle inc.",
        #"splitit",
        #"uplift, inc.",
    ]

    # Keyword terms for filtering BNPL & installment loan records
    BNPL_KEYWORDS = [
        "buy now pay later",
        "buy now, pay later",
        "bnpl",
        "pay in 4",
        "pay in four",
        "affirm",
        "klarna",
        "afterpay",
        #"sezzle",
        #"zip co",
        #"splitit",
        #"uplift",
    ]

    INSTALLMENT_KEYWORDS = [
        "affirm",
        #"installment loan",
        #"installment payment",
        #"installment plan",
    ]

    # Columns present in the CFPB bulk CSV
    _BULK_CSV_COLUMN_MAP = {
        "Date received":                    "date_received",
        "Product":                          "product",
        "Sub-product":                      "sub_product",
        "Issue":                            "issue",
        "Sub-issue":                        "sub_issue",
        "Consumer complaint narrative":     "consumer_complaint_narrative",
        "Company public response":          "company_response",
        "Company":                          "company",
        "State":                            "state",
        "ZIP code":                         "zip_code",
        "Tags":                             "tags",
        "Consumer consent provided?":       "consumer_consent",
        "Submitted via":                    "submitted_via",
        "Date sent to company":             "date_sent_to_company",
        "Company response to consumer":     "company_response_to_consumer",
        "Timely response?":                 "timely",
        "Consumer disputed?":               "consumer_disputed",
        "Complaint ID":                     "complaint_id",
    }

    CSV_HEADERS = [
        "complaint_id",
        "date_received",
        "company",
        "product",
        "sub_product",
        "issue",
        "sub_issue",
        "state",
        "zip_code",
        "submitted_via",
        "company_response",
        "timely",
        "consumer_complaint_narrative",
    ]

    def __init__(
        self,
        timeout: int = 300,
        cache_path: Optional[str] = None,
        narratives_path: Optional[str] = None,
    ):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "ShadowCreditResearch/1.0 "
                "(CFPB Complaints Data Pipeline; Python requests)"
            ),
            "Accept": "*/*",
        })
        self.timeout = timeout
        self.cache_path = cache_path
        self.narratives_path = narratives_path
        self.narratives_map: Dict[str, str] = {}

        # Pre-compile filter sets for O(1) keyword and company matching
        self._target_keywords: frozenset = frozenset(self.BNPL_KEYWORDS + self.INSTALLMENT_KEYWORDS)
        self._bnpl_company_lower: frozenset = frozenset(self.BNPL_COMPANIES)

        if narratives_path and os.path.exists(narratives_path):
            self.load_narratives_archive(narratives_path)

    # Narrative archive stuff

    def load_narratives_archive(self, file_path: str) -> int:
        """

        Expected columns:
        - 'Complaint ID' or 'complaint_id'
        - 'Consumer complaint narrative' or 'narrative'
        """
        if not os.path.exists(file_path):
            logger.warning("Narratives archive file not found: %s", file_path)
            return 0

        logger.info("Loading complaint narratives from %s ...", file_path)
        try:
            import pandas as pd
            df = pd.read_csv(file_path, dtype=str)
            cid_col = next((c for c in ["complaint_id", "Complaint ID", "Complaint Id"] if c in df.columns), None)
            nar_col = next((c for c in ["consumer_complaint_narrative", "Consumer complaint narrative", "narrative"] if c in df.columns), None)
            if cid_col and nar_col:
                df = df.dropna(subset=[cid_col, nar_col])
                self.narratives_map = dict(zip(df[cid_col].str.strip(), df[nar_col].str.strip()))
                count = len(self.narratives_map)
                logger.info("Loaded %d narratives into memory via pandas.", count)
                return count
        except Exception as e:
            logger.warning("Pandas load failed (%s), falling back to CSV reader...", e)

        count = 0

        def _process_reader(reader: csv.DictReader):
            nonlocal count
            for row in reader:
                cid = (
                    row.get("Complaint ID")
                    or row.get("complaint_id")
                    or row.get("Complaint Id")
                    or ""
                ).strip()
                narrative = (
                    row.get("Consumer complaint narrative")
                    or row.get("consumer_complaint_narrative")
                    or row.get("narrative")
                    or ""
                ).strip()
                if cid and narrative:
                    self.narratives_map[cid] = narrative
                    count += 1

        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".csv"):
                        with zf.open(name) as f:
                            text_f = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                            _process_reader(csv.DictReader(text_f))
        else:
            with open(file_path, mode="r", encoding="utf-8", errors="replace") as f:
                _process_reader(csv.DictReader(f))

        logger.info("Loaded %d narratives into memory.", count)
        return count

    # Internal helpers

    @staticmethod
    def _extract_source(record: Dict[str, Any]) -> Dict[str, Any]:
        """Return the _source sub-dict if present, otherwise the record itself."""
        return record.get("_source", record) if "_source" in record else record

    def _download_bulk_zip(self) -> bytes:
        """
        Download the CFPB bulk CSV zip. If cache_path is set and the file
        already exists, load from disk instead of downloading again.
        This should be in the repo already

        Returns:
            Raw bytes of the zip file.
        """
        if self.cache_path and os.path.isfile(self.cache_path):
            logger.info("Loading bulk CSV from cache: %s", self.cache_path)
            with open(self.cache_path, "rb") as f:
                return f.read()

        logger.info("Downloading CFPB bulk CSV from %s ...", self.BULK_CSV_URL)
        content_chunks: list[bytes] = []
        downloaded = 0

        with self.session.get(self.BULK_CSV_URL, stream=True, timeout=self.timeout) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            for chunk in r.iter_content(chunk_size=1_048_576):  # 1 MB chunks
                content_chunks.append(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    logger.info(
                        "  Downloading... %.1f MB / %.1f MB (%.0f%%)",
                        downloaded / 1e6,
                        total / 1e6,
                        pct,
                    )

        data = b"".join(content_chunks)
        logger.info("Download complete: %.1f MB", len(data) / 1e6)

        if self.cache_path:
            os.makedirs(os.path.dirname(os.path.abspath(self.cache_path)), exist_ok=True)
            with open(self.cache_path, "wb") as f:
                f.write(data)
            logger.info("Cached bulk zip to: %s", self.cache_path)

        return data

    def _iter_bulk_csv_rows(
        self,
        zip_bytes: bytes,
        date_received_min: Optional[str] = None,
        date_received_max: Optional[str] = None,
    ):
        """
        Open the bulk zip in memory and yield normalised row dicts (using our
        internal field names) optionally filtered by date range, with narratives
        joined from narratives_map if available.
        """
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_name = zf.namelist()[0]
            logger.info("Reading CSV entry '%s' from zip ...", csv_name)
            with zf.open(csv_name) as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="utf-8", errors="replace")
                reader = csv.DictReader(text_f)
                for row in reader:
                    # Normalise header names
                    norm: Dict[str, str] = {
                        self._BULK_CSV_COLUMN_MAP.get(k, k): v
                        for k, v in row.items()
                    }
                    # Date filter
                    dr = norm.get("date_received", "")
                    if date_received_min and dr < date_received_min:
                        continue
                    if date_received_max and dr > date_received_max:
                        continue

                    cid = norm.get("complaint_id", "")
                    # Attach narrative from archive if available, otherwise default to empty string
                    if cid in self.narratives_map:
                        norm["consumer_complaint_narrative"] = self.narratives_map[cid]
                    elif "consumer_complaint_narrative" not in norm or norm["consumer_complaint_narrative"] is None:
                        norm["consumer_complaint_narrative"] = ""

                    yield norm


    def is_bnpl_or_installment(self, record: Dict[str, Any]) -> bool:
        """Check whether a single complaint record matches BNPL / installment criteria."""
        source = self._extract_source(record)

        company = str(source.get("company") or "").lower()
        product = str(source.get("product") or "").lower()
        sub_product = str(source.get("sub_product") or "").lower()
        issue = str(source.get("issue") or "").lower()
        sub_issue = str(source.get("sub_issue") or "").lower()
        narrative = str(source.get("consumer_complaint_narrative") or "").lower()

        combined_text = f"{product} {sub_product} {issue} {sub_issue} {narrative}"

        # Check 1: Known BNPL company
        company_match = any(bnpl_co in company for bnpl_co in self._bnpl_company_lower)

        # Check 2: Product / Sub-product is Installment Loan or BNPL
        product_match = (
            "installment" in product
            or "installment" in sub_product
            or "bnpl" in product
            or "bnpl" in sub_product
        )

        # Check 3: Text content contains target keywords
        keyword_match = any(kw in combined_text for kw in self._target_keywords)

        return company_match or product_match or keyword_match

    def fetch_records_batch(
        self,
        search_terms: Optional[List[str]] = None,  # kept for API compat
        companies: Optional[List[str]] = None,
        date_received_min: Optional[str] = "2021-01-01",
        date_received_max: Optional[str] = None,
        total_limit: Optional[int] = None,
        page_size: int = 250,                      # kept for API compat
    ) -> List[Dict[str, Any]]:
        """
        Download the CFPB bulk CSV and return complaint records matching
        the given company list (if provided) and date range.
        If companies is None, all companies are included.
        """
        query_companies: Optional[frozenset] = (
            frozenset(c.lower() for c in companies) if companies is not None else None
        )

        logger.info(
            "Starting bulk fetch for %s companies (since %s): %s",
            len(query_companies) if query_companies is not None else "all",
            date_received_min or "beginning",
            list(query_companies) if query_companies is not None else "all companies",
        )

        zip_bytes = self._download_bulk_zip()
        records: List[Dict[str, Any]] = []

        for row in self._iter_bulk_csv_rows(zip_bytes, date_received_min, date_received_max):
            if query_companies is not None:
                company_lower = (row.get("company") or "").lower()
                if not any(bc in company_lower or company_lower in bc for bc in query_companies):
                    continue
            records.append(row)
            if len(records) % 50000 == 0:
                logger.info("Progress: %d matching records so far", len(records))
            if total_limit is not None and len(records) >= total_limit:
                break

        logger.info("Bulk fetch complete. Found %d matching records.", len(records))
        return records

    def filter_bnpl_and_installment(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter complaint records specifically for BNPL and Installment loans.
        """
        filtered = [r for r in records if self.is_bnpl_or_installment(r)]
        logger.info(
            "Filtered %d records down to %d BNPL & installment records",
            len(records),
            len(filtered),
        )
        return filtered

    def export_to_csv(self, records: List[Dict[str, Any]], filename: str) -> None:
        """
        Export complaint records into a formatted CSV file.
        """
        if not records:
            logger.warning("No records to export for file: %s", filename)

        dirname = os.path.dirname(filename)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        try:
            with open(filename, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self.CSV_HEADERS, extrasaction="ignore")
                writer.writeheader()

                for record in records:
                    source = self._extract_source(record)
                    narrative = source.get("consumer_complaint_narrative") or ""
                    cleaned_narrative = str(narrative).replace("\n", " ").replace("\r", " ").strip()

                    row = {
                        "complaint_id":                 source.get("complaint_id", ""),
                        "date_received":                source.get("date_received", ""),
                        "company":                      source.get("company", ""),
                        "product":                      source.get("product", ""),
                        "sub_product":                  source.get("sub_product", ""),
                        "issue":                        source.get("issue", ""),
                        "sub_issue":                    source.get("sub_issue", ""),
                        "state":                        source.get("state", ""),
                        "zip_code":                     source.get("zip_code", ""),
                        "submitted_via":                source.get("submitted_via", ""),
                        "company_response":             source.get("company_response", ""),
                        "timely":                       source.get("timely", ""),
                        "consumer_complaint_narrative": cleaned_narrative,
                    }
                    writer.writerow(row)

            logger.info("Successfully exported %d records to %s", len(records), filename)
        except IOError as e:
            logger.error("Failed to write CSV file %s: %s", filename, e)

    def run_pipeline(
        self,
        all_export_path: str = "data/raw/cfpb_all_complaints.csv",
        filtered_export_path: str = "data/raw/cfpb_filtered_bnpl_installment.csv",
        narratives_archive_path: Optional[str] = None,
        date_received_min: Optional[str] = "2021-01-01",
        date_received_max: Optional[str] = None,
        total_limit: Optional[int] = None,
    ) -> None:
        #Run the complete CFPB data pipeline:
        
        logger.info(
            "Starting CFPB data collection pipeline (since %s, limit=%s)...",
            date_received_min or "beginning",
            total_limit or "unlimited",
        )

        archive_path = narratives_archive_path or self.narratives_path
        if archive_path and not self.narratives_map and os.path.exists(archive_path):
            self.load_narratives_archive(archive_path)

        for path in [all_export_path, filtered_export_path]:
            dirname = os.path.dirname(path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)

        zip_bytes = self._download_bulk_zip()

        all_count = 0
        filtered_count = 0

        with open(all_export_path, mode="w", newline="", encoding="utf-8") as f_all, \
             open(filtered_export_path, mode="w", newline="", encoding="utf-8") as f_filt:

            w_all = csv.DictWriter(f_all, fieldnames=self.CSV_HEADERS, extrasaction="ignore")
            w_filt = csv.DictWriter(f_filt, fieldnames=self.CSV_HEADERS, extrasaction="ignore")

            w_all.writeheader()
            w_filt.writeheader()

            for row in self._iter_bulk_csv_rows(zip_bytes, date_received_min, date_received_max):
                narrative = row.get("consumer_complaint_narrative") or ""
                cleaned_narrative = str(narrative).replace("\n", " ").replace("\r", " ").strip()

                formatted_row = {
                    "complaint_id":                 row.get("complaint_id", ""),
                    "date_received":                row.get("date_received", ""),
                    "company":                      row.get("company", ""),
                    "product":                      row.get("product", ""),
                    "sub_product":                  row.get("sub_product", ""),
                    "issue":                        row.get("issue", ""),
                    "sub_issue":                    row.get("sub_issue", ""),
                    "state":                        row.get("state", ""),
                    "zip_code":                     row.get("zip_code", ""),
                    "submitted_via":                row.get("submitted_via", ""),
                    "company_response":             row.get("company_response", ""),
                    "timely":                       row.get("timely", ""),
                    "consumer_complaint_narrative": cleaned_narrative,
                }

                w_all.writerow(formatted_row)
                all_count += 1

                if self.is_bnpl_or_installment(formatted_row):
                    w_filt.writerow(formatted_row)
                    filtered_count += 1

                if all_count % 100000 == 0:
                    logger.info("Processed %d total complaints (%d BNPL/installment matching)...", all_count, filtered_count)

                if total_limit is not None and all_count >= total_limit:
                    break

        logger.info("Pipeline complete. Exported 2 CSV files:")
        logger.info(" - All Records:      %s (%d records)", all_export_path, all_count)
        logger.info(" - Filtered Records: %s (%d records)", filtered_export_path, filtered_count)


if __name__ == "__main__":
    client = CFPBComplaintsClient(
        cache_path="data/raw/cfpb_bulk.zip",
        narratives_path="data/raw/cfpb_narratives.csv",
    )
    client.run_pipeline(
        all_export_path="data/raw/cfpb_all_complaints.csv",
        filtered_export_path="data/raw/cfpb_filtered_bnpl_installment.csv",
        date_received_min="2021-01-01",
        total_limit=None,  # Pull all records since Jan 2021
    )
