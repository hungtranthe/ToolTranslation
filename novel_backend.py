#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel_backend.py
================
Backend cho bộ giao diện "Trình dịch tiểu thuyết Trung -> Việt" (7 màn hình A-G).

- Chỉ dùng thư viện chuẩn Python (psutil là tùy chọn, chỉ để đo CPU/RAM).
- Không sửa engine (dichTrungsangViet.py): chạy một bản sao tạm đã được vá bằng AST
  (đổi hằng số cấu hình + chèn "hook" ghi checkpoint/tạm dừng).
- Mọi thao tác ghi file đều nguyên tử, có khóa và backup; không có thao tác nào tự
  động ghi vào thuatngu.txt nếu người dùng chưa xác nhận.

Ánh xạ màn hình -> dịch vụ (xem NovelBackend ở cuối file):
  A Tổng quan & Cấu hình   -> project.*, config.*, ollama.*, system.*, run.preflight
  B Báo cáo tác phẩm       -> works.*
  C Nhân vật mốc           -> anchors.*
  D Gợi ý Moegirl          -> moegirl.*
  E Quản lý Glossary       -> glossary.*
  F Dịch & Tiến trình      -> run.*
  G Nhật ký & Kiểm tra     -> logs.*, guard.*
"""

from __future__ import annotations

import ast
import bisect
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qs, urlparse

APP_VERSION = "1.0.0"
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
UNTRANSLATED_TAG = "[CHƯA DỊCH - GIỮ BẢN GỐC]"
# Thư mục gốc D:\8man\code — nơi chứa moegirl_dataset_import.py và moegirl_terms.json
# (engine dichTrungsangViet.py nằm trong stitch_noveltranslator_desktop_gui).
CODE_ROOT = Path(__file__).resolve().parent.parent
# Thư mục chứa novel_backend.py, names_bgm_service.py và cache dùng chung (bgm_cache.json, moegirl_wikitext_names.json).
PROGRAM_DIR = Path(__file__).resolve().parent
NAMES_BGM_FILE = "moegirl_wikitext_names_bgm.json"


# ============================================================
# 1. TIỆN ÍCH CHUNG
# ============================================================

class ApiError(Exception):
    """Lỗi nghiệp vụ có mã máy đọc được; transport chuyển thành {"ok": False, ...}."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def ok(**data: Any) -> dict:
    return {"ok": True, **data}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def short_id(*parts: Any, n: int = 8) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:n]


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def read_text_auto(path: Path | str) -> str:
    """BOM UTF-16 -> UTF-8(-BOM) -> GB18030 -> UTF-8 thay ký tự lỗi. Chuẩn hóa về \\n."""
    return decode_bytes(Path(path).read_bytes())[0]


def decode_bytes(data: bytes) -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16").replace("\r\n", "\n"), "utf-16"
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding).replace("\r\n", "\n"), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n"), "utf-8-replace"


@contextmanager
def file_lock(path: Path, timeout: float = 5.0, stale: float = 30.0):
    """Khóa tệp đơn giản bằng <file>.lock (O_EXCL); khóa cũ hơn `stale` giây bị bỏ qua."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise ApiError("FILE_LOCKED", f"File đang được tiến trình khác ghi: {path.name}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_replace(path: Path, text: str) -> None:
    """Ghi tmp + os.replace KHÔNG khóa (dùng khi đã cầm file_lock ở ngoài)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def atomic_write(path: Path | str, text: str, *, backup: bool = False, keep: int = 10) -> Optional[Path]:
    """Ghi nguyên tử (tmp + os.replace). backup=True: sao lưu bản cũ thành .bak_YYYYmmdd_HHMMSS."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Optional[Path] = None
    with file_lock(path):
        if backup and path.is_file():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = path.with_name(f"{path.name}.bak_{stamp}")
            n = 1
            while backup_path.exists():
                backup_path = path.with_name(f"{path.name}.bak_{stamp}_{n}")
                n += 1
            shutil.copy2(path, backup_path)
            old = sorted(path.parent.glob(path.name + ".bak_*"), key=lambda p: p.stat().st_mtime)
            for stale_file in old[:-keep] if keep > 0 else []:
                try:
                    stale_file.unlink()
                except OSError:
                    pass
        _atomic_replace(path, text)
    return backup_path


def load_module_from(path: Path | str, name: str):
    """Nạp module theo đường dẫn (đăng ký vào sys.modules để dataclass hoạt động)."""
    path = Path(path)
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Không nạp được {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def page(items: list, page_no: int, page_size: int) -> dict:
    page_size = max(1, min(int(page_size or 50), 500))
    total = len(items)
    pages = max(1, math.ceil(total / page_size))
    page_no = max(1, min(int(page_no or 1), pages))
    start = (page_no - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "total": total, "page": page_no, "pages": pages, "page_size": page_size,
    }


def make_matcher(query: str, *, case: bool = False, whole: bool = False, regex: bool = False):
    """Bộ khớp cho ô tìm kiếm có 3 nút Aa / \\b / .* của giao diện."""
    query = query or ""
    if not query:
        return lambda text: True
    pattern = query if regex else re.escape(query)
    if whole:
        pattern = r"\b(?:" + pattern + r")\b"
    try:
        compiled = re.compile(pattern, 0 if case else re.IGNORECASE)
    except re.error as exc:
        raise ApiError("BAD_REGEX", f"Biểu thức chính quy không hợp lệ: {exc}")
    return lambda text: compiled.search(text or "") is not None


# ============================================================
# 2. CẤU HÌNH DỰ ÁN (màn hình A)
# ============================================================

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 4,
    "source": "",
    "output_name": "bandich.txt",
    "engine": "",
    # "script" = chạy dichTrungsangViet.py qua EngineRunner, "deepseek" = deepseek_service.py (gọi API DeepSeek).
    "engine_mode": "script",
    "glossary": "",
    "ollama": {"base_url": "http://127.0.0.1:11434", "model": ""},
    "translate": {
        "max_chars": 1500, "max_retries": 10, "max_invalid_retries": 2,
        "delay": 0.5, "temperature": 0.1, "top_p": 0.6, "target_language": "越南语",
        "workers": 1, "rpm_limit": 0, "tpm_limit": 0, "context_tokens": 8192,
        "max_bisect_depth": 2, "preserve_indent": True,
    },
    "guard": {
        "enabled": True, "min_source_len": 100, "min_length_ratio": 0.9,
        "max_length_ratio": 8.0, "max_han_ratio": 0.15, "strict": False,
    },
    "moegirl": {
        "enabled": True, "scope": "approved_works", "max_hints_per_chunk": 30,
        "dataset_dir": "", "terms_file": "", "work_subjects": {},
        "min_frequency": 3, "min_confidence": 0.0,
        "exclude_subjects": ["中国作品", "日本作品", "美国作品", "韩国作品", "欧美作品", "各地区"],
    },
    "auto_discover": {"enabled": False, "min_frequency": 3, "max_candidates": 30},
}

# Hai engine dịch mà giao diện cho chọn ở màn hình E.
ENGINE_MODES = ("deepseek", "script")
ENGINE_MODE_LABELS = {"deepseek": "DeepSeek API", "script": "dichTrungsangViet.py"}

# khóa -> (kiểu, min, max)
CONFIG_RULES: dict[str, tuple[type, float, float]] = {
    "translate.max_chars": (int, 100, 4000),
    "translate.max_retries": (int, 1, 50),
    "translate.max_invalid_retries": (int, 1, 20),
    "translate.delay": (float, 0, 60),
    "translate.workers": (int, 1, 16),
    "translate.rpm_limit": (int, 0, 100000),
    "translate.tpm_limit": (int, 0, 100000000),
    "translate.context_tokens": (int, 512, 1048576),
    "translate.max_bisect_depth": (int, 0, 5),
    "translate.temperature": (float, 0, 2),
    "translate.top_p": (float, 0, 1),
    "guard.min_source_len": (int, 0, 10000),
    "guard.min_length_ratio": (float, 0.1, 20),
    "guard.max_length_ratio": (float, 1, 50),
    "guard.max_han_ratio": (float, 0, 1),
    "moegirl.max_hints_per_chunk": (int, 0, 200),
    "moegirl.min_frequency": (int, 1, 1000),
    "auto_discover.min_frequency": (int, 1, 1000),
    "auto_discover.max_candidates": (int, 1, 500),
}


def deep_merge(base: dict, patch: dict) -> dict:
    """Trộn `patch` vào bản sao của `base`; chỉ nhận các khóa đã có trong `base`."""
    out = json.loads(json.dumps(base))
    for key, value in (patch or {}).items():
        if key not in out:
            continue
        if isinstance(out[key], dict) and isinstance(value, dict) and key != "work_subjects":
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def validate_config(cfg: dict) -> list[dict]:
    errors = []
    for dotted, (kind, low, high) in CONFIG_RULES.items():
        node: Any = cfg
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        try:
            value = kind(node)
        except (TypeError, ValueError):
            errors.append({"key": dotted, "message": f"{dotted} phải là {kind.__name__}."})
            continue
        if not (low <= value <= high):
            errors.append({"key": dotted, "message": f"{dotted} phải nằm trong [{low}, {high}]."})
    name = str(cfg.get("output_name", "")).strip()
    if not name or any(ch in name for ch in '\\/:*?"<>|'):
        errors.append({"key": "output_name", "message": "Tên file bản dịch không hợp lệ."})
    mode = str(cfg.get("engine_mode") or "script").strip().lower()
    if mode not in ENGINE_MODES:
        errors.append({"key": "engine_mode", "message": "engine_mode phải là 'deepseek' hoặc 'script'."})
    return errors


@dataclass
class ProjectPaths:
    source: Path
    dir: Path
    output: Path
    progress: Path
    error_log: Path
    failed: Path
    glossary: Path
    auto_glossary: Path
    anchors: Path
    approved: Path
    suggestions: Path
    config: Path
    sidecar: Path
    project_cache: Path
    pause_flag: Path
    stop_flag: Path
    runs_dir: Path

    @classmethod
    def build(cls, source: Path, cfg: dict) -> "ProjectPaths":
        source = Path(source).expanduser().resolve()
        folder = source.parent
        output = folder / (cfg.get("output_name") or "bandich.txt")
        glossary = Path(cfg["glossary"]) if cfg.get("glossary") else folder / "thuatngu.txt"
        return cls(
            source=source, dir=folder, output=output,
            progress=Path(str(output) + ".progress.json"),
            error_log=Path(str(output) + ".error.log"),
            failed=output.parent / "failed_chunks.json",
            glossary=glossary,
            auto_glossary=Path(str(glossary) + ".auto.txt"),
            anchors=folder / "moegirl_work_anchors.txt",
            approved=folder / "moegirl_works_approved.txt",
            suggestions=folder / "moegirl_suggestions.json",
            config=folder / "config.json",
            sidecar=Path(str(output) + ".chunks.jsonl"),
            project_cache=folder / "moegirl_terms.project.json",
            pause_flag=Path(str(output) + ".pause"),
            stop_flag=Path(str(output) + ".stop"),
            runs_dir=folder / "logs",
        )


# ============================================================
# 3. AHO-CORASICK + CHỈ MỤC CHƯƠNG
# ============================================================

class AhoCorasick:
    """So khớp nhiều chuỗi trong một lượt quét. Các mẫu chồng lấn đều được báo."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self.goto: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[list[int]] = [[]]
        self.patterns: list[str] = []
        for pattern in patterns:
            self._add(pattern)
        self._build()

    def _add(self, pattern: str) -> None:
        index = len(self.patterns)
        self.patterns.append(pattern)
        if not pattern:
            return
        node = 0
        for ch in pattern:
            nxt = self.goto[node].get(ch)
            if nxt is None:
                nxt = len(self.goto)
                self.goto.append({})
                self.fail.append(0)
                self.out.append([])
                self.goto[node][ch] = nxt
            node = nxt
        self.out[node].append(index)

    def _build(self) -> None:
        queue: deque[int] = deque()
        for nxt in self.goto[0].values():
            queue.append(nxt)
        while queue:
            node = queue.popleft()
            for ch, child in self.goto[node].items():
                queue.append(child)
                f = self.fail[node]
                while f and ch not in self.goto[f]:
                    f = self.fail[f]
                self.fail[child] = self.goto[f].get(ch, 0)
                self.out[child] = self.out[child] + self.out[self.fail[child]]

    def scan(self, text: str, keep_positions: int = 200) -> tuple[list[int], list[list[int]]]:
        """Trả về (số lần khớp theo mẫu, vị trí bắt đầu (tối đa keep_positions mỗi mẫu))."""
        counts = [0] * len(self.patterns)
        positions: list[list[int]] = [[] for _ in self.patterns]
        goto, fail, out = self.goto, self.fail, self.out
        lengths = [len(p) for p in self.patterns]
        node = 0
        for i, ch in enumerate(text):
            while node and ch not in goto[node]:
                node = fail[node]
            node = goto[node].get(ch, 0)
            hits = out[node]
            if hits:
                for pid in hits:
                    counts[pid] += 1
                    if len(positions[pid]) < keep_positions:
                        positions[pid].append(i - lengths[pid] + 1)
        return counts, positions


class ChapterIndex:
    """Tiêu đề chương = dòng bắt đầu bằng '##' (giống engine). Chương 0 = trước tiêu đề đầu."""

    def __init__(self, text: str) -> None:
        self.offsets: list[int] = []
        self.titles: list[str] = []
        offset = 0
        for line in text.split("\n"):
            if line.lstrip().startswith("##"):
                self.offsets.append(offset)
                self.titles.append(line.strip())
            offset += len(line) + 1

    @property
    def total(self) -> int:
        return len(self.offsets)

    def chapter_of(self, pos: int) -> int:
        return bisect.bisect_right(self.offsets, pos)

    def title_of(self, chapter: int) -> str:
        return self.titles[chapter - 1] if 1 <= chapter <= len(self.titles) else ""


def snippet(text: str, pos: int, length: int, before: int = 40, after: int = 60) -> str:
    a, b = max(0, pos - before), min(len(text), pos + length + after)
    return text[a:b].replace("\n", " ").strip()


# ============================================================
# 4. KHO NHÂN VẬT MỐC (màn hình C): moegirl_work_anchors.txt (TSV)
# ============================================================

ANCHOR_COLUMNS = ["source_name", "work_id", "work_title", "aliases", "weight", "kind", "subject_keys", "notes"]
ANCHOR_KINDS = ("main", "support", "review", "fanfic_only")
AUTO_KINDS = ("main", "support")
LEGACY_ANCHOR_KIND = {"canon": "main", "fanfic": "fanfic_only", "fanfic_only": "fanfic_only",
                      "review": "review", "support": "support", "main": "main"}


def split_aliases(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，;；|]", value or "") if x.strip()]


class AnchorStore:
    """Bản nháp trong RAM + file TSV. Chỉ `save()` mới ghi ra đĩa ("N chưa lưu")."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.rows: list[dict] = []
        self._saved_sig: dict[str, str] = {}
        self._lock = threading.RLock()
        self.load()

    # ---- đọc/ghi ----
    @staticmethod
    def _row_from_cells(cells: list[str]) -> dict:
        cells = (cells + [""] * len(ANCHOR_COLUMNS))[: len(ANCHOR_COLUMNS)]
        row = dict(zip(ANCHOR_COLUMNS, (c.strip() for c in cells)))
        try:
            row["weight"] = float(row["weight"]) if row["weight"] != "" else 1.0
        except ValueError:
            row["weight"] = row["weight"]  # giữ chuỗi sai để check() báo lỗi
        row["kind"] = row["kind"] or "support"
        return row

    @staticmethod
    def _sig(row: dict) -> str:
        return json.dumps([row.get(c) for c in ANCHOR_COLUMNS], ensure_ascii=False)

    def _assign_ids(self, rows: list[dict]) -> None:
        for row in rows:
            row["row_id"] = row.get("row_id") or uuid.uuid4().hex[:8]

    @staticmethod
    def _row_from_legacy(cells: list[str], header: list[str]) -> dict:
        """Chuyển dòng định dạng cũ (source_name, work_id, aliases, type, anchor_weight, notes)
        sang schema 8 cột mới. Cột `work_id` cũ thực chất là tên tác phẩm nên giữ làm cả work_id
        lẫn work_title để khớp khóa nhóm tác phẩm của engine (dichTrungsangViet.py)."""
        data = {name.strip(): (cells[i].strip() if i < len(cells) else "")
                for i, name in enumerate(header)}
        work_id = data.get("work_id", "")
        kind = LEGACY_ANCHOR_KIND.get(data.get("type", "").strip().lower(), "support")
        try:
            anchor_weight = int(data.get("anchor_weight") or 0)
        except ValueError:
            anchor_weight = 0
        weight = 1.0 if anchor_weight >= 2 else (0.8 if anchor_weight == 1 else 0.0)
        return {
            "source_name": data.get("source_name", ""),
            "work_id": work_id,
            "work_title": work_id,
            "aliases": data.get("aliases", ""),
            "weight": weight,
            "kind": kind,
            "subject_keys": "",
            "notes": data.get("notes", ""),
        }

    def parse(self, text: str) -> list[dict]:
        rows = []
        legacy_header = None
        for raw in text.split("\n"):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            cells = raw.split("\t")
            if cells and cells[0].strip().lower() == "source_name":
                header = [c.strip() for c in cells]
                legacy_header = header if ("anchor_weight" in header or "type" in header) else None
                continue
            if legacy_header is not None:
                rows.append(self._row_from_legacy(cells, legacy_header))
            else:
                rows.append(self._row_from_cells(cells))
        return rows

    def load(self) -> None:
        with self._lock:
            text = read_text_auto(self.path) if self.path.is_file() else ""
            self.rows = self.parse(text)
            self._assign_ids(self.rows)
            self._saved_sig = {r["row_id"]: self._sig(r) for r in self.rows}

    def serialize(self) -> str:
        lines = ["\t".join(ANCHOR_COLUMNS)]
        for row in self.rows:
            cells = []
            for col in ANCHOR_COLUMNS:
                value = row.get(col, "")
                if col == "weight" and isinstance(value, float):
                    value = f"{value:g}"
                cells.append(str(value).replace("\t", " ").replace("\n", " "))
            lines.append("\t".join(cells))
        return "\n".join(lines) + "\n"

    def dirty(self) -> dict:
        with self._lock:
            current = {r["row_id"]: self._sig(r) for r in self.rows}
            added = [k for k in current if k not in self._saved_sig]
            removed = [k for k in self._saved_sig if k not in current]
            changed = [k for k in current if k in self._saved_sig and current[k] != self._saved_sig[k]]
            return {"added": len(added), "removed": len(removed), "modified": len(changed),
                    "total": len(added) + len(removed) + len(changed)}

    # ---- kiểm tra ----
    def check(self) -> dict:
        errors, warnings = [], []
        seen: dict[tuple[str, str], str] = {}
        valid = 0
        with self._lock:
            for row in self.rows:
                rid, name = row["row_id"], row.get("source_name", "")
                row_errors = []
                for col in ("source_name", "work_id", "work_title"):
                    if not str(row.get(col, "")).strip():
                        row_errors.append({"row_id": rid, "field": col, "code": "REQUIRED",
                                           "message": f"Thiếu {col}."})
                weight = row.get("weight")
                if not isinstance(weight, float) or not (0.0 <= weight <= 1.0):
                    row_errors.append({"row_id": rid, "field": "weight", "code": "BAD_WEIGHT",
                                       "message": "weight phải là số trong [0, 1]."})
                if row.get("kind") not in ANCHOR_KINDS:
                    row_errors.append({"row_id": rid, "field": "kind", "code": "BAD_KIND",
                                       "message": f"kind phải thuộc {', '.join(ANCHOR_KINDS)}."})
                key = (name, row.get("work_id", ""))
                if name and key in seen:
                    row_errors.append({"row_id": rid, "field": "source_name", "code": "DUPLICATE",
                                       "message": f"Trùng với dòng {seen[key]} (cùng tên và tác phẩm)."})
                seen.setdefault(key, rid)
                errors.extend(row_errors)
                if not row_errors:
                    valid += 1
                if not split_aliases(row.get("aliases", "")):
                    warnings.append({"row_id": rid, "field": "aliases", "code": "NO_ALIAS",
                                     "message": "Chưa thiết lập bí danh."})
                if isinstance(weight, float):
                    if weight == 0.0:
                        warnings.append({"row_id": rid, "field": "weight", "code": "ZERO_WEIGHT",
                                         "message": "Trọng số 0: không làm bằng chứng xác nhận."})
                    elif row.get("kind") in ("review", "fanfic_only"):
                        warnings.append({"row_id": rid, "field": "kind", "code": "KIND_EXCLUDED",
                                         "message": "Nhóm review/fanfic_only bị loại khỏi nhận diện tự động."})
        return {"valid": valid, "errors": errors, "warnings": warnings,
                "total": len(self.rows), "can_save": not errors}

    # ---- truy vấn ----
    def list(self, query: str = "", *, case=False, whole=False, regex=False,
             work_id: str = "", kind: str = "", page_no: int = 1, page_size: int = 100) -> dict:
        match = make_matcher(query, case=case, whole=whole, regex=regex)
        with self._lock:
            items = []
            for row in self.rows:
                if work_id and row.get("work_id") != work_id:
                    continue
                if kind and row.get("kind") != kind:
                    continue
                haystack = " ".join(str(row.get(c, "")) for c in
                                    ("source_name", "work_id", "work_title", "aliases", "subject_keys", "notes"))
                if match(haystack):
                    items.append(dict(row))
            works = {}
            for row in self.rows:
                works.setdefault(row.get("work_id", ""), row.get("work_title", ""))
            kinds = Counter(r.get("kind") for r in self.rows)
            weights = [r["weight"] for r in self.rows if isinstance(r.get("weight"), float)]
            result = page(items, page_no, page_size)
            result.update({
                "works": [{"work_id": k, "work_title": v} for k, v in sorted(works.items())],
                "kind_counts": dict(kinds),
                "avg_weight": round(sum(weights) / len(weights), 3) if weights else 0.0,
                "total_rows": len(self.rows),
                "dirty": self.dirty(),
            })
            return result

    # ---- chỉnh sửa (nháp) ----
    @staticmethod
    def _clean_fields(fields: dict) -> dict:
        out = {}
        for col in ANCHOR_COLUMNS:
            if col in fields:
                value = fields[col]
                if col == "weight":
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        pass
                elif col == "aliases" and isinstance(value, list):
                    value = ", ".join(str(x).strip() for x in value if str(x).strip())
                else:
                    value = str(value if value is not None else "").strip()
                out[col] = value
        return out

    def add(self, fields: dict) -> dict:
        with self._lock:
            row = self._row_from_cells([""] * len(ANCHOR_COLUMNS))
            row.update(self._clean_fields(fields))
            row["row_id"] = uuid.uuid4().hex[:8]
            self.rows.append(row)
            return dict(row)

    def update(self, row_id: str, fields: dict) -> dict:
        with self._lock:
            row = next((r for r in self.rows if r["row_id"] == row_id), None)
            if row is None:
                raise ApiError("NOT_FOUND", f"Không có dòng {row_id}.")
            row.update(self._clean_fields(fields))
            return dict(row)

    def delete(self, row_ids: list[str]) -> int:
        ids = set(row_ids or [])
        with self._lock:
            before = len(self.rows)
            self.rows = [r for r in self.rows if r["row_id"] not in ids]
            return before - len(self.rows)

    def import_tsv(self, text: str, mode: str = "merge") -> dict:
        incoming = self.parse(text)
        if not incoming:
            raise ApiError("EMPTY_IMPORT", "Không đọc được dòng hợp lệ nào trong TSV.")
        with self._lock:
            if mode == "replace":
                self.rows = []
            existing = {(r.get("source_name"), r.get("work_id")): r for r in self.rows}
            added = updated = 0
            for row in incoming:
                key = (row.get("source_name"), row.get("work_id"))
                if key in existing:
                    existing[key].update({k: v for k, v in row.items() if k in ANCHOR_COLUMNS})
                    updated += 1
                else:
                    row["row_id"] = uuid.uuid4().hex[:8]
                    self.rows.append(row)
                    existing[key] = row
                    added += 1
            return {"added": added, "updated": updated}

    def save(self) -> dict:
        report = self.check()
        if not report["can_save"]:
            raise ApiError("INVALID_DATA", f"Còn {len(report['errors'])} lỗi dữ liệu; chưa thể lưu.",
                           errors=report["errors"][:50])
        with self._lock:
            backup = atomic_write(self.path, self.serialize(), backup=True)
            self._saved_sig = {r["row_id"]: self._sig(r) for r in self.rows}
        return {"path": str(self.path), "rows": len(self.rows),
                "backup": str(backup) if backup else None}

    def eligible(self) -> list[dict]:
        """Nhân vật mốc được phép dùng để nhận diện tự động."""
        with self._lock:
            return [dict(r) for r in self.rows
                    if r.get("kind") in AUTO_KINDS and isinstance(r.get("weight"), float) and r["weight"] > 0]

    def works(self) -> dict[str, str]:
        with self._lock:
            out: dict[str, str] = {}
            for row in self.rows:
                out.setdefault(row.get("work_id", ""), row.get("work_title", ""))
            return {k: v for k, v in out.items() if k}


# ============================================================
# 5. DUYỆT TÁC PHẨM (màn hình B): moegirl_works_approved.txt
# ============================================================

WORK_STATUSES = ("pending", "approved", "rejected")
WORK_LABELS = {"pending": "Chờ duyệt", "approved": "Đã duyệt", "rejected": "Từ chối"}


class ApprovalStore:
    """TSV: work_id <TAB> work_title <TAB> status <TAB> updated_at. Chỉ lưu approved/rejected."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.status: dict[str, dict] = {}
        self._saved: dict[str, str] = {}
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        with self._lock:
            self.status = {}
            if self.path.is_file():
                for raw in read_text_auto(self.path).split("\n"):
                    if not raw.strip() or raw.startswith("#"):
                        continue
                    cells = (raw.split("\t") + ["", "", "", ""])[:4]
                    if cells[0].strip() in ("work_id", ""):
                        continue
                    st = cells[2].strip().lower()
                    if st in ("approved", "rejected"):
                        self.status[cells[0].strip()] = {
                            "work_title": cells[1].strip(), "status": st, "updated_at": cells[3].strip()}
            self._saved = {k: v["status"] for k, v in self.status.items()}

    def get(self, work_id: str) -> str:
        with self._lock:
            return self.status.get(work_id, {}).get("status", "pending")

    def set(self, work_ids: list[str], status: str, titles: dict[str, str]) -> int:
        if status not in WORK_STATUSES:
            raise ApiError("BAD_STATUS", f"Trạng thái không hợp lệ: {status}")
        with self._lock:
            for wid in work_ids:
                if status == "pending":
                    self.status.pop(wid, None)
                else:
                    self.status[wid] = {"work_title": titles.get(wid, ""), "status": status,
                                        "updated_at": now_iso()}
            return len(work_ids)

    def approved_ids(self) -> list[str]:
        with self._lock:
            return sorted(k for k, v in self.status.items() if v["status"] == "approved")

    def dirty(self) -> bool:
        with self._lock:
            return {k: v["status"] for k, v in self.status.items()} != self._saved

    def save(self) -> dict:
        with self._lock:
            lines = ["work_id\twork_title\tstatus\tupdated_at"]
            for wid, v in sorted(self.status.items()):
                lines.append(f"{wid}\t{v['work_title']}\t{v['status']}\t{v['updated_at']}")
            backup = atomic_write(self.path, "\n".join(lines) + "\n", backup=True)
            self._saved = {k: v["status"] for k, v in self.status.items()}
        return {"path": str(self.path), "backup": str(backup) if backup else None,
                "approved": len(self.approved_ids())}


# ============================================================
# 6. GLOSSARY (màn hình E): thuatngu.txt  <Nguồn> => <Đích> # <mode>
# ============================================================

_FORCE_RE = re.compile(r"(?<!\w)(?:force|ép)(?!\w)", re.IGNORECASE)
_HINT_RE = re.compile(r"(?<!\w)(?:hint|gợi ý)(?!\w)", re.IGNORECASE)
_AUTO_RE = re.compile(r"(?<!\w)(?:auto|tự động)(?!\w)", re.IGNORECASE)
GLOSSARY_SEPARATORS = ("=>", "->", "\t", "=")


def effective_mode(src: str, dst: str, info: str, min_force_len: int = 2) -> str:
    """Cùng quy tắc với TermManager.entry_mode của translation_guard (v2)."""
    if _HINT_RE.search(info) or _AUTO_RE.search(info):
        return "hint"
    if src == dst or (HAN_RE.findall(dst) and len(HAN_RE.findall(dst)) / max(1, len(dst)) > 0.5):
        return "hint"
    if _FORCE_RE.search(info):
        return "force"
    if len(src) < min_force_len:
        return "hint"
    return "force" if dst[:1].isupper() else "hint"


def parse_glossary(text: str) -> dict:
    """Phân tích thuatngu.txt giữ nguyên số dòng. Không sửa nội dung."""
    rows: list[dict] = []
    issues: list[dict] = []
    first_dst: dict[str, tuple[int, str]] = {}
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        mapping, info = line, ""
        if "#" in line:
            mapping, info = line.split("#", 1)
            info = info.strip()
        sep = next((s for s in GLOSSARY_SEPARATORS if s in mapping), None)
        if sep is None:
            src = dst = mapping.strip()
            keep = True
        else:
            src, dst = (x.strip() for x in mapping.split(sep, 1))
            keep = False
        if not src:
            issues.append({"line": number, "code": "MISSING_SOURCE", "severity": "error",
                           "message": "Thiếu vế nguồn."})
            continue
        if not dst:
            issues.append({"line": number, "code": "MISSING_TARGET", "severity": "error",
                           "message": "Thiếu vế đích."})
            continue
        explicit = "force" if _FORCE_RE.search(info) else ("hint" if _HINT_RE.search(info) else None)
        if _FORCE_RE.search(info) and _HINT_RE.search(info):
            issues.append({"line": number, "code": "MODE_CONFLICT", "severity": "warning",
                           "message": "Có cả force và hint trong ghi chú; hint được ưu tiên."})
            explicit = "hint"
        row = {"line": number, "src": src, "dst": dst, "info": info, "keep": keep,
               "mode": explicit, "effective_mode": effective_mode(src, dst, info)}
        if src in first_dst:
            prev_line, prev_dst = first_dst[src]
            same = prev_dst == dst
            issues.append({
                "line": number, "code": "DUPLICATE_SOURCE" if same else "CONFLICT_TARGET",
                "severity": "warning",
                "message": (f"Trùng nguồn với dòng {prev_line}." if same else
                            f"Cùng vế nguồn nhưng khác vế đích so với dòng {prev_line} ({prev_dst}).")})
        else:
            first_dst[src] = (number, dst)
        rows.append(row)
    force = sum(1 for r in rows if r["effective_mode"] == "force")
    hint = len(rows) - force
    explicit_n = sum(1 for r in rows if r["mode"])
    errors = [i for i in issues if i["severity"] == "error"]
    stats = {
        "valid": len(rows), "force": force, "hint": hint, "explicit_mode": explicit_n,
        "implicit_mode": len(rows) - explicit_n,
        "duplicates": sum(1 for i in issues if i["code"] in ("DUPLICATE_SOURCE", "CONFLICT_TARGET")),
        "missing_target": sum(1 for i in issues if i["code"] == "MISSING_TARGET"),
        "missing_source": sum(1 for i in issues if i["code"] == "MISSING_SOURCE"),
        "force_pct": round(100 * force / len(rows), 1) if rows else 0.0,
        "hint_pct": round(100 * hint / len(rows), 1) if rows else 0.0,
    }
    return {"rows": rows, "issues": issues, "stats": stats, "can_save": not errors}


def format_entry(src: str, dst: str, mode: str = "force", note: str = "") -> str:
    tag = mode if mode in ("force", "hint") else "force"
    extra = f"; {note}" if note else ""
    return f"{src} => {dst} # {tag}{extra}"


def replace_in_glossary(text: str, find: str, repl: str, *, scope: str = "both", case=False,
                        whole=False, regex=False) -> dict:
    """Tìm & thay chỉ trên phần dữ liệu (nguồn/đích), không đụng vào ghi chú # hay dòng chú thích."""
    if not find:
        raise ApiError("EMPTY_FIND", "Chưa nhập chuỗi cần tìm.")
    pattern = find if regex else re.escape(find)
    if whole:
        pattern = r"(?<!\w)(?:" + pattern + r")(?!\w)"
    try:
        compiled = re.compile(pattern, 0 if case else re.IGNORECASE)
    except re.error as exc:
        raise ApiError("BAD_REGEX", f"Biểu thức chính quy không hợp lệ: {exc}")
    out_lines, total, changed_lines = [], 0, []
    for number, raw in enumerate(text.split("\n"), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(raw)
            continue
        mapping, hash_part = (raw.split("#", 1) + [None])[:2] if "#" in raw else (raw, None)
        sep = next((s for s in GLOSSARY_SEPARATORS if s in mapping), None)
        if sep is None:
            left, right = mapping, None
        else:
            left, right = mapping.split(sep, 1)
        count = 0
        if scope in ("both", "source"):
            left, n = compiled.subn(lambda _m: repl, left)
            count += n
        if right is not None and scope in ("both", "target"):
            right, n = compiled.subn(lambda _m: repl, right)
            count += n
        if count:
            total += count
            changed_lines.append(number)
            new_mapping = left if right is None else f"{left}{sep}{right}"
            raw = new_mapping + (f"#{hash_part}" if hash_part is not None else "")
        out_lines.append(raw)
    return {"text": "\n".join(out_lines), "replacements": total, "lines": changed_lines}


class GlossaryService:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> dict:
        text = read_text_auto(self.path) if self.path.is_file() else ""
        parsed = parse_glossary(text)
        mtime = datetime.fromtimestamp(self.path.stat().st_mtime).isoformat(timespec="seconds") \
            if self.path.is_file() else None
        return {"path": str(self.path), "exists": self.path.is_file(), "text": text,
                "line_count": len(text.split("\n")) if text else 0,
                "modified_at": mtime, **parsed}

    def validate(self, text: str) -> dict:
        return parse_glossary(text)

    def save(self, text: str, *, allow_errors: bool = False) -> dict:
        parsed = parse_glossary(text)
        if not parsed["can_save"] and not allow_errors:
            raise ApiError("INVALID_SYNTAX", "Glossary còn dòng sai cú pháp; chưa lưu.",
                           issues=[i for i in parsed["issues"] if i["severity"] == "error"][:50])
        if text and not text.endswith("\n"):
            text += "\n"
        backup = atomic_write(self.path, text, backup=True)
        return {"path": str(self.path), "backup": str(backup) if backup else None,
                "stats": parsed["stats"], "issues": parsed["issues"]}

    def backup(self) -> dict:
        if not self.path.is_file():
            raise ApiError("NOT_FOUND", "Chưa có file glossary để sao lưu.")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.path.with_name(f"{self.path.name}.bak_{stamp}")
        shutil.copy2(self.path, target)
        return {"backup": str(target)}

    def insert(self, src: str, dst: str, mode: str = "force", note: str = "") -> dict:
        src, dst = (src or "").strip(), (dst or "").strip()
        if not src or not dst:
            raise ApiError("MISSING_FIELD", "Cần nhập cả nguồn và đích.")
        text = read_text_auto(self.path) if self.path.is_file() else ""
        existing = next((r for r in parse_glossary(text)["rows"] if r["src"] == src), None)
        if existing:
            raise ApiError("DUPLICATE_SOURCE",
                           f"Nguồn '{src}' đã có ở dòng {existing['line']} ({existing['dst']}).",
                           line=existing["line"])
        text = text + ("" if not text or text.endswith("\n") else "\n") + format_entry(src, dst, mode, note) + "\n"
        return self.save(text)

    def find_replace(self, find: str, repl: str, *, apply: bool = False, **options: Any) -> dict:
        text = read_text_auto(self.path) if self.path.is_file() else ""
        result = replace_in_glossary(text, find, repl, **options)
        if apply and result["replacements"]:
            saved = self.save(result["text"])
            result["saved"] = saved
        return result

    # ---- ghép mục mới (dùng cho Moegirl commit) ----
    def plan_merge(self, entries: list[dict], conflict_policy: str = "hint") -> dict:
        """Lập kế hoạch ghép entries [{src,dst,mode,note,section}] mà không ghi file."""
        text = read_text_auto(self.path) if self.path.is_file() else ""
        existing = {r["src"]: r for r in parse_glossary(text)["rows"]}
        to_add, conflicts, same = [], [], []
        seen_new: set[str] = set()
        for e in entries:
            src, dst = e["src"], e["dst"]
            if src in seen_new:
                continue
            seen_new.add(src)
            if src in existing:
                if existing[src]["dst"] == dst:
                    same.append({"src": src, "dst": dst, "line": existing[src]["line"]})
                    continue
                conflicts.append({"src": src, "dst": dst, "existing_dst": existing[src]["dst"],
                                  "line": existing[src]["line"]})
                if conflict_policy == "skip":
                    continue
                to_add.append({**e, "mode": "hint", "note": f"xung đột với dòng {existing[src]['line']}"})
            else:
                to_add.append(e)
        lines_after = len(text.split("\n")) - (1 if text.endswith("\n") else 0) + len(to_add) \
            + len({e.get("section") for e in to_add if e.get("section")})
        return {"to_add": to_add, "conflicts": conflicts, "already_present": same,
                "lines_before": len(text.split("\n")) - (1 if text.endswith("\n") else 0) if text else 0,
                "lines_after_estimate": lines_after}

    def apply_merge(self, plan: dict) -> dict:
        text = read_text_auto(self.path) if self.path.is_file() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        by_section: dict[str, list[dict]] = {}
        for e in plan["to_add"]:
            by_section.setdefault(e.get("section", ""), []).append(e)
        chunks = []
        for section, items in by_section.items():
            if section:
                chunks.append(f"# [{section}] - phê duyệt từ Moegirl Suggestions ({now_iso()})")
            chunks.extend(format_entry(e["src"], e["dst"], e.get("mode", "force"), e.get("note", ""))
                          for e in items)
        if not chunks:
            return {"added": 0, "backup": None}
        backup = atomic_write(self.path, text + "\n".join(chunks) + "\n", backup=True)
        return {"added": len(plan["to_add"]), "backup": str(backup) if backup else None,
                "path": str(self.path)}


# ============================================================
# 7. DỰ ÁN: gom cấu hình + đường dẫn + kho dữ liệu của một truyện
# ============================================================

SOURCE_CLEANERS = (
    (re.compile(r"✅ Đã lưu thành công!"), ""),
    (re.compile(r"127\.0\.0\.1.*?200 -.*?(?:\n|$)"), ""),
    (re.compile(r"📥 \[Khối thứ \d+\].*?(?:\n|$)"), ""),
)


def clean_source(text: str) -> str:
    """Giống bước [1/6] của engine, để số chunk/fingerprint khớp."""
    for pattern, repl in SOURCE_CLEANERS:
        text = pattern.sub(repl, text)
    return text


class Project:
    def __init__(self, source: Path | str, default_engine: Path | str = "") -> None:
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise ApiError("SOURCE_NOT_FOUND", f"Không tìm thấy file nguồn: {source}")
        if source.suffix.lower() != ".txt":
            raise ApiError("BAD_SOURCE", "File nguồn phải là .txt.")
        self.default_engine = str(default_engine or "")
        cfg_file = source.parent / "config.json"
        self.cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        self.config_loaded = False
        if cfg_file.is_file():
            try:
                saved = json.loads(read_text_auto(cfg_file))
                saved = saved if isinstance(saved, dict) else {}
                self.cfg = deep_merge(self.cfg, saved)
                self.config_loaded = True
                self._migrate_config(int(saved.get("schema_version") or 0))
            except (OSError, json.JSONDecodeError):
                pass
        self.cfg["source"] = str(source)
        if not self.cfg.get("engine"):
            self.cfg["engine"] = self.default_engine
        self.paths = ProjectPaths.build(source, self.cfg)
        self.anchors = AnchorStore(self.paths.anchors)
        self.approvals = ApprovalStore(self.paths.approved)
        self.glossary = GlossaryService(self.paths.glossary)
        self._text_cache: Optional[tuple[tuple, str, ChapterIndex, str]] = None
        self._chunk_cache: Optional[tuple] = None
        self.fingerprint = ""
        self._lock = threading.RLock()

    # ---- cấu hình ----
    def _migrate_config(self, saved_version: int) -> None:
        """v4: tỉ lệ độ dài tính theo chữ Hán; ngưỡng cũ 1.2 (tính theo tổng ký tự) gây báo
        'quá ngắn' sai nên được hạ về 0.9 nếu người dùng chưa tự đổi."""
        if saved_version < 4:
            guard = self.cfg.get("guard", {})
            if float(guard.get("min_length_ratio", 0) or 0) == 1.2:
                guard["min_length_ratio"] = DEFAULT_CONFIG["guard"]["min_length_ratio"]
            self.cfg["schema_version"] = DEFAULT_CONFIG["schema_version"]

    def save_config(self) -> dict:
        errors = validate_config(self.cfg)
        if errors:
            raise ApiError("INVALID_CONFIG", "Cấu hình không hợp lệ.", errors=errors)
        backup = atomic_write(self.paths.config, json.dumps(self.cfg, ensure_ascii=False, indent=2) + "\n",
                              backup=True, keep=5)
        return {"path": str(self.paths.config), "backup": str(backup) if backup else None}

    def update_config(self, patch: dict) -> dict:
        patch = dict(patch or {})
        patch.pop("source", None)  # đổi nguồn = mở dự án khác
        merged = deep_merge(self.cfg, patch)
        errors = validate_config(merged)
        if errors:
            raise ApiError("INVALID_CONFIG", "Cấu hình không hợp lệ.", errors=errors)
        self.cfg = merged
        self.paths = ProjectPaths.build(Path(self.cfg["source"]), self.cfg)
        self.glossary = GlossaryService(self.paths.glossary)
        return self.cfg

    def engine_path(self) -> Path:
        return Path(self.cfg.get("engine") or self.default_engine or "").expanduser()

    def engine_mode(self) -> str:
        """'script' (dichTrungsangViet.py) hoặc 'deepseek' (deepseek_service.py)."""
        mode = str(self.cfg.get("engine_mode") or "script").strip().lower()
        return mode if mode in ENGINE_MODES else "script"

    def moegirl_terms_path(self) -> Path:
        explicit = self.cfg["moegirl"].get("terms_file")
        if explicit:
            return Path(explicit)
        return CODE_ROOT / "moegirl_terms.json"

    # ---- văn bản nguồn ----
    def source_text(self) -> tuple[str, ChapterIndex, str]:
        """(văn bản NFC đã dọn như engine, chỉ mục chương, encoding)"""
        with self._lock:
            st = self.paths.source.stat()
            key = (st.st_mtime_ns, st.st_size)
            if self._text_cache and self._text_cache[0] == key:
                return self._text_cache[1], self._text_cache[2], self._text_cache[3]
            text, encoding = decode_bytes(self.paths.source.read_bytes())
            text = clean_source(text)
            index = ChapterIndex(text)
            self._text_cache = (key, text, index, encoding)
            return text, index, encoding

    def source_info(self) -> dict:
        text, index, encoding = self.source_text()
        size = self.paths.source.stat().st_size
        return {"path": str(self.paths.source), "size_bytes": size, "encoding": encoding,
                "chars": len(text), "chapters": index.total,
                "han_chars": len(HAN_RE.findall(text))}

    def chunks(self) -> list[dict]:
        """Danh sách chunk theo đúng thuật toán của engine (import split_into_chunks)."""
        engine = self.engine_path()
        if not engine.is_file():
            raise ApiError("ENGINE_NOT_FOUND", f"Không tìm thấy engine: {engine}")
        max_chars = int(self.cfg["translate"]["max_chars"])
        with self._lock:
            st = self.paths.source.stat()
            key = (st.st_mtime_ns, st.st_size, max_chars, engine.stat().st_mtime_ns)
            if self._chunk_cache and self._chunk_cache[0] == key:
                if self._chunk_cache[3]:
                    raise ApiError("CHUNKS_UNAVAILABLE", self._chunk_cache[3])
                self.fingerprint = self._chunk_cache[2]
                return self._chunk_cache[1]
            text, _, _ = self.source_text()
            try:
                module = load_module_from(engine, "nv_engine_adapter")
                chunks = module.split_into_chunks(text, max_chars=max_chars)
                fingerprint = module.chunks_fingerprint(chunks) if chunks else ""
            except Exception as exc:  # engine không import được (thiếu openai...) -> nhớ lỗi, không thử lại mỗi lần poll
                self._chunk_cache = (key, [], "", f"Không tính được chunk từ engine: {exc}")
                raise ApiError("CHUNKS_UNAVAILABLE", self._chunk_cache[3])
            for i, c in enumerate(chunks):
                c["index"] = i
            self._chunk_cache = (key, chunks, fingerprint, "")
            self.fingerprint = fingerprint
            return chunks

    def progress_file(self) -> dict:
        if not self.paths.progress.is_file():
            return {}
        try:
            data = json.loads(read_text_auto(self.paths.progress).strip())
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        if isinstance(data, int):
            return {"next_chunk": data}
        return data if isinstance(data, dict) else {}


# ============================================================
# 8. NHẬN DIỆN TÁC PHẨM (màn hình B)
# ============================================================

def score_work(anchors_hit: list[dict], total_chapters: int) -> tuple[float, str]:
    """Điểm xếp hạng 0-100 (KHÔNG phải xác suất).

    strength = Σ weight * min(1, ln(1+count)/ln(21))   (mỗi nhân vật bão hòa ở ~20 lần)
    coverage = min(1, số chương có bằng chứng / max(3, 10% tổng chương))
    score    = 100 * (1 - exp(-strength/1.5)) * (0.5 + 0.5*coverage)
    Chỉ có 1 nhân vật độc lập khớp -> điểm bị chặn ở 40 ("bằng chứng đơn lẻ").
    """
    hit = [a for a in anchors_hit if a["count"] > 0]
    strength = sum(a["weight"] * min(1.0, math.log1p(a["count"]) / math.log1p(20)) for a in hit)
    chapters = set()
    for a in hit:
        chapters.update(a["chapters"])
    coverage = min(1.0, len(chapters) / max(3.0, 0.10 * max(total_chapters, 1)))
    score = 100.0 * (1.0 - math.exp(-strength / 1.5)) * (0.5 + 0.5 * coverage)
    note = ""
    if len(hit) < 2:
        if score > 40.0:
            score, note = 40.0, "single_anchor"
        elif hit:
            note = "single_anchor"
    return round(score, 1), note


class WorkDetector:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.last: Optional[dict] = None

    def scan(self) -> dict:
        project = self.project
        text, chapters, _ = project.source_text()
        anchors = project.anchors.eligible()
        started = time.time()
        pattern_owners: dict[str, list[int]] = {}
        for idx, row in enumerate(anchors):
            names = [row["source_name"]] + split_aliases(row.get("aliases", ""))
            for name in names:
                name = nfc(name).strip()
                if len(name) >= 2:
                    owners = pattern_owners.setdefault(name, [])
                    if idx not in owners:
                        owners.append(idx)
        patterns = list(pattern_owners)
        automaton = AhoCorasick(patterns)
        counts, positions = automaton.scan(text, keep_positions=5000)
        per_anchor: dict[int, dict] = {}
        for pid, pattern in enumerate(patterns):
            if not counts[pid]:
                continue
            for idx in pattern_owners[pattern]:
                entry = per_anchor.setdefault(idx, {"count": 0, "hits": [], "matched": []})
                entry["count"] += counts[pid]
                entry["matched"].append({"text": pattern, "count": counts[pid]})
                entry["hits"].extend((pos, len(pattern)) for pos in positions[pid])
        works: dict[str, dict] = {}
        for idx, row in enumerate(anchors):
            wid = row["work_id"]
            work = works.setdefault(wid, {"work_id": wid, "work_title": row["work_title"],
                                          "anchors": [], "excluded_anchors": 0})
            entry = per_anchor.get(idx)
            if not entry:
                continue
            chapter_counter = Counter(chapters.chapter_of(p) for p, _ in entry["hits"])
            top_chapter = chapter_counter.most_common(1)[0][0]
            first = min(entry["hits"])
            sample_positions = [first]
            for pos, length in sorted(entry["hits"]):
                if chapters.chapter_of(pos) == top_chapter and (pos, length) != first:
                    sample_positions.append((pos, length))
                    break
            work["anchors"].append({
                "row_id": row["row_id"], "name": row["source_name"], "kind": row["kind"],
                "weight": row["weight"], "count": entry["count"], "matched": entry["matched"],
                "chapters": sorted(chapter_counter), "top_chapter": top_chapter,
                "snippets": [{"chapter": chapters.chapter_of(p), "chapter_title": chapters.title_of(chapters.chapter_of(p)),
                              "text": snippet(text, p, length)} for p, length in sample_positions],
            })
        for row in project.anchors.rows:
            wid = row.get("work_id")
            if wid in works and (row.get("kind") in ("review", "fanfic_only") or row.get("weight") == 0.0):
                works[wid]["excluded_anchors"] += 1
        result_works = []
        for wid, work in works.items():
            if not work["anchors"]:
                continue
            work["anchors"].sort(key=lambda a: (-a["count"], a["name"]))
            chapters_set: set[int] = set()
            for a in work["anchors"]:
                chapters_set.update(a["chapters"])
            score, note = score_work(work["anchors"], chapters.total)
            status = project.approvals.get(wid)
            result_works.append({
                "work_id": wid, "work_title": work["work_title"], "score": score, "note": note,
                "evidence": "strong" if score >= 80 else "medium" if score >= 50 else "weak",
                "matched_anchors": len(work["anchors"]),
                "total_occurrences": sum(a["count"] for a in work["anchors"]),
                "chapters_with_evidence": len(chapters_set), "excluded_anchors": work["excluded_anchors"],
                "status": status, "status_label": WORK_LABELS[status], "anchors": work["anchors"],
                "preview": [{"name": a["name"], "count": a["count"]} for a in work["anchors"][:3]],
            })
        self.last = {
            "scanned_at": now_iso(), "source": str(project.paths.source),
            "total_chapters": chapters.total, "total_chars": len(text),
            "patterns": len(patterns), "elapsed_ms": int((time.time() - started) * 1000),
            "works": result_works,
        }
        return self.last

    def _ensure(self) -> dict:
        if self.last is None:
            raise ApiError("NOT_SCANNED", "Chưa quét văn bản nguồn. Hãy gọi works.scan trước.")
        return self.last

    def list(self, *, status: str = "", query: str = "", sort: str = "score",
             page_no: int = 1, page_size: int = 50) -> dict:
        data = self._ensure()
        match = make_matcher(query)
        items = []
        for w in data["works"]:
            w = dict(w)
            w["status"] = self.project.approvals.get(w["work_id"])
            w["status_label"] = WORK_LABELS[w["status"]]
            if status and status != "all" and w["status"] != status:
                continue
            if not match(f"{w['work_title']} {w['work_id']}"):
                continue
            w.pop("anchors", None)
            items.append(w)
        keys = {"score": "score", "matched": "matched_anchors", "chapters": "chapters_with_evidence",
                "occurrences": "total_occurrences"}
        items.sort(key=lambda w: (-w[keys.get(sort, "score")], w["work_title"]))
        counts = Counter(self.project.approvals.get(w["work_id"]) for w in data["works"])
        result = page(items, page_no, page_size)
        result.update({"status_counts": {"all": len(data["works"]), **{k: counts.get(k, 0) for k in WORK_STATUSES}},
                       "scanned_at": data["scanned_at"], "elapsed_ms": data["elapsed_ms"],
                       "dirty": self.project.approvals.dirty()})
        return result

    def detail(self, work_id: str) -> dict:
        data = self._ensure()
        for w in data["works"]:
            if w["work_id"] == work_id:
                w = dict(w)
                w["status"] = self.project.approvals.get(work_id)
                w["status_label"] = WORK_LABELS[w["status"]]
                return w
        raise ApiError("NOT_FOUND", f"Không có bằng chứng cho tác phẩm {work_id}.")

    def set_status(self, work_ids: list[str], status: str) -> dict:
        titles = self.project.anchors.works()
        n = self.project.approvals.set(list(work_ids or []), status, titles)
        return {"updated": n, "dirty": self.project.approvals.dirty()}


# ============================================================
# 9. MOEGIRL (màn hình D): ứng viên thuật ngữ + commit có xác nhận
# ============================================================

SUGGESTION_STATUSES = ("pending", "approved", "rejected")
SUGGESTION_LABELS = {"pending": "Chờ duyệt", "approved": "Đã duyệt", "rejected": "Từ chối"}
_TITLE_SUFFIX_RE = re.compile(r"\s*[（(][^()（）]*[)）]\s*$")
_COMMON_ZH = {"老师", "先生", "小姐", "大人", "队长", "会长", "社长", "老板", "同学", "前辈", "哥哥",
              "姐姐", "弟弟", "妹妹", "爸爸", "妈妈", "父亲", "母亲", "少年", "少女", "女王", "大小姐"}
# Thẻ vùng/phân loại chung trong char2subject.json không phải tên tác phẩm, phải loại
# khỏi phiếu bầu subject (nếu không, mọi nhân vật cùng vùng bị gán nhầm tác phẩm).
_SUBJECT_REGION_RE = re.compile(r"(?:作品|地区|各地区)$")


def catalog_info(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return {"exists": False, "path": str(path)}
    st = path.stat()
    info = {"exists": True, "path": str(path), "size_bytes": st.st_size,
            "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "record_count": None, "dataset_dir": None, "schema_version": None}
    try:
        head = path.read_bytes()[:4096].decode("utf-8", errors="ignore")
        for key, cast in (("record_count", int), ("schema_version", int)):
            m = re.search(rf'"{key}"\s*:\s*(\d+)', head)
            if m:
                info[key] = cast(m.group(1))
        m = re.search(r'"dataset_dir"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
        if m:
            info["dataset_dir"] = json.loads(f'"{m.group(1)}"')
    except OSError:
        pass
    return info


def _iter_records(catalog: dict):
    records = catalog.get("records", {})
    if isinstance(records, dict):
        yield from (r for r in records.values() if isinstance(r, dict))
    elif isinstance(records, list):
        yield from (r for r in records if isinstance(r, dict))


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _is_weak_alias_name(name: str, canonical: str) -> bool:
    name = nfc(name).strip()
    canonical = nfc(canonical).strip()
    bare_title = _TITLE_SUFFIX_RE.sub("", canonical).strip()
    return (
        bool(name)
        and name not in (canonical, bare_title)
        and len(name) <= 2
        and all(HAN_RE.fullmatch(char) for char in name)
    )


def _automatic_aliases(rec: dict, canonical: str) -> list[str]:
    if "strong_aliases" in rec:
        return _as_list(rec.get("strong_aliases"))
    return [
        alias for alias in _as_list(rec.get("aliases"))
        if not _is_weak_alias_name(alias, canonical)
    ]


def _norm_subject(value: str) -> str:
    return re.sub(r"\s+", "", nfc(value)).casefold()


def _latin_entries_for(rec: dict) -> list[dict]:
    """latin_entries của bản ghi (schema 3); [] nếu không có hoặc sai kiểu."""
    entries = rec.get("latin_entries")
    if not isinstance(entries, list):
        return []
    out = []
    for e in entries:
        if isinstance(e, dict) and e.get("name"):
            out.append({"name": str(e["name"]).strip(),
                        "field": e.get("field"),
                        "confidence": e.get("confidence")})
    return out


def _conf_key(option: dict):
    """Khóa sắp xếp latin_option: confidence cao lên trước; None (không rõ) xếp cuối."""
    c = option.get("confidence")
    return (1 if c is None else 0, -(c if c is not None else 0.0))


def _conf_sort(confidence) -> float:
    """Khóa sắp xếp giảm dần theo confidence; None xếp cuối."""
    return 1.0 if confidence is None else -float(confidence)


def _names_bgm_option(entry) -> Optional[dict]:
    """Mục trong moegirl_wikitext_names_bgm.json -> latin_option (cùng dạng _latin_entries_for)."""
    if not isinstance(entry, dict):
        return None
    latin = str(entry.get("latin") or "").strip()
    if not latin or not LATIN_RE.search(latin) or HAN_RE.search(latin):
        return None
    source = entry.get("source")
    if source == "page":
        field, conf = "wikitext", 0.95          # Latin có sẵn trên trang Moegirl
    elif source == "bangumi":
        field, conf = "bangumi", 0.9            # 罗马字 của Bangumi
    elif source == "bangumi-kana":
        field, conf = "bangumi-kana", 0.7       # tự đổi từ kana của Bangumi
    else:
        field, conf = "wikitext-kana", 0.7      # tự đổi từ kana trên trang Moegirl
    if entry.get("latin_differs") or entry.get("id_fallback"):
        conf = min(conf, 0.6)                   # cần người soát lại
    return {"name": latin, "field": field, "confidence": conf}


def _name_split(entry, canonical: str):
    """Tách họ/tên của người có trong names_bgm: (họ_Hán, tên_Hán, họ_Latin, tên_Latin) hoặc None.
    Chính xác khi dòng 姓名/本名 trên Moegirl viết '比企谷 八幡（ひきがや はちまん，Hikigaya Hachiman）'
    (có dấu cách giữa họ và tên) và Latin gồm đúng 2 từ theo thứ tự họ-tên."""
    if not isinstance(entry, dict) or not canonical:
        return None
    toks = str(entry.get("latin") or "").split()
    if len(toks) != 2:
        return None
    m = re.search(r"[=：:]\s*([^（(]*)", str(entry.get("line") or ""))
    parts = (m.group(1) if m else "").split()
    if len(parts) != 2 or not all(HAN_RE.search(x) for x in parts):
        return None
    if len(parts[0]) + len(parts[1]) != len(canonical):
        return None
    fl = len(parts[0])
    return canonical[:fl], canonical[fl:], toks[0], toks[1]


def _options_for_alias(name: str, canonical: str, options: list, names_entry) -> list:
    """Bí danh chỉ là HỌ hoặc TÊN của nhân vật thì chỉ trả về phần Latin tương ứng, không trả cả họ lẫn tên."""
    if not name or name == canonical or not canonical or "·" in canonical or "・" in canonical:
        return options
    split = _name_split(names_entry, canonical)
    if split:
        fam_han, giv_han, fam_lat, giv_lat = split
        part = fam_lat if name == fam_han else giv_lat if name == giv_han else None
        if part:
            base = _names_bgm_option(names_entry) or options[0]
            return [{"name": part, "field": (base.get("field") or "") + "(tách)",
                     "confidence": base.get("confidence")}]
    # Chưa biết chỗ tách: tên Latin 2 từ (họ tên) + bí danh là đầu/cuối của tên Hán -> đoán họ/tên.
    if len(canonical) >= 3 and len(name) < len(canonical) and name != canonical:
        idx = 0 if canonical.startswith(name) else 1 if canonical.endswith(name) else None
        if idx is not None:
            out = []
            for o in options:
                toks = str(o["name"]).split()
                if len(toks) == 2:
                    conf = o.get("confidence")
                    out.append({"name": toks[idx], "field": (o.get("field") or "") + "(đoán tách)",
                                "confidence": None if conf is None else round(float(conf) * 0.8, 2)})
                else:
                    out.append(o)
            return out
    return options


def _latin_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(s)).lower())


class MoegirlService:
    def __init__(self, project: Project) -> None:
        self.project = project
        self._previews: dict[str, dict] = {}
        self._lock = threading.RLock()

    # ---- kho dữ liệu ----
    def cache_info(self) -> dict:
        info = catalog_info(self.project.moegirl_terms_path())
        info["project_cache"] = catalog_info(self.project.paths.project_cache)
        info["scope"] = self.project.cfg["moegirl"]["scope"]
        return info

    def _load_catalog(self) -> dict:
        path = self.project.moegirl_terms_path()
        if not path.is_file():
            raise ApiError("NO_CATALOG", f"Chưa có kho Moegirl: {path}. Hãy chạy moegirl_dataset_import.py.")
        data = json.loads(read_text_auto(path))
        if not isinstance(data, dict):
            raise ApiError("BAD_CATALOG", "moegirl_terms.json phải là object có khóa 'records'.")
        return data

    def rebuild_cache(self, dataset_dir: str = "") -> dict:
        """Gọi moegirl_dataset_import.build_catalog (một nguồn sự thật duy nhất)."""
        importer = CODE_ROOT / "moegirl_dataset_import.py"
        if not importer.is_file():
            raise ApiError("NO_IMPORTER", f"Không thấy {importer}.")
        module = load_module_from(importer, "nv_moegirl_importer")
        # Ưu tiên tham số > config dự án > thư mục dataset mặc định của importer.
        explicit = dataset_dir or self.project.cfg["moegirl"].get("dataset_dir") or ""
        resolve = getattr(module, "find_dataset_dir", None)
        folder = resolve(explicit) if resolve else Path(
            explicit or getattr(module, "DEFAULT_DATASET_DIR", "") or "")
        if not folder or not folder.is_dir():
            raise ApiError("NO_DATASET", f"Không thấy thư mục dataset: {folder}")
        if not (folder / "extra_processed.json").is_file():
            raise ApiError("NO_DATASET",
                           f"Thư mục dataset thiếu extra_processed.json: {folder}")
        catalog = module.build_catalog(folder)
        module.save_catalog(catalog, self.project.moegirl_terms_path())
        self.project.cfg["moegirl"]["dataset_dir"] = str(folder)
        return {"records": catalog.get("record_count"), "path": str(self.project.moegirl_terms_path())}

    # ---- tên Latin từ moegirl_wikitext_names_bgm.json (cạnh novel_backend.py) ----
    def names_bgm_path(self) -> Path:
        """Nằm cùng thư mục với file truyện nguồn của dự án."""
        return self.project.paths.dir / NAMES_BGM_FILE

    def _find_bgm_dir(self) -> Optional[Path]:
        """Thư mục chứa moegirl2bgm.json: config moegirl.bgm_dir, rồi các chỗ thường gặp."""
        cfg = self.project.cfg["moegirl"]
        for c in (cfg.get("bgm_dir"), PROGRAM_DIR / "bangumi", PROGRAM_DIR, CODE_ROOT / "bangumi", CODE_ROOT,
                  cfg.get("dataset_dir")):
            if c and (Path(c) / "moegirl2bgm.json").is_file():
                return Path(c)
        return None

    def _read_names_file(self) -> Optional[dict]:
        """Đọc ĐÚNG moegirl_wikitext_names_bgm.json; None nếu thiếu/hỏng/rỗng (không dùng file dự phòng)."""
        path = self.names_bgm_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(read_text_auto(path))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data else None

    def _read_names_bgm(self) -> dict:
        """{page_title: entry|None}. Chưa có file cuối thì dùng tạm moegirl_wikitext_names.json."""
        for path in (self.names_bgm_path(), PROGRAM_DIR / "moegirl_wikitext_names.json"):  # dự phòng: cache wikitext
            if path.is_file():
                try:
                    data = json.loads(read_text_auto(path))
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict):
                    return data
        return {}

    def view_names_bgm(self) -> dict:
        """Nội dung moegirl_wikitext_names_bgm.json để giao diện hiển thị (không tạo mới)."""
        path = self.names_bgm_path()
        data = self._read_names_file()
        if data is None:
            raise ApiError("NO_NAMES_FILE", f"Chưa có file tên hợp lệ (thiếu, rỗng hoặc hỏng JSON): {path}. "
                           "Bấm 'Tạo lại gợi ý' để tạo.")
        return {"path": str(path), "data": data, "total": len(data),
                "filled": sum(1 for v in data.values() if v)}

    def ensure_names_bgm(self) -> dict:
        """File đã có thì dùng; chưa có thì tạo (tải wikitext Moegirl + Bangumi) cho nhân vật của các tác phẩm đã duyệt."""
        path = self.names_bgm_path()
        data = self._read_names_file()
        if data is not None:
            return {"created": False, "path": str(path), "total": len(data),
                    "filled": sum(1 for v in data.values() if v)}
        approved = self.project.approvals.approved_ids()
        if not approved:
            # generate() vẫn chạy được khi chưa duyệt tác phẩm nào, nên không chặn ở đây.
            return {"created": False, "skipped": "Chưa duyệt tác phẩm nào nên chưa tạo "
                    "moegirl_wikitext_names_bgm.json (duyệt ở màn hình B rồi bấm lại để tạo).",
                    "path": str(path), "total": 0, "filled": 0}
        catalog = self._load_catalog()
        subjects = self._subjects_for_works(catalog, approved)
        wanted = set().union(*subjects.values()) if subjects else set()
        titles, seen = [], set()
        for rec in _iter_records(catalog):
            subs = {_norm_subject(s) for s in _as_list(rec.get("subjects"))}
            t = str(rec.get("page_title") or "")
            if t and t not in seen and subs & wanted:
                seen.add(t); titles.append(t)
        del catalog
        if not titles:
            return {"created": False, "skipped": "Không có nhân vật Moegirl nào khớp các tác phẩm đã duyệt "
                    "nên chưa tạo file tên.", "path": str(path), "total": 0, "filled": 0}
        if str(PROGRAM_DIR) not in sys.path:
            sys.path.insert(0, str(PROGRAM_DIR))
        try:
            import names_bgm_service
        except ImportError as exc:
            raise ApiError("NO_NAMES_SERVICE", f"Thiếu names_bgm_service.py hoặc module phụ thuộc: {exc}")
        bgm_dir = self._find_bgm_dir()
        with self._lock:
            result = names_bgm_service.build_names_bgm(
                titles, bgm_dir, work_dir=PROGRAM_DIR, out_dir=self.project.paths.dir,
                cookie=str(self.project.cfg["moegirl"].get("cookie") or ""))
        warnings = []
        if result.get("wiki_error"):
            warnings.append(result["wiki_error"] + ". Kiểm tra mạng hoặc đặt cookie (biến môi trường MOEGIRL_COOKIES hoặc moegirl.cookie trong config.json) "
                            "rồi xóa file kết quả để tạo lại.")
        if not bgm_dir:
            warnings.append("Không thấy moegirl2bgm.json nên chưa bổ sung được tên từ Bangumi "
                            "(đặt moegirl.bgm_dir trong config.json rồi xóa file kết quả để tạo lại).")
        if warnings:
            result["warning"] = " | ".join(warnings)
        return result

    # ---- xác định tác phẩm -> tập subject trong Moegirl ----
    def _subjects_for_works(self, catalog: dict, work_ids: list[str]) -> dict[str, set[str]]:
        project = self.project
        titles = project.anchors.works()
        by_name: dict[str, list[dict]] = {}
        for rec in _iter_records(catalog):
            names = [_TITLE_SUFFIX_RE.sub("", str(rec.get("canonical_zh") or rec.get("page_title") or ""))]
            names += _as_list(rec.get("aliases"))
            for n in names:
                n = nfc(n).strip()
                if len(n) >= 2:
                    by_name.setdefault(n, []).append(rec)
        configured = project.cfg["moegirl"].get("work_subjects", {}) or {}
        exclude = {_norm_subject(s) for s in _as_list(
            project.cfg["moegirl"].get("exclude_subjects",
                                       ("中国作品", "日本作品", "美国作品", "韩国作品", "欧美作品", "各地区")))}
        result: dict[str, set[str]] = {}
        diag: dict[str, list[str]] = {}
        for wid in work_ids:
            # Thẻ do người dùng đặt tường minh trong work_subjects thì GIỮ nguyên, không lọc.
            subjects = {_norm_subject(s) for s in _as_list(configured.get(wid, []))}
            title = titles.get(wid, "")
            if title:
                subjects.add(_norm_subject(title))
            counter: Counter = Counter()
            matched_anchors = 0
            for row in project.anchors.eligible():
                if row["work_id"] != wid:
                    continue
                recs = []
                for n in [row["source_name"]] + split_aliases(row.get("aliases", "")):
                    recs.extend(by_name.get(nfc(n).strip(), []))
                if recs:
                    matched_anchors += 1
                    seen_here = set()
                    for rec in recs:
                        for s in _as_list(rec.get("subjects")):
                            ns = _norm_subject(s)
                            if ns in exclude or _SUBJECT_REGION_RE.search(str(s).strip()):
                                continue
                            seen_here.add(ns)
                    counter.update(seen_here)
            threshold = max(1, min(2, matched_anchors))
            subjects.update(s for s, c in counter.items() if c >= threshold)
            result[wid] = {s for s in subjects if s}
            diag[wid] = sorted(result[wid])
        self._subjects_diag = diag
        return result

    # ---- bộ nhớ đệm riêng của dự án cho engine ----
    def build_project_cache(self) -> dict:
        """Ghi moegirl_terms.project.json chỉ gồm nhân vật thuộc các tác phẩm ĐÃ DUYỆT."""
        approved = self.project.approvals.approved_ids()
        catalog = self._load_catalog()
        subjects = self._subjects_for_works(catalog, approved) if approved else {}
        wanted = set().union(*subjects.values()) if subjects else set()
        kept = {}
        for rec in _iter_records(catalog):
            subs = {_norm_subject(s) for s in _as_list(rec.get("subjects"))}
            if subs & wanted:
                kept[str(rec.get("page_title") or rec.get("canonical_zh"))] = rec
        out = {k: v for k, v in catalog.items() if k != "records"}
        out.update({"record_count": len(kept), "records": kept, "scoped_to_works": approved,
                    "generated_at": now_iso()})
        atomic_write(self.project.paths.project_cache, json.dumps(out, ensure_ascii=False))
        return {"path": str(self.project.paths.project_cache), "records": len(kept), "works": approved}

    # ---- sinh ứng viên ----
    def _load_store(self) -> dict:
        path = self.project.paths.suggestions
        if path.is_file():
            try:
                data = json.loads(read_text_auto(path))
                if isinstance(data, dict) and isinstance(data.get("candidates"), list):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        return {"version": 1, "candidates": [], "filtered": {}}

    def _save_store(self, data: dict) -> None:
        atomic_write(self.project.paths.suggestions, json.dumps(data, ensure_ascii=False, indent=1))

    def generate(self) -> dict:
        """
        Quét toàn bộ catalog Moegirl trên toàn văn bản truyện.

        Tác phẩm đã duyệt chỉ là tín hiệu ưu tiên:
        - Record có subject giao với subject của tác phẩm đã duyệt được đánh dấu priority.
        - Record không liên quan vẫn được xét, nhưng chỉ trở thành ứng viên nếu tên/alias
          thực sự xuất hiện trong truyện đủ min_frequency lần.
        - Các record trùng tên tiếng Trung được gộp thành một ứng viên để tránh nhân đôi
          dòng glossary; metadata page_titles giữ lại nguồn gốc để nhận biết nhập nhằng.
        """
        project = self.project
        approved = project.approvals.approved_ids()
        text, chapters, _ = project.source_text()
        catalog = self._load_catalog()
        subjects = self._subjects_for_works(catalog, approved) if approved else {}

        min_freq = max(1, int(project.cfg["moegirl"].get("min_frequency", 3)))
        min_conf = float(project.cfg["moegirl"].get("min_confidence", 0.0) or 0.0)
        glossary = {
            r["src"]: r for r in parse_glossary(
                read_text_auto(project.paths.glossary)
                if project.paths.glossary.is_file() else ""
            )["rows"]
        }
        filtered: Counter = Counter()
        raw: dict[str, dict] = {}
        matched_records = 0
        no_matching_work = 0
        scanned_records = 0
        names_bgm = self._read_names_bgm()

        for rec in _iter_records(catalog):
            scanned_records += 1
            subs = {_norm_subject(s) for s in _as_list(rec.get("subjects"))}
            works = [w for w in approved if subs & subjects.get(w, set())]
            is_priority = bool(works)
            if is_priority:
                matched_records += 1
            else:
                no_matching_work += 1

            options = _latin_entries_for(rec)
            if not options:
                latin_names = [
                    x for x in _as_list(rec.get("latin_names"))
                    if LATIN_RE.search(x) and not HAN_RE.search(x)
                ]
                options = [
                    {"name": x, "field": None, "confidence": None}
                    for x in latin_names
                ]

            page_title = str(
                rec.get("page_title") or rec.get("canonical_zh") or ""
            ).strip()
            extra = _names_bgm_option(names_bgm.get(
                page_title or str(rec.get("canonical_zh") or "")
            ))
            if extra and _latin_key(extra["name"]) not in {
                _latin_key(o["name"]) for o in options
            }:
                options.append(extra)

            options = [
                o for o in options
                if o.get("name")
                and (o.get("confidence") is None
                     or float(o["confidence"]) >= min_conf)
            ]
            if not options:
                filtered["no_latin_name"] += 1
                continue

            canonical = nfc(_TITLE_SUFFIX_RE.sub(
                "", str(rec.get("canonical_zh") or rec.get("page_title") or "")
            )).strip()
            names_for_record = (
                [(canonical, "moegirl_name")]
                + [(nfc(a).strip(), "moegirl_alias")
                   for a in _automatic_aliases(rec, canonical)]
            )

            for name, tag in names_for_record:
                if not name or not HAN_RE.search(name):
                    filtered["not_han"] += 1
                    continue
                if len(name) < 2:
                    filtered["too_short"] += 1
                    continue
                if name in _COMMON_ZH:
                    filtered["common_word"] += 1
                    continue

                entry = raw.setdefault(name, {
                    "src": name,
                    "works": set(),
                    "priority": False,
                    "source_tag": tag,
                    "aliases": set(),
                    "options": {},
                    "page_titles": set(),
                    "record_count": 0,
                })
                entry["works"].update(works)
                entry["priority"] = entry["priority"] or is_priority
                entry["record_count"] += 1
                if tag == "moegirl_name":
                    entry["source_tag"] = tag
                if canonical:
                    entry["aliases"].add(canonical)
                if page_title:
                    entry["page_titles"].add(page_title)

                for option in _options_for_alias(
                        name, canonical, options, names_bgm.get(page_title)):
                    key = option["name"]
                    current = entry["options"].get(key)
                    if current is None or _conf_key(option) < _conf_key(current):
                        entry["options"][key] = dict(option)

        # Toàn bộ catalog đều đã được xét; không còn lỗi NO_MATCHING_RECORDS chỉ vì
        # subject không khớp tác phẩm đã duyệt.
        names = list(raw)
        counts, positions = AhoCorasick(names).scan(text, keep_positions=2000) if names else ([], [])
        previous = {
            c.get("src"): c for c in self._load_store().get("candidates", [])
            if c.get("src")
        }
        candidates = []
        titles = project.anchors.works()

        for pid, name in enumerate(names):
            if counts[pid] < min_freq:
                filtered["low_frequency"] += 1
                continue

            entry = raw[name]
            if len(entry["page_titles"]) > 1:
                filtered["ambiguous_name"] += 1
                continue
            options = sorted(entry["options"].values(), key=_conf_key)
            if not options:
                filtered["no_latin_name"] += 1
                continue

            picked = options[0]
            dst = picked["name"]
            confidence = picked.get("confidence")
            latin_field = picked.get("field")

            existing = glossary.get(name)
            old = previous.get(name, {})
            # Chỉ giữ dst cũ khi người dùng đã chỉnh tay, đã duyệt/từ chối hoặc đã ghi vào glossary;
            # ứng viên còn "Chờ duyệt" thì lấy tên mới (vd. từ moegirl_wikitext_names_bgm.json).
            keep_old = bool(old.get("dst")) and bool(
                old.get("dst_edited") or old.get("committed_at"))
            final_dst = old["dst"] if keep_old else dst
            # Tên tự sinh đã đổi so với lần trước mà người dùng đã Duyệt/Từ chối trên tên cũ
            # -> quyết định đó không còn đúng, đưa về Chờ duyệt để xem lại.
            old_status = old.get("status", "pending")
            status_now = old_status
            renamed = bool(old.get("dst")) and not keep_old and old["dst"] != final_dst
            if renamed and old_status != "pending":
                status_now = "pending"
            if existing and existing["dst"] == final_dst:
                if not old.get("committed_at"):
                    filtered["already_in_glossary"] += 1
                    continue
                existing = None

            chapter_set = sorted({
                chapters.chapter_of(pos) for pos in positions[pid]
            })
            note_parts = []
            if entry["priority"]:
                note_parts.append("Ưu tiên: thuộc subject của tác phẩm đã duyệt")
            else:
                note_parts.append("Phát hiện toàn cục: chưa khớp subject tác phẩm đã duyệt")
            other_names = [o["name"] for o in options[1:]]
            if other_names:
                note_parts.append(
                    "Có tên Latin khác: " + ", ".join(sorted(set(other_names))[:3])
                )

            work_ids = sorted(entry["works"])
            # Ưu tiên record có liên hệ với tác phẩm đã duyệt; trong cùng nhóm,
            # xếp theo tần suất và độ phủ chương.
            priority_score = (
                (1000000 if entry["priority"] else 0)
                + counts[pid] * 10
                + len(chapter_set)
            )
            candidates.append({
                "id": "CAND_" + short_id(name),
                "src": name,
                # Giữ dst đã chỉnh tay/đã duyệt; ứng viên chờ duyệt được làm mới.
                "dst": final_dst,
                "dst_edited": bool(old.get("dst_edited")),
                "dst_alt": old.get("dst_alt", ""),
                "works": work_ids,
                "work_titles": [titles.get(w, w) for w in work_ids],
                "priority": bool(entry["priority"]),
                "priority_score": priority_score,
                "aliases": sorted(entry["aliases"] - {name})[:10],
                "count": counts[pid],
                "chapters": len(chapter_set),
                "source_tag": entry["source_tag"],
                "category": "character",
                "status": status_now,
                "mode": old.get("mode", "force"),
                "confidence": confidence,
                "latin_field": latin_field,
                "latin_options": options,
                "page_titles": sorted(entry["page_titles"]),
                "record_count": entry["record_count"],
                "note": " ; ".join(note_parts + (
                    [f"Tên đã đổi từ '{old['dst']}' (trước đó: {old_status}), cần duyệt lại"]
                    if renamed and old_status != "pending" else [])),
                "conflict": (
                    {"existing_dst": existing["dst"], "line": existing["line"]}
                    if existing else None
                ),
                "committed_at": old.get("committed_at"),
            })

        candidates.sort(key=lambda c: (
            not c["priority"], -c["count"], -c["chapters"], c["src"]
        ))
        data = {
            "version": 2,
            "generated_at": now_iso(),
            "source": str(project.paths.source),
            "approved_works": approved,
            "priority_mode": "approved_works_boost_global_scan",
            "min_frequency": min_freq,
            "min_confidence": min_conf,
            "scanned_records": scanned_records,
            "matched_records": matched_records,
            "no_matching_work": no_matching_work,
            "filtered": dict(filtered),
            "candidates": candidates,
        }
        self._save_store(data)
        del catalog
        return {
            "candidates": len(candidates),
            "filtered": dict(filtered),
            "filtered_total": sum(filtered.values()),
            "works": approved,
            "scanned_records": scanned_records,
            "matched_records": matched_records,
            "no_matching_work": no_matching_work,
            "priority_candidates": sum(1 for c in candidates if c["priority"]),
            "global_candidates": sum(1 for c in candidates if not c["priority"]),
        }

    # ---- danh sách / chi tiết ----
    def list(self, *, work_id: str = "", status: str = "", query: str = "", case=False, whole=False,
             regex=False, sort: str = "count", page_no: int = 1, page_size: int = 50,
             conflict: bool = False) -> dict:
        data = self._load_store()
        match = make_matcher(query, case=case, whole=whole, regex=regex)
        items = []
        for c in data["candidates"]:
            if work_id and work_id not in c["works"]:
                continue
            if status and status != "all" and c["status"] != status:
                continue
            if conflict and not c.get("conflict"):
                continue
            if not match(f"{c['src']} {c['dst']} {c['dst_alt']} {' '.join(c['aliases'])} {c['note']}"):
                continue
            items.append(c)
        keys = {
            # Mặc định: nhóm thuộc tác phẩm đã duyệt đứng trước, sau đó mới xét tần suất.
            "count": lambda c: (not c.get("priority", False), -c["count"], c["src"]),
            "priority": lambda c: (not c.get("priority", False), -c["count"], c["src"]),
            "src": lambda c: c["src"],
            "status": lambda c: (c["status"], not c.get("priority", False), -c["count"]),
            "confidence": lambda c: (_conf_sort(c.get("confidence")), not c.get("priority", False), c["src"]),
        }
        items.sort(key=keys.get(sort, keys["count"]))
        work_counts = Counter(w for c in data["candidates"] for w in c["works"])
        status_counts = Counter(c["status"] for c in data["candidates"])
        result = page(items, page_no, page_size)
        result.update({"work_counts": dict(work_counts), "status_counts": dict(status_counts),
                       "total_candidates": len(data["candidates"]), "filtered": data.get("filtered", {}),
                       "filtered_total": sum(data.get("filtered", {}).values()),
                       "matched_records": data.get("matched_records"),
                       "no_matching_work": data.get("no_matching_work"),
                       "generated_at": data.get("generated_at")})
        return result

    def detail(self, cand_id: str, max_contexts: int = 3) -> dict:
        data = self._load_store()
        cand = next((c for c in data["candidates"] if c["id"] == cand_id), None)
        if cand is None:
            raise ApiError("NOT_FOUND", f"Không có ứng viên {cand_id}.")
        text, chapters, _ = self.project.source_text()
        contexts, pos = [], 0
        while len(contexts) < max_contexts:
            pos = text.find(cand["src"], pos)
            if pos < 0:
                break
            chapter = chapters.chapter_of(pos)
            contexts.append({"chapter": chapter, "chapter_title": chapters.title_of(chapter),
                             "line": text.count("\n", 0, pos) + 1,
                             "text": snippet(text, pos, len(cand["src"]), 40, 60)})
            pos += max(1, len(cand["src"])) + 200  # lấy các ngữ cảnh cách xa nhau
        return {**cand, "contexts": contexts,
                "glossary_line": format_entry(cand["src"], cand["dst"], cand["mode"])}

    def _mutate(self, cand_ids: list[str], fn: Callable[[dict], None]) -> int:
        with self._lock:
            data = self._load_store()
            ids, n = set(cand_ids or []), 0
            for c in data["candidates"]:
                if c["id"] in ids:
                    fn(c)
                    n += 1
            self._save_store(data)
            return n

    def set_status(self, cand_ids: list[str], status: str) -> dict:
        if status not in SUGGESTION_STATUSES:
            raise ApiError("BAD_STATUS", f"Trạng thái không hợp lệ: {status}")
        return {"updated": self._mutate(cand_ids, lambda c: c.update(status=status))}

    def edit(self, cand_id: str, *, dst: Optional[str] = None, dst_alt: Optional[str] = None,
             mode: Optional[str] = None, note: Optional[str] = None) -> dict:
        def apply(c: dict) -> None:
            if dst is not None:
                if str(dst).strip() != c.get("dst"):
                    c["dst_edited"] = True
                c["dst"] = str(dst).strip()
            if dst_alt is not None:
                c["dst_alt"] = str(dst_alt).strip()
            if mode in ("force", "hint"):
                c["mode"] = mode
            if note is not None:
                c["note"] = str(note)
        if self._mutate([cand_id], apply) == 0:
            raise ApiError("NOT_FOUND", f"Không có ứng viên {cand_id}.")
        return {"updated": 1}

    def export(self, fmt: str = "tsv", path: str = "") -> dict:
        data = self._load_store()
        sep = "\t" if fmt == "tsv" else " => "
        lines = []
        if fmt == "tsv":
            lines.append("\t".join(["src", "dst", "works", "count", "status", "source_tag",
                                    "confidence", "note"]))
        for c in data["candidates"]:
            if fmt == "tsv":
                conf = c.get("confidence")
                lines.append("\t".join([c["src"], c["dst"], ",".join(c["work_titles"]), str(c["count"]),
                                        c["status"], c["source_tag"],
                                        ("" if conf is None else str(conf)), c["note"]]))
            else:
                lines.append(f"{c['src']}{sep}{c['dst']} # {c['status']}; {c['count']} lần")
        rows = len(data["candidates"])
        text = "\n".join(lines) + ("\n" if lines else "")
        if path:
            atomic_write(Path(path), text)
            return {"path": path, "rows": rows}
        return {"text": text, "rows": rows}

    # ---- commit có xác nhận hai bước ----
    def commit_preview(self, cand_ids: Optional[list[str]] = None, conflict_policy: str = "hint") -> dict:
        data = self._load_store()
        ids = set(cand_ids) if cand_ids else None
        chosen = [c for c in data["candidates"] if c["status"] == "approved"
                  and (ids is None or c["id"] in ids)]
        if not chosen:
            raise ApiError("NOTHING_APPROVED", "Chưa có ứng viên nào được duyệt để ghi.")
        invalid = [c["id"] for c in chosen if not c["dst"].strip()]
        if invalid:
            raise ApiError("EMPTY_TARGET", "Có ứng viên chưa có vế đích.", ids=invalid)
        titles = self.project.anchors.works()
        entries = [{"src": c["src"], "dst": c["dst"], "mode": c["mode"],
                    "note": c["source_tag"],
                    "section": " + ".join(titles.get(w, w) for w in c["works"]), "id": c["id"]}
                   for c in chosen]
        plan = self.project.glossary.plan_merge(entries, conflict_policy)
        signature = short_id(json.dumps(plan["to_add"], ensure_ascii=False, sort_keys=True), n=16)
        token = uuid.uuid4().hex
        with self._lock:
            self._previews = {t: p for t, p in self._previews.items() if time.time() - p["t"] < 600}
            self._previews[token] = {"t": time.time(), "plan": plan, "signature": signature,
                                     "ids": [e["id"] for e in plan["to_add"]],
                                     "glossary_hash": self._glossary_hash()}
        return {"token": token, "expires_in": 600, "target": str(self.project.paths.glossary),
                "add": [{k: e[k] for k in ("src", "dst", "mode", "note", "section")} for e in plan["to_add"]],
                "conflicts": plan["conflicts"], "already_present": plan["already_present"],
                "lines_before": plan["lines_before"], "lines_after_estimate": plan["lines_after_estimate"],
                "preview_text": [format_entry(e["src"], e["dst"], e["mode"], e.get("note", ""))
                                 for e in plan["to_add"]]}

    def commit(self, token: str) -> dict:
        with self._lock:
            preview = self._previews.pop(token, None)
        if not preview or time.time() - preview["t"] > 600:
            raise ApiError("BAD_TOKEN", "Chưa xem trước hoặc mã xác nhận đã hết hạn; hãy xem trước lại.")
        if self._glossary_hash() != preview["glossary_hash"]:
            raise ApiError("GLOSSARY_CHANGED", "thuatngu.txt đã thay đổi từ lúc xem trước; hãy xem trước lại.")
        applied = self.project.glossary.apply_merge(preview["plan"])
        ids = set(preview["ids"])
        self._mutate(list(ids), lambda c: c.update(committed_at=now_iso()))
        applied["committed"] = len(ids)
        return applied

    def _glossary_hash(self) -> str:
        path = self.project.paths.glossary
        return hashlib.sha1(path.read_bytes()).hexdigest() if path.is_file() else ""


# ============================================================
# 10. LOG (màn hình G) VÀ ENGINE RUNNER (màn hình F)
# ============================================================

LOG_LEVELS = ("INFO", "SUCCESS", "WARNING", "ERROR")


def classify_log(text: str) -> str:
    low = text.lower()
    if text.startswith("[GUI]"):
        return "INFO"
    if "traceback" in low or "[lỗi " in low or low.startswith("lỗi") or "exception" in low:
        return "ERROR"
    if "[bản dịch bị loại" in low or "cảnh báo" in low or "warning" in low or "[guard cảnh báo]" in low:
        return "WARNING"
    if text.startswith("Đã lưu chunk") or "hoàn tất" in low or "đã hoàn tất" in low or "đã thay thế" in low:
        return "SUCCESS"
    return "INFO"


class LogBuffer:
    def __init__(self, capacity: int = 5000) -> None:
        self.capacity = capacity
        self.entries: deque[dict] = deque(maxlen=capacity)
        self.next_id = 1
        self.cond = threading.Condition()
        self.file_path: Optional[Path] = None

    def add(self, text: str, level: Optional[str] = None, **extra: Any) -> dict:
        text = str(text).rstrip()
        if not text:
            return {}
        entry = {"id": 0, "time": datetime.now().strftime("%H:%M:%S"), "ts": time.time(),
                 "level": level or classify_log(text), "text": text, **extra}
        with self.cond:
            entry["id"] = self.next_id
            self.next_id += 1
            self.entries.append(entry)
            if self.file_path:
                try:
                    with self.file_path.open("a", encoding="utf-8") as handle:
                        handle.write(f"[{entry['level']}] [{entry['time']}] {text}\n")
                except OSError:
                    pass
            self.cond.notify_all()
        return entry

    def query(self, *, since_id: int = 0, levels: Optional[list[str]] = None, query: str = "",
              case=False, regex=False, limit: int = 500) -> dict:
        match = make_matcher(query, case=case, regex=regex)
        wanted = {x.upper() for x in levels} if levels else None
        with self.cond:
            snapshot = list(self.entries)
        counts = Counter(e["level"] for e in snapshot)
        items = [e for e in snapshot if e["id"] > since_id and (wanted is None or e["level"] in wanted)
                 and match(e["text"] + " " + e["time"])]
        return {"items": items[-limit:], "counts": {"ALL": len(snapshot), **{k: counts.get(k, 0) for k in LOG_LEVELS}},
                "last_id": snapshot[-1]["id"] if snapshot else 0, "buffer_lines": len(snapshot)}

    def wait(self, since_id: int, timeout: float) -> bool:
        with self.cond:
            if self.entries and self.entries[-1]["id"] > since_id:
                return True
            return self.cond.wait(timeout)

    def clear(self) -> None:
        with self.cond:
            self.entries.clear()

    def export(self, path: Path) -> int:
        with self.cond:
            lines = [f"[{e['level']}] [{e['time']}] {e['text']}" for e in self.entries]
        atomic_write(path, "\n".join(lines) + "\n")
        return len(lines)


RE_TOTAL = re.compile(r"Đã tạo (\d+) chunk")
RE_CHUNK = re.compile(r"^Chunk (\d+)/(\d+) \| Chương (\d+) \| Part (\d+)/(\d+) \| (\d+) ký tự")
RE_ATTEMPT = re.compile(r"Gọi model .*?, lần (\d+)/(\d+)")
RE_INVALID = re.compile(r"\[Bản dịch bị loại ở chunk (\d+)\] lần (\d+): (.*)")
RE_ERROR = re.compile(r"\[Lỗi (\w+) ở chunk (\d+)\] lần (\d+): (.*)")
RE_SAVED = re.compile(r"^Đã lưu chunk (\d+); mất ([\d.]+)s")
RE_FAILED = re.compile(r"hết số lần thử")
RE_GUARD = re.compile(r"\[Guard cảnh báo\]")


HOOK_TEMPLATE = r'''
# ======== NOVEL GUI HOOKS (tự chèn bởi novel_backend; không có trong engine gốc) ========
import contextlib as _nv_cl, json as _nv_json, os as _nv_os, sys as _nv_sys, time as _nv_time
_NV_SIDECAR = {sidecar!r}
_NV_PAUSE = {pause!r}
_NV_STOP = {stop!r}
_NV_OUTPUT = {output!r}
_nv_last = {{"text": ""}}
_nv_lock = {{"held": False, "ctx": None}}


@_nv_cl.contextmanager
def _nv_file_lock(path, timeout=15.0, stale=30.0):
    lock = path + ".lock"
    deadline = _nv_time.time() + timeout
    while True:
        try:
            fd = _nv_os.open(lock, _nv_os.O_CREAT | _nv_os.O_EXCL | _nv_os.O_WRONLY)
            _nv_os.close(fd)
            break
        except FileExistsError:
            try:
                if _nv_time.time() - _nv_os.stat(lock).st_mtime > stale:
                    _nv_os.unlink(lock)
                    continue
            except OSError:
                pass
            if _nv_time.time() > deadline:
                raise RuntimeError("file locked: " + path)
            _nv_time.sleep(0.05)
    try:
        yield
    finally:
        try:
            _nv_os.unlink(lock)
        except OSError:
            pass


def _nv_acquire():
    if not _nv_lock["held"]:
        _nv_lock["ctx"] = _nv_file_lock(_NV_OUTPUT)
        _nv_lock["ctx"].__enter__()
        _nv_lock["held"] = True


def _nv_release():
    if _nv_lock["held"]:
        _nv_lock["ctx"].__exit__(None, None, None)
        _nv_lock["held"] = False
        _nv_lock["ctx"] = None


def read_text_file(path):
    data = open(path, "rb").read()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = None
        for _enc in ("utf-8-sig", "gb18030"):
            try:
                text = data.decode(_enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = data.decode("utf-8", errors="replace")
    # Output trên Windows có \r\n; engine so khớp nhãn chưa dịch bằng \n nên phải chuẩn hóa.
    return text.replace("\r\n", "\n").replace("\r", "\n")

try:
    _nv_orig_append_output = append_output
    def append_output(text):
        _nv_last["text"] = text
        _nv_acquire()
        try:
            return _nv_orig_append_output(text)
        except Exception:
            _nv_release()
            raise
except NameError:
    pass

try:
    _nv_orig_save_progress = save_progress
    def save_progress(next_chunk, chunks):
        _nv_acquire()
        try:
            result = _nv_orig_save_progress(next_chunk, chunks)
            try:
                i = int(next_chunk) - 1
                if 0 <= i < len(chunks) and _nv_last["text"]:
                    c = chunks[i]
                    rec = {{"index": i, "chapter": c.get("chapter"), "part": c.get("part"),
                           "total_parts": c.get("total_parts"), "src": c.get("text", ""),
                           "dst": _nv_last["text"], "ts": _nv_time.time()}}
                    with open(_NV_SIDECAR, "a", encoding="utf-8") as _f:
                        _f.write(_nv_json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception as _e:
                print("[GUI] sidecar lỗi: %s" % _e, flush=True)
        finally:
            _nv_release()
        if _nv_os.path.exists(_NV_STOP):
            print("[GUI] STOP_REQUESTED", flush=True)
            raise SystemExit(0)
        if _nv_os.path.exists(_NV_PAUSE):
            print("[GUI] PAUSED", flush=True)
            while _nv_os.path.exists(_NV_PAUSE) and not _nv_os.path.exists(_NV_STOP):
                _nv_time.sleep(0.4)
            if _nv_os.path.exists(_NV_STOP):
                print("[GUI] STOP_REQUESTED", flush=True)
                raise SystemExit(0)
            print("[GUI] RESUMED", flush=True)
        return result
except NameError:
    pass
# ======== HẾT HOOK ========

'''


def _is_main_guard(node: ast.AST) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def build_runner_source(engine_source: str, overrides: dict[str, str], hook: str,
                        required: Iterable[str] = ("FILE_INPUT", "FILE_OUTPUT", "GLOSSARY_FILE")) -> tuple[str, list[str]]:
    """Vá bản sao engine: thay phép gán cấp module + chèn hook trước `if __name__ == "__main__"`.

    Trả về (mã nguồn, danh sách hằng số cấu hình không tìm thấy trong engine).
    """
    source = engine_source.replace("\r\n", "\n")
    tree = ast.parse(source)
    lines = source.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    spans: list[tuple[int, int, str]] = []
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in overrides and target.id not in found:
                spans.append((offsets[node.lineno - 1], offsets[node.end_lineno], f"{target.id} = {overrides[target.id]}\n"))
                found.add(target.id)
    missing_required = [n for n in required if n not in found]
    if missing_required:
        raise ApiError("ENGINE_INCOMPATIBLE",
                       "Engine thiếu biến cấu hình: " + ", ".join(missing_required)
                       + ". Có thể file engine đã đổi cấu trúc.")
    guard = next((n for n in tree.body if _is_main_guard(n)), None)
    if guard is None:
        raise ApiError("ENGINE_INCOMPATIBLE", "Engine không có khối if __name__ == '__main__'.")
    spans.append((offsets[guard.lineno - 1], offsets[guard.lineno - 1], hook))
    for start, end, replacement in sorted(spans, key=lambda s: (s[0], s[1]), reverse=True):
        source = source[:start] + replacement + source[end:]
    return source, sorted(set(overrides) - found)


def engine_overrides(project: Project) -> dict[str, str]:
    cfg, p = project.cfg, project.paths
    t, g, m, a = cfg["translate"], cfg["guard"], cfg["moegirl"], cfg["auto_discover"]
    use_project_cache = m["scope"] == "approved_works"
    terms_file = p.project_cache if use_project_cache else project.moegirl_terms_path()
    ov = {
        "FILE_INPUT": f"Path({str(p.source)!r})", "FILE_OUTPUT": f"Path({str(p.output)!r})",
        "GLOSSARY_FILE": repr(str(p.glossary)),
        "BASE_URL": repr(cfg["ollama"]["base_url"].rstrip("/") + "/v1"),
        "TARGET_LANGUAGE": repr(t["target_language"]),
        "MAX_CHARS": str(int(t["max_chars"])), "MAX_RETRIES_PER_CHUNK": str(int(t["max_retries"])),
        "MAX_INVALID_RETRIES": str(int(t["max_invalid_retries"])),
        "DELAY_BETWEEN_CALLS": repr(float(t["delay"])), "TEMPERATURE": repr(float(t["temperature"])),
        "TOP_P": repr(float(t["top_p"])),
        "MIN_SOURCE_LEN_CHECK": str(int(g["min_source_len"])), "MIN_RATIO": repr(float(g["min_length_ratio"])),
        "MAX_RATIO": repr(float(g["max_length_ratio"])),
        "WORKERS": str(int(t["workers"])), "RPM_LIMIT": str(int(t["rpm_limit"])),
        "TPM_LIMIT": str(int(t["tpm_limit"])), "CONTEXT_TOKENS": str(int(t["context_tokens"])),
        "MAX_BISECT_DEPTH": str(int(t["max_bisect_depth"])),
        "PRESERVE_INDENT": repr(bool(t["preserve_indent"])), "MAX_HAN_RATIO": repr(float(g["max_han_ratio"])),
        "AUTO_DISCOVER_TERMS": repr(bool(a["enabled"])), "AUTO_TERM_MIN_FREQUENCY": str(int(a["min_frequency"])),
        "AUTO_TERM_MAX_CANDIDATES": str(int(a["max_candidates"])),
        "MOEGIRL_ENABLED": repr(bool(m["enabled"])), "MOEGIRL_MAX_HINTS_PER_CHUNK": str(int(m["max_hints_per_chunk"])),
        "MOEGIRL_AUTO_IMPORT_DATASET": "False",  # backend quản lý kho; engine không tự nhập lại
        "MOEGIRL_AUTO_COMMIT_MIN_CONFIDENCE": "2.0",  # tắt tự ghi: chỉ người dùng duyệt ở màn hình D
        "MOEGIRL_TERMS_FILE": f"Path({str(terms_file)!r})",
        "RESTART_FRESH": "False",
    }
    if cfg["ollama"].get("model"):
        ov["MODEL"] = repr(cfg["ollama"]["model"])
    return ov


def _ollama_get(base_url: str, endpoint: str, timeout: float = 2.5) -> dict:
    request = urllib.request.Request(base_url.rstrip("/") + endpoint, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_status(base_url: str, model: str = "") -> dict:
    try:
        tags = _ollama_get(base_url, "/api/tags")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"connected": False, "error": str(exc), "models": [], "loaded": [], "model_present": False}
    models = [{"name": m.get("name", ""), "size": m.get("size", 0), "modified_at": m.get("modified_at", "")}
              for m in tags.get("models", [])]
    loaded = []
    try:
        for m in _ollama_get(base_url, "/api/ps").get("models", []):
            loaded.append({"name": m.get("name", ""), "size": m.get("size", 0),
                           "size_vram": m.get("size_vram", 0), "expires_at": m.get("expires_at", "")})
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        pass
    names = {m["name"] for m in models}
    present = bool(model) and (model in names or f"{model}:latest" in names)
    return {"connected": True, "error": "", "models": models, "loaded": loaded,
            "model_present": present, "model_loaded": any(l["name"] in (model, f"{model}:latest") for l in loaded)}


class EngineRunner:
    """Chạy engine bằng bản sao đã vá. Trạng thái: idle|starting|running|pausing|paused|stopping|finished|failed|stopped."""

    def __init__(self, project: Project, logs: LogBuffer) -> None:
        self.project = project
        self.logs = logs
        self.lock = threading.RLock()
        self.process: Optional[subprocess.Popen] = None
        self.runner_file: Optional[Path] = None
        self.state = "idle"
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.return_code: Optional[int] = None
        self.total_chunks = 0
        self.current: dict = {}
        self.completed: dict[int, float] = {}
        self.retries: Counter = Counter()
        self.failed: set[int] = set()
        self.guard_warnings = 0
        self.stage = ""
        self.stop_requested = False
        self._fresh_tokens: dict[str, float] = {}

    # ---- kiểm tra trước khi chạy ----
    def preflight(self, deepseek_check: Optional[Callable[[], dict]] = None) -> dict:
        """Chặn/cảnh báo trước khi dịch. Ở chế độ 'deepseek' bỏ qua engine script + Ollama,
        thay bằng kiểm tra API key (qua `deepseek_check`)."""
        project, cfg = self.project, self.project.cfg
        blockers, warnings, confirms = [], [], []
        mode = project.engine_mode()
        engine = project.engine_path()
        ds: dict = {}
        if mode == "deepseek":
            ds = (deepseek_check or (lambda: {}))()
            if not ds.get("keys"):
                blockers.append({"code": "NO_API_KEY",
                                 "message": "Chưa có API key DeepSeek: mở màn hình E để lưu key "
                                            "(hoặc đặt biến môi trường DEEPSEEK_API_KEYS)."})
            status = {"connected": False, "error": "bỏ qua ở chế độ DeepSeek", "models": [],
                      "loaded": [], "model_present": False, "skipped": True}
        else:
            if not engine.is_file():
                blockers.append({"code": "ENGINE_NOT_FOUND", "message": f"Không tìm thấy engine: {engine}"})
            status = ollama_status(cfg["ollama"]["base_url"], cfg["ollama"].get("model", ""))
            if not status["connected"]:
                blockers.append({"code": "OLLAMA_DOWN", "message": "Không kết nối được Ollama: " + status["error"]})
            elif cfg["ollama"].get("model") and not status["model_present"]:
                blockers.append({"code": "MODEL_MISSING",
                                 "message": f"Ollama chưa có model {cfg['ollama']['model']}."})
        info = project.source_info()
        if info["encoding"] not in ("utf-8-sig", "utf-8"):
            warnings.append({"code": "SOURCE_ENCODING",
                             "message": f"Nguồn là {info['encoding']}; backend sẽ đọc đúng, không cần đổi file."})
        if info["chapters"] == 0:
            blockers.append({"code": "NO_CHAPTERS", "message": "Không thấy tiêu đề chương bắt đầu bằng '##'."})
        if not project.paths.glossary.is_file():
            warnings.append({"code": "NO_GLOSSARY", "message": f"Chưa có {project.paths.glossary.name}; sẽ chạy không glossary."})
        out_exists = project.paths.output.is_file() and project.paths.output.stat().st_size > 0
        prog = project.progress_file()
        if out_exists and not prog and UNTRANSLATED_TAG in read_text_auto(project.paths.output):
            warnings.append({"code": "RETRY_FAILED_CHUNKS",
                             "message": "Output có chunk chưa dịch; engine sẽ ưu tiên dịch lại các chunk đó."})
        elif out_exists and not prog:
            confirms.append({"code": "OUTPUT_WITHOUT_PROGRESS",
                             "message": "Output đã có nhưng không có progress; cần chọn dịch lại từ đầu (có sao lưu)."})
        if prog and not out_exists:
            warnings.append({"code": "PROGRESS_WITHOUT_OUTPUT", "message": "Có progress nhưng thiếu output; engine sẽ bắt đầu lại."})
        if mode == "script" and prog.get("fingerprint") and engine.is_file():
            try:
                project.chunks()
                if getattr(project, "fingerprint", "") != prog["fingerprint"]:
                    confirms.append({"code": "FINGERPRINT_MISMATCH",
                                     "message": "Danh sách chunk khác lần chạy trước (đổi nguồn hoặc max_chars)."})
            except Exception as exc:  # engine không import được -> chỉ cảnh báo
                warnings.append({"code": "CHUNK_PREVIEW_FAILED", "message": f"Không tính được chunk: {exc}"})
        if cfg["moegirl"]["enabled"] and cfg["moegirl"]["scope"] == "approved_works" \
                and not project.paths.project_cache.is_file():
            warnings.append({"code": "NO_PROJECT_CACHE",
                             "message": "Chưa có kho Moegirl theo tác phẩm đã duyệt; sẽ tạo khi bắt đầu dịch."})
        return {"ready": not blockers, "blockers": blockers, "warnings": warnings,
                "needs_confirmation": confirms, "running": self.is_active(),
                "engine_mode": mode, "engine_label": ENGINE_MODE_LABELS.get(mode, mode),
                "deepseek": ds, "ollama": status}

    def is_active(self) -> bool:
        return self.state in ("starting", "running", "pausing", "paused", "stopping")

    # ---- điều khiển ----
    def start(self, on_fingerprint_mismatch: str = "stop", moegirl_refresh: Optional[Callable[[], Any]] = None) -> dict:
        with self.lock:
            if self.is_active():
                raise ApiError("ALREADY_RUNNING", "Engine đang chạy.")
        pre = self.preflight()
        if pre["blockers"]:
            raise ApiError("PREFLIGHT_FAILED", pre["blockers"][0]["message"], blockers=pre["blockers"])
        codes = {c["code"] for c in pre["needs_confirmation"]}
        if "OUTPUT_WITHOUT_PROGRESS" in codes:
            raise ApiError("NEED_CONFIRMATION", pre["needs_confirmation"][0]["message"],
                           needs_confirmation=pre["needs_confirmation"], action="restart_fresh")
        stdin_answer = None
        if "FINGERPRINT_MISMATCH" in codes:
            if on_fingerprint_mismatch not in ("continue", "fresh"):
                raise ApiError("NEED_CONFIRMATION", "Danh sách chunk khác lần chạy trước.",
                               needs_confirmation=pre["needs_confirmation"], action="fingerprint_choice",
                               options=["continue", "fresh"])
            stdin_answer = "y\n" if on_fingerprint_mismatch == "continue" else "n\n"
            if on_fingerprint_mismatch == "fresh":
                self._archive_outputs()
        if moegirl_refresh and self.project.cfg["moegirl"]["enabled"] \
                and self.project.cfg["moegirl"]["scope"] == "approved_works":
            try:
                moegirl_refresh()
            except ApiError as exc:
                self.logs.add(f"[GUI] Không tạo được kho Moegirl theo tác phẩm: {exc.message}", "WARNING")
        return self._launch(stdin_answer)

    def _archive_outputs(self) -> list[str]:
        moved = []
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        p = self.project.paths
        for path in (p.output, p.progress, p.error_log, p.sidecar):
            if path.is_file():
                target = path.with_name(f"{path.name}.bak_{stamp}")
                shutil.move(str(path), str(target))
                moved.append(str(target))
        return moved

    def restart_fresh(self, token: str = "") -> dict:
        with self.lock:
            if self.is_active():
                raise ApiError("ALREADY_RUNNING", "Hãy dừng engine trước khi dịch lại từ đầu.")
        p = self.project.paths
        targets = [str(x) for x in (p.output, p.progress, p.error_log, p.sidecar) if x.is_file()]
        if not token:
            new = uuid.uuid4().hex
            with self.lock:
                self._fresh_tokens = {t: ts for t, ts in self._fresh_tokens.items() if time.time() - ts < 300}
                self._fresh_tokens[new] = time.time()
            return {"needs_confirmation": True, "token": new,
                    "will_move_to_backup": targets,
                    "message": "Output/progress hiện tại sẽ được chuyển thành .bak_<thời gian> rồi dịch lại từ chunk #1."}
        with self.lock:
            created = self._fresh_tokens.pop(token, None)
        if created is None or time.time() - created > 300:
            raise ApiError("BAD_TOKEN", "Mã xác nhận không hợp lệ hoặc đã hết hạn.")
        moved = self._archive_outputs()
        self.logs.add(f"[GUI] Dịch lại từ đầu; đã sao lưu: {len(moved)} file.", "WARNING")
        started = self.start()
        started["archived"] = moved
        return started

    def _reset_state(self) -> None:
        self.state, self.started_at, self.finished_at = "starting", time.time(), None
        self.return_code, self.total_chunks, self.current = None, 0, {}
        self.completed, self.retries, self.failed = {}, Counter(), set()
        self.guard_warnings, self.stage, self.stop_requested = 0, "", False

    def _launch(self, stdin_answer: Optional[str]) -> dict:
        project, p = self.project, self.project.paths
        engine = project.engine_path()
        hook = HOOK_TEMPLATE.format(sidecar=str(p.sidecar), pause=str(p.pause_flag), stop=str(p.stop_flag), output=str(p.output))
        source, missing = build_runner_source(read_text_auto(engine), engine_overrides(project), hook)
        for flag in (p.pause_flag, p.stop_flag):
            flag.unlink(missing_ok=True)
        runner = engine.parent / f".__novel_runner_{os.getpid()}_{int(time.time() * 1000)}.py"
        runner.write_text(source, encoding="utf-8")
        with self.lock:
            self._reset_state()
            self.runner_file = runner
        p.runs_dir.mkdir(parents=True, exist_ok=True)
        self.logs.file_path = p.runs_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.logs.add(f"[GUI] Khởi động engine {engine.name} (bản sao đã vá: {runner.name})", "INFO")
        if missing:
            self.logs.add("[GUI] Engine không có các hằng số: " + ", ".join(missing) + " (bỏ qua).", "WARNING")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", str(runner)], cwd=str(engine.parent), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
        except OSError as exc:
            runner.unlink(missing_ok=True)
            with self.lock:
                self.state = "failed"
            raise ApiError("SPAWN_FAILED", f"Không chạy được engine: {exc}")
        with self.lock:
            self.process = process
        if process.stdin:
            try:
                if stdin_answer:
                    process.stdin.write(stdin_answer)
                process.stdin.close()  # engine không bao giờ treo chờ input() ngoài các câu đã trả lời
            except OSError:
                pass
        threading.Thread(target=self._pump, args=(process, runner), daemon=True, name="engine-pump").start()
        return {"pid": process.pid, "state": self.state, "output": str(p.output)}

    def _pump(self, process: subprocess.Popen, runner: Path) -> None:
        try:
            if process.stdout:
                for raw in process.stdout:
                    self._on_line(raw.rstrip("\n"))
            code = process.wait()
        except Exception as exc:  # pragma: no cover
            self.logs.add(f"[GUI] Lỗi đọc stdout: {exc}", "ERROR")
            code = -1
        finally:
            try:
                if process.stdout:
                    process.stdout.close()
            except OSError:
                pass
        with self.lock:
            self.return_code, self.finished_at, self.process = code, time.time(), None
            if self.stop_requested:
                self.state = "stopped"
            else:
                self.state = "finished" if code == 0 else "failed"
        self.project.paths.pause_flag.unlink(missing_ok=True)
        self.project.paths.stop_flag.unlink(missing_ok=True)
        try:
            runner.unlink(missing_ok=True)
        except OSError:
            pass
        self.logs.add(f"[GUI] Engine kết thúc, mã {code}.", "SUCCESS" if code == 0 else "ERROR")

    def _on_line(self, line: str) -> None:
        with self.lock:
            if line.startswith("[GUI] PAUSED"):
                self.state = "paused"
            elif line.startswith("[GUI] RESUMED"):
                self.state = "running"
            elif line.startswith("[GUI] STOP_REQUESTED"):
                self.stop_requested = True
            elif self.state == "starting":
                self.state = "running"
            m = re.match(r"^\[(\d)/6\]", line)
            if m:
                self.stage = m.group(1)
            m = RE_TOTAL.search(line)
            if m:
                self.total_chunks = int(m.group(1))
            m = RE_CHUNK.match(line)
            if m:
                chunk_no = int(m.group(1))
                self.total_chunks = int(m.group(2))
                self.current = {"index": chunk_no - 1, "number": chunk_no, "chapter": int(m.group(3)),
                                "part": int(m.group(4)), "total_parts": int(m.group(5)),
                                "chars": int(m.group(6)), "started_at": time.time(), "attempt": 0}
            m = RE_ATTEMPT.search(line)
            if m and self.current:
                self.current["attempt"] = int(m.group(1))
            for rx in (RE_INVALID, RE_ERROR):
                m = rx.search(line)
                if m:
                    number = int(m.group(1) if rx is RE_INVALID else m.group(2))
                    self.retries[number - 1] += 1
            m = RE_SAVED.match(line)
            if m:
                number = int(m.group(1))
                self.completed[number - 1] = float(m.group(2))
            if RE_FAILED.search(line) and self.current:
                self.failed.add(self.current["index"])
            if RE_GUARD.search(line):
                self.guard_warnings += 1
        extra = {"chunk": self.current.get("number")} if self.current else {}
        self.logs.add(line, **extra)

    def pause(self) -> dict:
        with self.lock:
            if self.state not in ("running", "starting"):
                raise ApiError("NOT_RUNNING", "Engine không ở trạng thái đang chạy.")
            self.project.paths.pause_flag.write_text(now_iso(), encoding="utf-8")
            self.state = "pausing"
        self.logs.add("[GUI] Yêu cầu tạm dừng: sẽ dừng sau khi lưu xong chunk hiện tại.", "WARNING")
        return {"state": "pausing"}

    def resume(self) -> dict:
        with self.lock:
            if self.state not in ("paused", "pausing"):
                raise ApiError("NOT_PAUSED", "Engine không ở trạng thái tạm dừng.")
            self.project.paths.pause_flag.unlink(missing_ok=True)
            self.state = "running"
        self.logs.add("[GUI] Tiếp tục dịch.", "INFO")
        return {"state": "running"}

    def stop(self, graceful: bool = False) -> dict:
        with self.lock:
            process = self.process
            if not self.is_active() or process is None:
                raise ApiError("NOT_RUNNING", "Không có tiến trình đang chạy.")
            self.stop_requested = True
            self.state = "stopping"
            if graceful:
                self.project.paths.stop_flag.write_text(now_iso(), encoding="utf-8")
        if graceful:
            self.logs.add("[GUI] Sẽ dừng sau khi lưu xong chunk hiện tại.", "WARNING")
        else:
            self.logs.add("[GUI] Dừng khẩn cấp: kết thúc tiến trình engine.", "WARNING")
            process.terminate()
            threading.Thread(target=self._kill_later, args=(process,), daemon=True).start()
        return {"state": "stopping", "graceful": graceful}

    @staticmethod
    def _kill_later(process: subprocess.Popen, wait: float = 6.0) -> None:
        try:
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            process.kill()

    def shutdown(self) -> None:
        process = self.process
        if process and process.poll() is None:
            process.terminate()


# ============================================================
# 11. TIẾN TRÌNH / HEATMAP / CHI TIẾT CHUNK (màn hình F)
# ============================================================

def read_sidecar(path: Path) -> dict[int, dict]:
    records: dict[int, dict] = {}
    if not path.is_file():
        return records
    try:
        for raw in read_text_auto(path).split("\n"):
            if raw.strip():
                try:
                    rec = json.loads(raw)
                    records[int(rec["index"])] = rec
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return records


class ProgressService:
    def __init__(self, project: Project, runner: EngineRunner, guard: "GuardService") -> None:
        self.project, self.runner, self.guard = project, runner, guard

    def _totals(self) -> tuple[int, int]:
        """(tổng chunk, số chunk đã xong)."""
        r = self.runner
        prog = self.project.progress_file()
        done = int(prog.get("next_chunk", 0)) if prog else 0
        total = r.total_chunks
        if not total:
            try:
                total = len(self.project.chunks())
            except Exception:
                total = 0
        if r.completed:
            done = max(done, max(r.completed) + 1)
        if not prog and total:  # engine xóa progress khi hoàn tất -> suy ra từ checkpoint
            sidecar = read_sidecar(self.project.paths.sidecar)
            if sidecar:
                done = max(done, max(sidecar) + 1)
            if r.state == "finished" and not r.failed:
                done = total
        return total, min(done, total) if total else done

    def status(self) -> dict:
        r, project = self.runner, self.project
        total, done = self._totals()
        sidecar = read_sidecar(project.paths.sidecar)
        stamps = sorted(rec["ts"] for rec in sidecar.values() if "ts" in rec)
        deltas = [b - a for a, b in zip(stamps, stamps[1:]) if 0 < b - a < 3600][-20:]
        avg = (sum(r.completed.values()) / len(r.completed)) if r.completed else \
              (sum(deltas) / len(deltas) if deltas else None)
        remaining = max(0, total - done)
        elapsed = (time.time() - r.started_at) if r.started_at and r.is_active() else \
                  ((r.finished_at - r.started_at) if r.started_at and r.finished_at else 0)
        last = sidecar.get(max(sidecar)) if sidecar else None
        cps = None
        if len(stamps) >= 2 and sidecar:
            recent = sorted(sidecar.values(), key=lambda x: x["ts"])[-10:]
            span = recent[-1]["ts"] - recent[0]["ts"]
            if span > 0 and len(recent) > 1:
                cps = round(sum(len(x["dst"]) for x in recent[1:]) / span, 1)
        out_size = project.paths.output.stat().st_size if project.paths.output.is_file() else 0
        chapters_done = None
        try:
            chunk_list = project.chunks()
            if done and chunk_list:
                chapters_done = chunk_list[min(done, len(chunk_list)) - 1]["chapter"]
        except Exception:
            pass
        return {
            "state": r.state, "pid": r.process.pid if r.process else None, "stage": r.stage,
            "total_chunks": total, "done_chunks": done, "remaining_chunks": remaining,
            "percent": round(100 * done / total, 2) if total else 0.0,
            "current": dict(r.current), "failed_chunks": sorted(i + 1 for i in r.failed),
            "retried_chunks": {str(k + 1): v for k, v in sorted(r.retries.items())},
            "guard_warnings": r.guard_warnings,
            "avg_seconds_per_chunk": round(avg, 2) if avg else None,
            "eta_seconds": int(avg * remaining) if avg and r.is_active() else None,
            "chars_per_second": cps,
            "elapsed_seconds": int(elapsed), "return_code": r.return_code,
            "chapters_done": chapters_done,
            "output_bytes": out_size, "output": str(project.paths.output),
            "progress_file": str(project.paths.progress),
            "last_chunk_chars": len(last["dst"]) if last else None,
            "tokens_per_second": None,  # engine không công bố số token; chỉ có chars_per_second
        }

    def heatmap(self, around: Optional[int] = None, window: int = 46) -> dict:
        r = self.runner
        total, done = self._totals()
        center = (around if around is not None else (r.current.get("index", done))) or 0
        start = max(0, min(center - window // 2, max(0, total - window)))
        end = min(total, start + window)
        sidecar = read_sidecar(self.project.paths.sidecar)
        cells = []
        for i in range(start, end):
            if i == r.current.get("index") and r.is_active() and i not in r.completed:
                st = "current"
            elif i in r.failed or (i in sidecar and sidecar[i]["dst"].startswith(UNTRANSLATED_TAG)):
                st = "failed"
            elif i < done:
                st = "retried" if r.retries.get(i) else "done"
            else:
                st = "pending"
            cells.append({"index": i, "number": i + 1, "status": st, "retries": r.retries.get(i, 0)})
        summary = Counter()
        for i in range(total):
            if i in r.failed or (i in sidecar and sidecar[i]["dst"].startswith(UNTRANSLATED_TAG)):
                summary["failed"] += 1
            elif i < done:
                summary["retried" if r.retries.get(i) else "done"] += 1
            elif i == r.current.get("index") and r.is_active():
                summary["current"] += 1
            else:
                summary["pending"] += 1
        return {"window": {"from": start + 1, "to": end}, "cells": cells, "total": total,
                "summary": {k: summary.get(k, 0) for k in ("done", "retried", "failed", "current", "pending")}}

    def chunk(self, number: int) -> dict:
        chunks = self.project.chunks()
        if not (1 <= number <= len(chunks)):
            raise ApiError("OUT_OF_RANGE", f"Chunk {number} không tồn tại (1..{len(chunks)}).")
        c = chunks[number - 1]
        sidecar = read_sidecar(self.project.paths.sidecar)
        rec = sidecar.get(number - 1)
        text = c["text"]
        terms = [{"src": row["src"], "dst": row["dst"], "mode": row["effective_mode"]}
                 for row in self.project.glossary.read()["rows"] if row["src"] in text]
        detail = {"number": number, "chapter": c["chapter"], "part": c["part"], "total_parts": c["total_parts"],
                  "source": text, "source_chars": len(text), "han_chars": len(HAN_RE.findall(text)),
                  "terms": terms, "translation": rec["dst"] if rec else None,
                  "translated_chars": len(rec["dst"]) if rec else 0,
                  "translated_at": datetime.fromtimestamp(rec["ts"]).isoformat(timespec="seconds") if rec else None,
                  "untranslated": bool(rec and rec["dst"].startswith(UNTRANSLATED_TAG)),
                  "retries": self.runner.retries.get(number - 1, 0)}
        if rec and not detail["untranslated"]:
            detail["quality"] = self.guard.inspect_pair(text, rec["dst"], terms)
        return detail

    def update_chunk(self, number: int, text: str) -> dict:
        """Ghi đè bản dịch thủ công cho một chunk: cập nhật sidecar + flatten vào output (bandich.txt)."""
        chunks = self.project.chunks()
        if not (1 <= number <= len(chunks)):
            raise ApiError("OUT_OF_RANGE", f"Chunk {number} không tồn tại (1..{len(chunks)}).")
        text = (text or "").strip()
        if not text:
            raise ApiError("MISSING_FIELD", "Nội dung bản dịch không được để trống.")
        idx = number - 1
        out = self.project.paths.output
        backup_path: Optional[Path] = None
        with file_lock(out):
            records = read_sidecar(self.project.paths.sidecar)
            if idx not in records:
                raise ApiError("NOT_TRANSLATED", f"Chunk {number} chưa được dịch; chưa thể sửa thủ công.")
            rec = dict(records[idx])
            rec["dst"] = text
            rec["ts"] = time.time()
            records[idx] = rec
            # ghi sidecar (đã cầm khóa output.lock nên dùng bản không khóa để tránh deadlock)
            _atomic_replace(self.project.paths.sidecar,
                            "\n".join(json.dumps(records[i], ensure_ascii=False) for i in sorted(records)) + "\n")
            # flatten toàn bộ bản dịch đã có vào output (khớp định dạng "\n\n" của engine append_output)
            if out.is_file():
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = out.with_name(f"{out.name}.bak_{stamp}")
                shutil.copy2(out, backup_path)
            _atomic_replace(out, "\n\n".join(records[i]["dst"] for i in sorted(records)))
        return {"number": number, "path": str(out),
                "translated_chars": len(text), "backup": str(backup_path) if backup_path else None}


# ============================================================
# 12. KIỂM TRA BẢN DỊCH (màn hình G) - dùng translation_guard
# ============================================================

CRITICAL_CODES = {"PROMPT_LEAK", "GLOSSARY_LEAK", "UNTRANSLATED"}
WARNING_CODES = {"HAN_REMAINS", "HAN_RUN", "FOREIGN_SCRIPT", "FORMAT_JUNK"}
LOOP_CODES = {"REPEAT"}


class GuardService:
    def __init__(self, project: Project) -> None:
        self.project = project
        self._module: Any = None

    def module(self) -> Any:
        if self._module is not None:
            return self._module
        engine_dir = self.project.engine_path().parent
        candidates = [engine_dir / "translation_guard.py", Path(__file__).resolve().with_name("translation_guard.py")]
        for path in candidates:
            if path.is_file():
                mod = load_module_from(path, "nv_translation_guard")
                if hasattr(mod, "scan_text") and hasattr(mod, "TranslationGuard"):
                    self._module = mod
                    return mod
        adapter = sys.modules.get("nv_engine_adapter")
        if adapter is None and self.project.engine_path().is_file():
            adapter = load_module_from(self.project.engine_path(), "nv_engine_adapter")
        if adapter is not None and hasattr(adapter, "scan_text") and hasattr(adapter, "TranslationGuard"):
            self._module = adapter
            return adapter
        raise ApiError("NO_GUARD", "Không tìm thấy translation_guard.py (v2) cạnh engine.")

    def _guard(self) -> Any:
        g = self.project.cfg["guard"]
        return self.module().TranslationGuard(
            min_source_len=int(g["min_source_len"]), min_length_ratio=float(g["min_length_ratio"]),
            max_length_ratio=float(g["max_length_ratio"]), max_han_ratio=float(g["max_han_ratio"]),
            strict=bool(g["strict"]))

    def inspect_pair(self, source: str, translation: str, terms: Optional[list[dict]] = None) -> dict:
        try:
            issues = self._guard().inspect(source, translation)
        except ApiError:
            return {"available": False}
        forced = [t for t in (terms or []) if t.get("mode") == "force"]
        ok_terms = sum(1 for t in forced if nfc(t["dst"]).casefold() in nfc(translation).casefold())
        han = len(HAN_RE.findall(translation))
        return {
            "available": True,
            "issues": [{"code": i.code, "message": i.message, "severity": i.severity} for i in issues],
            "hard": sum(1 for i in issues if i.severity == "hard"),
            "soft": sum(1 for i in issues if i.severity == "soft"),
            "passed": not any(i.severity == "hard" for i in issues),
            "glossary_strict_pct": round(100 * ok_terms / len(forced), 1) if forced else None,
            "han_chars": han,
        }

    def run(self, check_han: Optional[bool] = None) -> dict:
        p = self.project.paths
        if not p.output.is_file():
            raise ApiError("NO_OUTPUT", f"Chưa có file bản dịch: {p.output}")
        text = read_text_auto(p.output)
        g = self.project.cfg["guard"]
        hits = self.module().scan_text(text, check_han=True if check_han is None else bool(check_han))
        counts = Counter(code for _, code, _ in hits)
        lines = sum(1 for ln in text.split("\n") if ln.strip())
        flagged = len({n for n, _, _ in hits})
        critical = sum(counts[c] for c in CRITICAL_CODES)
        warnings = sum(counts[c] for c in WARNING_CODES)
        loops = sum(counts[c] for c in LOOP_CODES)
        status = "fail" if critical else ("warn" if warnings or loops else "pass")
        return {
            "ran_at": now_iso(), "file": str(p.output), "lines": lines, "flagged_lines": flagged,
            "cleanliness_pct": round(100.0 * (1 - flagged / lines), 1) if lines else 100.0,
            "status": status, "counts": dict(counts),
            "summary": {"critical": critical, "warnings": warnings, "loops": loops},
            "hits": [{"line": n, "code": c, "text": t[:300]} for n, c, t in hits[:500]],
            "hits_truncated": len(hits) > 500,
            "error_log": {"path": str(p.error_log), "bytes": p.error_log.stat().st_size if p.error_log.is_file() else 0},
            "progress_file": str(p.progress), "config": dict(g),
        }


# ============================================================
# 13. HỆ THỐNG (CPU/RAM/GPU) - thanh trạng thái chân trang
# ============================================================

_STATS_CACHE: dict[str, Any] = {"t": 0.0, "v": None}


def system_stats(max_age: float = 2.0) -> dict:
    now = time.time()
    if _STATS_CACHE["v"] and now - _STATS_CACHE["t"] < max_age:
        return _STATS_CACHE["v"]
    stats: dict[str, Any] = {"cpu_percent": None, "ram_used_gb": None, "ram_total_gb": None, "gpu": None}
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        stats.update(cpu_percent=psutil.cpu_percent(interval=None),
                     ram_used_gb=round(vm.used / 2**30, 1), ram_total_gb=round(vm.total / 2**30, 1))
    except ImportError:
        try:
            if sys.platform.startswith("linux"):
                info = {k: int(v.split()[0]) for k, v in
                        (ln.split(":", 1) for ln in Path("/proc/meminfo").read_text().splitlines() if ":" in ln)}
                total, avail = info["MemTotal"] * 1024, info["MemAvailable"] * 1024
                stats.update(ram_used_gb=round((total - avail) / 2**30, 1), ram_total_gb=round(total / 2**30, 1))
            elif sys.platform.startswith("win"):
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                                ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
                m = MEMORYSTATUSEX()
                m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
                stats.update(ram_used_gb=round((m.ullTotalPhys - m.ullAvailPhys) / 2**30, 1),
                             ram_total_gb=round(m.ullTotalPhys / 2**30, 1))
        except Exception:
            pass
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2.5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0)
            if out.returncode == 0 and out.stdout.strip():
                name, used, total, util, temp = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
                stats["gpu"] = {"name": name, "vram_used_gb": round(float(used) / 1024, 1),
                                "vram_total_gb": round(float(total) / 1024, 1),
                                "utilization_percent": float(util), "temperature_c": float(temp)}
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    _STATS_CACHE.update(t=now, v=stats)
    return stats


# ============================================================
# 14. NOVELBACKEND: bảng điều phối cho 7 màn hình
# ============================================================

class NovelBackend:
    """Điểm vào duy nhất. `dispatch("anchors.list", {...})` trả về dict có khóa "ok"."""

    def __init__(self, default_engine: Path | str = "", app_dir: Path | str | None = None) -> None:
        self.default_engine = str(default_engine or "")
        self.app_dir = Path(app_dir) if app_dir else Path(__file__).resolve().parent
        self.state_file = self.app_dir / "novel_translator_app.json"
        self.logs = LogBuffer()
        self.token = uuid.uuid4().hex
        self.project: Optional[Project] = None
        self.runner: Optional[EngineRunner] = None
        self.detector: Optional[WorkDetector] = None
        self.moegirl: Optional[MoegirlService] = None
        self.guard: Optional[GuardService] = None
        self.progress: Optional[ProgressService] = None
        self.lock = threading.RLock()
        self.closing = False
        self.deepseek_mgr: Any = None
        self.methods: dict[str, Callable[..., dict]] = {}
        self._register()

    # ---- trạng thái ứng dụng ----
    def _app_state(self) -> dict:
        try:
            data = json.loads(read_text_auto(self.state_file))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, ValueError):
            return {}

    def _remember(self, source: str) -> None:
        data = self._app_state()
        recent = [x for x in data.get("recent", []) if x != source]
        data["recent"] = ([source] + recent)[:10]
        data["last_source"] = source
        try:
            atomic_write(self.state_file, json.dumps(data, ensure_ascii=False, indent=2))
        except (OSError, ApiError):
            pass

    def _p(self) -> Project:
        if self.project is None:
            raise ApiError("NO_PROJECT", "Chưa chọn file truyện nguồn.")
        return self.project

    def _ensure_idle(self) -> None:
        if self.runner and self.runner.is_active():
            raise ApiError("RUN_ACTIVE", "Không thể đổi dữ liệu khi engine đang chạy.")

    def xungho_read(self) -> dict:
        import xungho_ui
        with self.lock:
            p = self._p().paths
            return ok(source=str(p.source), **xungho_ui.read(p.dir, p.glossary))

    def xungho_review(self, source: str = "") -> dict:
        import xungho_ui
        with self.lock:
            p = self._p().paths
            if source != str(p.source):
                raise ApiError("STALE_PROJECT", "Dự án đã thay đổi; hãy tải lại trang.")
            return ok(**xungho_ui.review(p.dir, p.glossary))

    def xungho_analyze(self, source: str, report: str) -> dict:
        import xungho_ui
        with self.lock:
            self._ensure_idle()
            p = self._p().paths
            if source != str(p.source):
                raise ApiError("STALE_PROJECT", "Dự án đã thay đổi; hãy tải lại trang.")
            result = xungho_ui.analyze(p.dir, p.glossary, report, self.app_dir / "moegirl", p.approved)
            return ok(**result)

    def xungho_decision(self, source: str, revision: str, name: str, choice: str) -> dict:
        import xungho_ui
        with self.lock:
            self._ensure_idle()
            p = self._p().paths
            if source != str(p.source):
                raise ApiError("STALE_PROJECT", "Dự án đã thay đổi; hãy tải lại trang.")
            if revision != xungho_ui._revision(p.dir, p.glossary):
                raise ApiError("STALE_DATA", "Dữ liệu đã thay đổi; hãy tải lại.")
            xungho_ui.decision(p.dir, name, choice)
            return ok(**xungho_ui.review(p.dir, p.glossary))

    def xungho_roles(self, source: str, revision: str, kind: str, a: str, b: str,
                     a_role: str, b_role: str) -> dict:
        import xungho_ui
        with self.lock:
            self._ensure_idle()
            p = self._p().paths
            if source != str(p.source):
                raise ApiError("STALE_PROJECT", "Dự án đã thay đổi; hãy tải lại trang.")
            if revision != xungho_ui._revision(p.dir, p.glossary):
                raise ApiError("STALE_DATA", "Dữ liệu đã thay đổi; hãy tải lại.")
            xungho_ui.save_roles(p.dir, kind, a, b, a_role, b_role)
            current = xungho_ui.read(p.dir, p.glossary)
            return ok(summary=current["summary"], revision=current["revision"], warnings=current["warnings"])

    def xungho_approve_character(self, source: str, revision: str, key: str,
                                  destination: str, gender: str, sources: list) -> dict:
        import xungho_ui
        with self.lock:
            self._ensure_idle()
            project = self._p()
            p = project.paths
            if source != str(p.source):
                raise ApiError("STALE_PROJECT", "Dự án đã thay đổi; hãy tải lại trang.")
            if revision != xungho_ui._revision(p.dir, p.glossary):
                raise ApiError("STALE_DATA", "Dữ liệu đã thay đổi; hãy tải lại.")
            result = xungho_ui.approve_character(p.dir, p.glossary, key, destination, gender, sources,
                                                  parse_glossary, project.glossary.save)
            return ok(saved=result, review=xungho_ui.review(p.dir, p.glossary))

    def xungho_generate(self, source: str) -> dict:
        import xungho_ui
        with self.lock:
            self._ensure_idle()
            p = self._p().paths
            if source != str(p.source):
                raise ApiError("STALE_PROJECT", "Dự án đã thay đổi; hãy tải lại trang.")
            rows, waiting = xungho_ui.generate(p.dir, p.glossary)
            return ok(rows=rows, waiting=waiting,
                      data={"source": str(p.source), **xungho_ui.read(p.dir, p.glossary)})

    def xungho_save(self, source: str, revision: str, updates: list) -> dict:
        import xungho_ui
        with self.lock:
            self._ensure_idle()
            p = self._p().paths
            if source != str(p.source):
                raise ApiError("STALE_PROJECT", "Dự án đã thay đổi; hãy tải lại trang.")
            current = xungho_ui.read(p.dir, p.glossary)
            if revision != current["revision"]:
                raise ApiError("STALE_DATA", "Dữ liệu đã thay đổi bên ngoài; hãy tải lại trước khi lưu.")
            if not isinstance(updates, list):
                raise ApiError("BAD_PARAMS", "Danh sách thay đổi không hợp lệ.")
            keys = {row["key"] for row in current["rows"]}
            changes = {}
            for item in updates:
                if not isinstance(item, dict) or item.get("key") not in keys or item["key"] in changes:
                    raise ApiError("BAD_PARAMS", "Dòng xưng hô không hợp lệ hoặc bị trùng.")
                values = {}
                for field, column in (("self", "Tự xưng"), ("you", "Gọi người nghe là"),
                                      ("name", "Ghi chú của tôi")):
                    value = item.get(field)
                    if not isinstance(value, str) or "\n" in value or "\r" in value:
                        raise ApiError("BAD_PARAMS", f"Giá trị {field} phải là chuỗi một dòng.")
                    values[column] = value.strip()
                changes[item["key"]] = values
            xungho_ui.generate(p.dir, p.glossary, changes)
            return ok(data={"source": str(p.source), **xungho_ui.read(p.dir, p.glossary)})

    # ---- điều phối ----
    def dispatch(self, method: str, params: Optional[dict] = None) -> dict:
        func = self.methods.get(method)
        if func is None:
            return {"ok": False, "error": {"code": "UNKNOWN_METHOD", "message": f"Không có phương thức {method}."}}
        try:
            result = func(**(params or {}))
            return result if isinstance(result, dict) and "ok" in result else ok(**(result or {}))
        except ApiError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message, **exc.details}}
        except ModuleNotFoundError as exc:
            if method.startswith("xungho.") and exc.name == "openpyxl":
                message = (f"Thiếu openpyxl trong Python đang chạy ứng dụng: {sys.executable}. "
                           f'Cài bằng PowerShell: & "{sys.executable}" -m pip install openpyxl '
                           "rồi khởi động lại ứng dụng.")
                self.logs.add(f"[GUI] {message}", "ERROR")
                return {"ok": False, "error": {"code": "MISSING_DEPENDENCY", "message": message}}
            import traceback
            trace = traceback.format_exc()
            self.logs.add(f"[GUI] Lỗi nội bộ trong {method}: {exc}", "ERROR")
            return {"ok": False, "error": {"code": "INTERNAL", "message": str(exc), "trace": trace[-1500:]}}
        except TypeError as exc:
            return {"ok": False, "error": {"code": "BAD_PARAMS", "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            import traceback
            trace = traceback.format_exc()
            self.logs.add(f"[GUI] Lỗi nội bộ trong {method}: {exc}", "ERROR")
            return {"ok": False, "error": {"code": "INTERNAL", "message": str(exc), "trace": trace[-1500:]}}

    # ---- dự án / cấu hình (A) ----
    def project_open(self, source: str, engine: str = "") -> dict:
        self._ensure_idle()
        project = Project(source, engine or self.default_engine)
        if engine:
            project.cfg["engine"] = engine
        with self.lock:
            self.project = project
            self.runner = EngineRunner(project, self.logs)
            self.detector = WorkDetector(project)
            self.moegirl = MoegirlService(project)
            self.guard = GuardService(project)
            self.progress = ProgressService(project, self.runner, self.guard)
        self._remember(str(project.paths.source))
        return self.project_info()

    def project_info(self) -> dict:
        p = self._p()
        pa = p.paths
        return {
            "source": p.source_info(), "output": str(pa.output), "output_dir": str(pa.dir),
            "files": {name: {"path": str(path), "exists": path.is_file(),
                             "bytes": path.stat().st_size if path.is_file() else 0}
                      for name, path in (("glossary", pa.glossary), ("auto_glossary", pa.auto_glossary),
                                         ("anchors", pa.anchors), ("approved", pa.approved),
                                         ("suggestions", pa.suggestions), ("config", pa.config),
                                         ("progress", pa.progress), ("error_log", pa.error_log),
                                         ("failed_chunks", pa.failed))},
            "config": p.cfg, "config_loaded": p.config_loaded,
            "estimate": self.estimate(), "gated": self.gating(),
        }

    def gating(self) -> dict:
        """Tính năng nào mở/khóa tùy việc đã có nguồn/anchors hay chưa (như ghi chú ở màn hình A)."""
        has_project = self.project is not None
        approved = bool(has_project and self.project.approvals.approved_ids())
        return {"works_report": has_project, "anchors": has_project,
                "moegirl_suggestions": has_project and approved, "glossary": has_project,
                "translate": has_project, "logs": True}

    def estimate(self) -> dict:
        p = self._p()
        try:
            total = len(p.chunks())
        except Exception:
            return {"available": False}
        sidecar = read_sidecar(p.paths.sidecar)
        stamps = sorted(r["ts"] for r in sidecar.values() if "ts" in r)
        deltas = [b - a for a, b in zip(stamps, stamps[1:]) if 0 < b - a < 3600]
        avg = sum(deltas[-50:]) / len(deltas[-50:]) if deltas else None
        done = int(p.progress_file().get("next_chunk", 0)) if p.progress_file() else 0
        basis = "measured" if avg else "default"
        avg = avg or 15.0
        return {"available": True, "total_chunks": total, "done_chunks": done,
                "seconds_per_chunk": round(avg, 1), "basis": basis,
                "remaining_seconds": int(avg * max(0, total - done))}

    def config_get(self) -> dict:
        p = self._p()
        return {"config": p.cfg, "path": str(p.paths.config), "exists": p.paths.config.is_file(),
                "defaults": DEFAULT_CONFIG, "rules": {k: list(v[1:]) for k, v in CONFIG_RULES.items()}}

    def config_save(self, patch: dict, persist: bool = True) -> dict:
        p = self._p()
        self._ensure_idle()
        p.update_config(patch)
        saved = p.save_config() if persist else None
        return {"config": p.cfg, "saved": saved}

    def config_reset(self) -> dict:
        p = self._p()
        self._ensure_idle()
        keep = {"source": p.cfg["source"], "engine": p.cfg.get("engine", "")}
        p.cfg = deep_merge(DEFAULT_CONFIG, keep)
        p.update_config({})
        return {"config": p.cfg}

    def config_export(self, path: str = "") -> dict:
        p = self._p()
        text = json.dumps(p.cfg, ensure_ascii=False, indent=2) + "\n"
        if path:
            atomic_write(Path(path), text)
            return {"path": path}
        return {"text": text, "md5": hashlib.md5(text.encode("utf-8")).hexdigest()}

    def engine_set_mode(self, mode: str, engine: str = "") -> dict:
        """Chọn engine dịch cho dự án (màn hình E): 'deepseek' hoặc 'script'.

        - 'script': chạy dichTrungsangViet.py qua EngineRunner; nếu chưa có đường dẫn engine
          thì tự điền dichTrungsangViet.py cạnh app (khắc phục lỗi ENGINE_NOT_FOUND).
        - 'deepseek': dịch bằng deepseek_service.py (gọi API, không cần Ollama).
        Lựa chọn được ghi ngay vào config.json của dự án.
        """
        p = self._p()
        self._ensure_idle()
        mode = (mode or "").strip().lower()
        if mode not in ENGINE_MODES:
            raise ApiError("BAD_ENGINE_MODE", "Engine phải là 'deepseek' hoặc 'script'.")
        patch: dict[str, Any] = {"engine_mode": mode}
        if (engine or "").strip():
            patch["engine"] = str(self._ds_resolve(engine.strip(), p.paths.dir))
        merged = deep_merge(p.cfg, patch)
        if mode == "script" and not str(merged.get("engine") or "").strip():
            fallback = PROGRAM_DIR / "dichTrungsangViet.py"
            if fallback.is_file():
                patch["engine"] = str(fallback)
        p.update_config(patch)
        saved = p.save_config()
        self.logs.add("[GUI] Engine dịch: " + ENGINE_MODE_LABELS.get(mode, mode)
                      + (f" ({p.cfg.get('engine')})" if mode == "script" else ""), "INFO")
        return {"mode": mode, "engine": p.cfg.get("engine", ""), "saved": saved,
                "config": p.cfg, "info": self.project_info()}

    def ollama_status(self) -> dict:
        p = self._p()
        return ollama_status(p.cfg["ollama"]["base_url"], p.cfg["ollama"].get("model", ""))

    def system_open(self, kind: str) -> dict:
        p = self._p().paths
        targets = {"output": p.output, "output_dir": p.dir, "error_log": p.error_log, "progress": p.progress,
               "failed_chunks": p.failed,
                   "glossary": p.glossary, "anchors": p.anchors, "config": p.config,
                   "suggestions": p.suggestions, "approved": p.approved}
        target = targets.get(kind)
        if target is None:
            raise ApiError("BAD_KIND", f"Không mở được loại '{kind}'.")
        if not target.exists():
            raise ApiError("NOT_FOUND", f"Chưa có: {target}")
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            raise ApiError("OPEN_FAILED", str(exc))
        return {"opened": str(target)}

    def files_read_aux(self, kind: str, tail_bytes: int = 200_000) -> dict:
        p = self._p().paths
        files = {"error_log": p.error_log, "progress": p.progress, "config": p.config,
                 "run_log": self.logs.file_path or p.runs_dir / "none.log"}
        target = files.get(kind)
        if target is None:
            raise ApiError("BAD_KIND", f"Không đọc được loại '{kind}'.")
        if not target.is_file():
            return {"path": str(target), "exists": False, "text": ""}
        data = target.read_bytes()
        truncated = len(data) > tail_bytes
        text, _ = decode_bytes(data[-tail_bytes:])
        return {"path": str(target), "exists": True, "bytes": len(data), "truncated": truncated, "text": text}

    # ---- dịch bằng DeepSeek (trang A) ----
    def _ds(self):
        """Nạp deepseek_service.py (cùng thư mục) một lần."""
        if self.deepseek_mgr is None:
            if str(PROGRAM_DIR) not in sys.path:
                sys.path.insert(0, str(PROGRAM_DIR))
            try:
                import deepseek_service
            except ImportError as exc:
                raise ApiError("NO_DEEPSEEK_SERVICE", f"Thiếu deepseek_service.py: {exc}")
            self._ds_mod = deepseek_service
            self.deepseek_mgr = deepseek_service.DeepSeekManager(lambda text, level="INFO": self.logs.add(text, level))
        return self.deepseek_mgr

    def _ds_settings(self) -> dict:
        data = self._app_state().get("deepseek")
        return data if isinstance(data, dict) else {}

    def _ds_keys(self) -> list:
        self._ds()
        keys = self._ds_mod.parse_keys(self._ds_settings().get("api_keys") or [])
        return keys or self._ds_mod.parse_keys(os.environ.get("DEEPSEEK_API_KEYS", ""))

    def deepseek_settings_get(self) -> dict:
        self._ds()
        st = self._ds_settings()
        keys = self._ds_keys()
        return {"keys": [self._ds_mod.mask_key(k) for k in keys], "key_count": len(keys),
                "from_env": not self._ds_settings().get("api_keys") and bool(keys),
                "model": st.get("model") or self._ds_mod.DEFAULTS["model"],
                "base_url": st.get("base_url") or self._ds_mod.DEFAULTS["base_url"]}

    def _deepseek_readiness(self) -> dict:
        """Tóm tắt cấu hình DeepSeek cho preflight (không lộ key)."""
        self._ds()
        st = self._ds_settings()
        return {"keys": len(self._ds_keys()),
                "model": st.get("model") or self._ds_mod.DEFAULTS["model"],
                "base_url": st.get("base_url") or self._ds_mod.DEFAULTS["base_url"]}

    def deepseek_settings_save(self, api_keys: Optional[str] = None, model: str = "", base_url: str = "") -> dict:
        self._ds()
        data = self._app_state()
        st = dict(data.get("deepseek") or {})
        if api_keys is not None and str(api_keys).strip():  # để trống = giữ key cũ
            st["api_keys"] = self._ds_mod.parse_keys(api_keys)
        if model.strip():
            st["model"] = model.strip()
        if base_url.strip():
            st["base_url"] = base_url.strip()
        data["deepseek"] = st
        atomic_write(self.state_file, json.dumps(data, ensure_ascii=False, indent=2))
        return self.deepseek_settings_get()

    def deepseek_settings_clear_keys(self) -> dict:
        self._ds()
        data = self._app_state()
        (data.get("deepseek") or {}).pop("api_keys", None)
        atomic_write(self.state_file, json.dumps(data, ensure_ascii=False, indent=2))
        return self.deepseek_settings_get()

    def _ds_resolve(self, value: str, base: Optional[Path]) -> Path:
        path = Path(str(value).strip().strip('"')).expanduser()
        if not path.is_absolute() and base is not None:
            path = base / path
        return path

    def deepseek_files(self, dir: str = "") -> dict:
        """Các file .txt trong thư mục dự án (hoặc `dir`) để người dùng chọn file cần dịch."""
        self._ds()
        folder = Path(dir).expanduser() if dir else self._p().paths.dir
        if not folder.is_dir():
            raise ApiError("NO_DIR", f"Không có thư mục: {folder}")
        files = []
        for f in sorted(folder.glob("*.txt"), key=lambda x: x.name.lower()):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            files.append({"name": f.name, "path": str(f), "size": size,
                          "chapters": self._ds_mod.count_chapters(f) if size < 30_000_000 else None,
                          "output_hint": f.stem + ".deepseek.txt"})
        source = str(self._p().paths.source) if self.project else ""
        return {"dir": str(folder), "files": files, "source": source,
                "glossary": str(self.project.paths.glossary) if self.project else str(folder / "thuatngu.txt")}

    def deepseek_start(self, input: str, output: str = "", start_chapter: int = 1,
                       end_chapter: Optional[int] = None, allow_append: bool = False,
                       glossary: str = "") -> dict:
        mgr = self._ds()
        base = self.project.paths.dir if self.project else None
        src = self._ds_resolve(input, base)
        out = self._ds_resolve(output, src.parent) if output.strip() else src.with_name(src.stem + ".deepseek.txt")
        gloss = self._ds_resolve(glossary, base) if glossary.strip() else (
            self.project.paths.glossary if self.project else src.parent / "thuatngu.txt")
        st = self._ds_settings()
        try:
            status = mgr.start(input_path=src, output_path=out, glossary_path=gloss, api_keys=self._ds_keys(),
                               model=st.get("model"), base_url=st.get("base_url"), start_chapter=start_chapter,
                               end_chapter=end_chapter, allow_append=allow_append)
        except self._ds_mod.DeepSeekError as exc:
            raise ApiError(exc.code, exc.message)
        return {"status": status}

    def deepseek_stop(self) -> dict:
        return {"status": self._ds().stop()}

    def deepseek_reset(self, output: str, input: str = "") -> dict:
        mgr = self._ds()
        base = self.project.paths.dir if self.project else None
        out = self._ds_resolve(output, base)
        try:
            return mgr.reset(out)
        except self._ds_mod.DeepSeekError as exc:
            raise ApiError(exc.code, exc.message)

    def deepseek_status(self) -> dict:
        return {"status": self._ds().status()}

    # ---- bảng đăng ký phương thức ----
    def _register(self) -> None:
        def need(attr: str) -> Any:
            self._p()
            return getattr(self, attr)

        m = self.methods
        m["api.describe"] = lambda: ok(methods=sorted(self.methods), version=APP_VERSION)
        m["app.state"] = lambda: ok(version=APP_VERSION, project=bool(self.project),
                                    source=str(self.project.paths.source) if self.project else None,
                                    recent=self._app_state().get("recent", []),
                                    run_state=self.runner.state if self.runner else "idle",
                                    gated=self.gating())
        m["project.open"] = self.project_open
        m["project.info"] = self.project_info
        m["project.recent"] = lambda: ok(recent=self._app_state().get("recent", []))
        m["config.get"] = self.config_get
        m["config.save"] = self.config_save
        m["config.reset"] = self.config_reset
        m["config.export"] = self.config_export
        m["engine.set_mode"] = self.engine_set_mode
        m["ollama.status"] = self.ollama_status
        m["system.stats"] = lambda: ok(**system_stats())
        m["system.open"] = self.system_open
        m["files.read_aux"] = self.files_read_aux
        m["xungho.read"] = self.xungho_read
        m["xungho.review"] = self.xungho_review
        m["xungho.analyze"] = self.xungho_analyze
        m["xungho.decision"] = self.xungho_decision
        m["xungho.roles"] = self.xungho_roles
        m["xungho.approve_character"] = self.xungho_approve_character
        m["xungho.generate"] = self.xungho_generate
        m["xungho.save"] = self.xungho_save
        # B - báo cáo tác phẩm
        m["works.scan"] = lambda: ok(**{k: v for k, v in need("detector").scan().items() if k != "works"},
                                    works=len(need("detector").last["works"]))
        m["works.list"] = lambda **kw: ok(**need("detector").list(**kw))
        m["works.detail"] = lambda work_id: ok(**need("detector").detail(work_id))
        m["works.set_status"] = lambda work_ids, status: ok(**need("detector").set_status(work_ids, status))
        m["works.save"] = lambda: ok(**self._p().approvals.save())
        m["works.discard"] = lambda: (self._p().approvals.load(), ok())[1]
        m["works.export"] = self.works_export
        # C - nhân vật mốc
        m["anchors.list"] = lambda **kw: ok(**self._p().anchors.list(**kw))
        m["anchors.add"] = lambda fields: ok(row=self._p().anchors.add(fields), dirty=self._p().anchors.dirty())
        m["anchors.update"] = lambda row_id, fields: ok(row=self._p().anchors.update(row_id, fields),
                                                         dirty=self._p().anchors.dirty())
        m["anchors.delete"] = lambda row_ids: ok(deleted=self._p().anchors.delete(row_ids),
                                                 dirty=self._p().anchors.dirty())
        m["anchors.check"] = lambda: ok(**self._p().anchors.check())
        m["anchors.save"] = lambda: ok(**self._p().anchors.save())
        m["anchors.reload"] = lambda: (self._p().anchors.load(), ok(dirty=self._p().anchors.dirty()))[1]
        m["anchors.import_tsv"] = self.anchors_import
        m["anchors.export_tsv"] = self.anchors_export
        # D - Moegirl
        m["moegirl.cache_info"] = lambda: ok(**need("moegirl").cache_info())
        m["moegirl.rebuild_cache"] = lambda dataset_dir="": ok(**need("moegirl").rebuild_cache(dataset_dir))
        m["moegirl.build_project_cache"] = lambda: ok(**need("moegirl").build_project_cache())
        m["deepseek.files"] = self.deepseek_files
        m["deepseek.settings_get"] = self.deepseek_settings_get
        m["deepseek.settings_save"] = self.deepseek_settings_save
        m["deepseek.settings_clear_keys"] = self.deepseek_settings_clear_keys
        m["deepseek.start"] = self.deepseek_start
        m["deepseek.stop"] = self.deepseek_stop
        m["deepseek.reset"] = self.deepseek_reset
        m["deepseek.status"] = self.deepseek_status
        m["moegirl.names_bgm_view"] = lambda: ok(**need("moegirl").view_names_bgm())
        m["moegirl.ensure_names_bgm"] = lambda: ok(**need("moegirl").ensure_names_bgm())
        m["moegirl.generate"] = lambda: ok(**need("moegirl").generate())
        m["moegirl.list"] = lambda **kw: ok(**need("moegirl").list(**kw))
        m["moegirl.detail"] = lambda cand_id, max_contexts=3: ok(**need("moegirl").detail(cand_id, max_contexts))
        m["moegirl.set_status"] = lambda cand_ids, status: ok(**need("moegirl").set_status(cand_ids, status))
        m["moegirl.edit"] = lambda cand_id, **kw: ok(**need("moegirl").edit(cand_id, **kw))
        m["moegirl.export"] = lambda fmt="tsv", path="": ok(**need("moegirl").export(fmt, path))
        m["moegirl.commit_preview"] = lambda cand_ids=None, conflict_policy="hint": ok(
            **need("moegirl").commit_preview(cand_ids, conflict_policy))
        m["moegirl.commit"] = lambda token: ok(**need("moegirl").commit(token))
        # E - glossary
        m["glossary.read"] = lambda: ok(**self._p().glossary.read())
        m["glossary.validate"] = lambda text: ok(**self._p().glossary.validate(text))
        m["glossary.save"] = lambda text, allow_errors=False: ok(**self._p().glossary.save(text, allow_errors=allow_errors))
        m["glossary.backup"] = lambda: ok(**self._p().glossary.backup())
        m["glossary.insert"] = lambda src, dst, mode="force", note="": ok(**self._p().glossary.insert(src, dst, mode, note))
        m["glossary.find_replace"] = lambda find, repl, apply=False, **opt: ok(
            **self._p().glossary.find_replace(find, repl, apply=apply, **opt))
        # F - dịch & tiến trình
        m["run.preflight"] = self.run_preflight
        m["run.start"] = self.run_start
        m["run.pause"] = lambda: ok(**need("runner").pause())
        m["run.resume"] = lambda: ok(**need("runner").resume())
        m["run.stop"] = lambda graceful=False: ok(**need("runner").stop(graceful))
        m["run.restart_fresh"] = lambda token="": ok(**need("runner").restart_fresh(token))
        m["run.status"] = lambda: ok(**need("progress").status())
        m["run.heatmap"] = lambda around=None, window=46: ok(**need("progress").heatmap(around, window))
        m["run.chunk"] = lambda number: ok(**need("progress").chunk(int(number)))
        m["run.update_chunk"] = lambda number, text: ok(**need("progress").update_chunk(int(number), text))
        # G - nhật ký & kiểm tra
        m["logs.query"] = lambda **kw: ok(**self.logs.query(**kw))
        m["logs.clear"] = lambda: (self.logs.clear(), ok())[1]
        m["logs.export"] = self.logs_export
        m["guard.run"] = lambda check_han=None: ok(**need("guard").run(check_han))
        m["guard.get_config"] = lambda: ok(config=self._p().cfg["guard"])

    def run_preflight(self) -> dict:
        """Kiểm tra trước khi dịch; ở chế độ DeepSeek kiểm tra API key thay cho engine/Ollama."""
        runner = self.runner
        if runner is None:
            raise ApiError("NO_PROJECT", "Chưa chọn file truyện nguồn.")
        return ok(**runner.preflight(deepseek_check=self._deepseek_readiness))

    def run_start(self, on_fingerprint_mismatch: str = "stop") -> dict:
        runner = self.runner
        if runner is None:
            raise ApiError("NO_PROJECT", "Chưa chọn file truyện nguồn.")
        if self._p().engine_mode() == "deepseek":
            raise ApiError("ENGINE_MODE_DEEPSEEK",
                           "Dự án đang chọn engine DeepSeek (API). Hãy bấm 'Bắt đầu dịch' ở màn hình E, "
                           "hoặc đổi engine sang dichTrungsangViet.py ở màn hình E.")
        moegirl = self.moegirl
        return ok(**runner.start(on_fingerprint_mismatch, moegirl_refresh=moegirl.build_project_cache if moegirl else None))

    def works_export(self, path: str = "") -> dict:
        if self.detector is None or self.detector.last is None:
            raise ApiError("NOT_SCANNED", "Chưa quét văn bản nguồn.")
        text = json.dumps(self.detector.last, ensure_ascii=False, indent=2)
        if path:
            atomic_write(Path(path), text + "\n")
            return ok(path=path)
        return ok(text=text)

    def anchors_import(self, text: str = "", path: str = "", mode: str = "merge") -> dict:
        if path:
            text = read_text_auto(Path(path))
        return ok(**self._p().anchors.import_tsv(text, mode), dirty=self._p().anchors.dirty())

    def anchors_export(self, path: str = "") -> dict:
        text = self._p().anchors.serialize()
        if path:
            atomic_write(Path(path), text)
            return ok(path=path, rows=len(self._p().anchors.rows))
        return ok(text=text, rows=len(self._p().anchors.rows))

    def logs_export(self, path: str = "") -> dict:
        p = self._p()
        target = Path(path) if path else p.paths.runs_dir / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        return ok(path=str(target), lines=self.logs.export(target))

    def shutdown(self) -> None:
        self.closing = True
        if self.runner:
            self.runner.shutdown()
        with self.logs.cond:
            self.logs.cond.notify_all()


# ============================================================
# 15. TRANSPORT: HTTP JSON + SSE (dùng chung cho GuiDich.py) VÀ PYWEBVIEW BRIDGE
# ============================================================

MAX_BODY = 20 * 1024 * 1024


def _host_allowed(host_header: str) -> bool:
    host = (host_header or "").rsplit(":", 1)[0].strip("[]").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _authorized(handler: BaseHTTPRequestHandler, backend: NovelBackend, query: dict) -> bool:
    import hmac
    token = handler.headers.get("X-Novel-Token") or (query.get("token", [""])[0])
    return bool(token) and hmac.compare_digest(token, backend.token)


def stream_events(handler: BaseHTTPRequestHandler, backend: NovelBackend, since: int) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()
    last, last_progress = since, 0.0

    def emit(event: str, data: Any) -> None:
        handler.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
        handler.wfile.flush()

    try:
        while not backend.closing:
            backend.logs.wait(last, 1.0)
            result = backend.logs.query(since_id=last, limit=1000)
            for entry in result["items"]:
                emit("log", entry)
            if result["items"]:
                last = result["items"][-1]["id"]
            if time.time() - last_progress >= 1.0:
                if backend.project is not None:
                    emit("progress", backend.dispatch("run.status"))
                last_progress = time.time()
            handler.wfile.write(b": keep-alive\n\n")
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        return


def handle_api_request(handler: BaseHTTPRequestHandler, backend: NovelBackend) -> bool:
    """Xử lý /api/*. Trả True nếu đã xử lý (để GuiDich.py bỏ qua phần còn lại của do_GET/do_POST).

    POST /api/<method>   body JSON = tham số;  header X-Novel-Token bắt buộc.
    GET  /api/events     SSE (log + progress);  ?token=...&since=<id>
    GET  /api/health     không cần token.
    """
    url = urlparse(handler.path)
    if not url.path.startswith("/api/"):
        return False
    query = parse_qs(url.query)
    if not _host_allowed(handler.headers.get("Host", "")):
        _send_json(handler, 403, {"ok": False, "error": {"code": "BAD_HOST", "message": "Host không hợp lệ."}})
        return True
    name = url.path[len("/api/"):]
    if handler.command == "GET" and name == "health":
        _send_json(handler, 200, {"ok": True, "version": APP_VERSION})
        return True
    if not _authorized(handler, backend, query):
        _send_json(handler, 401, {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "Thiếu hoặc sai token."}})
        return True
    if handler.command == "GET" and name == "events":
        stream_events(handler, backend, int(query.get("since", ["0"])[0] or 0))
        return True
    if handler.command == "POST":
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                _send_json(handler, 413, {"ok": False, "error": {"code": "TOO_LARGE", "message": "Nội dung quá lớn."}})
                return True
            raw = handler.rfile.read(length) if length else b"{}"
            params = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(params, dict):
                raise ValueError("body phải là object JSON")
        except (ValueError, UnicodeDecodeError) as exc:
            _send_json(handler, 400, {"ok": False, "error": {"code": "BAD_JSON", "message": str(exc)}})
            return True
        _send_json(handler, 200, backend.dispatch(name, params))
        return True
    _send_json(handler, 405, {"ok": False, "error": {"code": "METHOD_NOT_ALLOWED", "message": "Chỉ hỗ trợ POST."}})
    return True


class ApiHandler(BaseHTTPRequestHandler):
    backend: Optional[NovelBackend] = None

    def log_message(self, *_args: Any) -> None:  # không ghi mỗi request ra stdout
        return

    def do_GET(self) -> None:
        if not (self.backend and handle_api_request(self, self.backend)):
            _send_json(self, 404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Không có route."}})

    do_POST = do_GET  # handle_api_request tự phân biệt theo self.command


def serve(backend: NovelBackend, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Chạy API độc lập (không cần GuiDich). Trả về (server, url)."""
    handler = type("BoundApiHandler", (ApiHandler,), {"backend": backend})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="novel-api").start()
    return server, f"http://{host}:{server.server_address[1]}"


class Bridge:
    """Đối tượng cho pywebview `js_api=`: window.pywebview.api.call('anchors.list', {...})."""

    def __init__(self, backend: NovelBackend) -> None:
        self._backend = backend
        self.window = None

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        return self._backend.dispatch(method, params or {})

    def token(self) -> str:
        return self._backend.token

    def pick_file(self, kind: str = "txt") -> dict:
        """Hộp thoại chọn file (chỉ khi chạy trong pywebview)."""
        if self.window is None:
            return {"ok": False, "error": {"code": "NO_WINDOW", "message": "Cửa sổ chưa sẵn sàng."}}
        import webview  # type: ignore
        types = {"txt": ("Text files (*.txt)", "All files (*.*)"), "py": ("Python files (*.py)",),
                 "json": ("JSON files (*.json)",), "tsv": ("TSV/Text (*.txt;*.tsv)",)}
        files = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                               file_types=types.get(kind, types["txt"]))
        if not files:
            return {"ok": False, "cancelled": True}
        return {"ok": True, "path": str(Path(files[0]).resolve())}

    def save_file(self, suggested: str = "export.txt") -> dict:
        if self.window is None:
            return {"ok": False, "error": {"code": "NO_WINDOW", "message": "Cửa sổ chưa sẵn sàng."}}
        import webview  # type: ignore
        files = self.window.create_file_dialog(webview.SAVE_DIALOG, save_filename=suggested)
        if not files:
            return {"ok": False, "cancelled": True}
        return {"ok": True, "path": str(files if isinstance(files, str) else files[0])}


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Chạy API backend độc lập để thử/gọi từ giao diện.")
    parser.add_argument("--engine", default="", help="Đường dẫn dichTrungsangViet.py")
    parser.add_argument("--source", default="", help="File truyện .txt để mở sẵn")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    backend = NovelBackend(default_engine=args.engine)
    if args.source:
        result = backend.dispatch("project.open", {"source": args.source})
        if not result["ok"]:
            print("Không mở được dự án:", result["error"]["message"])
            return 1
    server, url = serve(backend, port=args.port)
    print(f"API: {url}/api/<method>   (header X-Novel-Token: {backend.token})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        backend.shutdown()
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())