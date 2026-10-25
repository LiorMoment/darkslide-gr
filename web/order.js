"use strict";
// 相机菜单顺序, sorted right in the preset column (user, 2026-10-24: no separate dialog). Drag a row
// (mouse: anywhere on it, after moving a few pixels so a plain click still opens the preset; touch:
// on its ⠿ grip) or press Alt+↑/↓ on it to move one of the 12 built-in presets in the camera's Image
// Control menu. No arrow buttons (user, 2026-10-13: 「不要加箭头，让它可以动就行」). The order is a
// list of fixed preset numbers, so a renamed preset, a new icon or a film moves with its preset.
// 自定义 1/2 stay last. Models whose menu tables are not checked yet show why they cannot be sorted.
// An order is either null (the factory order) or the 12 built-in presets, each exactly once: nothing
// else is ever stored in the page or sent (review 2026-10-18: a redraw during a drag used to add a 13th row).
(function () {
  const $ = (id) => document.getElementById(id);
  const FALLBACK = { schema: 1, factory_still: [1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11], video_excluded: [4, 10] };
  const THRESHOLD = 5;   // px a mouse has to move before a press on a row becomes a drag
  let ctx = null, drag = null, pending = null, swallow = false;

  const info = () => (ctx.status().menu_order || FALLBACK);
  const factory = () => info().factory_still.slice();
  const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
  // null (factory) or exactly the 12 built-in preset numbers, each once.
  function valid(order) {
    if (order === null) return true;
    const f = factory();
    return Array.isArray(order) && order.length === f.length && new Set(order).size === f.length &&
      order.every((s) => typeof s === "number" && f.includes(s));
  }
  // Always a copy: moves must not touch the order held by the app before its undo snapshot.
  const current = () => { const o = ctx.get(); return (o && valid(o) ? o : factory()).slice(); };
  const rows = () => [...$("slots").children];
  const movable = (r) => r && factory().includes(Number(r.dataset.slot));
  const shown = () => rows().filter(movable).map((r) => Number(r.dataset.slot));
  const rowOf = (no) => rows().find((r) => Number(r.dataset.slot) === no) || null;

  // What the server stores: null for the factory order; the movie list follows the photo order.
  // undefined: not a usable order, so the page sends nothing for it (the server keeps what it has).
  function payload(order) {
    if (!valid(order)) return undefined;
    if (!order || same(order, factory())) return null;
    const skip = info().video_excluded;
    return { schema: info().schema, still_slots: order.slice(), video_slots: order.filter((s) => !skip.includes(s)) };
  }

  function fromServer(value) {
    const still = value && Array.isArray(value.still_slots) ? value.still_slots.slice() : null;
    return still && valid(still) && !same(still, factory()) ? still : null;
  }

  // Motion (user, 2026-10-24: smoother). Rows glide to their new place instead of jumping: remember where
  // each preset is on screen, let the column redraw, then slide every row from there to its new place.
  // Nothing moves when the system asks for reduced motion.
  const GLIDE = "transform 180ms cubic-bezier(0.2, 0.8, 0.2, 1)";
  const still = () => !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  function places() {
    const m = new Map();
    for (const r of rows()) m.set(r.dataset.slot, r.getBoundingClientRect().top);
    return m;
  }
  function glide(before, skip) {
    if (!before || still()) return;
    for (const r of rows()) {
      if (r === skip || !before.has(r.dataset.slot)) continue;
      r.style.transition = "none";
      r.style.transform = "";
      const d = before.get(r.dataset.slot) - r.getBoundingClientRect().top;
      if (!d) continue;
      r.style.transform = `translateY(${d}px)`;
      void r.offsetHeight;   // start from the old place
      r.style.transition = GLIDE;
      r.style.transform = "";
    }
  }
  // Where a row sits in the layout, ignoring any slide it is in the middle of.
  function layoutTop(r) {
    const list = $("slots");
    if (typeof r.offsetTop === "number" && typeof list.offsetTop === "number")
      return list.getBoundingClientRect().top + r.offsetTop - list.offsetTop;
    return r.getBoundingClientRect().top;
  }

  // Checked before it reaches the app: a broken list is never stored, the column is redrawn from what is stored.
  function commit(order, focusSlot, before) {
    if (!ctx.supported()) return false;
    if (!valid(order)) { ctx.redraw(); render(); glide(before); return false; }
    ctx.set(same(order, factory()) ? null : order);
    glide(before);
    if (focusSlot) { const r = rowOf(focusSlot); if (r) r.focus({ preventScroll: true }); }
    return true;
  }

  function move(no, delta) {
    const order = current();
    const i = order.indexOf(no), j = i + delta;
    if (i < 0 || j < 0 || j >= order.length) return;
    order.splice(j, 0, order.splice(i, 1)[0]);
    announce(`${ctx.label(no).name} 移到第 ${j + 1} 位`);
    commit(order, no, places());
  }

  function announce(text) { $("orderLive").textContent = text; }

  // The ⠿ grip the preset column puts at the start of each row; fixed rows get an empty one so names line up.
  function grip(no) {
    if (!ctx || !ctx.supported()) return null;
    const g = document.createElement("span");
    g.setAttribute("aria-hidden", "true");
    if (factory().includes(no)) { g.className = "slot-grip"; g.textContent = "⠿"; }
    else g.className = "slot-grip fixed";
    return g;
  }

  // The row's hint, added to its title.
  function hint(no) {
    if (!ctx || !ctx.supported()) return "";
    return factory().includes(no) ? "拖动或按 Alt+↑/↓ 调整在相机菜单里的位置" : "相机固定在最后，不能移动";
  }

  // The line under 「影像控制」: the state of the order, the factory reset and why a model cannot be sorted.
  function render() {
    if (!ctx || !$("orderBar")) return;
    const ok = ctx.supported();
    $("orderBar").hidden = !ok;
    $("orderHint").hidden = !ok;
    $("orderUnsupported").hidden = ok;
    $("orderUnsupported").textContent = ok ? "" : ctx.unsupported();
    // A stored order the server could not read: shown as the factory order and left alone on disk
    // until the user moves a preset, restores the factory order or saves.
    const problem = ok && ctx.problem ? ctx.problem() : null;
    const changed = ok && !problem && !!ctx.get();
    $("orderBar").classList.toggle("on", changed);
    $("orderBar").classList.toggle("warn", !!problem);
    $("orderState").textContent = problem ? "菜单顺序 · 无法读取" : changed ? "菜单顺序 · 已调整" : "菜单顺序 · 原厂";
    $("orderProblem").hidden = !problem;
    $("orderProblem").textContent = problem ? `已存的菜单顺序无法读取，已按原厂顺序显示，保存后会清除。要用原厂顺序就点「恢复原厂」，也可以直接重新拖动。（${problem}）` : "";
    $("orderReset").disabled = !changed && !problem;
    $("orderBodyNote").textContent = ok ? ctx.note() : "";
    $("orderBodyNote").hidden = !ok || !ctx.note();
  }

  // Pointer drag: the row follows the finger/mouse, the others make room; one undo step on release.
  // Listeners sit on the window: moving the row in the list drops any pointer capture on its grip.
  // While a drag runs the preset column is not redrawn (dragging() tells it), so the rows the drag
  // moves are the rows on screen; a redraw asked for meanwhile happens when the drag ends.
  function rowFrom(target) {
    const list = $("slots");
    for (let n = target; n && n !== list; n = n.parentNode) if (n.parentNode === list) return n;
    return null;
  }

  function onDown(e) {
    swallow = false;
    if (!ctx.supported() || e.button !== 0 || drag || pending) return;
    const r = rowFrom(e.target);
    if (!movable(r)) return;
    const onGrip = !!(e.target && e.target.classList && e.target.classList.contains("slot-grip"));
    if (e.pointerType === "touch" && !onGrip) return;   // swiping elsewhere on the column still scrolls
    pending = { id: e.pointerId, no: Number(r.dataset.slot), y: e.clientY || 0, now: e.pointerType === "touch" };
    if (pending.now) { e.preventDefault(); begin(); }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  function begin() {
    const row = rowOf(pending.no);
    // grab: where on the row it was picked up, so the row follows the pointer without jumping
    drag = { no: pending.no, row, start: current(), grab: row ? pending.y - row.getBoundingClientRect().top : 0 };
    if (row) row.classList.add("dragging");
    $("slots").classList.add("sorting");
  }

  function onMove(ev) {
    if (!pending || ev.pointerId !== pending.id) return;
    if (!drag) {
      if (Math.abs((ev.clientY || 0) - pending.y) < THRESHOLD) return;
      begin();
    }
    ev.preventDefault();
    const list = $("slots"), mine = drag.row;
    if (!mine || mine.parentNode !== list) return;
    const all = rows().filter((r) => r !== mine);
    const others = all.filter(movable);
    let before = null;
    for (const r of others) { const top = layoutTop(r), h = r.getBoundingClientRect().height; if (ev.clientY < top + h / 2) { before = r; break; } }
    if (!before) before = all.find((r) => !movable(r)) || null;   // never past 自定义 1/2
    if (before !== mine.nextSibling) {
      const was = places();
      list.insertBefore(mine, before);
      glide(was, mine);   // the others slide out of the way
    }
    // the picked-up row follows the pointer
    if (!still()) {
      mine.style.transition = "none";
      mine.style.transform = `translateY(${(ev.clientY || 0) - drag.grab - layoutTop(mine)}px)`;
    }
  }

  function onUp(ev) {
    if (!pending || ev.pointerId !== pending.id) return;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    window.removeEventListener("pointercancel", onUp);
    pending = null;
    if (!drag) return;   // a plain click: the row opens its preset as usual
    const { no, start } = drag;
    const landed = places();   // the row lands from where the pointer left it
    if (drag.row) { drag.row.classList.remove("dragging"); drag.row.style.transform = ""; }
    $("slots").classList.remove("sorting");
    const order = shown();
    drag = null;
    swallow = true;      // the click that follows a drag must not also open the preset
    // A cancelled drag (the system took the pointer) or a list that is not the 12 presets once each:
    // nothing is committed, the column is redrawn in the order from before the drag.
    if (ev.type === "pointercancel" || !valid(order)) {
      if (!valid(order)) announce("拖动没有完成，已回到拖动前的顺序");
      ctx.redraw();
      render();
      glide(landed);
      return;
    }
    if (same(order, start)) { ctx.redraw(); glide(landed); return; }
    announce(`${ctx.label(no).name} 移到第 ${order.indexOf(no) + 1} 位`);
    commit(order, no, landed);
  }

  function onKey(e) {
    swallow = false;
    if (!ctx.supported() || !e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    const r = rowFrom(e.target);
    if (!movable(r)) return;
    e.preventDefault();
    move(Number(r.dataset.slot), e.key === "ArrowUp" ? -1 : 1);
  }

  function wire(c) {
    ctx = c;
    $("slots").addEventListener("pointerdown", onDown);
    $("slots").addEventListener("keydown", onKey);
    // The browser's own image drag would pick up a row's icon and fight the sorting drag.
    $("slots").addEventListener("dragstart", (e) => e.preventDefault());
    $("orderReset").addEventListener("click", () => { announce("已恢复原厂顺序"); commit(factory(), null, places()); });
  }

  // For the row's click handler: true once, right after a drag ended on it.
  function swallowClick() { const s = swallow; swallow = false; return s; }

  function describe(order) {
    const f = factory();
    const moved = order.filter((no, i) => f[i] !== no).length;
    return { moved, first: ctx.label(order[0]).name };
  }

  window.DarkslideOrder = { wire, render, grip, hint, dragging: () => !!drag, swallowClick, payload, fromServer, describe, current, valid };
})();
