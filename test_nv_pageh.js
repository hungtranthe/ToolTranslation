const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function setup(initialRows) {
  const elements = new Map();
  function element(selector) {
    if (!elements.has(selector)) {
      elements.set(selector, {
        value: selector === "#filter-status" ? "all" : "",
        innerHTML: "", textContent: "", disabled: false,
        classList: {contains: () => false},
        before() {}, after() {}, remove() {},
      });
    }
    return elements.get(selector);
  }
  const state = {
    source: "source.txt", revision: "r1", characters: [], warnings: [], summary: [],
    rows: JSON.parse(JSON.stringify(initialRows)),
  };
  const calls = [];
  let accept = true;
  let saveError = null;
  const context = {
    $: element, $$: () => [], Set, JSON, Number, String, Math, Object,
    document: {body: {inert: false}, createElement: () => ({before() {}, textContent: "", disabled: false})},
    window: {addEventListener() {}},
    confirm: () => accept,
    esc: value => String(value == null ? "" : value).replace(/[&<>"']/g, c =>
      ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c])),
    NV: {
      toast() {},
      call(method, params) {
        calls.push({method, params});
        if (method === "xungho.read") return Promise.resolve(JSON.parse(JSON.stringify(state)));
        if (method === "xungho.save") {
          if (saveError) return Promise.reject(new Error(saveError));
          for (const update of params.updates) {
            const target = state.rows.find(r => r.key === update.key);
            Object.assign(target, {self: update.self, you: update.you, name: update.name});
          }
          return Promise.resolve({data: JSON.parse(JSON.stringify(state))});
        }
        throw new Error("Unexpected API call: " + method);
      },
    },
  };
  const source = fs.readFileSync(require("node:path").join(__dirname, "nv_bindings.js"), "utf8");
  const start = source.indexOf("  function pageH() {");
  const end = source.indexOf("  // ------------------------------------------------------------------ khởi động", start);
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(source.slice(start, end) + "\npageH();", context);
  return {
    element, calls, setConfirm(value) { accept = value; }, failSave(value) { saveError = value; },
    body: context.document.body,
    selectRow(key, tag = "TD") {
      element("#table-body").onclick({
        target: {tagName: tag, closest: selector => selector === "tr[data-key]" ? {dataset: {key}} : null},
      });
    },
    selectPair(value) {
      const target = {id: "h-saved-address", value: String(value)};
      element("#evidence-container").onchange({target});
      return target;
    },
    applyAll() {
      element("#evidence-container").onclick({
        target: {closest: selector => selector === "[data-address-all]" ? {} : null},
      });
    },
    saveForward() {
      element("#evidence-container").onclick({
        target: {closest: selector => selector === "[data-address-forward]" ? {} : null},
      });
    },
    save() { element("#btn-export").onclick(); },
  };
}

function row(key, self = "", you = "", name = "") {
  return {key, speaker: "a", listener: "b", from: 1, to: 2, group: "Bạn bè",
    relations: "", evidence: "", self, you, name};
}

test("saved pairs are deduplicated and applied without changing notes or saving", async () => {
  const ui = setup([row("saved1", "tôi", "cậu"), row("saved2", "tôi", "cậu"),
    row("saved3", "anh", "em"), row("partial", "tôi", ""), row("target", "", "", "ghi chú")]);
  await new Promise(resolve => setImmediate(resolve));
  ui.selectRow("target", "INPUT");
  const html = ui.element("#evidence-container").innerHTML;
  assert.match(html, /anh – em/);
  assert.equal((html.match(/tôi – cậu/g) || []).length, 1);
  assert.doesNotMatch(html, /tôi – <\/option>/);
  ui.selectPair(0);
  assert.match(ui.element("#table-body").innerHTML, /value="anh"/);
  assert.match(ui.element("#table-body").innerHTML, /value="em"/);
  assert.match(ui.element("#table-body").innerHTML, /value="ghi chú"/);
  assert.equal(ui.element("#footer-save-status").textContent, "Có thay đổi chưa lưu");
  assert.deepEqual(ui.calls.map(call => call.method), ["xungho.read"]);
  ui.selectRow("partial");
  assert.doesNotMatch(ui.element("#evidence-container").innerHTML, /anh – cậu/);
});

test("replacement needs confirmation and empty saved pairs disable selection", async () => {
  const ui = setup([row("saved", "anh", "em"), row("target", "tôi", "cậu")]);
  await new Promise(resolve => setImmediate(resolve));
  ui.selectRow("target");
  ui.setConfirm(false);
  assert.equal(ui.selectPair(0).value, "");
  assert.equal(ui.element("#footer-save-status").textContent, "Đã đồng bộ");
  ui.setConfirm(true);
  ui.selectPair(0);
  assert.equal(ui.element("#footer-save-status").textContent, "Có thay đổi chưa lưu");
  const empty = setup([row("empty")]);
  await new Promise(resolve => setImmediate(resolve));
  empty.selectRow("empty");
  assert.match(empty.element("#evidence-container").innerHTML, /disabled><option value="">Chưa có cặp xưng hô đã lưu/);
});

test("apply to empty phases respects pair direction, existing notes and confirmation", async () => {
  const reverse = {...row("reverse"), speaker: "b", listener: "a"};
  const other = {...row("other"), speaker: "a", listener: "c"};
  const partial = row("partial", "tôi", "");
  const note = row("note", "", "", "ghi chú");
  const ui = setup([row("source", "anh", "em"), row("blank1"),
    row("blank2"), partial, note, row("existing", "tôi", "cậu"), reverse, other]);
  await new Promise(resolve => setImmediate(resolve));
  ui.selectRow("source");
  ui.setConfirm(false);
  ui.applyAll();
  assert.equal(ui.element("#footer-save-status").textContent, "Đã đồng bộ");
  ui.setConfirm(true);
  ui.applyAll();
  const html = ui.element("#table-body").innerHTML;
  for (const key of ["blank1", "blank2"]) {
    const markup = html.match(new RegExp('<tr data-key="' + key + '"[^>]*>.*?</tr>'))[0];
    assert.match(markup, /value="anh"/);
    assert.match(markup, /value="em"/);
    assert.match(markup, /đã chốt/);
  }
  for (const key of ["partial", "note", "existing", "reverse", "other"]) {
    const markup = html.match(new RegExp('<tr data-key="' + key + '"[^>]*>.*?</tr>'))[0];
    assert.doesNotMatch(markup, /value="anh"/);
  }
  assert.deepEqual(ui.calls.map(call => call.method), ["xungho.read"]);
  assert.equal(ui.element("#footer-save-status").textContent, "Có thay đổi chưa lưu");
  ui.save();
  await new Promise(resolve => setImmediate(resolve));
  const updates = ui.calls.find(call => call.method === "xungho.save").params.updates;
  assert.deepEqual(Array.from(updates.filter(update => update.key.startsWith("blank")), update => update.key),
    ["blank1", "blank2"]);
  assert.equal(updates.find(update => update.key === "blank1").self, "anh");
  assert.equal(ui.element("#footer-save-status").textContent, "Đã đồng bộ");
  assert.match(ui.element("#table-body").innerHTML.match(/<tr data-key="blank2"[^>]*>.*?<\/tr>/)[0], /value="em"/);
});

test("apply requires a complete pair and skips explicitly locked empty rows", async () => {
  const ui = setup([row("source", "anh", ""), row("blank"), row("locked")]);
  await new Promise(resolve => setImmediate(resolve));
  ui.selectRow("source");
  assert.match(ui.element("#evidence-container").innerHTML, /data-address-all disabled/);
  ui.applyAll();
  assert.equal(ui.element("#footer-save-status").textContent, "Đã đồng bộ");
  ui.selectRow("locked");
  ui.element("#table-body").onclick({
    target: {tagName: "BUTTON", closest: selector => selector === "tr[data-key]" ?
      {dataset: {key: "locked"}} : selector === "[data-lock]" ? {} : null},
  });
  ui.selectRow("source");
  ui.element("#table-body").onchange({
    target: {value: "em", dataset: {field: "you"},
      closest: () => ({dataset: {key: "source"}})},
  });
  ui.applyAll();
  const html = ui.element("#table-body").innerHTML;
  assert.match(html.match(/<tr data-key="blank"[^>]*>.*?<\/tr>/)[0], /value="anh"/);
  assert.doesNotMatch(html.match(/<tr data-key="locked"[^>]*>.*?<\/tr>/)[0], /value="anh"/);
});

test("save forward overwrites later phases only and preserves notes and unsaved unrelated changes", async () => {
    const ui = setup([
      {...row("early", "tôi", "cậu"), from: 1},
      {...row("source", "anh", "em", "nguồn"), from: 5},
      {...row("later", "ta", "ngươi", "ghi chú cũ"), from: 8},
      {...row("later-empty"), from: 12},
      {...row("reverse", "bạn", "tôi"), speaker: "b", listener: "a", from: 7},
      {...row("other"), speaker: "a", listener: "c", from: 9},
    ]);
    await new Promise(resolve => setImmediate(resolve));
    ui.element("#table-body").onchange({
      target: {value: "chưa lưu", dataset: {field: "name"},
        closest: () => ({dataset: {key: "later"}})},
    });
    ui.element("#table-body").onchange({
      target: {value: "đang sửa", dataset: {field: "self"},
        closest: () => ({dataset: {key: "other"}})},
    });
    ui.selectRow("source");
    assert.match(ui.element("#evidence-container").innerHTML, /class="btn btn-primary" data-address-all/);
    assert.match(ui.element("#evidence-container").innerHTML, /class="btn btn-primary" data-address-forward/);
    ui.setConfirm(false);
    ui.saveForward();
    assert.deepEqual(ui.calls.map(call => call.method), ["xungho.read"]);
    ui.setConfirm(true);
    ui.saveForward();
    assert.equal(ui.body.inert, true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(ui.body.inert, false);
    const updates = ui.calls.find(call => call.method === "xungho.save").params.updates;
    assert.deepEqual(Array.from(updates, update => update.key), ["source", "later", "later-empty"]);
    assert.equal(updates[1].name, "ghi chú cũ");
    assert.equal(updates[1].self, "anh");
    assert.equal(updates[1].you, "em");
    assert.equal(ui.element("#footer-save-status").textContent, "Có thay đổi chưa lưu");
    const html = ui.element("#table-body").innerHTML;
    assert.match(html.match(/<tr data-key="later"[^>]*>.*?<\/tr>/)[0], /value="chưa lưu"/);
    assert.match(html.match(/<tr data-key="later"[^>]*>.*?<\/tr>/)[0], /value="anh"/);
    assert.match(html.match(/<tr data-key="early"[^>]*>.*?<\/tr>/)[0], /value="tôi"/);
    assert.match(html.match(/<tr data-key="other"[^>]*>.*?<\/tr>/)[0], /value="đang sửa"/);
    assert.match(html.match(/<tr data-key="reverse"[^>]*>.*?<\/tr>/)[0], /value="bạn"/);
  });

test("failed forward save leaves pending state and prior rows unchanged", async () => {
    const ui = setup([row("source", "anh", "em"), {...row("later", "tôi", "cậu"), from: 5}]);
    await new Promise(resolve => setImmediate(resolve));
    ui.selectRow("source");
    ui.failSave("Dữ liệu đã thay đổi");
    ui.saveForward();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(ui.body.inert, false);
    assert.equal(ui.element("#footer-save-status").textContent, "Đã đồng bộ");
    assert.match(ui.element("#table-body").innerHTML.match(/<tr data-key="later"[^>]*>.*?<\/tr>/)[0], /value="tôi"/);
  });
