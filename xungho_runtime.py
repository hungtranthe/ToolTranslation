"""Đưa các quy tắc xưng hô đã duyệt vào đúng chương và ngữ cảnh dịch."""
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


def _title(value):
    return re.sub(r"\s+", "", value.removeprefix("##").strip())


def load_rules(path, glossary_path, chunks):
    path = Path(path)
    if not path.is_file():
        return None, 0
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != 1:
        raise ValueError(f"Phiên bản file xưng hô không được hỗ trợ: {path}")
    if not data["rules"]:
        return None, 0
    glossary_path = Path(glossary_path)
    digest = hashlib.sha256(glossary_path.read_bytes()).hexdigest()
    if digest != data["glossary_sha256"]:
        raise ValueError("thuatngu.txt đã đổi; hãy chạy lại lệnh xungho để xác nhận nhân vật.")

    titles = defaultdict(list)
    for chapter in data["chapters"]:
        heading = re.sub(r"^第\s*\d+\s*章\s*", "", chapter["title"])
        titles[_title(heading)].append(chapter["number"])
    chapter_map = {}
    missing = 0
    for chunk in chunks:
        if chunk["part"] != 1:
            continue
        heading = chunk["text"].splitlines()[0]
        candidates = titles.get(_title(heading), [])
        if len(candidates) == 1:
            chapter_map[chunk["chapter"]] = candidates[0]
        else:
            missing += 1

    names = defaultdict(set)
    for key, character in data["characters"].items():
        for name in character["names"]:
            if len(name.strip()) >= 2:
                names[name.casefold()].add(key)
    patterns = []
    for name in sorted(names, key=lambda value: (-len(value), value)):
        escaped = re.escape(name)
        if name[0].isascii() and name[0].isalnum():
            escaped = r"(?<!\w)" + escaped
        if name[-1].isascii() and name[-1].isalnum():
            escaped += r"(?!\w)"
        patterns.append(escaped)
    data["_pattern"] = re.compile("|".join(patterns), re.IGNORECASE) if patterns else None
    data["_names"] = names
    data["_chapter_map"] = chapter_map
    return data, missing


def hint_for_chunk(data, chapter, text):
    if not data or not data["_pattern"]:
        return ""
    report_chapter = data["_chapter_map"].get(chapter)
    if report_chapter is None:
        return ""
    present = set()
    for match in data["_pattern"].finditer(text):
        candidates = data["_names"][match.group().casefold()]
        if len(candidates) == 1:
            present.update(candidates)
    lines = []
    for rule in data["rules"]:
        if (rule["speaker"] not in present or rule["listener"] not in present
                or not rule["start"] <= report_chapter <= rule["end"]):
            continue
        parts = []
        if rule["self"]:
            parts.append(f'tự xưng "{rule["self"]}"')
        if rule["address"]:
            parts.append(f'gọi người nghe "{rule["address"]}"')
        if rule["note"]:
            parts.append(rule["note"])
        speaker = data["characters"][rule["speaker"]]["display"]
        listener = data["characters"][rule["listener"]]["display"]
        lines.append(f'{speaker} nói với {listener}: {"; ".join(parts)}')
    if not lines:
        return ""
    return ("Quy tắc xưng hô đã duyệt, chỉ áp dụng khi xác định đúng người nói và người nghe:\n"
            + "\n".join(lines) + "\n\n")
