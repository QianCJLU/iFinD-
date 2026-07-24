import argparse
import sqlite3
from pathlib import Path


TABLES = [
    "documents",
    "sections",
    "raw_tables",
    "company_profiles",
    "shareholders",
    "people",
    "financing_events",
    "tenders",
    "customers",
    "suppliers",
    "news_events",
    "patents",
    "software_copyrights",
    "trademarks",
    "risk_raw_sections",
    "missing_fields",
    "company_statistics",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Show database summary.")
    parser.add_argument("--db", default="data/ifind_reports.db")
    args = parser.parse_args()
    conn = sqlite3.connect(Path(args.db))
    conn.row_factory = sqlite3.Row
    for table in TABLES:
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        print(f"{table}: {count}")
    print("\nCompanies:")
    for row in conn.execute("SELECT id, company_name, report_time, page_count FROM documents ORDER BY id"):
        print(dict(row))
    print("\nCompany statistics:")
    for row in conn.execute(
        """
        SELECT company_name, patent_application_count, legal_case_count,
               negative_news_count, non_negative_news_count, unknown_news_count, total_news_count,
               negative_news_ratio, top_shareholder, top_shareholding_ratio
        FROM company_statistics ORDER BY company_name
        """
    ):
        print(dict(row))


if __name__ == "__main__":
    main()
