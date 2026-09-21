import csv
import logging
import os
from typing import Any, Dict, List, Optional
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class CFPBComplaintsClient:
    """
    API search client that queries and filters for BNPL (Buy Now, Pay Later)
    and installment loan records from the CFPB Consumer Complaint Database.
    """

    BASE_URL = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"

    # Key BNPL and point-of-sale installment fintech companies
    BNPL_COMPANIES = [
        "Affirm, Inc.",
        "Affirm Holdings, Inc",
        "Klarna Inc.",
        "Afterpay US, Inc.",
        "PayPal Holdings, Inc.",
        "Zip Co",
        "Sezzle Inc.",
        "Splitit",
        "Uplift, Inc.",
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
        "sezzle",
        "zip co",
        "splitit",
        "uplift",
    ]

    INSTALLMENT_KEYWORDS = [
        "installment",
        "installment loan",
        "installment payment",
        "installment plan",
    ]

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

    def __init__(self, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ShadowCreditResearch/1.0 (CFPB Complaints Data Pipeline; Python requests)",
            "Accept": "application/json",
        })
        self.timeout = timeout

    def search_complaints(
        self,
        search_term: Optional[str] = None,
        companies: Optional[List[str]] = None,
        product: Optional[str] = None,
        date_received_min: Optional[str] = None,
        date_received_max: Optional[str] = None,
        has_narrative: Optional[bool] = None,
        size: int = 100,
        search_after: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query the CFPB Consumer Complaint Database API endpoint.

        Args:
            search_term: Keyword or phrase to search.
            companies: List of company names to filter by.
            product: Financial product category to filter by.
            date_received_min: Lower date boundary (YYYY-MM-DD).
            date_received_max: Upper date boundary (YYYY-MM-DD).
            has_narrative: Filter by whether consumer complaint narrative is published.
            size: Number of records per page/request.
            search_after: Keyset pagination token (e.g., 'sortval1_sortval2').

        Returns:
            List of complaint records (dict objects from API hits).
        """
        params: Dict[str, Any] = {
            "size": size,
            "sort": "created_date_desc",
            "field": "all",
        }

        if search_term:
            params["search_term"] = search_term
        if companies:
            params["company"] = companies
        if product:
            params["product"] = product
        if date_received_min:
            params["date_received_min"] = date_received_min
        if date_received_max:
            params["date_received_max"] = date_received_max
        if has_narrative is not None:
            params["has_narrative"] = "true" if has_narrative else "false"
        if search_after:
            params["search_after"] = search_after

        try:
            logger.info("Requesting CFPB complaints with params: %s", params)
            response = self.session.get(self.BASE_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {})
            logger.info("Retrieved %d records (Total matching in CFPB: %s)", len(hits), total)
            return hits
        except requests.exceptions.RequestException as e:
            logger.error("Error fetching data from CFPB API: %s", e)
            return []

    def fetch_records_batch(
        self,
        search_terms: Optional[List[str]] = None,
        companies: Optional[List[str]] = None,
        date_received_min: Optional[str] = "2021-01-01",
        date_received_max: Optional[str] = None,
        total_limit: Optional[int] = None,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Fetch complaints in batches, querying across search terms and/or companies,
        and deduplicating by complaint_id using keyset pagination (search_after).

        Args:
            search_terms: List of search terms to query.
            companies: List of company names to filter by.
            date_received_min: Lower date boundary (YYYY-MM-DD), defaults to "2021-01-01".
            date_received_max: Upper date boundary (YYYY-MM-DD).
            total_limit: Maximum total unique records to fetch, or None for all matching records.
            page_size: Batch size per request (max 100 for CFPB API).

        Returns:
            List of combined unique raw complaint hits.
        """
        all_records: Dict[str, Dict[str, Any]] = {}
        terms = search_terms or [
            '"buy now pay later" OR "bnpl" OR "installment"',
            "affirm OR klarna OR afterpay OR sezzle OR uplift OR splitit OR 'zip co'",
        ]

        for term in terms:
            search_after_token: Optional[str] = None
            term_records_count = 0
            logger.info("Fetching complaints for search term: %s (since %s)", term, date_received_min or "beginning")

            while total_limit is None or len(all_records) < total_limit:
                current_size = page_size if total_limit is None else min(page_size, total_limit - len(all_records))
                hits = self.search_complaints(
                    search_term=term,
                    companies=companies,
                    date_received_min=date_received_min,
                    date_received_max=date_received_max,
                    size=current_size,
                    search_after=search_after_token,
                )
                if not hits:
                    break

                for hit in hits:
                    cid = str(hit.get("_source", {}).get("complaint_id") or hit.get("_id", ""))
                    if cid and cid not in all_records:
                        all_records[cid] = hit
                        term_records_count += 1
                        if total_limit is not None and len(all_records) >= total_limit:
                            break

                if len(all_records) % 1000 == 0:
                    logger.info("Progress: %d total unique complaints accumulated so far", len(all_records))

                # Update keyset pagination cursor
                last_hit = hits[-1]
                sort_vals = last_hit.get("sort")
                if sort_vals and isinstance(sort_vals, list):
                    search_after_token = "_".join(map(str, sort_vals))
                else:
                    break

                if len(hits) < current_size:
                    break

            logger.info("Finished term '%s'. Fetched %d new records (Total unique: %d)", term, term_records_count, len(all_records))

        return list(all_records.values())

    def filter_bnpl_and_installment(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter complaint records specifically for BNPL (Buy Now, Pay Later)
        and Installment loan products/services.

        Checks:
        - Company name matches known BNPL companies
        - Product or sub-product indicates installment loans or BNPL
        - Narrative, issue, or sub-issue contains BNPL / installment keywords

        Args:
            records: List of raw hit dicts from CFPB API.

        Returns:
            Filtered list of complaint records matching BNPL or installment criteria.
        """
        filtered: List[Dict[str, Any]] = []
        target_keywords = set(self.BNPL_KEYWORDS + self.INSTALLMENT_KEYWORDS)
        bnpl_company_lower = {c.lower() for c in self.BNPL_COMPANIES}

        for record in records:
            source = record.get("_source", {}) if "_source" in record else record

            company = str(source.get("company") or "").lower()
            product = str(source.get("product") or "").lower()
            sub_product = str(source.get("sub_product") or "").lower()
            issue = str(source.get("issue") or "").lower()
            sub_issue = str(source.get("sub_issue") or "").lower()
            narrative = str(source.get("consumer_complaint_narrative") or "").lower()

            combined_text = f"{product} {sub_product} {issue} {sub_issue} {narrative}"

            # Check 1: Known BNPL company
            company_match = any(bnpl_co in company for bnpl_co in bnpl_company_lower)

            # Check 2: Product / Sub-product is Installment Loan or BNPL
            product_match = "installment" in product or "installment" in sub_product or "bnpl" in product or "bnpl" in sub_product

            # Check 3: Text content contains target keywords
            keyword_match = any(kw in combined_text for kw in target_keywords)

            if company_match or product_match or keyword_match:
                filtered.append(record)

        logger.info("Filtered %d records down to %d BNPL & installment records", len(records), len(filtered))
        return filtered

    def export_to_csv(self, records: List[Dict[str, Any]], filename: str) -> None:
        """
        Export complaint records into a formatted CSV file.

        Args:
            records: List of complaint record hits.
            filename: Destination CSV file path.
        """
        if not records:
            logger.warning("No records to export for file: %s", filename)

        # Ensure destination directory structure exists locally
        dirname = os.path.dirname(filename)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        try:
            with open(filename, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self.CSV_HEADERS)
                writer.writeheader()

                for record in records:
                    source = record.get("_source", {}) if "_source" in record else record
                    narrative = source.get("consumer_complaint_narrative") or ""
                    cleaned_narrative = str(narrative).replace("\n", " ").replace("\r", " ").strip()

                    row = {
                        "complaint_id": source.get("complaint_id", ""),
                        "date_received": source.get("date_received", ""),
                        "company": source.get("company", ""),
                        "product": source.get("product", ""),
                        "sub_product": source.get("sub_product", ""),
                        "issue": source.get("issue", ""),
                        "sub_issue": source.get("sub_issue", ""),
                        "state": source.get("state", ""),
                        "zip_code": source.get("zip_code", ""),
                        "submitted_via": source.get("submitted_via", ""),
                        "company_response": source.get("company_response", ""),
                        "timely": source.get("timely", ""),
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
        date_received_min: Optional[str] = "2021-01-01",
        date_received_max: Optional[str] = None,
        total_limit: Optional[int] = None,
    ) -> None:
        """
        Run the complete data pipeline:
        1. Fetch all queried complaint records from CFPB API since date_received_min.
        2. Filter for BNPL and installment records.
        3. Export both datasets to distinct CSV files.

        Args:
            all_export_path: Filepath for all raw matching complaints CSV.
            filtered_export_path: Filepath for filtered BNPL/installment complaints CSV.
            date_received_min: Lower date cutoff (YYYY-MM-DD), default "2021-01-01".
            date_received_max: Upper date cutoff (YYYY-MM-DD), optional.
            total_limit: Optional cap on records (None pulls all available data).
        """
        logger.info(
            "Starting CFPB data collection pipeline (since %s, limit=%s)...",
            date_received_min or "beginning",
            total_limit or "unlimited",
        )

        # 1. Fetch raw query dataset
        all_records = self.fetch_records_batch(
            search_terms=[
                '"buy now pay later" OR "bnpl" OR "installment"',
                "affirm OR klarna OR afterpay OR sezzle OR uplift OR splitit OR 'zip co'",
            ],
            date_received_min=date_received_min,
            date_received_max=date_received_max,
            total_limit=total_limit,
        )

        # 2. Filter for BNPL and installment loans
        filtered_records = self.filter_bnpl_and_installment(all_records)

        # 3. Export both CSV files
        self.export_to_csv(all_records, all_export_path)
        self.export_to_csv(filtered_records, filtered_export_path)

        logger.info("Pipeline complete. Exported 2 CSV files:")
        logger.info(" - All Queried Records: %s (%d records)", all_export_path, len(all_records))
        logger.info(" - Filtered Records:   %s (%d records)", filtered_export_path, len(filtered_records))


if __name__ == "__main__":
    client = CFPBComplaintsClient()
    client.run_pipeline(
        all_export_path="data/raw/cfpb_all_complaints.csv",
        filtered_export_path="data/raw/cfpb_filtered_bnpl_installment.csv",
        date_received_min="2021-01-01",
        total_limit=None,  # Pull all records since Jan 2021
    )
