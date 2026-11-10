import csv
import os
import re
import sys
import json
import time
import random
import hashlib
import logging
import threading
import unicodedata
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from openai import OpenAI
from xungho_runtime import hint_for_chunk, load_rules

ADDRESSING_RULES = None

# ============================================================
# 1. CẤU HÌNH
# ============================================================

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

BASE_URL = "http://localhost:11434/v1"
MODEL = "index-nailong"
API_KEY = "ollama"
TARGET_LANGUAGE = "tiếng Việt"

# ---------------------------------------------------------------------------
# NHÓM 1 - Chia chunk & context window
# ---------------------------------------------------------------------------
# MAX_CHARS      : số ký tự tối đa mỗi chunk (mục tiêu). Chunk thật = min(MAX_CHARS, giới hạn
#                  suy ra từ CONTEXT_TOKENS) nên không bao giờ tràn context của model.
# CONTEXT_TOKENS : context window THỰC TẾ model đang chạy (num_ctx của Ollama / Modelfile).
#                  Ollama thường mặc định 2048-4096; nếu prompt+đầu ra vượt mức này, model bị
#                  cắt cụt -> chính là nguyên nhân hay gặp của lỗi "bản dịch quá ngắn".
# Ước lượng token: nguồn ~TOKENS_PER_SOURCE_CHAR / ký tự Hán, đầu ra ~OUTPUT_TOKENS_PER_HAN / ký tự Hán.
# PRESERVE_INDENT: giữ thụt lề đầu dòng (vd "　　") của từng đoạn và áp lại vào bản dịch.
#                  Lưu ý: đổi giá trị này làm đổi nội dung chunk -> dấu vân tay chunk lệch progress cũ.
MAX_CHARS = 1500               # Cắt khoảng nửa chương (rất lý tưởng, vừa đủ ngữ cảnh, vừa không quá nặng)
CONTEXT_TOKENS = 8192          # ĐỒNG BỘ với num_ctx 8192 trong Modelfile
CONTEXT_SAFETY = 0.90          # Tăng mức sử dụng an toàn lên 90% (vì 8192 rất rộng)
PROMPT_OVERHEAD_TOKENS = 300   # 700 là quá thừa cho cái prompt hệ thống ngắn của bạn, 300 là an toàn
TOKENS_PER_SOURCE_CHAR = 1.0
OUTPUT_TOKENS_PER_HAN = 2.5
MAX_OUTPUT_TOKENS_FACTOR = 3.0
MIN_CHUNK_CHARS = 60
PRESERVE_INDENT = True

# ---------------------------------------------------------------------------
# NHÓM 2 - Retry / Backoff / Bisect
# ---------------------------------------------------------------------------
# MAX_RETRIES_PER_CHUNK : tổng số lần gọi tối đa cho MỘT đoạn (gồm lỗi mạng/API).
# MAX_INVALID_RETRIES   : số lần chấp nhận bản dịch "không hợp lệ" trước khi bisect/bỏ cuộc.
# RETRY_TEMPERATURES    : temperature dùng cho các lần dịch lại (theo thứ tự), thấp hơn TEMPERATURE.
# RETRY_BACKOFF_*       : chờ = BASE * 2^(lần-1), tối đa MAX, có jitter +-15%.
# MAX_BISECT_DEPTH      : số tầng chia đôi tối đa (2 => tối đa 4 mảnh). 0 = tắt bisect.
# BISECT_MIN_CHARS      : chunk ngắn hơn mức này không chia đôi nữa.
DELAY_BETWEEN_CALLS = 0.5
MAX_RETRIES_PER_CHUNK = 10
MAX_INVALID_RETRIES = 3
RETRY_TEMPERATURES = (0.15, 0.1, 0.1)
RETRY_BACKOFF_BASE = 3.0
RETRY_BACKOFF_MAX = 60.0
RETRY_INVALID_DELAY = 2.0
MAX_BISECT_DEPTH = 2
BISECT_MIN_CHARS = 120

# ---------------------------------------------------------------------------
# NHÓM 3 - Song song & giới hạn tốc độ
# ---------------------------------------------------------------------------
# WORKERS   : số chunk dịch đồng thời (Semaphore). Ollama cục bộ thường nên để 1, trừ khi đã đặt
#             OLLAMA_NUM_PARALLEL. Bản dịch luôn được GHI theo đúng thứ tự chunk.
# RPM_LIMIT : tối đa số request/phút (0 = không giới hạn). TPM_LIMIT: token/phút ước lượng (0 = tắt).
WORKERS = 1
RPM_LIMIT = 0
TPM_LIMIT = 0

# ---------------------------------------------------------------------------
# NHÓM 4 - Sinh dịch & kiểm tra chất lượng
# ---------------------------------------------------------------------------
# Tỉ lệ độ dài = (số ký tự bản dịch) / (số CHỮ HÁN của nguồn); nguồn không phải chữ Hán thì chia
# cho số ký tự không-khoảng-trắng. Tính trên chữ Hán vì tổng ký tự nguồn còn gồm dấu câu toàn-chiều,
# tiêu đề "##", chữ Latin... làm mẫu số phình ra và báo "quá ngắn" sai.
#   ratio < MIN_RATIO                         -> CẢNH BÁO (vẫn nhận bản dịch)
#   ratio < MIN_RATIO * LENGTH_HARD_FACTOR    -> cụt thật: loại, dịch lại
#   ratio > MAX_RATIO                         -> nghi lặp: loại, dịch lại
# LANG_PAIR chọn cặp ngưỡng mặc định trong RATIO_PRESETS (các cặp ngoài zh-vi là ước lượng ban đầu,
# hãy hiệu chỉnh theo dữ liệu thật của bạn).
TEMPERATURE = 0.1
TOP_P = 0.6
UNTRANSLATED_TAG = "[CHƯA DỊCH - GIỮ BẢN GỐC]"
MIN_SOURCE_LEN_CHECK = 100
LANG_PAIR = "zh-vi"
RATIO_PRESETS = {"zh-vi": (0.9, 8.0), "zh-en": (0.5, 4.0), "ja-vi": (0.7, 6.0)}
MIN_RATIO = RATIO_PRESETS[LANG_PAIR][0]
MAX_RATIO = RATIO_PRESETS[LANG_PAIR][1]
LENGTH_HARD_FACTOR = 0.5
MAX_HAN_RATIO = 0.15     # (chỉ còn truyền cho TranslationGuard; kiểm tra Hán đã tắt)

FILE_INPUT = Path(
    r"D:\8man\Ciweimao\Chính trị và pháp luật hệ Hikigaya Hachiman\Chính trị và pháp luật hệ Hikigaya Hachiman.txt"
)
FILE_OUTPUT = Path(
    r"D:\8man\Ciweimao\Chính trị và pháp luật hệ Hikigaya Hachiman\bandich.txt"
)
# Để trống nếu không dùng glossary.
# đó đúng là file thuật ngữ của fic này.
GLOSSARY_FILE = r"D:\8man\Ciweimao\Chính trị và pháp luật hệ Hikigaya Hachiman\thuatngu.txt"
# Glossary tự động tách riêng; các mục trong file thủ công luôn được ưu tiên.
AUTO_GLOSSARY_FILE = Path(str(GLOSSARY_FILE) + ".auto.txt")
# Bật True nếu muốn model hiện tại đề xuất glossary tự động một lần mỗi lượt chạy.
# Các mục được ghi sang file .auto.txt và chỉ dùng làm gợi ý, không ép thay tên.
AUTO_DISCOVER_TERMS = False
AUTO_TERM_MIN_FREQUENCY = 3
AUTO_TERM_MAX_CANDIDATES = 30
# Kho dữ liệu Moegirl được thu thập riêng; không bao giờ ghi vào thuatngu.txt.
MOEGIRL_TERMS_FILE = Path(__file__).resolve().with_name("moegirl_terms.json")
# Dataset local của người dùng; không gọi API. Chỉ đọc extra_processed/char2subject/char2gender.
MOEGIRL_DATASET_DIR = Path(r"D:\8man\moe_girl_dataset\moegirl-dataset\moegirl")
MOEGIRL_AUTO_IMPORT_DATASET = True
MOEGIRL_ENABLED = True
MOEGIRL_MAX_HINTS_PER_CHUNK = 30
# Chỉ tự ghi tên Moegirl vào glossary riêng của truyện khi điểm tin cậy đạt ngưỡng.
# Điểm là heuristic (độ tin cậy nguồn + bằng chứng xuất hiện trong truyện), không phải xác suất thống kê đã kiểm định.
MOEGIRL_AUTO_COMMIT_MIN_CONFIDENCE = 0.90
MOEGIRL_AUTO_COMMIT_MIN_OCCURRENCES = 3
# --- Neo tác phẩm / tác phẩm đã duyệt (cùng thư mục với thuatngu.txt) ---
_WORK_DIR = Path(GLOSSARY_FILE).parent
WORK_ANCHORS_FILE = _WORK_DIR / "moegirl_work_anchors.txt"        # bạn viết
WORKS_APPROVED_FILE = _WORK_DIR / "moegirl_works_approved.txt"    # bạn duyệt
WORK_REVIEW_FILE = _WORK_DIR / "moegirl_work_review.txt"          # script ghi
MOEGIRL_SUGGESTIONS_FILE = _WORK_DIR / "moegirl_suggestions.txt"  # script ghi
WORK_ANCHOR_COUNT_CAP = 10        # một neo xuất hiện nhiều nữa cũng chỉ tính tối đa ngần này lần
WORK_MIN_DISTINCT_ANCHORS = 2     # cần ít nhất ngần này neo khác nhau mới coi là có dấu hiệu tác phẩm
# False: chỉ dùng nhân vật thuộc tác phẩm đã duyệt. True: nhân vật tác phẩm khác vẫn vào prompt (chỉ gợi ý).
MOEGIRL_INCLUDE_UNAPPROVED = False
MOEGIRL_MIN_FORCE_LEN = 2         # tên Hán ngắn hơn ngần này ký tự không bao giờ bị ép
MOEGIRL_LATIN_CONFIDENCE = 0.92   # điểm mặc định khi có tên Latin từ trường tên

FILE_PROGRESS = Path(str(FILE_OUTPUT) + ".progress.json")
FILE_ERROR_LOG = Path(str(FILE_OUTPUT) + ".error.log")

# True: xóa output và progress rồi dịch lại từ đầu.
RESTART_FRESH = False

HAN_RE = re.compile(r"[\u4e00-\u9fff]")

# Những cụm cho thấy model đã dịch/chép lại chỉ dẫn hoặc glossary vào đầu ra.
LEAK_MARKERS = [
    "=>",
    "bảng thuật ngữ",
    "Bảng thuật ngữ",
    "Tham khảo bản dịch sau đây",
    "tham khảo bản dịch",
    "Dịch văn bản sau",
    "không giải thích thêm",
    "dịch thành",
    "Hãy tuân thủ",
    "tuân thủ nghiêm ngặt",
    "ghi chú dưới đây",
]


class InvalidTranslation(Exception):
    """Bản dịch trả về không đạt kiểm tra (lọt chỉ dẫn, quá ngắn, lặp...)."""


# ============================================================
# 2. TIỆN ÍCH
# ============================================================

def format_duration(seconds):
    seconds = int(max(0, seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def read_text_file(path):
    """Đọc file và LUÔN chuẩn hóa xuống dòng về \n (file output trên Windows chứa \r\n)."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = Path(path).read_text(encoding="utf-16")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def atomic_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = Path(str(path) + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


# ============================================================
# 3. GLOSSARY
# ============================================================

# ============================================================
# Moegirl dataset importer tích hợp trực tiếp; không gọi API.
# Chỉ đọc extra_processed.json, char2subject.json, char2gender.json.
# Không sử dụng char2cv.json hoặc cv2char.json làm glossary.
# ============================================================

MOEGIRL_LATIN_FIELDS = ("英文名", "外文名", "罗马音", "罗马字", "拉丁字母", "英文", "罗马拼音")
MOEGIRL_ALIAS_FIELDS = ("别名", "别号", "昵称", "绰号", "其他名字", "本名")
MOEGIRL_REAL_NAME_FIELDS = ("本名",)  # tên thật, dùng làm đích dịch (dst)


def _moegirl_read_json(path, required=False):
    path = Path(path)
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Không tìm thấy dataset: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON không hợp lệ tại {path}: {exc}") from exc


def _moegirl_values(value):
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if isinstance(x, (str, int, float)) and str(x).strip()]
    return []


def _moegirl_is_latin_name(value):
    value = str(value or "").strip()
    if not 2 <= len(value) <= 100 or not re.search(r"[A-Za-z]", value):
        return False
    if re.search(r"[\u3400-\u9fff\u3040-\u30ff]", value):
        return False
    if len(value.split()) > 7 or re.search(r"[。！？；，：\n\r]", value):
        return False
    return True


def _moegirl_extract_fields(info):
    aliases, latin_names = [], []
    if not isinstance(info, dict):
        return aliases, latin_names
    # Bí danh (kể cả 本名) đưa vào danh sách tên gọi khác.
    for field in MOEGIRL_ALIAS_FIELDS:
        for value in _moegirl_values(info.get(field)):
            if value not in aliases:
                aliases.append(value)
    # Đích dịch (dst) CHỈ lấy tên thật (本名) + trường Latin, không lấy bí danh.
    for field in MOEGIRL_REAL_NAME_FIELDS + MOEGIRL_LATIN_FIELDS:
        for value in _moegirl_values(info.get(field)):
            if _moegirl_is_latin_name(value) and value not in latin_names:
                latin_names.append(value)
    return aliases, latin_names


def build_moegirl_catalog(dataset_dir):
    dataset_dir = Path(dataset_dir)
    extra = _moegirl_read_json(dataset_dir / "extra_processed.json", required=True)
    char2subject = _moegirl_read_json(dataset_dir / "char2subject.json")
    char2gender = _moegirl_read_json(dataset_dir / "char2gender.json")
    if not isinstance(extra, dict):
        raise ValueError("extra_processed.json phải chứa JSON object.")
    records = {}
    for title, info in extra.items():
        if not isinstance(title, str) or not title.strip() or not isinstance(info, dict):
            continue
        title = title.strip()
        aliases, latin_names = _moegirl_extract_fields(info)
        aliases = [x for x in aliases if x != title]
        subjects = _moegirl_values(char2subject.get(title, [])) if isinstance(char2subject, dict) else []
        raw_gender = char2gender.get(title) if isinstance(char2gender, dict) else None
        gender = str(raw_gender).strip().lower() if raw_gender is not None else ""
        if gender not in {"male", "female", "other"}:
            gender = ""
        records[title] = {
            "page_title": title, "canonical_zh": title, "aliases": aliases,
            "latin_names": latin_names, "subjects": subjects[:80], "gender": gender,
            # Tên Latin không chứng minh được rằng ánh xạ phù hợp với bản dịch.
            "confidence": 0.0, "source": "local-moegirl-dataset",
        }
    return {
        "schema_version": 2, "record_count": len(records),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir), "records": records,
    }


def save_moegirl_catalog(catalog, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = Path(str(output_path) + ".tmp")
    temp_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, output_path)


def ensure_moegirl_dataset_cache():
    """Tạo/cập nhật moegirl_terms.json từ dataset local khi nguồn mới hơn cache."""
    if not MOEGIRL_AUTO_IMPORT_DATASET:
        return
    source_files = [
        MOEGIRL_DATASET_DIR / "extra_processed.json",
        MOEGIRL_DATASET_DIR / "char2subject.json",
        MOEGIRL_DATASET_DIR / "char2gender.json",
    ]
    existing = [p for p in source_files if p.is_file()]
    if not existing:
        print(f"[Moegirl Dataset] Không thấy dataset tại {MOEGIRL_DATASET_DIR}; dùng cache hiện có nếu có.")
        return
    cache_schema_ok = False
    if MOEGIRL_TERMS_FILE.is_file():
        try:
            with MOEGIRL_TERMS_FILE.open("r", encoding="utf-8-sig") as cache_file:
                cache_meta = json.load(cache_file)
            cache_schema_ok = (
                isinstance(cache_meta, dict)
                and cache_meta.get("schema_version") == 2
                and str(cache_meta.get("dataset_dir", "")).lower() == str(MOEGIRL_DATASET_DIR).lower()
            )
        except (OSError, json.JSONDecodeError):
            cache_schema_ok = False
    cache_is_fresh = cache_schema_ok and all(
        MOEGIRL_TERMS_FILE.stat().st_mtime >= source.stat().st_mtime for source in existing
    )
    if cache_is_fresh:
        return
    try:
        print(f"[Moegirl Dataset] Đang nhập dữ liệu local từ {MOEGIRL_DATASET_DIR}...")
        catalog = build_moegirl_catalog(MOEGIRL_DATASET_DIR)
        save_moegirl_catalog(catalog, MOEGIRL_TERMS_FILE)
        latin_count = sum(bool(record.get("latin_names")) for record in catalog.get("records", {}).values())
        print(f"[Moegirl Dataset] Cache đã cập nhật: {catalog.get('record_count', len(catalog.get('records', {}))):,} nhân vật; "
              f"{latin_count:,} nhân vật có tên Latin; file={MOEGIRL_TERMS_FILE}")
    except Exception as exc:
        print(f"[Moegirl Dataset] Không nhập được dataset ({exc}); dùng cache hiện có nếu có.")


# ============================================================
# Neo tác phẩm -> tác phẩm đã duyệt -> nhân vật Moegirl ưu tiên
#   moegirl_work_anchors.txt   (bạn viết)  : tên neo canon, dùng nhận diện tác phẩm
#   moegirl_works_approved.txt (bạn duyệt) : work_id, work_title, subject_keys
#   moegirl_work_review.txt    (script ghi): báo cáo tác phẩm nhận diện được
#   moegirl_suggestions.txt    (script ghi): nhân vật của tác phẩm đã duyệt có trong truyện
# Chỉ nhân vật thuộc tác phẩm ĐÃ DUYỆT mới được xét "force".
# ============================================================

GENERATED_MARK = "# GENERATED by dichTrungsangViet.py"
_PAREN_TAIL_RE = re.compile(r"[\(（][^\)）]*[\)）]\s*$")


def strip_page_suffix(title):
    """'艾丝黛尔(明日方舟)' -> '艾丝黛尔': trong truyện không có hậu tố phân biệt."""
    stripped = _PAREN_TAIL_RE.sub("", str(title)).strip()
    return stripped or str(title).strip()


def _read_tsv_rows(path):
    """Đọc TSV có dòng tiêu đề; bỏ dòng trống và dòng bắt đầu bằng #."""
    path = Path(path)
    if not path.is_file():
        return []
    lines = [
        ln for ln in read_text_file(path).splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not lines:
        return []
    rows = []
    for row in csv.DictReader(lines, delimiter="\t"):
        rows.append({
            (k or "").strip(): (v or "").strip() if isinstance(v, str) or v is None else ""
            for k, v in row.items() if k is not None
        })
    return rows


def load_work_anchors(path):
    """Đọc neo tác phẩm, hỗ trợ cả định dạng cũ (type/anchor_weight) lẫn mới (kind/weight)."""
    if not Path(path).is_file():
        print(f"[Neo] Không thấy file neo: {path}; bỏ qua nhận diện tác phẩm.")
        return []
    anchors = []
    for row in _read_tsv_rows(path):
        name = row.get("source_name", "")
        work_id = row.get("work_id", "")
        if not name or not work_id:
            continue
        if "kind" in row or "weight" in row:
            # Định dạng mới (màn hình C): chỉ dùng kind main/support và weight > 0.
            if row.get("kind", "").strip().lower() not in ("main", "support"):
                continue
            try:
                weight = float(row.get("weight") or 0)
            except ValueError:
                weight = 0.0
            if weight <= 0:
                continue
            aliases = [a.strip() for a in re.split(r"[|,，;；]", row.get("aliases", "")) if a.strip()]
        else:
            # Định dạng cũ: chỉ dùng type=canon và anchor_weight > 0.
            if row.get("type", "").strip().lower() != "canon":
                continue
            try:
                weight = int(row.get("anchor_weight") or 0)
            except ValueError:
                weight = 0
            if weight <= 0:
                continue
            aliases = [a.strip() for a in row.get("aliases", "").split("|") if a.strip()]
        names = [name] + aliases
        anchors.append({"work_id": work_id, "name": name, "names": names, "weight": weight})
    print(f"[Neo] Đã nạp {len(anchors)} neo canon.")
    return anchors


def detect_works(text, anchors):
    """Điểm tác phẩm = tổng(weight x min(số lần xuất hiện, trần)) trên các neo."""
    works = {}
    for anchor in anchors:
        count = sum(text.count(n) for n in anchor["names"])
        if not count:
            continue
        info = works.setdefault(anchor["work_id"], {"work_id": anchor["work_id"], "score": 0.0, "anchors": {}})
        info["score"] += anchor["weight"] * min(count, WORK_ANCHOR_COUNT_CAP)
        info["anchors"][anchor["name"]] = count
    return works


def load_approved_works(path):
    """moegirl_works_approved.txt: work_id, work_title, subject_keys (phân tách '|')."""
    approved = {}
    if not Path(path).is_file():
        print(f"[Tác phẩm duyệt] Không thấy file: {path}")
        return approved
    for row in _read_tsv_rows(path):
        work_id = row.get("work_id", "")
        keys = [k.strip() for k in row.get("subject_keys", "").split("|") if k.strip()]
        if not work_id:
            continue
        if not keys:
            print(f"[Tác phẩm duyệt] '{work_id}' chưa có subject_keys; bỏ qua.")
            continue
        approved[work_id] = {"work_id": work_id, "title": row.get("work_title", ""), "subject_keys": keys}
    print(f"[Tác phẩm duyệt] Đã nạp {len(approved)} tác phẩm: {', '.join(approved) or '(chưa có)'}")
    return approved


def _write_generated_report(path, columns, rows, note):
    """Ghi báo cáo do script sinh. Không ghi đè file có nội dung của bạn."""
    path = Path(path)
    if path.is_file():
        old = read_text_file(path)
        if old.strip() and not old.lstrip().startswith(GENERATED_MARK):
            alt = path.with_name(path.stem + ".generated" + path.suffix)
            print(f"[Báo cáo] {path.name} có nội dung của bạn; ghi sang {alt.name} để không ghi đè.")
            path = alt
    lines = [f"{GENERATED_MARK} - {note}; bị ghi đè mỗi lần chạy", "\t".join(columns)]
    for row in rows:
        lines.append("\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in row))
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def write_work_review(path, detected, approved):
    order = {"pending_approval": 0, "approved_no_evidence": 1, "weak": 2, "approved": 3}
    rows = []
    for work_id in set(detected) | set(approved):
        info = detected.get(work_id)
        score = info["score"] if info else 0
        distinct = len(info["anchors"]) if info else 0
        if work_id in approved:
            status = "approved" if distinct else "approved_no_evidence"
        elif distinct >= WORK_MIN_DISTINCT_ANCHORS:
            status = "pending_approval"
        else:
            status = "weak"
        hits = ", ".join(
            f"{n}x{c}" for n, c in sorted(info["anchors"].items(), key=lambda kv: kv[1], reverse=True)
        ) if info else ""
        rows.append((status, work_id, f"{score:g}", distinct, hits))
    rows.sort(key=lambda r: (order[r[0]], -float(r[2])))
    out = _write_generated_report(
        path, ["status", "work_id", "score", "distinct_anchors", "anchors_hit"], rows,
        "tác phẩm nhận diện được từ neo",
    )
    for status, work_id, score, distinct, _hits in rows:
        if status == "pending_approval":
            print(f"[Tác phẩm] '{work_id}' có dấu hiệu trong truyện ({distinct} neo, điểm {score}) nhưng CHƯA duyệt.")
        elif status == "approved_no_evidence":
            print(f"[Tác phẩm] '{work_id}' đã duyệt nhưng không thấy neo nào trong truyện; kiểm tra lại.")
    print(f"[Tác phẩm] Báo cáo: {out}")
    return rows


def load_moegirl_entries(path, source_text="", approved=None):
    """Đọc kho Moegirl và trả về các mục cho TermManager.

    - Chỉ dùng nhân vật thuộc tác phẩm đã duyệt (trừ khi MOEGIRL_INCLUDE_UNAPPROVED).
    - Chỉ giữ mục có tên xuất hiện trong truyện; bỏ hậu tố '(tác phẩm)' của tên trang.
    - 'force' khi: tác phẩm đã duyệt + có tên Latin từ trường tên + là tên trang chính
      + xuất hiện >= MOEGIRL_AUTO_COMMIT_MIN_OCCURRENCES lần + src >= MOEGIRL_MIN_FORCE_LEN ký tự.
    - Nhân vật đã duyệt nhưng không có tên Latin: không đưa vào prompt (đích chữ Hán vô nghĩa
      với bản dịch Việt); chỉ liệt kê vào file gợi ý với dst rỗng để bạn tự điền.
    """
    path = Path(path)
    approved = approved or {}
    subject_to_work = {}
    for work in approved.values():
        for key in work["subject_keys"]:
            subject_to_work.setdefault(key, work["work_id"])
    if not path.is_file():
        print(f"[Moegirl] Chưa có kho dữ liệu: {path}")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[Moegirl] Không đọc được JSON ({exc}); bỏ qua kho Moegirl.")
        return []
    records = data.get("records", {}) if isinstance(data, dict) else {}
    iterable = records.values() if isinstance(records, dict) else records if isinstance(records, list) else []

    known_subjects = set()
    candidates = []
    for record in iterable:
        if not isinstance(record, dict):
            continue
        subjects = record.get("subjects", [])
        if isinstance(subjects, str):
            subjects = [subjects]
        subjects = [str(x) for x in subjects]
        known_subjects.update(subjects)
        work_id = next((subject_to_work[s] for s in subjects if s in subject_to_work), None)
        if work_id is None and not MOEGIRL_INCLUDE_UNAPPROVED:
            continue
        candidates.append((work_id, record))
    # Nhân vật của tác phẩm đã duyệt được xử lý trước nên thắng khi trùng tên.
    candidates.sort(key=lambda x: x[0] is None)

    for work in approved.values():
        for key in work["subject_keys"]:
            if key not in known_subjects:
                print(f"[Moegirl] subject_key '{key}' ({work['work_id']}) không khớp nhân vật nào; kiểm tra chính tả.")

    normalized_source = unicodedata.normalize("NFC", source_text or "")
    entries, seen = [], set()
    for work_id, record in candidates:
        title = str(record.get("canonical_zh") or record.get("page_title") or "").strip()
        if not title:
            continue
        title_src = strip_page_suffix(title)
        latin_names = record.get("latin_names", [])
        if isinstance(latin_names, str):
            latin_names = [latin_names]
        latin_names = [str(x).strip() for x in latin_names if str(x).strip()]
        try:
            confidence = float(record.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        if latin_names and confidence <= 0.0:
            confidence = MOEGIRL_LATIN_CONFIDENCE  # không phụ thuộc phiên bản importer đã tạo cache
        gender = str(record.get("gender") or "").strip()
        gender_note = f"; gender={gender}" if gender else ""
        work_note = f"; work={work_id}" if work_id else ""
        base = {"work_id": work_id, "page_title": title, "gender": gender}

        if not latin_names:
            occurrences = normalized_source.count(unicodedata.normalize("NFC", title_src))
            if work_id and occurrences and title_src not in seen and HAN_RE.search(title_src):
                seen.add(title_src)
                entries.append({**base, "src": title_src, "dst": "", "confidence": 0.0,
                                "occurrences": occurrences, "auto_commit": False,
                                "info": f"moegirl no-latin; occurrences={occurrences}{work_note}{gender_note}"})
            continue

        dst = latin_names[0]
        aliases = record.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        sources = [title_src] + [strip_page_suffix(x) for x in (aliases if isinstance(aliases, list) else [])]
        for source_index, src in enumerate(sources):
            if not src or src == dst or src in seen or not HAN_RE.search(src):
                continue
            occurrences = normalized_source.count(unicodedata.normalize("NFC", src))
            if occurrences == 0:
                continue
            seen.add(src)
            auto_commit = (
                work_id is not None
                and source_index == 0
                and occurrences >= MOEGIRL_AUTO_COMMIT_MIN_OCCURRENCES
                and confidence >= MOEGIRL_AUTO_COMMIT_MIN_CONFIDENCE
                and len(src) >= MOEGIRL_MIN_FORCE_LEN
            )
            # Lưu ý: không dùng chữ 'auto' trong info của mục force, vì TermManager coi
            # nhãn 'auto' là gợi ý (hint) và sẽ không bao giờ ép thay.
            info = (
                f"moegirl confidence={confidence:.2f}; occurrences={occurrences}; "
                + ("force; approved work" if auto_commit else "hint; verify context")
                + work_note + gender_note
            )
            entries.append({**base, "src": src, "dst": dst, "info": info,
                            "confidence": confidence, "occurrences": occurrences,
                            "auto_commit": auto_commit})
    entries.sort(key=lambda x: len(x["src"]), reverse=True)
    forced = sum(1 for item in entries if item["auto_commit"])
    print(f"[Moegirl] {len(entries)} mục có mặt trong truyện; {forced} mục đạt ngưỡng ép "
          f"(tác phẩm đã duyệt, ngưỡng {MOEGIRL_AUTO_COMMIT_MIN_CONFIDENCE:.2f}, >= {MOEGIRL_AUTO_COMMIT_MIN_OCCURRENCES} lần).")
    return entries


def write_moegirl_suggestions(path, entries):
    """Danh sách để bạn rà soát rồi tự chép mục ưng ý sang thuatngu.txt."""
    order = {"force": 0, "hint": 1, "need_translation": 2}
    rows = []
    for e in entries:
        if not e.get("work_id"):
            continue
        mode = "force" if e["auto_commit"] else ("hint" if e["dst"] else "need_translation")
        rows.append((mode, e["src"], e["dst"], e["work_id"], e["occurrences"],
                     e.get("gender", ""), e.get("page_title", "")))
    rows.sort(key=lambda r: (order[r[0]], -r[4]))
    out = _write_generated_report(
        path, ["mode", "src", "dst", "work_id", "occurrences", "gender", "page_title"], rows,
        "nhân vật của tác phẩm đã duyệt có trong truyện; chép mục ưng ý sang thuatngu.txt",
    )
    print(f"[Moegirl] Gợi ý ({len(rows)} dòng): {out}")
    return rows


def append_moegirl_auto_terms(path, entries, protected_sources):
    """Ghi các mục Moegirl đạt ngưỡng vào glossary tự động riêng của truyện.

    Không ghi đè glossary thủ công hoặc mục tự động đã tồn tại.
    """
    path = Path(path)
    protected = set(protected_sources)
    existing = set()
    if path.is_file():
        try:
            existing = {item["src"] for item in load_glossary(path)}
        except Exception:
            existing = set()
    additions = []
    for item in entries:
        if not item.get("auto_commit"):
            continue
        src = item["src"]
        if src in protected or src in existing:
            continue
        info = item.get("info", "moegirl confidence")
        additions.append(f"{src} => {item['dst']} # {info}")
        existing.add(src)
    if additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        separator = "" if not old or old.endswith("\n") else "\n"
        atomic_write_text(path, old + separator + "\n".join(additions) + "\n")
        print(f"[Moegirl] Đã thêm {len(additions)} thuật ngữ đạt ngưỡng vào {path.name}.")
    return len(additions)


def load_glossary(path):
    """
    Hỗ trợ:
      English=>tiếng Trung, tiếng Trung=>English, hoặc các dạng tương tự với ->, TAB, =
      Dòng không có dấu phân cách: giữ nguyên từ/cụm từ.
    Khi dịch văn bản nguồn tiếng Trung, relevant_glossary() tự chọn chiều
    dựa trên cụm nào thực sự xuất hiện trong chunk.

    Có thể thêm ghi chú bằng cách viết: nguồn=>đích#ghi chú
    Dòng trống và dòng bắt đầu bằng # được bỏ qua.
    """
    glossary = []
    if not path or not str(path).strip():
        print("[Glossary] Chưa cấu hình file thuật ngữ; bỏ qua glossary.")
        return glossary

    glossary_path = Path(path)
    if not glossary_path.exists() or not glossary_path.is_file():
        print(f"[Glossary] Không tìm thấy file: {glossary_path}; bỏ qua.")
        return glossary

    for lineno, raw_line in enumerate(read_text_file(glossary_path).splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        mapping_part, info = line, None
        if "#" in line:
            mapping_part, info = line.split("#", 1)
            info = info.strip() or None

        src = dst = None
        for separator in ("=>", "->", "\t", "="):
            if separator in mapping_part:
                src, dst = mapping_part.split(separator, 1)
                break

        # Không có separator: yêu cầu giữ nguyên thuật ngữ.
        if src is None:
            src = dst = mapping_part.strip()
            if not info:
                info = "Giữ nguyên văn bản gốc, không dịch"

        src, dst = src.strip(), dst.strip()
        if not src or not dst:
            print(f"[Glossary] Bỏ qua dòng {lineno} không hợp lệ: {raw_line!r}")
            continue

        item = {"src": src, "dst": dst}
        if info:
            item["info"] = info
        glossary.append(item)

    # Thuật ngữ dài được ưu tiên trước để prompt có thứ tự nhất quán.
    glossary.sort(key=lambda item: len(item["src"]), reverse=True)
    print(f"[Glossary] Đã nạp {len(glossary)} thuật ngữ.")
    return glossary


def _latin_pattern(term):
    # Thuật ngữ Latin chỉ khớp khi đứng riêng, không nằm giữa các chữ cái/số khác.
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])")


def term_in_text(term, text):
    if HAN_RE.search(term):
        return term in text
    return _latin_pattern(term).search(text) is not None


def mask_term(term, text):
    if HAN_RE.search(term):
        return text.replace(term, "\x00")
    return _latin_pattern(term).sub("\x00", text)


def relevant_glossary(chunk_text, glossary):
    """
    Hỗ trợ glossary trộn cả hai chiều (English = tiếng Trung và tiếng Trung = English).

    - Thuật ngữ chữ Hán: khớp theo chuỗi con.
    - Thuật ngữ Latin: chỉ khớp nguyên từ.
    - Cụm dài được xử lý trước và bị "che" khỏi văn bản; cụm ngắn chỉ được
      chọn nếu còn xuất hiện ở chỗ khác. Ví dụ 小夏海 khớp thì 小夏/夏海
      không bị đưa vào prompt trừ khi chúng còn xuất hiện riêng lẻ.
    """
    candidates = []
    for item in glossary:
        src, dst, info = item["src"], item["dst"], item.get("info")
        if term_in_text(src, chunk_text):
            candidates.append((src, dst, info))
        elif term_in_text(dst, chunk_text):
            candidates.append((dst, src, info))

    # sort ổn định: cụm dài trước, cùng độ dài giữ thứ tự glossary (quy tắc đầu thắng).
    candidates.sort(key=lambda c: len(c[0]), reverse=True)

    remaining = chunk_text
    matched = {}
    for source_term, target_term, info in candidates:
        if source_term in matched:
            continue
        if not term_in_text(source_term, remaining):
            continue
        entry = {"src": source_term, "dst": target_term}
        if info:
            entry["info"] = info
        matched[source_term] = entry
        remaining = mask_term(source_term, remaining)

    return list(matched.values())


# ============================================================
# 4. LOG + OLLAMA + PROMPT + GIỚI HẠN TỐC ĐỘ + KIỂM TRA CHẤT LƯỢNG
# ============================================================

LOGGER = logging.getLogger("translator")


class _FileFormatter(logging.Formatter):
    """Định dạng log file: thời gian | mức | luồng | thông điệp | key=value (chunk, retry, ratio...)."""

    def format(self, record):
        base = super().format(record)
        fields = getattr(record, "fields", None)
        if fields:
            base += " | " + " ".join(f"{k}={v}" for k, v in fields.items())
        return base


def setup_logging():
    """Console in nguyên văn thông điệp (giao diện parse log theo mẫu chữ) + file log chi tiết."""
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.propagate = False
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        handler.close()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(console)
    try:
        log_path = Path(str(FILE_OUTPUT) + ".engine.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_FileFormatter("%(asctime)s | %(levelname)-7s | %(threadName)s | %(message)s"))
        LOGGER.addHandler(file_handler)
    except OSError as error:
        print(f"[Log] Không mở được file log: {error}", flush=True)


def log(level, message, **fields):
    """Ghi log; `fields` (chunk, attempt, ratio, status...) chỉ xuất hiện trong file log."""
    LOGGER.log(level, message, extra={"fields": fields})


def estimate_tokens(text):
    """Ước lượng thô số token: ký tự Hán ~TOKENS_PER_SOURCE_CHAR, ký tự khác ~0.4."""
    text = text or ""
    han = len(HAN_RE.findall(text))
    return int(han * TOKENS_PER_SOURCE_CHAR + (len(text) - han) * 0.4) + 1


def max_chars_for_context():
    """Số ký tự nguồn tối đa mỗi chunk để prompt + đầu ra còn nằm trong CONTEXT_TOKENS."""
    budget = CONTEXT_TOKENS * CONTEXT_SAFETY - PROMPT_OVERHEAD_TOKENS
    per_char = TOKENS_PER_SOURCE_CHAR + OUTPUT_TOKENS_PER_HAN
    return max(MIN_CHUNK_CHARS, int(budget / per_char))


def get_client():
    return OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        timeout=600.0,  # Chờ tối đa 10 phút cho model dịch mỗi chunk
        max_retries=0,
    )


class RateLimiter:
    """Cửa sổ trượt 60s cho RPM/TPM, an toàn đa luồng. Giá trị 0 = không giới hạn."""

    def __init__(self, rpm=0, tpm=0):
        self.rpm, self.tpm = int(rpm), int(tpm)
        self._lock = threading.Lock()
        self._events = deque()  # (thời điểm, token)

    def acquire(self, tokens=0):
        if self.rpm <= 0 and self.tpm <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                while self._events and now - self._events[0][0] >= 60:
                    self._events.popleft()
                wait = 0.0
                if self._events:
                    until_free = 60 - (now - self._events[0][0])
                    if self.rpm > 0 and len(self._events) >= self.rpm:
                        wait = max(wait, until_free)
                    if self.tpm > 0 and sum(t for _, t in self._events) + tokens > self.tpm:
                        wait = max(wait, until_free)
                if wait <= 0:
                    self._events.append((now, tokens))
                    return
            time.sleep(min(wait, 5.0) + 0.01)


RATE_LIMITER = RateLimiter(RPM_LIMIT, TPM_LIMIT)
API_SEMAPHORE = threading.BoundedSemaphore(max(1, int(WORKERS)))


def limited_create(client, **kwargs):
    """Gọi chat.completions.create (không stream) qua Semaphore + RateLimiter."""
    tokens = sum(estimate_tokens(m.get("content", "")) for m in kwargs.get("messages", []))
    with API_SEMAPHORE:
        RATE_LIMITER.acquire(tokens * 2)
        return client.chat.completions.create(**kwargs)


def instruction_text(strict=False):
    """Câu lệnh dịch kiểu HY-MT. `strict=True` dùng cho lần dịch lại: nhấn mạnh dịch 100%."""
    if strict:
        return (
            f"Dịch toàn bộ văn bản sau sang {TARGET_LANGUAGE}, dịch đầy đủ 100% từng câu từng đoạn, "
            "không tóm tắt, không bỏ sót, giữ nguyên số đoạn và định dạng, "
            "chỉ cần xuất ra kết quả dịch, không giải thích thêm:"
        )
    return (
        f"Dịch văn bản sau sang {TARGET_LANGUAGE}, "
        f"chỉ cần xuất ra kết quả dịch, không giải thích thêm:"
    )


def build_prompt(chunk_text, matched, hint_block=None, strict=False):
    """Ghép prompt: [gợi ý thuật ngữ] + [thuật ngữ khớp theo glossary cũ, bỏ cặp trùng] + câu lệnh + văn bản.

    Mẫu theo khuyến nghị Hunyuan-MT (không system prompt): khối "Tham khảo bản dịch sau đây:" với
    các dòng "<nguồn> dịch thành <đích>", rồi câu lệnh dịch, rồi văn bản nguồn.
    """
    instruction = instruction_text(strict)
    seen, lines = set(), []
    for item in matched:
        pair = (item["src"], item["dst"])
        if pair not in seen:
            seen.add(pair)
            lines.append(f"{pair[0]} dịch thành {pair[1]}")
    legacy_block = ("Tham khảo bản dịch sau đây:\n" + "\n".join(lines) + "\n\n") if lines else ""
    if not hint_block and not legacy_block:
        return f"{instruction}\n\n{chunk_text}"
    return f"{hint_block or ''}{legacy_block}{instruction}\n{chunk_text}"


def process_chunk_api(client, chunk_text, glossary=None, seed=None, hint_block=None,
                      temperature=None, strict=False):
    """Gọi Ollama ở chế độ streaming; chỉ trả về bản dịch hoàn chỉnh sau khi stream kết thúc.

    Ném InvalidTranslation nếu model dừng vì hết token (finish_reason == "length"), vì đó là
    bản dịch bị cắt cụt chắc chắn.
    """
    matched = relevant_glossary(chunk_text, glossary or [])
    user_prompt = build_prompt(chunk_text, matched, hint_block, strict)
    han = len(HAN_RE.findall(chunk_text))
    est_prompt = estimate_tokens(user_prompt)
    est_output = int(han * OUTPUT_TOKENS_PER_HAN) + 64
    if est_prompt + est_output > CONTEXT_TOKENS:
        log(logging.WARNING,
            f"    [Cảnh báo] Ước lượng prompt+đầu ra ~{est_prompt + est_output} token vượt "
            f"CONTEXT_TOKENS={CONTEXT_TOKENS}; nguy cơ bị cắt cụt.",
            est_tokens=est_prompt + est_output)

    request = dict(
        model=MODEL,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=TEMPERATURE if temperature is None else temperature,
        top_p=TOP_P,
        seed=seed,
        stream=True,
    )
    if MAX_OUTPUT_TOKENS_FACTOR > 0:
        request["max_tokens"] = int(est_output * MAX_OUTPUT_TOKENS_FACTOR) + 256

    started = time.time()
    first_token_time = None
    event_count = 0
    pieces = []
    finish_reason = None

    with API_SEMAPHORE:
        RATE_LIMITER.acquire(est_prompt + est_output)
        with client.chat.completions.create(**request) as stream:
            for event in stream:
                event_count += 1
                if not event.choices:
                    continue
                choice = event.choices[0]
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                piece = choice.delta.content
                if piece:
                    if first_token_time is None:
                        first_token_time = time.time() - started
                        log(logging.INFO, f"    Model bắt đầu trả nội dung sau {first_token_time:.1f}s.")
                    pieces.append(piece)
                if event_count % 50 == 0:
                    log(logging.DEBUG,
                        f"    Đang nhận dữ liệu: {event_count} sự kiện, {time.time() - started:.1f}s...")

    result = "".join(pieces).strip()
    cleaned = _strip_think(result)  # bỏ khối <think>...</think>
    if "</think>" in cleaned.lower():  # chỉ còn thẻ đóng lẻ
        cleaned = re.split(r"</think>", cleaned, flags=re.I)[-1].strip()
    if "<think>" in cleaned.lower():  # mở mà không đóng: model vẫn đang nghĩ
        raise InvalidTranslation("model vẫn suy luận (thẻ <think> không đóng)")
    result = cleaned
    elapsed = time.time() - started
    if not result:
        raise RuntimeError(f"Ollama trả nội dung rỗng sau {elapsed:.1f}s ({event_count} sự kiện stream).")
    if finish_reason == "length":
        raise InvalidTranslation(
            "model dừng vì hết token (finish_reason=length): bản dịch bị cắt cụt; "
            "kiểm tra CONTEXT_TOKENS/num_ctx hoặc giảm MAX_CHARS"
        )
    if first_token_time is not None:
        log(logging.INFO, f"    Hoàn tất stream: {elapsed:.1f}s; độ trễ token đầu tiên: {first_token_time:.1f}s")
    else:
        log(logging.INFO, f"    Hoàn tất stream: {elapsed:.1f}s")
    return result


# ---------------------------------------------------------------------------
# Kiểm tra chất lượng
# ---------------------------------------------------------------------------

_INTRO_RE = re.compile(
    r"^[\s*_`>#]*(?:(?:dưới đây|sau đây|đây) là|bản dịch(?: tiếng việt)?\s*[:：]|kết quả dịch\s*[:：]|"
    r"translation\s*[:：]|here(?: is|'s)\b|sure\b|certainly\b|of course\b)",
    re.IGNORECASE,
)
_OUTRO_RE = re.compile(
    r"^[\s*_`(\[（—\-]*(?:lưu ý|ghi chú|chú thích|translator'?s? note|note)\s*[:：]|"
    r"hy vọng bản dịch|nếu bạn cần (?:chỉnh|thêm|tôi)|hãy cho tôi biết|i hope this|let me know",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,200}>")
_DIALOGUE_OPENERS = ("“", "”", '"', "「", "『", "—", "–", "-", "‘", "'", "«")


@dataclass
class QualityReport:
    """Kết quả kiểm tra một bản dịch."""
    hard: list = field(default_factory=list)   # lý do loại bản dịch
    soft: list = field(default_factory=list)   # chỉ cảnh báo
    ratio: float = 0.0

    @property
    def ok(self):
        return not self.hard


def length_ratio(source, result):
    """Độ dài bản dịch / số chữ Hán của nguồn (nếu nguồn chủ yếu là chữ Hán), ngược lại / số ký tự
    không-khoảng-trắng của nguồn. Cùng quy tắc với TranslationGuard."""
    stripped = re.sub(r"\s+", "", source or "")
    han = len(HAN_RE.findall(stripped))
    base = han if han >= len(stripped) * 0.3 else len(stripped)
    return len(result or "") / max(1, base)


def detect_repetition(source, result):
    """Phát hiện vòng lặp: câu/dòng giống hệt lặp quá nhiều so với nguồn, hoặc cụm lặp liên tiếp."""
    if re.search(r"(.{15,120}?)\1{3,}", result, re.S) and not re.search(r"(.{4,120}?)\1{3,}", source, re.S):
        return "có cụm câu lặp liên tiếp (nghi kẹt vòng lặp)"
    def sentences(text, min_len):
        return [s.strip() for s in re.split(r"[.!?…。！？\n]+", text) if len(s.strip()) >= min_len]
    counts = Counter(sentences(result, 12))
    if counts:
        _text, n = counts.most_common(1)[0]
        # So với mức lặp lớn nhất của chính nguồn (lời thoại lặp như "好的。好的。" là hợp lệ).
        src_counts = Counter(sentences(source, 2))
        src_max = max(src_counts.values(), default=0)
        if n >= max(4, src_max + 3):
            return f"một câu bị lặp {n} lần"
    return None


def check_translation(source, result, terms=None, min_source_len=None):
    """Kiểm tra bản dịch; trả về QualityReport (hard = loại, soft = cảnh báo).

    Gồm: rỗng, lọt chỉ dẫn/glossary, lời dẫn đầu/cuối thừa, tỉ lệ độ dài (cụt/lặp), vòng lặp,
    mất thẻ HTML, và các kiểm tra của TranslationGuard (lệch số dòng, thiếu thuật ngữ...).
    KHÔNG xét chữ Hán còn sót (đã có fix_han_leftovers).
    """
    report = QualityReport()
    result = result or ""
    if not result.strip():
        report.hard.append("bản dịch rỗng")
        return report

    for marker in LEAK_MARKERS:
        if marker in result and marker not in source:
            report.hard.append(f"bản dịch lọt chỉ dẫn/glossary (gặp '{marker}')")
            break

    lines = [ln.strip() for ln in result.splitlines() if ln.strip()]
    if lines:
        first, last = lines[0], lines[-1]
        if not first.startswith(_DIALOGUE_OPENERS) and _INTRO_RE.match(first) and not _INTRO_RE.match(source.strip()):
            report.hard.append(f"có lời dẫn thừa ở đầu: {first[:60]!r}")
        if len(lines) > 1 and not last.startswith(_DIALOGUE_OPENERS) and len(last) <= 200 \
                and _OUTRO_RE.search(last) and not _OUTRO_RE.search(source):
            report.hard.append(f"có lời kết/ghi chú thừa ở cuối: {last[:60]!r}")

    report.ratio = length_ratio(source, result)
    if len(source) >= (MIN_SOURCE_LEN_CHECK if min_source_len is None else min_source_len):
        if report.ratio < MIN_RATIO * LENGTH_HARD_FACTOR:
            report.hard.append(
                f"bản dịch quá ngắn so với nguồn (tỉ lệ {report.ratio:.2f} theo chữ Hán), nghi bị cụt/tóm tắt")
        elif report.ratio > MAX_RATIO:
            report.hard.append(f"bản dịch quá dài so với nguồn (tỉ lệ {report.ratio:.2f}), nghi bị lặp")
        elif report.ratio < MIN_RATIO:
            report.soft.append(f"bản dịch hơi ngắn (tỉ lệ {report.ratio:.2f} < {MIN_RATIO})")

    repeated = detect_repetition(source, result)
    if repeated:
        report.hard.append(repeated)

    src_tags, res_tags = Counter(_TAG_RE.findall(source)), Counter(_TAG_RE.findall(result))
    missing = src_tags - res_tags
    if missing:
        report.hard.append("mất thẻ định dạng HTML: " + ", ".join(list(missing)[:3]))
    elif res_tags - src_tags:
        report.soft.append("có thẻ HTML lạ không có trong nguồn")

    guard = globals().get("TRANSLATION_GUARD")
    if guard is not None:
        try:
            for issue in guard.inspect(source, result, terms):
                if issue.code in ("TOO_SHORT", "TOO_LONG"):
                    continue  # đã kiểm tra ở trên
                text = f"{issue.code}: {issue.message}"
                if issue.code == "REPEAT" and any("lặp" in h for h in report.hard):
                    continue
                (report.hard if issue.severity == "hard" else report.soft).append(text)
        except Exception as error:  # guard lỗi không được làm hỏng pipeline
            log(logging.WARNING, f"    [Guard lỗi] {error}")
    return report


def validate_translation(source, result, terms=None):
    """Ném InvalidTranslation nếu có lỗi hard; trả về danh sách cảnh báo soft."""
    report = check_translation(source, result, terms)
    if report.hard:
        raise InvalidTranslation("; ".join(report.hard))
    return report.soft


def restore_format(source, result):
    """Áp lại thụt lề đầu dòng của nguồn vào bản dịch khi số dòng khớp nhau (model hay làm rơi thụt lề)."""
    if not PRESERVE_INDENT:
        return result
    src_lines = [ln for ln in source.split("\n") if ln.strip()]
    res_lines = result.split("\n")
    non_empty = [i for i, ln in enumerate(res_lines) if ln.strip()]
    if len(src_lines) != len(non_empty):
        return result
    for src_line, idx in zip(src_lines, non_empty):
        indent = src_line[: len(src_line) - len(src_line.lstrip())]
        if indent and not res_lines[idx][:1].isspace():
            res_lines[idx] = indent + res_lines[idx].lstrip()
    return "\n".join(res_lines)

_HEADING_PREFIX_RE = re.compile(r"^(?:\s*#+\s*)+")


def restore_chapter_heading(source, result):
    """Nếu nguồn bắt đầu bằng tiêu đề '##', đảm bảo dòng đầu của bản dịch cũng bắt đầu bằng '## '.

    Model hay làm rơi '##' (hoặc đổi thành '#', '###', '##Chương'). Chỉ áp dụng cho
    chunk/mảnh có nguồn mở đầu bằng '##', nên các mảnh giữa chương không bị ảnh hưởng.
    """
    src_first = source.lstrip().split("\n", 1)[0].strip()
    if not src_first.startswith("##"):
        return result
    marker = re.match(r"#+", src_first).group(0)
    stripped = result.lstrip("\n")
    first, sep, rest = stripped.partition("\n")
    title = _HEADING_PREFIX_RE.sub("", first).strip()
    if not title:
        return result
    return f"{marker} {title}{sep}{rest}"

# ============================================================
# 5. CHIA CHƯƠNG THÀNH CHUNK (thông minh: đoạn -> câu -> mệnh đề)
# ============================================================

_SENT_END = "。！？!?…"
_SENT_CLOSE = "”’」』）)\"'】》"
_CLAUSE_END = "，,、；;：:"


def split_sentences(text):
    """Tách câu tại 。！？!?… và gắn kèm các dấu đóng ngoặc/nháy đi liền sau (vd “……。” )."""
    parts, buf, i, n = [], [], 0, len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        i += 1
        if ch in _SENT_END:
            while i < n and text[i] in _SENT_END:
                buf.append(text[i])
                i += 1
            while i < n and text[i] in _SENT_CLOSE:
                buf.append(text[i])
                i += 1
            parts.append("".join(buf))
            buf = []
    if buf:
        parts.append("".join(buf))
    return parts


def split_clauses(text):
    """Tách tại dấu phẩy/chấm phẩy/hai chấm (dùng khi một câu đơn vượt giới hạn)."""
    parts, buf = [], []
    for ch in text:
        buf.append(ch)
        if ch in _CLAUSE_END:
            parts.append("".join(buf))
            buf = []
    if buf:
        parts.append("".join(buf))
    return parts


def pack_text(text, limit):
    """Chia một đoạn dài thành các mảnh <= limit: ưu tiên ranh giới câu, rồi mệnh đề, cuối cùng mới cắt cứng."""
    if len(text) <= limit:
        return [text]
    atoms = []
    for sentence in split_sentences(text):
        if len(sentence) <= limit:
            atoms.append(sentence)
            continue
        for clause in split_clauses(sentence):
            if len(clause) <= limit:
                atoms.append(clause)
            else:
                atoms.extend(clause[i:i + limit] for i in range(0, len(clause), limit))
    pieces, current = [], ""
    for atom in atoms:
        if current and len(current) + len(atom) > limit:
            pieces.append(current)
            current = atom
        else:
            current += atom
    if current:
        pieces.append(current)
    return pieces


def split_into_chunks(content, max_chars=MAX_CHARS):
    """
    Nhận diện chương bằng dòng bắt đầu với ##. Chia tại ranh giới đoạn văn; đoạn đơn vượt giới hạn
    được chia tiếp tại ranh giới câu (rồi mệnh đề) thay vì để nguyên làm chunk khổng lồ.
    Giới hạn thực = min(max_chars, giới hạn suy ra từ CONTEXT_TOKENS). Tiêu đề chỉ nằm ở chunk đầu
    của mỗi chương. Thụt lề đầu dòng được giữ nếu PRESERVE_INDENT.
    """
    limit = max(MIN_CHUNK_CHARS, min(int(max_chars), max_chars_for_context()))
    chunks = []

    def append_chapter(title, body, number):
        if title is None:
            return
        title_line = title.strip()
        paragraphs = [p for p in body if p.strip()]
        if not paragraphs:
            chunks.append({"chapter": number, "part": 1, "total_parts": 1, "text": title_line})
            return
        prefix = title_line + "\n\n"
        first_limit = max(MIN_CHUNK_CHARS, limit - len(prefix))
        units = []
        for paragraph in paragraphs:
            for k, segment in enumerate(pack_text(paragraph, limit)):
                units.append((segment, k > 0))  # k > 0: phần tiếp của cùng một đoạn
        parts, current = [], ""
        for segment, continuation in units:
            cap = first_limit if not parts else limit
            sep = "" if continuation else "\n\n"
            if current and len(current) + len(sep) + len(segment) > cap:
                parts.append(current)
                current = segment
            elif current:
                current += sep + segment
            else:
                current = segment
        if current:
            parts.append(current)
        for number_part, part in enumerate(parts, 1):
            chunks.append({
                "chapter": number, "part": number_part, "total_parts": len(parts),
                "text": prefix + part if number_part == 1 else part,
            })

    current_title, current_body, chapter_number, orphan_lines = None, [], 0, 0
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            append_chapter(current_title, current_body, chapter_number)
            current_title, current_body = stripped, []
            chapter_number += 1
        elif current_title is None:
            orphan_lines += 1 if stripped else 0
        elif stripped:
            current_body.append(line.rstrip() if PRESERVE_INDENT else stripped)
    append_chapter(current_title, current_body, chapter_number)
    if orphan_lines:
        log(logging.WARNING, f"[Chunk] Bỏ qua {orphan_lines} dòng nằm trước tiêu đề chương '##' đầu tiên.")
    for index, chunk in enumerate(chunks):
        chunk["index"] = index
    return chunks


def chunk_id(chunk):
    raw = f"{chunk['chapter']}|{chunk['part']}|{chunk['text']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def chunks_fingerprint(chunks):
    combined = "\n".join(chunk_id(chunk) for chunk in chunks)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ============================================================
# 6. TIẾN TRÌNH + LOG LỖI
# ============================================================

def load_progress(chunks):
    if not FILE_PROGRESS.exists():
        return 0

    try:
        saved = FILE_PROGRESS.read_text(encoding="utf-8").strip()
        try:
            data = json.loads(saved)
        except json.JSONDecodeError:
            # Tương thích với progress dạng số từ code cũ.
            data = int(saved)

        if isinstance(data, int):
            saved_index = data
            old_fingerprint = None
        else:
            saved_index = int(data.get("next_chunk", 0))
            old_fingerprint = data.get("fingerprint")

        current_fingerprint = chunks_fingerprint(chunks)
        if old_fingerprint and old_fingerprint != current_fingerprint:
            print("[Progress] Nội dung/danh sách chunk khác lần chạy trước.")
            try:
                answer = input("Vẫn tiếp tục theo index cũ? (y/n): ").strip().lower()
            except EOFError:
                print("[Progress] Không có đầu vào để xác nhận; dừng để tránh dịch trùng/lệch output.")
                sys.exit(2)
            if answer != "y":
                print("Không sử dụng progress cũ.")
                return 0

        return max(0, min(saved_index, len(chunks)))
    except Exception as error:
        print(f"[Progress] Không đọc được tiến trình: {error}")
        return 0


def save_progress(next_chunk, chunks):
    data = {
        "next_chunk": next_chunk,
        "fingerprint": chunks_fingerprint(chunks),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    atomic_write_text(FILE_PROGRESS, json.dumps(data, ensure_ascii=False, indent=2))


def log_error(index, chunk, error):
    FILE_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FILE_ERROR_LOG, "a", encoding="utf-8") as file:
        file.write("\n" + "=" * 80 + "\n")
        file.write(f"Chunk: {index + 1}\n")
        file.write(f"Chapter: {chunk['chapter']}\n")
        file.write(f"Part: {chunk['part']}/{chunk['total_parts']}\n")
        file.write(f"Error: {error}\n")
        file.write("-" * 80 + "\n")
        file.write(chunk["text"] + "\n")


# ============================================================
# 7. RETRY VÀ DỊCH LẠI CHUNK THẤT BẠI
# ============================================================

def classify_error(error):
    """Phân loại lỗi API: ưu tiên mã HTTP (status_code), sau đó mới đoán theo nội dung thông báo."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        if status == 429:
            return "RATE_LIMIT"
        if status >= 500:
            return "SERVER"
    message = str(error).lower()
    if any(x in message for x in ("429", "rate limit", "too many requests")):
        return "RATE_LIMIT"
    if any(x in message for x in ("500", "502", "503", "504", "server error", "temporarily unavailable")):
        return "SERVER"
    if any(x in message for x in ("timeout", "timed out", "connection", "network", "refused")):
        return "NETWORK"
    return "OTHER"


def backoff_delay(kind, attempt, error=None):
    """Exponential backoff có jitter; tôn trọng header Retry-After nếu server gửi."""
    delay = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
    if kind == "RATE_LIMIT":
        delay = max(delay, 5.0 * attempt)
    delay = min(delay, RETRY_BACKOFF_MAX)
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers:
        try:
            delay = max(delay, min(float(headers.get("retry-after", 0)), RETRY_BACKOFF_MAX))
        except (TypeError, ValueError):
            pass
    return delay * random.uniform(0.85, 1.15)


def _han_word_by_word_fix(text, terms=None, glossary=None):
    """Thay các cụm chữ Hán còn sót bằng bản dịch từ từ điển (glossary + Moegirl).

    Ưu tiên khớp cụm dài trước ("萧炎" trước "萧"); chỉ thay mục có đích KHÔNG phải
    chữ Hán (không thay Hán bằng Hán). Trả về (văn_bản_đã_sửa, số_cụm_đã_thay).
    """
    lookup: dict[str, str] = {}

    def add(src, dst):
        src = str(src or "").strip()
        dst = str(dst or "").strip()
        if not src or not dst:
            return
        if not HAN_RE.search(src) or HAN_RE.search(dst) or src == dst:
            return
        lookup[src] = dst

    if terms is not None:
        for item in getattr(terms, "glossary", None) or []:
            if isinstance(item, dict):
                add(item.get("src"), item.get("dst"))
    if glossary:
        for item in glossary:
            if isinstance(item, dict):
                add(item.get("src"), item.get("dst"))

    if not lookup:
        return text, 0

    # Cụm dài đứng trước trong alternation để regex chọn khớp dài nhất.
    words = sorted(lookup, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(word) for word in words))

    replaced = 0

    def repl(match: re.Match) -> str:
        nonlocal replaced
        replaced += 1
        return lookup[match.group(0)]

    return pattern.sub(repl, text), replaced


# ------------------------------------------------------------
# Vá chữ Hán còn sót bằng model (không loại chunk vì chữ Hán nữa)
# ------------------------------------------------------------

HAN_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
# Số vòng gọi model để dịch các cụm Hán còn sót (mỗi vòng chỉ gửi cụm còn lại).
HAN_REPAIR_ROUNDS = 3


def _han_context(text, run, width=40):
    pos = text.find(run)
    start = max(0, pos - width)
    end = min(len(text), pos + len(run) + width)
    return text[start:end].replace("\n", " ")


def _ask_model_for_han_runs(client, text, runs, seed=None):
    """Gửi các cụm Hán kèm câu ngữ cảnh; trả về {cụm_Hán: bản_dịch_Việt}."""
    items = [
        f"{i}. Cụm: {run}\n   Câu: {_han_context(text, run)}"
        for i, run in enumerate(runs, 1)
    ]
    prompt = (
        f"Trong các câu {TARGET_LANGUAGE} dưới đây còn sót cụm chữ Hán chưa dịch. "
        f"Hãy dịch riêng từng cụm chữ Hán sang {TARGET_LANGUAGE} sao cho thay vào câu "
        "vẫn tự nhiên. Tên người, địa danh, chiêu thức, danh xưng dùng âm Hán-Việt. "
        "Mỗi cụm trả về đúng một dòng theo dạng: cụm chữ Hán => bản dịch. "
        "Bản dịch không được chứa chữ Hán. Không giải thích thêm.\n\n"
        + "\n".join(items)
    )
    response = limited_create(
        client,
        model=MODEL,
        temperature=0.1,
        seed=seed,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.choices[0].message.content or ""
    wanted = set(runs)
    mapping = {}
    for line in raw.splitlines():
        if "=>" not in line:
            continue
        left, right = line.split("=>", 1)
        # Lấy lại phần chữ Hán của vế trái (bỏ số thứ tự, "Cụm:", dấu ngoặc...).
        key = "".join(HAN_RUN_RE.findall(left))
        value = right.strip().strip("\"'“”「」*`")
        if (key in wanted and value and "\n" not in value
                and len(value) <= 80 and not HAN_RE.search(value)):
            mapping[key] = value
    return mapping


def _rewrite_lines_with_han(client, result, tries=3):
    """Dịch lại nguyên dòng còn chữ Hán; nhận kết quả nếu số chữ Hán giảm."""
    lines = result.split("\n")
    for i, line in enumerate(lines):
        if not HAN_RE.search(line):
            continue
        runs = sorted(set(HAN_RUN_RE.findall(line)), key=len, reverse=True)
        prompt = (
            f"Dòng {TARGET_LANGUAGE} sau còn sót đoạn chữ Hán chưa dịch: {', '.join(runs)}.\n"
            f"Hãy dịch đoạn đó sang {TARGET_LANGUAGE} cho tự nhiên và viết lại cả dòng, "
            "giữ nguyên phần đã dịch, tên riêng dùng âm Hán-Việt. "
            "Kết quả không được chứa chữ Hán. Chỉ xuất ra dòng đã viết lại, không giải thích:\n\n"
            + line
        )
        best, best_han = None, len(HAN_RE.findall(line))
        for t in range(tries):
            try:
                resp = limited_create(
                    client, model=MODEL, temperature=0.3, seed=3000 + t,
                    messages=[{"role": "user", "content": prompt}],
                )
                new = (resp.choices[0].message.content or "").strip()
            except Exception as error:
                print(f"    [Viết lại dòng lỗi] {error}")
                continue
            new = new.strip("\"'“”*` ")
            han_new = len(HAN_RE.findall(new))
            if new and "\n" not in new and len(new) >= len(line) * 0.5 and han_new < best_han:
                best, best_han = new, han_new
                if han_new == 0:
                    break
            else:
                print(f"    [Viết lại dòng bị loại, lần {t + 1}] {new[:120]!r}")
        if best:
            lines[i] = best
    return "\n".join(lines)


def fix_han_leftovers(client, result, terms=None, glossary=None):
    """Dịch các cụm Hán còn sót. KHÔNG bao giờ ném lỗi; hết lượt thì giữ nguyên."""
    if not HAN_RE.search(result):
        return result

    # Bước rẻ: dùng từ điển (glossary + Moegirl) trước, không tốn lượt gọi model.
    result, replaced = _han_word_by_word_fix(result, terms, glossary)
    if replaced:
        print(f"    Đã thay {replaced} cụm chữ Hán bằng từ điển.")

    # Thuật ngữ được phép giữ chữ Hán thì không coi là "sót".
    allowed = set(terms.han_dsts()) if terms is not None else set()

    for round_no in range(1, HAN_REPAIR_ROUNDS + 1):
        runs = sorted(
            {r for r in HAN_RUN_RE.findall(result) if r not in allowed},
            key=len, reverse=True,
        )
        if not runs:
            return result
        print(f"    Còn {len(runs)} cụm chữ Hán; nhờ model dịch (vòng {round_no}/{HAN_REPAIR_ROUNDS})...")
        try:
            mapping = _ask_model_for_han_runs(client, result, runs, seed=2000 + round_no)
        except Exception as error:
            print(f"    [Vá chữ Hán lỗi] {error}")
            time.sleep(3)
            continue
        if not mapping:
            continue
        pattern = re.compile("|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)))
        result = pattern.sub(lambda m: mapping[m.group(0)], result)

    # Chạy MỘT lần sau khi hết các vòng vá cụm: viết lại nguyên dòng còn chữ Hán.
    if any(r not in allowed for r in HAN_RUN_RE.findall(result)):
        print("    Vá từng cụm chưa hết; nhờ model viết lại nguyên dòng còn chữ Hán...")
        result = _rewrite_lines_with_han(client, result)

    left = sorted({r for r in HAN_RUN_RE.findall(result) if r not in allowed})
    if left:
        print(f"    [Cảnh báo] Vẫn còn cụm chữ Hán sau {HAN_REPAIR_ROUNDS} vòng: {', '.join(left[:10])}")
        try:
            with open(FILE_ERROR_LOG, "a", encoding="utf-8") as file:
                file.write(f"\n[HAN_LEFT] {', '.join(left)}\n")
        except OSError:
            pass
    return result


@dataclass
class ChunkTrace:
    """Nhật ký các lần thử của một chunk, dùng để ghi failed_chunks.json khi không cứu được."""
    index: int
    chunk: dict
    attempts: list = field(default_factory=list)
    best_text: Optional[str] = None
    best_score: tuple = (10 ** 9, 10 ** 9)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, label, attempt, kind, message, **extra):
        entry = {"label": label, "attempt": attempt, "kind": kind, "message": message[:500],
                 "at": datetime.now().isoformat(timespec="seconds"), **extra}
        with self.lock:
            self.attempts.append(entry)

    def offer_best(self, text, report):
        """Giữ bản thử 'ít tệ nhất' (ít lỗi hard, rồi ít lỗi soft) để người dùng sửa tay."""
        score = (len(report.hard), len(report.soft))
        with self.lock:
            if text and score < self.best_score:
                self.best_text, self.best_score = text, score


class FailedChunkStore:
    """failed_chunks.json: các chunk không cứu được kèm lịch sử lỗi (nằm cạnh file bản dịch)."""

    def __init__(self):
        self._lock = threading.Lock()

    @staticmethod
    def path():
        return Path(FILE_OUTPUT).parent / "failed_chunks.json"

    def _load(self):
        try:
            data = json.loads(self.path().read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("chunks"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"chunks": []}

    def _save(self, data):
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        data["output"] = str(FILE_OUTPUT)
        data["count"] = len(data["chunks"])
        atomic_write_text(self.path(), json.dumps(data, ensure_ascii=False, indent=2))

    def add(self, trace, reason):
        chunk = trace.chunk
        entry = {
            "chunk": trace.index + 1, "chapter": chunk.get("chapter"), "part": chunk.get("part"),
            "total_parts": chunk.get("total_parts"), "reason": reason,
            "failed_at": datetime.now().isoformat(timespec="seconds"),
            "source_chars": len(chunk["text"]), "source": chunk["text"],
            "best_candidate": trace.best_text, "attempts": trace.attempts[-60:],
        }
        with self._lock:
            data = self._load()
            data["chunks"] = [c for c in data["chunks"] if c.get("chunk") != entry["chunk"]] + [entry]
            data["chunks"].sort(key=lambda c: c.get("chunk", 0))
            self._save(data)

    def resolve(self, index):
        with self._lock:
            if not self.path().is_file():
                return
            data = self._load()
            kept = [c for c in data["chunks"] if c.get("chunk") != index + 1]
            if len(kept) != len(data["chunks"]):
                data["chunks"] = kept
                self._save(data)


FAILED_STORE = FailedChunkStore()


def bisect_source(text):
    """Chia nguồn làm hai nửa cân bằng. Trả về (trái, phải, dấu nối) hoặc None nếu không chia được.

    Ưu tiên ranh giới đoạn (\\n\\n), rồi câu, rồi mệnh đề.
    """
    def best_cut(pieces, joiner):
        if len(pieces) < 2:
            return None
        total = sum(len(p) for p in pieces)
        acc, best_k, best_dist = 0, None, None
        for k in range(1, len(pieces)):
            acc += len(pieces[k - 1])
            dist = abs(acc - total / 2)
            if best_dist is None or dist < best_dist:
                best_k, best_dist = k, dist
        left, right = "".join(pieces[:best_k]).strip(), "".join(pieces[best_k:]).strip()
        if left and right:
            return left, right, joiner
        return None

    paragraphs = text.split("\n\n")
    if len(paragraphs) >= 2:
        cut = best_cut([p + "\n\n" for p in paragraphs[:-1]] + [paragraphs[-1]], "\n\n")
        if cut:
            return cut
    for splitter, joiner in ((split_sentences, " "), (split_clauses, " ")):
        cut = best_cut(splitter(text), joiner)
        if cut:
            return cut
    return None


def translate_text(client, text, glossary, label, terms, trace, depth=0):
    """Dịch một đoạn văn bản có kiểm tra; retry + giảm temperature + prompt nghiêm ngặt + bisect.

    Trả về bản dịch hoặc None nếu thất bại hoàn toàn.
    """
    invalid_count = 0
    index = trace.index
    for attempt in range(1, MAX_RETRIES_PER_CHUNK + 1):
        strict = invalid_count > 0 or depth > 0
        if invalid_count == 0:
            temperature = TEMPERATURE
        else:
            temperature = RETRY_TEMPERATURES[min(invalid_count - 1, len(RETRY_TEMPERATURES) - 1)]
        # Lần đầu để Ollama tự chọn seed; mỗi lần bị loại thì đổi seed.
        seed = None if invalid_count == 0 else 1000 + invalid_count + 100 * depth
        extra_info = ("" if seed is None else f" (seed {seed})") + \
                     (f" [nghiêm ngặt, T={temperature}]" if strict else "")
        log(logging.INFO, f"    Gọi model {MODEL}, lần {attempt}/{MAX_RETRIES_PER_CHUNK}{extra_info}...",
            chunk=index + 1, attempt=attempt, depth=depth, temperature=temperature)
        try:
            if terms is not None:
                prepared, _used = terms.apply_forced_terms(text)
                hint_block = terms.build_hint_block(text)
            else:
                prepared, hint_block = text, None
            addressing_hint = hint_for_chunk(ADDRESSING_RULES, trace.chunk.get("chapter"), text)
            hint_block = addressing_hint + (hint_block or "")
            raw = process_chunk_api(client, prepared, glossary, seed, hint_block=hint_block,
                                    temperature=temperature, strict=strict)
            # Mảnh sau bisect ngắn hơn MIN_SOURCE_LEN_CHECK vẫn phải được kiểm tra độ dài (ngưỡng thấp hơn),
            # nếu không bản dịch cụt của mảnh nhỏ sẽ lọt qua.
            min_len = MIN_SOURCE_LEN_CHECK if depth == 0 else max(30, MIN_SOURCE_LEN_CHECK // 3)
            report = check_translation(text, raw, terms, min_source_len=min_len)
            trace.offer_best(raw, report)
            log(logging.DEBUG, "    Kết quả kiểm tra", chunk=index + 1, attempt=attempt,
                ratio=f"{report.ratio:.2f}", hard=len(report.hard), soft=len(report.soft), depth=depth)
            if report.hard:
                raise InvalidTranslation("; ".join(report.hard))
            for warning in report.soft:
                log(logging.WARNING, f"    [Guard cảnh báo] {warning}", chunk=index + 1, ratio=f"{report.ratio:.2f}")
            # Đạt các kiểm tra khác: vá nốt chữ Hán sót, rồi áp lại thụt lề.
            result = restore_chapter_heading(
                text, restore_format(text, fix_han_leftovers(client, raw, terms, glossary))
            )
            time.sleep(DELAY_BETWEEN_CALLS)
            return result
        except InvalidTranslation as error:
            invalid_count += 1
            trace.record(label, attempt, "INVALID", str(error), depth=depth, temperature=temperature)
            log(logging.WARNING, f"    [Bản dịch bị loại ở {label}] lần {attempt}: {error}",
                chunk=index + 1, attempt=attempt, depth=depth)
            if invalid_count >= MAX_INVALID_RETRIES or attempt >= MAX_RETRIES_PER_CHUNK:
                break
            wait = min(RETRY_INVALID_DELAY * (2 ** (invalid_count - 1)), 20.0)
            log(logging.INFO, f"    Chờ {wait:.0f}s rồi dịch lại (temperature thấp hơn, prompt nghiêm ngặt)...")
            time.sleep(wait)
        except Exception as error:  # lỗi API/mạng
            kind = classify_error(error)
            status = getattr(error, "status_code", None)
            trace.record(label, attempt, kind, str(error), status=status, depth=depth)
            log(logging.ERROR, f"    [Lỗi {kind} ở {label}] lần {attempt}: {error}",
                chunk=index + 1, attempt=attempt, status=status, depth=depth)
            if attempt >= MAX_RETRIES_PER_CHUNK:
                break
            wait = backoff_delay(kind, attempt, error)
            log(logging.INFO, f"    Chờ {wait:.0f}s rồi thử lại...")
            time.sleep(wait)

    # Hết lượt retry: chia đôi và dịch từng nửa.
    if depth < MAX_BISECT_DEPTH and len(text) >= BISECT_MIN_CHARS:
        halves = bisect_source(text)
        if halves:
            left_src, right_src, joiner = halves
            log(logging.WARNING,
                f"    [Bisect] {label}: chia đôi ({len(left_src)} + {len(right_src)} ký tự), tầng {depth + 1}.",
                chunk=index + 1, depth=depth + 1)
            trace.record(label, 0, "BISECT", f"chia đôi {len(left_src)}+{len(right_src)} ký tự", depth=depth + 1)
            left = translate_text(client, left_src, glossary, f"{label} (nửa 1, tầng {depth + 1})",
                                  terms, trace, depth + 1)
            if left is None:
                return None
            right = translate_text(client, right_src, glossary, f"{label} (nửa 2, tầng {depth + 1})",
                                   terms, trace, depth + 1)
            if right is None:
                return None
            return left + joiner + right
    return None


def translate_with_retry(client, chunk, glossary, label, terms=None):
    """Dịch một chunk; thất bại hoàn toàn thì ghi failed_chunks.json và trả về None."""
    index = chunk.get("index", 0)
    trace = ChunkTrace(index=index, chunk=chunk)
    result = translate_text(client, chunk["text"], glossary, label, terms, trace)
    if result is None:
        reason = trace.attempts[-1]["message"] if trace.attempts else "không rõ"
        try:
            FAILED_STORE.add(trace, reason)
        except OSError as error:
            log(logging.ERROR, f"    [failed_chunks] Không ghi được: {error}")
        log(logging.ERROR, f"    {label}: hết số lần thử (đã thử retry + bisect); xem {FailedChunkStore.path().name}.",
            chunk=index + 1)
    else:
        try:
            FAILED_STORE.resolve(index)
        except OSError:
            pass
    return result


def _translate_one(client, chunks, index, glossary, terms):
    """Công việc của một luồng: dịch chunk `index`. Không bao giờ ném lỗi ra ngoài."""
    chunk = chunks[index]
    log(logging.INFO, "\n" + "=" * 70)
    log(logging.INFO,
        f"Chunk {index + 1}/{len(chunks)} | Chương {chunk['chapter']} | "
        f"Part {chunk['part']}/{chunk['total_parts']} | {len(chunk['text'])} ký tự")
    log(logging.INFO, f"Thuật ngữ khớp: {len(relevant_glossary(chunk['text'], glossary))}")
    started = time.time()
    try:
        result = translate_with_retry(client, chunk, glossary, f"chunk {index + 1}", terms)
    except Exception as error:  # chặn lỗi bất ngờ để luồng chính vẫn ghi nhãn chưa dịch
        log(logging.ERROR, f"    [Lỗi OTHER ở chunk {index + 1}] lần 0: {error}", chunk=index + 1)
        result = None
    return result, time.time() - started


def run_translation(client, chunks, start_index, glossary, terms):
    """Dịch từ start_index tới hết. Tối đa WORKERS chunk chạy song song, nhưng GHI output và
    checkpoint theo đúng thứ tự (giữ nguyên hành vi tạm dừng/dừng của giao diện)."""
    durations = []
    total = len(chunks)
    workers = max(1, int(WORKERS))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chunk")
    futures = {}
    next_submit = start_index
    try:
        for index in range(start_index, total):
            while next_submit < total and len(futures) < workers:
                futures[next_submit] = executor.submit(_translate_one, client, chunks, next_submit, glossary, terms)
                next_submit += 1
            result, duration = futures.pop(index).result()
            chunk = chunks[index]
            if result is None:
                log_error(index, chunk, "Thất bại sau tất cả các lần retry và bisect.")
                final_text = f"{UNTRANSLATED_TAG}\n{chunk['text']}"
                log(logging.ERROR, "Cảnh báo: hết số lần thử; lưu nhãn để có thể dịch lại ở lần chạy sau.")
            else:
                final_text = result

            append_output(final_text)
            save_progress(index + 1, chunks)

            durations.append(duration)
            average = sum(durations) / len(durations)
            remaining_count = total - index - 1
            eta = average * remaining_count / workers
            finish = datetime.now() + timedelta(seconds=eta)
            log(logging.INFO, f"Đã lưu chunk {index + 1}; mất {duration:.1f}s, trung bình {average:.1f}s/chunk.",
                chunk=index + 1)
            if remaining_count:
                log(logging.INFO,
                    f"Còn {remaining_count} chunk; ETA {format_duration(eta)}, "
                    f"dự kiến hoàn thành {finish.strftime('%H:%M:%S %d/%m')}.")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def find_untranslated_indices(chunks, upto):
    if not FILE_OUTPUT.exists():
        return []
    output = read_text_file(FILE_OUTPUT)
    if UNTRANSLATED_TAG not in output:
        return []
    found = []
    for index in range(min(upto, len(chunks))):
        marker = f"{UNTRANSLATED_TAG}\n{chunks[index]['text']}"
        if marker in output:
            found.append(index)
    if not found:
        # Có nhãn nhưng không khớp chunk nào: thường do đổi MAX_CHARS/nguồn so với lần chạy trước.
        n = output.count(UNTRANSLATED_TAG)
        print(f"[Cảnh báo] Output có {n} nhãn chưa dịch nhưng không khớp chunk nào "
              "(đổi max_chars hoặc file nguồn so với lần chạy trước?); không thể tự dịch lại.")
    return found


def retry_untranslated(client, chunks, upto, glossary, terms=None):
    failed_indices = find_untranslated_indices(chunks, upto)
    if not failed_indices:
        return

    print(f"Phát hiện {len(failed_indices)} chunk chưa dịch; ưu tiên dịch lại.")
    for index in failed_indices:
        chunk = chunks[index]
        label = f"chunk {index + 1}"
        result = translate_with_retry(client, chunk, glossary, label, terms)
        if result is None:
            print(f"  Chunk {index + 1} vẫn thất bại; giữ nguyên nhãn.")
            log_error(index, chunk, "Không dịch được khi thử dịch lại.")
            continue

        output = read_text_file(FILE_OUTPUT)
        old = f"{UNTRANSLATED_TAG}\n{chunk['text']}"
        if old not in output:
            print(f"  Không tìm thấy chính xác chunk {index + 1} trong output; không thay thế.")
            continue

        output = output.replace(old, result, 1)
        atomic_write_text(FILE_OUTPUT, output)
        print(f"  Đã thay thế chunk {index + 1} bằng bản dịch mới.")


def append_output(text):
    FILE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    has_content = FILE_OUTPUT.exists() and FILE_OUTPUT.stat().st_size > 0
    with open(FILE_OUTPUT, "a", encoding="utf-8") as file:
        if has_content:
            file.write("\n\n")
        file.write(text)


# ============================================================
# 8. QUÉT OUTPUT CŨ TÌM DÒNG BỊ LỌT GLOSSARY/CHỈ DẪN
#    Chạy: python translate_zh_vi.py --scan
# ============================================================

def scan_output():
    if not FILE_OUTPUT.exists():
        print(f"Không thấy file output: {FILE_OUTPUT}")
        return

    arrow_line = re.compile(r"^\s*\S.*=>\s*\S")
    hits = 0
    for lineno, line in enumerate(read_text_file(FILE_OUTPUT).splitlines(), 1):
        reason = None
        for marker in LEAK_MARKERS:
            if marker in line:
                reason = marker
                break
        if reason is None and arrow_line.match(line):
            reason = "=>"
        if reason:
            hits += 1
            preview = line.strip()
            if len(preview) > 100:
                preview = preview[:100] + "..."
            print(f"Dòng {lineno} [{reason}]: {preview}")

    if hits:
        print(f"\nTìm thấy {hits} dòng nghi ngờ trong {FILE_OUTPUT.name}.")
    else:
        print("Không thấy dòng nào bị lọt glossary/chỉ dẫn.")


# ============================================================
# 9. MAIN
# ============================================================

def main():
    if "--scan" in sys.argv:
        scan_output()
        return

    setup_logging()
    if not FILE_INPUT.exists():
        print(f"LỖI: Không tìm thấy file đầu vào:\n{FILE_INPUT}")
        sys.exit(1)

    print("[1/6] Đọc và dọn dẹp file nguồn...")
    content = read_text_file(FILE_INPUT)
    content = re.sub(r"✅ Đã lưu thành công!", "", content)
    content = re.sub(r"127\.0\.0\.1.*?200 -.*?(?:\n|$)", "", content)
    content = re.sub(r"📥 \[Khối thứ \d+\].*?(?:\n|$)", "", content)

    print("[2/6] Nạp glossary và khởi tạo bộ kiểm tra...")
    glossary = load_glossary(GLOSSARY_FILE)
    auto_glossary = load_glossary(AUTO_GLOSSARY_FILE)
    if MOEGIRL_ENABLED:
        ensure_moegirl_dataset_cache()
    work_anchors = load_work_anchors(WORK_ANCHORS_FILE)
    detected_works = detect_works(content, work_anchors)
    approved_works = load_approved_works(WORKS_APPROVED_FILE)
    write_work_review(WORK_REVIEW_FILE, detected_works, approved_works)
    if MOEGIRL_ENABLED and not approved_works and not MOEGIRL_INCLUDE_UNAPPROVED:
        print("[Moegirl] Chưa có tác phẩm nào được duyệt; không dùng nhân vật Moegirl. "
              f"Xem {WORK_REVIEW_FILE.name} rồi điền {WORKS_APPROVED_FILE.name}.")
    moegirl_entries = (
        load_moegirl_entries(MOEGIRL_TERMS_FILE, content, approved_works) if MOEGIRL_ENABLED else []
    )
    if MOEGIRL_ENABLED:
        write_moegirl_suggestions(MOEGIRL_SUGGESTIONS_FILE, moegirl_entries)
    # Thứ tự ưu tiên: glossary thủ công > glossary tự động > Moegirl.
    # Chỉ mục đủ tin cậy và xuất hiện đủ nhiều trong đúng truyện này mới được lưu
    # thành thuật ngữ force trong file .auto.txt cạnh glossary của truyện.
    protected_sources = {item["src"] for item in glossary + auto_glossary}
    if MOEGIRL_ENABLED:
        append_moegirl_auto_terms(AUTO_GLOSSARY_FILE, moegirl_entries, protected_sources)
        auto_glossary = load_glossary(AUTO_GLOSSARY_FILE)
    glossary_sources = {item["src"] for item in glossary + auto_glossary}
    moegirl_for_prompt = [
        {k: v for k, v in item.items() if k in {"src", "dst", "info"}}
        for item in moegirl_entries if item["src"] not in glossary_sources and item.get("dst")
    ]
    glossary = glossary + auto_glossary
    terms = TermManager.from_files([GLOSSARY_FILE, AUTO_GLOSSARY_FILE])
    terms.add_entries(moegirl_for_prompt)
    global TRANSLATION_GUARD
    TRANSLATION_GUARD = TranslationGuard(
        min_source_len=MIN_SOURCE_LEN_CHECK,
        min_length_ratio=MIN_RATIO,
        max_length_ratio=MAX_RATIO,
        max_han_ratio=MAX_HAN_RATIO,
        check_han=False,
        strict=False,
    )
    print(f"[Guard] Đã nạp {len(terms.glossary)} mục thuật ngữ; "
          f"glossary tự động: {AUTO_GLOSSARY_FILE}; "
          f"Moegirl: {len(moegirl_for_prompt)} mục gợi ý; ngưỡng tự động {MOEGIRL_AUTO_COMMIT_MIN_CONFIDENCE:.2f}; "
          f"nguồn {MOEGIRL_TERMS_FILE}")

    print("[3/6] Chia chương thành chunk...")
    chunks = split_into_chunks(content)
    if not chunks:
        print("Không tìm thấy chương nào. Kiểm tra định dạng tiêu đề bắt đầu bằng ##.")
        sys.exit(0)
    global ADDRESSING_RULES
    addressing_path = FILE_INPUT.parent / "xungho.rules.json"
    ADDRESSING_RULES, unmatched_chapters = load_rules(addressing_path, GLOSSARY_FILE, chunks)
    if ADDRESSING_RULES:
        print(f"[Xưng hô] Đã nạp {len(ADDRESSING_RULES['rules'])} quy tắc đã duyệt; "
              f"{unmatched_chapters} chương không khớp tiêu đề báo cáo sẽ không áp dụng quy tắc.")
    print(f"Đã tạo {len(chunks)} chunk (giới hạn thông thường khoảng "
          f"{min(MAX_CHARS, max_chars_for_context())} ký tự; MAX_CHARS={MAX_CHARS}, CONTEXT_TOKENS={CONTEXT_TOKENS}).")

    if RESTART_FRESH:
        print("[Progress] RESTART_FRESH=True: xóa output và progress.")
        for path in (FILE_OUTPUT, FILE_PROGRESS):
            if path.exists():
                path.unlink()
        start_index = 0
    else:
        start_index = load_progress(chunks)

        if not FILE_PROGRESS.exists() and FILE_OUTPUT.exists() and FILE_OUTPUT.stat().st_size:
            old_output = read_text_file(FILE_OUTPUT)
            if UNTRANSLATED_TAG in old_output:
                start_index = len(chunks)
                print("Có output với nhãn chunk lỗi; sẽ ưu tiên thử dịch lại chunk lỗi.")
            else:
                answer = input(
                    "Output đã tồn tại nhưng không có progress. "
                    "Xóa output và dịch lại từ đầu? (y/n): "
                ).strip().lower()
                if answer == "y":
                    FILE_OUTPUT.unlink()
                    start_index = 0
                else:
                    print("Đã hủy để tránh nối trùng hoặc làm lệch output.")
                    sys.exit(0)

        if FILE_PROGRESS.exists() and not FILE_OUTPUT.exists():
            print("Có progress nhưng không có output; bắt đầu lại từ đầu.")
            FILE_PROGRESS.unlink()
            start_index = 0

    print("[4/6] Khởi tạo Ollama client...")
    client = get_client()

    if AUTO_DISCOVER_TERMS:
        print("[Thuật ngữ tự động] Đang tìm cụm chữ Hán lặp lại...")
        candidates = terms.discover_candidates(
            content, min_frequency=AUTO_TERM_MIN_FREQUENCY,
            max_candidates=AUTO_TERM_MAX_CANDIDATES,
        )
        print(f"[Thuật ngữ tự động] Tìm được {len(candidates)} ứng viên.")
        suggestions = terms.translate_candidates(
            client, MODEL, TARGET_LANGUAGE, candidates,
            max_terms=AUTO_TERM_MAX_CANDIDATES,
        )
        added = TermManager.append_auto_glossary(AUTO_GLOSSARY_FILE, suggestions)
        print(f"[Thuật ngữ tự động] Đề xuất {len(suggestions)} mục; ghi mới {added} mục vào file riêng.")
        if added:
            terms = TermManager.from_files([GLOSSARY_FILE, AUTO_GLOSSARY_FILE])
            terms.add_entries(moegirl_for_prompt)
            glossary = load_glossary(GLOSSARY_FILE) + load_glossary(AUTO_GLOSSARY_FILE)

    print("[5/6] Thử dịch lại các chunk lỗi từ lần chạy trước...")
    if start_index > 0 and FILE_OUTPUT.exists():
        retry_untranslated(client, chunks, start_index, glossary, terms)

    if start_index >= len(chunks):
        remaining = find_untranslated_indices(chunks, len(chunks))
        if remaining:
            print(f"Còn {len(remaining)} chunk chưa dịch: {[i + 1 for i in remaining]}")
            print(f"Chạy lại script để thử tiếp. Log lỗi: {FILE_ERROR_LOG}")
        else:
            print("Tất cả chunk trong phạm vi đã hoàn tất.")
            if FILE_PROGRESS.exists():
                FILE_PROGRESS.unlink()
        return

    print("[6/6] Bắt đầu dịch...")
    run_translation(client, chunks, start_index, glossary, terms)

    remaining_failed = find_untranslated_indices(chunks, len(chunks))
    print(f"\nHoàn tất lượt chạy. File output:\n{FILE_OUTPUT}")
    if remaining_failed:
        print(f"Còn {len(remaining_failed)} chunk chưa dịch: {[i + 1 for i in remaining_failed]}")
        print(f"Chạy lại script để ưu tiên thử lại. Log: {FILE_ERROR_LOG}")
        save_progress(len(chunks), chunks)
    else:
        print("Không còn chunk nào mang nhãn chưa dịch.")
        if FILE_PROGRESS.exists():
            FILE_PROGRESS.unlink()



# ============================================================
# 8. TRANSLATION GUARD + TERM MANAGER (hợp nhất từ translation_guard.py)
# ============================================================

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translation_guard.py (v2)
-------------------------
Bộ kiểm tra đầu ra + quản lý thuật ngữ cho script dịch tiểu thuyết Trung -> Việt
bằng Ollama (HY-MT...).

- Chỉ dùng thư viện chuẩn Python; không cần cài thêm package hoặc tải LLM.
- Không tự ý xóa/sửa nội dung bản dịch.
- Quét file bản dịch đã tạo:
      python translation_guard.py "D:\\duong-dan\\bandichmoi.txt" --context 1
- Xem glossary được phân loại ép/gợi ý ra sao:
      python translation_guard.py --glossary "D:\\duong-dan\\thuatngu.txt"

Thay đổi chính so với v1
------------------------
1. Đọc file đúng encoding (BOM -> UTF-8 -> GB18030), không còn đọc nhầm GBK thành rác.
2. Cụm rò prompt chia 2 mức: MẠNH (luôn báo) / YẾU (bỏ qua trong lời thoại).
3. Mỗi lỗi có `severity`: "hard" (nên dịch lại) hoặc "soft" (chỉ cảnh báo).
4. Phát hiện thêm: lệch số dòng, lặp, cụm chữ Hán dài còn sót, ký tự lạ
   (kana/hangul/cyrillic/thái), bản dịch không phải tiếng Việt, lời dẫn
   ("Dưới đây là bản dịch..."), <think>, code fence, thiếu thuật ngữ.
5. Chuẩn hóa Unicode NFC khi so khớp.
6. TermManager xử lý thuật ngữ bằng code:
     - Mục "ép" (force): thay thẳng src -> dst (chữ Latin/Việt) trước khi gửi model,
       không dùng placeholder nên model vẫn thấy tên thật.
     - Mục "gợi ý" (hint): chỉ đưa vào prompt những mục có xuất hiện trong chunk.
     - Mục tự động đề xuất: luôn là "gợi ý", không bao giờ bị ép.
7. discover_candidates: xếp hạng theo tần suất x độ dài (không còn để cụm 5 chữ
   nhiễu lấn át tên nhân vật), đếm theo kiểu apriori nên nhanh và ít tốn RAM.
8. guarded_translate(): vòng retry có sẵn, tự chia đôi chunk khi lỗi lặp lại,
   và giữ lại lần thử tốt nhất nếu hết lượt.

Quy tắc phân loại glossary (xem TermManager.entry_mode)
-------------------------------------------------------
- Có nhãn `# hint` / `# gợi ý` / `# auto` / `# tự động`  -> hint
- Có nhãn `# force` / `# ép`                              -> force
- src == dst, hoặc dst phần lớn là chữ Hán                -> hint
- src ngắn hơn `min_force_len` (mặc định 2 ký tự)         -> hint (tên 1 chữ dễ thay nhầm)
- dst viết hoa chữ đầu (tên riêng: "Hikigaya")            -> force
- còn lại (từ thường: "chủ nghĩa thực lực")               -> hint

Lưu ý: kiểm tra dựa trên quy tắc nên có thể bỏ sót hoặc báo nhầm. Hãy xem các
dòng được báo trước khi chỉnh sửa file bản dịch.
"""


import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# Kiểu dữ liệu
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GuardIssue:
    code: str
    message: str
    severity: str = "hard"  # "hard": nên dịch lại | "soft": chỉ cảnh báo


class InvalidTranslation(Exception):
    """Kết quả dịch không đạt kiểm tra; script dịch có thể bắt lỗi để retry."""

    def __init__(self, message: str = "", issues: Optional[list[GuardIssue]] = None) -> None:
        super().__init__(message)
        self.issues: list[GuardIssue] = issues or []


@dataclass
class GuardedResult:
    text: str
    ok: bool
    issues: list[GuardIssue]
    attempts: int


# ---------------------------------------------------------------------------
# Hằng số / regex
# ---------------------------------------------------------------------------

_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HAN_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_FOREIGN_RE = re.compile(r"[\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u0e00-\u0e7f]")
_VI_ACCENT_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)

# Cụm MẠNH: gần như chắc chắn là prompt/glossary bị lọt -> luôn báo.
STRONG_MARKERS = (
    # Prompt mẫu tiếng Việt thường dùng với HY-MT
    "Tham khảo bản dịch sau đây",
    "Dịch văn bản sau",
    "chỉ cần xuất ra kết quả dịch",
    "không giải thích thêm",
    "dịch thành",
    # Dấu vết kỹ thuật
    "zxqterm",
    "<think>",
    "</think>",
    # Biến thể tiếng Việt do model dịch lại chỉ dẫn
    "bảng thuật ngữ",
    "thuật ngữ bên dưới",
    "thuật ngữ phía dưới",
    "các thuật ngữ bên dưới",
    "thuật ngữ và ghi chú",
    "tham khảo bản dịch",
    "phía dưới thuật ngữ",
    "thuật ngữ bày tỏ cùng ghi chú",
    "tuân thủ một cách nghiêm chỉnh",
)

# Cụm YẾU: có thể xuất hiện trong lời thoại nhân vật -> chỉ báo khi nằm ngoài
# lời thoại và (dòng ngắn hoặc ở đầu/cuối khối).
WEAK_MARKERS = (
    "ghi chú dưới đây",
    "chỉ cần xuất ra bản dịch",
    "chỉ xuất bản dịch",
    "không cần giải thích thêm",
    "không thêm lời giải thích",
    "vui lòng tuân thủ",
    "hãy tuân thủ",
    "tuân thủ nghiêm ngặt",
)

# Giữ tên cũ để tương thích với code v1.
LEAK_MARKERS = STRONG_MARKERS + WEAK_MARKERS

_STRONG_CF = tuple(m.casefold() for m in STRONG_MARKERS)
_WEAK_CF = tuple(m.casefold() for m in WEAK_MARKERS)

_MARKDOWN_DECORATION_RE = re.compile(r"[*_`~]+")
_WHITESPACE_RE = re.compile(r"\s+")
_ARROW_RE = re.compile(r"=>|→|⇒")
_MAPPING_LINE_RE = re.compile(r"^\s*(?:[*_`~]+\s*)?.+?\s*=>\s*.+?\s*(?:[*_`~]+)?\s*$")
_SENTENCE_END_RE = re.compile(r"[。！？!?.…”\"」]$")

_VI_INSTRUCTION_RE = re.compile(
    r"(?:tuân thủ.{0,100}(?:thuật ngữ|ghi chú|bản dịch))|"
    r"(?:tham khảo.{0,60}(?:thuật ngữ|bản dịch))|"
    r"(?:chỉ cần.{0,50}(?:xuất ra|xuất|trả về).{0,30}bản dịch)|"
    r"(?:không cần.{0,40}giải thích)",
    re.IGNORECASE,
)

_PREAMBLE_RE = re.compile(
    r"^[*_`~>\s]*(?:"
    r"(?:dưới đây|sau đây|đây) là (?:bản |kết quả )?dịch|"
    r"bản dịch(?: tiếng việt)?\s*[:：]|"
    r"kết quả dịch\s*[:：]|"
    r"(?:here is|here's|sure|certainly|of course)\b|"
    r"translation\s*[:：]"
    r")",
    re.IGNORECASE,
)

# Cụm 12-80 ký tự lặp liền nhau >= 4 lần (model nhỏ hay bị kẹt vòng lặp).
_REPEAT_RE = re.compile(r"(.{12,80}?)\1{3,}")

_DIALOGUE_STARTS = ("“", "”", '"', "「", "『", "—", "–", "-", "‘", "'", "«")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_HINT_FLAG_RE = re.compile(r"(?<!\w)(?:hint|gợi ý|auto|tự động)(?!\w)", re.IGNORECASE)
_FORCE_FLAG_RE = re.compile(r"(?<!\w)(?:force|ép)(?!\w)", re.IGNORECASE)
_AUTO_FLAG_RE = re.compile(r"(?<!\w)(?:auto|tự động)(?!\w)", re.IGNORECASE)

_UNTRANSLATED_TAG_KEYS = ("chưa dịch", "giữ bản gốc")


# ---------------------------------------------------------------------------
# Tiện ích chung
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    """Đọc file: BOM UTF-16 -> UTF-8(-BOM) -> GB18030 -> UTF-8 thay ký tự lỗi.

    Không thử UTF-16 theo kiểu "thử rồi bắt lỗi" vì UTF-16 giải mã được hầu hết
    chuỗi byte (kể cả file GBK) mà không báo lỗi.
    """
    data = Path(path).read_bytes()
    text: Optional[str] = None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError:
            text = None
    if text is None:
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n")


def _normalize(text: str) -> str:
    """Chuẩn hóa NFC + bỏ dấu Markdown + gộp khoảng trắng (chỉ để kiểm tra)."""
    text = unicodedata.normalize("NFC", text)
    text = _MARKDOWN_DECORATION_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _prep(text: str) -> str:
    return _normalize(text).casefold()


def _looks_like_dialogue(line: str) -> bool:
    return line.lstrip(" \t*_`~").startswith(_DIALOGUE_STARTS)


def _find_markers(norm_text: str, norm_source: str = "") -> tuple[Optional[str], Optional[str]]:
    """Nhận text/source đã qua `_prep`. Trả về (cụm_mạnh, cụm_yếu)."""
    strong = next((m for m in _STRONG_CF if m in norm_text and m not in norm_source), None)
    weak = next((m for m in _WEAK_CF if m in norm_text and m not in norm_source), None)
    if weak is None and _VI_INSTRUCTION_RE.search(norm_text) and not _VI_INSTRUCTION_RE.search(norm_source):
        weak = "câu chỉ dẫn tiếng Việt"
    return strong, weak


def _contains_marker(text: str, source: str = "") -> Optional[str]:
    """Tương thích v1: trả về cụm đầu tiên tìm thấy (mạnh trước, yếu sau)."""
    strong, weak = _find_markers(_prep(text), _prep(source))
    return strong or weak


def _find_mapping_line(text: str) -> Optional[str]:
    for raw_line in text.splitlines():
        line = _MARKDOWN_DECORATION_RE.sub("", raw_line).strip()
        if not line:
            continue
        if "=>" in line and _MAPPING_LINE_RE.match(line):
            return raw_line.strip()
        # X → Y / X ⇒ Y: chỉ coi là glossary lộ nếu dòng còn chữ Hán, để không
        # báo nhầm bảng chỉ số kiểu "Lực lượng: 10 → 15" trong truyện hệ thống.
        if _ARROW_RE.search(line) and len(line) <= 180 and _HAN_RE.search(line):
            parts = _ARROW_RE.split(line, maxsplit=1)
            if len(parts) == 2:
                left, right = parts[0].strip(), parts[1].strip()
                if left and right and not _SENTENCE_END_RE.search(line):
                    if len(left) <= 70 and len(right) <= 70:
                        return raw_line.strip()
    return None


def _check_line(
    raw_line: str,
    *,
    is_edge: bool = False,
    norm_src: str = "",
    check_preamble: bool = False,
) -> list[GuardIssue]:
    """Kiểm tra một dòng: rò prompt/glossary, lời dẫn, code fence, lặp trong dòng."""
    issues: list[GuardIssue] = []
    line = raw_line.strip()
    if not line:
        return issues
    dialogue = _looks_like_dialogue(line)

    strong, weak = _find_markers(_prep(line), norm_src)
    if strong:
        issues.append(GuardIssue(
            "PROMPT_LEAK",
            f"Nghi có chỉ dẫn/glossary lọt vào bản dịch (cụm: {strong!r}).",
            "hard",
        ))
    elif weak and not dialogue and (is_edge or len(line) <= 200):
        issues.append(GuardIssue(
            "PROMPT_LEAK",
            f"Nghi có chỉ dẫn lọt vào bản dịch (cụm yếu: {weak!r}).",
            "hard" if is_edge else "soft",
        ))

    mapping = _find_mapping_line(line)
    if mapping:
        issues.append(GuardIssue(
            "GLOSSARY_LEAK", f"Nghi có dòng ánh xạ thuật ngữ: {mapping[:160]!r}.", "hard"
        ))

    if check_preamble and not dialogue and _PREAMBLE_RE.match(line):
        issues.append(GuardIssue(
            "FORMAT_JUNK", f"Nghi có lời dẫn của model: {line[:100]!r}.", "hard"
        ))
    if line.startswith("```"):
        issues.append(GuardIssue("FORMAT_JUNK", "Có code fence ``` trong bản dịch.", "hard"))

    if len(line) >= 48 and _REPEAT_RE.search(line):
        issues.append(GuardIssue("REPEAT", "Có cụm câu bị lặp liên tiếp (nghi kẹt vòng lặp).", "hard"))
    return issues


# ---------------------------------------------------------------------------
# Chia chunk khi retry
# ---------------------------------------------------------------------------

def _split_at_middle(pieces: list[str], source_sep: str) -> Optional[tuple[str, str]]:
    if len(pieces) < 2:
        return None
    total = sum(len(p) for p in pieces)
    acc = 0
    best_k, best_dist = None, None
    for k in range(1, len(pieces)):
        acc += len(pieces[k - 1])
        dist = abs(acc - total / 2)
        if best_dist is None or dist < best_dist:
            best_k, best_dist = k, dist
    left = source_sep.join(pieces[:best_k]).strip("\n")
    right = source_sep.join(pieces[best_k:]).strip("\n")
    if left.strip() and right.strip():
        return left, right
    return None


def _split_source(source: str) -> Optional[tuple[str, str, str]]:
    """Chia nguồn làm hai nửa. Trả về (trái, phải, dấu nối bản dịch) hoặc None."""
    lines = source.split("\n")
    if sum(1 for ln in lines if ln.strip()) >= 2:
        halves = _split_at_middle(lines, "\n")
        if halves:
            return halves[0], halves[1], "\n"
    sentences = [p for p in re.split(r"(?<=[。！？!?…])", source) if p]
    halves = _split_at_middle(sentences, "")
    if halves:
        return halves[0], halves[1], " "
    return None


# ---------------------------------------------------------------------------
# TranslationGuard
# ---------------------------------------------------------------------------

class TranslationGuard:
    """
    Kiểm tra đầu ra mà không gọi model phụ.

    - `inspect()` trả về danh sách GuardIssue (hard/soft).
    - `validate()` ném InvalidTranslation nếu có lỗi hard (hoặc soft khi strict=True);
      nếu không, trả về danh sách lỗi soft để bạn ghi log.
    - `guarded_translate()` bọc vòng retry: dịch -> kiểm tra -> dịch lại -> chia đôi
      chunk -> giữ lần thử tốt nhất.

    Luôn truyền NGUỒN GỐC (chưa thay thuật ngữ) vào source.
    Tỉ lệ độ dài tính trên số chữ Hán của nguồn (nếu nguồn chủ yếu là chữ Hán).
    """

    def __init__(
        self,
        min_source_len: int = 100,
        min_length_ratio: float = 0.9,
        max_length_ratio: float = 8.0,
        max_han_ratio: float = 0.15,
        check_length: bool = True,
        check_han: bool = True,
        check_lines: bool = True,
        check_repeat: bool = True,
        check_foreign: bool = True,
        check_diacritics: bool = True,
        max_han_run: int = 8,
        strict: bool = False,
    ) -> None:
        self.min_source_len = min_source_len
        self.min_length_ratio = min_length_ratio
        self.max_length_ratio = max_length_ratio
        self.max_han_ratio = max_han_ratio
        self.check_length = check_length
        self.check_han = check_han
        self.check_lines = check_lines
        self.check_repeat = check_repeat
        self.check_foreign = check_foreign
        self.check_diacritics = check_diacritics
        self.max_han_run = max_han_run
        self.strict = strict

    # -- kiểm tra ----------------------------------------------------------

    def inspect(
        self,
        source: str,
        result: str,
        terms: Optional["TermManager"] = None,
    ) -> list[GuardIssue]:
        result = result or ""
        if not result.strip():
            return [GuardIssue("EMPTY", "Model trả về nội dung rỗng.")]
        source = source or ""
        text = unicodedata.normalize("NFC", result)
        norm_src = _prep(source)
        found: dict[str, GuardIssue] = {}

        def add(issue: GuardIssue) -> None:
            old = found.get(issue.code)
            if old is None or (old.severity == "soft" and issue.severity == "hard"):
                found[issue.code] = issue

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        # 1) Kiểm tra từng dòng
        for idx, ln in enumerate(lines):
            edge = idx == 0 or idx == len(lines) - 1
            for issue in _check_line(
                ln, is_edge=edge, norm_src=norm_src, check_preamble=(idx == 0)
            ):
                if issue.code == "REPEAT" and not self.check_repeat:
                    continue
                add(issue)

        # 2) Cùng một dòng dài lặp nhiều lần
        if self.check_repeat:
            counts = Counter(ln for ln in lines if len(ln) >= 12)
            if counts:
                _, n = counts.most_common(1)[0]
                src_counts = Counter(
                    s.strip() for s in source.splitlines() if len(s.strip()) >= 4
                )
                src_max = max(src_counts.values(), default=1)
                if n >= max(4, src_max + 2):
                    add(GuardIssue("REPEAT", f"Một dòng bị lặp {n} lần.", "hard"))

        # 3) Độ dài / chữ Hán / ngôn ngữ (chỉ khi nguồn đủ dài)
        if len(source) >= self.min_source_len:
            src_nospace = _WHITESPACE_RE.sub("", source)
            han_src = len(_HAN_RE.findall(src_nospace))
            base = han_src if han_src >= len(src_nospace) * 0.3 else len(src_nospace)
            ratio = len(text) / max(1, base)
            if self.check_length and ratio < self.min_length_ratio:
                # Chỉ "hard" khi ngắn hơn hẳn một nửa ngưỡng; ngắn nhẹ chỉ cảnh báo.
                severe = ratio < self.min_length_ratio * 0.5
                add(GuardIssue(
                    "TOO_SHORT",
                    f"Bản dịch quá ngắn (tỉ lệ độ dài {ratio:.2f}); có thể bị cụt/tóm tắt.",
                    "hard" if severe else "soft",
                ))
            elif self.check_length and ratio > self.max_length_ratio:
                add(GuardIssue(
                    "TOO_LONG",
                    f"Bản dịch quá dài (tỉ lệ độ dài {ratio:.2f}); có thể bị lặp.",
                ))

            if self.check_han:
                han_text = text
                if terms is not None:
                    # Thuật ngữ được phép giữ chữ Hán thì không tính là "còn sót".
                    for dst in terms.han_dsts():
                        han_text = han_text.replace(dst, "")
                han_ratio = len(_HAN_RE.findall(han_text)) / max(1, len(han_text))
                if han_ratio > self.max_han_ratio:
                    add(GuardIssue("HAN_REMAINS", f"Bản dịch còn nhiều chữ Hán ({han_ratio:.0%})."))
                else:
                    longest = max((len(m.group(0)) for m in _HAN_RUN_RE.finditer(han_text)), default=0)
                    if longest >= self.max_han_run:
                        add(GuardIssue(
                            "HAN_RUN",
                            f"Còn một cụm {longest} chữ Hán liên tiếp (nghi sót câu chưa dịch).",
                        ))

            if self.check_foreign:
                foreign_res = len(_FOREIGN_RE.findall(text))
                foreign_src = len(_FOREIGN_RE.findall(source))
                if foreign_res > foreign_src + 3:
                    add(GuardIssue(
                        "FOREIGN_SCRIPT",
                        f"Có {foreign_res} ký tự kana/hangul/cyrillic/thái (model trôi ngôn ngữ).",
                    ))

            if self.check_diacritics:
                letters = sum(1 for ch in text if ch.isalpha() and not _HAN_RE.match(ch))
                if letters >= 100:
                    accent_ratio = len(_VI_ACCENT_RE.findall(text)) / letters
                    if accent_ratio < 0.08:
                        add(GuardIssue(
                            "NOT_VIETNAMESE",
                            "Gần như không có dấu tiếng Việt; có thể model trả lời sai ngôn ngữ.",
                        ))

        # 4) Lệch số dòng
        if self.check_lines:
            src_lines = sum(1 for ln in source.splitlines() if ln.strip())
            res_lines = len(lines)
            if src_lines >= 3:
                diff = abs(src_lines - res_lines)
                tolerance = max(1, math.ceil(src_lines * 0.15))
                if diff > tolerance:
                    dropped = res_lines < src_lines
                    add(GuardIssue(
                        "LINE_MISMATCH",
                        f"Số dòng lệch: nguồn {src_lines}, bản dịch {res_lines}"
                        + (" (nghi mất đoạn)." if dropped else " (model tách thêm dòng)."),
                        "hard" if dropped else "soft",
                    ))

        # 5) Thuật ngữ
        if terms is not None:
            missing = terms.missing_terms(source, text)
            if missing:
                shown = ", ".join(f"{m['src']}=>{m['dst']}" for m in missing[:5])
                more = f" (+{len(missing) - 5})" if len(missing) > 5 else ""
                add(GuardIssue(
                    "TERM_MISSING",
                    f"Thiếu thuật ngữ trong bản dịch: {shown}{more}.",
                    "soft",
                ))

        return list(found.values())

    def validate(
        self,
        source: str,
        result: str,
        terms: Optional["TermManager"] = None,
    ) -> list[GuardIssue]:
        """Ném InvalidTranslation nếu có lỗi hard (hoặc soft khi strict=True).

        Trả về danh sách lỗi soft (có thể rỗng) để bạn ghi log.
        """
        issues = self.inspect(source, result, terms)
        soft = [i for i in issues if i.severity == "soft"]
        blocking = [i for i in issues if i.severity == "hard"] + (soft if self.strict else [])
        if blocking:
            details = "; ".join(f"{i.code}: {i.message}" for i in blocking)
            raise InvalidTranslation(details, issues)
        return soft

    # -- vòng retry --------------------------------------------------------

    def guarded_translate(
        self,
        translate_fn: Callable[[str, str], str],
        source: str,
        terms: Optional["TermManager"] = None,
        max_attempts: int = 4,
        split_after: int = 2,
        min_split_len: int = 300,
        max_split_depth: int = 2,
        on_retry: Optional[Callable[[int, list[GuardIssue]], None]] = None,
        _depth: int = 0,
    ) -> GuardedResult:
        """Dịch có kiểm tra.

        `translate_fn(text, hint_block) -> str`:
            - text: nguồn đã thay các thuật ngữ "ép" (nếu có `terms`).
            - hint_block: khối gợi ý thuật ngữ để ghép vào đầu prompt ("" nếu không có).
        Sau `split_after` lần lỗi, chunk được chia đôi (theo dòng, hoặc theo câu) và
        dịch từng nửa. Hết lượt vẫn lỗi -> trả về lần thử tốt nhất với ok=False.
        """
        max_attempts = max(1, max_attempts)
        if terms is not None:
            prepared, _ = terms.apply_forced_terms(source)
            hints = terms.build_hint_block(source)
        else:
            prepared, hints = source, ""

        best: Optional[tuple[tuple[int, int, int], str, list[GuardIssue]]] = None
        for attempt in range(1, max_attempts + 1):
            result = translate_fn(prepared, hints)
            issues = self.inspect(source, result, terms)
            hard = [i for i in issues if i.severity == "hard"]
            soft = [i for i in issues if i.severity == "soft"]
            if not hard and not (self.strict and soft):
                return GuardedResult(result, True, soft, attempt)

            score = (len(hard), len(soft), -len(result or ""))
            if best is None or score < best[0]:
                best = (score, result, issues)
            if on_retry:
                on_retry(attempt, issues)

            if (
                attempt >= split_after
                and _depth < max_split_depth
                and len(source) >= 2 * min_split_len
            ):
                parts = _split_source(source)
                if parts:
                    left_src, right_src, joiner = parts
                    kwargs = dict(
                        terms=terms,
                        max_attempts=max_attempts,
                        split_after=split_after,
                        min_split_len=min_split_len,
                        max_split_depth=max_split_depth,
                        on_retry=on_retry,
                        _depth=_depth + 1,
                    )
                    left = self.guarded_translate(translate_fn, left_src, **kwargs)
                    right = self.guarded_translate(translate_fn, right_src, **kwargs)
                    return GuardedResult(
                        left.text + joiner + right.text,
                        left.ok and right.ok,
                        left.issues + right.issues,
                        attempt + left.attempts + right.attempts,
                    )

        assert best is not None
        return GuardedResult(best[1], False, best[2], max_attempts)


# ---------------------------------------------------------------------------
# Quản lý thuật ngữ (không tải model riêng)
# ---------------------------------------------------------------------------

# Danh sách chặn bảo thủ: tránh đưa các cụm chức năng phổ biến vào glossary.
_COMMON_ZH_TERMS = {
    "然后", "因为", "所以", "但是", "不过", "如果", "虽然", "只是", "已经",
    "现在", "之前", "之后", "时候", "什么", "怎么", "为什么", "如何", "这样",
    "那样", "这里", "那里", "自己", "对方", "大家", "所有", "没有", "不是",
    "可以", "可能", "觉得", "知道", "发现", "看着", "看见", "听到", "说道",
    "问道", "回答", "继续", "开始", "突然", "立刻", "马上", "最后", "同时",
    "一点", "一些", "东西", "事情", "问题", "情况", "地方", "世界", "时间",
    "今天", "明天", "昨天", "起来", "过去", "回来", "进去", "出来",
    "如果说", "没关系", "怎么样", "与此同时", "不由得", "忍不住",
}

# Ký tự chức năng: ứng viên bắt đầu/kết thúc bằng các chữ này gần như chắc chắn
# là mảnh vỡ của câu, không phải tên riêng.
_STOP_CHARS = set("的了是在不有这那他她它我你们也就都而及与和或但并被把让给对向从到着过吗呢吧啊呀么之其所又还才只再很太更最")


def _parse_glossary_lines(text: str) -> list[dict[str, str]]:
    """Đọc các định dạng glossary: =>, ->, TAB, = hoặc dòng giữ nguyên."""
    entries: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        mapping_part, info = line, None
        if "#" in line:
            mapping_part, info = line.split("#", 1)
            info = info.strip()
        src = dst = None
        for separator in ("=>", "->", "\t", "="):
            if separator in mapping_part:
                src, dst = mapping_part.split(separator, 1)
                break
        if src is None:
            src = dst = mapping_part.strip()
            info = info or "giữ nguyên thuật ngữ"
        src, dst = src.strip(), dst.strip()
        if src and dst:
            item = {"src": src, "dst": dst}
            if info:
                item["info"] = info
            entries.append(item)
    return entries


def _is_han_dominant(text: str) -> bool:
    return len(_HAN_RE.findall(text)) / max(1, len(text)) > 0.5


def _is_auto_entry(item: dict[str, str]) -> bool:
    return bool(_AUTO_FLAG_RE.search(item.get("info", "")))


def _strip_think(raw: str) -> str:
    return _THINK_RE.sub("", raw).strip()


def _extract_json_terms(raw: str) -> Optional[list]:
    """Tìm object JSON đầu tiên có khóa "terms" trong câu trả lời (chịu được lời dẫn thừa)."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            obj, _ = decoder.raw_decode(raw[match.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("terms"), list):
            return obj["terms"]
    return None


def _parse_line_pairs(raw: str) -> list[dict[str, str]]:
    """Dự phòng khi model không trả JSON: đọc các dòng 'nguồn => dịch'."""
    rows = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-*•0123456789.) ").strip()
        parts = re.split(r"\s*(?:=>|->|\||\t)\s*", line, maxsplit=1)
        if len(parts) == 2 and parts[0] and parts[1]:
            rows.append({"source": parts[0].strip("*_` "), "translation": parts[1].strip("*_` ")})
    return rows


class TermManager:
    """Quản lý glossary, phát hiện ứng viên thuật ngữ và lưu glossary tự động.

    Luồng dùng gợi ý:
        terms = TermManager.from_files([thuatngu_tay, auto_glossary])   # file đầu ưu tiên hơn
        prepared, _ = terms.apply_forced_terms(chunk)      # thay tên riêng đã duyệt
        hints = terms.build_hint_block(chunk)              # gợi ý các mục còn lại
        ... gọi model với hints + prepared ...
        guard.validate(chunk, output, terms)               # chunk = NGUỒN GỐC
    Hoặc dùng thẳng TranslationGuard.guarded_translate().
    """

    def __init__(
        self,
        glossary: Optional[list[dict[str, str]]] = None,
        min_force_len: int = 2,
    ) -> None:
        self.min_force_len = min_force_len
        self.glossary: list[dict[str, str]] = []
        self._index: dict[str, dict[str, str]] = {}
        self._force_re: Optional[re.Pattern] = None
        self._force_built = False
        self._force_map: dict[str, str] = {}
        self.add_entries(glossary or [])

    @classmethod
    def from_files(
        cls,
        paths: Iterable[str | Path | None],
        min_force_len: int = 2,
    ) -> "TermManager":
        """Nạp nhiều file glossary. Nếu trùng src, file đứng trước thắng."""
        entries: list[dict[str, str]] = []
        for raw_path in paths:
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_file():
                continue
            if path.suffix.lower() == ".json":
                try:
                    payload = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    continue
                records = payload.get("records", {}) if isinstance(payload, dict) else {}
                values = records.values() if isinstance(records, dict) else records if isinstance(records, list) else []
                for record in values:
                    if not isinstance(record, dict):
                        continue
                    names = record.get("latin_names", [])
                    if isinstance(names, str):
                        names = [names]
                    names = [str(x).strip() for x in names if str(x).strip()]
                    if not names:
                        continue
                    dst = names[0]
                    aliases = record.get("aliases", [])
                    if isinstance(aliases, str):
                        aliases = [aliases]
                    srcs = [record.get("canonical_zh") or record.get("page_title", "")]
                    srcs.extend(aliases if isinstance(aliases, list) else [])
                    for raw_src in srcs:
                        src = str(raw_src).strip()
                        if not src or src == dst or not HAN_RE.search(src):
                            continue
                        url = str(record.get("source_url", "")).strip()
                        entries.append({"src": src, "dst": dst, "info": "moegirl hint; verify context" + (f"; source={url}" if url else "")})
            else:
                entries.extend(_parse_glossary_lines(_read_text(path)))
        return cls(entries, min_force_len=min_force_len)

    # -- sửa đổi -----------------------------------------------------------

    def add_entries(self, entries: Iterable[dict[str, str]]) -> int:
        added = 0
        for item in entries:
            src = str(item.get("src", "")).strip()
            dst = str(item.get("dst", "")).strip()
            if not src or not dst or src in self._index:
                continue
            new_item = {"src": src, "dst": dst}
            if item.get("info"):
                new_item["info"] = str(item["info"])
            self.glossary.append(new_item)
            self._index[src] = new_item
            added += 1
        if added:
            self.glossary.sort(key=lambda x: len(x["src"]), reverse=True)
            self._force_built = False
        return added

    # -- phân loại ---------------------------------------------------------

    def entry_mode(self, item: dict[str, str]) -> str:
        """Trả về "force" (thay thẳng trong nguồn) hoặc "hint" (chỉ gợi ý trong prompt)."""
        info = item.get("info", "")
        src, dst = item["src"], item["dst"]
        if _HINT_FLAG_RE.search(info):
            return "hint"
        if src == dst or _is_han_dominant(dst):
            return "hint"
        if _FORCE_FLAG_RE.search(info):
            return "force"
        if len(src) < self.min_force_len:
            return "hint"
        return "force" if dst[:1].isupper() else "hint"

    def han_dsts(self) -> list[str]:
        """Các bản dịch được phép chứa chữ Hán (để guard không tính là 'còn sót')."""
        return [it["dst"] for it in self.glossary if _HAN_RE.search(it["dst"])]

    def relevant_entries(self, text: str) -> list[dict[str, str]]:
        return [it for it in self.glossary if it["src"] in text]

    # -- thay thế / gợi ý --------------------------------------------------

    def _ensure_force_regex(self) -> None:
        if self._force_built:
            return
        forced = [it for it in self.glossary if self.entry_mode(it) == "force"]
        self._force_map = {it["src"]: it["dst"] for it in forced}
        self._force_re = (
            re.compile("|".join(re.escape(it["src"]) for it in forced)) if forced else None
        )
        self._force_built = True

    def apply_forced_terms(self, text: str) -> tuple[str, list[dict[str, str]]]:
        """Thay các mục "ép" bằng bản dịch ngay trong nguồn (một lượt, ưu tiên cụm dài).

        Trả về (text_đã_thay, các_mục_đã_dùng).
        """
        self._ensure_force_regex()
        if self._force_re is None:
            return text, []
        used: dict[str, str] = {}

        def repl(match: re.Match) -> str:
            src = match.group(0)
            used[src] = self._force_map[src]
            return self._force_map[src]

        out = self._force_re.sub(repl, text)
        return out, [self._index[s] for s in used]

    def build_hint_block(
        self,
        text: str,
        max_entries: int = 30,
        include_forced: bool = False,
        header: str = "Tham khảo bản dịch sau đây:",
        template: str = "{src} dịch thành {dst}",
    ) -> str:
        """Khối gợi ý chỉ gồm các mục có mặt trong `text` (mặc định mẫu prompt HY-MT).

        Trả về "" nếu không có mục nào; nếu có, kết thúc bằng một dòng trống để bạn
        ghép trực tiếp:  prompt = hints + "Dịch văn bản sau sang tiếng Việt…：\\n" + prepared
        """
        entries = [
            it for it in self.relevant_entries(text)
            if include_forced or self.entry_mode(it) != "force"
        ][:max_entries]
        if not entries:
            return ""
        lines = [template.format(src=it["src"], dst=it["dst"]) for it in entries]
        return header + "\n" + "\n".join(lines) + "\n\n"

    def missing_terms(
        self,
        source: str,
        result: str,
        include_auto: bool = False,
    ) -> list[dict[str, str]]:
        """Các mục có src trong nguồn nhưng dst không thấy trong bản dịch.

        Mục tự động đề xuất (chưa duyệt) bị bỏ qua để tránh báo nhiễu.
        """
        norm_result = unicodedata.normalize("NFC", result).casefold()
        missing = []
        for item in self.relevant_entries(source):
            dst = item["dst"]
            if item["src"] == dst or _HAN_RE.search(dst):
                continue
            if not include_auto and _is_auto_entry(item):
                continue
            if unicodedata.normalize("NFC", dst).casefold() not in norm_result:
                missing.append(item)
        return missing

    # -- phát hiện ứng viên ------------------------------------------------

    def discover_candidates(
        self,
        text: str,
        min_frequency: int = 2,
        max_candidates: int = 40,
        min_term_length: int = 2,
        max_term_length: int = 5,
    ) -> list[dict[str, object]]:
        """Tìm cụm chữ Hán lặp lại chưa có trong glossary.

        Trả về [{term, frequency, context}], xếp theo tần suất x độ dài.
        Đếm theo từng độ dài (apriori): n-gram chỉ được đếm nếu tiền tố và hậu tố
        (n-1) của nó đã đủ tần suất, nên nhanh và ít tốn RAM với truyện dài.
        Đây là heuristic: không thể xác định chắc chắn mọi tên riêng bằng regex.
        """
        runs = [(m.start(), m.group(0)) for m in _HAN_RUN_RE.finditer(text)]
        counts: dict[str, int] = {}
        first_pos: dict[str, int] = {}
        prev_frequent: Optional[set[str]] = None

        for size in range(min_term_length, max_term_length + 1):
            level: Counter = Counter()
            pos: dict[str, int] = {}
            for start, run in runs:
                if len(run) < size:
                    continue
                for off in range(len(run) - size + 1):
                    if prev_frequent is not None:
                        if (
                            run[off:off + size - 1] not in prev_frequent
                            or run[off + 1:off + size] not in prev_frequent
                        ):
                            continue
                    term = run[off:off + size]
                    level[term] += 1
                    if term not in pos:
                        pos[term] = start + off
            frequent = {t for t, c in level.items() if c >= min_frequency}
            if not frequent:
                break
            for t in frequent:
                counts[t] = level[t]
                first_pos[t] = pos[t]
            prev_frequent = frequent

        known_all = {it["src"] for it in self.glossary} | {it["dst"] for it in self.glossary}
        known_long = {it["src"] for it in self.glossary if len(it["src"]) >= 2}
        known_inner: set[str] = set()     # mọi chuỗi con (>=2) của thuật ngữ đã biết
        known_prefixes: set[str] = set()  # tiền tố (>=2) của thuật ngữ đã biết
        known_suffixes: set[str] = set()  # hậu tố (>=2) của thuật ngữ đã biết
        for k in known_long:
            for i in range(len(k)):
                for j in range(i + 2, len(k) + 1):
                    known_inner.add(k[i:j])
            for n in range(2, len(k)):
                known_prefixes.add(k[:n])
                known_suffixes.add(k[-n:])

        def overlaps_known(term: str) -> bool:
            # Chứa thuật ngữ đã biết, nằm trong thuật ngữ đã biết, hoặc là mảnh
            # "dính" vào rìa thuật ngữ đã biết (vd 塚静走进来 khi đã có 平塚静).
            if term in known_inner:
                return True
            for i in range(len(term)):
                for j in range(i + 2, len(term) + 1):
                    if term[i:j] in known_long:
                        return True
            for n in range(2, len(term)):
                if term[:n] in known_suffixes or term[-n:] in known_prefixes:
                    return True
            return False

        candidates: list[dict[str, object]] = []
        for term, freq in counts.items():
            if term in _COMMON_ZH_TERMS or term in known_all:
                continue
            if term[0] in _STOP_CHARS or term[-1] in _STOP_CHARS:
                continue
            if known_long and overlaps_known(term):
                continue
            p = first_pos[term]
            context = text[max(0, p - 12):min(len(text), p + len(term) + 12)].replace("\n", " ")
            candidates.append({"term": term, "frequency": freq, "context": context})

        # Loại cụm ngắn là một phần của cụm dài có tần suất >= nó (giảm n-gram nhiễu).
        candidates.sort(key=lambda x: (len(str(x["term"])), int(x["frequency"])), reverse=True)
        contained_max: dict[str, int] = {}
        selected: list[dict[str, object]] = []
        for cand in candidates:
            term, freq = str(cand["term"]), int(cand["frequency"])
            if contained_max.get(term, 0) >= freq:
                continue
            selected.append(cand)
            for i in range(len(term)):
                for j in range(i + min_term_length, len(term) + 1):
                    sub = term[i:j]
                    if sub != term:
                        contained_max[sub] = max(contained_max.get(sub, 0), freq)

        selected.sort(key=lambda x: int(x["frequency"]) * len(str(x["term"])), reverse=True)
        return selected[:max_candidates]

    def translate_candidates(
        self,
        client,
        model: str,
        target_language: str,
        candidates: list[dict[str, object]],
        max_terms: int = 40,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> list[dict[str, str]]:
        """Dùng model dịch hiện có để đề xuất glossary cho nhóm ứng viên.

        Không khởi tạo model/client mới. Chỉ nhận các source nằm trong danh sách đã
        gửi. Câu trả lời được làm sạch <think>, đọc JSON chịu lỗi, và có dự phòng
        đọc các dòng 'nguồn => dịch' nếu model không trả JSON. Mục trả về luôn mang
        nhãn "tự động" nên TermManager chỉ coi là gợi ý (không ép thay).
        Đặt json_mode=True nếu server hỗ trợ response_format json_object.
        """
        if not candidates:
            return []
        candidates = candidates[:max_terms]
        payload = [
            {"source": str(x["term"]), "context": str(x.get("context", ""))}
            for x in candidates
        ]
        prompt = (
            f"Bạn là trợ lý chuyên biên dịch thuật ngữ Light Novel từ tiếng Trung sang {target_language}.\n"
            f"Nhiệm vụ: Dịch chính xác danh sách từ/danh từ riêng trong đầu vào sang {target_language}.\n\n"
            "【QUY TẮC BẮT BUỘC】:\n"
            "1. Giữ nguyên gốc key 'source'. Chỉ xử lý từ trong input, không tự thêm từ mới ngoài danh sách.\n"
            "2. Tên nhân vật/địa danh gốc Nhật hoặc phương Tây: BẮT BUỘC dùng tên Latinh/Quốc ngữ chuẩn (vd: Alice, Asuna, Arthur, Hikigaya). Kỹ năng/vật phẩm/danh hiệu: Dịch sang tiếng Việt tự nhiên hoặc Hán-Việt Quốc ngữ.\n"
            f"3. Phần 'translation' BẮT BUỘC 100% là chữ Quốc ngữ hoặc ký tự Latinh. TUYỆT ĐỐI KHÔNG CHỨA BẤT KỲ KÝ TỰ CHỮ HÁN NÀO. Nếu từ quá khó, BẮT BUỘC đổi sang âm Hán-Việt chữ Quốc ngữ hoặc để chuỗi rỗng \"\".\n"
            "4. Không viết lời mở đầu, chào hỏi hay giải thích phụ trợ.\n"
            "5. CHỈ XUẤT RA MỘT CHUỖI JSON HỢP LỆ. Tuyệt đối KHÔNG bọc trong Markdown block (không dùng ```json hay ```).\n\n"
            "【ĐỊNH DẠNG JSON MẪU】:\n"
            '{"terms":[{"source":"原词","translation":"bản dịch Quốc ngữ"}]}\n\n'
            f"【ĐẦU VÀO】:\n{json.dumps(payload, ensure_ascii=False)}"
        )
        kwargs: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        raw = _strip_think(response.choices[0].message.content or "")

        rows = _extract_json_terms(raw)
        if rows is None:
            rows = _parse_line_pairs(raw)

        allowed = {str(x["term"]) for x in candidates}
        dedup: dict[str, dict[str, str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            src = str(row.get("source", "")).strip()
            dst = str(row.get("translation", "")).strip()
            if src not in allowed or not dst or src == dst or "\n" in dst or len(dst) > 60:
                continue
            if _is_han_dominant(dst):
                continue
            dedup[src] = {"src": src, "dst": dst, "info": "tự động đề xuất; nên rà soát"}
        return list(dedup.values())

    @staticmethod
    def append_auto_glossary(path: str | Path, entries: Iterable[dict[str, str]]) -> int:
        """Ghi nối các mục mới vào file riêng; không sửa glossary thủ công."""
        path = Path(path)
        rows = list(entries)
        if not rows:
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: set[str] = set()
        needs_newline = False
        if path.is_file():
            old_text = _read_text(path)
            needs_newline = bool(old_text) and not old_text.endswith("\n")
            for item in _parse_glossary_lines(old_text):
                existing.add(item["src"])
        new_rows = [
            item for item in rows
            if item.get("src") and item.get("dst") and item["src"] not in existing
        ]
        if not new_rows:
            return 0
        with path.open("a", encoding="utf-8") as file:
            if needs_newline:
                file.write("\n")
            if path.stat().st_size == 0:
                file.write("# Thuật ngữ tự động đề xuất bởi model; nên rà soát trước khi dùng lâu dài.\n")
            for item in new_rows:
                file.write(f"{item['src']} => {item['dst']} # auto-generated; review\n")
        return len(new_rows)

    # -- placeholder (tùy chọn, giữ để tương thích v1) ----------------------

    def protect_terms(
        self, text: str, terms: Optional[list[dict[str, str]]] = None
    ) -> tuple[str, dict[str, str]]:
        """Khóa các thuật ngữ nguồn bằng placeholder; trả về text và map khôi phục.

        Lưu ý: model nhỏ hay làm hỏng placeholder và mất ngữ cảnh giới tính nhân vật.
        Ưu tiên apply_forced_terms() (thay thẳng bằng tên thật).
        """
        selected = terms if terms is not None else self.glossary
        ordered = sorted(selected, key=lambda x: len(x.get("src", "")), reverse=True)
        protected = text
        replacements: dict[str, str] = {}
        for index, item in enumerate(ordered, 1):
            src, dst = item.get("src", ""), item.get("dst", "")
            if not src or not dst or src not in protected:
                continue
            token = f"ZXQTERM{index:04d}QXZ"
            while token in text or token in replacements:
                index += 1
                token = f"ZXQTERM{index:04d}QXZ"
            protected = protected.replace(src, token)
            replacements[token] = dst
        return protected, replacements

    @staticmethod
    def restore_terms(text: str, replacements: dict[str, str], require_all: bool = True) -> str:
        """Khôi phục placeholder và báo lỗi nếu model làm mất mã giữ chỗ."""
        result = text
        missing = []
        for token, term in replacements.items():
            if result.count(token) == 0:
                missing.append(token)
            result = result.replace(token, term)
        if require_all and missing:
            raise InvalidTranslation("Model làm mất placeholder thuật ngữ: " + ", ".join(missing))
        return result


# ---------------------------------------------------------------------------
# Quét file bản dịch
# ---------------------------------------------------------------------------

def scan_text(
    text: str,
    check_han: bool = True,
    max_han_run: int = 8,
) -> list[tuple[int, str, str]]:
    """Trả về (số_dòng, mã_lỗi, nội_dung_dòng) cho các dòng đáng ngờ."""
    hits: list[tuple[int, str, str]] = []
    skip_han = False
    prev_line, run = None, 0

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        folded = line.casefold()

        if all(key in folded for key in _UNTRANSLATED_TAG_KEYS):
            hits.append((line_number, "UNTRANSLATED", line))
            skip_han = True
            prev_line, run = None, 0
            continue

        seen: set[str] = set()
        for issue in _check_line(line, is_edge=False, check_preamble=True):
            if issue.code not in seen:
                seen.add(issue.code)
                hits.append((line_number, issue.code, line))

        # Dòng giống hệt lặp liên tiếp
        if line == prev_line and len(line) >= 12:
            run += 1
            if run == 3:
                hits.append((line_number, "REPEAT", line))
        else:
            prev_line, run = line, 1

        if len(_FOREIGN_RE.findall(line)) >= 6:
            hits.append((line_number, "FOREIGN_SCRIPT", line))

        has_han = bool(_HAN_RE.search(line))
        if skip_han and not has_han:
            skip_han = False  # đã hết khối bản gốc chưa dịch
        if check_han and has_han and not skip_han and not line.startswith("##"):
            han_count = len(_HAN_RE.findall(line))
            longest = max(len(m.group(0)) for m in _HAN_RUN_RE.finditer(line))
            if longest >= max_han_run or (han_count >= 4 and han_count / len(line) >= 0.3):
                hits.append((line_number, "HAN_REMAINS", line))

    return hits


def scan_file(
    path: str | Path,
    context: int = 0,
    as_json: bool = False,
    check_han: bool = True,
) -> int:
    file_path = Path(path)
    if not file_path.is_file():
        print(f"LỖI: Không tìm thấy file: {file_path}")
        return 2
    text = _read_text(file_path)
    hits = scan_text(text, check_han=check_han)
    if as_json:
        print(json.dumps(
            [{"line": n, "code": c, "text": t} for n, c, t in hits],
            ensure_ascii=False, indent=2,
        ))
        return 1 if hits else 0
    if not hits:
        print(f"Không phát hiện dòng đáng ngờ trong: {file_path}")
        print("Lưu ý: đây là kiểm tra theo quy tắc, không bảo đảm phát hiện mọi lỗi.")
        return 0

    lines = text.splitlines()

    def short(s: str, limit: int = 180) -> str:
        return s if len(s) <= limit else s[: limit - 3] + "..."

    print(f"File: {file_path}")
    for line_number, code, line in hits:
        print(f"Dòng {line_number} [{code}]: {short(line)}")
        if context > 0:
            for j in range(max(1, line_number - context), min(len(lines), line_number + context) + 1):
                if j != line_number and lines[j - 1].strip():
                    print(f"    {j}: {short(lines[j - 1].strip(), 120)}")
    summary = Counter(code for _, code, _ in hits)
    print(f"\nTổng cộng: {len(hits)} dòng đáng ngờ ("
          + ", ".join(f"{code}: {n}" for code, n in summary.most_common()) + ").")
    print("File gốc không bị sửa. Hãy kiểm tra các dòng được liệt kê trước khi chỉnh sửa.")
    return 1


def show_glossary(paths: list[str]) -> int:
    terms = TermManager.from_files(paths)
    if not terms.glossary:
        print("Không đọc được mục glossary nào.")
        return 2
    counts: Counter = Counter()
    for item in terms.glossary:
        mode = terms.entry_mode(item)
        counts[mode] += 1
        info = f"  # {item['info']}" if item.get("info") else ""
        print(f"[{mode:5}] {item['src']} => {item['dst']}{info}")
    print(f"\nTổng: {len(terms.glossary)} mục (ép: {counts['force']}, gợi ý: {counts['hint']}).")
    print("Muốn đổi cách xử lý một mục, thêm nhãn '# force' hoặc '# hint' ở cuối dòng.")
    return 0



if __name__ == "__main__":
    main()