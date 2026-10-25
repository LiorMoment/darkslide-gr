"use strict";
// First-use guide overlay and the "import passed" arrow.
// Positions are measured from the real controls, never hard-coded.

window.DarkslideGuide = (function () {
  const PAD = 6;
  const GAP = 16;
  const CARD_W = 360;
  // A step without a target is a centred card (the news card; badge is its corner mark). next/skip label the two buttons;
  // a step whose control is missing or hidden (narrow window, folded column) is skipped quietly.
  const FLOWS = {
    start: [
      { target: () => document.querySelector("#models .model.on"), title: "先选你的相机",
        text: "支持 GR III（固件 2.10）和 GR IIIx（固件 1.60）两个系列，GR IV 适配中。下面再选具体型号。" },
      { target: () => document.getElementById("drop"), title: "把官方固件拖到这里",
        text: "从理光官网下载对应系列的官方固件：GR III 是 fwdc239b.bin，GR IIIx 是 fwdc243b.bin。工具会核对是不是官方原件，文件只留在你的电脑上。" },
    ],
    // 下一版 (user 2026-10-25: 菜单排序 and 柔光 ship, for GR III and GR IIIx; 画幅 comes later). The
    // version number is not decided yet, so the corner says NEW until it is.
    news: [
      { label: "这一版新增", badge: "NEW", title: "这一版新加了这些",
        lines: ["菜单排序：在左栏按住预设上下拖，就是相机里影像控制菜单的顺序", "柔光：整台相机的开关，在相机里切关、弱、中、强，所有预设共用",
                "柔光页里有「菜单」和「ADJ 快捷键」两种用法的图示", "GR III 和 GR IIIx 都能用"],
        next: "带我看看", skip: "以后再说" },
    ],
    tour: [
      { target: () => document.getElementById("slots"), title: "按住拖动，排相机菜单顺序",
        text: "按住预设上下拖，就是相机影像控制菜单里的顺序。自定义 1、2 固定在最后；点「恢复原厂」可以还原。" },
      { target: () => document.getElementById("featSoft"), title: "柔光",
        text: "像柔光镜一样让亮处晕开。在相机里切关、弱、中、强，所有预设共用一个档位。", next: "知道了" },
    ],
    editor: [
      { target: () => document.getElementById("slots"), title: "先选一个预设",
        text: "12 个内置预设都能改，也能按住拖动排顺序；标准只能换图标，自定义 1、2 不能改。" },
      { target: () => document.getElementById("iconField"), title: "点图标就能换",
        text: "名字在下面改，只能用英文；右边的数字是还能写几个字。" },
      { target: () => document.getElementById("filmField"), title: "点一卷胶片当起点",
        text: "预览会马上变。「＋ 添加」可以导入自己的 LUT 或 Lightroom 预设。只有银流冲洗、怀旧、HDR 色调、交叉冲洗这 4 个预设能把胶片写进相机。" },
      { target: () => document.getElementById("adjustPanel"), title: "在胶片基础上微调",
        text: "标「可写进相机」的会一起写进相机；标「仅预览/导出」的只进导出的 .cube。装了胶片的预设和「标准」共用相机里那组调节值，比较胶片前先把「标准」的调节归零。" },
      { target: () => document.querySelector("#paneIC .v1-out"), title: "看相机能还原多少",
        text: "近似程度「高」就放心生成；用了「仅预览/导出」的调整会变低。也可以只导出 .cube，或生成配方卡。", next: "知道了" },
    ],
  };

  let layer, spot, card, flow, index, onEnd, arrowEl, arrowTarget, arrowWired = false;

  function build() {
    if (layer) return;
    layer = document.createElement("div");
    layer.className = "guide-layer";
    layer.hidden = true;
    spot = document.createElement("button");
    spot.className = "guide-spot";
    spot.setAttribute("aria-label", "关闭指引");
    spot.addEventListener("click", () => finish(false));
    card = document.createElement("div");
    card.className = "guide-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.innerHTML =
      '<div class="guide-bar"></div><div class="guide-body">' +
      '<div class="guide-head"><span class="mono guide-label"></span><span class="mono guide-num"></span></div>' +
      '<div class="guide-title"></div><div class="guide-text"></div>' +
      '<div class="guide-actions"><button class="guide-next">知道了</button><button class="guide-skip">不再提示</button></div></div>';
    card.querySelector(".guide-next").addEventListener("click", next);
    card.querySelector(".guide-skip").addEventListener("click", () => {
      const step = FLOWS[flow][index];
      finish(!step.skip, "skip");  // a custom skip label (e.g. 以后再说) only closes this card
    });
    layer.append(spot, card);
    document.body.append(layer);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    // Cards rise into place (CSS animation): measure again once they have landed, or the frame sits 8px low.
    document.addEventListener("animationend", () => { if (!layer.hidden) place(); }, true);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !layer.hidden) finish(false);
      if (e.key === "Escape" && arrowEl) hideArrow();
    });
  }

  function start(name, done) {
    build();
    flow = name;
    index = 0;
    onEnd = done;
    layer.hidden = false;
    show();
  }

  function visible(el) {
    if (!el || el.closest("[hidden]")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 4 && r.height > 4;
  }

  function show() {
    const step = FLOWS[flow][index];
    const el = step.target ? step.target() : null;
    if (step.target && !visible(el)) { next(); return; }
    if (el) el.scrollIntoView({ block: "center" });
    const total = FLOWS[flow].length, n = index + 1;
    card.querySelector(".guide-label").textContent = step.label || (total > 1 ? "指引 " + n + " / " + total : "指引");
    card.querySelector(".guide-num").textContent = step.badge || String(n).padStart(2, "0");
    card.querySelector(".guide-title").textContent = step.title;
    const text = card.querySelector(".guide-text");
    if (step.lines) {
      text.replaceChildren(...step.lines.map((t) => { const li = document.createElement("div"); li.className = "guide-line"; li.textContent = "· " + t; return li; }));
    } else {
      text.textContent = step.text;
    }
    card.querySelector(".guide-next").textContent = step.next || (n === total ? "知道了" : "下一步");
    card.querySelector(".guide-skip").textContent = step.skip || "不再提示";
    card.classList.remove("in");
    place();
    setTimeout(() => { if (!layer.hidden) place(); }, 450);  // after any entry animation of the target
    void card.offsetWidth;
    card.classList.add("in");
    card.querySelector(".guide-next").focus({ preventScroll: true });
  }

  function place() {
    if (layer && !layer.hidden) {
      const step = FLOWS[flow][index];
      if (!step.target) {
        Object.assign(spot.style, { left: "50%", top: "50%", width: "0px", height: "0px" });
        const ch = card.offsetHeight || 260;
        Object.assign(card.style, { left: Math.max(GAP, (window.innerWidth - CARD_W) / 2) + "px",
                                    top: Math.max(GAP, (window.innerHeight - ch) / 2) + "px" });
        placeArrow();
        return;
      }
      const el = step.target();
      if (!el) return;
      const r = el.getBoundingClientRect();
      const s = { x: r.left - PAD, y: r.top - PAD, w: r.width + PAD * 2, h: r.height + PAD * 2 };
      // keep the whole frame inside the window (targets at the very edge)
      const vwin = window.innerWidth, vhin = window.innerHeight;
      if (s.x < 2) { s.w -= 2 - s.x; s.x = 2; }
      if (s.y < 2) { s.h -= 2 - s.y; s.y = 2; }
      if (s.x + s.w > vwin - 2) s.w = vwin - 2 - s.x;
      if (s.y + s.h > vhin - 2) s.h = vhin - 2 - s.y;
      Object.assign(spot.style, { left: s.x + "px", top: s.y + "px", width: s.w + "px", height: s.h + "px" });
      const vw = window.innerWidth, vh = window.innerHeight;
      const ch = card.offsetHeight || 220;
      const space = { right: vw - (s.x + s.w), left: s.x, below: vh - (s.y + s.h), above: s.y };
      let x, y;
      if (space.right >= CARD_W + GAP * 2) { x = s.x + s.w + GAP; y = s.y; }
      else if (space.left >= CARD_W + GAP * 2) { x = s.x - CARD_W - GAP; y = s.y; }
      else if (space.below >= ch + GAP * 2) { x = s.x; y = s.y + s.h + GAP; }
      else if (space.above >= ch + GAP * 2) { x = s.x; y = s.y - ch - GAP; }
      else { x = (vw - CARD_W) / 2; y = vh - ch - GAP * 2; }
      x = Math.max(GAP, Math.min(x, vw - CARD_W - GAP));
      y = Math.max(GAP, Math.min(y, vh - ch - GAP));
      Object.assign(card.style, { left: x + "px", top: y + "px" });
    }
    placeArrow();
  }

  function next() {
    index += 1;
    if (index >= FLOWS[flow].length) finish(false, "next");
    else show();
  }

  // forever: the user asked never to see guides again ("不再提示"). how: "next" | "skip" | "close".
  function finish(forever, how) {
    if (!layer || layer.hidden) return;
    layer.hidden = true;
    const cb = onEnd;
    onEnd = null;
    if (cb) cb(forever, how || "close");
  }

  // ------------------------------------------------ import-passed arrow
  function showArrow(target) {
    hideArrow();
    if (!arrowWired) {  // keep the arrow on its target while the page or a column scrolls
      arrowWired = true;
      window.addEventListener("resize", placeArrow);
      window.addEventListener("scroll", placeArrow, true);
    }
    target.scrollIntoView({ block: "nearest" });
    arrowTarget = target;
    arrowEl = document.createElement("div");
    arrowEl.className = "guide-arrow";
    // Only the small label; no drawn arrow (user, 2026-10-16).
    arrowEl.innerHTML = '<span class="mono guide-arrow-label">导入通过 · 点击下一步</span>';
    document.body.append(arrowEl);
    target.classList.add("guide-target");
    // the arrow floats over the page: it never pushes the layout (no extra room is made for it)
    placeArrow();
    setTimeout(() => document.addEventListener("click", hideArrow, { once: true, capture: true }), 0);
  }

  function placeArrow() {
    // label centred under the target (the space under 下一步 is empty); above it only when there is no room below
    if (!arrowEl || !arrowTarget) return;
    const r = arrowTarget.getBoundingClientRect();
    const w = arrowEl.offsetWidth, h = arrowEl.offsetHeight;
    const below = r.bottom + h + 6 <= window.innerHeight - 8;
    arrowEl.classList.toggle("up", below);
    const x = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 8));
    const y = below ? r.bottom + 6 : Math.max(8, r.top - h - 6);
    Object.assign(arrowEl.style, { left: x + "px", top: y + "px" });
  }

  function hideArrow() {
    if (arrowTarget) {
      arrowTarget.classList.remove("guide-target");
    }
    if (arrowEl) arrowEl.remove();
    arrowEl = null;
    arrowTarget = null;
  }

  return { start, showArrow, hideArrow, active: () => !!layer && !layer.hidden };
})();
