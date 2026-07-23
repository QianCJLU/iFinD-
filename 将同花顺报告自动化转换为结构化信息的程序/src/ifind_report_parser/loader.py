import sqlite3
from pathlib import Path

from .db import clear_document
from .parser import ParsedReport, parsed_at


def insert_report(conn: sqlite3.Connection, report: ParsedReport) -> int:
    clear_document(conn, str(report.path))
    cur = conn.execute(
        """
        INSERT INTO documents
        (source_path, file_name, parent_folder, sha256, company_name, report_time, page_count, full_text, parsed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(report.path),
            report.path.name,
            report.path.parent.name,
            report.sha256,
            report.company_name,
            report.report_time,
            report.page_count,
            report.full_text,
            parsed_at(),
        ),
    )
    doc_id = cur.lastrowid
    for section in report.sections:
        conn.execute(
            "INSERT INTO sections (document_id, section_no, title, page_start, page_end, content) VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, section["section_no"], section["title"], section["page_start"], section["page_end"], section["content"]),
        )
    profile = report.profile
    conn.execute(
        """
        INSERT INTO company_profiles
        (document_id, company_name, former_name, legal_representative, unified_social_credit_code,
         organization_code, registration_no, company_nature, registration_status, company_type,
         registered_capital, paid_in_capital, establishment_date, approval_date, staff_size,
         enterprise_size, region, national_industry, strategic_emerging_industry, business_scope,
         registered_address, communication_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            profile.get("company_name"),
            profile.get("former_name"),
            profile.get("legal_representative"),
            profile.get("unified_social_credit_code"),
            profile.get("organization_code"),
            profile.get("registration_no"),
            profile.get("company_nature"),
            profile.get("registration_status"),
            profile.get("company_type"),
            profile.get("registered_capital"),
            profile.get("paid_in_capital"),
            profile.get("establishment_date"),
            profile.get("approval_date"),
            profile.get("staff_size"),
            profile.get("enterprise_size"),
            profile.get("region"),
            profile.get("national_industry"),
            profile.get("strategic_emerging_industry"),
            profile.get("business_scope"),
            profile.get("registered_address"),
            profile.get("communication_address"),
        ),
    )
    bulk_insert(conn, "shareholders", doc_id, report.shareholders)
    bulk_insert(conn, "people", doc_id, report.people)
    bulk_insert(conn, "financing_events", doc_id, report.financing_events)
    bulk_insert(conn, "tenders", doc_id, report.tenders)
    bulk_insert(conn, "customers", doc_id, report.customers)
    bulk_insert(conn, "suppliers", doc_id, report.suppliers)
    bulk_insert(conn, "news_events", doc_id, report.news_events)
    bulk_insert(conn, "patents", doc_id, report.patents)
    bulk_insert(conn, "software_copyrights", doc_id, report.software_copyrights)
    bulk_insert(conn, "trademarks", doc_id, report.trademarks)
    bulk_insert(conn, "risk_raw_sections", doc_id, report.risk_raw_sections)
    bulk_insert(conn, "missing_fields", doc_id, report.missing_fields)
    insert_company_statistics(conn, doc_id, report)
    conn.commit()
    return doc_id


def bulk_insert(conn: sqlite3.Connection, table: str, doc_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    for row in rows:
        data = {"document_id": doc_id, **row}
        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        conn.execute(sql, [data[c] for c in cols])


def insert_company_statistics(conn: sqlite3.Connection, doc_id: int, report: ParsedReport) -> None:
    negative = sum(1 for item in report.news_events if item.get("sentiment") == "负面")
    non_negative = sum(1 for item in report.news_events if item.get("sentiment") == "非负")
    total_news = len(report.news_events)
    unknown_news = max(0, total_news - negative - non_negative)
    legal_case_count = estimate_legal_case_count(report)
    top_shareholder, top_ratio = top_shareholder_info(report.shareholders)
    shareholder_amount_summary = summarize_shareholder_amounts(report.shareholders)
    external_investment_count, external_investment_summary = summarize_external_investments(report.sections)
    missing_summary = summarize_missing(report.missing_fields)
    conn.execute(
        """
        INSERT INTO company_statistics
        (document_id, company_name, patent_application_count, legal_case_count,
         negative_news_count, non_negative_news_count, unknown_news_count, total_news_count,
         negative_news_ratio, non_negative_news_ratio, shareholder_count,
         top_shareholder, top_shareholding_ratio, shareholder_amount_summary,
         external_investment_count, external_investment_summary, missing_summary, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            report.company_name,
            len(report.patents),
            legal_case_count,
            negative,
            non_negative,
            unknown_news,
            total_news,
            round(negative / total_news, 4) if total_news else None,
            round(non_negative / total_news, 4) if total_news else None,
            len(report.shareholders),
            top_shareholder,
            top_ratio,
            shareholder_amount_summary,
            external_investment_count,
            external_investment_summary,
            missing_summary,
            parsed_at(),
        ),
    )


def estimate_legal_case_count(report: ParsedReport) -> int:
    keywords = ["案号", "案件编号", "开庭公告", "裁判文书", "民初", "执行"]
    lines = []
    for section in report.risk_raw_sections:
        if any(k in section.get("section_title", "") for k in ["司法", "开庭", "裁判", "诉讼", "执行"]):
            lines.extend(section.get("content", "").splitlines())
    count = 0
    seen = set()
    for line in lines:
        if any(k in line for k in keywords) and "序号" not in line:
            key = line.strip()[:120]
            if key and key not in seen:
                seen.add(key)
                count += 1
    return count


def top_shareholder_info(shareholders: list[dict]) -> tuple[str, float | None]:
    best_name = "数据缺失"
    best_ratio = None
    for item in shareholders:
        try:
            ratio = float(item.get("shareholding_ratio") or "")
        except ValueError:
            continue
        if best_ratio is None or ratio > best_ratio:
            best_ratio = ratio
            best_name = item.get("shareholder_name") or "数据缺失"
    return best_name, best_ratio


def summarize_shareholder_amounts(shareholders: list[dict]) -> str:
    if not shareholders:
        return "数据缺失"
    parts = []
    for item in shareholders[:10]:
        name = item.get("shareholder_name") or "数据缺失"
        ratio = item.get("shareholding_ratio") or "数据缺失"
        amount = item.get("subscribed_amount") or "数据缺失"
        parts.append(f"{name}: 持股{ratio}%, 金额/股数={amount}")
    return "；".join(parts)


def summarize_external_investments(sections: list[dict]) -> tuple[int, str]:
    target_sections = [s for s in sections if s.get("section_no") in {"1.7", "1.10", "1.11", "4.5"}]
    if not target_sections:
        return 0, "数据缺失"
    snippets = []
    count_keys = set()
    count = 0
    for section in target_sections:
        for line in section.get("content", "").splitlines():
            line = line.strip()
            if not line or "序号" in line:
                continue
            if "%" in line or "万" in line or "股权" in line or "投资" in line:
                snippets.append(line[:180])
            if section.get("section_no") in {"1.7", "1.10"} and any(status in line for status in ["存续", "注销", "开业", "在业"]):
                key = line[:80]
                if key not in count_keys:
                    count_keys.add(key)
                    count += 1
    if not snippets:
        return 0, "数据缺失"
    return count, "；".join(snippets[:20])


def summarize_missing(rows: list[dict]) -> str:
    if not rows:
        return "无"
    by_module = {}
    for row in rows:
        by_module.setdefault(row.get("module", "未分类"), 0)
        by_module[row.get("module", "未分类")] += 1
    return "；".join(f"{k}:{v}项" for k, v in by_module.items())
