# -*- coding: utf-8 -*-
"""Xem cấu trúc dữ liệu Bangumi: tên trang Moegirl -> id Bangumi -> thông tin nhân vật.

    python inspect_bgm.py --dir "D:\\8man\\code\\stitch_noveltranslator_desktop_gui\\bangumi" 灰原哀 工藤新一 比企谷八幡

Chỉ đọc, không ghi gì. In tối đa --n ký tự cho mỗi mục.
"""
import argparse, json
from pathlib import Path


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def show(label, obj, n):
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    print(f"{label}: {s[:n]}{' ...(cắt)' if len(s) > n else ''}")


def ids_of(v):
    if isinstance(v, (int, str)):
        return [v]
    if isinstance(v, list):
        return [x for i in v for x in ids_of(i)]
    if isinstance(v, dict):
        for k in ("id", "bgm_id", "character_id"):
            if k in v:
                return ids_of(v[k])
        return [x for i in v.values() for x in ids_of(i)]
    return []


def find_info(info, i):
    if isinstance(info, dict):
        for k in (str(i), i):
            if k in info:
                return info[k]
        return None
    if isinstance(info, list):
        for it in info:
            if isinstance(it, dict) and str(it.get("id")) == str(i):
                return it
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("titles", nargs="+")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--n", type=int, default=1500)
    a = ap.parse_args()
    d = Path(a.dir)

    m2b = load(d / "moegirl2bgm.json")
    info = load(d / "bgm_info.json")
    print(f"moegirl2bgm: {type(m2b).__name__}, {len(m2b):,} mục")
    print(f"bgm_info: {type(info).__name__}, {len(info):,} mục")
    sample = next(iter(info.items())) if isinstance(info, dict) else (info[0] if info else None)
    show("Mẫu 1 mục của bgm_info", sample, a.n)

    for t in a.titles:
        print(f"\n=== {t} ===")
        v = m2b.get(t) if isinstance(m2b, dict) else None
        if v is None:
            print("Không có trong moegirl2bgm.")
            continue
        show("moegirl2bgm", v, 300)
        for i in ids_of(v)[:3]:
            rec = find_info(info, i)
            if rec is None:
                print(f"  id {i}: không thấy trong bgm_info")
            else:
                show(f"  bgm_info[{i}]", rec, a.n)


if __name__ == "__main__":
    main()
