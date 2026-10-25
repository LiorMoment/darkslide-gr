// 在左栏预设列表里直接排序（2026-10-24 起不再开对话框）的交互路径，用页面自己的 order.js 和 app.js
// （renderSlots、setOrder、撤销/重做、draftBody、hasEdits、loadProject、generate 以及 init() 里真实的
// DarkslideOrder.wire 配置），在一个很小的假 DOM 里跑：单击照常打开预设、移动几像素才算拖动、拖完不误开预设、
// Alt+↑/↓ 的方向、整行拖动后提交、拖回原厂顺序时归为 null（界面不显示「已调整」）、恢复原厂顺序、
// 撤销/重做；拖动途中页面重绘不能让行重复（以前会变成 13 项，之后每次自动保存都被拒）；
// 提交前的完整性检查；排序数据异常时改名等其他改动照常保存；已存顺序读不出时的提示与「不改文件」。
// 运行：node app/tests/menu_order_column_ui.cjs（DARKSLIDE_WEB 可指向另一份 web 目录，用来做变异检查）。
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const WEB = process.env.DARKSLIDE_WEB || path.join(__dirname, "..", "web");
const ORDER_JS = fs.readFileSync(path.join(WEB, "order.js"), "utf8");
const APP_JS = fs.readFileSync(path.join(WEB, "app.js"), "utf8");
const FACTORY = [1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11];
const ROW = 40;   // every fake row is 40 px high, stacked from y = 0

function between(s, a, b) {
  const i = s.indexOf(a), j = s.indexOf(b, i);
  if (i < 0 || j < 0) throw new Error("marker missing: " + (i < 0 ? a : b));
  return s.slice(i, j);
}

// ---------------------------------------------------------------- a very small DOM
function makeDom() {
  const doc = { activeElement: null, ids: {} };
  class El {
    constructor(tag) {
      this.tagName = String(tag).toUpperCase();
      this.children = []; this.parentNode = null; this.listeners = {}; this.attrs = {};
      this._text = ""; this._cls = new Set();
      this.hidden = false; this.disabled = false; this.title = ""; this.open = false; this.value = ""; this.checked = false;
      this.style = {};
      const data = {};
      this.dataset = new Proxy(data, { set: (o, k, v) => { o[k] = String(v); return true; } });
    }
    get className() { return [...this._cls].join(" "); }
    set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
    get classList() {
      const s = this._cls;
      return { add: (...c) => c.forEach((x) => s.add(x)), remove: (...c) => c.forEach((x) => s.delete(x)),
        toggle: (c, on) => { if (on === undefined) on = !s.has(c); if (on) s.add(c); else s.delete(c); return on; },
        contains: (c) => s.has(c) };
    }
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
    set textContent(v) { for (const c of this.children) c.parentNode = null; this.children = []; this._text = String(v); }
    get parentElement() { return this.parentNode || this; }
    get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === doc.root; }
    _detach(n) { if (n.parentNode) { const p = n.parentNode, i = p.children.indexOf(n); if (i >= 0) p.children.splice(i, 1); n.parentNode = null; } }
    append(...nodes) {
      for (let n of nodes) {
        if (typeof n === "string") { const t = new El("#text"); t._text = n; n = t; }
        this._detach(n); n.parentNode = this; this.children.push(n);
      }
    }
    replaceChildren(...nodes) { for (const c of this.children) c.parentNode = null; this.children = []; this._text = ""; this.append(...nodes); }
    insertBefore(node, ref) {
      if (ref != null && ref.parentNode !== this) throw new Error("NotFoundError: the reference row is not in this list");
      this._detach(node); node.parentNode = this;
      if (ref == null) this.children.push(node); else this.children.splice(this.children.indexOf(ref), 0, node);
      return node;
    }
    remove() { this._detach(this); }
    get nextSibling() { const p = this.parentNode; if (!p) return null; return p.children[p.children.indexOf(this) + 1] || null; }
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
    removeEventListener(t, f) { const l = this.listeners[t] || []; const i = l.indexOf(f); if (i >= 0) l.splice(i, 1); }
    dispatch(type, props) {
      const ev = Object.assign({ type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }, props || {});
      for (let n = this; n; n = n.parentNode) for (const f of [...(n.listeners[type] || [])]) { ev.currentTarget = n; f.call(n, ev); }
      return ev;
    }
    click() { if (!this.disabled) this.dispatch("click", { button: 0 }); }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k.startsWith("data-")) this.dataset[k.slice(5)] = v; }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    focus() { doc.activeElement = this; }
    blur() { if (doc.activeElement === this) doc.activeElement = null; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    scrollIntoView() {}
    getBoundingClientRect() {
      const p = this.parentNode, i = p ? p.children.indexOf(this) : 0;
      return { top: i * ROW, bottom: (i + 1) * ROW, height: ROW, left: 0, right: 400, width: 400 };
    }
    get offsetWidth() { return 0; }
    get scrollHeight() { return 0; } get scrollTop() { return 0; } get clientHeight() { return 0; }
    *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
    // Only what order.js asks for: '[data-slot="N"] .cls'. Anything else gets a detached stand-in.
    querySelector(sel) {
      const m = /^\[data-slot="(\d+)"\]\s+\.([\w-]+)$/.exec(sel);
      if (!m) return new El("div");
      for (const r of this.walk()) if (r.dataset.slot === m[1]) for (const c of r.walk()) if (c._cls.has(m[2])) return c;
      return null;
    }
    querySelectorAll() { return []; }
    closest() { return null; }
  }
  doc.root = new El("#document");
  doc.getElementById = (id) => {
    if (!doc.ids[id]) { const e = new El("div"); e.id = id; doc.ids[id] = e; doc.root.append(e); }
    return doc.ids[id];
  };
  doc.createElement = (tag) => new El(tag);
  doc.querySelector = () => null;
  doc.querySelectorAll = () => [];
  doc.addEventListener = () => {};
  return { doc, El };
}

// ---------------------------------------------------------------- the page, freshly loaded for every case
function page() {
  const { doc } = makeDom();
  const winListeners = {};
  const window = {
    DarkslideNames: { ok: () => true },
    DarkslideGuide: { hideArrow() {}, showArrow() {}, active: () => false, start() {} },
    DarkslideFilm: { SLOTS: [8, 9, 10, 11], nameOf: (x) => x, load() {}, fitPending: async () => {}, candidateReady: () => false, writerReady: () => false },
    DarkslideRecipe: { wire() {} },
    addEventListener(t, f) { (winListeners[t] = winListeners[t] || []).push(f); },
    removeEventListener(t, f) { const l = winListeners[t] || []; const i = l.indexOf(f); if (i >= 0) l.splice(i, 1); },
  };
  const ctx = vm.createContext({ window, document: doc, console, setTimeout: () => 0, clearTimeout() {},
    requestAnimationFrame() {}, matchMedia: () => ({ matches: true }) });
  vm.runInContext(ORDER_JS, ctx);
  vm.runInContext(APP_JS.replace(/\binit\(\);\s*$/, ""), ctx);
  const wire = between(APP_JS, "  window.DarkslideOrder.wire({", "\n  });\n") + "\n  });";
  vm.runInContext(`
    globalThis.drafts = []; globalThis.toasts = []; globalThis.apis = [];
    toast = (msg, bad) => toasts.push([msg, !!bad]);
    api = async (method, url, body) => { apis.push([method, url, body === undefined ? null : JSON.parse(JSON.stringify(body))]); return url.endsWith("/jobs") ? { jobs: [] } : {}; };
    // Autosave: record exactly what would be sent at this moment.
    scheduleDraft = () => drafts.push(JSON.parse(JSON.stringify(draftBody())));
    renderAll = () => { renderSlots(); window.DarkslideOrder.render(); };
    state.status = { prefs: { preflight_seen: true }, builds: [],
      menu_order: { schema: 1, factory_still: ${JSON.stringify(FACTORY)}, factory_video: [1, 2, 3, 5, 6, 12, 7, 8, 9, 11], video_excluded: [4, 10] } };
    state.template = { model: "GR III", firmware: "2.10", reviewed: true,
      slots: Array.from({ length: 14 }, (_, i) => ({ no: i + 1, name: "预设" + (i + 1), abbr: "P" + (i + 1), factory_en: "Preset " + (i + 1) })) };
    state.project = { id: "p_synthetic", unsaved: false, capabilities: { menu_order: true, menu_order_write: true, full_firmware: true }, menu_language: "en", draft: { changes: {} } };
    state.changes = {};
    state.order = null;
    ${wire}
    renderAll();
  `, ctx);
  const $ = (id) => doc.getElementById(id);
  // Values from the page's realm, as plain data (arrays from another realm are not deepStrictEqual).
  const run = (code) => {
    const v = vm.runInContext(code, ctx);
    return v && typeof v === "object" && typeof v.then !== "function" ? JSON.parse(JSON.stringify(v)) : v;
  };
  const rows = () => $("slots").children;
  const listed = () => rows().map((r) => Number(r.dataset.slot)).filter((n) => FACTORY.includes(n));
  const row = (no) => rows().find((r) => r.dataset.slot === String(no));
  const grip = (no) => row(no).children.find((c) => c._cls.has("slot-grip"));
  const fire = (type, props) => { for (const f of [...(winListeners[type] || [])]) f(Object.assign({ type, preventDefault() {} }, props)); };
  const drag = {
    down: (no, extra) => row(no).dispatch("pointerdown", Object.assign({ button: 0, pointerId: 1, pointerType: "mouse", clientY: row(no).getBoundingClientRect().top + 1 }, extra || {})),
    move: (y) => fire("pointermove", { pointerId: 1, clientY: y }),
    // Pointer just inside the top half of row `no`: the dragged row goes right before it.
    before: (no) => fire("pointermove", { pointerId: 1, clientY: row(no).getBoundingClientRect().top + 1 }),
    toEnd: () => fire("pointermove", { pointerId: 1, clientY: 100000 }),
    fire,
    up: () => fire("pointerup", { pointerId: 1 }),
  };
  const key = (no, k, alt) => row(no).dispatch("keydown", { key: k, altKey: alt !== false });
  const order = () => run("state.order");
  const body = (save) => run("JSON.parse(JSON.stringify(draftBody(" + (save ? "true" : "") + ")))");
  return { $, run, rows, listed, row, grip, drag, key, order, body, doc, ctx, winListeners };
}

const permutation = (a) => Array.isArray(a) && a.length === 12 && new Set(a).size === 12 && a.every((n) => FACTORY.includes(n));
const validBody = (b) => b.menu_order === null || (permutation(b.menu_order.still_slots) &&
  JSON.stringify(b.menu_order.video_slots) === JSON.stringify(b.menu_order.still_slots.filter((s) => s !== 4 && s !== 10)));

// ---------------------------------------------------------------- cases
const cases = {
  "初始：原厂顺序、按钮不显示已调整、恢复按钮不可点"(p) {
    assert.deepStrictEqual(p.listed(), FACTORY);
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 原厂");
        assert.strictEqual(p.$("orderReset").disabled, true);
    assert.strictEqual(p.order(), null);
  },

  "Alt+↓ 往下挪一位、Alt+↑ 往上挪一位，挪回原厂时归为 null"(p) {
    p.key(1, "ArrowDown");
    assert.deepStrictEqual(p.order(), [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11], "Alt+↓ 把预设 1 挪到第 2 位");
    assert.deepStrictEqual(p.listed(), p.order());
    assert.strictEqual(p.$("orderLive").textContent, "预设1 移到第 2 位");
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 已调整");
    assert.strictEqual(p.$("orderReset").disabled, false);
    assert.strictEqual(p.doc.activeElement, p.row(1), "焦点跟着预设 1 那一行");
    const sent = p.run("drafts[drafts.length - 1]");
    assert.deepStrictEqual(sent.menu_order, { schema: 1, still_slots: [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11], video_slots: [2, 1, 3, 5, 6, 12, 7, 8, 9, 11] });
    p.key(12, "ArrowUp");
    assert.deepStrictEqual(p.order(), [2, 1, 3, 4, 5, 12, 6, 7, 8, 9, 10, 11], "Alt+↑ 把负片挪到前一位");
    p.key(12, "ArrowDown");
    p.key(1, "ArrowUp");
    assert.strictEqual(p.order(), null, "挪回原厂顺序后存 null，不是一份和原厂一样的列表");
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 原厂");
        assert.strictEqual(p.$("orderReset").disabled, true);
    assert.strictEqual(p.run("drafts[drafts.length - 1]").menu_order, null);
    assert.strictEqual(p.run("hasEdits()"), false);
  },

  "Alt 键之外的方向键、首行再往上、末行再往下都不动"(p) {
    p.key(3, "ArrowDown", false);
    p.key(1, "ArrowUp");
    p.key(11, "ArrowDown");
    p.key(3, "ArrowLeft");
    assert.strictEqual(p.order(), null);
    assert.strictEqual(p.run("drafts.length"), 0, "没有动就不自动保存");
    assert.strictEqual(p.run("state.undo.length"), 0);
  },

  "每次挪动是一步撤销，撤销/重做后界面与写入一致"(p) {
    p.key(1, "ArrowDown");
    p.key(1, "ArrowDown");
    assert.deepStrictEqual(p.order(), [2, 3, 1, 4, 5, 6, 12, 7, 8, 9, 10, 11]);
    p.run("undo()");
    assert.deepStrictEqual(p.order(), [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11]);
    assert.deepStrictEqual(p.listed(), p.order());
    p.run("undo()");
    assert.strictEqual(p.order(), null);
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 原厂");
    assert.strictEqual(p.run("drafts[drafts.length - 1]").menu_order, null);
    p.run("redo()");
    assert.deepStrictEqual(p.order(), [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11]);
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 已调整");
  },

  "整行拖动：松手时提交一次，算一步撤销"(p) {
    p.drag.down(12);
    assert.ok(!p.row(12)._cls.has("dragging"), "只按下还不算拖动（单击要能打开预设）");
    p.drag.move(0);
    assert.ok(p.row(12)._cls.has("dragging"));
    assert.deepStrictEqual(p.listed(), [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], "拖动中其他行让位");
    assert.strictEqual(p.order(), null, "松手前不提交");
    p.drag.up();
    assert.deepStrictEqual(p.order(), [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    assert.strictEqual(p.run("state.undo.length"), 1);
    assert.ok(!p.row(12)._cls.has("dragging"));
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 已调整");
    assert.strictEqual(p.$("orderLive").textContent, "预设12 移到第 1 位");
    const sent = p.run("drafts[drafts.length - 1]");
    assert.deepStrictEqual(sent.menu_order.still_slots, [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    // 第二次拖：把预设 1 拖到最后
    p.drag.down(1);
    p.drag.toEnd();
    p.drag.up();
    assert.deepStrictEqual(p.order(), [12, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 1]);
    assert.strictEqual(p.run("state.undo.length"), 2);
  },

  "拖回原厂顺序：存 null，界面不显示已调整，写入也是 null"(p) {
    p.drag.down(12);
    p.drag.move(0);
    p.drag.up();
    assert.deepStrictEqual(p.order(), [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    p.drag.down(12);
    p.drag.before(7);   // back between 6 and 7: the factory order
    p.drag.up();
    assert.strictEqual(p.order(), null);
    assert.deepStrictEqual(p.listed(), FACTORY);
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 原厂");
    assert.ok(!p.$("orderBar")._cls.has("on"));
        const sent = p.run("drafts[drafts.length - 1]");
    assert.ok("menu_order" in sent && sent.menu_order === null, "界面显示原厂时写入也是 null");
    assert.strictEqual(p.run("hasEdits()"), false);
  },

  async "拖动后马上退出：退出前写的草稿带着刚拖好的顺序"(p) {
    p.run("state.changes = { 2: { name: 'Vivid+' } }");
    p.drag.down(11);
    p.drag.move(0);
    p.drag.up();
    await p.run("quitApp()");
    const [put, quit] = p.run("apis");
    assert.deepStrictEqual(put.slice(0, 2), ["PUT", "/api/projects/p_synthetic/draft"]);
    assert.deepStrictEqual(put[2].changes, { 2: { name: "Vivid+" } });
    assert.deepStrictEqual(put[2].menu_order.still_slots, [11, 1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10]);
    assert.deepStrictEqual(put[2].menu_order.video_slots, [11, 1, 2, 3, 5, 6, 12, 7, 8, 9]);
    assert.deepStrictEqual(quit.slice(0, 2), ["POST", "/api/quit"]);
  },

  "拖了又放回原位：不提交、不加撤销步"(p) {
    p.drag.down(5);
    p.drag.move(0);
    p.drag.before(6);
    p.drag.up();
    assert.strictEqual(p.order(), null);
    assert.strictEqual(p.run("state.undo.length"), 0);
    assert.strictEqual(p.run("drafts.length"), 0);
    assert.deepStrictEqual(p.listed(), FACTORY);
  },

  "触屏只在拖动柄上开始拖，按在行上别处照常滚动"(p) {
    p.row(3).dispatch("pointerdown", { button: 0, pointerId: 1, pointerType: "touch" });
    p.drag.move(0);
    p.drag.up();
    assert.strictEqual(p.order(), null);
    p.grip(3).dispatch("pointerdown", { button: 0, pointerId: 1, pointerType: "touch" });
    p.drag.move(0);
    p.drag.up();
    assert.deepStrictEqual(p.order(), [3, 1, 2, 4, 5, 6, 12, 7, 8, 9, 10, 11]);
  },

  "系统收回指针（pointercancel）：不提交，回到拖动前"(p) {
    p.key(1, "ArrowDown");
    const before = p.order(), sent = p.run("drafts.length");
    p.drag.down(9);
    p.drag.move(0);
    p.drag.fire("pointercancel", { pointerId: 1 });
    assert.deepStrictEqual(p.order(), before);
    assert.deepStrictEqual(p.listed(), before);
    assert.strictEqual(p.run("drafts.length"), sent);
    p.drag.down(9);   // and a new drag starts normally afterwards
    p.drag.move(0);
    p.drag.up();
    assert.strictEqual(p.order()[0], 9);
  },

  "恢复原厂顺序按钮：回到 null，写入 null"(p) {
    p.key(2, "ArrowDown");
    p.key(7, "ArrowUp");
    assert.notStrictEqual(p.order(), null);
    p.$("orderReset").click();
    assert.strictEqual(p.order(), null);
    assert.strictEqual(p.$("orderLive").textContent, "已恢复原厂顺序");
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 原厂");
    assert.strictEqual(p.$("orderReset").disabled, true);
    assert.strictEqual(p.run("drafts[drafts.length - 1]").menu_order, null);
    p.run("undo()");
    assert.notStrictEqual(p.order(), null, "恢复原厂也能撤销");
  },

  "拖动途中页面重绘一次：不重复、不丢行，仍是 12 个合法槽位"(p) {
    p.drag.down(3);
    p.drag.move(0);
    p.run("renderAll()");   // e.g. another part of the page re-renders mid-drag
    assert.deepStrictEqual(p.listed(), [3, 1, 2, 4, 5, 6, 12, 7, 8, 9, 10, 11], "重绘保留拖动中的位置");
    assert.ok(p.row(3)._cls.has("dragging"), "重绘后新的那一行接着被拖");
    p.drag.before(9);
    assert.strictEqual(p.listed().length, 12, "列表里始终 12 个内置预设");
    assert.ok(permutation(p.listed()), "拖动中也没有重复行：" + p.listed());
    p.drag.up();
    assert.strictEqual(p.listed().length, 12);
    assert.ok(permutation(p.order()), "提交的是 12 个不重复的槽位：" + JSON.stringify(p.order()));
    assert.deepStrictEqual(p.order(), [1, 2, 4, 5, 6, 12, 7, 8, 3, 9, 10, 11], "重绘后拖动照常生效");
    assert.ok(validBody(p.body()), "发给服务器的是合法顺序");
    assert.ok(validBody(p.run("drafts[drafts.length - 1]")));
    assert.deepStrictEqual(p.listed(), p.order());
  },

  "拖动中重绘后直接松手（没有再移动）：顺序合法"(p) {
    p.drag.down(8);
    p.drag.move(0);
    p.run("renderAll()");
    p.drag.up();
    assert.strictEqual(p.listed().length, 12);
    const o = p.order();
    assert.ok(o === null || permutation(o), JSON.stringify(o));
    assert.ok(validBody(p.body()));
  },

  "拖动中连续重绘多次再拖：每次自动保存都是合法顺序"(p) {
    p.drag.down(10);
    for (let i = 0; i < 3; i++) { p.drag.move(i * ROW); p.run("renderAll()"); }
    p.drag.before(3);
    p.drag.up();
    assert.ok(permutation(p.order()), JSON.stringify(p.order()));
    p.key(1, "ArrowDown");
    assert.ok(permutation(p.order()));
    for (const d of p.run("drafts")) assert.ok(validBody(d), JSON.stringify(d));
  },

  "提交前完整性检查：列表被弄坏时回到拖动前的顺序，不发出坏数据"(p) {
    p.key(1, "ArrowDown");
    const before = p.order(), sentBefore = p.run("drafts.length");
    p.drag.down(4);
    p.drag.move(0);
    // Something else slipped a stray copy of a row into the list while dragging.
    const stray = p.doc.createElement("li"); stray.dataset.slot = 7; stray.className = "order-row";
    p.$("slots").insertBefore(stray, p.row(13));
    p.drag.up();
    assert.deepStrictEqual(p.order(), before, "回到拖动前的顺序");
    assert.strictEqual(p.run("drafts.length"), sentBefore, "没有发出这次的顺序");
    assert.deepStrictEqual(p.listed(), before, "列表按拖动前的顺序重画");
    assert.match(p.$("orderLive").textContent, /拖动前/);
  },

  "左栏按相机原厂顺序列出（负片在正片前），12 个内置预设有拖动柄，自定义 1/2 没有"(p) {
    assert.deepStrictEqual(p.rows().map((r) => Number(r.dataset.slot)), FACTORY.concat([13, 14]));
    for (const no of FACTORY) assert.strictEqual(p.grip(no).textContent, "⠿");
    for (const no of [13, 14]) assert.ok(p.grip(no)._cls.has("fixed") && p.grip(no).textContent === "", "自定义预设只占位");
    assert.match(p.row(13).getAttribute("title"), /相机固定在最后/);
  },

  "单击照常打开预设；按下后移动不到 5 像素也算单击"(p) {
    p.row(3).click();
    assert.strictEqual(p.run("state.sel"), 3);
    p.drag.down(5);
    p.drag.move(p.row(5).getBoundingClientRect().top + 4);
    p.drag.up();
    assert.strictEqual(p.order(), null, "没有拖动");
    p.row(5).click();
    assert.strictEqual(p.run("state.sel"), 5);
    assert.strictEqual(p.run("state.undo.length"), 0);
  },

  "拖完紧跟的那次点击不会打开预设，之后的点击照常"(p) {
    p.run("state.sel = 1");
    p.drag.down(9);
    p.drag.move(0);
    p.drag.up();
    p.row(9).click();
    assert.strictEqual(p.run("state.sel"), 1, "拖动结束的点击被吞掉");
    p.row(9).click();
    assert.strictEqual(p.run("state.sel"), 9);
  },

  "拖到最底：停在自定义 1/2 前面，不越过它们"(p) {
    p.drag.down(1);
    p.drag.toEnd();
    assert.deepStrictEqual(p.rows().slice(-2).map((r) => Number(r.dataset.slot)), [13, 14]);
    p.drag.up();
    assert.deepStrictEqual(p.order(), [2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11, 1]);
    p.row(13).dispatch("pointerdown", { button: 0, pointerId: 1, pointerType: "mouse", clientY: 521 });
    p.drag.move(0);
    p.drag.up();
    assert.deepStrictEqual(p.order(), [2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11, 1], "自定义预设拖不动");
  },

  "不能排序的型号（GR IIIx，适配中）：说明原因，没有拖动柄，拖动和 Alt+方向键都不动"(p) {
    p.run("state.project.capabilities = { menu_order: false, full_firmware: true }; state.template.model = 'GR IIIx'; renderAll()");
    assert.strictEqual(p.$("orderBar").hidden, true);
    assert.strictEqual(p.$("orderUnsupported").hidden, false);
    assert.strictEqual(p.$("orderUnsupported").textContent, "GR IIIx 的菜单排序还在适配中，暂时不能拖动");
    assert.ok(p.rows().every((r) => !r.children.some((c) => c._cls.has("slot-grip"))), "没有拖动柄");
    p.drag.down(3);
    p.drag.move(0);
    p.drag.up();
    p.key(3, "ArrowUp");
    assert.strictEqual(p.order(), null);
    assert.strictEqual(p.run("state.undo.length"), 0);
  },

  "开着动画（没选减少动态效果）：拖动、键盘和恢复原厂结果不变，松手后没有行停在半路"(p) {
    p.run("window.matchMedia = () => ({ matches: false })");
    p.drag.down(12);
    p.drag.move(0);
    assert.match(p.row(12).style.transform || "", /translateY/, "拖着的行跟着指针");
    p.drag.up();
    assert.deepStrictEqual(p.order(), [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    assert.strictEqual(p.listed().length, 12);
    p.key(1, "ArrowDown");
    assert.deepStrictEqual(p.order(), [12, 2, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    p.$("orderReset").click();
    assert.strictEqual(p.order(), null);
    assert.deepStrictEqual(p.listed(), FACTORY);
    assert.ok(p.rows().every((r) => !/translateY\(-?[1-9]/.test(r.style.transform || "")), "结束后每行都回到自己的位置");
  },

  "setOrder 拒绝不合法的顺序（重复、缺项、多项、非槽位）"(p) {
    for (const bad of [[1, 1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10], FACTORY.concat([3]), FACTORY.slice(1), FACTORY.slice(0, 11).concat([13]), FACTORY.map(String)]) {
      p.run("setOrder(" + JSON.stringify(bad) + ")");
      assert.strictEqual(p.order(), null, JSON.stringify(bad));
    }
    assert.strictEqual(p.run("drafts.length"), 0);
    assert.strictEqual(p.run("state.undo.length"), 0);
  },

  "顺序数据异常时不连带其他改动：改名照常保存，顺序这次不发"(p) {
    p.run("state.changes = { 7: { name: 'Posi Warm' } }");
    p.run("state.order = " + JSON.stringify(FACTORY.concat([7])));   // e.g. left over from an older page
    const b = p.body();
    assert.deepStrictEqual(b.changes, { 7: { name: "Posi Warm" } });
    assert.ok(!("menu_order" in b), "不发坏顺序（服务器保留它已有的）");
    assert.strictEqual(p.run("hasEdits()"), true);
    p.run("state.changes = {}");
    assert.strictEqual(p.run("hasEdits()"), false, "坏顺序不算改动");
    // and a list equal to the factory order is sent as null and is not an edit either
    p.run("state.order = " + JSON.stringify(FACTORY));
    assert.ok("menu_order" in p.body() && p.body().menu_order === null);
    assert.strictEqual(p.run("hasEdits()"), false);
  },

  async "已存顺序读不出：照常打开、按原厂显示并提示，自动保存不改它，保存时才清除"(p) {
    const project = { id: "p_synthetic", title: "t", template_id: "gr3-2.10-reviewed-1", input_sha256: "0".repeat(64), unsaved: false,
      saved_at: "2026-10-18T10:00:00+0900", capabilities: { menu_order: true, menu_order_write: true, full_firmware: true }, menu_language: "en",
      menu_order: { schema: 9 }, menu_order_unreadable: "菜单顺序格式错误：菜单顺序版本不认识",
      draft: { changes: { 7: { name: "Posi Warm" } }, menu_order: null, menu_order_unreadable: "菜单顺序格式错误：菜单顺序版本不认识" } };
    p.run("renderProjectBody = () => {}; show = () => {}; maybeGuide = () => {};");
    await p.run("loadProject(" + JSON.stringify(project) + ")");
    assert.strictEqual(p.order(), null, "按原厂顺序显示");
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 无法读取");
    assert.match(p.$("orderProblem").textContent, /已存的菜单顺序无法读取，已按原厂顺序显示，保存后会清除/);
    assert.strictEqual(p.$("orderProblem").hidden, false);
    assert.strictEqual(p.$("orderReset").disabled, false, "可以明确恢复原厂顺序");
    assert.ok(p.run("toasts").some(([m, bad]) => bad && /无法读取/.test(m)), "打开时就提示");
    // autosave / quit / page close: the stored value stays as it is (no menu_order key)
    assert.ok(!("menu_order" in p.body()), "自动保存不带顺序，服务器原样保留");
    assert.deepStrictEqual(p.body().changes, { 7: { name: "Posi Warm" } }, "改名照常保存");
    // ⌘S is the confirmation the notice talks about: it clears the unreadable order
    const s = p.body(true);
    assert.ok("menu_order" in s && s.menu_order === null);
    // generating is refused until the user settles the order (it would save and clear it)
    p.run("toasts.length = 0; apis.length = 0");
    await p.run("generate()");
    assert.strictEqual(p.run("apis.length"), 0, "没有保存、没有生成");
    assert.ok(p.run("toasts").some(([m, bad]) => bad && /菜单顺序/.test(m)));
  },

  async "只有草稿里的顺序读不出：显示上次保存的顺序并提示，下次写草稿带上它（不会被换成原厂）"(p) {
    const saved = { schema: 1, still_slots: FACTORY.slice().reverse(), video_slots: FACTORY.slice().reverse().filter((s) => s !== 4 && s !== 10) };
    const project = { id: "p_synthetic", title: "t", template_id: "gr3-2.10-reviewed-1", input_sha256: "0".repeat(64), unsaved: true,
      saved_at: "2026-10-18T10:00:00+0900", capabilities: { menu_order: true, menu_order_write: true, full_firmware: true }, menu_language: "en", menu_order: saved,
      draft: { changes: {}, menu_order: saved, menu_order_fallback: "菜单顺序格式错误：菜单顺序版本不认识" } };
    p.run("renderProjectBody = () => {}; show = () => {}; maybeGuide = () => {};");
    await p.run("loadProject(" + JSON.stringify(project) + ")");
    assert.deepStrictEqual(p.order(), saved.still_slots, "显示上次保存的顺序");
    assert.strictEqual(p.run("state.orderProblem"), null);
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 已调整");
    assert.ok(p.run("toasts").some(([m, bad]) => bad && /草稿里的菜单顺序无法读取，已改用上次保存的顺序/.test(m)));
    assert.deepStrictEqual(p.body().menu_order.still_slots, saved.still_slots, "自动保存写回上次保存的顺序");
    assert.deepStrictEqual(p.body(true).menu_order.still_slots, saved.still_slots, "⌘S 也不会换成原厂");
  },

  "不能排序的型号带着读不出的顺序：自动保存不带顺序，⌘S 发 null 清掉；没有问题时都不带"(p) {
    p.run("state.project.capabilities = { menu_order: false, full_firmware: true }; state.order = null; state.orderProblem = null");
    assert.ok(!("menu_order" in p.body()));
    assert.ok(!("menu_order" in p.body(true)));
    p.run("state.orderProblem = '这个型号暂不支持排序'");
    assert.ok(!("menu_order" in p.body()), "自动保存不改它");
    const s = p.body(true);
    assert.ok("menu_order" in s && s.menu_order === null, "⌘S 时清掉");
  },

  "已存顺序读不出时点恢复原厂顺序：提示消失，明确写 null"(p) {
    p.run("state.orderProblem = '菜单顺序格式错误：x'; window.DarkslideOrder.render()");
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 无法读取");
    p.$("orderReset").click();
    assert.strictEqual(p.run("state.orderProblem"), null);
    assert.strictEqual(p.$("orderProblem").hidden, true);
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 原厂");
    const sent = p.run("drafts[drafts.length - 1]");
    assert.ok("menu_order" in sent && sent.menu_order === null);
  },

  "已存顺序读不出时重新排序：提示消失，发出新顺序"(p) {
    p.run("state.orderProblem = '菜单顺序格式错误：x'; window.DarkslideOrder.render()");
    p.key(1, "ArrowDown");
    assert.strictEqual(p.run("state.orderProblem"), null);
    assert.deepStrictEqual(p.run("drafts[drafts.length - 1]").menu_order.still_slots, [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11]);
    assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 已调整");
  },

  "保存成功后不再提示读不出"(p) {
    p.run("state.orderProblem = '菜单顺序格式错误：x'; window.DarkslideOrder.render()");
    p.run("api = async (m, u, b) => { apis.push([m, u, b === undefined ? null : JSON.parse(JSON.stringify(b))]); " +
      "return u.endsWith('/save') ? { id: 'p_synthetic', saved_at: '2026-10-18T10:00:00', versions: [{}], unsaved: false, capabilities: state.project.capabilities, menu_language: 'en', draft: { changes: {}, menu_order: null } } : u.endsWith('/jobs') ? { jobs: [] } : {}; }");
    return p.run("saveProject()").then((ok) => {
      assert.strictEqual(ok, true);
      const put = p.run("apis")[0];
      assert.ok("menu_order" in put[2] && put[2].menu_order === null, "保存时把读不出的顺序清掉");
      assert.strictEqual(p.run("state.orderProblem"), null);
      assert.strictEqual(p.$("orderState").textContent, "菜单顺序 · 原厂");
    });
  },
};

(async () => {
  const failed = [];
  for (const [name, fn] of Object.entries(cases)) {
    try { await fn(page()); }
    catch (e) { failed.push(name); console.error("FAIL " + name + "\n  " + String(e && e.message || e).split("\n").join("\n  ")); }
  }
  if (failed.length) { console.error(failed.length + " / " + Object.keys(cases).length + " 项失败"); process.exitCode = 1; return; }
  console.log("PASS menu order column: " + Object.keys(cases).length + " cases (Alt+↑/↓ direction, drag commit, drag back to factory = null, reset, undo/redo, " +
    "redraw mid-drag keeps 12 distinct rows, integrity check reverts, other edits still save, unreadable stored order)");
})().catch((e) => { console.error(e); process.exitCode = 1; });
