# -*- coding: utf-8 -*-
"""
serve_ui.py - mở 7 giao diện HTML trong trình duyệt, nối với backend thật.

Đặt CÙNG THƯ MỤC với: GuiDich.py, novel_backend.py (và dichTrungsangViet.py,
moegirl_dataset_import.py như cấu trúc cũ), cạnh thư mục stitch_noveltranslator_desktop_gui.

    python serve_ui.py
    python serve_ui.py --port 8000 --no-browser
    python serve_ui.py --source "D:\\truyen\\chuong.txt"      # mở sẵn dự án
    python serve_ui.py --root "D:\\8man\\code\\stitch_noveltranslator_desktop_gui"

Những gì được nối:
  * window.pywebview.api.<tên>(...)  -> POST /rpc/<tên>   (DesktopBridge của GuiDich.py
    + Bridge của novel_backend.py: call / token / pick_file / save_file)
  * /api/<method> và /api/events (SSE) -> NovelBackend.dispatch, có token X-Novel-Token
  * fetch() và EventSource tới /api/* được tự gắn token trong trình duyệt
  * Hộp thoại chọn file mở bằng tkinter trên máy chạy server; nếu không có tkinter
    hoặc không có màn hình, trang sẽ hỏi bạn dán đường dẫn đầy đủ.
Chỉ lắng nghe 127.0.0.1.
"""
from __future__ import annotations

import argparse
import hmac
import html
import importlib
import json
import mimetypes
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

APP_DIR = Path(__file__).resolve().parent
UI_DIRNAME = "stitch_noveltranslator_desktop_gui"

PAGES = [
    ("a", "tong-quan", "Tổng quan và cấu hình"),
    ("b", "bao-cao", "Báo cáo tác phẩm"),
    ("c", "nhan-vat", "Quản lý nhân vật mốc"),
    ("d", "moegirl", "Gợi ý thuật ngữ Moegirl"),
    ("e", "glossary", "Quản lý glossary"),
    ("f", "dich-tien-trinh", "Dịch và tiến trình"),
    ("g", "nhat-ky", "Nhật ký và kiểm tra"),
    ("h", "xung-ho", "Quản lý xưng hô"),
]
ROUTES = {route for _, route, _ in PAGES}
ROUTE_ALIASES = {
    "tongquancauhinh": "tong-quan", "tongquan": "tong-quan",
    "baocaotacpham": "bao-cao", "baocao": "bao-cao",
    "nhanvatmoc": "nhan-vat", "nhanvat": "nhan-vat",
    "goimoegirl": "moegirl", "goiymoegirl": "moegirl", "moegirl": "moegirl",
    "quanlyglossary": "glossary", "glossary": "glossary",
    "dichtientrinh": "dich-tien-trinh",
    "nhatkykiemtra": "nhat-ky", "nhatky": "nhat-ky",
    "xungho": "xung-ho", "quanlyxungho": "xung-ho",
}
PRIORITY_FILES = ("index.html", "main.html", "code.html", "code(1).html")


# --------------------------------------------------------------------------
# Thư mục giao diện
# --------------------------------------------------------------------------
def pick_root(cli_root):
    if cli_root:
        return Path(cli_root).expanduser().resolve()
    if APP_DIR.name == UI_DIRNAME:
        return APP_DIR
    if (APP_DIR / UI_DIRNAME).is_dir():
        return APP_DIR / UI_DIRNAME
    return APP_DIR


def find_folder(root: Path, letter: str):
    """Khớp theo tiền tố giao_di_n_<chữ cái>_ nên không vướng lệch tên thư mục."""
    prefix = f"giao_di_n_{letter}_"
    if not root.is_dir():
        return None
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.lower().startswith(prefix):
            return child
    return None


def find_html(folder: Path):
    candidates = list(folder.rglob("*.html"))
    if not candidates:
        return None
    for name in PRIORITY_FILES:
        for c in candidates:
            if c.name.lower() == name:
                return c
    return sorted(candidates, key=lambda p: (len(p.parts), p.name.lower()))[0]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# Hộp thoại chọn file (tkinter chạy ở tiến trình con để khỏi vướng luồng)
# --------------------------------------------------------------------------
_TK_CODE = r"""
import sys
import tkinter as tk
from tkinter import filedialog as fd
mode, title, label, pattern, initial = sys.argv[1:6]
root = tk.Tk(); root.withdraw()
try: root.attributes("-topmost", True)
except Exception: pass
types = [(label, pattern), ("All files", "*.*")]
if mode == "save":
    p = fd.asksaveasfilename(title=title, initialfile=initial, filetypes=types)
else:
    p = fd.askopenfilename(title=title, filetypes=types)
sys.stdout.buffer.write((p or "").encode("utf-8"))
"""


def native_pick(mode, title, label, pattern, initial=""):
    """Trả về đường dẫn, "" nếu người dùng hủy, None nếu không mở được hộp thoại."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _TK_CODE, mode, title, label, pattern, initial],
            capture_output=True, timeout=900)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace").strip()


def resolve_path(path, mode, title, label, pattern, need_label, initial=""):
    """Trả về str đường dẫn hợp lệ, hoặc dict kết quả (hủy / cần nhập tay / lỗi)."""
    path = (path or "").strip().strip('"')
    if not path:
        picked = native_pick(mode, title, label, pattern, initial)
        if picked is None:
            return {"ok": False, "need_path": True, "label": need_label}
        if picked == "":
            return {"ok": False, "cancelled": True}
        path = picked
    if mode == "open" and not Path(path).expanduser().is_file():
        return {"ok": False, "error": f"Không thấy file: {path}"}
    return str(Path(path).expanduser())


# --------------------------------------------------------------------------
# Nối backend
# --------------------------------------------------------------------------
class Wiring:
    """Giữ NovelBackend, DesktopBridge (GuiDich.py) và Bridge (novel_backend.py)."""

    def __init__(self, extra_dir: Path | None):
        for d in (extra_dir, APP_DIR):
            if d and str(d) not in sys.path:
                sys.path.insert(0, str(d))
        try:
            self.nb = importlib.import_module("novel_backend")
        except ImportError as exc:
            raise SystemExit(
                f"Không nạp được novel_backend.py ({exc}).\n"
                f"Hãy đặt serve_ui.py cùng thư mục với novel_backend.py và GuiDich.py.")

        self.gd = None
        for name in ("GuiDich", "desktop_app"):
            try:
                self.gd = importlib.import_module(name)
                break
            except ImportError:
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"[cảnh báo] Nạp {name}.py lỗi: {exc}")
                break
        if self.gd is None:
            print("[cảnh báo] Không có GuiDich.py: các nút dịch/glossary kiểu cũ sẽ không chạy, "
                  "chỉ còn /api/* của novel_backend.")

        engine = getattr(self.gd, "ENGINE_DEFAULT", "") if self.gd else ""
        if not engine:
            # Không có GuiDich.py (hoặc không khai báo engine): dùng engine script đi kèm app.
            fallback = Path(__file__).resolve().parent / "dichTrungsangViet.py"
            if fallback.is_file():
                engine = str(fallback)
        self.backend = self.nb.NovelBackend(default_engine=engine)
        self.token = self.backend.token

        targets = [self._make_novel_bridge()]
        self.desktop = self._make_desktop_bridge() if self.gd else None
        if self.desktop is not None:
            targets.insert(0, self.desktop)
        self.targets = targets

    # -- bridge của novel_backend (call/token/pick_file/save_file) --
    def _make_novel_bridge(self):
        backend = self.backend

        class BrowserNovelBridge(self.nb.Bridge):
            def pick_file(self, kind: str = "txt", path: str = "") -> dict:
                pats = {"txt": ("Text files", "*.txt"), "py": ("Python files", "*.py"),
                        "json": ("JSON files", "*.json"), "tsv": ("TSV/Text", "*.tsv"),
                        "md": ("Markdown files", "*.md")}
                label, pat = pats.get(kind, pats["txt"])
                res = resolve_path(path, "open", "Chọn file", label, pat, f"file .{kind}")
                return res if isinstance(res, dict) else {"ok": True, "path": str(Path(res).resolve())}

            def save_file(self, suggested: str = "export.txt", path: str = "") -> dict:
                res = resolve_path(path, "save", "Lưu file", "Files", "*.*",
                                   "nơi lưu file", suggested)
                return res if isinstance(res, dict) else {"ok": True, "path": res}

        return BrowserNovelBridge(backend)

    # -- DesktopBridge của GuiDich.py, thay hộp thoại bằng bản chạy trong trình duyệt --
    def _make_desktop_bridge(self):
        gd, backend = self.gd, self.backend

        class BrowserDesktopBridge(gd.DesktopBridge):
            def __init__(self):
                super().__init__()
                self.backend = backend

            def _sync_project(self):
                src = str(self.settings.get("source", "")).strip()
                if not src or not Path(src).is_file():
                    return None
                eng = str(self.settings.get("engine", "")).strip()
                params = {"source": src}
                if eng and Path(eng).is_file():
                    params["engine"] = eng
                return self.backend.dispatch("project.open", params)

            def _choose(self, key, path, title, label, pattern, need_label):
                res = resolve_path(path, "open", title, label, pattern, need_label)
                if isinstance(res, dict):
                    return res
                p = Path(res).resolve()
                self.settings[key] = str(p)
                if key == "source" and not self.settings.get("glossary"):
                    self.settings["glossary"] = str(gd.glossary_path_for(p))
                self._save_settings()
                synced = self._sync_project() if key in ("source", "engine") else None
                out = {"ok": True, "state": self.get_state()}
                if synced is not None:
                    out["backend"] = {"ok": synced.get("ok", False),
                                      "message": (synced.get("error") or {}).get("message", "")}
                return out

            def choose_source(self, path: str = "") -> dict:
                return self._choose("source", path, "Chọn file truyện nguồn",
                                    "Text files", "*.txt", "file truyện .txt")

            def choose_engine(self, path: str = "") -> dict:
                return self._choose("engine", path, "Chọn engine",
                                    "Python files", "*.py", "engine .py")

            def choose_glossary(self, path: str = "") -> dict:
                return self._choose("glossary", path, "Chọn glossary",
                                    "Text files", "*.txt", "file glossary .txt")

            def set_path(self, key: str, value: str) -> dict:
                result = super().set_path(key, value)
                if result.get("ok") and key in ("source", "engine"):
                    self._sync_project()
                return result

        return BrowserDesktopBridge()

    def find_method(self, name: str):
        if not name or name.startswith("_"):
            return None
        for target in self.targets:
            fn = getattr(target, name, None)
            if callable(fn):
                return fn
        return None

    def shutdown(self) -> None:
        try:
            if self.gd is not None and self.desktop is not None and self.gd.RUNTIME.running:
                self.desktop.stop_translation()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.backend.shutdown()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# Mã JS chèn vào trang
# --------------------------------------------------------------------------
SHIM_JS = r"""
<script>
(function () {
  if (window.__nvShim) return;
  window.__nvShim = true;
  var TOKEN = %TOKEN%;
  window.NOVEL_TOKEN = TOKEN;
  var origFetch = window.fetch.bind(window);

  function isApi(u) {
    try {
      var url = new URL(u, location.href);
      return url.origin === location.origin && url.pathname.indexOf("/api/") === 0;
    } catch (e) { return false; }
  }
  window.fetch = function (input, init) {
    try {
      var u = typeof input === "string" ? input : (input && input.url) || "";
      if (isApi(u)) {
        init = init || {};
        var h = new Headers(init.headers || (typeof input !== "string" && input.headers) || {});
        if (!h.has("X-Novel-Token")) h.set("X-Novel-Token", TOKEN);
        init.headers = h;
      }
    } catch (e) {}
    return origFetch(input, init);
  };
  var OrigES = window.EventSource;
  if (OrigES) {
    var ES = function (u, o) {
      try {
        if (isApi(String(u))) {
          var url = new URL(String(u), location.href);
          if (!url.searchParams.has("token")) url.searchParams.set("token", TOKEN);
          u = url.toString();
        }
      } catch (e) {}
      return new OrigES(u, o);
    };
    ES.prototype = OrigES.prototype;
    ES.CONNECTING = 0; ES.OPEN = 1; ES.CLOSED = 2;
    window.EventSource = ES;
  }

  async function rpc(name, args) {
    var r = await origFetch("/rpc/" + encodeURIComponent(name), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Novel-Token": TOKEN },
      body: JSON.stringify({ args: args })
    });
    var data = await r.json();
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    return data;
  }
  async function call(name, args) {
    var res = await rpc(name, args);
    if (res && res.need_path) {
      var p = window.prompt("Không mở được hộp thoại chọn file. Dán đường dẫn đầy đủ tới " +
                            (res.label || "file") + ":");
      if (!p) return { ok: false, cancelled: true };
      var pos = (name === "pick_file" || name === "save_file") ? 1 : 0;
      var a = args.slice();
      while (a.length < pos) a.push("");
      a[pos] = p;
      res = await rpc(name, a);
    }
    return res;
  }
  window.pywebview = {
    api: new Proxy({}, {
      get: function (_, name) {
        if (typeof name !== "string" || name === "then") return undefined;
        return function () { return call(name, Array.prototype.slice.call(arguments)); };
      }
    })
  };
  window.addEventListener("load", function () {
    if (!window.__novelTranslatorInjected) window.dispatchEvent(new Event("pywebviewready"));
  });
})();
</script>
"""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def bindings_script(route: str) -> str:
    """Đọc nv_bindings.js và trả về thẻ <script> để nối trang với NovelBackend qua /api/*."""
    try:
        js = (APP_DIR / "nv_bindings.js").read_text(encoding="utf-8")
    except OSError:
        return ""
    preamble = (
        "<script>window.NV_ROUTE=" + json.dumps(route)
        + ";window.NV_ALIASES=" + json.dumps(ROUTE_ALIASES) + ";</script>\n"
    )
    return preamble + "<script>\n" + js + "\n</script>"


class Handler(BaseHTTPRequestHandler):
    root: Path = APP_DIR
    wiring: Wiring = None  # type: ignore
    server_version = "UIViewer/2.0"

    def log_message(self, fmt, *args):
        line = fmt % args
        if "/rpc/" in line or "/api/" in line:
            return  # tránh ngập log vì polling 2 giây/lần
        sys.stderr.write("  %s\n" % line)

    # ---- tiện ích ----
    def send_bytes(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, code, text):
        self.send_bytes(code, text.encode("utf-8"), "text/html; charset=utf-8")

    def send_json(self, code, payload):
        self.send_bytes(code, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                        "application/json; charset=utf-8")

    def host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def try_api(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self.wiring.nb.handle_api_request(self, self.wiring.backend)
            return True
        return False

    # ---- phương thức ----
    def do_GET(self):  # noqa: N802
        if self.try_api():
            return
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self.send_html(200, self.menu_page())
        if path.startswith("/p/"):
            return self.serve_page(path[3:].strip("/"))
        if path.startswith("/asset/"):
            return self.serve_asset(path[len("/asset/"):])
        self.send_html(404, self.error_page("Không tìm thấy", path))

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/rpc/"):
            return self.handle_rpc(unquote(path[len("/rpc/"):]))
        if self.try_api():
            return
        self.send_json(404, {"error": "Không có route."})

    def handle_rpc(self, name):
        if not self.host_ok():
            return self.send_json(403, {"error": "Host không hợp lệ."})
        token = self.headers.get("X-Novel-Token", "")
        if not token or not hmac.compare_digest(token, self.wiring.token):
            return self.send_json(401, {"error": "Thiếu hoặc sai token."})
        fn = self.wiring.find_method(name)
        if fn is None:
            return self.send_json(404, {"error": f"API chưa được đăng ký: {name}"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 20 * 1024 * 1024:
                return self.send_json(413, {"error": "Nội dung quá lớn."})
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            args = body.get("args", []) if isinstance(body, dict) else []
            if not isinstance(args, list):
                raise ValueError("args phải là mảng")
        except (ValueError, UnicodeDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        try:
            self.send_json(200, fn(*args))
        except TypeError as exc:
            self.send_json(400, {"error": f"Tham số không hợp lệ: {exc}"})
        except Exception as exc:  # noqa: BLE001
            self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})

    # ---- trang và tài nguyên ----
    def resolve(self, value):
        if value in ROUTES:
            return value
        compact = re.sub(r"[^a-z0-9]", "", html.unescape(value).lower())
        return ROUTE_ALIASES.get(compact)

    def serve_page(self, raw):
        route = self.resolve(raw)
        if not route:
            return self.send_html(404, self.error_page("Route không hợp lệ", raw))
        letter = next(l for l, r, _ in PAGES if r == route)
        folder = find_folder(self.root, letter)
        if folder is None:
            return self.send_html(404, self.error_page(
                "Không thấy thư mục giao diện",
                f"Cần thư mục bắt đầu bằng giao_di_n_{letter}_ trong:\n{self.root}"))
        page_file = find_html(folder)
        if page_file is None:
            return self.send_html(404, self.error_page("Thư mục chưa có file HTML", str(folder)))
        page = read_text(page_file)
        if not page.strip():
            return self.send_html(200, self.error_page("File HTML đang rỗng (0 byte)", str(page_file)))
        if route == "xung-ho":
            page = re.sub(r"<!-- LOGIC JAVASCRIPT THUẦN -->\s*<script>.*?</script>",
                          "", page, count=1, flags=re.S)

        shim = SHIM_JS.replace("%TOKEN%", json.dumps(self.wiring.token))
        head_part = f'<base href="/asset/{route}/">' + shim
        if re.search(r"<head[^>]*>", page, flags=re.I):
            page = re.sub(r"<head([^>]*)>", lambda m: m.group(0) + head_part, page, count=1, flags=re.I)
        else:
            page = head_part + page

        tail = bindings_script(route)
        m = re.search(r"</body\s*>", page, flags=re.I)
        page = page[:m.start()] + tail + page[m.start():] if m else page + tail
        self.send_html(200, page)

    def serve_asset(self, rest):
        parts = rest.split("/", 1)
        route = self.resolve(parts[0]) if len(parts) == 2 else None
        if not route:
            return self.send_bytes(404, b"Not found", "text/plain; charset=utf-8")
        letter = next(l for l, r, _ in PAGES if r == route)
        folder = find_folder(self.root, letter)
        if folder is None:
            return self.send_bytes(404, b"Not found", "text/plain; charset=utf-8")
        base_dir = folder.resolve()
        target = (base_dir / parts[1]).resolve()
        if target != base_dir and base_dir not in target.parents:
            return self.send_bytes(403, b"Forbidden", "text/plain; charset=utf-8")
        if not target.is_file():
            return self.send_bytes(404, b"Not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_bytes(200, target.read_bytes(), ctype)

    def menu_page(self):
        rows = []
        for letter, route, title in PAGES:
            folder = find_folder(self.root, letter)
            page = find_html(folder) if folder else None
            if page and page.stat().st_size > 0:
                status, cls, link = "sẵn sàng", "ok", f'<a href="/p/{route}">{html.escape(title)}</a>'
            else:
                status = "file rỗng" if page else ("chưa có HTML" if folder else "thiếu thư mục")
                cls, link = "bad", f"<span>{html.escape(title)}</span>"
            rows.append(f'<li class="{cls}">{link}<small>{letter.upper()} · /p/{route}</small><em>{status}</em></li>')
        w = self.wiring
        backend_line = "NovelBackend: đã nối (/api/*)"
        backend_line += " · GuiDich.py: đã nối (các nút)" if w.gd else " · GuiDich.py: không tìm thấy"
        return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<title>Giao diện Novel Translator</title>
<style>
body{{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 16px;color:#222}}
h1{{font-size:22px;font-weight:500}} ul{{list-style:none;padding:0}}
li{{display:flex;gap:12px;align-items:baseline;padding:10px 0;border-top:1px solid #ddd}}
li a{{font-weight:500;color:#185FA5;text-decoration:none}} li small{{color:#777;flex:1}}
li em{{font-style:normal;font-size:13px}} li.ok em{{color:#3B6D11}} li.bad em{{color:#A32D2D}}
code{{background:#f2f2f2;padding:2px 6px;border-radius:4px;font-size:13px}}
</style></head><body>
<h1>Giao diện Novel Translator</h1>
<p>Thư mục gốc: <code>{html.escape(str(self.root))}</code><br><small>{html.escape(backend_line)}</small></p>
<ul>{''.join(rows)}</ul></body></html>"""

    @staticmethod
    def error_page(title, detail):
        return (f"<!doctype html><meta charset='utf-8'><body style='font-family:system-ui;padding:32px'>"
                f"<h2>{html.escape(title)}</h2><pre style='white-space:pre-wrap'>{html.escape(detail)}</pre>"
                f"<p><a href='/'>Về menu</a></p></body>")


def free_port(preferred):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def main():
    # Console Windows có thể là cp1252/cp936 (nhất là khi chuyển hướng ra file): chữ Việt trong log
    # sẽ làm UnicodeEncodeError và app không khởi động được -> ép UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Xem 7 giao diện HTML với backend thật")
    ap.add_argument("--root", help="Thư mục chứa 7 thư mục giao_di_n_*")
    ap.add_argument("--code-dir", help="Thư mục chứa GuiDich.py và novel_backend.py (mặc định: cạnh file này)")
    ap.add_argument("--source", help="File truyện .txt để mở sẵn dự án")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    wiring = Wiring(Path(args.code_dir).resolve() if args.code_dir else None)
    root = pick_root(args.root)
    Handler.root, Handler.wiring = root, wiring

    if args.source:
        result = wiring.backend.dispatch("project.open", {"source": args.source})
        if not result.get("ok"):
            print("Không mở được dự án:", (result.get("error") or {}).get("message"))
        elif wiring.desktop is not None:
            wiring.desktop.settings["source"] = str(Path(args.source).resolve())
            wiring.desktop._save_settings()

    print(f"Thư mục giao diện: {root}")
    for letter, route, _ in PAGES:
        folder = find_folder(root, letter)
        page = find_html(folder) if folder else None
        state = "OK" if page and page.stat().st_size else ("RỖNG" if page else "THIẾU")
        print(f"  [{state:>5}] {letter.upper()}  {route:<16} {folder.name if folder else '-'}")

    port = free_port(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"\nĐang chạy tại {url}  (Ctrl+C để dừng)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang dừng...")
    finally:
        wiring.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()