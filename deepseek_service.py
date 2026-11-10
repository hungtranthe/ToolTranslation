# -*- coding: utf-8 -*-
"""Dịch truyện bằng DeepSeek, chạy nền trong app (chuyển từ TranByDeepseak.py).

Khác bản script gốc:
  - không dùng input()/sys.exit(): mọi lựa chọn (file, phạm vi chương...) nhận qua tham số;
  - chạy trong một luồng, dừng được giữa chừng, trạng thái đọc bằng DeepSeekManager.status();
  - gọi API bằng urllib (giao thức OpenAI-compatible /chat/completions) nên không cần cài `openai`;
  - tiến trình lưu kèm file nguồn + phạm vi chương để không resume nhầm sang file khác.
Logic chia chunk, thuật ngữ, prompt, retry/đổi key giữ nguyên như bản gốc.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

DEFAULTS = {
    "model": "deepseek-flash",
    "base_url": "https://api.deepseek.com",
    "chunk_target_chars": 7500,
    "max_chars": 8000,
    "max_retries_per_chunk": 10,
    "initial_retry_delay": 5,
    "delay_between_calls": 1,
    "request_timeout": 300,
}

LogFn = Callable[[str, str], None]


class DeepSeekError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ============================================================ đọc file / thuật ngữ

def read_text_file(path) -> str:
    """UTF-8 (chấp nhận BOM) trước, lỗi thì thử UTF-16."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="utf-16") as f:
            return f.read()


def load_terminology(path: Optional[Path], log: LogFn) -> list:
    """Các dạng dòng: 原文 => Dịch | 原文 -> Dịch | 原文<TAB>Dịch | 原文 = Dịch. Dòng # là chú thích."""
    terms: list = []
    if not path or not Path(path).exists():
        log(f"[DeepSeek] Không có file thuật ngữ ({path}); dịch không dùng glossary.", "WARNING")
        return terms
    for number, raw in enumerate(read_text_file(path).splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        source = target = None
        for sep in ("=>", "->", "\t", "="):
            if sep in line:
                a, b = line.split(sep, 1)
                source, target = a.strip(), b.strip()
                break
        if not source or not target:
            log(f"[DeepSeek] Bỏ qua dòng thuật ngữ {number}: {raw}", "WARNING")
            continue
        terms.append({"source": source, "target": target})
    terms.sort(key=lambda x: len(x["source"]), reverse=True)  # dài trước ngắn
    log(f"[DeepSeek] Đã tải {len(terms)} thuật ngữ.", "INFO")
    return terms


def relevant_terms(chunk_text: str, terms: list) -> list:
    return [t for t in terms if t["source"] in chunk_text]


def build_terminology_prompt(chunk_text: str, terms: list) -> str:
    rel = relevant_terms(chunk_text, terms)
    if not rel:
        return ""
    lines = ["=== THUẬT NGỮ BẮT BUỘC ===",
             "Khi gặp các thuật ngữ dưới đây, ưu tiên sử dụng đúng cách dịch tương ứng:", ""]
    lines += [f"- {t['source']} => {t['target']}" for t in rel]
    lines += ["", "Không tự ý thay đổi cách dịch của các thuật ngữ trên.", "=== HẾT THUẬT NGỮ ===", ""]
    return "\n".join(lines)


# ============================================================ chia chunk

def clean_content(content: str) -> str:
    """Dọn các dòng rác do log của công cụ khác lẫn vào file."""
    content = re.sub(r"✅ Đã lưu thành công!", "", content)
    content = re.sub(r"127\.0\.0\.1.*?200 -.*?(?:\n|$)", "", content)
    content = re.sub(r"📥 \[Khối thứ \d+\].*?(?:\n|$)", "", content)
    return content


def split_into_chunks(content: str, start_chapter: int, end_chapter: float,
                      max_chars: int, target_chars: int) -> list:
    """Chương bắt đầu bằng dòng '##'. Ưu tiên cắt giữa các đoạn; đoạn dài hơn max_chars được giữ nguyên.
    Tiêu đề chương chỉ nằm ở part 1."""
    chunks: list = []
    state = {"title": None, "body": [], "number": 0}

    def flush(title, body, number):
        if title is None or not body or number < start_chapter or number > end_chapter:
            return
        paragraphs = [p for p in body if p.strip()]
        if not paragraphs:
            return
        clean_title = title.strip()
        title_len = len(f"{clean_title}\n\n")
        full = "\n\n".join(paragraphs)
        if title_len + len(full) <= max_chars:
            chunks.append({"chapter": number, "part": 1, "total_parts": 1,
                           "text": f"{clean_title}\n\n{full}"})
            return
        parts, cur, cur_len = [], [], 0
        for paragraph in paragraphs:
            plen = len(paragraph)
            if not parts:
                part_max = max_chars - title_len
                if part_max <= 0:
                    part_max = max_chars
            else:
                part_max = max_chars
            if plen > part_max:
                if cur:
                    parts.append("\n\n".join(cur))
                    cur, cur_len = [], 0
                parts.append(paragraph)
                continue
            new_len = cur_len + (2 if cur else 0) + plen
            if cur and new_len > part_max:
                parts.append("\n\n".join(cur))
                cur, cur_len = [paragraph], plen
                continue
            cur.append(paragraph)
            cur_len = new_len
        if cur:
            parts.append("\n\n".join(cur))
        total = len(parts)
        for index, part_text in enumerate(parts, start=1):
            text = f"{clean_title}\n\n{part_text}" if index == 1 else part_text
            chunks.append({"chapter": number, "part": index, "total_parts": total, "text": text})

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            flush(state["title"], state["body"], state["number"])
            state["title"], state["body"] = stripped, []
            state["number"] += 1
        elif state["title"] is not None and stripped:
            state["body"].append(line)
    flush(state["title"], state["body"], state["number"])
    return chunks


def count_chapters(path) -> int:
    try:
        return sum(1 for line in read_text_file(path).splitlines() if line.strip().startswith("##"))
    except (OSError, UnicodeError):
        return 0


# ============================================================ prompt + API

def build_translation_prompt(chunk_text: str, terms: list) -> str:
    glossary = build_terminology_prompt(chunk_text, terms)
    return f"""
Bạn là một biên tập viên dịch thuật tiểu thuyết/ light novel chuyên nghiệp,
chuyên dịch tiểu thuyết tiếng Trung sang tiếng Việt.

NHIỆM VỤ:

1. Dịch toàn bộ nội dung tiếng Trung sang tiếng Việt.

2. Giữ nguyên ý nghĩa, ngữ cảnh, sắc thái và văn phong của nguyên tác.

3. Tên riêng, địa danh, tổ chức, kỹ năng, vật phẩm và thuật ngữ
   phải nhất quán trong toàn bộ bản dịch.

4. Nếu có phần thuật ngữ bắt buộc bên dưới,
   phải ưu tiên sử dụng đúng bản dịch được cung cấp.

5. Không tự ý thêm nội dung không có trong nguyên tác.

6. Không tóm tắt.

7. Không giải thích quá trình dịch.

8. Không thêm lời mở đầu như:
   "Bản dịch:"
   "Dưới đây là bản dịch:"
   hoặc bất kỳ lời bình luận nào.

9. Chỉ trả về bản dịch cuối cùng.

10. Giữ nguyên cấu trúc đoạn văn của nội dung được cung cấp.
    Không tự ý thêm tiêu đề, đánh số hoặc markdown.

{glossary}

=== NỘI DUNG CẦN DỊCH ===

{chunk_text}

=== HẾT NỘI DUNG ===
"""


class ApiFailure(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def call_deepseek(api_key: str, base_url: str, model: str, prompt: str, timeout: int) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "reasoning_effort": "none",
        "temperature": 0.3,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise ApiFailure(f"HTTP {exc.code} {detail}", exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiFailure(f"connection/timeout: {exc}")
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ApiFailure(f"Phản hồi không đúng dạng: {str(data)[:200]}")
    if not text or not text.strip():
        raise ApiFailure("API trả về nội dung rỗng.")
    return text.strip()


def classify_api_error(error: Exception) -> str:
    message = str(error).lower()
    status = getattr(error, "status", 0)
    if status in (401, 402, 429) or any(x in message for x in ("429", "rate limit", "insufficient", "402")):
        return "KEY"
    if status in (500, 502, 503, 504) or any(x in message for x in ("502", "503", "504")):
        return "SERVER"
    if any(x in message for x in ("timeout", "timed out", "connection", "network")):
        return "NETWORK"
    return "OTHER"


# ============================================================ một lần dịch

class Job:
    """Một lần dịch một file. Chạy trong một luồng; gọi stop() để dừng sau chunk đang xử lý."""

    def __init__(self, *, input_path: Path, output_path: Path, glossary_path: Optional[Path],
                 api_keys: list, model: str, base_url: str, start_chapter: int, end_chapter: float,
                 allow_append: bool, log: LogFn, options: Optional[dict] = None):
        self.input_path, self.output_path, self.glossary_path = Path(input_path), Path(output_path), glossary_path
        self.api_keys, self.model, self.base_url = list(api_keys), model, base_url.rstrip("/")
        self.start_chapter, self.end_chapter = start_chapter, end_chapter
        self.allow_append = allow_append
        self.log = log
        self.opt = {**DEFAULTS, **(options or {})}
        self.progress_path = Path(str(self.output_path) + ".progress.json")
        self.error_log = Path(str(self.output_path) + ".error.log")
        self.stop_event = threading.Event()
        self.key_index = 0
        self.status = {
            "state": "starting", "input": str(self.input_path), "output": str(self.output_path),
            "chunk": 0, "total_chunks": 0, "chapter": 0, "part": 0, "total_parts": 0,
            "chars_done": 0, "key": 1, "keys": len(self.api_keys), "message": "", "error": "",
            "started_at": time.time(), "finished_at": None,
        }

    # ---- tiến trình ----
    def _range_sig(self) -> dict:
        end = None if self.end_chapter == float("inf") else int(self.end_chapter)
        return {"source": str(self.input_path), "start_chapter": self.start_chapter, "end_chapter": end}

    def load_progress(self) -> tuple[int, Optional[dict]]:
        if not self.progress_path.exists():
            return 0, None
        try:
            data = json.loads(self.progress_path.read_text(encoding="utf-8"))
            return int(data.get("next_chunk", 0)), data
        except (OSError, ValueError, TypeError):
            self.log("[DeepSeek] Không đọc được file progress, coi như chưa có.", "WARNING")
            return 0, None

    def save_progress(self, next_chunk: int, total: int) -> None:
        tmp = Path(str(self.progress_path) + ".tmp")
        data = {"next_chunk": next_chunk, "total_chunks": total, **self._range_sig(),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.progress_path)

    def append_output(self, text: str) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        need_sep = self.output_path.exists() and self.output_path.stat().st_size > 0
        with open(self.output_path, "a", encoding="utf-8") as f:
            if need_sep:
                f.write("\n\n")
            f.write(text)

    def log_error(self, index: int, chunk: dict, error: str) -> None:
        with open(self.error_log, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 80}\nChunk: {index + 1}\nChapter: {chunk['chapter']}\n"
                    f"Part: {chunk['part']}/{chunk['total_parts']}\nError: {error}\n{'-' * 80}\n"
                    f"{chunk['text']}\n")

    # ---- gọi API có retry ----
    def _sleep(self, seconds: float) -> bool:
        """Ngủ nhưng thức dậy ngay nếu bị yêu cầu dừng. True = bị dừng."""
        return self.stop_event.wait(seconds)

    def translate_chunk(self, chunk: dict, terms: list) -> Optional[str]:
        retries = 0
        max_retries = int(self.opt["max_retries_per_chunk"])
        prompt = build_translation_prompt(chunk["text"], terms)
        while retries < max_retries:
            if self.stop_event.is_set():
                return None
            try:
                self.log(f"[DeepSeek]   -> Gọi API (key {self.key_index + 1}/{len(self.api_keys)})...", "INFO")
                result = call_deepseek(self.api_keys[self.key_index], self.base_url, self.model, prompt,
                                       int(self.opt["request_timeout"]))
                self._sleep(float(self.opt["delay_between_calls"]))
                return result
            except ApiFailure as error:
                kind = classify_api_error(error)
                retries += 1
                self.log(f"[DeepSeek]   [Lỗi {retries}/{max_retries}] {error}", "WARNING")
                if kind == "KEY":
                    if len(self.api_keys) <= 1:
                        self.log("[DeepSeek] Key hiện tại bị giới hạn/hết tiền và không còn key khác.", "ERROR")
                        return None
                    self.key_index = (self.key_index + 1) % len(self.api_keys)
                    self.status["key"] = self.key_index + 1
                    self.log(f"[DeepSeek] Chuyển sang key {self.key_index + 1}/{len(self.api_keys)}.", "WARNING")
                    wait = 2
                elif kind == "SERVER":
                    wait = min(float(self.opt["initial_retry_delay"]) * (2 ** (retries - 1)), 120)
                    self.log(f"[DeepSeek]   [Server] Chờ {wait:g}s rồi thử lại...", "WARNING")
                elif kind == "NETWORK":
                    wait = min(3 * retries, 30)
                    self.log(f"[DeepSeek]   [Network] Chờ {wait:g}s...", "WARNING")
                else:
                    wait = 3
                if self._sleep(wait):
                    return None
        return None

    # ---- vòng chạy chính ----
    def run(self) -> None:
        st = self.status
        try:
            st["state"] = "running"
            self.log(f"[DeepSeek] Bắt đầu: {self.input_path.name} -> {self.output_path.name}", "INFO")
            content = clean_content(read_text_file(self.input_path))
            terms = load_terminology(self.glossary_path, self.log)
            chunks = split_into_chunks(content, self.start_chapter, self.end_chapter,
                                       int(self.opt["max_chars"]), int(self.opt["chunk_target_chars"]))
            if not chunks:
                raise DeepSeekError("NO_CHAPTERS", "Không tìm thấy chương nào (dòng bắt đầu bằng ##) trong phạm vi yêu cầu.")
            total = len(chunks)
            st["total_chunks"] = total
            start_index, saved = self.load_progress()
            if saved is not None and start_index > 0:
                if start_index >= total:
                    st["chunk"] = total
                    st["message"] = "Tất cả chunk trong phạm vi này đã được dịch."
                    st["state"] = "done"
                    self.log("[DeepSeek] " + st["message"], "SUCCESS")
                    return
                self.log(f"[DeepSeek] Tiếp tục từ chunk {start_index + 1}/{total}.", "INFO")
            st["chunk"] = start_index
            self.log(f"[DeepSeek] {total} chunk, {len(terms)} thuật ngữ.", "INFO")

            for index in range(start_index, total):
                if self.stop_event.is_set():
                    st["state"], st["message"] = "stopped", f"Đã dừng trước chunk {index + 1}/{total}."
                    self.log("[DeepSeek] " + st["message"], "WARNING")
                    return
                chunk = chunks[index]
                st.update(chunk=index, chapter=chunk["chapter"], part=chunk["part"], total_parts=chunk["total_parts"],
                          message=f"Đang dịch chunk {index + 1}/{total} (chương {chunk['chapter']}, "
                                  f"phần {chunk['part']}/{chunk['total_parts']})")
                self.log(f"[DeepSeek] Chunk {index + 1}/{total} | chương {chunk['chapter']} "
                         f"phần {chunk['part']}/{chunk['total_parts']} | {len(chunk['text'])} ký tự | "
                         f"thuật ngữ áp dụng: {len(relevant_terms(chunk['text'], terms))}", "INFO")
                result = self.translate_chunk(chunk, terms)
                if result is None:
                    if self.stop_event.is_set():
                        st["state"], st["message"] = "stopped", f"Đã dừng ở chunk {index + 1}/{total}."
                        self.log("[DeepSeek] " + st["message"], "WARNING")
                        return
                    self.log_error(index, chunk, f"Không thể dịch sau {self.opt['max_retries_per_chunk']} lần retry.")
                    raise DeepSeekError("CHUNK_FAILED",
                                        f"Không thể dịch chunk {index + 1}. Chi tiết: {self.error_log}. "
                                        "Không ghi bản gốc vào file dịch; bấm Bắt đầu để tiếp tục từ chunk này.")
                self.append_output(result)
                self.save_progress(index + 1, total)
                st["chunk"] = index + 1
                st["chars_done"] += len(chunk["text"])
                self.log(f"[DeepSeek] Đã lưu chunk {index + 1}/{total}.", "SUCCESS")
            st["state"], st["message"] = "done", f"Hoàn tất: {self.output_path}"
            self.log("[DeepSeek] " + st["message"], "SUCCESS")
        except DeepSeekError as exc:
            st["state"], st["error"], st["message"] = "error", exc.message, exc.message
            self.log(f"[DeepSeek] {exc.message}", "ERROR")
        except Exception as exc:  # noqa: BLE001
            st["state"], st["error"], st["message"] = "error", f"{type(exc).__name__}: {exc}", str(exc)
            self.log(f"[DeepSeek] Lỗi: {type(exc).__name__}: {exc}", "ERROR")
        finally:
            st["finished_at"] = time.time()


# ============================================================ quản lý

def mask_key(key: str) -> str:
    key = key.strip()
    return key if len(key) <= 10 else f"{key[:5]}…{key[-4:]}"


def parse_keys(text) -> list:
    if isinstance(text, (list, tuple)):
        raw = list(text)
    else:
        raw = re.split(r"[\s,;]+", str(text or ""))
    seen, out = set(), []
    for k in raw:
        k = str(k).strip().strip('"').strip("'")
        if k and not k.startswith("#") and k not in seen:
            seen.add(k)
            out.append(k)
    return out


class DeepSeekManager:
    """Giữ tối đa một job đang chạy tại một thời điểm."""

    def __init__(self, log: LogFn):
        self.log = log
        self.job: Optional[Job] = None
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.RLock()

    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, *, input_path, output_path, glossary_path, api_keys, model=None, base_url=None,
              start_chapter=1, end_chapter=None, allow_append=False, options=None) -> dict:
        with self.lock:
            if self.is_running():
                raise DeepSeekError("ALREADY_RUNNING", "Đang có một lượt dịch DeepSeek chạy. Hãy dừng nó trước.")
            input_path, output_path = Path(input_path), Path(output_path)
            if not input_path.is_file():
                raise DeepSeekError("NO_INPUT", f"Không tìm thấy file cần dịch: {input_path}")
            if input_path.resolve() == output_path.resolve():
                raise DeepSeekError("SAME_FILE", "File đầu ra trùng file nguồn; hãy đặt tên khác.")
            keys = parse_keys(api_keys)
            if not keys:
                raise DeepSeekError("NO_API_KEY", "Chưa có API key DeepSeek.")
            try:
                start = int(start_chapter or 1)
                end = float("inf") if end_chapter in (None, "", 0) else int(end_chapter)
            except (TypeError, ValueError):
                raise DeepSeekError("BAD_RANGE", "Số chương không hợp lệ.")
            if start < 1 or (end != float("inf") and end < start):
                raise DeepSeekError("BAD_RANGE", "Phạm vi chương không hợp lệ.")
            job = Job(input_path=input_path, output_path=output_path,
                      glossary_path=Path(glossary_path) if glossary_path else None, api_keys=keys,
                      model=model or DEFAULTS["model"], base_url=base_url or DEFAULTS["base_url"],
                      start_chapter=start, end_chapter=end, allow_append=allow_append, log=self.log, options=options)
            # Kiểm tra trước khi tạo luồng: tiến trình cũ có thuộc đúng file/phạm vi này không?
            idx, saved = job.load_progress()
            if saved is not None and idx > 0:
                sig = job._range_sig()
                # Tiến trình của script cũ không có "source": chấp nhận. Có thì phải khớp file + phạm vi.
                if any(saved.get(k) != sig[k] for k in sig if k in saved):
                    raise DeepSeekError(
                        "PROGRESS_MISMATCH",
                        "File đầu ra này đã có tiến trình của một file hoặc phạm vi chương khác "
                        f"({saved.get('source', 'không rõ')}). Chọn tên đầu ra khác hoặc bấm 'Dịch lại từ đầu'.")
            elif output_path.exists() and output_path.stat().st_size > 0 and not allow_append:
                raise DeepSeekError(
                    "OUTPUT_EXISTS",
                    "File đầu ra đã tồn tại nhưng không có tiến trình, nên không biết đã dịch đến đâu. "
                    "Xác nhận để ghi nối tiếp, hoặc chọn tên đầu ra khác.")
            self.job = job
            self.thread = threading.Thread(target=job.run, daemon=True, name="deepseek-job")
            self.thread.start()
            return self.status()

    def stop(self) -> dict:
        with self.lock:
            if self.job and self.is_running():
                self.job.stop_event.set()
                self.job.status["state"] = "stopping"
                self.job.status["message"] = "Đang dừng sau chunk hiện tại..."
                self.log("[DeepSeek] Yêu cầu dừng.", "WARNING")
            return self.status()

    def reset(self, output_path) -> dict:
        """Xóa tiến trình và sao lưu file đầu ra để dịch lại từ đầu."""
        with self.lock:
            if self.is_running():
                raise DeepSeekError("ALREADY_RUNNING", "Hãy dừng lượt dịch đang chạy trước.")
            out = Path(output_path)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            moved = []
            for p in (out, Path(str(out) + ".progress.json"), Path(str(out) + ".error.log")):
                if p.exists():
                    dest = p.with_name(p.name + f".bak_{stamp}")
                    shutil.move(str(p), str(dest))
                    moved.append(dest.name)
            self.log(f"[DeepSeek] Dịch lại từ đầu; đã sao lưu {len(moved)} file.", "WARNING")
            self.job = None
            return {"backups": moved}

    def status(self) -> dict:
        job = self.job
        if job is None:
            return {"state": "idle"}
        s = dict(job.status)
        s["running"] = self.is_running()
        total = s.get("total_chunks") or 0
        s["percent"] = round(100.0 * s.get("chunk", 0) / total, 1) if total else 0.0
        return s
