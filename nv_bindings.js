/* nv_bindings.js - nối các trang HTML (Stitch) với NovelBackend qua /api/*.
 * Được serve_ui.py chèn vào từng trang. Cần window.NV_ROUTE và window.NV_ALIASES.
 * Trang đã nối: A tong-quan, B bao-cao, C nhan-vat, D moegirl, E glossary, F dich-tien-trinh, G nhat-ky.
 * Tất cả 7 trang đã nối xong.
 */
(function () {
  "use strict";
  var ROUTE = window.NV_ROUTE || "";
  var ALIASES = window.NV_ALIASES || {};

  // ------------------------------------------------------------------ tiện ích
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var T = function (s) { return String(s == null ? "" : s).replace(/\s+/g, " ").trim(); };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function ownText(el) {
    var s = "";
    for (var n = el.firstChild; n; n = n.nextSibling) if (n.nodeType === 3) s += n.nodeValue;
    return T(s);
  }
  function setOwn(el, text) {
    if (!el) return;
    var last = null;
    for (var n = el.firstChild; n; n = n.nextSibling) if (n.nodeType === 3 && T(n.nodeValue)) last = n;
    if (last) last.nodeValue = " " + text + " "; else el.appendChild(document.createTextNode(text));
  }
  function setText(el, text) { if (el) el.textContent = text; }
  /** Phần tử lá (không có phần tử con) có văn bản khớp regex. */
  function leaf(re, root) {
    var els = $$("span,div,p,td,a,label,h1,h2,h3,strong", root);
    for (var i = 0; i < els.length; i++) if (!els[i].children.length && re.test(T(els[i].textContent))) return els[i];
    return null;
  }
  /** Phần tử có văn bản trực tiếp khớp regex (kể cả khi còn phần tử con). */
  function withOwn(re, root) {
    var els = $$("span,div,p,td,label", root);
    for (var i = 0; i < els.length; i++) if (re.test(ownText(els[i]))) return els[i];
    return null;
  }
  function btn(re, root) {
    var bs = $$("button", root);
    for (var i = 0; i < bs.length; i++) if (re.test(T(bs[i].textContent))) return bs[i];
    return null;
  }
  /** Gỡ mọi listener cũ của mẫu rồi gắn handler thật. */
  function rebind(el, fn) {
    if (!el) return null;
    var c = el.cloneNode(true);
    c.removeAttribute("onclick");
    el.replaceWith(c);
    c.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (c.dataset.busy === "1") return;
      c.dataset.busy = "1";
      Promise.resolve().then(fn).catch(function (e) { NV.toast(e.message || String(e), "error"); })
        .then(function () { delete c.dataset.busy; });
    });
    return c;
  }
  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n >= 1073741824) return (n / 1073741824).toFixed(1) + " GB";
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
    return n + " B";
  }
  function fmtDur(s) {
    if (s == null) return "--:--:--";
    s = Math.max(0, Math.round(s));
    var p = function (x) { return String(x).padStart(2, "0"); };
    return p(Math.floor(s / 3600)) + ":" + p(Math.floor(s % 3600 / 60)) + ":" + p(s % 60);
  }
  function base(p) { return String(p || "").split(/[\\/]/).pop(); }
  function debounce(fn, ms) { var t; return function () { var a = arguments; clearTimeout(t); t = setTimeout(function () { fn.apply(null, a); }, ms); }; }
  function download(name, text, type) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: type || "text/plain;charset=utf-8" }));
    a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  // ------------------------------------------------------------------ lõi NV
  var NV = window.NV = {
    api: function (method, params) {
      return fetch("/api/" + method, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(params || {})
      }).then(function (r) { return r.json(); });
    },
    call: function (method, params) {
      return NV.api(method, params).then(function (d) {
        if (!d.ok) {
          var e = new Error((d.error && d.error.message) || "Lỗi không rõ");
          e.code = d.error && d.error.code; e.data = d.error; throw e;
        }
        return d;
      });
    },
    toast: function (msg, kind) {
      var box = $("#nv-toast-box");
      if (!box) {
        box = document.createElement("div");
        box.id = "nv-toast-box";
        box.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:99999;display:flex;flex-direction:column;gap:8px;max-width:420px";
        document.body.appendChild(box);
      }
      var el = document.createElement("div");
      var bg = kind === "error" ? "#7f1d1d" : kind === "ok" ? "#14532d" : "#1f2937";
      el.style.cssText = "padding:10px 14px;border-radius:6px;color:#fff;font:13px/1.4 system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.4);background:" + bg;
      el.textContent = msg; box.appendChild(el);
      setTimeout(function () { el.remove(); }, kind === "error" ? 7000 : 3500);
    },
    pickFile: function (kind) {
      return window.pywebview.api.pick_file(kind || "txt");
    },
    go: function (route) { location.href = "/p/" + route; },
    sse: function (since, handlers) {
      var es = new EventSource("/api/events?since=" + (since || 0));
      Object.keys(handlers).forEach(function (k) {
        es.addEventListener(k, function (ev) { try { handlers[k](JSON.parse(ev.data)); } catch (e) { /* bỏ qua */ } });
      });
      window.addEventListener("beforeunload", function () { es.close(); });
      return es;
    }
  };

  function openSource() {
    return NV.pickFile("txt").then(function (r) {
      if (!r || !r.ok) { if (r && !r.cancelled) NV.toast("Không chọn được file.", "error"); return null; }
      return NV.call("project.open", { source: r.path }).then(function () { location.reload(); });
    });
  }

  function installNav() {
    if (ROUTE === "xung-ho") {
      var links = $$(".sidebar-nav .nav-item");
      ["tong-quan", "bao-cao", "nhan-vat", "moegirl", "glossary", "dich-tien-trinh", "nhat-ky", "xung-ho"].forEach(function (route, i) {
        if (links[i]) { links[i].classList.remove("disabled"); links[i].href = "/p/" + route; links[i].removeAttribute("title"); }
      });
    } else {
      var nav = $("nav[data-active-classes]");
      if (nav && !$('a[data-path="xung-ho"]', nav)) {
        var link = document.createElement("a");
        link.className = "flex items-center gap-space-md px-space-md py-space-sm rounded text-on-surface-variant hover:bg-surface-container-high";
        link.href = "/p/xung-ho"; link.setAttribute("data-path", "xung-ho");
        link.textContent = "H. Quản lý xưng hô"; nav.appendChild(link);
      }
    }
    document.addEventListener("click", function (ev) {
      var a = ev.target.closest && ev.target.closest("a[data-path]");
      if (!a) return;
      var v = a.getAttribute("data-path") || "";
      var r = ALIASES[v.toLowerCase().replace(/[^a-z0-9]/g, "")];
      if (r) { ev.preventDefault(); NV.go(r); }
    }, true);
  }

  function noProjectBanner(state) {
    var bar = document.createElement("div");
    bar.style.cssText = "position:sticky;top:0;z-index:9999;background:#78350f;color:#fff;padding:8px 16px;font:14px system-ui,sans-serif;display:flex;gap:12px;align-items:center;flex-wrap:wrap";
    var span = document.createElement("span");
    span.textContent = "Chưa mở dự án. Chọn file truyện (.txt) để bắt đầu.";
    bar.appendChild(span);
    var b = document.createElement("button");
    b.textContent = "Chọn file truyện";
    b.style.cssText = "background:#fff;color:#78350f;border:0;border-radius:4px;padding:4px 10px;cursor:pointer";
    b.onclick = function () { openSource().catch(function (e) { NV.toast(e.message, "error"); }); };
    bar.appendChild(b);
    (state.recent || []).slice(0, 3).forEach(function (p) {
      var r = document.createElement("button");
      r.textContent = base(p); r.title = p;
      r.style.cssText = "background:transparent;color:#fff;border:1px solid #fff;border-radius:4px;padding:4px 10px;cursor:pointer";
      r.onclick = function () {
        NV.call("project.open", { source: p }).then(function () { location.reload(); })
          .catch(function (e) { NV.toast(e.message, "error"); });
      };
      bar.appendChild(r);
    });
    document.body.insertBefore(bar, document.body.firstChild);
  }

  // ------------------------------------------------------------------ A. tong-quan
  function pageA(state) {
    var inputs = $$("input[type=text]");
    var inSource = $("#source-file-input"), inDir = $("#output-dir-input"), inOut = $("#output-file-name");
    var inEngine = inputs[3], inCache = inputs[4], inGloss = inputs[5];
    var info = null, cfg = null;

    function fill() {
      return NV.call("project.info").then(function (i) {
        info = i; cfg = i.config;
        inSource.value = i.source.path;
        inDir.value = i.output_dir;
        inOut.value = base(i.output);
        setText(leaf(/^Dung lượng:/), "Dung lượng: " + fmtBytes(i.source.size_bytes));
        setText(leaf(/^~?\d+ chương$/), i.source.chapters + " chương");
        setText(leaf(/Detected$/), (i.source.encoding || "").toUpperCase() + " Detected");
        setText(leaf(/^Đường dẫn đầy đủ:/), "Đường dẫn đầy đủ: " + i.output);
        if (inEngine) inEngine.value = cfg.engine || (cfg.engine === "" ? "" : "");
        if (inGloss) inGloss.value = i.files.glossary.path;
        var gl = leaf(/mục áp dụng$/);
        if (gl) gl.textContent = i.files.glossary.exists ? fmtBytes(i.files.glossary.bytes) : "Chưa có file";
        var crumb = leaf(/^D:\\/); if (crumb) crumb.textContent = i.output_dir;
        setText(leaf(/^JSON Schema:/), "JSON Schema: v" + cfg.schema_version);
        setText(leaf(/Đã tải cấu hình/), i.config_loaded ? "Đã tải cấu hình từ " + base(i.files.config.path)
                                                       : "Đang dùng cấu hình mặc định");
        setText(leaf(/^http:\/\/127/), cfg.ollama.base_url);
        var m = leaf(/^qwen|:\d+b/i); if (m && cfg.ollama.model) m.textContent = cfg.ollama.model;
        setText(leaf(/tks$/), cfg.translate.max_chars + " ký tự");
        var tks = $$("div").filter(function (d) { return !d.children.length && /^\d+ lần$/.test(T(d.textContent)); })[0];
        if (tks) tks.textContent = cfg.translate.max_retries + " lần";
        var est = i.estimate;
        var e = leaf(/^Ước tính:/);
        if (e) e.textContent = est && est.available
          ? "Ước tính: ~" + Math.round(est.remaining_seconds / 60) + " phút / " + i.source.chapters + " chương"
          : "Ước tính: chưa tính được (kiểm tra engine)";
        NV.call("config.export").then(function (c) { setText(leaf(/^MD5:/), "MD5: " + c.md5.slice(0, 8) + "..."); }).catch(function () {});
        return NV.call("moegirl.cache_info").then(function (c) {
          if (inCache) inCache.value = c.path || "";
          var sp = leaf(/\(\d{2}\/\d{2}\/\d{4}\)$/) || leaf(/^\d+MB/);
          if (sp && c.exists) sp.textContent = fmtBytes(c.size_bytes);
        }).catch(function () {});
      });
    }
    function loadOllama() {
      return NV.call("ollama.status").then(function (o) {
        var c = leaf(/^(Connected|Disconnected|Ngắt kết nối|Đã kết nối)$/);
        if (c) c.textContent = o.connected ? "Connected" : "Không kết nối";
        var r = leaf(/^(Ready in VRAM|Ready|Model chưa tải|Không có model)$/);
        if (r) r.textContent = !o.connected ? "—" : o.model_loaded ? "Ready in VRAM" : o.model_present ? "Model có sẵn" : "Thiếu model";
        if (o.connected && !(cfg && cfg.ollama.model) && o.models[0]) {
          var m = leaf(/^qwen|:\d+b/i); if (m) m.textContent = o.models[0].name;
        }
      }).catch(function () {});
    }
    function loadStats() {
      NV.call("system.stats").then(function (s) {
        var g = s.gpu, name = leaf(/^NVIDIA|^AMD|GPU$/), pct = leaf(/^\d+%$/);
        if (name) name.textContent = g ? g.name : "Không phát hiện GPU";
        if (pct) pct.textContent = g ? Math.round(g.utilization_percent) + "%" : "—";
      }).catch(function () {});
    }

    function patchFromForm() {
      return { output_name: inOut.value.trim() || "bandich.txt", engine: inEngine ? inEngine.value.trim() : "" };
    }
    function save() {
      return NV.call("config.save", { patch: patchFromForm(), persist: true }).then(function (r) {
        NV.toast("Đã lưu cấu hình: " + (r.saved && r.saved.path || ""), "ok"); return fill();
      });
    }
    var saveBtns = [btn(/Lưu cấu hình/), btn(/Lưu nhanh/)];
    saveBtns.forEach(function (b) { rebind(b, save); });
    document.addEventListener("keydown", function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") { ev.preventDefault(); save().catch(function (e) { NV.toast(e.message, "error"); }); }
    });
    rebind(btn(/Mặc định/), function () {
      if (!confirm("Đặt lại toàn bộ cấu hình về mặc định?")) return;
      return NV.call("config.reset").then(function () { return NV.call("config.save", { patch: {}, persist: true }); })
        .then(fill).then(function () { NV.toast("Đã đặt lại cấu hình mặc định.", "ok"); });
    });
    rebind(btn(/Xuất JSON/), function () {
      return NV.call("config.export").then(function (c) { download("config.json", c.text, "application/json"); });
    });
    rebind(btn(/Chọn file truyện/), openSource);
    rebind(btn(/Mở thư mục đầu ra/), function () { return NV.call("system.open", { kind: "output_dir" }); });
    rebind(btn(/Chọn script/), function () {
      // Dùng chung engine.set_mode với màn hình E để engine được ghi vào config.json của dự án.
      return NV.pickFile("py").then(function (r) {
        if (!r || !r.ok) return;
        return NV.call("engine.set_mode", { mode: "script", engine: r.path }).then(function (res) {
          if (inEngine) inEngine.value = res.engine || r.path;
          NV.toast("Đã chọn script: " + base(res.engine || r.path), "ok");
        });
      });
    });
    rebind(btn(/Chọn glossary/), function () {
      return NV.pickFile("txt").then(function (r) {
        if (!r || !r.ok) return;
        return NV.call("config.save", { patch: { glossary: r.path }, persist: true }).then(function () {
          NV.toast("Đã đặt glossary: " + r.path + " (áp dụng khi mở lại dự án)", "ok");
          inGloss.value = r.path;
        });
      });
    });
    rebind(btn(/Chọn cache/), function () { NV.toast("Cache Moegirl được dựng ở màn hình D (Gợi ý Moegirl).", "info"); });
    rebind(btn(/^\s*refresh\s*$/) || $$("button").filter(function (b) { return /^refresh$/.test(T(b.textContent)); })[0], loadOllama);
    rebind(btn(/Tạo gợi ý Moegirl/), function () {
      if (!state.gated.moegirl_suggestions) { NV.toast("Cần duyệt ít nhất một tác phẩm ở màn B trước.", "error"); return; }
      NV.go("moegirl");
    });
    rebind(btn(/Phân tích tác phẩm/), function () {
      return NV.call("works.scan").then(function () { NV.go("bao-cao"); });
    });
    // Nút "Bắt đầu dịch" đã chuyển sang màn hình E (glossary): ở đó chọn engine DeepSeek hoặc dichTrungsangViet.py.
    rebind(btn(/màn hình E/), function () { NV.go("glossary"); });
    fill().then(loadOllama).then(loadStats).catch(function (e) { NV.toast(e.message, "error"); });
    setInterval(loadStats, 5000);
  }

  // ------------------------------------------------------------------ B. bao-cao
  function pageB() {
    var S = { status: "", query: "", sort: "score", selected: new Set(), current: null, items: [] };
    var tbody = $("#works-tbody"), counter = $("#selection-counter");
    var selAll = $("#select-all");
    var SORT = { score_desc: "score", matched_desc: "matched", chapters_desc: "chapters", occurrences_desc: "occurrences" };
    var STATUS_KEY = { "Chờ duyệt": "pending", "Đã duyệt": "approved", "Từ chối": "rejected" };
    var BADGE = {
      approved: ["bg-secondary-container/20 text-secondary", "bg-secondary", "Đã duyệt"],
      rejected: ["bg-error-container/20 text-error", "bg-error", "Từ chối"],
      pending: ["bg-tertiary-container/20 text-tertiary", "bg-tertiary", "Chờ duyệt"]
    };

    function updateCounter() {
      counter.innerHTML = "Đã chọn: <span class=\"font-code-sm font-semibold\">" + S.selected.size + "</span> tác phẩm";
    }
    function renderRows() {
      if (!S.items.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="py-6 text-center text-outline">Không có tác phẩm nào khớp. Kiểm tra bảng nhân vật mốc hoặc bấm quét lại.</td></tr>';
        return;
      }
      tbody.innerHTML = S.items.map(function (w) {
        var b = BADGE[w.status] || BADGE.pending;
        var active = S.current === w.work_id;
        var chips = (w.preview || []).map(function (a) {
          return '<span class="px-space-xs py-0.5 rounded bg-surface-container text-on-surface font-code-sm text-code-sm">' + esc(a.name) +
            ' <span class="text-secondary">(' + a.count + ')</span></span>';
        }).join("");
        return '<tr class="work-row ' + (active ? "bg-surface-container-high/60 " : "") + 'hover:bg-surface-container-high cursor-pointer transition-colors group' +
          (w.status === "rejected" ? " opacity-75" : "") + '" data-id="' + esc(w.work_id) + '">' +
          '<td class="px-space-sm text-center"><input class="row-checkbox rounded bg-surface-container-highest cursor-pointer accent-primary" type="checkbox"' + (S.selected.has(w.work_id) ? " checked" : "") + '/></td>' +
          '<td class="py-space-sm px-space-md"><div class="flex items-center gap-space-sm"><span class="w-1.5 h-4 ' + (active ? "bg-primary" : "bg-transparent") + ' rounded-full"></span>' +
          '<div class="flex flex-col"><span class="font-headline-sm text-headline-sm text-on-surface group-hover:text-primary transition-colors">' + esc(w.work_title) + '</span>' +
          '<span class="font-code-sm text-code-sm text-outline">' + esc(w.note || "") + '</span></div></div></td>' +
          '<td class="py-space-sm px-space-md font-code-sm text-code-sm text-on-surface-variant">' + esc(w.work_id) + '</td>' +
          '<td class="py-space-sm px-space-md"><div class="flex flex-wrap gap-space-xs">' + chips + '</div></td>' +
          '<td class="py-space-sm px-space-md text-right font-code-sm font-medium text-on-surface">' + w.matched_anchors + '</td>' +
          '<td class="py-space-sm px-space-md text-right font-code-sm text-on-surface">' + w.total_occurrences + '</td>' +
          '<td class="py-space-sm px-space-md text-right font-code-sm text-primary">' + w.chapters_with_evidence + ' chương</td>' +
          '<td class="py-space-sm px-space-md text-right"><div class="inline-flex items-center gap-space-xs"><span class="font-code-sm font-semibold text-secondary">' + w.score + '</span>' +
          '<div class="w-10 h-1.5 bg-surface-container-highest rounded-full overflow-hidden"><div class="h-full bg-secondary rounded-full" style="width:' + Math.min(100, w.score) + '%"></div></div></div></td>' +
          '<td class="py-space-sm px-space-md text-center"><span class="status-badge inline-flex items-center gap-1 px-space-xs py-0.5 rounded ' + b[0] + ' font-label-sm text-label-sm"><span class="w-1.5 h-1.5 rounded-full ' + b[1] + '"></span>' + b[2] + '</span></td>' +
          '<td class="py-space-sm px-space-md text-center"><div class="flex items-center justify-center gap-1">' +
          '<button type="button" data-act="approved" title="Duyệt" class="p-1 rounded hover:bg-surface-container-highest"><span class="material-symbols-outlined text-[18px] text-secondary">check</span></button>' +
          '<button type="button" data-act="rejected" title="Từ chối" class="p-1 rounded hover:bg-surface-container-highest"><span class="material-symbols-outlined text-[18px] text-error">close</span></button>' +
          '</div></td></tr>';
      }).join("");
    }
    function renderCounts(c) {
      $$("#filter-status option").forEach(function (o) {
        var k = o.value === "ALL" ? "all" : STATUS_KEY[o.value];
        var label = o.value === "ALL" ? "Tất cả" : o.value;
        if (k && c && c[k] != null) o.textContent = label + " (" + c[k] + ")";
      });
    }
    function renderDetail(d) {
      setText($("#detail-work-title"), d.work_title);
      setText($("#detail-work-id"), d.work_id);
      var grid = $("#detail-work-title").closest(".bg-surface-container-low").querySelector(".grid");
      if (!grid) return;
      grid.innerHTML = d.anchors.map(function (a) {
        var sn = (a.snippets && a.snippets[0]) || null;
        var quote = sn ? esc(sn.text).split(esc(a.name)).join('<mark class="bg-primary/25 text-primary-fixed px-0.5 rounded">' + esc(a.name) + "</mark>") : "Không có đoạn trích.";
        var chs = (a.chapters || []).slice(0, 6).join(", ") + ((a.chapters || []).length > 6 ? "..." : "");
        return '<div class="bg-surface-container rounded p-space-md flex flex-col gap-space-sm shadow-sm">' +
          '<div class="flex items-center justify-between"><div class="flex items-center gap-space-xs"><span class="w-2 h-2 rounded-full bg-secondary"></span>' +
          '<span class="font-headline-sm text-headline-sm text-on-surface font-semibold">' + esc(a.name) + '</span></div>' +
          '<span class="font-code-sm text-code-sm px-1.5 py-0.5 rounded bg-secondary-container/20 text-secondary font-semibold">Trọng số: ' + a.weight + '</span></div>' +
          '<div class="flex items-center justify-between font-label-sm text-label-sm text-outline"><span>Tần suất: <strong class="text-on-surface font-code-sm">' + a.count + ' lần</strong></span>' +
          '<span>Xuất hiện: <span class="font-code-sm text-primary">Chương ' + esc(chs) + '</span></span></div>' +
          '<div class="bg-surface-container-lowest rounded p-space-sm flex flex-col gap-space-xs mt-space-xs">' +
          '<span class="font-label-sm text-label-sm text-outline uppercase tracking-wider">Đoạn trích' + (sn ? " (Chương " + sn.chapter + ")" : "") + '</span>' +
          '<p class="font-body-sm text-body-sm text-on-surface-variant italic">"...' + quote + '..."</p></div>' +
          '<div class="flex items-center justify-between pt-space-xs text-code-sm font-code-sm text-outline"><span>Loại: ' + esc(a.kind) + '</span></div></div>';
      }).join("") || '<div class="text-outline">Tác phẩm này chưa có nhân vật mốc khớp.</div>';
      S.detail = d;
    }
    function selectRow(id) {
      S.current = id; renderRows();
      return NV.call("works.detail", { work_id: id }).then(renderDetail).catch(function (e) { NV.toast(e.message, "error"); });
    }
    function load() {
      return NV.call("works.list", { status: S.status, query: S.query, sort: S.sort, page_size: 500 }).then(function (r) {
        S.items = r.items; renderCounts(r.status_counts); renderRows(); updateCounter();
        if (!S.current && r.items[0]) selectRow(r.items[0].work_id);
      }).catch(function (e) {
        if (e.code === "NOT_SCANNED") return scan();
        throw e;
      });
    }
    function scan() {
      return NV.call("works.scan").then(function (r) {
        NV.toast("Đã quét " + r.patterns + " mẫu trong " + r.elapsed_ms + " ms, " + r.works + " tác phẩm.", "ok");
        S.current = null; return load();
      });
    }
    function setStatus(ids, status) {
      if (!ids.length) { NV.toast("Chưa chọn tác phẩm nào.", "error"); return; }
      return NV.call("works.set_status", { work_ids: ids, status: status }).then(function (r) {
        NV.toast("Đã cập nhật " + r.updated + " tác phẩm (" + r.dirty.total + " thay đổi chưa lưu).", "ok");
        return load();
      });
    }

    tbody.addEventListener("click", function (ev) {
      var tr = ev.target.closest("tr.work-row"); if (!tr) return;
      var id = tr.dataset.id;
      var act = ev.target.closest("button[data-act]");
      if (act) { setStatus([id], act.dataset.act); return; }
      var cb = ev.target.closest(".row-checkbox");
      if (cb) { if (cb.checked) S.selected.add(id); else S.selected.delete(id); updateCounter(); return; }
      selectRow(id);
    });
    if (selAll) {
      var sa = selAll.cloneNode(true); selAll.replaceWith(sa);
      sa.addEventListener("change", function () {
        S.items.forEach(function (w) { if (sa.checked) S.selected.add(w.work_id); else S.selected.delete(w.work_id); });
        renderRows(); updateCounter();
      });
    }
    var search = $("#search-input"); search.addEventListener("input", debounce(function () { S.query = search.value.trim(); load(); }, 250));
    document.addEventListener("keydown", function (ev) { if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") { ev.preventDefault(); search.focus(); } });
    var fs = $("#filter-status"); fs.addEventListener("change", function () { S.status = STATUS_KEY[fs.value] || ""; load(); });
    var ss = $("#sort-select"); ss.addEventListener("change", function () { S.sort = SORT[ss.value] || "score"; load(); });

    rebind($("#btn-rescan"), scan);
    var sel = function () { return Array.from(S.selected); };
    rebind(btn(/Duyệt tác phẩm/), function () { return setStatus(sel(), "approved"); });
    rebind(btn(/^\s*(block\s*)?Từ chối/), function () { return setStatus(sel(), "rejected"); });
    rebind(btn(/Đặt lại về Chờ/), function () { return setStatus(sel(), "pending"); });
    rebind(btn(/Lưu file duyệt/), function () {
      return NV.call("works.save").then(function (r) { NV.toast("Đã lưu file duyệt" + (r.path ? ": " + r.path : "."), "ok"); });
    });
    rebind(btn(/Mở file gốc/), function () { return NV.call("system.open", { kind: "anchors" }); });
    rebind(btn(/Xuất JSON đối chiếu/), function () {
      if (!S.detail) { NV.toast("Chưa chọn tác phẩm.", "error"); return; }
      download("doi_chieu_" + S.detail.work_id + ".json", JSON.stringify(S.detail, null, 2), "application/json");
    });
    load().catch(function (e) { NV.toast(e.message, "error"); });
  }

  // ------------------------------------------------------------------ F. dich-tien-trinh
  function pageF(state) {
    var LABEL = { idle: "Chưa chạy", starting: "Đang khởi động", running: "Đang thực thi", pausing: "Đang tạm dừng…",
      paused: "Đã tạm dừng", stopping: "Đang dừng…", finished: "Đã hoàn tất", failed: "Lỗi", stopped: "Đã dừng" };
    var ACTIVE = { starting: 1, running: 1, pausing: 1 };
    var term = $("#log-terminal"), autoScroll = true, lastState = "idle", lastId = 0;
    var currentChunk = null; // chunk đang hiển thị trong panel kiểm tra (dùng cho Edit bản dịch)

    var pauseBtn = $("#btn-pause"), pauseIcon, pauseText;
    function paintPause(st) {
      pauseIcon = $("#pause-icon"); pauseText = $("#pause-text");
      if (!pauseIcon || !pauseText) return;
      var paused = st === "paused" || st === "pausing";
      pauseIcon.textContent = paused ? "play_arrow" : "pause_circle";
      pauseText.textContent = paused ? "Tiếp tục (Resume)" : "Tạm dừng (Pause)";
    }
    function addLog(e) {
      var cls = { ERROR: "text-error", WARNING: "text-tertiary", SUCCESS: "text-secondary", INFO: "text-primary" }[e.level] || "text-primary";
      var row = document.createElement("div");
      row.className = "flex items-start gap-space-sm";
      row.innerHTML = '<span class="text-outline flex-shrink-0">[' + esc(e.time) + ']</span><span class="' + cls + ' font-medium flex-shrink-0">[' + esc(e.level) + ']</span><span class="text-on-surface">' + esc(e.text) + "</span>";
      term.appendChild(row);
      while (term.children.length > 300) term.removeChild(term.firstChild);
      if (autoScroll) term.scrollTop = term.scrollHeight;
      lastId = Math.max(lastId, e.id || 0);
    }
    var asBtn = $("#btn-autoscroll"); if (asBtn) asBtn.addEventListener("click", function () { autoScroll = !autoScroll; });

    function renderStatus(s) {
      lastState = s.state;
      setText(leaf(/^Engine:/), "Engine: " + (LABEL[s.state] || s.state));
      setText(leaf(/PID #/), "PID #" + (s.pid == null ? "—" : s.pid));
      paintPause(s.state);
      var pct = (s.percent || 0).toFixed(1);
      var top = leaf(/^\d+(\.\d+)?%$/); if (top) top.textContent = pct + "%";
      setText(leaf(/^TIẾN ĐỘ TỔNG THỂ/), "TIẾN ĐỘ TỔNG THỂ: " + (s.percent || 0).toFixed(2) + "%");
      setText(leaf(/CHUNKS \(CÒN LẠI/), s.done_chunks + " / " + s.total_chunks + " CHUNKS (CÒN LẠI " + s.remaining_chunks + " CHUNKS)");
      setText(leaf(/\d+ \/ \d+ chunk$/i), s.done_chunks + " / " + s.total_chunks + " chunk");
      // Cập nhật CẢ 2 thanh tiến trình: mini-meter (Card "Tiến độ tổng thể") + thanh gradient chính.
      var pctW = Math.max(0, Math.min(100, s.percent || 0));
      $$('div[class*="bg-gradient-to-r"][style*="width"], div.bg-secondary[style*="width"]').forEach(function (b) { b.style.width = pctW + "%"; });
      var ch = withOwn(/^Chương \d+/); if (ch && s.chapters_done != null) setOwn(ch, "Chương " + s.chapters_done);
      setText(leaf(/^\(~[\d.]+ (B|KB|MB|GB)\)$/), "(~" + fmtBytes(s.output_bytes) + ")");
      var sp = leaf(/tok\/s$/); if (sp) sp.textContent = s.chars_per_second != null ? s.chars_per_second + " ký tự/s" : "—";
      var el = leaf(/^Đã chạy:/); if (el && el.nextElementSibling) el.nextElementSibling.textContent = fmtDur(s.elapsed_seconds);
      var et = leaf(/^Ước tính còn:/); if (et && et.nextElementSibling) et.nextElementSibling.textContent = s.eta_seconds != null ? "~" + fmtDur(s.eta_seconds) : "—";
      var cur = leaf(/^Chunk #\d+$/); if (cur) cur.textContent = s.current && s.current.index != null ? "Chunk #" + (s.current.index + 1) : "Chunk —";
      var nm = leaf(/\.txt$/); if (nm && s.output) { var n2 = $$("span").filter(function (x) { return !x.children.length && /\.txt$/.test(T(x.textContent)); }); if (n2[1]) n2[1].textContent = base(s.output); }
    }
    function renderHeat(h) {
      var grid = $('[style*="repeat(46"]'); if (!grid) return;
      var CL = { done: "bg-secondary text-on-secondary", retried: "bg-tertiary text-on-tertiary", failed: "bg-error text-on-error",
                 current: "bg-primary text-on-primary animate-pulse", pending: "bg-surface-container-highest text-outline" };
      var TT = { done: "Đạt", retried: "Đã thử lại", failed: "Thất bại", current: "Đang dịch", pending: "Chờ xử lý" };
      grid.innerHTML = h.cells.map(function (c) {
        return '<div class="h-6 rounded-sm ' + CL[c.status] + ' flex items-center justify-center font-code-sm text-[9px] font-bold select-none cursor-pointer hover:opacity-80" data-n="' + c.number + '" title="Chunk #' + c.number + ": " + TT[c.status] + '">' + c.number + "</div>";
      }).join("");
      setText(withOwn(/^Góc nhìn tức thời/) || leaf(/^Góc nhìn tức thời/), "Góc nhìn tức thời (Cửa sổ Chunk #" + h.window.from + " → #" + h.window.to + ")");
      var s = h.summary;
      [[/^Thành công \(/, "Thành công (" + s.done + ")"], [/^Thử lại x/, "Thử lại (" + s.retried + ")"],
       [/^Đang dịch \(/, "Đang dịch (" + s.current + ")"], [/^Chờ xử lý \(/, "Chờ xử lý (" + s.pending + ")"]].forEach(function (p) {
        setOwn(withOwn(p[0]), p[1]);
      });
    }
    function showChunk(n) {
      return NV.call("run.chunk", { number: n }).then(function (c) {
        setText($("#src-text"), c.source);
        currentChunk = c.number;
        setText($("#tgt-text"), c.translation == null ? "(Chunk này chưa được dịch)" : c.translation);
        var code = leaf(/^SEC_/); if (code) code.textContent = "Chương " + c.chapter + " · phần " + c.part + "/" + c.total_parts + " · chunk #" + c.number;
      }).catch(function (e) { NV.toast(e.message, "error"); });
    }
    // --- Bản dịch sinh tự động: Edit / Save / Cancel (lưu thủ công xuống backend) ---
    var tgtBox = $("#tgt-text");
    var editBtn = $$("button").filter(function (b) { var ic = b.querySelector(".material-symbols-outlined"); return ic && T(ic.textContent) === "edit"; })[0];
    var cancelBtn = null, editing = false, originalText = "";
    function enterEdit() {
      if (currentChunk == null) { NV.toast("Chưa chọn chunk nào để sửa.", "error"); return; }
      originalText = tgtBox.innerText;
      editing = true;
      tgtBox.setAttribute("contenteditable", "true");
      tgtBox.classList.add("ring-2", "ring-secondary", "outline-none", "cursor-text");
      tgtBox.focus();
      var ic = editBtn.querySelector(".material-symbols-outlined"); if (ic) ic.textContent = "check";
      editBtn.title = "Lưu bản dịch";
      if (cancelBtn) cancelBtn.classList.remove("hidden");
    }
    function exitEdit() {
      editing = false;
      if (tgtBox) { tgtBox.removeAttribute("contenteditable"); tgtBox.classList.remove("ring-2", "ring-secondary", "outline-none", "cursor-text"); }
      if (editBtn) { var ic = editBtn.querySelector(".material-symbols-outlined"); if (ic) ic.textContent = "edit"; editBtn.title = "Chỉnh sửa nóng segment"; }
      if (cancelBtn) cancelBtn.classList.add("hidden");
    }
    function saveEdit() {
      if (!editing) return;
      var text = (tgtBox.innerText || "").trim();
      if (!text) { NV.toast("Nội dung trống, chưa lưu.", "error"); return; }
      exitEdit();
      var chunkNo = currentChunk;
      return NV.call("run.update_chunk", { number: chunkNo, text: text }).then(function () {
        NV.toast("Đã lưu bản dịch chunk #" + chunkNo + ".", "ok");
      }).catch(function (e) {
        setText(tgtBox, originalText);
        NV.toast(e.message || String(e), "error");
      });
    }
    if (editBtn && tgtBox) {
      cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "p-1 rounded hover:bg-surface-container text-on-surface-variant hover:text-on-surface transition-colors hidden";
      cancelBtn.title = "Hủy chỉnh sửa";
      cancelBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">close</span>';
      editBtn.insertAdjacentElement("afterend", cancelBtn);
      editBtn.addEventListener("click", function () { if (editing) saveEdit(); else enterEdit(); });
      cancelBtn.addEventListener("click", function () { if (editing) { setText(tgtBox, originalText); exitEdit(); } });
      tgtBox.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && editing) { setText(tgtBox, originalText); exitEdit(); } });
    }

    var grid0 = $('[style*="repeat(46"]');
    if (grid0) grid0.addEventListener("click", function (ev) { var d = ev.target.closest("[data-n]"); if (d) showChunk(Number(d.dataset.n)); });

    function refreshHeat() { NV.call("run.heatmap").then(renderHeat).catch(function () {}); }
    function refreshStats() {
      NV.call("system.stats").then(function (s) {
        var g = s.gpu;
        setText(leaf(/°C$/), g ? Math.round(g.temperature_c) + "°C" : "—");
        setText(leaf(/^CUDA:/), g ? "CUDA: " + Math.round(g.utilization_percent) + "%" : "GPU: —");
        var v = leaf(/^[\d.]+ GB$/); if (v) v.textContent = g ? g.vram_used_gb + " GB" : "—";
        var vt = leaf(/^\/ \d+ GB$/); if (vt) vt.textContent = g ? "/ " + g.vram_total_gb + " GB" : "";
      }).catch(function () {});
    }

    function startFlow(mismatch) {
      return NV.call("run.preflight").then(function (p) {
        if (!p.ready) throw new Error(p.blockers.map(function (b) { return b.message; }).join(" | "));
        return NV.call("run.start", { on_fingerprint_mismatch: mismatch || "stop" });
      }).then(function () { NV.toast("Đã bắt đầu dịch.", "ok"); }).catch(function (e) {
        var codes = (e.data && e.data.needs_confirmation || []).map(function (c) { return c.code; });
        if (e.code === "NEED_CONFIRMATION" && codes.indexOf("FINGERPRINT_MISMATCH") >= 0 && !mismatch) {
          if (confirm("Danh sách chunk khác lần chạy trước.\nOK = tiếp tục với tiến độ cũ.\nHủy = chọn tiếp: dịch lại từ đầu.")) return startFlow("continue");
          if (confirm("Dịch lại từ đầu? (output và progress cũ sẽ được sao lưu .bak)")) return startFlow("fresh");
          return;
        }
        if (e.code === "NEED_CONFIRMATION") {
          if (confirm(e.message + "\n\nDịch lại từ đầu (có sao lưu)?")) return restartFresh(true);
          return;
        }
        throw e;
      });
    }
    function restartFresh(skipAsk) {
      return NV.call("run.restart_fresh", {}).then(function (r) {
        if (!r.needs_confirmation) return r;
        var files = (r.will_move_to_backup || []).map(base).join(", ") || "(chưa có file)";
        if (!skipAsk && !confirm(r.message + "\nSao lưu: " + files + "\n\nTiếp tục?")) return null;
        return NV.call("run.restart_fresh", { token: r.token });
      }).then(function (r) { if (r) NV.toast("Đã dịch lại từ đầu.", "ok"); });
    }

    rebind(pauseBtn, function () {
      if (lastState === "paused" || lastState === "pausing") return NV.call("run.resume");
      if (ACTIVE[lastState]) return NV.call("run.pause").then(function () { NV.toast("Sẽ tạm dừng sau khi lưu xong chunk hiện tại.", "info"); });
      return startFlow();
    });
    rebind(btn(/Dừng khẩn cấp/), function () {
      if (!ACTIVE[lastState] && lastState !== "paused") { NV.toast("Engine không chạy.", "error"); return; }
      if (!confirm("Dừng khẩn cấp engine? Chunk đang dịch dở sẽ mất.")) return;
      return NV.call("run.stop", { graceful: false });
    });
    rebind(btn(/Tiếp tục dịch/), function () {
      if (lastState === "paused" || lastState === "pausing") return NV.call("run.resume");
      if (ACTIVE[lastState]) { NV.toast("Engine đang chạy.", "info"); return; }
      return startFlow();
    });
    rebind(btn(/Dịch lại từ đầu/), function () { return restartFresh(false); });
    rebind(btn(/Mở file bản dịch/), function () { return NV.call("system.open", { kind: "output" }); });
    rebind(btn(/Mở thư mục đầu ra/), function () { return NV.call("system.open", { kind: "output_dir" }); });
    rebind(btn(/Xóa log|Clear/) || $$('button[onclick="clearLogs()"]')[0], function () { term.innerHTML = ""; });

    term.innerHTML = "";
    NV.call("logs.query", { limit: 200 }).then(function (r) {
      r.items.forEach(addLog);
      NV.sse(lastId, { log: addLog, progress: renderStatus });
    });
    NV.call("run.status").then(renderStatus).catch(function () {});
    refreshHeat(); refreshStats();
    setInterval(refreshHeat, 3000); setInterval(refreshStats, 5000);
  }

  // ------------------------------------------------------------------ G. nhat-ky
  function pageG() {
    var term = $("#log-terminal");
    var S = { level: "ALL", query: "", regex: false, caseSens: false, since: 0 };
    var LV = { INFO: "bg-primary-container/20 text-primary", SUCCESS: "bg-secondary-container/20 text-secondary",
               WARNING: "bg-tertiary-container/20 text-tertiary", ERROR: "bg-error-container/20 text-error" };
    var auto = $("#auto-scroll-toggle");
    function scroll() { if (auto && auto.checked) term.scrollTop = term.scrollHeight; }
    function rowHtml(e) {
      return '<div class="log-row flex items-start gap-space-sm leading-relaxed p-0.5 rounded hover:bg-surface-container-high/40 transition-colors" data-type="' + esc(e.level) + '">' +
        '<span class="px-1.5 py-0.2 rounded ' + (LV[e.level] || LV.INFO) + ' font-code-sm text-code-sm font-medium shrink-0">[' + esc(e.level) + ']</span>' +
        '<span class="text-outline font-code-sm text-code-sm shrink-0">[' + esc(e.time) + ']</span>' +
        '<span class="text-on-surface font-code-sm text-code-sm">' + esc(e.text) + "</span></div>";
    }
    function matches(e) {
      if (S.level !== "ALL" && e.level !== S.level) return false;
      if (!S.query) return true;
      var hay = e.text + " " + e.time;
      if (S.regex) { try { return new RegExp(S.query, S.caseSens ? "" : "i").test(hay); } catch (x) { return true; } }
      return S.caseSens ? hay.indexOf(S.query) >= 0 : hay.toLowerCase().indexOf(S.query.toLowerCase()) >= 0;
    }
    function counts(c) {
      $$(".log-filter-btn").forEach(function (b) {
        var n = b.querySelector("span:last-child");
        if (n && c && c[b.dataset.level] != null) n.textContent = Number(c[b.dataset.level]).toLocaleString("en-US");
      });
    }
    function load() {
      return NV.call("logs.query", { levels: S.level === "ALL" ? null : [S.level], query: S.query, regex: S.regex, case: S.caseSens, limit: 500 })
        .then(function (r) {
          var items = r.items.filter(function (e) { return e.id > S.since; });
          term.innerHTML = items.map(rowHtml).join("") || '<div class="text-outline p-2">Chưa có log nào.</div>';
          counts(r.counts); scroll();
          setText(leaf(/^Buffer:/), "Buffer: " + r.buffer_lines.toLocaleString("en-US") + " lines");
          setText(leaf(/sự kiện mới nhất$/), items.length + " sự kiện hiển thị");
          S.last = r.last_id;
        });
    }
    function paintFilters() {
      $$(".log-filter-btn").forEach(function (b) {
        var on = b.dataset.level === S.level;
        b.classList.toggle("bg-surface-container-high", on); b.classList.toggle("text-on-surface", on); b.classList.toggle("shadow-sm", on);
        b.classList.toggle("bg-surface-container", !on); b.classList.toggle("text-on-surface-variant", !on);
      });
    }
    $$(".log-filter-btn").forEach(function (b) {
      rebind(b, function () { S.level = b.dataset.level; paintFilters(); return load(); });
    });
    var search = $("#log-search-input");
    var ns = search.cloneNode(true); search.replaceWith(ns);
    ns.addEventListener("input", debounce(function () { S.query = ns.value.trim(); load().catch(function () {}); }, 250));
    function toggle(el, key) {
      rebind(el, function () {
        S[key] = !S[key];
        el.classList.toggle("text-primary", S[key]); el.classList.toggle("bg-surface-container-high", S[key]);
        return load();
      });
    }
    $$("button").forEach(function (b) { var t = T(b.textContent); if (t === "Aa") toggle(b, "caseSens"); else if (t === ".*") toggle(b, "regex"); });

    rebind($("#btn-clear"), function () { S.since = S.last || 0; return load(); });
    rebind($("#btn-export"), function () {
      return NV.call("logs.export", {}).then(function (r) { NV.toast("Đã lưu " + r.lines + " dòng vào " + r.path, "ok"); });
    });
    rebind(btn(/Mở error\.log/), function () { return NV.call("system.open", { kind: "error_log" }); });
    rebind(btn(/Mở progress\.json/), function () { return NV.call("system.open", { kind: "progress" }); });

    rebind($("#btn-run-guard"), function () {
      NV.toast("Đang kiểm tra bản dịch...", "info");
      return NV.call("guard.run", {}).then(function (g) {
        var now = new Date().toTimeString().slice(0, 8);
        var lines = [{ level: g.status === "fail" ? "ERROR" : g.status === "warn" ? "WARNING" : "SUCCESS", time: now,
          text: "[GUARD] " + g.status.toUpperCase() + " · " + g.lines + " dòng, " + g.flagged_lines + " dòng nghi vấn, sạch " + g.cleanliness_pct + "% · nghiêm trọng " + g.summary.critical + ", cảnh báo " + g.summary.warnings + ", lặp " + g.summary.loops }];
        g.hits.slice(0, 40).forEach(function (h) { lines.push({ level: "WARNING", time: now, text: "[GUARD:" + h.code + "] dòng " + h.line + ": " + h.text }); });
        if (g.hits_truncated || g.hits.length > 40) lines.push({ level: "INFO", time: now, text: "[GUARD] Chỉ hiển thị 40 phát hiện đầu tiên." });
        term.insertAdjacentHTML("beforeend", lines.map(rowHtml).join("")); scroll();
        NV.toast("Guard: " + g.status.toUpperCase() + " (" + g.flagged_lines + "/" + g.lines + " dòng nghi vấn)", g.status === "pass" ? "ok" : "error");
      });
    });

    paintFilters();
    load().then(function () {
      NV.sse(S.last || 0, { log: function (e) {
        if (!matches(e)) return;
        term.insertAdjacentHTML("beforeend", rowHtml(e));
        while (term.children.length > 1000) term.removeChild(term.firstChild);
        scroll();
      } });
      setInterval(function () { NV.call("logs.query", { limit: 1 }).then(function (r) { counts(r.counts); }).catch(function () {}); }, 3000);
    }).catch(function (e) { NV.toast(e.message, "error"); });
  }

  // ------------------------------------------------------------------ E. glossary
  function pageE() {
    var S = { text: "", path: "", caseSens: false, whole: false };
    var editorLines = $("#editor-lines"), lineNumbers = $("#line-numbers"), scroller = $("#editor-scroller");
    var srcInput = $("#new-src-input"), tgtInput = $("#new-tgt-input"), modeInput = $("#new-mode-input");
    var findInput = $("#glossary-search"), replInput = $("input[placeholder='Thay thế bằng...']");
    var modal = $("#modal-moegirl");
    var SEPS = ["=>", "->", "=", "\t"];

    function modeOf(info, src, dst) {
      var low = (info || "").toLowerCase();
      if (low.indexOf("hint") >= 0 || low.indexOf("auto") >= 0 || low.indexOf("gợi ý") >= 0 || low.indexOf("tự động") >= 0) return "hint";
      if (low.indexOf("force") >= 0 || low.indexOf("ép") >= 0) return "force";
      if (src === dst) return "hint";
      var d0 = (dst || "").charAt(0);
      if (d0 && d0 === d0.toUpperCase() && d0 !== d0.toLowerCase()) return "force";
      return "hint";
    }

    function lineHtml(raw) {
      var line = (raw || "").replace(/\s+$/, "");
      var t = line.trim();
      if (!t) return '<div class="hover:bg-surface-container-high/40 px-1 rounded transition-colors">&nbsp;</div>';
      if (t.charAt(0) === "#") return '<div class="text-outline hover:bg-surface-container-high/40 px-1 rounded transition-colors">' + esc(t) + "</div>";
      var mapping = line, info = "";
      var hi = line.indexOf("#");
      if (hi >= 0) { mapping = line.slice(0, hi); info = line.slice(hi + 1).trim(); }
      var sep = null;
      for (var i = 0; i < SEPS.length; i++) if (mapping.indexOf(SEPS[i]) >= 0) { sep = SEPS[i]; break; }
      var src, dst;
      if (!sep) { src = mapping.trim(); dst = ""; }
      else { var parts = mapping.split(sep); src = parts[0].trim(); dst = parts.slice(1).join(sep).trim(); }
      var mode = modeOf(info, src, dst);
      var modeCls = mode === "hint" ? "text-tertiary" : "text-outline";
      var label = info ? "# " + info : ("# " + mode);
      if (!sep) {
        return '<div class="hover:bg-surface-container-high/40 px-1 rounded transition-colors group">'
          + '<span class="text-primary font-medium">' + esc(src) + "</span>"
          + '<span class="' + modeCls + ' ml-2">' + esc(label) + "</span></div>";
      }
      return '<div class="hover:bg-surface-container-high/40 px-1 rounded transition-colors group">'
        + '<span class="text-primary font-medium">' + esc(src) + "</span>"
        + '<span class="text-secondary px-1.5 font-bold">=&gt;</span>'
        + '<span class="text-secondary font-medium">' + esc(dst) + "</span>"
        + '<span class="' + modeCls + ' ml-2">' + esc(label) + "</span></div>";
    }

    function render(text) {
      var lines = text.split("\n");
      if (lines.length && lines[lines.length - 1] === "") lines.pop();
      var nums = [], rows = [];
      for (var i = 0; i < lines.length; i++) { nums.push("<span>" + (i + 1) + "</span>"); rows.push(lineHtml(lines[i])); }
      lineNumbers.innerHTML = nums.join("");
      editorLines.innerHTML = rows.join("");
      if (scroller) scroller.scrollTop = 0;
    }

    function setPill(label, value) {
      var span = withOwn(new RegExp("^" + label + "[:：]"));
      if (span) { var strong = span.querySelector("strong"); if (strong) strong.textContent = String(value); }
    }

    function renderStats(r) {
      var st = r.stats || {};
      var pathEl = $(".select-all");
      if (pathEl) setText(pathEl, r.path || "(chưa có file)");
      var lc = leaf(/dòng hợp lệ/);
      if (lc) setText(lc, (st.valid || 0) + " dòng hợp lệ");
      var auto = withOwn(/\(Auto-saved\)/);
      if (auto) setOwn(auto, " " + (r.modified_at ? r.modified_at.slice(11, 19) : "--:--:--") + " (Auto-saved)");
      setPill("Hợp lệ", st.valid || 0);
      setPill("Trùng nguồn", st.duplicates || 0);
      setPill("Thiếu vế đích", st.missing_target || 0);
      setPill("Force", st.force || 0);
      setPill("Hint", st.hint || 0);

      var errors = (r.issues || []).filter(function (i) { return i.severity === "error"; });
      var badge = leaf(/^(PASS|FAIL)/);
      if (badge) {
        if (errors.length) {
          badge.textContent = "FAIL " + errors.length + " lỗi";
          badge.className = "px-space-xs py-0.5 rounded bg-error/20 text-error font-label-sm text-label-sm uppercase tracking-wider font-semibold";
        } else {
          badge.textContent = "PASS 100%";
        }
      }
      var descCount = leaf(/^\d+ dòng$/);
      if (descCount) descCount.textContent = (st.valid || 0) + " dòng";

      var total = (st.force || 0) + (st.hint || 0);
      var fp = total ? (100 * (st.force || 0) / total) : 0, hp = total ? (100 * (st.hint || 0) / total) : 0;
      var distLabel = withOwn(/Phân bổ chế độ thuật ngữ:/);
      if (distLabel) {
        var right = distLabel.parentElement.querySelector(".text-on-surface");
        if (right) right.textContent = fp.toFixed(1) + "% Force / " + hp.toFixed(1) + "% Hint";
      }
      var b1 = $(".bg-primary.h-full");
      if (b1) { b1.style.width = fp.toFixed(1) + "%"; b1.title = "Force: " + (st.force || 0); }
      var b2 = $(".bg-tertiary.h-full");
      if (b2) { b2.style.width = hp.toFixed(1) + "%"; b2.title = "Hint: " + (st.hint || 0); }
    }

    function load() {
      return NV.call("glossary.read", {}).then(function (r) {
        S.text = r.text || "";
        S.path = r.path || "";
        render(S.text);
        renderStats(r);
        return r;
      });
    }

    function refreshValidate(text) {
      return NV.call("glossary.validate", { text: text }).then(function (v) {
        renderStats({ stats: v.stats, issues: v.issues, modified_at: null, path: S.path });
        var errs = (v.issues || []).filter(function (i) { return i.severity === "error"; });
        NV.toast(errs.length ? "Phát hiện " + errs.length + " lỗi cú pháp." : "Cú pháp hợp lệ (" + (v.stats.valid || 0) + " dòng).", errs.length ? "error" : "ok");
        return v;
      });
    }

    // ---- nút lệnh chính ----
    rebind($("#btn-save"), function () {
      return NV.call("glossary.save", { text: S.text }).then(function (r) {
        NV.toast("Đã lưu glossary" + (r.backup ? " (backup: " + base(r.backup) + ")" : ""), "ok");
        return load();
      });
    });
    rebind(btn(/Tải lại/), load);
    rebind(btn(/Tạo backup/), function () {
      return NV.call("glossary.backup", {}).then(function (r) { NV.toast("Đã tạo backup: " + r.backup, "ok"); });
    });
    rebind(btn(/Kiểm tra cú pháp/), function () { return refreshValidate(S.text); });
    rebind($("#btn-add-line"), function () { if (srcInput) srcInput.focus(); });

    // ---- chèn nhanh ----
    rebind($("#btn-quick-insert"), function () {
      var src = (srcInput ? srcInput.value : "").trim();
      var tgt = (tgtInput ? tgtInput.value : "").trim();
      var modeRaw = modeInput ? modeInput.value : "# force";
      var mode = modeRaw.indexOf("hint") >= 0 ? "hint" : "force";
      if (!src || !tgt) { NV.toast("Cần nhập cả nguồn và đích.", "error"); return; }
      return NV.call("glossary.insert", { src: src, dst: tgt, mode: mode }).then(function () {
        NV.toast("Đã thêm: " + src + " => " + tgt, "ok");
        if (srcInput) srcInput.value = "";
        if (tgtInput) tgtInput.value = "";
        if (srcInput) srcInput.focus();
        return load();
      }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
    });

    // ---- tìm & thay thế ----
    function toggleCase() { S.caseSens = !S.caseSens; NV.toast(S.caseSens ? "Phân biệt hoa/thường: BẬT" : "Phân biệt hoa/thường: TẮT", "info"); }
    function toggleWhole() { S.whole = !S.whole; NV.toast(S.whole ? "Khớp toàn bộ từ: BẬT" : "Khớp toàn bộ từ: TẮT", "info"); }
    $$("button").forEach(function (b) {
      var t = T(b.textContent);
      if (t === "Aa") rebind(b, toggleCase);
      else if (t === "\\b") rebind(b, toggleWhole);
    });
    function doReplace(apply) {
      var find = (findInput ? findInput.value : "").trim();
      var repl = replInput ? replInput.value : "";
      if (!find) { NV.toast("Chưa nhập chuỗi cần tìm.", "error"); return; }
      return NV.call("glossary.find_replace", { find: find, repl: repl, apply: apply, case: S.caseSens, whole: S.whole }).then(function (r) {
        if (!r.replacements) { NV.toast("Không tìm thấy chuỗi '" + find + "'.", "info"); return; }
        if (apply) {
          NV.toast("Đã thay " + r.replacements + " chỗ và lưu.", "ok");
          return load();
        }
        S.text = r.text;
        render(S.text);
        NV.toast("Đã áp " + r.replacements + " thay thế vào bản nháp; bấm Lưu thay đổi để ghi file.", "info");
      }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
    }
    rebind(btn(/^Đổi hết$/), function () { return doReplace(true); });
    rebind(btn(/^Đổi$/), function () { return doReplace(false); });

    // ---- modal nhập từ Moegirl ----
    var previewToken = null;
    rebind($("#btn-open-modal"), function () {
      return NV.call("moegirl.commit_preview", {}).then(function (p) {
        previewToken = p.token;
        var add = p.add || [];
        var pEl = withOwn(/sẽ hợp nhất/);
        if (pEl) { var strong = pEl.querySelector("strong"); if (strong) strong.textContent = add.length + " thuật ngữ mới"; }
        var bakLabel = withOwn(/Dự phòng tự động:/);
        if (bakLabel && bakLabel.nextElementSibling) {
          bakLabel.nextElementSibling.textContent = base(p.target) + ".bak_" + new Date().toISOString().slice(0, 10).replace(/-/g, "");
        }
        var lineLabel = withOwn(/Tổng số dòng sau khi gộp:/);
        if (lineLabel && lineLabel.nextElementSibling) {
          lineLabel.nextElementSibling.textContent = (p.lines_after_estimate || 0) + " dòng";
        }
        if (modal) { modal.classList.remove("hidden"); modal.classList.add("flex"); }
      }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
    });
    rebind($("#btn-cancel-modal"), function () {
      if (modal) { modal.classList.add("hidden"); modal.classList.remove("flex"); }
    });
    rebind($("#btn-confirm-modal"), function () {
      if (!previewToken) { NV.toast("Hãy xem trước trước khi xác nhận.", "error"); return; }
      return NV.call("moegirl.commit", { token: previewToken }).then(function (r) {
        previewToken = null;
        if (modal) { modal.classList.add("hidden"); modal.classList.remove("flex"); }
        NV.toast("Đã hợp nhất " + (r.added || 0) + " thuật ngữ vào glossary.", "ok");
        return load();
      }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
    });

    // ---- Engine dịch + Bắt đầu dịch (chuyển từ màn hình A) ----
    (function installEnginePanel() {
      var card = $("#nv-engine-card");
      if (!card) return;
      var el = function (id) { return card.querySelector("#" + id); };
      var DS_LABEL = { idle: "Chưa chạy", starting: "Đang khởi động", running: "Đang dịch",
                       stopping: "Đang dừng…", finished: "Đã xong", failed: "Lỗi", stopped: "Đã dừng" };
      var info = null, mode = "script", files = [], outTouched = false, dsRunning = false;

      function modeName(m) { return m === "deepseek" ? "DeepSeek (API)" : "dichTrungsangViet.py"; }

      function paintMode() {
        var scripted = mode === "script";
        el("engine-script-row").style.display = scripted ? "" : "none";
        el("nv-deepseek-box").style.display = scripted ? "none" : "";
        el("nv-ds-actions").style.display = scripted ? "none" : "";
        el("ds-bar").parentElement.style.display = scripted ? "none" : "";
        el("engine-mode-badge").textContent = scripted ? "Script engine" : "DeepSeek API";
        el("engine-mode-script").checked = scripted;
        el("engine-mode-deepseek").checked = !scripted;
        el("btn-ds-stop").disabled = scripted || !dsRunning;
        el("btn-ds-reset").disabled = scripted || dsRunning;
        el("btn-ds-stop").style.opacity = el("btn-ds-stop").disabled ? ".5" : "1";
        el("btn-ds-reset").style.opacity = el("btn-ds-reset").disabled ? ".5" : "1";
      }

      function currentInput() { return el("ds-file").value || (info ? info.source.path : ""); }
      function hintOut(p) {
        var f = files.filter(function (x) { return x.path === p; })[0];
        if (f) return f.output_hint;
        var s = String(p || "").replace(/\.[^./\\]+$/, "");
        return s ? s + ".deepseek.txt" : "";
      }
      function syncOut() {
        if (!outTouched) el("ds-out").value = hintOut(currentInput());
        var f = files.filter(function (x) { return x.path === currentInput(); })[0];
        el("ds-info").textContent = f ? (f.chapters != null ? f.chapters + " chương · " : "") + fmtBytes(f.size) : "";
      }
      function loadFiles() {
        return NV.call("deepseek.files").then(function (r) {
          files = r.files || [];
          var sel = el("ds-file"), keep = sel.value;
          sel.innerHTML = files.map(function (f) {
            return '<option value="' + esc(f.path) + '">' + esc(f.name) +
              (f.chapters != null ? "  —  " + f.chapters + " chương" : "") + "  (" + fmtBytes(f.size) + ")</option>";
          }).join("");
          var pref = keep || (files.filter(function (f) { return f.path === r.source; })[0] || files[0] || {}).path;
          if (pref) sel.value = pref;
          syncOut();
        }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
      }
      function loadSettings() {
        return NV.call("deepseek.settings_get").then(function (s) {
          el("ds-model").value = s.model || "";
          el("ds-base").value = s.base_url || "";
          el("ds-keyinfo").textContent = (s.key_count || 0) + " key"
            + (s.from_env ? " (từ DEEPSEEK_API_KEYS)" : "")
            + (s.key_count ? " · " + (s.keys || []).join(", ") : " · chưa có key");
        }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
      }

      function paintDS(st) {
        st = st || {};
        dsRunning = !!st.running || st.state === "running" || st.state === "starting" || st.state === "stopping";
        var pct = Math.max(0, Math.min(100, Number(st.percent || 0)));
        el("ds-bar").style.width = pct + "%";
        el("btn-start-translate").disabled = mode === "deepseek" && dsRunning;
        el("btn-start-translate").style.opacity = el("btn-start-translate").disabled ? ".5" : "1";
        paintMode();
        if (mode !== "deepseek") { el("ds-msg").textContent = "Engine script: xem tiến trình ở màn hình F."; return; }
        var bits = [DS_LABEL[st.state] || st.state || "Chưa chạy"];
        if (st.total_chunks) bits.push("chunk " + (st.chunk || 0) + "/" + st.total_chunks + " (" + pct.toFixed(1) + "%)");
        if (st.chapter) bits.push("chương " + st.chapter);
        if (st.message) bits.push(st.message);
        el("ds-msg").textContent = bits.join(" · ");
      }
      function poll() {
        if (mode !== "deepseek") return Promise.resolve();
        return NV.call("deepseek.status").then(function (r) { paintDS(r.status); }).catch(function () {});
      }

      function startDeepSeek(allowAppend) {
        var input = currentInput();
        if (!input) { NV.toast("Chưa chọn file cần dịch.", "error"); return Promise.resolve(); }
        var to = el("ds-to").value.trim();
        return NV.call("deepseek.start", {
          input: input, output: el("ds-out").value.trim(),
          glossary: (info && info.files.glossary.path) || "",
          start_chapter: parseInt(el("ds-from").value, 10) || 1,
          end_chapter: to ? parseInt(to, 10) : null, allow_append: !!allowAppend
        }).then(function (r) { paintDS(r.status); NV.toast("Đã bắt đầu dịch bằng DeepSeek.", "ok"); })
          .catch(function (e) {
            if (e.code === "OUTPUT_EXISTS" && confirm(e.message + "\n\nGhi nối tiếp vào file đó?")) return startDeepSeek(true);
            if (e.code === "NO_API_KEY") el("ds-settings").open = true;
            NV.toast(e.message || String(e), "error");
          });
      }

      function restartFresh(skipAsk) {
        var out = el("ds-out").value.trim();
        if (!out) { NV.toast("Chưa có tên file đầu ra.", "error"); return Promise.resolve(); }
        if (!skipAsk && !confirm("Dịch lại từ đầu?\nFile đầu ra '" + out
          + "' và tiến trình sẽ được sao lưu (.bak) rồi bắt đầu mới.")) return Promise.resolve();
        return NV.call("deepseek.reset", { output: out, input: currentInput() }).then(function (r) {
          NV.toast("Đã sao lưu " + (r.backups || []).length + " file. Bấm Bắt đầu dịch để dịch lại.", "ok");
          return poll();
        }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
      }

      // Engine script: chạy dichTrungsangViet.py, xử lý các bước cần xác nhận như màn hình F.
      function startScript(mismatch) {
        return NV.call("run.start", { on_fingerprint_mismatch: mismatch || "stop" }).then(function () {
          NV.toast("Đã bắt đầu dịch bằng dichTrungsangViet.py. Đang mở màn hình tiến trình…", "ok");
          NV.go("dich-tien-trinh");
        }).catch(function (e) {
          var codes = ((e.data && e.data.needs_confirmation) || []).map(function (c) { return c.code; });
          if (e.code === "NEED_CONFIRMATION" && codes.indexOf("FINGERPRINT_MISMATCH") >= 0 && !mismatch) {
            if (confirm("Danh sách chunk khác lần chạy trước.\nOK = tiếp tục với tiến độ cũ.\nHủy = chọn tiếp: dịch lại từ đầu.")) return startScript("continue");
            if (confirm("Dịch lại từ đầu? (output và progress cũ sẽ được sao lưu .bak)")) return startScript("fresh");
            return;
          }
          if (e.code === "NEED_CONFIRMATION") {
            if (confirm(e.message + "\n\nDịch lại từ đầu (có sao lưu)?")) {
              return NV.call("run.restart_fresh", {}).then(function (r) {
                if (!r.needs_confirmation) return r;
                return NV.call("run.restart_fresh", { token: r.token });
              }).then(function () { return startScript("fresh"); });
            }
            return;
          }
          NV.toast(e.message || String(e), "error");
        });
      }

      function setMode(next, enginePath) {
        return NV.call("engine.set_mode", { mode: next, engine: enginePath || "" }).then(function (r) {
          mode = r.mode || next;
          if (r.engine) el("engine-script-path").value = r.engine;
          paintMode();
          NV.toast("Engine dịch: " + modeName(mode), "ok");
          if (mode === "deepseek") return loadFiles().then(loadSettings).then(poll);
          return null;
        }).catch(function (e) { NV.toast(e.message || String(e), "error"); paintMode(); });
      }

      el("engine-mode-script").addEventListener("change", function () { setMode("script"); });
      el("engine-mode-deepseek").addEventListener("change", function () { setMode("deepseek"); });
      el("btn-engine-script").onclick = function () {
        NV.pickFile("py").then(function (r) {
          if (!r || !r.ok) { if (r && !r.cancelled) NV.toast("Không chọn được script.", "error"); return; }
          return setMode("script", r.path);
        }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
      };
      el("ds-file").addEventListener("change", function () { outTouched = false; syncOut(); });
      el("ds-out").addEventListener("input", function () { outTouched = true; });
      el("ds-refresh").onclick = function () { outTouched = false; loadFiles(); };
      el("ds-save").onclick = function () {
        NV.call("deepseek.settings_save", { api_keys: el("ds-keys").value, model: el("ds-model").value, base_url: el("ds-base").value })
          .then(function () { el("ds-keys").value = ""; NV.toast("Đã lưu cài đặt DeepSeek.", "ok"); return loadSettings(); })
          .catch(function (e) { NV.toast(e.message || String(e), "error"); });
      };
      el("ds-clear").onclick = function () {
        if (!confirm("Xóa toàn bộ API key DeepSeek đã lưu?")) return;
        NV.call("deepseek.settings_clear_keys").then(loadSettings)
          .catch(function (e) { NV.toast(e.message || String(e), "error"); });
      };
      el("btn-ds-stop").onclick = function () {
        NV.call("deepseek.stop").then(function (r) { paintDS(r.status); })
          .catch(function (e) { NV.toast(e.message || String(e), "error"); });
      };
      el("btn-ds-reset").onclick = function () { restartFresh(false); };
      el("btn-start-translate").onclick = function () {
        if (mode === "deepseek") return startDeepSeek(false);
        return NV.call("run.preflight").then(function (p) {
          if (!p.ready) throw new Error(p.blockers.map(function (b) { return b.message; }).join(" | "));
          return startScript("stop");
        }).catch(function (e) { NV.toast(e.message || String(e), "error"); });
      };

      NV.call("project.info").then(function (i) {
        info = i;
        var cfg = i.config || {};
        mode = cfg.engine_mode === "deepseek" ? "deepseek" : "script";
        el("engine-script-path").value = cfg.engine || "";
        paintMode();
        if (mode === "deepseek") return loadFiles().then(loadSettings).then(poll);
        return null;
      }).catch(function (e) {
        if (e.code === "NO_PROJECT") {
          el("ds-msg").textContent = "Chưa mở dự án: chọn file truyện ở màn hình A trước.";
          return;
        }
        NV.toast(e.message || String(e), "error");
      });
      setInterval(function () { if (mode === "deepseek") poll(); }, 2000);
    })();

    load().catch(function (e) { NV.toast(e.message || String(e), "error"); });
  }

  // ------------------------------------------------------------------ C. nhan-vat
  function pageC() {
    var S = { query: "", case: false, whole: false, regex: false, work_id: "", kind: "",
              items: [], selected: new Set(), editing: null, pendingDelete: null };
    var tbody = $("tbody");
    var selAll = $("#selectAllCheckbox");
    var searchInput = $("#searchInput");
    var kindFilter = $("#kindFilter");
    var workFilter = $("#workFilter");
    var modal = $("#anchorModal");
    var deleteAlert = $("#deleteAlert");
    var modalTitle = withOwn(/nhân vật mốc mới/);
    var submitBtn = $("#submitModalBtn");

    var KIND = {
      main: { icon: "star", cls: "bg-primary-container text-on-primary-container" },
      support: { icon: "shield_person", cls: "bg-surface-container-highest text-secondary" },
      review: { icon: "pending_actions", cls: "bg-surface-container-highest text-tertiary" },
      fanfic_only: { icon: "theater_comedy", cls: "bg-surface-container-highest text-tertiary" }
    };

    // Gỡ listener mẫu trong code.html (open/close/submit/delete/filter/select-all).
    function detach(el) { if (!el) return null; var c = el.cloneNode(true); c.removeAttribute("onclick"); el.replaceWith(c); return c; }
    searchInput = detach(searchInput);
    kindFilter = detach(kindFilter);
    workFilter = detach(workFilter);
    selAll = detach(selAll);

    function field(name) {
      var labels = $$("#anchorModal label");
      for (var i = 0; i < labels.length; i++) {
        if (labels[i].textContent.indexOf(name) >= 0) {
          var ctrl = labels[i].parentElement.querySelector("input, select, textarea");
          if (ctrl) return ctrl;
        }
      }
      return null;
    }
    var fSource = field("source_name"), fWorkId = field("work_id"), fWorkTitle = field("work_title");
    var fAliases = field("aliases"), fWeight = field("weight"), fKind = field("kind");
    var fKeys = field("subject_keys"), fNotes = field("notes");

    function aliasesOf(a) { return String(a || "").split(/[,，;；|]/).map(function (x) { return x.trim(); }).filter(Boolean); }
    function weightText(w) { return typeof w === "number" ? w.toFixed(1) : String(w == null ? "" : w); }
    function weightCls(w) { return typeof w !== "number" || w === 0 ? "text-outline" : (w >= 1 ? "text-secondary" : "text-primary"); }
    function dotCls(w) { return typeof w !== "number" || w === 0 ? "bg-outline" : (w >= 1 ? "bg-secondary" : "bg-primary"); }

    function rowHtml(r) {
      var k = KIND[r.kind] || KIND.support;
      var chips = aliasesOf(r.aliases).map(function (a) {
        return '<span class="px-1.5 py-0.5 rounded bg-surface-container-highest">' + esc(a) + '</span>';
      }).join(" ");
      var checked = S.selected.has(r.row_id) ? " checked" : "";
      return '<tr class="h-8 hover:bg-surface-container-high transition-colors group cursor-pointer" data-id="' + esc(r.row_id) + '">' +
        '<td class="px-space-sm text-center"><input class="row-checkbox rounded bg-surface-container-highest cursor-pointer accent-primary h-3.5 w-3.5" type="checkbox"' + checked + '/></td>' +
        '<td class="px-space-md font-headline-sm text-label-md text-on-surface font-medium">' + esc(r.source_name) + '</td>' +
        '<td class="px-space-md font-code-sm text-code-sm text-outline">' + esc(r.work_id) + '</td>' +
        '<td class="px-space-md font-label-md text-on-surface font-medium">' + esc(r.work_title) + '</td>' +
        '<td class="px-space-md font-code-sm text-code-sm text-on-surface-variant">' + (chips || '<span class="text-outline-variant italic">—</span>') + '</td>' +
        '<td class="px-space-md text-right font-code-sm text-code-sm"><span class="inline-flex items-center justify-end gap-1 font-semibold ' + weightCls(r.weight) + '">' + weightText(r.weight) + '<span class="w-1.5 h-1.5 rounded-full ' + dotCls(r.weight) + '"></span></span></td>' +
        '<td class="px-space-md"><span class="inline-flex items-center gap-1 px-space-sm py-0.5 rounded ' + k.cls + ' font-label-sm text-label-sm font-medium"><span class="material-symbols-outlined text-[12px]">' + k.icon + '</span>' + esc(r.kind) + '</span></td>' +
        '<td class="px-space-md font-code-sm text-code-sm text-outline">' + esc(r.subject_keys) + '</td>' +
        '<td class="px-space-md text-on-surface-variant truncate max-w-xs" title="' + esc(r.notes) + '">' + esc(r.notes) + '</td>' +
        '<td class="px-space-sm text-center"><div class="flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">' +
        '<button class="p-1 hover:text-primary transition-colors" title="Chỉnh sửa dòng" type="button" data-act="edit"><span class="material-symbols-outlined text-[15px]">edit</span></button>' +
        '<button class="p-1 hover:text-error transition-colors" title="Xóa" type="button" data-act="del"><span class="material-symbols-outlined text-[15px]">delete</span></button>' +
        '</div></td></tr>';
    }

    function updateSelAll() {
      if (!selAll) return;
      selAll.checked = S.items.length > 0 && S.selected.size === S.items.length;
      selAll.indeterminate = S.selected.size > 0 && S.selected.size < S.items.length;
    }
    function renderRows() {
      if (!tbody) return;
      tbody.innerHTML = S.items.length
        ? S.items.map(rowHtml).join("")
        : '<tr><td colspan="10" class="py-6 text-center text-outline">Không có nhân vật mốc nào khớp. Bấm “Thêm nhân vật” hoặc “Nhập TSV” để tạo.</td></tr>';
      updateSelAll();
    }
    function findRow(id) {
      for (var i = 0; i < S.items.length; i++) if (S.items[i].row_id === id) return S.items[i];
      return null;
    }

    function setStrong(labelRe, value) {
      var el = withOwn(labelRe);
      if (el) { var s = el.querySelector("strong"); if (s) s.textContent = String(value); }
    }
    function setCard(labelRe, value) {
      var el = withOwn(labelRe);
      if (!el) return;
      var card = el.closest(".bg-surface-container-low");
      if (!card) return;
      var num = card.querySelector(".font-headline-md");
      if (num) num.textContent = value + " mục";
    }
    function renderDirty(d) {
      var badge = $("#saveFileBtn");
      if (!badge) return;
      var span = badge.querySelector("span.inline-flex");
      if (!span) return;
      if (d && d.total) { span.textContent = "* " + d.total + " chưa lưu"; span.style.display = ""; }
      else span.style.display = "none";
    }
    function renderCheck(c) {
      var btn = $("#checkDataBtn");
      if (!btn) return;
      var badge = btn.querySelector("span.inline-flex");
      var sec = badge ? badge.querySelector(".text-secondary") : null;
      if (sec) sec.textContent = c.valid + " Hợp lệ";
      var tert = badge ? badge.querySelector(".text-tertiary") : null;
      if (tert) tert.textContent = c.warnings.length + " Cảnh báo";
    }
    function renderMeta(r) {
      setStrong(/Tổng số nhân vật mốc/, r.total_rows);
      var avg = leaf(/^Trọng số trung bình:/);
      if (avg) setText(avg, "Trọng số trung bình: " + r.avg_weight);
      var kc = r.kind_counts || {};
      setCard(/Nhân vật chính/, kc.main || 0);
      setCard(/Phụ trợ/, kc.support || 0);
      setCard(/Cần duyệt/, kc.review || 0);
      setCard(/Ngoại truyện|Fanfic/, kc.fanfic_only || 0);
      renderDirty(r.dirty);
    }

    function populateWorks(works) {
      if (!workFilter) return;
      var html = '<option value="">Tác phẩm: Tất cả (' + works.length + ' tác phẩm)</option>';
      (works || []).forEach(function (w) {
        html += '<option value="' + esc(w.work_id) + '">' + esc(w.work_title) + ' (' + esc(w.work_id) + ')</option>';
      });
      workFilter.innerHTML = html;
      workFilter.value = S.work_id;
    }
    function load() {
      return NV.call("anchors.list", {
        query: S.query, case: S.case, whole: S.whole, regex: S.regex,
        work_id: S.work_id, kind: S.kind, page_size: 500
      }).then(function (r) {
        S.items = r.items || [];
        populateWorks(r.works || []);
        renderRows();
        renderMeta(r);
        return r;
      });
    }

    function openModal(mode, row) {
      S.editing = mode === "edit" && row ? row.row_id : null;
      if (modalTitle) setText(modalTitle, S.editing ? "Sửa nhân vật mốc" : "Thêm nhân vật mốc mới");
      if (submitBtn) setText(submitBtn, S.editing ? "Cập nhật danh sách" : "Thêm vào danh sách");
      if (row) fillForm(row); else resetForm();
      modal.classList.remove("hidden");
      modal.classList.add("flex");
    }
    function closeModal() {
      if (modal) { modal.classList.add("hidden"); modal.classList.remove("flex"); }
      S.editing = null;
    }
    function resetForm() {
      fSource.value = ""; fWorkId.value = ""; fWorkTitle.value = "";
      fAliases.value = ""; fWeight.value = "1.0"; fKind.value = "main";
      fKeys.value = ""; fNotes.value = "";
    }
    function fillForm(row) {
      fSource.value = row.source_name || "";
      fWorkId.value = row.work_id || "";
      fWorkTitle.value = row.work_title || "";
      fAliases.value = row.aliases || "";
      fWeight.value = typeof row.weight === "number" ? row.weight : (row.weight || "1.0");
      fKind.value = row.kind || "main";
      fKeys.value = row.subject_keys || "";
      fNotes.value = row.notes || "";
    }
    function readForm() {
      return {
        source_name: fSource.value.trim(), work_id: fWorkId.value.trim(),
        work_title: fWorkTitle.value.trim(), aliases: fAliases.value.trim(),
        weight: fWeight.value, kind: fKind.value,
        subject_keys: fKeys.value.trim(), notes: fNotes.value.trim()
      };
    }
    function submitModal() {
      var fields = readForm();
      if (!fields.source_name || !fields.work_id || !fields.work_title) {
        NV.toast("Cần nhập tên Trung, ID tác phẩm và tên tác phẩm.", "error"); return;
      }
      var editing = S.editing;
      var p = editing
        ? NV.call("anchors.update", { row_id: editing, fields: fields })
        : NV.call("anchors.add", { fields: fields });
      return p.then(function () {
        closeModal();
        NV.toast(editing ? "Đã cập nhật nhân vật mốc." : "Đã thêm nhân vật mốc.", "ok");
        return load();
      });
    }

    function askDelete(ids) {
      if (!ids.length) { NV.toast("Chưa chọn nhân vật nào.", "error"); return; }
      S.pendingDelete = ids;
      var msg = withOwn(/muốn xóa/, deleteAlert);
      if (msg) setOwn(msg, " Bạn có chắc chắn muốn xóa " + ids.length + " mục đã chọn? Thao tác có thể khôi phục trước khi Lưu file.");
      deleteAlert.classList.remove("hidden");
    }
    function doDelete() {
      var ids = S.pendingDelete || [];
      S.pendingDelete = null;
      deleteAlert.classList.add("hidden");
      if (!ids.length) return;
      return NV.call("anchors.delete", { row_ids: ids }).then(function (r) {
        NV.toast("Đã xóa " + r.deleted + " nhân vật mốc.", "ok");
        ids.forEach(function (id) { S.selected.delete(id); });
        return load();
      });
    }
    function importTsv() {
      return NV.pickFile("tsv").then(function (r) {
        if (!r || !r.ok) { if (r && !r.cancelled) NV.toast("Không chọn được file.", "error"); return; }
        return NV.call("anchors.import_tsv", { path: r.path, mode: "merge" }).then(function (res) {
          NV.toast("Đã nhập TSV: " + res.added + " thêm, " + res.updated + " cập nhật.", "ok");
          S.selected.clear();
          return load();
        });
      });
    }
    function exportTsv() {
      return NV.call("anchors.export_tsv", {}).then(function (r) {
        download("moegirl_work_anchors.tsv", r.text, "text/tab-separated-values;charset=utf-8");
        NV.toast("Đã xuất " + r.rows + " dòng TSV.", "ok");
      });
    }
    function reloadFromFile() {
      return NV.call("anchors.reload", {}).then(function () {
        NV.toast("Đã nạp lại từ file.", "ok");
        S.selected.clear();
        return load();
      });
    }

    if (tbody) tbody.addEventListener("change", function (ev) {
      var cb = ev.target.closest && ev.target.closest(".row-checkbox");
      if (!cb) return;
      var tr = cb.closest("tr[data-id]"); if (!tr) return;
      var id = tr.dataset.id;
      if (cb.checked) S.selected.add(id); else S.selected.delete(id);
      updateSelAll();
    });
    if (tbody) tbody.addEventListener("click", function (ev) {
      var tr = ev.target.closest && ev.target.closest("tr[data-id]"); if (!tr) return;
      var id = tr.dataset.id;
      var actBtn = ev.target.closest("button[data-act]");
      if (actBtn) {
        if (actBtn.dataset.act === "edit") openModal("edit", findRow(id));
        else if (actBtn.dataset.act === "del") askDelete([id]);
        return;
      }
      if (ev.target.closest(".row-checkbox")) return;
      var cb = tr.querySelector(".row-checkbox");
      if (cb) cb.checked = !cb.checked;
      if (cb && cb.checked) S.selected.add(id); else S.selected.delete(id);
      updateSelAll();
    });

    if (selAll) selAll.addEventListener("change", function () {
      S.items.forEach(function (r) { if (selAll.checked) S.selected.add(r.row_id); else S.selected.delete(r.row_id); });
      renderRows();
    });

    if (searchInput) searchInput.addEventListener("input", debounce(function () {
      S.query = searchInput.value.trim(); load().catch(function (e) { NV.toast(e.message, "error"); });
    }, 250));
    if (kindFilter) kindFilter.addEventListener("change", function () { S.kind = kindFilter.value; load().catch(function (e) { NV.toast(e.message, "error"); }); });
    if (workFilter) workFilter.addEventListener("change", function () { S.work_id = workFilter.value; load().catch(function (e) { NV.toast(e.message, "error"); }); });

    var caseBtn = null, wholeBtn = null, regexBtn = null;
    function paintToggle(b, on) { if (!b) return; if (on) b.classList.add("bg-surface-container-highest", "text-primary"); else b.classList.remove("bg-surface-container-highest", "text-primary"); }
    function bindToggle(b, key) {
      if (!b) return null;
      var c = b.cloneNode(true); c.removeAttribute("onclick"); b.replaceWith(c);
      c.addEventListener("click", function (ev) {
        ev.preventDefault(); S[key] = !S[key]; paintToggle(c, S[key]);
        load().catch(function (e) { NV.toast(e.message, "error"); });
      });
      return c;
    }
    var searchBar = searchInput ? searchInput.closest("div.flex") : null;
    if (searchBar) $$("button", searchBar).forEach(function (b) {
      var t = T(b.textContent);
      if (t === "Aa") caseBtn = bindToggle(b, "case");
      else if (t === "\\b") wholeBtn = bindToggle(b, "whole");
      else if (t === ".*") regexBtn = bindToggle(b, "regex");
    });
    paintToggle(caseBtn, S.case); paintToggle(wholeBtn, S.whole); paintToggle(regexBtn, S.regex);

    rebind($("#openAddModalBtn"), function () { openModal("add"); });
    rebind($("#editSelectedBtn"), function () {
      var ids = Array.from(S.selected);
      if (!ids.length) { NV.toast("Chưa chọn dòng nào.", "error"); return; }
      if (ids.length > 1) { NV.toast("Chỉ sửa từng dòng một.", "error"); return; }
      openModal("edit", findRow(ids[0]));
    });
    rebind($("#deleteBtn"), function () { askDelete(Array.from(S.selected)); });
    rebind($("#closeModalBtn"), closeModal);
    rebind($("#cancelModalBtn"), closeModal);
    rebind($("#submitModalBtn"), submitModal);
    rebind(btn(/Xác nhận xóa/, deleteAlert), doDelete);
    rebind(btn(/^Hủy$/, deleteAlert), function () { S.pendingDelete = null; deleteAlert.classList.add("hidden"); });

    rebind($("#saveFileBtn"), function () {
      return NV.call("anchors.save", {}).then(function (r) {
        NV.toast("Đã lưu " + r.rows + " nhân vật mốc" + (r.backup ? " (backup: " + base(r.backup) + ")" : ""), "ok");
        return load();
      });
    });
    rebind($("#checkDataBtn"), function () {
      return NV.call("anchors.check", {}).then(function (c) {
        renderCheck(c);
        if (c.errors.length) NV.toast("Kiểm tra: " + c.valid + " hợp lệ, " + c.errors.length + " lỗi, " + c.warnings.length + " cảnh báo.", "error");
        else if (c.warnings.length) NV.toast("Kiểm tra: " + c.valid + " hợp lệ, " + c.warnings.length + " cảnh báo.", "info");
        else NV.toast("Kiểm tra: tất cả " + c.valid + " dòng hợp lệ.", "ok");
      });
    });
    rebind(btn(/Nhập TSV/), importTsv);
    rebind(btn(/Xuất TSV/), exportTsv);
    rebind(btn(/Nạp lại từ file/), reloadFromFile);

    load().catch(function (e) { NV.toast(e.message || String(e), "error"); });
  }

  // ------------------------------------------------------------------ D. moegirl
  function pageD() {
    var S = { query: "", case: false, regex: false, work_id: "", status: "all",
              items: [], selected: new Set(), current: null, totalCandidates: 0,
              statusCounts: {}, workCounts: {}, workMap: {}, previewToken: null,
              page_no: 1, page_size: 50, total: 0, pages: 1, sort: "count", conflictOnly: false };
    var tbody = $("tbody");
    var selAll = $("#check-all");
    var statusFilter = $("#status-filter");
    var searchInput = $("#search-input");
    var workFilter = $("#work-filter");
    var inspector = $("#inspector");
    var modal = $("#confirmation-dialog");
    var openCommitBtn = $("#open-commit-modal-btn");

    var STATUS_LABEL = { pending: "Chờ duyệt", approved: "Đã duyệt", rejected: "Từ chối" };
    var STATUS_BADGE = {
      pending: ["bg-tertiary-container/20 text-tertiary", "bg-tertiary"],
      approved: ["bg-secondary-container/20 text-secondary", "bg-secondary"],
      rejected: ["bg-error-container/20 text-error", "bg-error"]
    };

    // Gỡ listener tĩnh của code.html.
    function detach(el) { if (!el) return null; var c = el.cloneNode(true); c.removeAttribute("onclick"); el.replaceWith(c); return c; }
    selAll = detach(selAll);
    statusFilter = detach(statusFilter);
    searchInput = detach(searchInput);
    if (statusFilter) statusFilter.value = S.status;
    if (inspector) inspector.innerHTML = '<div class="text-outline text-center py-8">Chọn một ứng viên trong bảng để xem chi tiết và duyệt.</div>';

    function findRow(id) { for (var i = 0; i < S.items.length; i++) if (S.items[i].id === id) return S.items[i]; return null; }
    function mrow(k, v) { return '<div class="flex items-center justify-between"><span class="text-outline">' + k + ':</span><span class="text-on-surface-variant">' + v + '</span></div>'; }

    function rowHtml(c) {
      var b = STATUS_BADGE[c.status] || STATUS_BADGE.pending;
      var checked = S.selected.has(c.id) ? " checked" : "";
      var alt = c.dst_alt ? ' <span class="text-outline-variant font-normal">(' + esc(c.dst_alt) + ')</span>' : "";
      var titles = (c.work_titles || []).join(", ");
      var aliases = (c.aliases || []).slice(0, 2).join(", ");
      var confPct = (c.confidence === null || c.confidence === undefined) ? "" : Math.round(c.confidence * 100) + "%";
      var latinField = esc(c.latin_field || "");
      var note = c.conflict
        ? '<span class="text-tertiary">⚠ xung đột: ' + esc(c.conflict.existing_dst || "") + '</span>'
        : esc(c.note || "");
      var act = c.status === "approved"
        ? '<button class="px-space-sm py-0.5 rounded bg-surface-container-high hover:bg-surface-container-highest text-outline hover:text-error font-label-sm text-label-sm transition-colors" data-act="pending" type="button">Bỏ duyệt</button>'
        : '<button class="px-space-sm py-0.5 rounded bg-surface-container-high hover:bg-primary-container hover:text-on-primary-container text-outline font-label-sm text-label-sm transition-colors" data-act="approved" type="button">Duyệt</button>' +
          '<button class="px-space-sm py-0.5 rounded bg-surface-container-high hover:bg-error-container hover:text-on-error-container text-outline font-label-sm text-label-sm transition-colors" data-act="rejected" type="button">Từ chối</button>';
      return '<tr class="hover:bg-surface-container transition-colors group" data-id="' + esc(c.id) + '">' +
        '<td class="px-space-md py-space-sm text-center"><input class="row-checkbox rounded bg-surface-container-highest accent-primary" type="checkbox"' + checked + '/></td>' +
        '<td class="px-space-md py-space-sm font-headline-sm text-label-md text-on-surface font-semibold font-mono">' + esc(c.src) + '</td>' +
        '<td class="px-space-md py-space-sm"><div class="flex items-center gap-space-xs font-label-md text-label-md text-primary font-medium"><span>' + esc(c.dst) + '</span>' + alt + '</div></td>' +
        '<td class="px-space-md py-space-sm text-on-surface-variant font-label-sm text-label-sm">' + esc(titles) + '</td>' +
        '<td class="px-space-md py-space-sm font-mono text-outline">' + esc(aliases) + '</td>' +
        '<td class="px-space-md py-space-sm text-right font-code-sm text-code-sm text-secondary font-semibold">' + c.count + '</td>' +
        '<td class="px-space-md py-space-sm text-on-surface-variant font-label-sm text-label-sm">' + latinField + '</td>' +
        '<td class="px-space-md py-space-sm text-right font-code-sm text-code-sm text-secondary">' + esc(confPct) + '</td>' +
        '<td class="px-space-md py-space-sm font-code-sm text-code-sm text-outline">' + esc(c.source_tag) + '</td>' +
        '<td class="px-space-md py-space-sm"><span class="inline-flex items-center gap-1 px-space-xs py-0.5 rounded ' + b[0] + ' font-label-sm text-label-sm"><span class="w-1.5 h-1.5 rounded-full ' + b[1] + '"></span>' + STATUS_LABEL[c.status] + '</span></td>' +
        '<td class="px-space-md py-space-sm text-on-surface-variant font-body-sm text-body-sm">' + note + '</td>' +
        '<td class="px-space-md py-space-sm text-center">' + act + '</td></tr>';
    }

    function selectedOnPage() {
      var n = 0;
      S.items.forEach(function (c) { if (S.selected.has(c.id)) n++; });
      return n;
    }
    function updateSelAll() {
      if (!selAll) return;
      var onPage = selectedOnPage();
      selAll.checked = S.items.length > 0 && onPage === S.items.length;
      selAll.indeterminate = onPage > 0 && onPage < S.items.length;
    }
    function renderRows() {
      if (!tbody) return;
      tbody.innerHTML = S.items.length
        ? S.items.map(rowHtml).join("")
        : '<tr><td colspan="12" class="py-6 text-center text-outline">Chưa có gợi ý nào. Bấm “Tạo lại gợi ý từ truyện” để sinh ứng viên.</td></tr>';
      updateSelAll();
    }

    function renderMeta(r) {
      var appr = S.statusCounts.approved || 0;
      var pend = S.statusCounts.pending || 0;
      var vEl = withOwn(/Hợp lệ:/);
      if (vEl) { var vs = vEl.querySelector("strong"); if (vs) vs.textContent = (r.total || 0) + "/" + (r.total_candidates || 0); }
      var fEl = withOwn(/Đã lọc rác/);
      if (fEl) { var fs = fEl.querySelector("strong"); if (fs) fs.textContent = r.filtered_total || 0; }
      var show = leaf(/Đang hiển thị/);
      if (show) {
        var start = r.total ? (r.page - 1) * r.page_size + 1 : 0;
        var end = Math.min(r.page * r.page_size, r.total);
        setText(show, "Đang hiển thị " + start + "-" + end + " trong tổng số " + r.total + " ứng viên hợp lệ");
      }
      var ap = leaf(/Đã duyệt:/);
      if (ap) setText(ap, "Đã duyệt: " + appr);
      var pp = leaf(/Chờ duyệt:/);
      if (pp) setText(pp, "Chờ duyệt: " + pend);
      if (openCommitBtn) {
        var badge = openCommitBtn.querySelector("span.rounded-full");
        if (badge) badge.textContent = appr + " mục";
      }
    }

    function renderWorkFilter() {
      if (!workFilter) return;
      var counts = S.workCounts || {};
      var html = '<button class="px-space-sm py-1 rounded ' + (S.work_id ? "bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant" : "bg-primary-container text-on-primary-container") + ' font-label-md text-label-md flex items-center gap-1 shadow-sm" data-work="" type="button"><span>Tất cả</span><span class="px-1 rounded bg-surface-container-lowest font-code-sm text-code-sm">' + S.totalCandidates + '</span></button>';
      Object.keys(counts).forEach(function (wid) {
        var title = S.workMap[wid] || wid;
        var on = S.work_id === wid;
        html += '<button class="px-space-sm py-1 rounded ' + (on ? "bg-primary-container text-on-primary-container" : "bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant") + ' font-label-md text-label-md flex items-center gap-1 shadow-sm" data-work="' + esc(wid) + '" type="button"><span>' + esc(title) + '</span><span class="px-1 rounded bg-surface-container-lowest font-code-sm text-code-sm">' + counts[wid] + '</span></button>';
      });
      workFilter.innerHTML = html;
    }

    function load(resetPage) {
      if (resetPage) S.page_no = 1;
      return NV.call("moegirl.list", {
        work_id: S.work_id, status: S.status, query: S.query,
        case: S.case, regex: S.regex, sort: S.sort,
        page_no: S.page_no, page_size: S.page_size,
        conflict: S.conflictOnly
      }).then(function (r) {
        S.items = r.items || [];
        S.total = r.total || 0;
        S.page = r.page || 1;
        S.pages = r.pages || 1;
        S.page_no = r.page || 1;
        S.page_size = r.page_size || S.page_size;
        S.workCounts = r.work_counts || {};
        S.totalCandidates = r.total_candidates || 0;
        S.statusCounts = r.status_counts || {};
        var ids = {};
        S.items.forEach(function (c) { ids[c.id] = 1; });
        S.selected.forEach(function (id) { if (!ids[id]) S.selected.delete(id); });
        var map = {};
        S.items.forEach(function (c) {
          var ws = c.works || [], ts = c.work_titles || [];
          for (var i = 0; i < ws.length; i++) if (!map[ws[i]]) map[ws[i]] = ts[i] || ws[i];
        });
        S.workMap = map;
        renderRows();
        renderWorkFilter();
        renderMeta(r);
        renderPager();
        return r;
      });
    }

    function renderPager() {
      var prev = $("#page-prev"), next = $("#page-next"), nums = $("#page-numbers");
      if (prev) {
        prev.disabled = S.page <= 1;
        prev.classList.toggle("opacity-50", S.page <= 1);
        prev.classList.toggle("cursor-not-allowed", S.page <= 1);
      }
      if (next) {
        next.disabled = S.page >= S.pages;
        next.classList.toggle("opacity-50", S.page >= S.pages);
        next.classList.toggle("cursor-not-allowed", S.page >= S.pages);
      }
      if (nums) {
        var html = "";
        var start = Math.max(1, S.page - 2), end = Math.min(S.pages, S.page + 2);
        for (var p = start; p <= end; p++) {
          html += '<button class="px-2 py-0.5 rounded ' + (p === S.page
            ? "bg-primary text-on-primary font-semibold"
            : "bg-surface-container-high hover:bg-surface-container-highest text-on-surface") +
            '" data-page="' + p + '" type="button">' + p + '</button>';
        }
        nums.innerHTML = html;
      }
    }

    function renderCacheInfo() {
      return NV.call("moegirl.cache_info", {}).then(function (c) {
        var n = (c.project_cache && c.project_cache.record_count) || c.record_count || 0;
        var label = withOwn(/Bộ nhớ TM/);
        if (label) {
          var count = label.parentElement.querySelector(".text-primary");
          if (count) count.textContent = n + " mục";
        }
      }).catch(function () {});
    }

    function renderDetail(c) {
      if (!inspector) return;
      var ctx = (c.contexts || []).map(function (x) {
        var quote = esc(x.text).split(esc(c.src)).join('<mark class="bg-primary/20 text-primary px-0.5 rounded font-mono font-bold">' + esc(c.src) + '</mark>');
        return '<div class="p-space-sm rounded bg-surface-container flex flex-col gap-1">' +
          '<div class="flex items-center justify-between text-code-sm font-code-sm"><span class="text-primary font-semibold">Chương ' + x.chapter + (x.chapter_title ? ": " + esc(x.chapter_title) : "") + '</span><span class="text-outline">Dòng ' + x.line + '</span></div>' +
          '<p class="font-body-sm text-body-sm text-on-surface-variant leading-relaxed">"…' + quote + '…"</p></div>';
      }).join("");
      var works = [];
      for (var i = 0; i < (c.works || []).length; i++) works.push((c.work_titles && c.work_titles[i]) || c.works[i]);
      var confPct = (c.confidence === null || c.confidence === undefined) ? "" : Math.round(c.confidence * 100) + "%";
      var pageTitlesRow = (c.page_titles || []).length
        ? mrow("page_titles", esc((c.page_titles || []).join(", ")))
        : "";
      var meta = mrow("work_id", '"' + esc((c.works || []).join(", ")) + '" [' + esc(works.join(", ")) + ']') +
        mrow("source_tag", esc(c.source_tag || "")) +
        mrow("category", esc(c.category || "character")) +
        mrow("tần suất", c.count + " lần / " + c.chapters + " chương") +
        mrow("latin_field", esc(c.latin_field || "")) +
        mrow("confidence", esc(confPct)) +
        pageTitlesRow +
        mrow("aliases", esc((c.aliases || []).join(", ")));

      var latinOptions = (c.latin_options || []).map(function (o) {
        var pct = (o.confidence === null || o.confidence === undefined) ? "" : Math.round(o.confidence * 100) + "%";
        return '<button class="flex items-center justify-between w-full px-space-sm py-1 rounded bg-surface-container hover:bg-surface-container-high text-left transition-colors" data-latin="' + esc(o.name) + '" type="button">' +
          '<span class="font-code-sm text-code-sm text-on-surface">' + esc(o.name) + '</span>' +
          '<span class="text-outline text-label-sm font-label-sm">' + esc(o.field || "") + (pct ? " · " + esc(pct) : "") + '</span></button>';
      }).join("");
      var latinOptionsHtml = latinOptions
        ? '<div class="flex flex-col gap-space-xs"><div class="text-outline font-label-sm text-label-sm uppercase tracking-wider">Tên Latin khác (bấm để điền vào ô đích)</div>' +
          '<div class="flex flex-col gap-space-xs">' + latinOptions + '</div></div>'
        : "";

      inspector.innerHTML =
        '<div class="flex items-center justify-between border-b border-surface-container pb-space-sm">' +
        '<div class="flex items-center gap-space-xs"><span class="material-symbols-outlined text-primary text-[18px]">find_in_page</span>' +
        '<span class="font-headline-sm text-label-md text-on-surface font-semibold">Chi tiết ứng viên: ' + esc(c.src) + '</span></div>' +
        '<span class="px-space-xs py-0.5 rounded bg-surface-container-high text-outline font-code-sm text-code-sm">ID: ' + esc(c.id) + '</span></div>' +
        '<div class="flex flex-col gap-space-xs"><div class="text-outline font-label-sm text-label-sm uppercase tracking-wider">Thông số trích xuất thô</div>' +
        '<div class="p-space-sm bg-surface-container-lowest rounded flex flex-col gap-1 font-code-sm text-code-sm">' + meta + '</div></div>' +
        latinOptionsHtml +
        '<div class="flex flex-col gap-space-xs"><div class="text-outline font-label-sm text-label-sm uppercase tracking-wider">Chỉnh sửa mục từ</div>' +
        '<div class="flex flex-col gap-1">' +
        '<input id="edit-dst" class="h-8 px-space-sm rounded bg-surface-container text-on-surface focus:outline-none focus:ring-1 focus:ring-primary font-code-sm text-code-sm" placeholder="Tên đích (dst)" value="' + esc(c.dst || "") + '"/>' +
        '<input id="edit-dst-alt" class="h-8 px-space-sm rounded bg-surface-container text-on-surface focus:outline-none focus:ring-1 focus:ring-primary font-code-sm text-code-sm" placeholder="Tên đích phụ (dst_alt)" value="' + esc(c.dst_alt || "") + '"/>' +
        '<div class="flex items-center gap-1">' +
        '<select id="edit-mode" class="h-8 px-space-sm rounded bg-surface-container text-on-surface focus:outline-none focus:ring-1 focus:ring-primary"><option value="force"' + (c.mode === "force" ? " selected" : "") + '>force (ép)</option><option value="hint"' + (c.mode === "hint" ? " selected" : "") + '>hint (gợi ý)</option></select>' +
        '<input id="edit-note" class="flex-1 h-8 px-space-sm rounded bg-surface-container text-on-surface focus:outline-none focus:ring-1 focus:ring-primary font-code-sm text-code-sm" placeholder="Ghi chú" value="' + esc(c.note || "") + '"/>' +
        '</div>' +
        '<button class="h-8 px-space-md rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface font-label-md text-label-md" data-act="save-edit" type="button">Lưu chỉnh sửa</button>' +
        '</div></div>' +
        '<div class="flex flex-col gap-space-xs"><span class="text-outline font-label-sm text-label-sm uppercase tracking-wider">Ngữ cảnh xuất hiện thực tế</span>' +
        '<div class="flex flex-col gap-space-xs">' + (ctx || '<div class="text-outline">Không có đoạn trích.</div>') + '</div></div>' +
        '<div class="flex flex-col gap-space-xs"><span class="text-outline font-label-sm text-label-sm uppercase tracking-wider">Cú pháp sẽ ghi vào thuatngu.txt</span>' +
        '<div class="p-space-sm rounded bg-surface-container-lowest font-code-sm text-code-sm text-secondary flex items-center justify-between"><span>' + esc(c.glossary_line || (c.src + " = " + c.dst)) + '</span></div></div>' +
        '<div class="flex flex-col gap-space-sm pt-space-xs">' +
        '<button class="w-full h-8 px-space-md rounded bg-primary text-on-primary hover:bg-primary/90 font-label-md text-label-md font-semibold flex items-center justify-center gap-space-xs transition-colors shadow-sm" data-act="single-confirm" type="button"><span class="material-symbols-outlined text-[16px]">add_circle</span><span>Duyệt &amp; thêm: ' + esc(c.src) + ' =&gt; ' + esc(c.dst) + '</span></button>' +
        '</div>';
    }

    function selectRow(id) {
      S.current = id;
      return NV.call("moegirl.detail", { cand_id: id }).then(renderDetail);
    }

    function setStatus(ids, status) {
      if (!ids.length) { NV.toast("Chưa chọn ứng viên nào.", "error"); return; }
      return NV.call("moegirl.set_status", { cand_ids: ids, status: status }).then(function (r) {
        ids.forEach(function (id) { S.selected.delete(id); });
        NV.toast("Đã cập nhật " + r.updated + " ứng viên.", "ok");
        return load();
      });
    }

    function fillLatin(name) {
      var dst = $("input#edit-dst");
      var dstAlt = $("input#edit-dst-alt");
      var tgt = (dst && dst.value && dst.value.trim()) ? dstAlt : dst;
      if (!tgt) tgt = dst || dstAlt;
      if (!tgt) return;
      tgt.value = name;
      NV.toast("Đã điền \"" + name + "\" vào ô tên đích.", "info");
    }

    function saveEdit() {
      if (!S.current) return;
      var dst = ($("#edit-dst") || {}).value;
      var dstAlt = ($("#edit-dst-alt") || {}).value;
      var mode = ($("#edit-mode") || {}).value;
      var note = ($("#edit-note") || {}).value;
      return NV.call("moegirl.edit", { cand_id: S.current, dst: dst, dst_alt: dstAlt, mode: mode, note: note }).then(function () {
        NV.toast("Đã lưu chỉnh sửa.", "ok");
        return load().then(function () { return selectRow(S.current); });
      });
    }

    function singleCommit() {
      if (!S.current) return;
      return NV.call("moegirl.set_status", { cand_ids: [S.current], status: "approved" }).then(function () {
        return openCommitModal([S.current]);
      });
    }

    function dominantReason(filtered, matched, noWork) {
      if (matched === 0) return "không có bản ghi Moegirl nào khớp tác phẩm đã duyệt.";
      var f = filtered || {};
      var labels = {
        no_latin_name: "không có tên Latin",
        low_frequency: "tần suất thấp",
        already_in_glossary: "đã có trong glossary",
        not_han: "không phải chữ Hán",
        too_short: "tên quá ngắn",
        common_word: "từ thông dụng"
      };
      var best = null, bestN = 0;
      Object.keys(f).forEach(function (k) { if (f[k] > bestN) { bestN = f[k]; best = k; } });
      if (!best) return "không có lý do lọc cụ thể.";
      return "phần lớn bị loại vì " + (labels[best] || best) + ": " + bestN + " mục";
    }
    function generate() {
      NV.toast("Đang kiểm tra moegirl_wikitext_names_bgm.json...", "info");
      // Bước 1: file tên cạnh chương trình; chưa có thì backend tự tạo (tải Moegirl + Bangumi, có thể mất vài phút).
      return NV.call("moegirl.ensure_names_bgm", {}).then(function (n) {
        if (n.skipped) NV.toast(n.skipped, "info");
        else if (n.stopped) NV.toast("Chưa tạo được file tên: " + n.stopped + ". Bấm lại để tiếp tục.", "error");
        else if (n.created) NV.toast("Đã tạo moegirl_wikitext_names_bgm.json (" + (n.filled || 0) + " tên bổ sung từ Bangumi)" +
          (n.stopped ? " - dừng giữa chừng: " + n.stopped + ". Bấm lại để tiếp tục." : "."), n.stopped ? "error" : "ok");
        else NV.toast("Dùng file có sẵn (" + (n.total || 0) + " trang): " + (n.path || ""), "info");
        if (n.warning) NV.toast(n.warning, "error");
        if (n.created && !n.stopped) viewNamesBgm().catch(function () {});
        if (n.stopped) return null;
        NV.toast("Đang tạo gợi ý từ truyện (có thể mất vài giây)...", "info");
        return NV.call("moegirl.generate", {});
      }).then(function (r) {
        if (!r) return load(true);
        if (!r.candidates) {
          NV.toast("Không tạo được ứng viên nào: " + dominantReason(r.filtered, r.matched_records, r.no_matching_work), "error");
        } else {
          NV.toast("Đã tạo " + r.candidates + " ứng viên (lọc " + r.filtered_total + " mục).", "ok");
        }
        return load(true);
      });
    }
    function buildProjectCache() {
      NV.toast("Đang dựng cache dự án...", "info");
      return NV.call("moegirl.build_project_cache", {}).then(function (r) {
        NV.toast("Đã dựng cache dự án: " + r.records + " mục.", "ok");
        return renderCacheInfo();
      });
    }
    function rebuildCache() {
      NV.toast("Đang dựng lại kho Moegirl (tốn thời gian)...", "info");
      return NV.call("moegirl.rebuild_cache", {}).then(function (r) {
        NV.toast("Đã dựng lại kho Moegirl: " + r.records + " mục.", "ok");
        return renderCacheInfo();
      });
    }
    function exportSuggestions() {
      return NV.call("moegirl.export", { fmt: "tsv" }).then(function (r) {
        if (!r.rows) { NV.toast("Chưa có gợi ý nào để xuất.", "error"); return; }
        download("moegirl_suggestions.tsv", r.text, "text/tab-separated-values;charset=utf-8");
        NV.toast("Đã xuất " + r.rows + " gợi ý.", "ok");
      });
    }
    function clearFilter() {
      S.query = ""; S.work_id = ""; S.status = "all"; S.case = false; S.regex = false; S.conflictOnly = false;
      if (searchInput) searchInput.value = "";
      if (statusFilter) statusFilter.value = "all";
      if (sortSelect) sortSelect.value = "count"; S.sort = "count";
      paintToggle(caseBtn, false); paintToggle(regexBtn, false);
      paintConflict();
      return load(true);
    }

    function renderCommitPreview(p) {
      var add = p.add || [];
      var payload = modal.querySelector(".max-h-40");
      if (payload) {
        payload.innerHTML = add.map(function (e) {
          return '<div class="text-on-surface">' + esc(e.src) + ' = ' + esc(e.dst) + ' <span class="text-outline">#' + esc(e.note || e.section || "") + '</span></div>';
        }).join("");
      }
      var desc = withOwn(/Hãy kiểm tra danh sách/, modal);
      if (desc) setOwn(desc, " Hành động này sẽ ghi đè trực tiếp vào từ điển chính thức. Hãy kiểm tra danh sách " + add.length + " ứng viên đã được bạn đánh dấu duyệt:");
      var target = withOwn(/File đích:/, modal);
      if (target) target.innerHTML = 'File đích: <code class="text-primary font-code-sm text-code-sm">' + esc(p.target) + '</code> (+' + add.length + ' dòng mới)';
      if (p.conflicts && p.conflicts.length) NV.toast("Có " + p.conflicts.length + " xung đột (sẽ giữ bản cũ).", "info");
    }
    function openCommitModal(ids) {
      var params = {};
      if (ids && ids.length) params.cand_ids = ids;
      return NV.call("moegirl.commit_preview", params).then(function (p) {
        S.previewToken = p.token;
        renderCommitPreview(p);
        modal.classList.remove("hidden");
        modal.classList.add("flex");
      });
    }
    function commit() {
      if (!S.previewToken) { NV.toast("Hãy xem trước trước khi xác nhận.", "error"); return; }
      return NV.call("moegirl.commit", { token: S.previewToken }).then(function (r) {
        S.previewToken = null;
        modal.classList.add("hidden"); modal.classList.remove("flex");
        NV.toast("Đã ghi " + (r.committed || 0) + " mục vào glossary.", "ok");
        return load();
      });
    }

    var caseBtn = null, regexBtn = null;
    var sortSelect = $("#sort-select");
    function paintToggle(b, on) { if (!b) return; if (on) b.classList.add("bg-surface-container-high", "text-primary"); else b.classList.remove("bg-surface-container-high", "text-primary"); }
    function paintConflict() {
      var b = $("#btn-conflict-only");
      if (!b) return;
      if (S.conflictOnly) { b.classList.add("bg-tertiary-container", "text-on-tertiary-container"); }
      else { b.classList.remove("bg-tertiary-container", "text-on-tertiary-container"); }
    }
    function bindSpanToggle(s, key) {
      if (!s) return null;
      var c = s.cloneNode(true);
      s.replaceWith(c);
      c.style.cursor = "pointer";
      c.addEventListener("click", function (ev) {
        ev.preventDefault(); S[key] = !S[key]; paintToggle(c, S[key]);
        load(true).catch(function (e) { NV.toast(e.message, "error"); });
      });
      return c;
    }
    var searchBar = searchInput ? searchInput.closest("div.flex") : null;
    if (searchBar) $$("span", searchBar).forEach(function (s) {
      var t = T(s.textContent);
      if (t === "Aa") caseBtn = bindSpanToggle(s, "case");
      else if (t === ".*") regexBtn = bindSpanToggle(s, "regex");
    });
    paintToggle(caseBtn, S.case); paintToggle(regexBtn, S.regex);

    if (tbody) tbody.addEventListener("change", function (ev) {
      var cb = ev.target.closest && ev.target.closest(".row-checkbox");
      if (!cb) return;
      var tr = cb.closest("tr[data-id]"); if (!tr) return;
      var id = tr.dataset.id;
      if (cb.checked) S.selected.add(id); else S.selected.delete(id);
      updateSelAll();
    });
    if (tbody) tbody.addEventListener("click", function (ev) {
      var tr = ev.target.closest && ev.target.closest("tr[data-id]"); if (!tr) return;
      var id = tr.dataset.id;
      var actBtn = ev.target.closest("button[data-act]");
      if (actBtn) { setStatus([id], actBtn.dataset.act); return; }
      if (ev.target.closest(".row-checkbox")) return;
      selectRow(id);
    });
    if (selAll) selAll.addEventListener("change", function () {
      S.items.forEach(function (c) { if (selAll.checked) S.selected.add(c.id); else S.selected.delete(c.id); });
      renderRows();
    });
    if (workFilter) workFilter.addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-work]"); if (!b) return;
      S.work_id = b.dataset.work || "";
      load(true).catch(function (e) { NV.toast(e.message, "error"); });
    });
    if (inspector) inspector.addEventListener("click", function (ev) {
      var lb = ev.target.closest("button[data-latin]");
      if (lb) { fillLatin(lb.dataset.latin); return; }
      var b = ev.target.closest("button[data-act]"); if (!b) return;
      if (b.dataset.act === "save-edit") saveEdit().catch(function (e) { NV.toast(e.message, "error"); });
      else if (b.dataset.act === "single-confirm") singleCommit().catch(function (e) { NV.toast(e.message, "error"); });
    });
    if (searchInput) searchInput.addEventListener("input", debounce(function () {
      S.query = searchInput.value.trim(); load(true).catch(function (e) { NV.toast(e.message, "error"); });
    }, 250));
    if (statusFilter) statusFilter.addEventListener("change", function () {
      S.status = statusFilter.value; load(true).catch(function (e) { NV.toast(e.message, "error"); });
    });
    if (sortSelect) sortSelect.addEventListener("change", function () {
      S.sort = sortSelect.value; load(true).catch(function (e) { NV.toast(e.message, "error"); });
    });
    var pager = $("#page-numbers");
    if (pager) pager.addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-page]"); if (!b) return;
      S.page_no = parseInt(b.dataset.page, 10) || 1;
      load().catch(function (e) { NV.toast(e.message, "error"); });
    });

    rebind($("#open-commit-modal-btn"), function () { return openCommitModal(); });
    rebind($("#btn-build-project-cache"), buildProjectCache);
    rebind($("#btn-rebuild-cache"), rebuildCache);
    rebind($("#cancel-modal-btn"), function () { modal.classList.add("hidden"); modal.classList.remove("flex"); });
    rebind($("#confirm-commit-btn"), commit);
    rebind(btn(/Tạo lại gợi ý/), generate);
    rebind(btn(/Xuất gợi ý/), exportSuggestions);
    rebind(btn(/Xóa bộ lọc/), clearFilter);
    rebind($("#btn-approve-selected"), function () { return setStatus(Array.from(S.selected), "approved"); });
    rebind($("#btn-reject-selected"), function () { return setStatus(Array.from(S.selected), "rejected"); });
    rebind($("#btn-conflict-only"), function () { S.conflictOnly = !S.conflictOnly; paintConflict(); return load(true); });
    rebind($("#page-prev"), function () { if (S.page > 1) { S.page_no = S.page - 1; return load(); } });
    rebind($("#page-next"), function () { if (S.page < S.pages) { S.page_no = S.page + 1; return load(); } });


    // ---- xem danh sách tên trong moegirl_wikitext_names_bgm.json ----
    function showNamesBgm(r) {
      var data = r.data || {};
      var text = JSON.stringify(data, null, 1);
      var keys = Object.keys(data);
      var filled = keys.filter(function (k) { return data[k]; }).length;
      var old = $("#nv-bgm-modal"); if (old) old.remove();
      var m = document.createElement("div");
      m.id = "nv-bgm-modal";
      m.style.cssText = "position:fixed;inset:0;z-index:99998;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;font:13px/1.4 system-ui,sans-serif";
      m.innerHTML =
        '<div style="background:#1b1b1f;color:#e5e1e6;width:min(1100px,94vw);max-height:90vh;display:flex;flex-direction:column;border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,.5)">' +
        '<div style="padding:12px 16px;display:flex;gap:12px;align-items:center;border-bottom:1px solid #444;flex-wrap:wrap">' +
          '<b style="font-size:15px">Tên nhân vật (names_bgm)</b>' +
          '<span style="opacity:.75">' + keys.length + ' trang · có tên ' + filled + ' · null ' + (keys.length - filled) + '</span>' +
          '<input id="nv-bgm-q" placeholder="Lọc theo tên / kana / latin" style="margin-left:auto;padding:4px 8px;border-radius:4px;border:1px solid #555;background:#111;color:inherit;width:240px">' +
          '<label style="display:flex;gap:4px;align-items:center"><input type="checkbox" id="nv-bgm-null"> chỉ null</label>' +
          '<button data-v="table" style="padding:4px 10px;border-radius:4px;background:#3a3a44;color:inherit">Bảng</button>' +
          '<button data-v="json" style="padding:4px 10px;border-radius:4px;background:#3a3a44;color:inherit">JSON</button>' +
          '<button data-v="dl" style="padding:4px 10px;border-radius:4px;background:#3b5bdb;color:#fff">Tải .json</button>' +
          '<button data-v="close" style="padding:4px 10px;border-radius:4px;background:#3a3a44;color:inherit">Đóng</button>' +
        '</div>' +
        '<div style="padding:4px 16px;opacity:.6;font-size:12px">' + esc(r.path || "") + '</div>' +
        '<div id="nv-bgm-body" style="overflow:auto;padding:0 16px 16px"></div></div>';
      document.body.appendChild(m);
      var body = $("#nv-bgm-body", m), q = $("#nv-bgm-q", m), onlyNull = $("#nv-bgm-null", m), view = "table";
      function paint() {
        var f = q.value.trim().toLowerCase();
        if (view === "json") {
          body.innerHTML = '<pre style="white-space:pre-wrap;font:12px/1.45 ui-monospace,Consolas,monospace;margin:12px 0">' + esc(text) + '</pre>';
          return;
        }
        var rows = keys.filter(function (k) {
          var v = data[k];
          if (onlyNull.checked && v) return false;
          if (!f) return true;
          return (k + " " + (v ? (v.kana || "") + " " + (v.latin || "") : "")).toLowerCase().indexOf(f) >= 0;
        });
        var td = 'style="padding:4px 8px"';
        body.innerHTML = '<table style="width:100%;border-collapse:collapse;margin-top:8px"><thead><tr style="text-align:left;position:sticky;top:0;background:#1b1b1f">' +
          ["Trang Moegirl", "Kana", "Latin", "Nguồn", "Bangumi", "Ghi chú"].map(function (h) { return '<th style="padding:6px 8px;border-bottom:1px solid #444">' + h + '</th>'; }).join("") +
          '</tr></thead><tbody>' + rows.slice(0, 2000).map(function (k) {
            var v = data[k], b = v && v.bangumi;
            if (!v) return '<tr style="opacity:.6"><td ' + td + '>' + esc(k) + '</td><td colspan="5" ' + td + '>null</td></tr>';
            var notes = [];
            if (v.latin_differs || (b && b.latin_differs)) notes.push("⚠ Latin lệch kana");
            if (v.id_fallback || (b && b.id_fallback)) notes.push("⚠ id dự phòng");
            var bid = v.bgm_id || (b && b.bgm_id) || "", bname = v.bgm_name || (b && b.bgm_name) || "";
            return '<tr style="border-bottom:1px solid #2c2c33"><td ' + td + '>' + esc(k) + '</td><td ' + td + '>' + esc(v.kana) +
              '</td><td ' + td + '>' + esc(v.latin) + '</td><td ' + td + '>' + esc(v.source) +
              '</td><td ' + td + '>' + (bid ? esc(bid) + " " + esc(bname) : "") + '</td><td style="padding:4px 8px;color:#f5c26b">' + notes.join(", ") + '</td></tr>';
          }).join("") + '</tbody></table>' + (rows.length > 2000 ? '<div style="opacity:.7;padding:8px">Hiển thị 2000/' + rows.length + ' dòng, hãy lọc thêm.</div>' : "");
      }
      m.addEventListener("click", function (ev) {
        if (ev.target === m) { m.remove(); return; }
        var b = ev.target.closest("button[data-v]"); if (!b) return;
        var v = b.dataset.v;
        if (v === "close") m.remove();
        else if (v === "dl") download("moegirl_wikitext_names_bgm.json", text, "application/json;charset=utf-8");
        else { view = v; paint(); }
      });
      q.addEventListener("input", paint); onlyNull.addEventListener("change", paint);
      paint();
    }
    function viewNamesBgm() {
      return NV.call("moegirl.names_bgm_view", {}).then(showNamesBgm);
    }
    (function installViewButton() {
      var ex = btn(/Xuất gợi ý/), nb;
      if (ex) {
        nb = ex.cloneNode(true);
        nb.removeAttribute("onclick"); nb.id = "btn-names-bgm";
        nb.textContent = "Xem tên (names_bgm)";
        ex.parentNode.insertBefore(nb, ex.nextSibling);
      } else {
        nb = document.createElement("button");
        nb.id = "btn-names-bgm"; nb.textContent = "Xem tên (names_bgm)";
        nb.style.cssText = "position:fixed;right:16px;top:12px;z-index:9999;padding:6px 12px;border-radius:6px;background:#3b5bdb;color:#fff";
        document.body.appendChild(nb);
      }
      rebind(nb, viewNamesBgm);
    })();
    load(true).catch(function (e) { NV.toast(e.message || String(e), "error"); });
    renderCacheInfo();
  }

  function pageH() {
    var data = { characters: [], rows: [], revision: "" }, selected = "", selectedKey = "", page = 1;
    var rules = Object.create(null), dirty = false, size = 200, loaded = false, analyzing = false, savingForward = false;
    var savedAddressPairs = [];
    var savedRows = Object.create(null);
    var status = $("#footer-save-status"), info = $("#footer-storage-info");
    var fileLabel = $('label[for="file-input"]');
    if (fileLabel) fileLabel.remove();
    $("#file-input").remove(); $("#btn-sample-data").remove();
    $("#btn-export").textContent = "Lưu các dòng đã chốt";
    $("#btn-banner-export").remove();
    $("#storage-alert-banner").remove();
    $(".module-sub").textContent = "(dữ liệu dự án)";
    $("th:nth-child(7)", $(".data-table")).textContent = "Ghi chú / gọi tên";
    $("#filter-status option[value=default]").textContent = "Gợi ý chưa chốt";
    function setDirty(value) {
      dirty = value;
      status.className = value ? "status-err" : "status-ok";
      status.textContent = value ? "Có thay đổi chưa lưu" : "Đã đồng bộ";
    }
    function notice(messages) {
      var old = $("#nv-xungho-notice"); if (old) old.remove();
      if (!messages.length) return;
      var el = document.createElement("div"); el.id = "nv-xungho-notice";
      el.style.cssText = "background:#78350f;color:white;padding:8px 12px;";
      el.textContent = messages.join(" "); $(".toolbar").after(el);
    }
    function rememberSavedRows(rows) {
      var seenPairs = new Set();
      savedRows = Object.create(null);
      savedAddressPairs = [];
      rows.forEach(function (r) {
        savedRows[r.key] = {self:r.self, you:r.you, name:r.name};
        var self = String(r.self || "").trim(), you = String(r.you || "").trim();
        if (!self || !you) return;
        var key = JSON.stringify([self, you]);
        if (!seenPairs.has(key)) {
          seenPairs.add(key);
          savedAddressPairs.push({self:self, you:you});
        }
      });
      savedAddressPairs.sort(function (a, b) {
        return (a.self + " – " + a.you).localeCompare(b.self + " – " + b.you, "vi");
      });
    }
    function load(next) {
      if (!next.source) throw new Error("Backend không trả về đường dẫn dự án; hãy khởi động lại ứng dụng.");
      data = next; selected = ""; selectedKey = ""; page = 1;
      rememberSavedRows(data.rows);
      data.rows.forEach(function (r) { r.status = (r.self || r.you || r.name) ? "locked" : "empty"; });
      loaded = true;
      reviewButton.disabled = false;
      $("#btn-run-entities").disabled = false;
      generate.disabled = false;
      $("#btn-export").disabled = false;
      setDirty(false); notice(data.warnings || []); render();
      if ($("#rules-panel").classList.contains("open")) renderSummary();
    }
    function fetchData() {
      loaded = false;
      reviewButton.disabled = true;
      $("#btn-run-entities").disabled = true;
      generate.disabled = true;
      $("#btn-export").disabled = true;
      status.className = "status-err";
      status.textContent = "Đang tải dữ liệu";
      return NV.call("xungho.read").then(load).catch(function (e) {
        status.textContent = "Không tải được dữ liệu";
        notice([e.message || String(e)]);
        throw e;
      });
    }
    var reviewButton = document.createElement("button");
    reviewButton.className = "btn btn-primary"; reviewButton.textContent = "Phân tích & duyệt nhân vật";
    reviewButton.disabled = true;
    $("#btn-toggle-rules").before(reviewButton);
    reviewButton.onclick = function () {
      if (!loaded) return;
      if (dirty && !confirm("Các thay đổi xưng hô chưa lưu sẽ bị bỏ khi cập nhật dữ liệu. Tiếp tục?")) return;
      openReview();
    };
    $("#btn-run-entities").onclick = function () {
      if (!loaded) return;
      if (dirty && !confirm("Các thay đổi xưng hô chưa lưu sẽ bị bỏ sau khi cập nhật. Tiếp tục?")) return;
      var button = this;
      button.disabled = true;
      NV.call("xungho.review", {source:data.source}).then(function (review) {
        var report = review.report || (review.reports.length === 1 ? review.reports[0] : "");
        if (report) return report;
        return NV.pickFile("md").then(function (picked) {
          if (!picked || !picked.ok) {
            if (picked && !picked.cancelled) throw new Error(picked.error || "Không chọn được báo cáo.");
            return "";
          }
          return picked.path;
        });
      }).then(function (report) {
        if (!report || !confirm("Chạy entities với báo cáo sau?\n" + report +
            "\n\nDữ liệu nhân vật và quan hệ sẽ được cập nhật; các quyết định trong entities.xlsx được giữ theo khóa.")) return;
        loaded = false;
        analyzing = true;
        reload.disabled = true;
        reviewButton.disabled = true;
        generate.disabled = true;
        $("#btn-export").disabled = true;
        button.textContent = "Đang chạy entities…";
        status.className = "status-err";
        status.textContent = "Đang phân tích báo cáo; vui lòng chờ";
        return NV.call("xungho.analyze", {source:data.source, report:report}).then(function (result) {
          return fetchData().then(function () {
            NV.toast("Đã chạy entities: " + result.chapters + " chương, " + result.characters + " nhân vật. Hãy duyệt nhân vật rồi tạo lại bảng xưng hô.", "ok");
          });
        }).catch(function (e) {
          return fetchData().catch(function () {}).then(function () { throw e; });
        });
      }).catch(function (e) { NV.toast(e.message || String(e), "error"); })
        .finally(function () { analyzing = false; reload.disabled = false; button.textContent = "Chạy entities"; button.disabled = !loaded; });
    };
    function openReview() {
      var overlay = document.createElement("div");
      overlay.style.cssText = "position:fixed;inset:0;z-index:10000;background:#0c0d10;display:flex;flex-direction:column;padding:16px;gap:10px;color:#e2e4e9;overflow:auto";
      overlay.innerHTML = '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><strong>Phân tích & duyệt nhân vật</strong>' +
        '<input id="h-report" class="filter-select" style="flex:1;min-width:220px" placeholder="Đường dẫn báo cáo AI Reader .md">' +
        '<button class="btn" id="h-pick">Chọn báo cáo</button><button class="btn btn-primary" id="h-analyze">Phân tích báo cáo</button>' +
        '<button class="btn" id="h-close">Đóng</button></div>' +
        '<div id="h-summary"></div><div style="color:#fcd34d">Chỉ chọn các cách gọi bạn đã kiểm tra. Gộp mơ hồ cần lưu quyết định rồi phân tích lại. Sau khi duyệt, tạo lại bảng xưng hô.</div>' +
        '<div style="display:flex;gap:8px;align-items:center"><input id="h-search" class="filter-select" placeholder="Tìm nhân vật" style="width:240px">' +
        '<select id="h-filter" class="filter-select"><option value="pending">Chưa duyệt</option><option value="all">Tất cả</option><option value="approved">Đã có trong thuật ngữ</option></select>' +
        '<button class="btn" id="h-ambiguous">Tên mơ hồ</button></div>' +
        '<div style="display:flex;gap:12px;min-height:320px;flex:1"><div id="h-list" style="width:300px;overflow:auto;border:1px solid #363b47"></div>' +
        '<div id="h-detail" style="flex:1;overflow:auto;border:1px solid #363b47;padding:12px"></div></div>';
      document.body.appendChild(overlay);
      var current = null, chosen = "", mode = "characters";
      function fetchReview() {
        return NV.call("xungho.review", {source:data.source}).then(function (r) {
          current = r;
          if (!$("#h-report", overlay).value) $("#h-report", overlay).value = r.report || (r.reports.length === 1 ? r.reports[0] : "");
          chosen = ""; paint();
        });
      }
      function paint() {
        $("#h-summary", overlay).textContent = current.chapters + " chương · " + current.characters.length +
          " nhân vật · " + current.ambiguous.length + " tên mơ hồ";
        var q = $("#h-search", overlay).value.toLowerCase(), filter = $("#h-filter", overlay).value;
        var list = $("#h-list", overlay), detail = $("#h-detail", overlay);
        if (mode === "ambiguous") {
          list.innerHTML = current.ambiguous.filter(function (a) { return String(a["Cách gọi"]).toLowerCase().indexOf(q) >= 0; })
            .map(function (a) { return '<button class="btn" style="display:block;width:100%;text-align:left" data-name="' + esc(a["Cách gọi"]) + '">' + esc(a["Cách gọi"]) + ' · ' + esc(a["Số lần"]) + '</button>'; }).join("");
          var amb = current.ambiguous.find(function (a) { return a["Cách gọi"] === chosen; });
          detail.innerHTML = amb ? '<h3>' + esc(amb["Cách gọi"]) + '</h3><p>Chọn đúng nhân vật sau khi đối chiếu; chưa chắc thì để trống.</p>' +
            '<select id="h-choice" class="filter-select">' + String(amb["Ứng viên"] || "").split(" | ").map(function (v) { return '<option value="' + esc(v) + '">' + esc(v) + '</option>'; }).join("") +
            '</select> <button class="btn btn-primary" id="h-save-choice">Lưu quyết định</button>' : "Chọn một tên mơ hồ.";
          var saveChoice = $("#h-save-choice", overlay);
          if (saveChoice) saveChoice.onclick = function () {
            NV.call("xungho.decision", {source:data.source, revision:current.revision, name:chosen, choice:$("#h-choice", overlay).value})
              .then(function (r) { current = r; NV.toast("Đã lưu. Hãy phân tích lại để áp dụng.", "ok"); })
              .catch(function (e) { NV.toast(e.message, "error"); });
          };
          return;
        }
        var visible = current.characters.filter(function (c) {
          return (c.name + " " + c.dest + " " + c.calls.map(function (v) { return v.name; }).join(" ")).toLowerCase().indexOf(q) >= 0 &&
            (filter === "all" || (filter === "approved" ? c.approved : !c.approved));
        });
        list.innerHTML = visible.map(function (c) {
          return '<button class="btn" style="display:block;width:100%;text-align:left;white-space:normal" data-name="' + esc(c.key) + '">' +
            esc(c.name) + ' · ' + c.chapters + ' chương · ' + (c.approved ? "đã duyệt" : "chưa duyệt") + '</button>';
        }).join("");
        var c = current.characters.find(function (v) { return v.key === chosen; });
        if (!c) { detail.textContent = "Chọn một nhân vật để xem bằng chứng và duyệt."; return; }
        detail.innerHTML = '<h3>' + esc(c.name) + ' · ' + esc(c.match) + '</h3><p>Giới tính nguồn: ' + esc(c.gender_source) +
          ' · ' + esc(c.chapters) + ' chương' + (c.approved ? ' · đã có trong thuật ngữ' : '') + (c.conflict ? ' · ' + esc(c.conflict) : '') + '</p><p>' + esc(c.descriptions.join(" | ")) +
          '</p><p>Bí danh AI Reader chưa duyệt: ' + esc(c.reader_aliases.join("、") || "không có") + '</p>' +
          '<p style="margin-top:12px">Tên dịch <input id="h-dest" class="filter-select" value="' + esc(c.dest) + '"> ' +
          'Giới tính <select id="h-gender" class="filter-select"><option value="">Chưa rõ</option><option value="male">Nam</option><option value="female">Nữ</option><option value="other">Khác</option></select></p>' +
          '<p style="margin-top:12px">Chọn cách gọi đã xác nhận:</p>' + c.calls.map(function (call) {
            return '<label style="display:block;padding:4px"><input type="checkbox" data-call value="' + esc(call.name) + '"> ' +
              esc(call.name) + ' · mức ' + esc(call.level) + ' · ' + call.count + ' lần</label>';
          }).join("") + '<button id="h-approve" class="btn btn-emerald">Duyệt vào glossary</button>';
        $("#h-gender", overlay).value = c.gender || "";
        $("#h-approve", overlay).onclick = function () {
          var sources = $$("input[data-call]:checked", overlay).map(function (el) { return el.value; });
          var destination = $("#h-dest", overlay).value.trim(), gender = $("#h-gender", overlay).value;
          if (!destination || !sources.length) { NV.toast("Nhập tên đích và chọn ít nhất một cách gọi.", "error"); return; }
          if (!confirm("Duyệt " + c.name + " → " + destination + " với " + sources.length + " cách gọi? Glossary sẽ được sao lưu và cập nhật.")) return;
          NV.call("xungho.approve_character", {source:data.source, revision:current.revision, key:c.key, destination:destination, gender:gender, sources:sources})
            .then(function (r) { current = r.review; paint(); NV.toast("Đã duyệt vào glossary. Hãy tạo lại bảng xưng hô.", "ok"); })
            .catch(function (e) { NV.toast(e.message, "error"); });
        };
      }
      $("#h-list", overlay).onclick = function (ev) { var el = ev.target.closest("[data-name]"); if (el) { chosen = el.dataset.name; paint(); } };
      $("#h-search", overlay).oninput = paint;
      $("#h-filter", overlay).onchange = paint;
      $("#h-ambiguous", overlay).onclick = function () { mode = mode === "characters" ? "ambiguous" : "characters"; this.textContent = mode === "characters" ? "Tên mơ hồ" : "Nhân vật"; chosen = ""; paint(); };
      $("#h-pick", overlay).onclick = function () {
        NV.pickFile("md").then(function (r) { if (r && r.ok) $("#h-report", overlay).value = r.path; })
          .catch(function (e) { NV.toast(e.message, "error"); });
      };
      $("#h-analyze", overlay).onclick = function () {
        var report = $("#h-report", overlay).value.trim(), button = this;
        if (!report) { NV.toast("Chọn báo cáo AI Reader.", "error"); return; }
        if (!confirm("Phân tích lại báo cáo? Quyết định trong entities.xlsx sẽ được giữ theo khóa.")) return;
        button.disabled = true; button.textContent = "Đang phân tích…";
        NV.call("xungho.analyze", {source:data.source, report:report}).then(function (r) {
          NV.toast("Đã phân tích " + r.chapters + " chương.", "ok"); return fetchReview();
        }).catch(function (e) { NV.toast(e.message, "error"); })
          .finally(function () { button.disabled = false; button.textContent = "Phân tích báo cáo"; });
      };
      $("#h-close", overlay).onclick = function () { overlay.remove(); fetchData().catch(function (e) { NV.toast(e.message, "error"); }); };
      fetchReview().catch(function (e) { overlay.remove(); NV.toast(e.message, "error"); });
    }
    var reload = document.createElement("button");
    reload.className = "btn"; reload.textContent = "Tải lại";
    $("#btn-toggle-rules").before(reload);
    reload.onclick = function () {
      if (dirty && !confirm("Bỏ các thay đổi chưa lưu và tải lại?")) return;
      fetchData().catch(function (e) { NV.toast(e.message, "error"); });
    };
    var generate = document.createElement("button");
    generate.className = "btn"; generate.textContent = "Tạo lại bảng";
    generate.disabled = true;
    reload.before(generate);
    generate.onclick = function () {
      if (!loaded) return;
      if (!confirm("Tạo lại bảng từ entities.json và glossary? Các dòng đã lưu sẽ được giữ theo khóa; thay đổi chưa lưu sẽ bị bỏ.")) return;
      generate.disabled = true;
      NV.call("xungho.generate", {source:data.source}).then(function (r) { load(r.data); NV.toast("Đã tạo lại bảng.", "ok"); })
        .catch(function (e) { NV.toast(e.message, "error"); }).finally(function () { generate.disabled = false; });
    };
    function render() {
      var chars = Object.create(null), counts = { empty: 0, "default": 0, locked: 0 };
      data.characters.forEach(function (c) { chars[c.id] = c; });
      data.rows.forEach(function (r) { counts[r.status]++; });
      ["empty", "default", "locked"].forEach(function (k) { $("#stat-" + k).textContent = counts[k]; });
      info.textContent = data.rows.length + " dòng · " + (data.waiting || 0) + " mục chờ duyệt";
      var query = $("#input-search-char").value.toLowerCase();
      $("#char-list-container").innerHTML =
        '<div class="char-item ' + (!selected ? "active" : "") + '" data-id="">Tất cả</div>' +
        data.characters.filter(function (c) { return (c.name + " " + c.id).toLowerCase().indexOf(query) !== -1; })
          .map(function (c) { return '<div class="char-item ' + (selected === c.id ? "active" : "") + '" data-id="' + esc(c.id) + '">' + esc(c.name) + ' <span class="badge-mono">' + esc(c.gender || "?") + '</span></div>'; }).join("");
      var filtered = data.rows.filter(function (r) {
        return (!selected || r.speaker === selected) && ($("#filter-status").value === "all" || $("#filter-status").value === r.status);
      });
      var pages = Math.max(1, Math.ceil(filtered.length / size)); page = Math.max(1, Math.min(page, pages));
      var shown = filtered.slice((page - 1) * size, page * size);
      $("#row-count-display").textContent = "Hiển thị " + shown.length + " / " + filtered.length + " dòng";
      ["top", "bottom"].forEach(function (pos) {
        $("#pagination-info-" + pos).textContent = "Trang " + page + " / " + pages;
        $("#btn-prev-page-" + pos).disabled = page === 1;
        $("#btn-next-page-" + pos).disabled = page === pages;
      });
      $("#table-body").innerHTML = shown.map(function (r) {
        var inputs = ["self", "you", "name"].map(function (field) {
          return '<td><input class="table-input" data-field="' + field + '" value="' + esc(r[field]) + '"></td>';
        }).join("");
        return '<tr data-key="' + esc(r.key) + '" class="' + (selectedKey === r.key ? "selected" : "") + '">' +
          '<td>' + esc((chars[r.speaker] || {}).name || r.speaker) + '</td><td>' + esc((chars[r.listener] || {}).name || r.listener) +
          '</td><td>' + esc(r.from) + ' – ' + esc(r.to) + '</td><td>' + esc(r.group) + '</td>' + inputs +
          '<td><span class="tag-status ' + esc(r.status) + '">' + esc({empty:"trống", "default":"gợi ý", locked:"đã chốt"}[r.status]) +
          '</span></td><td><button class="btn btn-sm" data-lock>Chốt</button></td></tr>';
      }).join("") || '<tr><td colspan="9" class="text-center">Không có dữ liệu. Kiểm tra cảnh báo hoặc tạo lại bảng.</td></tr>';
      renderEvidence();
    }
    function renderEvidence() {
      var row = data.rows.find(function (r) { return r.key === selectedKey; });
      $("#evidence-container").innerHTML = row
        ? '<div class="evidence-card"><label for="h-saved-address">Điền nhanh xưng hô đã lưu</label>' +
          '<select id="h-saved-address" class="filter-select" style="width:100%;margin-top:6px" ' +
          (savedAddressPairs.length ? '' : 'disabled') + '><option value="">' +
          (savedAddressPairs.length ? 'Chọn cách xưng hô' : 'Chưa có cặp xưng hô đã lưu') + '</option>' +
          savedAddressPairs.map(function (pair, index) {
            return '<option value="' + index + '">' + esc(pair.self + " – " + pair.you) + '</option>';
          }).join("") + '</select><p style="color:var(--text-muted)">Tự xưng – gọi người nghe; không thay đổi ghi chú.</p>' +
          '<button type="button" class="btn btn-primary" data-address-all' + (row.self && row.you ? '' : ' disabled') +
          '>Áp dụng cho các giai đoạn trống</button> ' +
          '<button type="button" class="btn btn-primary" data-address-forward' + (row.self && row.you ? '' : ' disabled') +
          '>Lưu tất cả từ đây về sau</button><p style="color:var(--text-muted)">Cùng chiều người nói → người nghe. Nút giai đoạn trống cần bấm Lưu các dòng đã chốt; nút lưu từ đây ghi ngay và thay cả xưng hô đã có. Không tạo giai đoạn mới.</p></div>' +
          '<div class="evidence-card"><strong>' + esc(row.group) + '</strong><p>' + esc(row.relations) + '</p></div><div class="evidence-quote-item">' + esc(row.evidence || "Không có trích dẫn.") + '</div>'
        : "Chọn một dòng để xem bằng chứng và điền nhanh xưng hô.";
    }
    $("#evidence-container").onchange = function (ev) {
      if (ev.target.id !== "h-saved-address" || ev.target.value === "") return;
      var pair = savedAddressPairs[Number(ev.target.value)];
      var row = data.rows.find(function (r) { return r.key === selectedKey; });
      if (!pair || !row) return;
      if ((row.self || row.you) && (row.self !== pair.self || row.you !== pair.you) &&
          !confirm("Thay cách xưng hô hiện tại của dòng này bằng “" + pair.self + " – " + pair.you + "”?")) {
        ev.target.value = ""; return;
      }
      row.self = pair.self; row.you = pair.you; row.status = "locked";
      setDirty(true); render();
    };
    $("#evidence-container").onclick = function (ev) {
      var forward = ev.target.closest("[data-address-forward]");
      if (!forward && !ev.target.closest("[data-address-all]")) return;
      if (savingForward) return;
      var source = data.rows.find(function (r) { return r.key === selectedKey; });
      if (!source || !source.self || !source.you) {
        NV.toast("Chọn dòng có đủ tự xưng và cách gọi người nghe.", "error"); return;
      }
      if (forward) {
        var start = Number(source.from);
        if (!Number.isInteger(start) || start < 1) {
          NV.toast("Chương bắt đầu của dòng không hợp lệ; hãy tải lại.", "error"); return;
        }
        var later = data.rows.filter(function (r) {
          return r.speaker === source.speaker && r.listener === source.listener && Number(r.from) >= start;
        });
        if (!confirm("Lưu ngay “" + source.self + " – " + source.you + "” cho " + later.length +
            " giai đoạn từ chương báo cáo " + start + " về sau của chiều này? Cách xưng hô đã có sẽ bị ghi đè; ghi chú và chiều ngược lại không đổi.")) return;
        var updates = later.map(function (r) {
          return {key:r.key, self:source.self, you:source.you, name:savedRows[r.key].name};
        });
        savingForward = true;
        document.body.inert = true;
        status.className = "status-err";
        status.textContent = "Đang lưu xưng hô";
        NV.call("xungho.save", {source:data.source, revision:data.revision, updates:updates}).then(function (result) {
          var next = result.data, changed = new Set(updates.map(function (item) { return item.key; }));
          rememberSavedRows(next.rows);
          data.rows.forEach(function (row) {
            if (changed.has(row.key)) {
              row.self = source.self; row.you = source.you; row.status = "locked";
              delete row.suggestionGroup;
            }
          });
          data.revision = next.revision; data.summary = next.summary; data.warnings = next.warnings;
          data.waiting = next.waiting; data.orphans = next.orphans; data.ready = next.ready;
          setDirty(data.rows.some(function (row) {
            var saved = savedRows[row.key];
            return !saved || row.status === "default" ||
              row.self !== saved.self || row.you !== saved.you || row.name !== saved.name;
          }));
          notice(data.warnings || []); render();
          if ($("#rules-panel").classList.contains("open")) renderSummary();
          NV.toast("Đã lưu " + later.length + " giai đoạn từ chương " + start + " về sau.", "ok");
        }).catch(function (e) {
          setDirty(dirty); NV.toast(e.message || String(e), "error");
        }).finally(function () { savingForward = false; document.body.inert = false; });
        return;
      }
      var targets = data.rows.filter(function (r) {
        return r.speaker === source.speaker && r.listener === source.listener &&
          r.status === "empty" && !r.self && !r.you && !r.name;
      });
      if (!targets.length) { NV.toast("Không có giai đoạn trống cho chiều này."); return; }
      if (!confirm("Điền “" + source.self + " – " + source.you + "” vào " + targets.length +
          " giai đoạn trống của cùng chiều người nói → người nghe? Các dòng khác không thay đổi.")) return;
      targets.forEach(function (r) { r.self = source.self; r.you = source.you; r.status = "locked"; });
      setDirty(true); render();
      if ($("#rules-panel").classList.contains("open")) renderSummary();
      NV.toast("Đã điền " + targets.length + " giai đoạn. Bấm Lưu các dòng đã chốt để ghi vào dự án.", "ok");
    };
    $("#char-list-container").onclick = function (ev) { var el = ev.target.closest("[data-id]"); if (el) { selected = el.dataset.id; page = 1; render(); } };
    $("#input-search-char").oninput = render;
    $("#filter-status").onchange = function () { page = 1; render(); };
    $$('[data-action="prev-page"]').forEach(function (b) { b.onclick = function () { page--; render(); }; });
    $$('[data-action="next-page"]').forEach(function (b) { b.onclick = function () { page++; render(); }; });
    $("#table-body").onchange = function (ev) {
      var tr = ev.target.closest("tr[data-key]"), field = ev.target.dataset.field;
      if (!tr || !field) return;
      var row = data.rows.find(function (r) { return r.key === tr.dataset.key; });
      row[field] = ev.target.value.trim(); row.status = "locked"; setDirty(true); render();
    };
    $("#table-body").onclick = function (ev) {
      var tr = ev.target.closest("tr[data-key]"); if (!tr) return;
      selectedKey = tr.dataset.key;
      if (ev.target.closest("[data-lock]")) {
        var row = data.rows.find(function (r) { return r.key === selectedKey; });
        row.status = "locked"; setDirty(true);
      }
      if (ev.target.tagName !== "INPUT") render(); else renderEvidence();
    };
    var summaryGroups = [];
    function groupKey(type, group, speakerGender, listenerGender, sourceRole, targetRole, origin) {
      return JSON.stringify([type || "", group || "", speakerGender || "", listenerGender || "", sourceRole || "", targetRole || "", origin || ""]);
    }
    function genderLabel(gender) {
      return {male:"Nam", female:"Nữ", other:"Khác", "":"Chưa rõ"}[gender] || "Chưa rõ";
    }
    function renderSummary() {
      var rows = Object.create(null);
      data.rows.forEach(function (row) { rows[row.key] = row; });
      summaryGroups = (data.summary || []).map(function (item) {
        return {key:groupKey(item.type, item.group, item.speaker_gender, item.listener_gender, item.source_role, item.target_role, item.role_origin),
          type:item.type, group:item.group, speaker:item.speaker_gender, listener:item.listener_gender,
          sourceRole:item.source_role, targetRole:item.target_role, roleLabel:item.role_label, roleOrigin:item.role_origin,
          observations:item.observations, pairs:item.pairs || [],
          rows:(item.row_keys || []).map(function (key) { return rows[key]; }).filter(Boolean)};
      });
      $("#rules-summary").innerHTML = summaryGroups.length ? '<table class="data-table"><thead><tr><th>Nhãn báo cáo / bằng chứng</th><th>Nhân vật A</th><th>Nhân vật B</th><th>Báo cáo / bảng A → B</th><th>Tự xưng</th><th>Gọi</th><th>Ghi chú / gọi tên</th><th>Thao tác</th></tr></thead><tbody>' +
        summaryGroups.map(function (g, index) {
          var rule = rules[g.key] || {self:"", you:"", name:""};
          var fields = ["self", "you", "name"].map(function (field) {
            return '<td><input class="table-input" data-rule-field="' + field + '" value="' + esc(rule[field]) + '"></td>';
          }).join("");
          var evidence = g.pairs.map(function (pair, pairIndex) {
            var roles = [
              ["older_male|younger_male", "Anh trai – em trai"], ["younger_male|older_male", "Em trai – anh trai"],
              ["older_male|younger_female", "Anh trai – em gái"], ["younger_female|older_male", "Em gái – anh trai"],
              ["older_female|younger_male", "Chị gái – em trai"], ["younger_male|older_female", "Em trai – chị gái"],
              ["older_female|younger_female", "Chị gái – em gái"], ["younger_female|older_female", "Em gái – chị gái"]
            ];
            var rolePicker = g.roleOrigin ? '<div><select class="filter-select" data-role-select><option value="">Theo báo cáo / chưa rõ</option>' +
              roles.map(function (option) {
                var selected = g.roleOrigin === "manual" && option[0] === g.sourceRole + "|" + g.targetRole;
                return '<option value="' + option[0] + '"' + (selected ? ' selected' : '') + '>' + option[1] + '</option>';
              }).join("") + '</select> <button class="btn btn-sm" data-role-save="' + pairIndex + '">Lưu vai A → B</button></div>' : '';
            return '<li>' + esc(pair.source_name) + ' → ' + esc(pair.target_name) + ' · chương báo cáo: ' +
              esc(pair.chapters.join(", ")) + (pair.quotes.length ? '<br>' + esc(pair.quotes.join(" | ")) : '') + rolePicker + '</li>';
          }).join("");
          return '<tr data-group-index="' + index + '"><td>' + esc(g.type) + ' <span style="color:var(--text-muted)">(' + esc(g.group === "Chưa rõ (chỉ tương tác)" ? "Chưa phân nhóm" : g.group) +
            ')</span>' + (g.roleLabel ? '<br><strong>' + esc(g.roleLabel) + '</strong> <span style="color:var(--text-muted)">(' +
              (g.roleOrigin === "manual" ? 'đã duyệt' : g.roleOrigin === "report" ? 'theo nhãn và giới tính' : 'cần duyệt') + ')</span>' : '') +
            '<details><summary>' + g.pairs.length + ' cặp trong báo cáo</summary><ul>' + evidence + '</ul></details></td><td>' + esc(genderLabel(g.speaker)) +
            '</td><td>' + esc(genderLabel(g.listener)) + '</td><td>' + g.observations + ' / ' + g.rows.length + '</td>' + fields +
            '<td><button class="btn btn-sm" data-rule-action="suggest"' + (g.rows.length && g.roleOrigin !== "pending" ? "" : " disabled") + '>Gợi ý</button> ' +
            '<button class="btn btn-sm" data-rule-action="lock"' + (g.rows.length && g.roleOrigin !== "pending" ? "" : " disabled") + '>Chốt gợi ý</button></td></tr>';
        }).join("") + '</tbody></table><p style="color:var(--text-muted)">A → B là chiều ghi trong báo cáo, không xác định ai đang nói. Vai theo nhãn và giới tính chỉ là gợi ý: báo cáo có thể sai. Mở danh sách cặp để kiểm tra và lưu vai cho từng cặp; vai đã duyệt được lưu riêng. Báo cáo: số chương-cặp theo nhãn gốc; bảng: số dòng A → B có bằng chứng cùng nhãn. Quy tắc xưng hô chỉ ở phiên hiện tại cho đến khi chốt và lưu.</p>'
        : "Chưa có quan hệ. Hãy chạy entities và tạo lại bảng.";
    }
    $("#btn-toggle-rules").onclick = function () { $("#rules-panel").classList.toggle("open"); renderSummary(); };
    $("#rules-summary").oninput = function (ev) {
      var field = ev.target.dataset.ruleField, tr = ev.target.closest("tr[data-group-index]");
      if (!field || !tr) return;
      var group = summaryGroups[Number(tr.dataset.groupIndex)];
      if (!rules[group.key]) rules[group.key] = {self:"", you:"", name:""};
      rules[group.key][field] = ev.target.value;
    };
    $("#rules-summary").onclick = function (ev) {
      var roleButton = ev.target.closest("[data-role-save]");
      if (roleButton) {
        var roleRow = roleButton.closest("tr[data-group-index]");
        var roleGroup = summaryGroups[Number(roleRow.dataset.groupIndex)];
        var pair = roleGroup.pairs[Number(roleButton.dataset.roleSave)];
        var selection = roleButton.parentElement.querySelector("[data-role-select]").value.split("|");
        roleButton.disabled = true;
        NV.call("xungho.roles", {source:data.source, revision:data.revision, kind:roleGroup.type,
          a:pair.source, b:pair.target, a_role:selection[0], b_role:selection[1] || ""}).then(function (result) {
          data.summary = result.summary; data.revision = result.revision;
          notice(result.warnings || []); renderSummary();
          NV.toast("Đã lưu vai cho cặp nhân vật.", "ok");
        }).catch(function (e) { NV.toast(e.message || String(e), "error"); })
          .finally(function () { roleButton.disabled = false; });
        return;
      }
      var button = ev.target.closest("[data-rule-action]"), tr = ev.target.closest("tr[data-group-index]");
      if (!button || !tr) return;
      var group = summaryGroups[Number(tr.dataset.groupIndex)];
      if (button.dataset.ruleAction === "suggest") {
        var rule = rules[group.key] || {self:"", you:"", name:""};
        if (!(rule.self.trim() || rule.you.trim() || rule.name.trim())) {
          NV.toast("Nhập ít nhất một cách xưng hô hoặc ghi chú.", "error"); return;
        }
        var changed = 0;
        group.rows.forEach(function (row) {
          if (row.status === "locked") return;
          row.self = rule.self.trim(); row.you = rule.you.trim(); row.name = rule.name.trim();
          row.status = "default"; row.suggestionGroup = group.key; changed++;
        });
        if (changed) setDirty(true);
        render(); renderSummary();
        NV.toast("Đã tạo " + changed + " gợi ý; chưa áp dụng khi dịch.");
      } else {
        var pending = group.rows.filter(function (row) { return row.status === "default" && row.suggestionGroup === group.key; });
        if (!pending.length) { NV.toast("Nhóm này chưa có gợi ý để chốt."); return; }
        if (!confirm("Chốt " + pending.length + " gợi ý theo chiều A → B của nhãn " + group.type +
            " (" + (group.roleLabel || genderLabel(group.speaker) + " → " + genderLabel(group.listener)) +
            ")? Chiều báo cáo không xác định người nói. Các dòng đã chốt trước đó không thay đổi.")) return;
        pending.forEach(function (row) { row.status = "locked"; });
        setDirty(true); render(); renderSummary();
        NV.toast("Đã chốt " + pending.length + " dòng. Bấm Lưu để cập nhật quy tắc dịch.", "ok");
      }
    };
    $("#btn-export").onclick = function () {
      if (!loaded) return;
      var updates = data.rows.filter(function (r) { return r.status === "locked"; }).map(function (r) {
        return {key:r.key, self:r.self, you:r.you, name:r.name};
      });
      if (!confirm("Lưu các dòng đã chốt vào dự án? Các dòng gợi ý chưa chốt sẽ không được áp dụng.")) return;
      var button = $("#btn-export"); button.disabled = true;
      NV.call("xungho.save", {source:data.source, revision:data.revision, updates:updates}).then(function (r) { load(r.data); NV.toast("Đã lưu bảng và cập nhật quy tắc dịch.", "ok"); })
        .catch(function (e) { NV.toast(e.message, "error"); }).finally(function () { button.disabled = false; });
    };
    window.addEventListener("beforeunload", function (ev) { if (dirty || analyzing || savingForward) { ev.preventDefault(); ev.returnValue = ""; } });
    fetchData().catch(function (e) { NV.toast(e.message, "error"); });
  }

  // ------------------------------------------------------------------ khởi động
  var PAGES = { "tong-quan": pageA, "bao-cao": pageB, "nhan-vat": pageC, "moegirl": pageD, "glossary": pageE, "dich-tien-trinh": pageF, "nhat-ky": pageG, "xung-ho": pageH };
  var NEEDS_PROJECT = { "tong-quan": 1, "bao-cao": 1, "nhan-vat": 1, "moegirl": 1, "glossary": 1, "dich-tien-trinh": 1, "xung-ho": 1 };

  function boot() {
    installNav();
    NV.call("app.state").then(function (state) {
      var fn = PAGES[ROUTE];
      if (!state.project) {
        noProjectBanner(state);
        if (NEEDS_PROJECT[ROUTE]) return;
      }
      if (fn) fn(state);
    }).catch(function (e) { NV.toast("Không nối được backend: " + e.message, "error"); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
