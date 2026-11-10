#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
polish_qwen.py - hậu xử lý bản dịch của dichTrungsangViet.py bằng Qwen local (Ollama).

Đặt cùng thư mục với dichTrungsangViet.py. Dùng lại FILE_OUTPUT, glossary, get_client,
check_translation... của engine nên không cần cấu hình lại đường dẫn.

  python polish_qwen.py polish --limit 20   # thử 20 đoạn đầu -> bandich.txt.polish.sample.txt
  python polish_qwen.py polish              # chỉnh văn phong toàn bộ (Qwen2.5 7B) -> bandich.txt.polish.txt
  python polish_qwen.py han                 # vá chữ Hán sót (Qwen3 4B) -> bandich.txt.final.txt
  python polish_qwen.py all                 # polish rồi han

Nguyên tắc an toàn: bản của Qwen chỉ được nhận khi qua kiểm tra (đủ số đoạn, độ dài hợp lý,
không mất tên trong glossary, không thêm chữ Hán, không lọt chỉ dẫn). Không đạt -> GIỮ bản HY-MT.
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dichTrungsangViet as E  # noqa: E402

# Kiểm tra tên đúng bằng `ollama list` / thư viện Ollama; sửa nếu tag khác.
HAN_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
POLISH_MODEL = "qwen2.5:7b-instruct-q4_K_M"

POLISH_TEMPS = (0.3, 0.15)       # nhiệt độ cho các lần thử
POLISH_MIN_RATIO = 0.85          # độ dài mới / cũ
POLISH_MAX_RATIO = 1.25
NOSRC_GROUP_CHARS = 700          # khi không có nguồn: gom đoạn tới ngần này ký tự

OUT = Path(str(E.FILE_OUTPUT))
SIDECAR = Path(str(OUT) + ".chunks.jsonl")          # do GUI ghi (nguồn + bản dịch từng chunk)
POLISH_FILE = Path(str(OUT) + ".polish.txt")
SAMPLE_FILE = Path(str(OUT) + ".polish.sample.txt")
FINAL_FILE = Path(str(OUT) + ".final.txt")
CACHE_FILE = Path(str(OUT) + ".polish.cache.jsonl")


# ------------------------------------------------------------------ tiện ích
def strip_think(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()
    text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    return text


def chat(client, model, prompt, temperature, seed, max_tokens):
    """Gọi model (không stream), retry nhẹ khi lỗi mạng. Trả về (text, finish_reason)."""
    last = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                temperature=temperature, top_p=0.8, seed=seed, max_tokens=max_tokens)
            choice = resp.choices[0]
            return strip_think(choice.message.content), choice.finish_reason
        except Exception as error:  # noqa: BLE001
            last = error
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Gọi {model} thất bại: {last}")


def unload_model(model):
    """Giải phóng VRAM trước khi nạp model khác (keep_alive=0)."""
    base = E.BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        req = urllib.request.Request(
            base + "/api/generate", method="POST",
            data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:  # noqa: BLE001
        pass


def load_glossary_all():
    return E.load_glossary(E.GLOSSARY_FILE) + E.load_glossary(E.AUTO_GLOSSARY_FILE)


def han_count(text):
    return len(E.HAN_RE.findall(text or ""))


# ------------------------------------------------------------------ PHA 1: chỉnh văn phong (Qwen2.5 7B)
def load_units():
    """Trả về (units, có_nguồn). Mỗi unit = {'src': str|None, 'dst': str}."""
    if not OUT.is_file():
        raise SystemExit(f"Không thấy file bản dịch: {OUT}")
    out_text = E.read_text_file(OUT).strip()
    if SIDECAR.is_file():
        recs = {}
        for raw in E.read_text_file(SIDECAR).split("\n"):
            if raw.strip():
                try:
                    r = json.loads(raw)
                    recs[int(r["index"])] = r
                except (ValueError, KeyError, TypeError):
                    continue
        ordered = [recs[i] for i in sorted(recs)]
        if ordered and "\n\n".join(r["dst"] for r in ordered).strip() == out_text:
            print(f"[Polish] Có nguồn: {len(ordered)} chunk khớp với {OUT.name}.")
            return [{"src": r["src"], "dst": r["dst"]} for r in ordered], True
        print("[Polish] CẢNH BÁO: sidecar không khớp bản dịch (đã sửa tay/dịch lại chunk lỗi?); "
              "chuyển sang chế độ KHÔNG nguồn.")
    else:
        print("[Polish] Không có sidecar (chạy engine ngoài GUI); chuyển sang chế độ KHÔNG nguồn.")
    paragraphs = [p for p in out_text.split("\n\n") if p.strip()]
    units, cur = [], ""
    for p in paragraphs:
        if cur and len(cur) + len(p) > NOSRC_GROUP_CHARS:
            units.append({"src": None, "dst": cur})
            cur = p
        else:
            cur = p if not cur else cur + "\n\n" + p
    if cur:
        units.append({"src": None, "dst": cur})
    return units, False


def keep_terms(src, dst, glossary):
    """Các cặp (nguồn, đích) mà bản HY-MT đã dịch đúng và Qwen phải giữ nguyên."""
    pairs = []
    if src:
        for m in E.relevant_glossary(src, glossary):
            if m["dst"] and not E.HAN_RE.search(m["dst"]) and m["dst"] in dst:
                pairs.append((m["src"], m["dst"]))
    else:
        for m in E.relevant_glossary(dst, glossary):  # văn bản Việt: m['src'] là tên Latin
            if m["src"] and not E.HAN_RE.search(m["src"]):
                pairs.append((m["dst"], m["src"]))
    seen, out = set(), []
    for a, b in pairs:
        if b not in seen:
            seen.add(b)
            out.append((a, b))
    return out


def build_polish_prompt(src, dst, pairs):
    rules = [
        "Giữ nguyên số đoạn và thứ tự đoạn; không gộp, không tách, không bỏ câu, không thêm ý, không tóm tắt.",
        "Chỉ sửa lỗi ngữ pháp, dùng từ gượng, câu cụt/rườm; làm văn phong tự nhiên, đúng giọng tiểu thuyết.",
        "Kết quả không được chứa chữ Hán; nếu còn chữ Hán thì dịch sang tiếng Việt.",
        "Chỉ xuất ra bản dịch đã chỉnh, không giải thích, không lời dẫn.",
    ]
    if pairs:
        rules.append("Giữ NGUYÊN VĂN các tên/thuật ngữ sau (không đổi, không dịch lại):\n"
                     + "\n".join(f"- {a} => {b}" for a, b in pairs))
    head = ("Bạn là biên tập viên truyện dịch Trung-Việt. Hãy chỉnh sửa BẢN DỊCH tiếng Việt dưới đây "
            "cho mượt mà hơn.\n")
    if src:
        head += "Đối chiếu NGUYÊN VĂN tiếng Trung để sửa chỗ sai nghĩa.\n"
    body = "Quy tắc:\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1)) + "\n\n"
    if src:
        body += f"[NGUYÊN VĂN]\n{src}\n\n"
    body += f"[BẢN DỊCH CẦN CHỈNH]\n{dst}\n\n[BẢN DỊCH ĐÃ CHỈNH]\n"
    return head + body


def validate_polish(src, old, new, pairs, finish):
    """Trả về lý do loại, hoặc None nếu đạt."""
    if finish == "length":
        return "cụt (hết token)"
    if not new.strip():
        return "rỗng"
    for marker in E.LEAK_MARKERS:
        if marker in new and marker not in old:
            return f"lọt chỉ dẫn ({marker!r})"
    if len([x for x in old.split("\n") if x.strip()]) != len([x for x in new.split("\n") if x.strip()]):
        return "lệch số đoạn"
    ratio = len(new) / max(1, len(old))
    if not POLISH_MIN_RATIO <= ratio <= POLISH_MAX_RATIO:
        return f"độ dài lệch (tỉ lệ {ratio:.2f})"
    if han_count(new) > han_count(old):
        return "thêm chữ Hán"
    missing = [b for _a, b in pairs if b not in new]
    if missing:
        return "mất thuật ngữ: " + ", ".join(missing[:3])
    if src:
        report = E.check_translation(src, new)
        if report.hard:
            return "guard: " + "; ".join(report.hard)[:120]
    return None


def polish_unit(client, model, unit, glossary):
    src, old = unit["src"], unit["dst"]
    if old.startswith(E.UNTRANSLATED_TAG):
        return None, "chunk chưa dịch"
    pairs = keep_terms(src, old, glossary)
    prompt = build_polish_prompt(src, old, pairs)
    reason = "?"
    for attempt, temp in enumerate(POLISH_TEMPS):
        new, finish = chat(client, model, prompt, temp, 500 + attempt, len(old) + 300)
        new = E.restore_format(src, new) if src else new
        reason = validate_polish(src, old, new, pairs, finish)
        if reason is None:
            return new, "ok"
    return None, reason


def polish_phase(limit=0, model=POLISH_MODEL):
    client = E.get_client()
    glossary = load_glossary_all()
    units, has_src = load_units()
    cache = {}
    if CACHE_FILE.is_file():
        for raw in E.read_text_file(CACHE_FILE).split("\n"):
            if raw.strip():
                try:
                    r = json.loads(raw)
                    cache[r["key"]] = r["new"]
                except (ValueError, KeyError):
                    continue
    print(f"[Polish] Model {model}; {len(units)} đơn vị; đã có cache {len(cache)}.")
    results, stats, reasons = [], Counter(), Counter()
    started = time.time()
    done_new = 0
    for n, unit in enumerate(units):
        old = unit["dst"]
        if limit and n >= limit:
            results.append(old)
            continue
        key = hashlib.sha1(old.encode("utf-8")).hexdigest()
        if key in cache:
            results.append(cache[key])
            stats["cache"] += 1
            continue
        try:
            new, reason = polish_unit(client, model, unit, glossary)
        except RuntimeError as error:
            print(f"[Polish] {error}; dừng, kết quả đã xong được lưu trong cache.")
            break
        done_new += 1
        if new is None:
            results.append(old)
            stats["giữ HY-MT"] += 1
            reasons[reason] += 1
        else:
            results.append(new)
            stats["đã chỉnh"] += 1
            with CACHE_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "new": new}, ensure_ascii=False) + "\n")
        if done_new % 10 == 0:
            avg = (time.time() - started) / done_new
            left = len(units) - n - 1
            print(f"  {n + 1}/{len(units)} | trung bình {avg:.1f}s/đơn vị | còn ~{E.format_duration(avg * left)}")
    else:
        pass
    # Nếu vòng lặp bị break (lỗi), phần còn lại giữ nguyên bản cũ.
    while len(results) < len(units):
        results.append(units[len(results)]["dst"])
    target = SAMPLE_FILE if limit else POLISH_FILE
    E.atomic_write_text(target, "\n\n".join(results))
    print(f"[Polish] Xong: {dict(stats)} -> {target}")
    if reasons:
        print("[Polish] Lý do giữ bản HY-MT:", dict(reasons.most_common(6)))
    return target


# ------------------------------------------------------------------ PHA 2: vá chữ Hán sót (Qwen3 4B)
def rewrite_line(client, model, line, runs, prev_line, next_line, glossary):
    hints = E.relevant_glossary(" ".join(runs), glossary)
    hint_text = ("Thuật ngữ bắt buộc: " + "; ".join(f"{h['src']} = {h['dst']}" for h in hints) + "\n") if hints else ""
    ctx = ""
    if prev_line.strip():
        ctx += f"Dòng trước (không sửa): {prev_line.strip()[:200]}\n"
    if next_line.strip():
        ctx += f"Dòng sau (không sửa): {next_line.strip()[:200]}\n"
    prompt = (
        f"Dòng truyện tiếng Việt sau còn sót đoạn chữ Hán chưa dịch: {', '.join(runs)}.\n"
        "Hãy dịch đoạn chữ Hán đó sang tiếng Việt cho tự nhiên (tên người, địa danh, danh xưng dùng âm Hán-Việt "
        "hoặc theo thuật ngữ) và viết lại cả dòng, giữ nguyên phần đã dịch. "
        "Kết quả tuyệt đối không chứa chữ Hán. Chỉ xuất ra dòng đã viết lại, không giải thích.\n"
        f"{hint_text}{ctx}\nDòng cần sửa:\n{line}"
    )
    best, best_han = None, han_count(line)
    for t, temp in enumerate((0.2, 0.3, 0.4)):
        try:
            new, finish = chat(client, model, prompt, temp, 3000 + t, len(line) * 2 + 200)
        except RuntimeError as error:
            print(f"    {error}")
            continue
        new = new.strip("\"'“”*` ")
        h = han_count(new)
        if (finish != "length" and new and "\n" not in new and h < best_han
                and 0.6 * len(line) <= len(new) <= 1.8 * len(line) + 20):
            best, best_han = new, h
            if h == 0:
                break
    return best


def han_phase(src_path=None, model=HAN_MODEL):
    path = Path(src_path) if src_path else (POLISH_FILE if POLISH_FILE.is_file() else OUT)
    if not path.is_file():
        raise SystemExit(f"Không thấy file: {path}")
    client = E.get_client()
    glossary = load_glossary_all()
    allowed = {g["dst"] for g in glossary if E.HAN_RE.search(g["dst"])}
    lines = E.read_text_file(path).split("\n")
    todo = [i for i, ln in enumerate(lines) if E.HAN_RE.search(ln)]
    print(f"[Han] Đọc {path.name}; {len(todo)} dòng còn chữ Hán; model {model}.")
    fixed = skipped = 0
    for k, i in enumerate(todo, 1):
        line = lines[i]
        # Dòng gần như toàn chữ Hán = chunk chưa dịch (giữ bản gốc), không đụng vào.
        if han_count(line) / max(1, len(line.strip())) > 0.5:
            skipped += 1
            continue
        new, _n = E._han_word_by_word_fix(line, None, glossary)
        runs = [r for r in E.HAN_RUN_RE.findall(new) if r not in allowed]
        if runs:
            rewritten = rewrite_line(client, model, new, runs,
                                     lines[i - 1] if i else "", lines[i + 1] if i + 1 < len(lines) else "", glossary)
            if rewritten:
                new = rewritten
        if new != line:
            lines[i] = new
            fixed += 1
        if k % 20 == 0:
            print(f"  {k}/{len(todo)} dòng")
    E.atomic_write_text(FINAL_FILE, "\n".join(lines))
    left = sum(1 for ln in lines if E.HAN_RE.search(ln))
    print(f"[Han] Đã sửa {fixed} dòng, bỏ qua {skipped} dòng chưa dịch; còn {left} dòng có chữ Hán -> {FINAL_FILE}")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Hậu xử lý bản dịch bằng Qwen local.")
    ap.add_argument("mode", choices=("polish", "han", "all"))
    ap.add_argument("--limit", type=int, default=0, help="polish: chỉ xử lý N đơn vị đầu để thử")
    ap.add_argument("--polish-model", default=POLISH_MODEL)
    ap.add_argument("--han-model", default=HAN_MODEL)
    ap.add_argument("--in", dest="src", default=None, help="han: file đầu vào khác")
    args = ap.parse_args()

    if args.mode in ("polish", "all"):
        polish_phase(args.limit, args.polish_model)
        if args.mode == "all":
            unload_model(args.polish_model)
    if args.mode in ("han", "all") and not (args.mode == "all" and args.limit):
        han_phase(args.src, args.han_model)


if __name__ == "__main__":
    main()
