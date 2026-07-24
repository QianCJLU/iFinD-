import argparse
from pathlib import Path

from .db import connect
from .loader import insert_report
from .parser import parse_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse iFinD enterprise PDF reports into SQLite.")
    parser.add_argument("--source", help="Directory containing iFinD PDF reports.")
    parser.add_argument("--db", default="data/ifind_reports.db", help="SQLite database path.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of PDFs to parse.")
    args = parser.parse_args()

    source_text = args.source
    if not source_text:
        source_text = input("请输入iFinD企业报告PDF所在文件夹路径：").strip().strip('"')
    source = Path(source_text)
    if not source.exists() or not source.is_dir():
        raise SystemExit(f"报告文件夹不存在或不是目录：{source}")
    db_path = Path(args.db)
    pdfs = sorted(source.rglob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    conn = connect(db_path)
    print(f"Found {len(pdfs)} PDF report(s).")
    for path in pdfs:
        print(f"Parsing: {path}")
        report = parse_pdf(path)
        doc_id = insert_report(conn, report)
        print(
            f"  -> document_id={doc_id}, company={report.company_name}, "
            f"pages={report.page_count}, patents={len(report.patents)}, news={len(report.news_events)}"
        )
    print(f"Done. SQLite database: {db_path.resolve()}")


if __name__ == "__main__":
    main()
