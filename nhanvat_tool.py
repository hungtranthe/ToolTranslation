#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chuẩn hóa nhân vật AI Reader, sinh glossary ứng viên và bảng xưng hô."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from moegirl_dataset_import import build_catalog, find_dataset_dir
from moegirl_index import MoegirlIndex


_CHAPTER_RE = re.compile(r"^##\s+(.+?)\s*$")
_PERSON_RE = re.compile(r"^-\s+\*\*(.+?)\*\*(?:（别名：(.+?)）)?(?:\s*[—–-]\s*(.*))?$")
_REL_RE = re.compile(r"^-\s+(.+?)\s+—（(.+?)）→\s*(.+?)：\s*(.*)$")
_ROLE_SUFFIX_GENDER = {
    "爸爸": "male", "父亲": "male", "哥哥": "male",
    "妈妈": "female", "母亲": "female", "姐姐": "female",
}
_HONORIFICS = ("老师", "同学", "先生", "小姐", "学姐", "学长", "君", "桑", "酱")
_GENERIC_ROLES = {
    "医生", "护士", "警察", "老师", "同学", "学生", "店员", "老板", "记者",
    "警官", "教授", "校长", "父亲", "母亲", "爸爸", "妈妈", "哥哥", "姐姐",
}
_REL_DEFAULTS = {
    "同学": "Bạn học", "同学关系": "Bạn học", "朋友": "Bạn bè",
    "兄妹": "Anh trai - em gái", "姐弟": "Chị gái - em trai",
    "姐妹": "Chị em gái", "兄弟": "Anh em trai", "父子": "Cha - con",
    "母女": "Mẹ - con gái", "母子": "Mẹ - con trai",
    "同事": "Đồng nghiệp", "学生会成员": "Hội học sinh",
    "情侣": "Tình nhân", "恋人": "Tình nhân",
    "上下级": "Cấp trên - cấp dưới", "主仆": "Chủ - tớ",
    "室友": "Bạn cùng phòng", "前辈": "Tiền bối - hậu bối",
    "学姐-学弟": "Tiền bối - hậu bối",
    "师生": "Thầy - trò", "老师": "Thầy - trò", "学生": "Thầy - trò",
    "学生-老师": "Thầy - trò", "老师-学生": "Thầy - trò", "指导关系": "Thầy - trò",
    "学长-学弟": "Tiền bối - hậu bối", "学长": "Tiền bối - hậu bối",
}
_NOISE_RELATIONS = (
    "对话", "解释", "互动", "提问", "回答", "提及", "间接", "未知",
    "支持", "帮助", "安慰",
)
_USER_COLUMNS = {
    "Quyết định của tôi", "Tự xưng", "Gọi người nghe là", "Ghi chú của tôi",
}
_YELLOW = PatternFill("solid", fgColor="FFF2CC")
_HEADER = PatternFill("solid", fgColor="D9EAF7")


def _read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def _atomic_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=path.suffix + ".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_text(path: Path, text: str, *, bom: bool = False) -> None:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8-sig" if bom else "utf-8")
    _atomic_bytes(path, data)


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _split_mapping(line: str) -> tuple[str, str] | None:
    for sep in ("=>", "->", "\t", "="):
        if sep in line:
            left, right = line.split(sep, 1)
            return left.strip(), right.strip()
    return None


def _read_tsv(path: Path) -> list[dict[str, str]]:
    import csv

    text = _read_text(path)
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return []
    return list(csv.DictReader(lines, delimiter="\t"))


def parse_reader(path: Path) -> dict:
    """Đọc tiêu đề, chương, nhân vật và quan hệ theo các mục AI Reader."""
    chapters = []
    current = None
    section = ""
    document_title = ""
    for line in _read_text(path).splitlines():
        if not document_title and line.startswith("# "):
            document_title = line[2:].strip()
        chapter_heading = _CHAPTER_RE.match(line)
        if chapter_heading and line.startswith("## "):
            title = chapter_heading.group(1).strip()
            if title.startswith("第") or title.startswith("序章"):
                idx = len(chapters) + 1
                original = 0 if title.startswith("序章") else None
                heading_tail = re.sub(r"^第\s*\d+\s*章\s*", "", title).strip()
                match = re.search(r"第\s*0*(\d+)\s*章", heading_tail)
                if match and not re.search(r"第\s*\d+\s*节", heading_tail):
                    original = int(match.group(1))
                current = {
                    "number": idx, "source_number": original, "title": title,
                    "characters": [], "relations": [],
                }
                chapters.append(current)
                section = ""
                continue
        if current is None:
            continue
        if line.startswith("###"):
            heading = line
            section = "characters" if "人物" in heading else "relations" if "关系" in heading else ""
            continue
        if section == "characters":
            match = _PERSON_RE.match(line)
            if match:
                name = _key(match.group(1))
                if name:
                    current["characters"].append({
                        "name": name, "aliases": [part.strip() for part in re.split(r"[、，,]", match.group(2) or "") if part.strip()],
                        "description": (match.group(3) or "").strip(),
                    })
        elif section == "relations":
            match = _REL_RE.match(line)
            if match:
                source, kind, target, quote = (_key(part) for part in match.groups())
                if source and target:
                    current["relations"].append({
                        "source": source, "type": kind, "target": target, "quote": quote,
                    })
    if not chapters:
        raise ValueError(f"Không tìm thấy chương dạng '## 第 N 章' hoặc '## 序章' trong {path}")
    return {"title": document_title, "chapters": chapters}


def load_glossary_characters(path: Path) -> tuple[list[dict], dict[str, set[str]]]:
    """Nhóm các dòng nhân vật trong thuatngu.txt theo id hoặc đích."""
    groups: dict[str, dict] = {}
    source_to_groups: dict[str, set[str]] = defaultdict(set)
    if not Path(path).is_file():
        return [], source_to_groups
    for line_no, raw in enumerate(_read_text(path).splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, _, note = line.partition("#")
        pair = _split_mapping(body.strip())
        if not pair:
            continue
        source, dest = pair
        if "nhân vật" not in note.casefold() or not source or not dest:
            continue
        id_match = re.search(r"(?:^|[;,\s])id\s*=\s*([^;,\s]+)", note, re.I)
        key_value = id_match.group(1) if id_match else dest.strip()
        if not key_value:
            continue
        group_key = "glossary:" + key_value
        group = groups.setdefault(group_key, {
            "key": group_key, "id": id_match.group(1) if id_match else "",
            "dest": dest, "sources": [], "genders": [], "notes": [],
        })
        if source and source not in group["sources"]:
            group["sources"].append(source)
        gender_match = re.search(r"gender\s*=\s*(male|female|other)", note, re.I)
        if gender_match:
            group["genders"].append(gender_match.group(1).lower())
        group["notes"].append({"line": line_no, "text": note.strip()})
        if source:
            source_to_groups[source].add(group_key)
    return list(groups.values()), source_to_groups


def _load_aliases(path: Path) -> dict[str, str]:
    aliases = {}
    if not Path(path).exists():
        _atomic_text(path, "# cách_gọi => tên_nguồn_trong_glossary_hoặc_trang_Moegirl\r\n")
        return aliases
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pair = _split_mapping(line)
        if pair and pair[0]:
            aliases[pair[0]] = pair[1]
    return aliases


def _load_overrides(path: Path) -> dict[str, dict[str, str]]:
    result = {}
    if not Path(path).exists():
        _atomic_text(path, "key\tgender\tnote\r\n")
        return result
    rows = _read_tsv(path)
    for row in rows:
        key = _key(row.get("key") or row.get("id") or row.get("name") or "")
        if key:
            result[key] = {str(k).strip(): str(v or "").strip() for k, v in row.items()}
    return result


def _eligible_pages(index: MoegirlIndex, approved_subjects: set[str]) -> set[str]:
    return {
        page for page, char in index.chars.items()
        if approved_subjects.intersection(char.subjects)
    }


def _name_parts(name: str) -> set[str]:
    compact = re.sub(r"[\s·・（）()]", "", name)
    return {
        compact[:cut] for cut in range(2, len(compact) - 1)
    } | {
        compact[cut:] for cut in range(1, len(compact) - 1)
    }


def _role_gender(name: str) -> str:
    for suffix, gender in _ROLE_SUFFIX_GENDER.items():
        if name.endswith(suffix):
            return gender
    return ""


def _is_role_label(name: str) -> bool:
    return name in _GENERIC_ROLES or any(name.endswith(suffix) for suffix in _ROLE_SUFFIX_GENDER)


def _strip_honorific(name: str) -> str:
    for suffix in sorted(_HONORIFICS, key=len, reverse=True):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)].strip()
    return name


def _approved_subjects(path: Path) -> set[str]:
    subjects = set()
    for row in _read_tsv(path):
        normalized = {str(key).strip().casefold(): str(value or "").strip()
                      for key, value in row.items()}
        status = normalized.get("status", "")
        if status and status.casefold() != "approved":
            continue
        values = normalized.get("subject_keys", "")
        if not values:
            values = normalized.get("work_id", "") or normalized.get("work_title", "")
        subjects.update(key.strip() for key in values.split("|") if key.strip())
    return subjects


def _entity_for_page(page: str, index: MoegirlIndex, groups: list[dict]) -> str:
    char = index.chars[page]
    possible = []
    for group in groups:
        sources = set(group["sources"])
        if page in sources or char.name in sources or any(a in sources for a in char.aliases):
            possible.append(group["key"])
            continue
        if group["dest"] and group["dest"] in char.latin_names:
            possible.append(group["key"])
    return possible[0] if len(set(possible)) == 1 else "moe:" + page


def resolve_name(
    name: str,
    *,
    aliases: dict[str, str],
    source_to_groups: dict[str, set[str]],
    index: MoegirlIndex,
    eligible: set[str],
    moe_entities: dict[str, str],
    seed_forms: dict[str, set[str]],
    decisions: dict[str, str] | None = None,
) -> dict:
    """Khớp tên theo thủ công, chính xác, dàn đã biết, rồi mới để mức D."""
    name = _key(name)
    if _is_role_label(name):
        return {"key": "role:" + name, "level": "D", "page": "", "ambiguous": []}

    target = aliases.get(name, "") or (decisions or {}).get(name, "")
    if target:
        choice = target.rsplit(" [", 1)
        explicit_key = choice[1][:-1] if len(choice) == 2 and choice[1].endswith("]") else target
        known_entity_keys = set(moe_entities.values()) | {
            group_key for group_keys in source_to_groups.values() for group_key in group_keys
        }
        if explicit_key != target and explicit_key in known_entity_keys:
            page = next((p for p, entity in moe_entities.items() if entity == explicit_key), "")
            return {"key": explicit_key, "level": "0", "page": page, "ambiguous": []}
        pages = [p for p in index.pages_for_name(target) if p in eligible]
        groups = source_to_groups.get(target, set())
        ids = {moe_entities[p] for p in pages} | set(groups)
        if explicit_key in moe_entities.values():
            ids.add(explicit_key)
        if explicit_key.startswith("glossary:") and any(
            explicit_key in group_keys for group_keys in source_to_groups.values()
        ):
            ids.add(explicit_key)
        if len(ids) == 1:
            return {"key": next(iter(ids)), "level": "0", "page": pages[0] if pages else ""}
        if len(ids) > 1:
            return {"key": None, "level": "0", "page": "", "ambiguous": sorted(ids)}

    pages = [page for page in index.pages_for_name(name) if page in eligible]
    ids = {moe_entities[page] for page in pages}
    ids.update(source_to_groups.get(name, set()))
    if len(ids) == 1:
        entity = next(iter(ids))
        page = next((p for p in pages if moe_entities[p] == entity), "")
        return {"key": entity, "level": "A", "page": page, "ambiguous": []}
    if len(ids) > 1:
        return {"key": None, "level": "A", "page": "", "ambiguous": sorted(ids)}

    candidates = set()
    for entity, forms in seed_forms.items():
        if name in forms:
            candidates.add(entity)
    if len(candidates) == 1:
        return {"key": next(iter(candidates)), "level": "B", "page": "", "ambiguous": []}
    if len(candidates) > 1:
        return {"key": None, "level": "B", "page": "", "ambiguous": sorted(candidates)}

    stripped = _strip_honorific(name)
    if stripped != name:
        pages = [page for page in index.pages_for_name(stripped) if page in eligible]
        ids = {moe_entities[page] for page in pages}
        ids.update(source_to_groups.get(stripped, set()))
        candidates = set(ids)
        if not candidates:
            candidates = {entity for entity, forms in seed_forms.items() if stripped in forms}
        if len(candidates) == 1:
            entity = next(iter(candidates))
            page = next((p for p in pages if moe_entities[p] == entity), "")
            return {"key": entity, "level": "C", "page": page, "ambiguous": []}
        if len(candidates) > 1:
            return {"key": None, "level": "C", "page": "", "ambiguous": sorted(candidates)}
    return {"key": "ai:" + name, "level": "D", "page": "", "ambiguous": []}


def _previous_decisions(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    wb = load_workbook(path, read_only=True, data_only=True)
    result = {}
    try:
        for sheet_name in ("Nhân vật", "Mơ hồ", "Chưa khớp"):
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            headers = {cell.value: cell.column for cell in ws[1]}
            key_col = headers.get("_key")
            decision_col = headers.get("Quyết định của tôi")
            if not key_col or not decision_col:
                continue
            name_col = headers.get("Cách gọi")
            for row in ws.iter_rows(min_row=2, values_only=True):
                row_key, value = row[key_col - 1], row[decision_col - 1]
                name = row[name_col - 1] if name_col else ""
                key = str(name or row_key or "").strip()
                if key and value:
                    result[key] = str(value).strip()
    finally:
        wb.close()
    return result


def build_cast(parsed: dict, index: MoegirlIndex, catalog: dict, groups: list[dict],
               source_to_groups: dict[str, set[str]], aliases: dict[str, str],
               eligible: set[str], approved_subjects: set[str],
               overrides: dict[str, dict[str, str]], decisions: dict[str, str]) -> dict:
    """Lập dàn, thống kê cách gọi và quy quan hệ về khóa nhân vật."""
    moe_entities = {page: _entity_for_page(page, index, groups) for page in eligible}
    observed = Counter()
    stats = defaultdict(lambda: {
        "count": 0, "relation_count": 0, "chapters": set(), "descriptions": [],
    })
    reader_aliases = defaultdict(set)
    for chapter in parsed["chapters"]:
        for person in chapter["characters"]:
            reader_aliases[person["name"]].update(person.get("aliases", []))
            observed[person["name"]] += 1
            stats[person["name"]]["count"] += 1
            stats[person["name"]]["chapters"].add(chapter["number"])
            if person["description"]:
                samples = stats[person["name"]]["descriptions"]
                if person["description"] not in samples and len(samples) < 3:
                    samples.append(person["description"])
        for rel in chapter["relations"]:
            for name in (rel["source"], rel["target"]):
                observed[name] += 1
                stats[name]["count"] += 1
                stats[name]["relation_count"] += 1
                stats[name]["chapters"].add(chapter["number"])

    # Tên đủ chắc tạo dàn trước; mức B chỉ được xét trong dàn này.
    seed_forms: dict[str, set[str]] = defaultdict(set)
    seed_pages = {}
    for name in observed:
        exact_pages = [p for p in index.pages_for_name(name) if p in eligible]
        exact_ids = {moe_entities[p] for p in exact_pages}
        exact_ids.update(source_to_groups.get(name, set()))
        if len(exact_ids) == 1:
            entity = next(iter(exact_ids))
            seed_forms[entity].add(name)
            for page in exact_pages:
                seed_pages[entity] = page
            for page, key in moe_entities.items():
                if key == entity:
                    char = index.chars[page]
                    seed_forms[entity].update((page, char.name, *char.strong_aliases))
                    for alias in (page, char.name, *char.strong_aliases):
                        seed_forms[entity].update(_name_parts(alias))
            for group in groups:
                if group["key"] == entity:
                    for source in group["sources"]:
                        seed_forms[entity].add(source)
                        seed_forms[entity].update(_name_parts(source))

    # Suffix honorific bị loại khỏi danh tính riêng, nhưng vẫn giữ nguyên cách gọi.
    for name in observed:
        bare = _strip_honorific(name)
        if bare != name:
            exact_pages = [p for p in index.pages_for_name(bare) if p in eligible]
            exact_ids = {moe_entities[p] for p in exact_pages} | set(source_to_groups.get(bare, set()))
            if len(exact_ids) == 1:
                entity = next(iter(exact_ids))
                seed_forms[entity].add(name)
                seed_forms[entity].add(bare)

    resolutions = {}
    ambiguous = []
    for name in sorted(observed):
        resolution = resolve_name(
            name, aliases=aliases, source_to_groups=source_to_groups, index=index,
            eligible=eligible, moe_entities=moe_entities, seed_forms=seed_forms,
            decisions=decisions,
        )
        resolutions[name] = resolution
        if resolution.get("ambiguous"):
            labels = {}
            for candidate in resolution["ambiguous"]:
                if candidate.startswith("moe:"):
                    label = index.chars[candidate[4:]].name
                elif candidate.startswith("glossary:"):
                    candidate_group = next((g for g in groups if g["key"] == candidate), None)
                    label = (candidate_group["sources"][0] if candidate_group and candidate_group["sources"]
                             else candidate_group["dest"] if candidate_group else candidate)
                else:
                    label = candidate.split(":", 1)[-1]
                labels.setdefault(label, []).append(candidate)
            ambiguous.append({
                "_key": "ambiguous:" + name, "Cách gọi": name,
                "Ứng viên": " | ".join(
                    label if len(keys) == 1 else f"{label} [{keys[0]}]"
                    for label, keys in sorted(labels.items())
                ),
                "Số lần": stats[name]["count"], "Quyết định của tôi": "",
            })

    chars = {}
    call_rows = defaultdict(list)
    for name, resolution in resolutions.items():
        key = resolution.get("key")
        if not key:
            continue
        call_rows[key].append({
            "name": name, "count": stats[name]["count"],
            "chapters": sorted(stats[name]["chapters"]),
            "level": resolution["level"],
        })
        char = chars.setdefault(key, {
            "key": key, "canonical_zh": "", "moegirl_page": "", "works": set(),
            "gender": "", "gender_source": "thiếu", "aliases": [], "latin_candidates": [],
            "calls": [], "first_chapter": None, "last_chapter": None, "chapter_count": 0,
            "descriptions": [], "match_level": "D", "warnings": [], "decision": "",
        })
        char["match_level"] = min((char["match_level"], resolution["level"]),
                                  key=lambda level: {"0": 0, "A": 1, "B": 2, "C": 3, "D": 4}[level])
        page = resolution.get("page") or seed_pages.get(key, "")
        if page:
            char["moegirl_page"] = page
            char["canonical_zh"] = index.chars[page].name
        elif key.startswith("role:") or key.startswith("ai:"):
            char["canonical_zh"] = key.split(":", 1)[1]
        else:
            char["canonical_zh"] = char["canonical_zh"] or name

    group_by_key = {group["key"]: group for group in groups}
    suggestions = {}
    for key, char in chars.items():
        page = char["moegirl_page"]
        if page:
            data = catalog.get("records", {}).get(page, {})
            info = index.chars[page]
            char["works"] = sorted(set(info.subjects) & approved_subjects)
            char["aliases"] = list(info.aliases)
            char["latin_candidates"] = data.get("latin_entries", [])
            if info.gender in ("male", "female"):
                char["gender"] = info.gender
                char["gender_source"] = "moegirl"
        group = group_by_key.get(key)
        if group and group["genders"]:
            char["gender"] = group["genders"][-1]
            char["gender_source"] = "thuatngu"
        if key.startswith("role:"):
            gender = _role_gender(char["canonical_zh"])
            if gender:
                char["gender"] = gender
                char["gender_source"] = "quy ước"
        override_id = key.split(":", 1)[1] if ":" in key else key
        override = (overrides.get(key) or overrides.get(override_id)
                    or overrides.get(char["canonical_zh"]) or {})
        if override.get("gender") in ("male", "female", "other"):
            char["gender"] = override["gender"]
            char["gender_source"] = "thủ công"
        char["calls"] = sorted(call_rows[key], key=lambda item: (-item["count"], item["name"]))
        char["reader_aliases"] = sorted({alias for call in char["calls"] for alias in reader_aliases[call["name"]]})
        all_chapters = sorted({n for call in char["calls"] for n in call["chapters"]})
        char["first_chapter"] = all_chapters[0] if all_chapters else None
        char["last_chapter"] = all_chapters[-1] if all_chapters else None
        char["chapter_count"] = len(all_chapters)
        char["descriptions"] = list(dict.fromkeys(
            description for call in char["calls"] for description in stats[call["name"]]["descriptions"]
        ))[:3]
        if (sum(stats[call["name"]]["count"] for call in char["calls"]) == 1
                and sum(stats[call["name"]]["relation_count"] for call in char["calls"]) == 0):
            char["warnings"].append("nghi ngờ: chỉ xuất hiện một lần, không có quan hệ")
        if char["canonical_zh"] in _GENERIC_ROLES:
            char["warnings"].append("nghi ngờ: chức danh chung")
        if char["gender_source"] == "thiếu":
            char["warnings"].append("chưa có giới tính")
        char["works"] = sorted(char["works"])
        char["aliases"] = sorted(set(char["aliases"]))
        char["calls"] = char["calls"]

    for name, result in resolutions.items():
        if result["level"] != "D" or len(name) < 2:
            continue
        matching = []
        name_chapters = stats[name]["chapters"]
        for key, char in chars.items():
            if key.startswith(("ai:", "role:")):
                continue
            forms = [char["canonical_zh"]]
            if char["moegirl_page"]:
                forms.extend(index.chars[char["moegirl_page"]].strong_aliases)
            if any(name in form and name != form for form in forms):
                other_chapters = {n for call in char["calls"] for n in call["chapters"]}
                if not name_chapters.intersection(other_chapters):
                    matching.append(f'{char["canonical_zh"]} [{key}]')
        if len(set(matching)) == 1:
            suggestions[name] = matching[0]

    relations = {}
    unresolved_relations = []
    for chapter in parsed["chapters"]:
        for rel in chapter["relations"]:
            source = resolutions[rel["source"]].get("key")
            target = resolutions[rel["target"]].get("key")
            if not source or not target:
                unresolved_relations.append({
                    "chapter": chapter["number"], **rel,
                    "source_candidates": resolutions[rel["source"]].get("ambiguous", []),
                    "target_candidates": resolutions[rel["target"]].get("ambiguous", []),
                })
                continue
            if source == target:
                continue
            relation_key = (chapter["number"], source, target, rel["type"])
            record = relations.setdefault(relation_key, {
                "chapter": chapter["number"], "source": source, "target": target,
                "type": rel["type"], "count": 0, "quotes": [],
            })
            record["count"] += 1
            if rel["quote"] and rel["quote"] not in record["quotes"] and len(record["quotes"]) < 2:
                record["quotes"].append(rel["quote"])

    for char in chars.values():
        char["works"] = list(char["works"])
    return {
        "chapters": parsed["chapters"], "characters": list(chars.values()),
        "relations": list(relations.values()), "unresolved_relations": unresolved_relations,
        "ambiguous": ambiguous, "resolutions": resolutions, "stats": stats,
        "suggestions": suggestions,
    }


def _entity_sheets(data: dict) -> dict[str, tuple[list[str], list[dict]]]:
    chars = data["characters"]
    character_rows = []
    for char in sorted(chars, key=lambda item: (
        -(item["chapter_count"]), item["canonical_zh"]
    )):
        character_rows.append({
            "_key": char["key"], "Khóa": char["key"], "Tên chuẩn (zh)": char["canonical_zh"],
            "Trang Moegirl": char["moegirl_page"], "Tác phẩm": " | ".join(char["works"]),
            "Giới tính": char["gender"], "Nguồn giới tính": char["gender_source"],
            "Bí danh Moegirl": " | ".join(char["aliases"]),
            "Bí danh AI Reader (chưa duyệt)": " | ".join(char.get("reader_aliases", [])),
            "Tên Latin ứng viên": " | ".join(
                f'{entry.get("name", "")} ({entry.get("confidence", "")})'
                for entry in char["latin_candidates"]
            ),
            "Cách gọi (số lần)": " | ".join(
                f'{call["name"]} ×{call["count"]} [{call["level"]}]' for call in char["calls"]
            ),
            "Chương đầu": char["first_chapter"], "Chương cuối": char["last_chapter"],
            "Số chương": char["chapter_count"], "Mô tả tiêu biểu": " | ".join(char["descriptions"]),
            "Mức khớp thấp nhất": char["match_level"],
            "Cảnh báo": " | ".join(char["warnings"]), "Quyết định của tôi": "",
        })
    unresolved = []
    for name, result in data["resolutions"].items():
        if result["level"] == "D":
            unresolved.append({
                "_key": "unmatched:" + name, "Cách gọi": name,
                "Số lần": data["stats"][name]["count"],
                "Số chương": len(data["stats"][name]["chapters"]),
                "Gợi ý chưa áp dụng": data["suggestions"].get(name, ""),
                "Quyết định của tôi": "",
            })
    warning_rows = [
        {"_key": f'warning:{char["key"]}:{i}', "Khóa": char["key"],
         "Nhân vật": char["canonical_zh"], "Cảnh báo": warning}
        for char in chars for i, warning in enumerate(char["warnings"], 1)
    ]
    chapter_rows = [{
        "_key": f'chapter:{chapter["number"]}', "Chương (file)": chapter["number"],
        "Chương gốc": chapter["source_number"], "Tiêu đề": chapter["title"],
        "Số nhân vật": len(chapter["characters"]), "Số quan hệ": len(chapter["relations"]),
    } for chapter in data["chapters"]]
    return {
        "Nhân vật": (list(character_rows[0].keys()) if character_rows else ["_key", "Khóa", "Tên chuẩn (zh)", "Quyết định của tôi"], character_rows),
        "Mơ hồ": (["_key", "Cách gọi", "Ứng viên", "Số lần", "Quyết định của tôi"], data["ambiguous"]),
        "Chưa khớp": (["_key", "Cách gọi", "Số lần", "Số chương", "Gợi ý chưa áp dụng", "Quyết định của tôi"], unresolved),
        "Cảnh báo": (["_key", "Khóa", "Nhân vật", "Cảnh báo"], warning_rows),
        "Chương": (["_key", "Chương (file)", "Chương gốc", "Tiêu đề", "Số nhân vật", "Số quan hệ"], chapter_rows),
    }


def _old_user_values(path: Path) -> dict[str, dict[str, dict[str, object]]]:
    result = defaultdict(dict)
    if not path.is_file():
        return result
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            if ws.max_row < 1:
                continue
            headers = {cell.value: cell.column for cell in ws[1]}
            key_col = headers.get("_key")
            if not key_col:
                continue
            user_cols = {name: col for name, col in headers.items() if name in _USER_COLUMNS}
            for row in ws.iter_rows(min_row=2, values_only=True):
                key = row[key_col - 1]
                if not key:
                    continue
                key = re.sub(r"^(pair:.*:\d+)-\d+$", r"\1", str(key))
                values = {name: row[col - 1] for name, col in user_cols.items()}
                if key in result[ws.title] and result[ws.title][key] != values:
                    raise ValueError(f"Khóa xưng hô trùng và có nội dung khác nhau: {key}")
                result[ws.title][key] = values
    finally:
        wb.close()
    return result


def merge_user_cells(path: Path, sheets: dict[str, tuple[list[str], list[dict]]],
                     *, orphan_sheet: str = "") -> dict[str, tuple[list[str], list[dict]]]:
    """Giữ ô người dùng theo khóa ổn định; chuyển các ô xưng hô mất khóa sang Mồ côi."""
    old = _old_user_values(path)
    output = {}
    current_keys = {}
    for name, (headers, rows) in sheets.items():
        merged_rows = []
        current_keys[name] = {str(row.get("_key", "")) for row in rows}
        for row in rows:
            row = dict(row)
            key = str(row.get("_key", ""))
            for column, value in old.get(name, {}).get(key, {}).items():
                if column in headers and value not in (None, ""):
                    row[column] = value
            merged_rows.append(row)
        output[name] = (headers, merged_rows)
    if orphan_sheet:
        existing_headers, existing_rows = output.get(orphan_sheet, (["_key", "Khóa cũ"], []))
        orphan_by_key = {str(row.get("_key")): dict(row) for row in existing_rows}
        for sheet_name, entries in old.items():
            if sheet_name in (orphan_sheet,):
                continue
            for key, values in entries.items():
                if key in current_keys.get(sheet_name, set()):
                    continue
                if not any(value not in (None, "") for value in values.values()):
                    continue
                orphan_key = f"orphan:{sheet_name}:{key}"
                row = {"_key": orphan_key, "Khóa cũ": f"{sheet_name}: {key}"}
                row.update(values)
                orphan_by_key[orphan_key] = row
        for key, values in old.get(orphan_sheet, {}).items():
            if key not in orphan_by_key:
                row = {"_key": key, "Khóa cũ": key}
                row.update(values)
                orphan_by_key[key] = row
        headers = list(dict.fromkeys(existing_headers + ["Khóa cũ"] + list(_USER_COLUMNS)))
        output[orphan_sheet] = (headers, list(orphan_by_key.values()))
    return output


def write_workbook(path: Path, sheets: dict[str, tuple[list[str], list[dict]]],
                   *, orphan_sheet: str = "", user_updates: dict | None = None) -> dict[str, tuple[list[str], list[dict]]]:
    sheets = merge_user_cells(path, sheets, orphan_sheet=orphan_sheet)
    if user_updates is not None:
        rows = {row["_key"]: row for row in sheets["Xưng hô"][1]}
        unknown = set(user_updates) - set(rows)
        if unknown:
            raise ValueError(f"Dòng xưng hô không còn tồn tại: {', '.join(sorted(unknown))}")
        for key, values in user_updates.items():
            for column, value in values.items():
                if column not in ("Tự xưng", "Gọi người nghe là", "Ghi chú của tôi"):
                    raise ValueError(f"Cột xưng hô không hợp lệ: {column}")
                rows[key][column] = value
    wb = Workbook()
    wb.remove(wb.active)
    for name, (headers, rows) in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{chr(64 + min(len(headers), 26))}{max(1, len(rows) + 1)}"
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = _HEADER
        for row in rows:
            ws.append([row.get(header, "") for header in headers])
        if "_key" in headers:
            ws.column_dimensions["A"].hidden = True
        for col, header in enumerate(headers, 1):
            if header in _USER_COLUMNS:
                for row_no in range(2, max(2, ws.max_row + 1)):
                    ws.cell(row_no, col).fill = _YELLOW
        for column in ws.columns:
            letter = column[0].column_letter
            width = min(max(max((len(str(cell.value or "")) for cell in column[:100]), default=0) + 2, 12), 60)
            ws.column_dimensions[letter].width = width
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        wb.save(temp_name)
        wb.close()
        os.replace(temp_name, path)
    except Exception:
        wb.close()
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return sheets


def _write_json(path: Path, value: dict) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    _atomic_text(path, text)


def run_entities(args: argparse.Namespace) -> dict:
    """Chạy parse, khớp dàn và xuất entities.xlsx/entities.json."""
    reader = Path(args.reader)
    out = Path(args.out)
    glossary_path = Path(args.glossary)
    aliases_path = Path(args.aliases)
    overrides_path = Path(args.overrides)
    if args.moegirl_dir:
        dataset_dir = find_dataset_dir(args.moegirl_dir)
    else:
        local_dataset = Path(__file__).resolve().parent / "moegirl"
        dataset_dir = (
            local_dataset if (local_dataset / "extra_processed.json").is_file()
            else find_dataset_dir()
        )
    approved = _approved_subjects(Path(args.works))
    parsed = parse_reader(reader)
    groups, source_to_groups = load_glossary_characters(glossary_path)
    aliases = _load_aliases(aliases_path)
    overrides = _load_overrides(overrides_path)
    index = MoegirlIndex(dataset_dir)
    eligible = _eligible_pages(index, approved)
    catalog = build_catalog(dataset_dir)
    previous_path = out / "entities.xlsx"
    decisions = _previous_decisions(previous_path)
    data = build_cast(
        parsed, index, catalog, groups, source_to_groups, aliases, eligible,
        approved, overrides, decisions,
    )
    sheets = _entity_sheets(data)
    write_workbook(previous_path, sheets)
    serializable = {
        "schema_version": 1,
        "title": parsed["title"],
        "source_file": str(reader.resolve()),
        "chapters": [
            {key: value for key, value in chapter.items() if key not in ("characters", "relations")}
            for chapter in parsed["chapters"]
        ],
        "characters": data["characters"],
        "relations": data["relations"],
        "unresolved_relations": data["unresolved_relations"],
        "ambiguous": data["ambiguous"],
    }
    _write_json(out / "entities.json", serializable)
    return data


def run_glossary(args: argparse.Namespace) -> tuple[int, list[str]]:
    """Sinh thuatngu.candidates.txt, không bao giờ ghi vào thuatngu.txt."""
    out = Path(args.out)
    entities_path = out / "entities.json"
    if not entities_path.is_file():
        raise FileNotFoundError(f"Chưa có {entities_path}; hãy chạy lệnh entities trước.")
    data = json.loads(_read_text(entities_path))
    groups, _ = load_glossary_characters(Path(args.glossary))
    present = defaultdict(set)
    for group in groups:
        for source in group["sources"]:
            present[source].add(group["dest"])
    characters = sorted(data.get("characters", []), key=lambda char: (-char["chapter_count"], char["canonical_zh"]))
    lines = ["# ỨNG VIÊN nhân vật — duyệt thủ công rồi tự chép vào thuatngu.txt.", ""]
    warnings = []
    regular, roles = [], []
    for char in characters:
        if char["match_level"] == "D" and not char["key"].startswith("role:"):
            continue
        calls = sorted({
            call["name"] for call in char["calls"]
            if call["level"] in ("0", "A", "B", "C")
        })
        if not calls:
            calls = [char["canonical_zh"]]
        candidates = {
            entry["name"] for entry in char.get("latin_candidates", [])
            if float(entry.get("confidence") or 0) >= 0.90
        }
        dest = next(iter(candidates)) if len(candidates) == 1 else ""
        if not dest:
            lines.append(f'# CHƯA CÓ ĐÍCH: {char["canonical_zh"]} (xuất hiện {char["chapter_count"]} chương)')
        is_role = char["key"].startswith("role:")
        gender = char.get("gender", "")
        source_tag = f'moegirl:{char["moegirl_page"]}' if char.get("moegirl_page") else "ai_reader"
        note = f'# nhân vật; gender={gender}; id={char["key"].split(":", 1)[1]}; nguồn={source_tag}'
        target_lines = roles if is_role else regular
        for source in calls:
            old_dests = present.get(source, set())
            if dest and dest in old_dests:
                continue
            if old_dests and (not dest or dest not in old_dests):
                warnings.append(f'Xung đột glossary: "{source}" hiện có đích {", ".join(sorted(old_dests))}; đề xuất {dest or "(trống)"}.')
                continue
            target_lines.append(f"{source} => {dest} {note}".rstrip())
    lines.extend(regular)
    if roles:
        lines.extend(["", "# Nhãn vai — đích để trống, không tự phiên âm."])
        lines.extend(re.sub(r" =>  #", " =>  #", line) for line in roles)
    if warnings:
        lines.extend(["", "# CẢNH BÁO XUNG ĐỘT"])
        lines.extend("# " + warning for warning in warnings)
    _atomic_text(out / "thuatngu.candidates.txt", "\n".join(lines) + "\n", bom=True)
    return len(regular) + len(roles), warnings


def _load_relation_types(path: Path, observed: Counter) -> dict[str, str]:
    if path.exists():
        mapping = {}
        for line in _read_text(path).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pair = _split_mapping(line)
            if pair:
                mapping[pair[0]] = pair[1] or "?"
    else:
        mapping = {key: value for key, value in _REL_DEFAULTS.items()}
        mapping.update({kind: "?" for kind in _NOISE_RELATIONS})
        _atomic_text(path, "# Loại quan hệ gốc => nhóm; để ? để bỏ qua khi phân nhóm\r\n" + "".join(
            f"{kind} => {group}\r\n" for kind, group in mapping.items()
        ))
    unknown = [kind for kind in observed if kind not in mapping]
    if unknown:
        with path.open("a", encoding="utf-8", newline="") as stream:
            for kind in unknown:
                mapping[kind] = "?"
                stream.write(f"{kind} => ?\r\n")
    for kind in observed:
        if any(kind.startswith(prefix) for prefix in _NOISE_RELATIONS) and kind not in mapping:
            mapping[kind] = "?"
    return mapping


def _approved_glossary_map(groups: list[dict]) -> tuple[dict[str, set[str]], dict[str, dict]]:
    lookup = {}
    by_key = {group["key"]: group for group in groups}
    for group in groups:
        lookup.setdefault(group["key"], set()).add(group["key"])
        for source in group["sources"]:
            lookup.setdefault(source, set()).add(group["key"])
        if group["dest"]:
            lookup.setdefault(group["dest"], set()).add(group["key"])
    return lookup, by_key


def match_glossary_characters(characters: list[dict], path: Path) -> tuple[dict, dict]:
    """Khớp chính xác tên nhân vật với glossary thủ công, không sửa glossary."""
    groups, source_groups = load_glossary_characters(path)
    lookup, groups_by_key = _approved_glossary_map(groups)
    raw_terms = defaultdict(set)
    han = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
    if Path(path).is_file():
        for line in _read_text(path).splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            pair = _split_mapping(line.partition("#")[0])
            if not pair:
                continue
            left, right = pair
            if not left or not right:
                continue
            left_han, right_han = bool(han.search(left)), bool(han.search(right))
            if left_han == right_han:
                continue
            source, dest = (left, right) if left_han else (right, left)
            raw_terms[source].add(dest)
    owners = defaultdict(set)
    for char in characters:
        for call in char.get("calls", []):
            owners[call["name"]].add(char["key"])
    matched, conflicts = {}, {}
    for char in characters:
        key = char["key"]
        calls = char.get("calls", [])
        explicit = {candidate for value in (key, char.get("canonical_zh", ""),
                    char.get("moegirl_page", ""), *(c["name"] for c in calls))
                    for candidate in lookup.get(value, set())}
        if len(explicit) > 1:
            conflicts[key] = "Nhiều nhân vật đã duyệt cùng khớp."
            continue
        if explicit:
            group = groups_by_key[next(iter(explicit))]
            matched[key] = {"dest": group["dest"], "group": group["key"],
                            "genders": group["genders"], "origin": "thủ công"}
            continue
        possibilities = {c["name"]: raw_terms[c["name"]] for c in calls
                         if c["name"] in raw_terms and len(owners[c["name"]]) == 1}
        if not possibilities:
            continue
        canonical = char.get("canonical_zh", "")
        selected = {canonical: possibilities[canonical]} if canonical in possibilities else possibilities
        destinations = set().union(*selected.values())
        if len(destinations) != 1:
            conflicts[key] = "Tên trong glossary có nhiều đích; cần duyệt thủ công."
            continue
        matched[key] = {"dest": next(iter(destinations)), "group": "", "genders": [],
                        "origin": "glossary"}
    by_group = defaultdict(list)
    for key, entry in matched.items():
        if entry["group"]:
            by_group[entry["group"]].append(key)
    for group, keys in by_group.items():
        if len(keys) > 1:
            for key in keys:
                matched.pop(key)
                conflicts[key] = f"Nhiều nhân vật cùng khớp {group}; cần xử lý thủ công."
    return matched, conflicts


def build_pairs(data: dict, approved_keys: set[str], rel_types: dict[str, str],
                min_run: int = 2) -> tuple[list[dict], list[dict]]:
    """Lọc cặp đã duyệt và gộp quan hệ từng chương theo nhóm chính."""
    chars = {char["key"]: char for char in data["characters"]}
    waiting = []
    for char in data["characters"]:
        if char["key"] not in approved_keys:
            waiting.append({
                "_key": f'waiting-char:{char["key"]}',
                "Chương": f'{char.get("first_chapter") or ""}-{char.get("last_chapter") or ""}',
                "A": char["canonical_zh"], "B": "", "Loại": "",
                "Số lần": char.get("chapter_count", 0),
                "Trích dẫn": " | ".join(char.get("descriptions", [])),
                "Lý do": "Chưa có nhãn nhân vật đã duyệt trong thuatngu.txt",
            })
    per_pair_chapter = defaultdict(list)
    for rel in data["relations"]:
        a, b = rel["source"], rel["target"]
        if a not in approved_keys or b not in approved_keys:
            waiting.append({
                "_key": f'waiting:{rel["chapter"]}:{a}:{b}:{rel["type"]}',
                "Chương": rel["chapter"],
                "A": chars.get(a, {}).get("canonical_zh", a),
                "B": chars.get(b, {}).get("canonical_zh", b), "Loại": rel["type"],
                "Số lần": rel["count"], "Trích dẫn": " | ".join(rel["quotes"]),
                "Lý do": "Chưa duyệt đủ cả hai nhân vật",
            })
            continue
        pair = tuple(sorted((a, b)))
        group = rel_types.get(rel["type"], "?")
        per_pair_chapter[(pair, rel["chapter"])].append((group, rel))

    pair_chapters = defaultdict(dict)
    for (pair, chapter), relations in per_pair_chapter.items():
        non_noise = [(group, rel) for group, rel in relations if group != "?"]
        if non_noise:
            tally = Counter()
            for group, rel in non_noise:
                tally[group] += rel["count"]
            main_group = sorted(tally, key=lambda group: (-tally[group], group))[0]
        else:
            main_group = "Chưa rõ (chỉ tương tác)"
        pair_chapters[pair][chapter] = {"group": main_group, "relations": relations}

    phases = []
    for pair, chapter_map in pair_chapters.items():
        chapters = sorted(chapter_map)
        runs = []
        for chapter in chapters:
            group = chapter_map[chapter]["group"]
            if runs and runs[-1]["group"] == group and chapter == runs[-1]["chapters"][-1] + 1:
                runs[-1]["chapters"].append(chapter)
            else:
                runs.append({"group": group, "chapters": [chapter]})
        while len(runs) > 1:
            short_index = next(
                (idx for idx, run in enumerate(runs) if len(run["chapters"]) < min_run),
                None,
            )
            if short_index is None:
                break
            neighbor = short_index - 1 if short_index else 1
            runs[neighbor]["chapters"] = sorted(
                runs[neighbor]["chapters"] + runs[short_index]["chapters"]
            )
            runs.pop(short_index)
        for run_index, run in enumerate(runs):
            span = run["chapters"]
            evidence = [chapter_map[n] for n in span if n in chapter_map]
            rel_counts = Counter()
            quotes = []
            for item in evidence:
                for _, rel in item["relations"]:
                    signature = f'{rel["source"]} —{rel["type"]}→ {rel["target"]}'
                    rel_counts[signature] += rel["count"]
                    for quote in rel["quotes"]:
                        if quote not in quotes and len(quotes) < 2:
                            quotes.append(quote)
            a, b = pair
            phases.append({
                "pair": pair, "chapters": span, "group": run["group"],
                "end": runs[run_index + 1]["chapters"][0] - 1 if run_index + 1 < len(runs) else len(data["chapters"]),
                "relations": " | ".join(f"{key} ×{count}" for key, count in rel_counts.items()),
                "quotes": " | ".join(quotes),
                "a_chapter_count": chars[a]["chapter_count"], "b_chapter_count": chars[b]["chapter_count"],
            })

    rows = []
    chapter_lookup = {chapter["number"]: chapter for chapter in data["chapters"]}
    for phase in phases:
        start, end = phase["chapters"][0], phase["end"]
        originals = [chapter_lookup[n]["source_number"] for n in phase["chapters"]]
        start_original = next((n for n in originals if n is not None), "")
        for speaker, listener in (phase["pair"], phase["pair"][::-1]):
            speaker_char, listener_char = chars[speaker], chars[listener]
            rows.append({
                "_key": f'pair:{speaker}>{listener}:{start}',
                "_speaker": speaker, "_listener": listener,
                "Người nói": f'{speaker_char["canonical_zh"]} ({speaker_char.get("display_dest", "")})'.strip(" ()"),
                "Người nghe": f'{listener_char["canonical_zh"]} ({listener_char.get("display_dest", "")})'.strip(" ()"),
                "Từ chương (file)": start, "Đến chương": "hết" if end == len(data["chapters"]) else end,
                "Chương gốc (từ)": start_original, "Nhóm quan hệ": phase["group"],
                "Số chương": len(phase["chapters"]), "Quan hệ theo file": phase["relations"],
                "2 câu trích mẫu": phase["quotes"], "Tự xưng": "",
                "Gọi người nghe là": "", "Ghi chú của tôi": "",
                "_sort_count": max(phase["a_chapter_count"], phase["b_chapter_count"]),
            })
    rows.sort(key=lambda row: (-row["_sort_count"], row["Từ chương (file)"], row["Người nói"]))
    return rows, waiting


def _load_rel_types_path(path: Path, data: dict) -> dict[str, str]:
    counts = Counter(rel["type"] for rel in data["relations"])
    return _load_relation_types(path, counts)


def run_xungho(args: argparse.Namespace) -> tuple[int, int]:
    """Tạo xungho.xlsx/.txt chỉ từ nhân vật có nhãn nhân vật đã duyệt."""
    out = Path(args.out)
    entities_path = out / "entities.json"
    if not entities_path.is_file():
        raise FileNotFoundError(f"Chưa có {entities_path}; hãy chạy lệnh entities trước.")
    data = json.loads(_read_text(entities_path))
    matches, conflicts = match_glossary_characters(data.get("characters", []), Path(args.glossary))
    approved_keys = set(matches)
    char_by_key = {char["key"]: char for char in data.get("characters", [])}
    for char in data.get("characters", []):
        match = matches.get(char["key"])
        if match:
            char["display_dest"] = match["dest"]
            if match["genders"]:
                char["gender"] = match["genders"][-1]
        if char["key"] in conflicts:
            char.setdefault("warnings", []).append(conflicts[char["key"]])
    rel_types_path = Path(args.rel_types)
    rel_types = _load_rel_types_path(rel_types_path, data)
    rows, waiting = build_pairs(data, approved_keys, rel_types, args.min_run)
    for char in data.get("characters", []):
        if char["key"] in approved_keys and char["gender"]:
            continue
        if char["key"] in approved_keys:
            char.setdefault("warnings", []).append("thiếu giới tính trong glossary")
    character_rows = [{
        "_key": f'character:{key}', "Khóa": key,
        "Đích": matches[key]["dest"],
        "Tên Hán": char["canonical_zh"],
        "Các cách gọi": " | ".join(call["name"] for call in char.get("calls", [])),
        "Giới tính": char.get("gender", ""), "Cảnh báo": " | ".join(char.get("warnings", [])),
        "Chương đầu": char.get("first_chapter"), "Chương cuối": char.get("last_chapter"),
        "Số chương": char.get("chapter_count"),
    } for key, char in char_by_key.items() if key in approved_keys]
    ambiguous_rows = [{
        "_key": f'ambiguous:{item.get("Cách gọi", "")}',
        **{key: value for key, value in item.items() if key != "_key"},
    } for item in data.get("ambiguous", [])]
    warning_rows = [{
        "_key": f'warning:{char["key"]}:{idx}',
        "Khóa": char["key"], "Nhân vật": char["canonical_zh"], "Cảnh báo": warning,
    } for char in data.get("characters", []) for idx, warning in enumerate(char.get("warnings", []), 1)]
    sheets = {
        "Xưng hô": ([
            "_key", "Người nói", "Người nghe", "Từ chương (file)", "Đến chương",
            "Chương gốc (từ)", "Nhóm quan hệ", "Số chương", "Quan hệ theo file",
            "2 câu trích mẫu", "Tự xưng", "Gọi người nghe là", "Ghi chú của tôi",
        ], rows),
        "Nhân vật": (["_key", "Khóa", "Đích", "Tên Hán", "Các cách gọi", "Giới tính",
                      "Cảnh báo", "Chương đầu", "Chương cuối", "Số chương"], character_rows),
        "Chờ duyệt": (["_key", "Chương", "A", "B", "Loại", "Số lần", "Trích dẫn", "Lý do"], waiting),
        "Mơ hồ": (["_key", "Cách gọi", "Ứng viên", "Số lần"], ambiguous_rows),
        "Mồ côi": (["_key", "Khóa cũ", "Tự xưng", "Gọi người nghe là", "Ghi chú của tôi"], []),
        "Cảnh báo": (["_key", "Khóa", "Nhân vật", "Cảnh báo"], warning_rows),
    }
    written_sheets = write_workbook(out / "xungho.xlsx", sheets, orphan_sheet="Mồ côi",
                                    user_updates=getattr(args, "user_updates", None))
    txt_lines = []
    rules = []
    for row in written_sheets["Xưng hô"][1]:
        if any(str(row[column] or "").strip() for column in ("Tự xưng", "Gọi người nghe là", "Ghi chú của tôi")):
            parts = []
            if row["Tự xưng"]:
                parts.append(f'tự xưng "{row["Tự xưng"]}"')
            if row["Gọi người nghe là"]:
                parts.append(f'gọi "{row["Gọi người nghe là"]}"')
            if row["Ghi chú của tôi"]:
                parts.append(f'ghi chú: {row["Ghi chú của tôi"]}')
            txt_lines.append(
                f'{row["Người nói"]} -> {row["Người nghe"]} [chương {row["Từ chương (file)"]}-{row["Đến chương"]}]: '
                + "; ".join(parts)
            )
            rules.append({
                "speaker": row["_speaker"], "listener": row["_listener"],
                "start": row["Từ chương (file)"],
                "end": len(data["chapters"]) if row["Đến chương"] == "hết" else row["Đến chương"],
                "self": str(row["Tự xưng"] or "").strip(),
                "address": str(row["Gọi người nghe là"] or "").strip(),
                "note": str(row["Ghi chú của tôi"] or "").strip(),
            })
    _atomic_text(out / "xungho.txt", "\n".join(txt_lines) + ("\n" if txt_lines else ""), bom=True)
    characters = {}
    for key in approved_keys:
        char = char_by_key[key]
        characters[key] = {
            "display": char.get("display_dest", char["canonical_zh"]),
            "names": sorted({char["canonical_zh"], char.get("display_dest", ""),
                             *(call["name"] for call in char.get("calls", []))} - {""}),
        }
    glossary_path = Path(args.glossary)
    _write_json(out / "xungho.rules.json", {
        "schema_version": 1,
        "glossary_sha256": hashlib.sha256(glossary_path.read_bytes()).hexdigest() if glossary_path.is_file() else "",
        "chapters": data["chapters"], "characters": characters, "rules": rules,
    })
    return len(rows), len(waiting)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Công cụ nhân vật AI Reader, chạy offline.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    entities = subparsers.add_parser("entities", help="Chuẩn hóa nhân vật từ báo cáo AI Reader")
    entities.add_argument("--reader", required=True)
    entities.add_argument("--moegirl-dir", default="")
    entities.add_argument("--works", default="moegirl_works_approved.txt")
    entities.add_argument("--glossary", default="thuatngu.txt")
    entities.add_argument("--aliases", default="aliases.txt")
    entities.add_argument("--overrides", default="overrides.tsv")
    entities.add_argument("--out", default=".")
    glossary = subparsers.add_parser("glossary", help="Sinh thuật ngữ nhân vật ứng viên")
    glossary.add_argument("--glossary", default="thuatngu.txt")
    glossary.add_argument("--out", default=".")
    xungho = subparsers.add_parser("xungho", help="Tạo bảng xưng hô nhân vật đã duyệt")
    xungho.add_argument("--glossary", default="thuatngu.txt")
    xungho.add_argument("--rel-types", default="rel_types.txt")
    xungho.add_argument("--min-run", type=int, default=2)
    xungho.add_argument("--out", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        if args.command == "entities":
            data = run_entities(args)
            levels = Counter(char["match_level"] for char in data["characters"])
            unmatched = sum(char["match_level"] == "D" for char in data["characters"])
            print(
                f"entities: {len(data['characters'])} nhân vật; mức 0/A/B/C/D = "
                f"{levels['0']}/{levels['A']}/{levels['B']}/{levels['C']}/{levels['D']}; "
                f"{len(data['ambiguous'])} mơ hồ; {unmatched} chưa khớp; "
                f"{len(data['relations'])} quan hệ."
            )
        elif args.command == "glossary":
            count, warnings = run_glossary(args)
            print(f"glossary: {count} dòng ứng viên; {len(warnings)} cảnh báo xung đột.")
            for warning in warnings:
                print("CẢNH BÁO:", warning)
        else:
            if args.min_run < 1:
                raise ValueError("--min-run phải >= 1")
            pairs, waiting = run_xungho(args)
            print(f"xungho: {pairs} dòng xưng hô; {waiting} quan hệ chờ duyệt.")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Lỗi: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
