#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chỉ mục Moegirl trong RAM cho giao diện chọn tác phẩm.

Dựng một lần (~0,4 giây), truy vấn là tra dict. Cần moegirl_dataset_import.py cùng thư mục.

    idx = MoegirlIndex(r"D:\\...\\moegirl",
                       approved_file=r"...\\moegirl_works_approved.txt",   # lấy work_title
                       titles_file=r"...\\subject_titles.tsv")             # tên Latin bổ sung
    idx.search_subjects("青春")            # tìm theo tên Hán HOẶC tên Latin ("oregairu")
    idx.work_label("声之形")               # "声之形 (A Silent Voice)"
    idx.characters_of("声之形")
    idx.find_pages("hachiman")             # tên Hán hoặc Latin, không phân biệt hoa thường/thứ tự
    idx.subjects_of("毛利兰")

subject_titles.tsv (TAB, có dòng tiêu đề):  subject_key <TAB> latin_title   (nhiều tên: ngăn bằng |)
"""
from __future__ import annotations
import csv
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from moegirl_dataset_import import extract_aliases_ex, extract_fields

_PAREN_TAIL_RE = re.compile(r"[\(（][^\)）]*[\)）]\s*$")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def strip_page_suffix(title: str) -> str:
    """'艾丝黛尔(明日方舟)' -> '艾丝黛尔'."""
    return _PAREN_TAIL_RE.sub("", title).strip() or title.strip()


def latin_tokens(s: str) -> list:
    """'Hikigaya Hachiman' / 'Ōkami-san' -> ['hikigaya','hachiman'] / ['okami','san']."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.findall(r"[a-z0-9]+", s)


def is_latin_query(s: str) -> bool:
    return bool(_LATIN_RE.search(s)) and not _CJK_RE.search(s)


def _load(path: Path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_tsv(path):
    path = Path(path)
    if not path.is_file():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return []
    return [{(k or "").strip(): (v or "").strip() if isinstance(v, str) or v is None else ""
             for k, v in row.items() if k is not None}
            for row in csv.DictReader(lines, delimiter="\t")]


@dataclass
class CharInfo:
    page_title: str
    name: str                      # tên trang đã bỏ hậu tố (...)
    aliases: list = field(default_factory=list)
    latin_names: list = field(default_factory=list)
    subjects: list = field(default_factory=list)
    gender: str = ""
    strong_aliases: list = field(default_factory=list)
    weak_aliases: list = field(default_factory=list)


class MoegirlIndex:
    def __init__(self, dataset_dir, approved_file=None, titles_file=None):
        d = Path(dataset_dir)
        char2subject = _load(d / "char2subject.json")
        if not isinstance(char2subject, dict):
            raise FileNotFoundError(f"Không đọc được {d / 'char2subject.json'}")
        extra = _load(d / "extra_processed.json") or {}
        gender = _load(d / "char2gender.json") or {}
        subject_index = _load(d / "subject_index.json")

        self.chars: dict[str, CharInfo] = {}
        self._by_subject: dict[str, list] = defaultdict(list)
        self._by_name: dict[str, set] = defaultdict(set)      # tên mạnh -> tên trang
        self._by_weak_name: dict[str, set] = defaultdict(set) # biệt danh ngắn -> tên trang
        self._latin_compact: dict[str, set] = defaultdict(set)  # 'hikigayahachiman' -> tên trang
        self._latin_tok: dict[str, set] = defaultdict(set)      # 'hachiman' -> tên trang

        for title, subjects in char2subject.items():
            info = extra.get(title) if isinstance(extra, dict) else None
            aliases, latin = extract_fields(info) if isinstance(info, dict) else ([], [])
            _, strong_aliases, weak_aliases = extract_aliases_ex(info, title)
            subs = [str(s) for s in subjects] if isinstance(subjects, list) else []
            ch = CharInfo(title, strip_page_suffix(title), aliases, latin, subs,
                          gender.get(title, "") if isinstance(gender, dict) else "",
                          strong_aliases, weak_aliases)
            self.chars[title] = ch
            for s in subs:
                self._by_subject[s].append(title)
            for n in (title, ch.name, *strong_aliases):
                self._by_name[n].add(title)
            for n in weak_aliases:
                self._by_weak_name[n].add(title)
            for lat in latin:
                toks = latin_tokens(lat)
                if toks:
                    self._latin_compact["".join(toks)].add(title)
                    for t in set(toks):
                        self._latin_tok[t].add(title)

        if isinstance(subject_index, list) and subject_index:
            self.works = [s for s in subject_index if s in self._by_subject]
        else:
            self.works = sorted(self._by_subject)
        self._works_set = set(self.works)

        # Tên Latin của tác phẩm: dataset không có, lấy từ file của bạn.
        self.work_titles: dict[str, list] = {}
        self._title_compact: dict[str, str] = {}
        if approved_file:
            for row in _read_tsv(approved_file):
                title = row.get("work_title", "")
                for key in (k.strip() for k in row.get("subject_keys", "").split("|") if k.strip()):
                    if title:
                        self._add_title(key, [title])
        if titles_file:
            for row in _read_tsv(titles_file):
                key = row.get("subject_key", "")
                titles = [t.strip() for t in row.get("latin_title", "").split("|") if t.strip()]
                if key and titles:
                    self._add_title(key, titles)

    def _add_title(self, subject, titles):
        cur = self.work_titles.setdefault(subject, [])
        for t in titles:
            if t not in cur:
                cur.append(t)
        self._title_compact[subject] = "".join("".join(latin_tokens(t)) for t in cur)

    # ---- tác phẩm ----
    def work_label(self, subject: str) -> str:
        t = self.work_titles.get(subject)
        return f"{subject} ({t[0]})" if t else subject

    def work_size(self, subject: str) -> int:
        return len(self._by_subject.get(subject, ()))

    def search_subjects(self, text: str, limit: int = 50):
        """Tác phẩm có tên Hán hoặc tên Latin chứa `text`, xếp theo số nhân vật."""
        text = text.strip()
        if not text:
            return []
        q = "".join(latin_tokens(text)) if is_latin_query(text) else ""
        hits = [(s, self.work_size(s)) for s in self.works
                if text in s or (q and q in self._title_compact.get(s, ""))]
        hits.sort(key=lambda x: (-x[1], x[0]))
        return hits[:limit]

    def characters_of(self, subject: str):
        return [self.chars[t] for t in self._by_subject.get(subject, ())]

    # ---- nhân vật ----
    def pages_for_name(self, name: str, *, include_weak: bool = False):
        """Tra chính xác; mặc định không dùng biệt danh ngắn trong nhận dạng tự động."""
        key = name.strip()
        pages = set(self._by_name.get(key, ()))
        if include_weak:
            pages.update(self._by_weak_name.get(key, ()))
        return sorted(pages)

    def _latin_pages(self, text: str):
        toks = latin_tokens(text)
        if not toks:
            return []
        compact = "".join(toks)
        exact = set(self._latin_compact.get(compact, ()))
        cand = None  # mọi từ của truy vấn là tiền tố của một từ trong tên (không cần đúng thứ tự)
        for q in toks:
            pages = {p for tok, ps in self._latin_tok.items() if tok.startswith(q) for p in ps}
            cand = pages if cand is None else cand & pages
            if not cand:
                break
        cand = (cand or set()) - exact
        sub = {p for k, ps in self._latin_compact.items() if compact in k for p in ps} - exact - cand
        return sorted(exact) + sorted(cand) + sorted(sub)

    def find_pages(self, text: str, limit: int = 50, *, include_weak: bool = True):
        """Tra cứu người dùng: tên mạnh/Latin chứa chuỗi và biệt danh yếu khớp chính xác."""
        text = text.strip()
        if not text:
            return []
        out = list(self.pages_for_name(text, include_weak=include_weak))
        if is_latin_query(text):
            out += [p for p in self._latin_pages(text) if p not in out]
        sub = sorted({p for n, ps in self._by_name.items() if text in n for p in ps})
        out += [p for p in sub if p not in out]
        return out[:limit]

    def search_characters(self, text: str, limit: int = 50):
        return [self.chars[t] for t in self.find_pages(text, limit)]

    def subjects_of(self, name: str, works_only: bool = True):
        """Các tác phẩm mà tên này có thể thuộc về; tên mơ hồ chia đều phiếu."""
        pages = set(self.pages_for_name(name)) or set(self.find_pages(name, 20, include_weak=False))
        votes = defaultdict(float)
        for p in pages:
            for s in self.chars[p].subjects:
                if works_only and s not in self._works_set:
                    continue
                votes[s] += 1.0 / len(pages)
        return sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))


if __name__ == "__main__":
    import argparse, time
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset_dir")
    ap.add_argument("--subject")
    ap.add_argument("--name")
    ap.add_argument("--approved")
    ap.add_argument("--titles")
    a = ap.parse_args()
    t = time.perf_counter()
    idx = MoegirlIndex(a.dataset_dir, a.approved, a.titles)
    print(f"Dựng chỉ mục: {time.perf_counter()-t:.2f}s; {len(idx.chars):,} nhân vật, {len(idx.works):,} tác phẩm")
    if a.subject:
        for s, n in idx.search_subjects(a.subject, 10):
            print(f"  {idx.work_label(s)}\t{n} nhân vật")
    if a.name:
        for p in idx.find_pages(a.name, 10):
            print(f"  {p}  {idx.chars[p].latin_names}")