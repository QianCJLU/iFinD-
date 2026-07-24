import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pdfplumber

from .table_parser import (
    extract_customers_by_tables,
    extract_financing_by_tables,
    extract_news_by_tables,
    extract_patents_by_cells,
    extract_shareholders_by_tables,
    extract_suppliers_by_tables,
    extract_tenders_by_tables,
    extract_tables,
)


MISSING_FIELD_RULES = {
    "技术风险": [
        "核心技术性能benchmark",
        "第三方测试或认证结果",
        "论文清单",
        "论文引用与转化证据",
        "研发费用",
        "研发项目清单",
        "TRL阶段证据",
        "客户验收记录",
        "核心技术供应商或算力供应商",
        "安全测试通过率",
    ],
    "财务风险": [
        "资产总额",
        "营业总收入",
        "主营业务收入",
        "净利润",
        "负债总额",
        "经营活动现金流",
        "货币资金",
    ],
}


SECTION_RE = re.compile(r"^(?P<no>\d+(?:\.\d+)?)(?:[-—]\d{4}年报)?\s+(?P<title>.+)$")
DATE_RE = re.compile(r"\d{4}[-年]\d{1,2}(?:[-月]\d{1,2}日?)?")


@dataclass
class ParsedReport:
    path: Path
    sha256: str
    company_name: str
    report_time: str
    page_count: int
    pages: list[dict]
    full_text: str
    raw_tables: list[dict]
    sections: list[dict]
    profile: dict
    shareholders: list[dict]
    people: list[dict]
    financing_events: list[dict]
    tenders: list[dict]
    customers: list[dict]
    suppliers: list[dict]
    news_events: list[dict]
    patents: list[dict]
    software_copyrights: list[dict]
    trademarks: list[dict]
    risk_raw_sections: list[dict]
    missing_fields: list[dict]


def parse_pdf(path: Path) -> ParsedReport:
    pages = extract_pages(path)
    raw_tables = extract_tables(path)
    full_text = "\n".join(f"---PAGE {p['page']}---\n{p['text']}" for p in pages)
    first_lines = [x.strip() for x in pages[0]["text"].splitlines() if x.strip()] if pages else []
    company_name = first_lines[1] if len(first_lines) > 1 else "数据缺失"
    report_time = first_lines[2].replace("报告生成时间", "").replace(":", "").strip() if len(first_lines) > 2 else "数据缺失"
    sections = split_sections(pages)
    sec_map = {s["title"]: s for s in sections}
    profile_text = get_section_text_by_prefix(sections, "1.1")
    profile = parse_profile(profile_text, company_name)
    return ParsedReport(
        path=path,
        sha256=file_sha256(path),
        company_name=company_name,
        report_time=report_time,
        page_count=len(pages),
        pages=pages,
        full_text=full_text,
        raw_tables=raw_tables,
        sections=sections,
        profile=profile,
        shareholders=parse_records_with_fallback(extract_shareholders_by_tables(raw_tables), parse_shareholders(get_section_text_by_prefix(sections, "1.2"))),
        people=parse_people(get_section_text_by_prefix(sections, "1.3") + "\n" + get_section_text_by_prefix(sections, "1.4") + "\n" + get_section_text_by_prefix(sections, "6.1")),
        financing_events=parse_records_with_fallback(extract_financing_by_tables(raw_tables), parse_financing(get_section_text_by_prefix(sections, "5.6"))),
        tenders=parse_records_with_fallback(extract_tenders_by_tables(raw_tables), parse_tenders(get_section_text_by_prefix(sections, "4.1"), "招标公告") + parse_tenders(get_section_text_by_prefix(sections, "4.2"), "中标公告")),
        customers=parse_records_with_fallback(extract_customers_by_tables(raw_tables), parse_customers(get_section_text_by_prefix(sections, "4.3"))),
        suppliers=parse_records_with_fallback(extract_suppliers_by_tables(raw_tables), parse_suppliers(get_section_text_by_prefix(sections, "4.4"))),
        news_events=parse_records_with_fallback(extract_news_by_tables(raw_tables), parse_news(get_section_text_by_prefix(sections, "6.5"))),
        patents=parse_patents_with_cell_fallback(path, get_section_text_by_prefix(sections, "7.2")),
        software_copyrights=parse_software_copyrights(get_section_text_by_prefix(sections, "7.4")),
        trademarks=parse_trademarks(get_section_text_by_prefix(sections, "7.1")),
        risk_raw_sections=parse_risk_sections(sections),
        missing_fields=build_missing_fields(profile, sec_map),
    )


def parse_records_with_fallback(primary: list[dict], fallback: list[dict]) -> list[dict]:
    return primary if primary else fallback


def extract_pages(path: Path) -> list[dict]:
    pages = []
    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages, 1):
            pages.append({"page": idx, "text": clean_extracted_text(page.extract_text() or "")})
    return pages


def clean_extracted_text(text: str) -> str:
    """Fix common iFinD PDF line-breaking artifacts before rule parsing."""
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    # CN publication numbers are often split by a table line break: CN12031 / 5864A.
    text = re.sub(
        r"CN\s*((?:\d\s*){6,13})([A-Z])",
        lambda m: "CN" + re.sub(r"\s+", "", m.group(1)) + m.group(2),
        text,
    )
    # Dates may be split at line endings: 2025-07-1 / 5.
    text = re.sub(r"(\d{4}-\d{1,2}-\d)\s*\n\s*(\d)\b", r"\1\2", text)
    # Registration numbers may be split in software copyright rows.
    text = re.sub(
        r"(\d{4}SR\d+)\s*\n\s*(\d+)",
        lambda m: m.group(1) + m.group(2),
        text,
    )
    return text


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def split_sections(pages: list[dict]) -> list[dict]:
    sections = []
    current = None
    for page in pages:
        for line in page["text"].splitlines():
            raw = line.strip()
            if not raw:
                continue
            match = SECTION_RE.match(raw)
            is_heading = bool(match) and len(raw) < 80 and not raw.startswith(("1 ", "2 ", "3 ", "4 ", "5 ", "6 ", "7 ", "8 ", "9 ")) or bool(match and "." in match.group("no"))
            if is_heading:
                if current:
                    current["page_end"] = page["page"]
                    sections.append(current)
                current = {
                    "section_no": match.group("no"),
                    "title": match.group("title").strip(),
                    "page_start": page["page"],
                    "page_end": page["page"],
                    "content": raw + "\n",
                }
            elif current:
                current["content"] += raw + "\n"
                current["page_end"] = page["page"]
    if current:
        sections.append(current)
    return sections


def get_section_text_by_prefix(sections: list[dict], prefix: str) -> str:
    for section in sections:
        if section["section_no"] == prefix:
            return section["content"]
    return ""


def normalize_missing(value: str | None) -> str:
    if value is None:
        return "数据缺失"
    value = value.strip()
    return value if value and value != "--" else "数据缺失"


def find_after(text: str, label: str, stop_labels: list[str]) -> str:
    idx = text.find(label)
    if idx < 0:
        return "数据缺失"
    start = idx + len(label)
    end = len(text)
    for stop in stop_labels:
        s = text.find(stop, start)
        if s != -1:
            end = min(end, s)
    return normalize_missing(text[start:end].replace("\n", " ").strip())


def parse_profile(text: str, company_name: str) -> dict:
    fields = {
        "company_name": company_name,
        "former_name": find_after(text, "曾用名", ["法定代表人"]),
        "legal_representative": clean_person_name(find_after(text, "法定代表人", ["统一社会信用代码", "组织机构代码"])),
        "unified_social_credit_code": find_after(text, "统一社会信用代码", ["组织机构代码"]),
        "organization_code": find_after(text, "组织机构代码", ["工商注册号"]),
        "registration_no": find_after(text, "工商注册号", ["企业性质"]),
        "company_nature": find_after(text, "企业性质", ["登记状态"]),
        "registration_status": find_after(text, "登记状态", ["公司类型"]),
        "company_type": find_after(text, "公司类型", ["注册资本"]),
        "registered_capital": find_after(text, "注册资本", ["实缴资本"]),
        "paid_in_capital": find_after(text, "实缴资本", ["成立日期"]),
        "establishment_date": find_after(text, "成立日期", ["核准日期"]),
        "approval_date": find_after(text, "核准日期", ["人员规模"]),
        "staff_size": find_after(text, "人员规模", ["企业规模"]),
        "enterprise_size": find_after(text, "企业规模", ["国标行业"]),
        "region": find_after(text, "所属地区", ["曾用名", "法定代表人"]),
        "national_industry": find_after(text, "国标行业", ["战略性新兴产业"]),
        "strategic_emerging_industry": find_after(text, "战略性新兴产业", ["营业期限", "登记机关"]),
        "business_scope": find_after(text, "经营范围", ["英文名", "1.2"]),
        "registered_address": find_after(text, "注册地址", ["通信地址"]),
        "communication_address": find_after(text, "通信地址", ["经营范围"]),
    }
    return {k: normalize_missing(v) for k, v in fields.items()}


def row_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_shareholders(text: str) -> list[dict]:
    rows = []
    for line in row_lines(text):
        m = re.match(r"^(\d+)\s+(.+?)\s+([\d.]+)\s+(.+)$", line)
        if m and "序号" not in line:
            rows.append({
                "shareholder_name": m.group(2).strip(),
                "shareholding_ratio": m.group(3).strip(),
                "subscribed_amount": m.group(4).strip(),
                "share_type": "数据缺失",
                "shareholder_type": "数据缺失",
                "raw_text": line,
            })
    return rows


def parse_people(text: str) -> list[dict]:
    rows = []
    seen = set()
    for line in row_lines(text):
        if "序号" in line or "姓名" in line:
            continue
        m = re.match(r"^\d+\s+([\u4e00-\u9fa5·]{2,8})\s+(.*)$", line)
        if m:
            name = m.group(1)
            rest = m.group(2)
            if name not in seen:
                seen.add(name)
                rows.append({
                    "name": name,
                    "role": rest[:80] if rest else "数据缺失",
                    "gender": "男" if "男" in rest else ("女" if "女" in rest else "数据缺失"),
                    "education": next((x for x in ["博士", "硕士", "本科", "大专"] if x in rest), "数据缺失"),
                    "birth_year": (re.search(r"\b(19\d{2}|20\d{2})\b", rest).group(1) if re.search(r"\b(19\d{2}|20\d{2})\b", rest) else "数据缺失"),
                    "raw_text": line,
                })
    return rows


def parse_financing(text: str) -> list[dict]:
    rows = []
    for line in row_lines(text):
        if "融资" in line and ("轮" in line or "投资" in line):
            rows.append({
                "event_date": first_date(line),
                "event_name": line[:120],
                "round_name": first_match(line, r"([A-Z][+\w]*轮|天使轮|Pre-[A-Z]轮)") or "数据缺失",
                "financing_amount": first_match(line, r"(\d+(?:\.\d+)?亿[^，。\s]*)") or "数据缺失",
                "valuation": first_match(line, r"估值[^\s，。]*") or "数据缺失",
                "investors": line,
                "raw_text": line,
            })
    return rows


def parse_tenders(text: str, tender_type: str) -> list[dict]:
    rows = []
    for line in row_lines(text):
        if first_date(line) and "序号" not in line:
            rows.append({
                "tender_type": tender_type,
                "project_name": line[:120],
                "publish_date": first_date(line),
                "region": "数据缺失",
                "counterparty": "数据缺失",
                "amount_10k": first_match(line, r"(\d+(?:\.\d+)?)\s*$") or "数据缺失",
                "raw_text": line,
            })
    return rows


def parse_customers(text: str) -> list[dict]:
    return parse_counterparty(text, "customer")


def parse_suppliers(text: str) -> list[dict]:
    return parse_counterparty(text, "supplier")


def parse_counterparty(text: str, kind: str) -> list[dict]:
    rows = []
    for line in row_lines(text):
        if first_date(line) and "序号" not in line:
            name = re.sub(r"^\d+\s+", "", line).split()[0] if line.split() else "数据缺失"
            record = {
                f"{kind}_name": name,
                "raw_text": line,
            }
            if kind == "customer":
                record.update({"sales_ratio": "数据缺失", "sales_amount_10k": first_match(line, r"\s(\d+(?:\.\d+)?)\s+\d{4}-") or "数据缺失", "report_period_or_date": first_date(line), "data_source": "数据缺失"})
            else:
                record.update({"purchase_ratio": "数据缺失", "purchase_amount_10k": first_match(line, r"\s(\d+(?:\.\d+)?)\s+\d{4}-") or "数据缺失", "report_period_or_date": first_date(line), "data_source": "数据缺失"})
            rows.append(record)
    return rows


def parse_news(text: str) -> list[dict]:
    rows = []
    for line in row_lines(text):
        date = first_date(line)
        if date and "序号" not in line:
            rows.append({
                "title": re.sub(r"^\d+\s+", "", line).replace(date, "").strip()[:200],
                "publish_date": date,
                "importance": "重要" if "重要" in line else "数据缺失",
                "sentiment": "负面" if "负面" in line else ("非负" if "非负" in line else "数据缺失"),
                "risk_category": first_match(line, r"([\u4e00-\u9fa5]+预警[^ ]*)") or "数据缺失",
                "raw_text": line,
            })
    return rows


def parse_patents(text: str) -> list[dict]:
    rows = []
    matches = list(re.finditer(r"CN\s*\d{5,13}\s*[A-Z]?", text))
    for idx, match in enumerate(matches):
        start = max(0, match.start() - 700)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else min(len(text), match.end() + 900)
        chunk = text[start:end].strip()
        publication_no = clean_cn_publication_no(match.group(0))
        if re.fullmatch(r"CN\d{5,8}", publication_no):
            suffix = first_match(chunk[match.end() - start :], r"\b(\d{3,6}[A-Z])\b")
            if suffix:
                publication_no = publication_no + suffix
        rows.append({
            "patent_name": extract_patent_name(chunk, publication_no),
            "publication_no": publication_no,
            "patent_type": "发明专利" if "发明专" in chunk else ("发明授权" if "发明授" in chunk else ("外观设计" if "外观设" in chunk else ("实用新型" if "实用新型" in chunk else "数据缺失"))),
            "inventors": "数据缺失",
            "agency": first_match(chunk, r"[\u4e00-\u9fa5]+专利(?:事务所|代理)[^\s]*") or "数据缺失",
            "abstract": chunk[:600],
            "legal_status": "有效-授权" if "有效-授" in chunk else ("实审" if "实审" in chunk else ("授权" if "授权" in chunk else "数据缺失")),
            "publication_date": first_date(chunk),
            "raw_text": chunk,
        })
    return rows


def parse_patents_with_cell_fallback(path: Path, text: str) -> list[dict]:
    cell_rows = extract_patents_by_cells(path)
    if cell_rows:
        return cell_rows
    return parse_patents(text)


def extract_patent_name(chunk: str, publication_no: str) -> str:
    compact = re.sub(r"\s+", " ", chunk)
    patterns = [
        r"(一种[\u4e00-\u9fa5A-Za-z0-9、，,（）()]{4,80})",
        r"([\u4e00-\u9fa5A-Za-z0-9、，,（）()]{4,80}(?:方法|系统|装置|设备|机器人|芯片|介质|产品))",
    ]
    for pattern in patterns:
        m = re.search(pattern, compact)
        if m:
            return m.group(1).strip(" ，,。")
    before = compact.split(publication_no[:7])[0]
    return before[-80:].strip(" ，,。") if before else "数据缺失"


def parse_software_copyrights(text: str) -> list[dict]:
    rows = []
    for line in row_lines(text):
        if first_date(line) and ("V" in line or "SR" in line):
            rows.append({
                "approval_date": first_date(line),
                "software_full_name": re.sub(r"^\d+\s+", "", line).replace(first_date(line), "").strip()[:120],
                "software_short_name": "数据缺失",
                "registration_no": first_match(line, r"\d{4}SR\d+") or "数据缺失",
                "category_no": "数据缺失",
                "version_no": first_match(line, r"V\d+(?:\.\d+)*") or "数据缺失",
                "raw_text": line,
            })
    return rows


def parse_trademarks(text: str) -> list[dict]:
    rows = []
    for line in row_lines(text):
        if re.search(r"\d{8}", line) and first_date(line):
            rows.append({
                "trademark_name": re.sub(r"^\d+\s+", "", line).split()[0],
                "registration_no": first_match(line, r"\d{8}") or "数据缺失",
                "status": next((x for x in ["注册公告", "初审公告", "实质审查", "驳回"] if x in line), "数据缺失"),
                "category": first_match(line, r"\d+类[^\s]*") or "数据缺失",
                "application_date": first_date(line),
                "raw_text": line,
            })
    return rows


def parse_risk_sections(sections: list[dict]) -> list[dict]:
    rows = []
    for section in sections:
        title = section["title"]
        if any(k in title for k in ["司法", "开庭", "裁判", "经营风险", "行政处罚", "经营异常", "被执行", "失信"]):
            rows.append({"risk_type": title, "section_title": f"{section['section_no']} {title}", "content": section["content"]})
    return rows


def build_missing_fields(profile: dict, section_map: dict) -> list[dict]:
    rows = []
    for module, fields in MISSING_FIELD_RULES.items():
        for field in fields:
            rows.append({"module": module, "field_name": field, "status": "数据缺失", "reason": "iFinD PDF报告未提供该字段或当前规则无法稳定抽取"})
    for field in ["asset_total", "revenue", "net_profit", "liability_total"]:
        pass
    return rows


def split_numbered_records(text: str) -> list[str]:
    lines = row_lines(text)
    chunks = []
    current = []
    for line in lines:
        if re.match(r"^\d+\s+", line) and current:
            chunks.append(" ".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(" ".join(current))
    return chunks


def first_date(text: str) -> str:
    m = DATE_RE.search(text)
    return m.group(0) if m else "数据缺失"


def first_match(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    if not m:
        return None
    return m.group(1) if m.lastindex else m.group(0)


def clean_person_name(value: str) -> str:
    value = normalize_missing(value)
    if value == "数据缺失":
        return value
    m = re.search(r"[\u4e00-\u9fa5·]{2,5}", value)
    return m.group(0) if m else value.split()[0]


def clean_cn_publication_no(value: str) -> str:
    value = normalize_missing(value)
    if value == "数据缺失":
        return value
    return re.sub(r"\s+", "", value)


def parse_count_from_section(text: str) -> int:
    count = 0
    for line in row_lines(text):
        if re.match(r"^\d+\s+", line) and "序号" not in line:
            count += 1
    return count


def parsed_at() -> str:
    return datetime.now().isoformat(timespec="seconds")
