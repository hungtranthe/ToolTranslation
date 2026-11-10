# -*- coding: utf-8 -*-
"""Kana -> romaji Hepburn (không dấu macron), chỉ thư viện chuẩn."""
_BASE = {
 'あ':'a','い':'i','う':'u','え':'e','お':'o',
 'か':'ka','き':'ki','く':'ku','け':'ke','こ':'ko',
 'さ':'sa','し':'shi','す':'su','せ':'se','そ':'so',
 'た':'ta','ち':'chi','つ':'tsu','て':'te','と':'to',
 'な':'na','に':'ni','ぬ':'nu','ね':'ne','の':'no',
 'は':'ha','ひ':'hi','ふ':'fu','へ':'he','ほ':'ho',
 'ま':'ma','み':'mi','む':'mu','め':'me','も':'mo',
 'や':'ya','ゆ':'yu','よ':'yo',
 'ら':'ra','り':'ri','る':'ru','れ':'re','ろ':'ro',
 'わ':'wa','を':'o','ん':'n',
 'が':'ga','ぎ':'gi','ぐ':'gu','げ':'ge','ご':'go',
 'ざ':'za','じ':'ji','ず':'zu','ぜ':'ze','ぞ':'zo',
 'だ':'da','ぢ':'ji','づ':'zu','で':'de','ど':'do',
 'ば':'ba','び':'bi','ぶ':'bu','べ':'be','ぼ':'bo',
 'ぱ':'pa','ぴ':'pi','ぷ':'pu','ぺ':'pe','ぽ':'po',
 'ゔ':'vu',
}
_SMALL_Y = {'ゃ': 'ya', 'ゅ': 'yu', 'ょ': 'yo'}
_SMALL_V = {'ぁ': 'a', 'ぃ': 'i', 'ぅ': 'u', 'ぇ': 'e', 'ぉ': 'o'}
_SPECIAL_Y = {'shi', 'chi', 'ji'}


def _to_hira(s):
    return "".join(chr(ord(c) - 0x60) if '\u30a1' <= c <= '\u30f6' else c for c in s)


def kana_to_romaji(text, long_vowel="keep"):
    """'やわた かれん' -> 'Yawata Karen'. Trả None nếu chứa ký tự không phải kana.
    long_vowel: 'keep' (ー lặp nguyên âm: Yuuki) hoặc 'drop' (Yuki)."""
    s = _to_hira(str(text).strip().replace("・", " ").replace("　", " "))
    if not s or not any(c in _BASE for c in s):
        return None
    out, i, geminate = [], 0, False
    while i < len(s):
        c = s[i]
        if c == ' ':
            out.append(' '); i += 1; continue
        if c == 'っ':
            geminate = True; i += 1; continue
        if c == 'ー':
            if long_vowel == "keep" and out and out[-1][-1:] in "aiueo":
                out.append(out[-1][-1])
            i += 1; continue
        if c not in _BASE:
            return None
        r = _BASE[c]
        nxt = s[i + 1] if i + 1 < len(s) else ''
        if nxt in _SMALL_Y:
            r = r[:-1] + (_SMALL_Y[nxt][1:] if r in _SPECIAL_Y else _SMALL_Y[nxt])
            i += 1
        elif nxt in _SMALL_V:
            r = r[:-1] + _SMALL_V[nxt]
            i += 1
        if geminate:
            r = ('t' if r.startswith('ch') else r[0]) + r
            geminate = False
        out.append(r)
        i += 1
    return " ".join(w.capitalize() for w in "".join(out).split(" ") if w) or None


if __name__ == "__main__":
    tests = {
        'やわた かれん': 'Yawata Karen', 'ひきがや はちまん': 'Hikigaya Hachiman',
        'アイ': 'Ai', 'ゼロツー': 'Zerotsuu', 'ゼロツー ': 'Zerotsuu', 'アチャ子': None,
        'おおかみ みお': 'Ookami Mio', 'しょうこ': 'Shouko', 'ちゃん': 'Chan',
        'がっこう': 'Gakkou', 'ケイト': 'Keito', 'ユーキ': 'Yuuki', 'ファイト': 'Faito',
        'じゅん': 'Jun', 'さんじい': 'Sanjii', 'きゃりー': 'Kyarii', 'いっぱい': 'Ippai',
        'まっちゃ': 'Matcha',
    }
    for k, v in tests.items():
        got = kana_to_romaji(k)
        print(("OK  " if got == v else "FAIL"), repr(k), '->', got, '(mong đợi', v, ')')
    print(kana_to_romaji('ユーキ', 'drop'))