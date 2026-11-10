"""Backend của giao diện xưng hô, dùng chung dữ liệu với nhanvat_tool."""
import hashlib
import json
import os
import re
import tempfile
from argparse import Namespace
from pathlib import Path

from openpyxl import load_workbook

import nhanvat_tool


def review(folder, glossary):
    folder = Path(folder)
    path = folder / "entities.json"
    if not path.is_file():
        return {"characters": [], "ambiguous": [], "revision": _revision(folder, glossary), "chapters": 0,
                "report": "", "reports": [str(p) for p in folder.glob("*分析报告.md")]}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    matches, conflicts = nhanvat_tool.match_glossary_characters(data["characters"], Path(glossary))
    items = []
    for char in data["characters"]:
        calls = char.get("calls", [])
        matched = matches.get(char["key"])
        candidates = sorted({entry["name"] for entry in char.get("latin_candidates", [])
                             if float(entry.get("confidence") or 0) >= 0.90})
        items.append({
            "key": char["key"], "name": char["canonical_zh"],
            "calls": [{"name": c["name"], "level": c["level"], "count": c["count"]} for c in calls],
            "reader_aliases": char.get("reader_aliases", []), "descriptions": char.get("descriptions", []),
            "chapters": char.get("chapter_count", 0), "match": char.get("match_level", ""),
            "gender": char.get("gender", ""), "gender_source": char.get("gender_source", ""),
            "dest": matched["dest"] if matched else candidates[0] if len(candidates) == 1 else "",
            "approved": matched is not None, "approval_source": matched["origin"] if matched else "",
            "conflict": conflicts.get(char["key"], ""),
        })
    items.sort(key=lambda item: (-item["chapters"], item["name"]))
    return {"characters": items, "ambiguous": data.get("ambiguous", []),
            "chapters": len(data.get("chapters", [])), "revision": _revision(folder, glossary),
            "report": data.get("source_file", ""),
            "reports": [str(p) for p in folder.glob("*分析报告.md")]}


def analyze(folder, glossary, report, dataset, works):
    folder = Path(folder)
    report = Path(report)
    if not report.is_file() or report.suffix.lower() != ".md":
        raise ValueError("Chọn file báo cáo AI Reader .md hợp lệ.")
    data = nhanvat_tool.run_entities(Namespace(
        reader=str(report), out=str(folder), glossary=str(glossary),
        aliases=str(folder / "aliases.txt"), overrides=str(folder / "overrides.tsv"),
        moegirl_dir=str(dataset), works=str(works),
    ))
    return {"chapters": len(data["chapters"]), "characters": len(data["characters"]),
            "ambiguous": len(data["ambiguous"])}


def decision(folder, name, choice):
    path = Path(folder) / "entities.xlsx"
    book = load_workbook(path)
    try:
        ws = book["Mơ hồ"]
        headers = {cell.value: cell.column for cell in ws[1]}
        found = False
        for row in ws.iter_rows(min_row=2):
            if row[headers["Cách gọi"] - 1].value == name:
                options = str(row[headers["Ứng viên"] - 1].value or "").split(" | ")
                if choice not in options:
                    raise ValueError("Ứng viên không thuộc danh sách mơ hồ hiện tại.")
                row[headers["Quyết định của tôi"] - 1].value = choice
                found = True
                break
        if not found:
            raise ValueError("Không tìm thấy tên mơ hồ trong bảng.")
        fd, temp = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
        os.close(fd)
        try:
            book.save(temp)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    finally:
        book.close()


def approve_character(folder, glossary, key, destination, gender, sources, parse_glossary, save_glossary):
    destination = destination.strip()
    if not destination or any(c in destination for c in "\r\n#\t") or any(s in destination for s in ("=>", "->", "=")):
        raise ValueError("Tên đích không hợp lệ.")
    if gender not in ("", "male", "female", "other"):
        raise ValueError("Giới tính không hợp lệ.")
    if not isinstance(sources, list) or not sources or len(set(sources)) != len(sources):
        raise ValueError("Chọn ít nhất một cách gọi, không trùng.")
    data = json.loads((Path(folder) / "entities.json").read_text(encoding="utf-8-sig"))
    char = next((c for c in data["characters"] if c["key"] == key), None)
    if not char:
        raise ValueError("Nhân vật không còn tồn tại; hãy tải lại.")
    valid = {c["name"] for c in char["calls"]}
    if any(not isinstance(s, str) or s not in valid or any(c in s for c in "\r\n#\t=") for s in sources):
        raise ValueError("Cách gọi không thuộc nhân vật hoặc không hợp lệ.")
    path = Path(glossary)
    text = nhanvat_tool._read_text(path) if path.is_file() else ""
    parsed = parse_glossary(text)
    existing = {}
    for row in parsed["rows"]:
        existing.setdefault(row["src"], []).append(row)
    groups, source_groups = nhanvat_tool.load_glossary_characters(path)
    group_ids = {g for source in sources for g in source_groups.get(source, set())}
    if key.startswith("glossary:") and any(g["key"] == key for g in groups):
        group_ids.add(key)
    if len(group_ids) > 1:
        raise ValueError("Các cách gọi đã thuộc nhiều nhân vật được duyệt; cần xử lý xung đột trong glossary.")
    group = next((g for g in groups if g["key"] in group_ids), None)
    if group and (group["dest"] != destination or (group["genders"] and gender and gender not in group["genders"])):
        raise ValueError("Tên đích hoặc giới tính khác nhân vật đã duyệt; sửa trong glossary trước.")
    ident = group["id"] if group else "h_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    lines = text.splitlines()
    additions = []
    for src in sources:
        rows = existing.get(src, [])
        if any(r["dst"] != destination for r in rows):
            raise ValueError(f'Xung đột thuật ngữ "{src}"; sửa tại trang Glossary trước.')
        if len(rows) > 1:
            raise ValueError(f'Nguồn "{src}" có nhiều dòng trong glossary; xử lý trùng trước.')
        note = "nhân vật" + (f"; id={ident}" if ident else "") + (f"; gender={gender}" if gender else "")
        if rows:
            row = rows[0]
            if "nhân vật" not in row["info"].casefold():
                i = row["line"] - 1
                lines[i] += ("; " if "#" in lines[i] else " # ") + note
        else:
            additions.append(f"{src} => {destination} # {note}")
    return save_glossary("\n".join(lines + additions) + "\n")


def _revision(folder, glossary):
    digest = hashlib.sha256()
    for path in (folder / "entities.json", folder / "entities.xlsx", folder / "xungho.xlsx", Path(glossary),
                 folder / "rel_types.txt", folder / "xungho.roles.json"):
        digest.update(str(path).encode("utf-8"))
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    return digest.hexdigest()


def generate(folder, glossary, updates=None):
    return nhanvat_tool.run_xungho(Namespace(
        out=str(folder), glossary=str(glossary), rel_types=str(folder / "rel_types.txt"),
        min_run=2, user_updates=updates,
    ))


_SIBLING_TYPES = {
    "兄妹", "兄妹关系", "姐弟", "姐弟关系", "兄弟", "姐妹", "姐妹关系",
    "哥哥-弟弟", "姐姐-妹妹", "姐-妹", "妹妹-哥哥", "姐姐-弟弟",
}
_SIBLING_ROLES = {
    "older_male": "Anh trai", "younger_male": "Em trai",
    "older_female": "Chị gái", "younger_female": "Em gái",
}


def _read_roles(folder):
    path = Path(folder) / "xungho.roles.json"
    if not path.is_file():
        return []
    records = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(records, list):
        raise ValueError("File xungho.roles.json không hợp lệ.")
    return records


def save_roles(folder, kind, source, target, source_role, target_role):
    if kind not in _SIBLING_TYPES or not all(isinstance(v, str) and v for v in (source, target)) or source == target:
        raise ValueError("Cặp quan hệ anh chị em không hợp lệ.")
    data = json.loads((Path(folder) / "entities.json").read_text(encoding="utf-8-sig"))
    if not any(r["type"] == kind and {r["source"], r["target"]} == {source, target}
               for r in data["relations"]):
        raise ValueError("Cặp và nhãn không có trong báo cáo hiện tại.")
    if (source_role, target_role) != ("", ""):
        if (source_role not in _SIBLING_ROLES or target_role not in _SIBLING_ROLES
                or source_role.split("_")[0] == target_role.split("_")[0]):
            raise ValueError("Chọn một vai anh/chị và một vai em hợp lệ.")
    records = _read_roles(folder)
    report = data.get("source_file", "")
    records = [r for r in records if not (r.get("report", "") == report and r["type"] == kind
                                           and {r["a"], r["b"]} == {source, target})]
    if source_role:
        records.append({"report": report, "type": kind, "a": source, "b": target,
                        "a_role": source_role, "b_role": target_role})
    nhanvat_tool._atomic_text(Path(folder) / "xungho.roles.json",
                            json.dumps(records, ensure_ascii=False, indent=2) + "\n")


def relationship_summary(data, relation_types, glossary, role_decisions=None):
    """Giữ nhãn, chiều và bằng chứng quan hệ đúng như dữ liệu báo cáo."""
    chars = {char["key"]: char for char in data.get("characters", [])}
    matches, _ = nhanvat_tool.match_glossary_characters(list(chars.values()), Path(glossary))
    mapping = dict(nhanvat_tool._REL_DEFAULTS)
    if Path(relation_types).is_file():
        mapping = {}
        for line in nhanvat_tool._read_text(Path(relation_types)).splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                pair = nhanvat_tool._split_mapping(line)
                if pair:
                    mapping[pair[0]] = pair[1] or "?"
    def gender(key):
        genders = matches.get(key, {}).get("genders", [])
        value = genders[-1] if genders else chars.get(key, {}).get("gender", "")
        return value if value in ("male", "female", "other") else ""
    decisions = {}
    for record in role_decisions or []:
        if record.get("report", "") != data.get("source_file", ""):
            continue
        decisions[(record["type"], record["a"], record["b"])] = (record["a_role"], record["b_role"])
        decisions[(record["type"], record["b"], record["a"])] = (record["b_role"], record["a_role"])

    def roles(kind, a, b):
        if kind not in _SIBLING_TYPES:
            return "", "", ""
        decision = decisions.get((kind, a, b))
        if decision:
            return decision[0], decision[1], "manual"
        by_gender = {
            "兄妹": {"male": "older_male", "female": "younger_female"},
            "兄妹关系": {"male": "older_male", "female": "younger_female"},
            "姐弟": {"female": "older_female", "male": "younger_male"},
            "姐弟关系": {"female": "older_female", "male": "younger_male"},
        }.get(kind, {})
        ga, gb = gender(a), gender(b)
        if ga != gb and ga in by_gender and gb in by_gender:
            return by_gender[ga], by_gender[gb], "report"
        return "", "", "pending"

    groups = {}
    for relation in data.get("relations", []):
        a, b = relation["source"], relation["target"]
        if a == b or a not in chars or b not in chars:
            continue
        kind = relation["type"]
        group = mapping.get(kind, "?")
        if group == "?":
            group = "Chưa rõ (chỉ tương tác)"
        source_role, target_role, role_origin = roles(kind, a, b)
        key = (kind, group, gender(a), gender(b), source_role, target_role, role_origin)
        pairs = groups.setdefault(key, {})
        pair = pairs.setdefault((a, b), {"source": a, "target": b,
                                         "source_name": chars[a].get("canonical_zh", a),
                                         "target_name": chars[b].get("canonical_zh", b),
                                         "chapters": set(), "quotes": []})
        pair["chapters"].add(relation["chapter"])
        for quote in relation.get("quotes", []):
            if quote and quote not in pair["quotes"] and len(pair["quotes"]) < 2:
                pair["quotes"].append(quote)
    summary = []
    for (kind, group, source_gender, target_gender, source_role, target_role, role_origin), pairs in sorted(groups.items()):
        evidence = []
        for pair in pairs.values():
            pair["chapters"] = sorted(pair["chapters"])
            evidence.append(pair)
        evidence.sort(key=lambda pair: (-len(pair["chapters"]), pair["source_name"], pair["target_name"]))
        summary.append({"type": kind, "group": group, "speaker_gender": source_gender,
                        "listener_gender": target_gender,
                        "source_role": source_role, "target_role": target_role, "role_origin": role_origin,
                        "role_label": (f"{_SIBLING_ROLES[source_role]} – {_SIBLING_ROLES[target_role]}"
                                       if source_role else "Chưa rõ vai – chờ duyệt" if role_origin else ""),
                        "observations": sum(len(pair["chapters"]) for pair in evidence),
                        "pairs": evidence, "row_keys": []})
    return summary


def read(folder, glossary):
    folder = Path(folder)
    workbook = folder / "xungho.xlsx"
    entities = folder / "entities.json"
    warnings = []
    if not entities.is_file():
        return {"characters": [], "rows": [], "summary": [], "warnings": ["Chưa có entities.json; hãy chạy phân tích nhân vật trước."],
                "revision": _revision(folder, glossary), "ready": False}
    data = json.loads(entities.read_text(encoding="utf-8-sig"))
    role_decisions = _read_roles(folder)
    summary = relationship_summary(data, folder / "rel_types.txt", glossary, role_decisions)
    current_relations = {(r["type"], frozenset((r["source"], r["target"]))) for r in data.get("relations", [])}
    unused = sum(1 for r in role_decisions
                 if r.get("report", "") != data.get("source_file", "") or
                 (r["type"], frozenset((r["a"], r["b"]))) not in current_relations)
    if unused:
        warnings.append(f"Có {unused} quyết định vai không khớp báo cáo hiện tại; không được áp dụng.")
    report = Path(data.get("source_file", ""))
    if report.is_file() and report.stat().st_mtime_ns > entities.stat().st_mtime_ns:
        warnings.append("Báo cáo mới hơn entities.json; cần chạy lại entities để cập nhật nhân vật và quan hệ.")
    if not workbook.is_file():
        return {"characters": [], "rows": [], "summary": summary, "warnings": warnings + ["Chưa có xungho.xlsx; bấm Tạo lại bảng."],
                "revision": _revision(folder, glossary), "ready": False}
    book = load_workbook(workbook, read_only=True, data_only=True)
    try:
        def sheet(name):
            ws = book[name]
            rows = ws.iter_rows(values_only=True)
            headers = next(rows)
            return [dict(zip(headers, row)) for row in rows if any(v is not None for v in row)]

        chars = sheet("Nhân vật")
        workbook_rows = sheet("Xưng hô")
        waiting = len(sheet("Chờ duyệt")) if "Chờ duyệt" in book.sheetnames else 0
        orphans = len(sheet("Mồ côi")) if "Mồ côi" in book.sheetnames else 0
    finally:
        book.close()
    characters = [{"id": c["Khóa"], "name": c.get("Đích") or c.get("Tên Hán") or c["Khóa"],
                   "gender": c.get("Giới tính") or ""} for c in chars]
    rows = []
    for row in workbook_rows:
        key = str(row.get("_key") or "")
        match = re.fullmatch(r"pair:(.+)>(.+):(\d+)(?:-\d+)?", key)
        if not match:
            raise ValueError(f"Khóa dòng xưng hô không hợp lệ: {key}")
        speaker, listener, start = match.groups()
        normalized_key = f"pair:{speaker}>{listener}:{start}"
        rows.append({
            "key": normalized_key, "speaker": speaker, "listener": listener,
            "from": row["Từ chương (file)"], "to": row.get("Đến chương"),
            "group": row.get("Nhóm quan hệ") or "", "relations": row.get("Quan hệ theo file") or "",
            "evidence": row.get("2 câu trích mẫu") or "",
            "self": row.get("Tự xưng") or "", "you": row.get("Gọi người nghe là") or "",
            "name": row.get("Ghi chú của tôi") or "",
        })
    rows_by_pair = {}
    for row in rows:
        rows_by_pair.setdefault((row["speaker"], row["listener"], row["group"]), []).append(row)
    for item in summary:
        matched = set()
        for pair in item["pairs"]:
            for row in rows_by_pair.get((pair["source"], pair["target"], item["group"]), []):
                end = row["to"]
                end = len(data.get("chapters", [])) if end == "hết" or end is None else int(end)
                if any(int(row["from"]) <= chapter <= end for chapter in pair["chapters"]):
                    matched.add(row["key"])
        item["row_keys"] = sorted(matched)
    if entities.stat().st_mtime_ns > workbook.stat().st_mtime_ns:
        warnings.append("Entities mới hơn bảng xưng hô; hãy tạo lại bảng trước khi duyệt các quy tắc.")
    if not characters:
        warnings.append("Chưa có nhân vật khớp chắc với thuatngu.txt; hãy duyệt nhân vật trong trang H.")
    if orphans:
        warnings.append(f"Có {orphans} quyết định mồ côi; xem sheet Mồ côi trong xungho.xlsx.")
    rules_path = folder / "xungho.rules.json"
    if not rules_path.is_file():
        warnings.append("Chưa có file quy tắc dịch; hãy tạo lại bảng hoặc lưu các dòng đã chốt.")
    else:
        rules = json.loads(rules_path.read_text(encoding="utf-8-sig"))
        actual = hashlib.sha256(Path(glossary).read_bytes()).hexdigest() if Path(glossary).is_file() else ""
        if rules.get("glossary_sha256") != actual:
            warnings.append("Glossary đã thay đổi; cần tạo lại bảng để cập nhật quy tắc dịch.")
        if workbook.stat().st_mtime_ns > rules_path.stat().st_mtime_ns:
            warnings.append("Bảng Excel mới hơn file quy tắc dịch; hãy lưu hoặc tạo lại bảng để đồng bộ.")
    return {"characters": characters, "rows": rows, "summary": summary, "warnings": warnings, "waiting": waiting,
            "orphans": orphans, "revision": _revision(folder, glossary), "ready": True}
