#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import local Zzzzzzyt/moegirl-dataset JSON into compact moegirl_terms.json.

No network/API calls. Does not read char2cv.json or cv2char.json.
Only Python standard library is required.
"""
from __future__ import annotations
import argparse
import json
import re
import os
from pathlib import Path
from datetime import datetime, timezone

_IMPORTER_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_DIR = _IMPORTER_DIR / "stitch_noveltranslator_desktop_gui" / "moegirl"
_LEGACY_DATASET_DIRS = [
    Path(r"D:\8man\moe_girl_dataset\moegirl-dataset\moegirl"),
    Path(r"D:\8man\moe_girl_dataset\moegirl-dataset\src\moegirl"),
]
DEFAULT_OUTPUT = Path(__file__).resolve().with_name("moegirl_terms.json")
LATIN_RE = re.compile(r"[A-Za-z]")
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
SPACE_RE = re.compile(r"\s+")

# These fields may contain a usable romanized/Latin spelling. The importer does
# not infer a Latin name from a Chinese page title.
LATIN_FIELDS = ("英文名", "外文名", "罗马音", "罗马字", "拉丁字母", "英文", "罗马拼音")
ALIAS_FIELDS = ("别名", "别号", "昵称", "绰号", "其他名字", "本名")
REAL_NAME_FIELDS = ("本名",)  # tên thật, dùng làm đích dịch (dst)
BAD_CHARS_RE = re.compile(r"[<>\[\]{}|/\\=@#;:]|https?|\.com|\d{3,}")
CLEAN_WRAP_RE = re.compile(r"^[\s<>\[\]()（）《》「」『』\"'“”‘’]+|[\s<>\[\]()（）《》「」『』\"'“”‘’]+$")
_HTML_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")
# Độ tin cậy theo trường nguồn của tên Latin. 本名/罗马音 là romaji gốc (cao nhất);
# 拉丁字母 chỉ là bảng chữ; 英文名/外文名 là bản địa hóa tiếng Anh (dễ sai).
LATIN_FIELD_CONFIDENCE = {
    "本名": 0.90,
    "罗马音": 0.90, "罗马字": 0.90, "罗马拼音": 0.90,
    "拉丁字母": 0.70,
    "英文名": 0.55, "外文名": 0.55, "英文": 0.55,
}


def find_dataset_dir(explicit: str = ""):
    """Tìm thư mục dataset chứa extra_processed.json; thử tham số > mặc định > vị trí cũ."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates += [DEFAULT_DATASET_DIR] + _LEGACY_DATASET_DIRS
    for cand in candidates:
        if cand.is_dir() and (cand / "extra_processed.json").is_file():
            return cand
    return Path(explicit or str(DEFAULT_DATASET_DIR))


def read_json(path: Path, required=False):
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"缺少必要文件: {path}")
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def values(value):
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(values(item))
        return out
    if isinstance(value, dict):
        # Image/structured objects are not names; don't stringify them.
        return []
    s = str(value).strip()
    return [s] if s else []


def unique_strings(items, limit=100):
    result, seen = [], set()
    for item in items:
        s = SPACE_RE.sub(" ", str(item)).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append(s)
        if len(result) >= limit:
            break
    return result


def _is_weak_alias(value, title=""):
    name = SPACE_RE.sub(" ", str(value)).strip()
    page_title = SPACE_RE.sub(" ", str(title)).strip()
    bare_title = re.sub(r"[\(（][^()（）]*[)）]\s*$", "", page_title).strip()
    if not name or name in (page_title, bare_title):
        return False
    return len(name) <= 2 and all(HAN_RE.fullmatch(char) for char in name)


def extract_aliases_ex(info, title=""):
    """Return all aliases and split them into strong and weak lookup names."""
    aliases, strong, weak = [], [], []
    if not isinstance(info, dict):
        return [], [], []
    for field in ALIAS_FIELDS:
        for item in values(info.get(field)):
            aliases.append(item)
            if field == "本名" or not _is_weak_alias(item, title):
                strong.append(item)
            else:
                weak.append(item)
    aliases = unique_strings(aliases)
    strong = unique_strings(strong)
    strong_set = set(strong)
    weak = [name for name in unique_strings(weak) if name not in strong_set]
    return aliases, strong, weak


def clean_latin(value):
    s = _HTML_TAG_RE.sub("", str(value))           # <ref>, </ref>, <br/> ... (không ăn "< Ookami Mio >")
    s = CLEAN_WRAP_RE.sub("", s)                   # "< Ookami Mio >" -> "Ookami Mio"
    return SPACE_RE.sub(" ", s).strip()

def is_latin_name(value):
    s = clean_latin(value)
    if not s or not LATIN_RE.search(s):
        return False
    if HAN_RE.search(s) or KANA_RE.search(s) or BAD_CHARS_RE.search(s):
        return False
    words = s.split()
    if not (1 <= len(words) <= 4) or not (2 <= len(s) <= 40):
        return False
    # mỗi từ phải bắt đầu bằng chữ hoa (loại câu văn, tên súng dài, v.v.)
    if not all(w[0].isupper() for w in words if w[0].isalpha()):
        return False
    return True


def _dedup_latin_entries(entries):
    """Khử trùng theo tên; giữ mục có confidence cao nhất (cùng tên nhiều trường)."""
    best, order = {}, []
    for e in entries:
        name = e["name"]
        if name not in best:
            best[name] = dict(e)
            order.append(name)
        elif (e["confidence"] or -1.0) > (best[name]["confidence"] or -1.0):
            best[name] = dict(e)
    return [best[n] for n in order]


def extract_fields_ex(info):
    """(aliases, latin_entries) với latin_entries = [{"name","field","confidence"}]."""
    aliases, _, _ = extract_aliases_ex(info)
    latin_entries = []
    if not isinstance(info, dict):
        return aliases, latin_entries
    # Đích dịch (dst) CHỈ lấy tên thật (本名) + trường Latin/ngoại văn, không lấy bí danh.
    for field in REAL_NAME_FIELDS + LATIN_FIELDS:
        base = LATIN_FIELD_CONFIDENCE.get(field)
        for item in values(info.get(field)):
            if is_latin_name(item):
                name = clean_latin(item)
                conf = base
                # Tên 3+ từ thường là tên vật/súng/mô tả đầy đủ (vd "Višenamjenska Hrvatska
                # Strojnica") chứ không phải tên nhân vật — hạ confidence rõ rệt thay vì loại.
                if conf is not None and len(name.split()) >= 3:
                    conf = round(conf * 0.5, 2)
                latin_entries.append({"name": name, "field": field, "confidence": conf})
    return aliases, _dedup_latin_entries(latin_entries)


def extract_fields(info):
    """Chữ ký cũ, moegirl_index.py vẫn import: (aliases, latin_names)."""
    aliases, latin_entries = extract_fields_ex(info)
    return aliases, unique_strings([e["name"] for e in latin_entries], limit=20)


def build_catalog(dataset_dir: Path):
    dataset_dir = Path(dataset_dir)
    extra_path = dataset_dir / "extra_processed.json"
    char_subject_path = dataset_dir / "char2subject.json"
    char_gender_path = dataset_dir / "char2gender.json"

    extra = read_json(extra_path, required=True)
    char_subject = read_json(char_subject_path)
    char_gender = read_json(char_gender_path)
    if not isinstance(extra, dict):
        raise ValueError("extra_processed.json phải là JSON object {tên nhân vật: thông tin}.")
    if not isinstance(char_subject, dict):
        char_subject = {}
    if not isinstance(char_gender, dict):
        char_gender = {}

    records = {}
    for raw_title, info in extra.items():
        title = str(raw_title).strip()
        if not title or not isinstance(info, dict):
            continue
        aliases, latin_entries = extract_fields_ex(info)
        _, strong_aliases, weak_aliases = extract_aliases_ex(info, title)
        aliases = [x for x in aliases if x != title]
        latin_names = [e["name"] for e in latin_entries]
        subjects = unique_strings(values(char_subject.get(title)), limit=80)
        gender = char_gender.get(title)
        if gender not in ("male", "female", "other"):
            gender = None

        # Confidence = cao nhất trong các trường Latin; 0 nếu không có tên Latin.
        confidence = max((e["confidence"] for e in latin_entries), default=0.0)
        records[title] = {
            "page_title": title,
            "canonical_zh": title,
            "aliases": aliases,
            "strong_aliases": [x for x in strong_aliases if x != title],
            "weak_aliases": [x for x in weak_aliases if x != title],
            "latin_names": latin_names,
            "latin_entries": latin_entries,
            "subjects": subjects,
            "gender": gender,
            "confidence": confidence,
            "source": "local moegirl-dataset/extra_processed.json",
        }

    return {
        "schema_version": 4,
        "source": "Zzzzzzyt/moegirl-dataset (local files)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_dir": str(dataset_dir),
        "record_count": len(records),
        "records": records,
    }


def save_catalog(catalog, output: Path):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp, output)


def main():
    parser = argparse.ArgumentParser(description="Nhập dataset Moegirl local, không gọi API.")
    parser.add_argument("--dataset-dir", default="", help="Thư mục chứa extra_processed.json")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="File moegirl_terms.json đầu ra")
    args = parser.parse_args()
    catalog = build_catalog(find_dataset_dir(args.dataset_dir))
    save_catalog(catalog, Path(args.output))
    with_latin = sum(bool(r["latin_names"]) for r in catalog["records"].values())
    aliases = sum(len(r["aliases"]) for r in catalog["records"].values())
    print(f"[Moegirl Dataset] Đã nhập {catalog['record_count']:,} nhân vật.")
    print(f"[Moegirl Dataset] Nhân vật có tên Latin đáng tin từ trường tên: {with_latin:,}.")
    print(f"[Moegirl Dataset] Bí danh đã nhập: {aliases:,}.")
    print(f"[Moegirl Dataset] Đã ghi: {Path(args.output).resolve()}")
    print("[Moegirl Dataset] Không đọc char2cv.json/cv2char.json; không gọi API; không sửa thuatngu.txt.")

if __name__ == "__main__":
    main()
