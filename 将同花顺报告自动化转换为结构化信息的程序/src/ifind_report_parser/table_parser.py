from pathlib import Path
import json
import re

import pdfplumber


TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "intersection_tolerance": 5,
}


def extract_tables(path: Path) -> list[dict]:
    out = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            for mode, settings in [("default", None), ("lines", TABLE_SETTINGS)]:
                tables = page.extract_tables(table_settings=settings) if settings else page.extract_tables()
                for idx, table in enumerate(tables or []):
                    normalized = normalize_table(table)
                    if not normalized:
                        continue
                    text = table_to_text(normalized)
                    out.append({
                        "page_no": pno,
                        "table_index": idx,
                        "extraction_mode": mode,
                        "row_count": len(normalized),
                        "column_count": max(len(r) for r in normalized),
                        "rows": normalized,
                        "table_text": text,
                    })
    return dedupe_tables(out)


def words_by_page(path: Path) -> list[dict]:
    pages = []
    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages, 1):
            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False,
            )
            pages.append({"page": idx, "width": page.width, "height": page.height, "words": words})
    return pages


def extract_patents_by_cells(path: Path) -> list[dict]:
    grid_rows = extract_patents_by_grid(path)
    if grid_rows:
        return grid_rows
    rows = []
    for page in words_by_page(path):
        words = page["words"]
        header = find_header(words, ["序号", "专利名称", "申请公布号", "法律状态"])
        if not header:
            continue
        boundaries = infer_boundaries(header, page["width"])
        body = [w for w in words if w["top"] > header["top"] + 8]
        line_groups = group_words_by_line(body)
        records = []
        current = None
        for line in line_groups:
            cells = line_to_cells(line, boundaries)
            joined = "".join(cells)
            if not joined or "序号专利名称" in joined:
                continue
            has_cn = any(re.search(r"CN\d{5,13}[A-Z]?", c) for c in cells)
            starts_record = bool(cells[0].strip().isdigit()) or has_cn
            if starts_record and current:
                records.append(current)
                current = cells
            elif starts_record:
                current = cells
            elif current:
                current = merge_cells(current, cells)
        if current:
            records.append(current)
        for cells in records:
            row = patent_cells_to_record(cells)
            if row and row["publication_no"] != "数据缺失":
                rows.append(row)
    return dedupe_by(rows, "publication_no")


def extract_patents_by_grid(path: Path) -> list[dict]:
    rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables(table_settings=TABLE_SETTINGS) or []:
                if not table or len(table) < 2:
                    continue
                header_text = "".join(clean_cell(c) for c in table[0] if c)
                if "专利名称" not in header_text or "申请公布号" not in header_text:
                    continue
                for row in table[1:]:
                    if not row or not any(row):
                        continue
                    record = patent_grid_row_to_record(row)
                    if record and record["publication_no"] != "数据缺失":
                        rows.append(record)
    return dedupe_by(rows, "publication_no")


def extract_news_by_tables(tables: list[dict]) -> list[dict]:
    rows = []
    for table in matching_tables(tables, ["标题", "发布日期", "情感"]):
        header_idx, mapping = find_header_mapping(table["rows"], {
            "title": "标题",
            "publish_date": "发布日期",
            "importance": "主体重要性",
            "sentiment": "情感",
            "risk_category": "风险类别",
        })
        if header_idx is None:
            continue
        for row in table["rows"][header_idx + 1:]:
            title = get_mapped(row, mapping, "title")
            date = get_mapped(row, mapping, "publish_date")
            if title == "数据缺失" or date == "数据缺失":
                continue
            rows.append({
                "title": title,
                "publish_date": date,
                "importance": get_mapped(row, mapping, "importance"),
                "sentiment": get_mapped(row, mapping, "sentiment"),
                "risk_category": get_mapped(row, mapping, "risk_category"),
                "raw_text": row_to_text(row),
            })
    return dedupe_by(rows, "raw_text")


def extract_customers_by_tables(tables: list[dict]) -> list[dict]:
    rows = []
    for table in matching_tables(tables, ["客户", "销售金额"]):
        header_idx, mapping = find_header_mapping(table["rows"], {
            "customer_name": "客户",
            "sales_ratio": "销售占比",
            "sales_amount_10k": "销售金额",
            "report_period_or_date": "报告期",
            "data_source": "数据来源",
        })
        if header_idx is None:
            continue
        for row in table["rows"][header_idx + 1:]:
            name = get_mapped(row, mapping, "customer_name")
            if name == "数据缺失" or name == "客户":
                continue
            rows.append({
                "customer_name": name,
                "sales_ratio": get_mapped(row, mapping, "sales_ratio"),
                "sales_amount_10k": get_mapped(row, mapping, "sales_amount_10k"),
                "report_period_or_date": get_mapped(row, mapping, "report_period_or_date"),
                "data_source": get_mapped(row, mapping, "data_source"),
                "raw_text": row_to_text(row),
            })
    return dedupe_by(rows, "raw_text")


def extract_suppliers_by_tables(tables: list[dict]) -> list[dict]:
    rows = []
    for table in matching_tables(tables, ["供应商", "采购金额"]):
        header_idx, mapping = find_header_mapping(table["rows"], {
            "supplier_name": "供应商",
            "purchase_ratio": "采购占比",
            "purchase_amount_10k": "采购金额",
            "report_period_or_date": "报告期",
            "data_source": "数据来源",
        })
        if header_idx is None:
            continue
        for row in table["rows"][header_idx + 1:]:
            name = get_mapped(row, mapping, "supplier_name")
            if name == "数据缺失" or name == "供应商":
                continue
            rows.append({
                "supplier_name": name,
                "purchase_ratio": get_mapped(row, mapping, "purchase_ratio"),
                "purchase_amount_10k": get_mapped(row, mapping, "purchase_amount_10k"),
                "report_period_or_date": get_mapped(row, mapping, "report_period_or_date"),
                "data_source": get_mapped(row, mapping, "data_source"),
                "raw_text": row_to_text(row),
            })
    return dedupe_by(rows, "raw_text")


def extract_shareholders_by_tables(tables: list[dict]) -> list[dict]:
    rows = []
    for table in matching_tables(tables, ["股东", "持股比例"]):
        header_idx, mapping = find_header_mapping(table["rows"], {
            "shareholder_name": "股东",
            "shareholding_ratio": "持股比例",
            "subscribed_amount": "持股数额",
            "share_type": "股份类型",
            "shareholder_type": "股东类型",
        })
        if header_idx is None:
            continue
        for row in table["rows"][header_idx + 1:]:
            name = get_mapped(row, mapping, "shareholder_name")
            ratio = get_mapped(row, mapping, "shareholding_ratio")
            if name == "数据缺失" or "股东" in name:
                continue
            rows.append({
                "shareholder_name": name,
                "shareholding_ratio": ratio,
                "subscribed_amount": get_mapped(row, mapping, "subscribed_amount"),
                "share_type": get_mapped(row, mapping, "share_type"),
                "shareholder_type": get_mapped(row, mapping, "shareholder_type"),
                "raw_text": row_to_text(row),
            })
    return dedupe_by(rows, "raw_text")


def extract_financing_by_tables(tables: list[dict]) -> list[dict]:
    rows = []
    for table in matching_tables(tables, ["融资", "投资机构"]):
        header_idx, mapping = find_header_mapping(table["rows"], {
            "event_date": "融资时间",
            "event_name": "事件名称",
            "round_name": "融资轮次",
            "financing_amount": "融资金额",
            "valuation": "估值",
            "investors": "投资机构",
        })
        if header_idx is None:
            continue
        for row in table["rows"][header_idx + 1:]:
            raw = row_to_text(row)
            if "融资" not in raw and "投资" not in raw:
                continue
            rows.append({
                "event_date": get_mapped(row, mapping, "event_date"),
                "event_name": get_mapped(row, mapping, "event_name"),
                "round_name": get_mapped(row, mapping, "round_name"),
                "financing_amount": get_mapped(row, mapping, "financing_amount"),
                "valuation": get_mapped(row, mapping, "valuation"),
                "investors": get_mapped(row, mapping, "investors"),
                "raw_text": raw,
            })
    return dedupe_by(rows, "raw_text")


def extract_tenders_by_tables(tables: list[dict]) -> list[dict]:
    rows = []
    for table in matching_tables(tables, ["项目名称"]):
        text = table["table_text"]
        if "中标" not in text and "招标" not in text and "采购单位" not in text:
            continue
        tender_type = "中标公告" if "中标单位" in text or "中标金额" in text else "招标公告"
        header_idx, mapping = find_header_mapping(table["rows"], {
            "project_name": "项目名称",
            "publish_date": "发布日期",
            "region": "省份地区",
            "counterparty": "招标/采购单位",
            "amount_10k": "金额",
        })
        if header_idx is None:
            continue
        for row in table["rows"][header_idx + 1:]:
            project = get_mapped(row, mapping, "project_name")
            if project == "数据缺失":
                continue
            rows.append({
                "tender_type": tender_type,
                "project_name": project,
                "publish_date": get_mapped(row, mapping, "publish_date"),
                "region": get_mapped(row, mapping, "region"),
                "counterparty": get_mapped(row, mapping, "counterparty"),
                "amount_10k": get_mapped(row, mapping, "amount_10k"),
                "raw_text": row_to_text(row),
            })
    return dedupe_by(rows, "raw_text")


def patent_grid_row_to_record(row: list[str | None]) -> dict | None:
    cells = [(c or "") for c in row]
    if len(cells) < 8:
        return None
    while len(cells) < 9:
        cells.append("")
    raw_text = " | ".join(clean_cell(c) for c in cells)
    if "CN" not in raw_text:
        return None
    publication_no = normalize_joined_code(cells[2])
    if publication_no == "数据缺失":
        publication_no = first_match(raw_text, r"CN\d{5,13}[A-Z]?") or "数据缺失"
    return {
        "patent_name": clean_cell(cells[1]),
        "publication_no": publication_no,
        "patent_type": clean_cell(cells[3]),
        "inventors": clean_cell(cells[4]),
        "agency": clean_cell(cells[5]),
        "abstract": clean_cell(cells[6]),
        "legal_status": clean_cell(cells[7]),
        "publication_date": normalize_joined_date(cells[8]),
        "raw_text": raw_text,
    }


def find_header(words: list[dict], labels: list[str]) -> dict | None:
    for w in words:
        if w["text"] == labels[0]:
            same_line = [x for x in words if abs(x["top"] - w["top"]) < 5]
            text = "".join(x["text"] for x in sorted(same_line, key=lambda x: x["x0"]))
            if all(label in text for label in labels[1:]):
                label_positions = {}
                for label in ["序号", "专利名称", "申请公布号", "专利类型", "发明人", "代理机构", "摘要", "法律状态", "申请公布日"]:
                    hit = next((x for x in same_line if x["text"] == label), None)
                    if hit:
                        label_positions[label] = (hit["x0"], hit["x1"])
                return {"top": w["top"], "labels": label_positions}
    return None


def infer_boundaries(header: dict, page_width: float) -> list[tuple[str, float, float]]:
    ordered = sorted(header["labels"].items(), key=lambda item: item[1][0])
    columns = []
    for idx, (name, (x0, x1)) in enumerate(ordered):
        left = 0 if idx == 0 else (ordered[idx - 1][1][1] + x0) / 2
        right = page_width if idx == len(ordered) - 1 else (x1 + ordered[idx + 1][1][0]) / 2
        columns.append((name, left, right))
    return columns


def group_words_by_line(words: list[dict]) -> list[list[dict]]:
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    for word in sorted_words:
        if not lines or abs(lines[-1][0]["top"] - word["top"]) > 4:
            lines.append([word])
        else:
            lines[-1].append(word)
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def line_to_cells(line: list[dict], boundaries: list[tuple[str, float, float]]) -> list[str]:
    cells = []
    for _, left, right in boundaries:
        cell_words = [w["text"] for w in line if w["x0"] >= left - 1 and w["x0"] < right + 1]
        cells.append("".join(cell_words))
    return cells


def merge_cells(base: list[str], more: list[str]) -> list[str]:
    width = max(len(base), len(more))
    base = base + [""] * (width - len(base))
    more = more + [""] * (width - len(more))
    return [(base[i] + more[i]).strip() for i in range(width)]


def patent_cells_to_record(cells: list[str]) -> dict | None:
    if len(cells) < 6:
        return None
    cn_text = "".join(cells)
    publication_no = first_match(cn_text, r"CN\d{5,13}[A-Z]?") or "数据缺失"
    if re.fullmatch(r"CN\d{5,8}", publication_no):
        suffix_text = cn_text.split(publication_no, 1)[-1]
        suffix = first_match(suffix_text, r"\d{3,6}[A-Z]")
        if suffix:
            publication_no += suffix
    name = pick_cell(cells, 1)
    if name == "数据缺失":
        name = first_match(cn_text, r"(一种[\u4e00-\u9fa5A-Za-z0-9、，,（）()]{4,80})") or "数据缺失"
    legal_status = next((x for x in ["有效-授权", "有效", "授权", "实审", "公开", "驳回"] if x in cn_text), "数据缺失")
    return {
        "patent_name": clean_cell(name),
        "publication_no": publication_no,
        "patent_type": clean_cell(pick_cell(cells, 3)),
        "inventors": clean_cell(pick_cell(cells, 4)),
        "agency": clean_cell(pick_cell(cells, 5)),
        "abstract": clean_cell(pick_cell(cells, 6)),
        "legal_status": legal_status,
        "publication_date": extract_date(cn_text),
        "raw_text": " | ".join(clean_cell(c) for c in cells),
    }


def pick_cell(cells: list[str], idx: int) -> str:
    if idx >= len(cells):
        return "数据缺失"
    return cells[idx] if cells[idx].strip() else "数据缺失"


def clean_cell(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    text = text.replace("/", "")
    return text if text and text != "--" else "数据缺失"


def normalize_table(table: list[list[str | None]]) -> list[list[str]]:
    rows = []
    for row in table:
        cleaned = [clean_cell(cell or "") for cell in row]
        if any(cell != "数据缺失" for cell in cleaned):
            rows.append(cleaned)
    return rows


def table_to_text(rows: list[list[str]]) -> str:
    return "\n".join(row_to_text(row) for row in rows)


def row_to_text(row: list[str]) -> str:
    return " | ".join(cell for cell in row if cell and cell != "数据缺失")


def matching_tables(tables: list[dict], keywords: list[str]) -> list[dict]:
    return [table for table in tables if all(keyword in table["table_text"] for keyword in keywords)]


def find_header_mapping(rows: list[list[str]], fields: dict[str, str]) -> tuple[int | None, dict[str, int]]:
    for idx, row in enumerate(rows[:5]):
        row_text = "".join(row)
        if not any(label in row_text for label in fields.values()):
            continue
        mapping = {}
        for field, label in fields.items():
            for col_idx, cell in enumerate(row):
                if label in cell:
                    mapping[field] = col_idx
                    break
        if mapping:
            return idx, mapping
    return None, {}


def get_mapped(row: list[str], mapping: dict[str, int], field: str) -> str:
    idx = mapping.get(field)
    if idx is None or idx >= len(row):
        return "数据缺失"
    return clean_cell(row[idx])


def dedupe_tables(tables: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for table in tables:
        key = (table["page_no"], table["table_text"][:500])
        if key not in seen:
            seen.add(key)
            out.append(table)
    return out


def normalize_joined_code(text: str) -> str:
    text = clean_cell(text)
    if text == "数据缺失":
        return text
    match = re.search(r"CN\d{5,13}[A-Z]?", text)
    return match.group(0) if match else "数据缺失"


def normalize_joined_date(text: str) -> str:
    text = clean_cell(text)
    if text == "数据缺失":
        return text
    match = re.search(r"\d{4}-\d{1,2}-\d{1,2}", text)
    return match.group(0) if match else "数据缺失"


def first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(0) if match else None


def extract_date(text: str) -> str:
    # Prefer a full yyyy-mm-dd date. iFinD PDF sometimes leaves a trailing page/line digit nearby.
    m = re.search(r"\d{4}-\d{1,2}-\d{1,2}", text)
    if not m:
        return "数据缺失"
    value = m.group(0)
    parts = value.split("-")
    if len(parts[-1]) == 1:
        rest = text[m.end() : m.end() + 8]
        extra = re.search(r"\d", rest)
        if extra:
            value += extra.group(0)
    return value


def dedupe_by(rows: list[dict], key: str) -> list[dict]:
    out = []
    seen = set()
    for row in rows:
        value = row.get(key)
        if value and value not in seen:
            seen.add(value)
            out.append(row)
    return out
