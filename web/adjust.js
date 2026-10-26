"use strict";
// Right-hand 调整 column of the 1.0 workbench: white balance, tone, curve, colour and
// HSL. Values live in the project (changes[slot].grade). They shape the preview and the
// exported .cube; writing them into the camera waits for the fitting step.
(function () {
  const $ = (id) => document.getElementById(id);
  const HUES = [["红", "#E0524A", 0], ["橙", "#E8913C", 30], ["黄", "#E3C94A", 60], ["绿", "#6CB86A", 120],
                ["青", "#4AB3B0", 180], ["蓝", "#4A7EE0", 240], ["紫", "#8A5ED0", 270], ["品", "#D05AA8", 300]];
  const GROUPS = [
    { title: "冷暖偏色", cam: true, sliders: [["temp", "冷暖"], ["tint", "绿—品红"]] },  // this preset's own colour cast, not the camera's white balance
    { title: "影调", cam: true, sliders: [["exp", "曝光"], ["con", "对比度"], ["hi", "高光"], ["sh", "阴影"], ["white", "白色"], ["black", "黑色"], ["fade", "褪色"]] },
    { title: "曲线", cam: true, curve: true, sliders: [] },
    { title: "色彩", cam: true, sliders: [["sat", "饱和度"], ["splitHi", "高光着色"], ["splitSh", "阴影着色"]] },
    { title: "HSL 分色", cam: false, hsl: true, sliders: [["hH", "色相"], ["hS", "饱和"], ["hL", "明度"], ["vib", "自然饱和"]],
      note: "只改某一种颜色，相机做不到；导出 LUT 保留，写进相机会丢掉。" },
  ];
  let hooks = null, ui = { ch: "all", hue: 5, drag: null };

  // ---- camera bake begin
  // Everything between these markers decides the LUT that is fitted into the camera
  // (combine with camera=true). Its hash, not the whole file's, is what the slider-range
  // table and the user-LUT range caches are tied to (darkslide/grade_bake.page_bake_sha), so
  // wording and layout below can change freely. HUES (above) only feeds the HSL path, which
  // camera=true leaves out. Mirrored by darkslide/grade_bake.py (tests/test_grade_bake.py).
  const SCALAR = ["temp", "tint", "exp", "con", "hi", "sh", "white", "black", "fade", "sat", "splitHi", "splitSh", "vib"];

  const empty = () => ({});
  const val = (g, k) => (g && typeof g[k] === "number" ? g[k] : 0);
  const curvePts = (g, ch) => (g && g.curve && g.curve[ch]) || [[0, 0], [1, 1]];
  const hslArr = (g, k) => (g && g[k]) || [0, 0, 0, 0, 0, 0, 0, 0];

  function isEmpty(g) {
    if (!g) return true;
    if (SCALAR.some((k) => val(g, k))) return false;
    if (["hH", "hS", "hL"].some((k) => hslArr(g, k).some((v) => v))) return false;
    return !(g.curve && Object.values(g.curve).some((p) => p.length > 2 || p[0][1] !== 0 || p[1][1] !== 1));
  }
  const lutOnly = (g) => !!g && (val(g, "vib") !== 0 || ["hH", "hS", "hL"].some((k) => hslArr(g, k).some((v) => v)));

  // ---------------------------------------------------------------- colour math
  const dec = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const enc = (c) => { c = Math.min(1, Math.max(0, c)); return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; };
  const cbrt = (x) => Math.sign(x) * Math.pow(Math.abs(x), 1 / 3);
  function oklab(r, g, b) {
    const l = cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m = cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s = cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
  }
  function linear(L, a, b) {
    const l = Math.pow(L + 0.3963377774 * a + 0.2158037573 * b, 3), m = Math.pow(L - 0.1055613458 * a - 0.0638541728 * b, 3);
    const s = Math.pow(L - 0.0894841775 * a - 1.291485548 * b, 3);
    return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
  }
  const smooth = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };

  // Natural cubic spline through the points, flat outside them.
  function spline(pts) {
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]), n = xs.length, m = new Array(n).fill(0);
    if (n > 2) {
      const h = xs.slice(1).map((x, i) => x - xs[i]), a = [0], b = [1], c = [0], d = [0];
      for (let i = 1; i < n - 1; i++) { a.push(h[i - 1]); b.push(2 * (h[i - 1] + h[i])); c.push(h[i]); d.push(6 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1])); }
      a.push(0); b.push(1); c.push(0); d.push(0);
      for (let i = 1; i < n; i++) { const w = a[i] / b[i - 1]; b[i] -= w * c[i - 1]; d[i] -= w * d[i - 1]; }
      m[n - 1] = d[n - 1] / b[n - 1];
      for (let i = n - 2; i >= 0; i--) m[i] = (d[i] - c[i] * m[i + 1]) / b[i];
    }
    return (x) => {
      if (x <= xs[0]) return ys[0];
      if (x >= xs[n - 1]) return ys[n - 1];
      let i = 0; while (i < n - 2 && xs[i + 1] < x) i++;
      const hh = xs[i + 1] - xs[i], t0 = xs[i + 1] - x, t1 = x - xs[i];
      return (m[i] * t0 ** 3 + m[i + 1] * t1 ** 3) / (6 * hh) + (ys[i] / hh - m[i] * hh / 6) * t0 + (ys[i + 1] / hh - m[i + 1] * hh / 6) * t1;
    };
  }

  function bands(hue) {
    for (let i = 0; i < 8; i++) {
      const lo = HUES[i][2], hi = i < 7 ? HUES[i + 1][2] : 360;
      if (hue >= lo && hue < hi) { const w = 0.5 - 0.5 * Math.cos(Math.PI * (hue - lo) / (hi - lo)); return [[i, 1 - w], [(i + 1) % 8, w]]; }
    }
    return [[0, 1], [1, 0]];
  }

  // A function rgb(0..1 encoded) -> rgb, for the given adjustments. camera=true drops LUT-only parts.
  function transform(g, camera) {
    const t = val(g, "temp") / 100, ti = val(g, "tint") / 100, ev = Math.pow(2, val(g, "exp") / 50);
    const gr = Math.pow(2, 0.3 * t), gg = Math.pow(2, -0.2 * ti), gb = Math.pow(2, -0.3 * t), gn = 0.2126 * gr + 0.7152 * gg + 0.0722 * gb;
    const c = val(g, "con") / 100, hi = val(g, "hi") / 100, sh = val(g, "sh") / 100, wh = val(g, "white") / 100, bk = val(g, "black") / 100, fade = val(g, "fade") / 100;
    const tone = (x) => {
      let y = x + 0.22 * c * 4 * x * (1 - x) * (2 * x - 1);
      if (x > 0.4) y += 0.15 * hi * Math.sin(Math.PI * (x - 0.4) / 0.6) ** 2;
      if (x < 0.6) y += 0.15 * sh * Math.sin(Math.PI * x / 0.6) ** 2;
      y += 0.12 * wh * x ** 4 + 0.12 * bk * (1 - x) ** 4;
      return Math.min(1, Math.max(0, fade > 0 ? 0.15 * fade + y * (1 - 0.15 * fade) : y));
    };
    const master = spline(curvePts(g, "all")), chan = ["r", "g", "b"].map((k) => spline(curvePts(g, k)));
    const sat = 1 + val(g, "sat") / 100, vib = camera ? 0 : val(g, "vib") / 100;
    const sHi = val(g, "splitHi") / 100, sSh = val(g, "splitSh") / 100;
    const hH = camera ? null : hslArr(g, "hH"), hS = camera ? null : hslArr(g, "hS"), hL = camera ? null : hslArr(g, "hL");
    const useHsl = hH && [hH, hS, hL].some((a) => a.some((v) => v));
    return (rgb) => {
      let [r, gg2, b] = rgb.map(dec);
      r *= gr / gn * ev; gg2 *= gg / gn * ev; b *= gb / gn * ev;
      const y = 0.2126 * r + 0.7152 * gg2 + 0.0722 * b;
      const y2 = dec(tone(enc(y)));
      if (y > 1e-6) { r *= y2 / y; gg2 *= y2 / y; b *= y2 / y; } else { r = gg2 = b = y2; }
      let [L, A, B] = oklab(Math.max(0, r), Math.max(0, gg2), Math.max(0, b));
      let f = sat;
      if (vib) f *= 1 + vib * (1 - Math.min(1, Math.hypot(A, B) / 0.2));
      A *= f; B *= f;
      // highlight / shadow toning: + warm (orange), - cool (teal)
      const wHi = smooth(0.55, 0.85, L), wSh = 1 - smooth(0.25, 0.5, L);
      A += 0.04 * (sHi * wHi + sSh * wSh) * 0.45; B += 0.04 * (sHi * wHi + sSh * wSh);
      let out = linear(L, A, B).map(enc);
      out = out.map((v) => master(v));
      out = out.map((v, i) => chan[i](v));
      if (useHsl) {
        const mx = Math.max(...out), mn = Math.min(...out), d = mx - mn, s = mx ? d / mx : 0;
        let hue = 0;
        if (d) hue = mx === out[0] ? ((out[1] - out[2]) / d) % 6 : mx === out[1] ? (out[2] - out[0]) / d + 2 : (out[0] - out[1]) / d + 4;
        hue = (hue * 60 + 360) % 360;
        const bw = bands(hue);
        const dh = bw.reduce((a, [i, w]) => a + w * hH[i] / 100 * 25, 0), ds = 1 + bw.reduce((a, [i, w]) => a + w * hS[i] / 100, 0);
        const dl = Math.pow(2, 0.6 * s * bw.reduce((a, [i, w]) => a + w * hL[i] / 100, 0));
        const h2 = ((hue + dh) % 360 + 360) % 360, s2 = Math.min(1, s * Math.max(0, ds)), v = mx;
        const k = (n) => (n + h2 / 60) % 6, fC = (n) => v - v * s2 * Math.max(0, Math.min(k(n), 4 - k(n), 1));
        out = [fC(5), fC(3), fC(1)].map((x) => enc(dec(x) * dl));
      }
      return out.map((v) => Math.min(1, Math.max(0, v)));
    };
  }

  // Combine an optional base LUT ({size, data}) with the adjustments into a new LUT.
  function combine(base, g, camera, size) {
    const fn = isEmpty(g) ? null : transform(g, camera);
    const n = size || (base ? base.size : 17), m = n - 1, data = new Float32Array(n * n * n * 3);
    const look = base ? (rgb) => sample(base, rgb) : (rgb) => rgb;
    let k = 0;
    for (let b = 0; b < n; b++) for (let gi = 0; gi < n; gi++) for (let r = 0; r < n; r++) {
      let v = look([r / m, gi / m, b / m]);
      if (fn) v = fn(v);
      data[k++] = v[0]; data[k++] = v[1]; data[k++] = v[2];
    }
    return { size: n, data };
  }

  function sample(lut, rgb) {
    const n = lut.size, d = lut.data, m = n - 1;
    const at = (r, g, b, c) => d[((b * n + g) * n + r) * 3 + c];
    const f = rgb.map((v) => Math.min(1, Math.max(0, v)) * m);
    const i0 = f.map((v) => Math.min(m - 1, Math.floor(v))), t = f.map((v, i) => v - i0[i]);
    const out = [0, 0, 0];
    for (let c = 0; c < 3; c++) {
      const c00 = at(i0[0], i0[1], i0[2], c) * (1 - t[0]) + at(i0[0] + 1, i0[1], i0[2], c) * t[0];
      const c10 = at(i0[0], i0[1] + 1, i0[2], c) * (1 - t[0]) + at(i0[0] + 1, i0[1] + 1, i0[2], c) * t[0];
      const c01 = at(i0[0], i0[1], i0[2] + 1, c) * (1 - t[0]) + at(i0[0] + 1, i0[1], i0[2] + 1, c) * t[0];
      const c11 = at(i0[0], i0[1] + 1, i0[2] + 1, c) * (1 - t[0]) + at(i0[0] + 1, i0[1] + 1, i0[2] + 1, c) * t[0];
      out[c] = (c00 * (1 - t[1]) + c10 * t[1]) * (1 - t[2]) + (c01 * (1 - t[1]) + c11 * t[1]) * t[2];
    }
    return out;
  }

  // ---- camera bake end

  // ---------------------------------------------------------------- panel
  // writable: whether this preset can take colour into the camera (the four film presets).
  function render(slot, g, note, writable) {
    const body = $("adjustBody");
    const blocks = GROUPS.map((grp) => {
      const box = document.createElement("div");
      box.className = "adj-group";
      const head = document.createElement("div");
      head.className = "adj-head";
      head.innerHTML = '<span class="adj-title"></span><span class="adj-tag"></span>';
      head.children[0].textContent = grp.title;
      head.children[1].textContent = grp.cam && writable ? "可写进相机" : "仅预览/导出";
      head.children[1].classList.toggle("lut", !grp.cam);
      box.append(head);
      if (grp.curve) box.append(curveEditor(g));
      const limits = grp.cam && writable && hooks && hooks.limits ? hooks.limits() : null;
      for (const [key, label] of grp.sliders) box.append(slider(g, key, label, !grp.cam, limits && limits[key], limits && limits._subject));
      if (grp.hsl) {
        const dots = document.createElement("div");
        dots.className = "adj-hues";
        HUES.forEach(([name, color], i) => {
          const b = document.createElement("button");
          b.type = "button"; b.className = "adj-hue" + (ui.hue === i ? " on" : ""); b.style.background = color;
          b.setAttribute("aria-label", name); b.title = name;
          b.addEventListener("click", () => { ui.hue = i; hooks.rerender(); });
          dots.append(b);
        });
        box.append(dots);
      }
      if (grp.note) { const n = document.createElement("span"); n.className = "adj-note"; n.textContent = grp.note; box.append(n); }
      return box;
    });
    // user, 2026-10-17: no standing explanation for the presets that write into the camera.
    if (!writable) note = "这个预设只能预览和导出 LUT，右边的调整不会写进相机。能写进相机的是银流冲洗、怀旧、HDR 色调、交叉冲洗。";
    if (note) { const n = document.createElement("p"); n.className = "adj-note adj-from" + (writable ? "" : " adj-warn"); n.textContent = note; blocks.unshift(n); }
    body.replaceChildren(...blocks);
  }

  // range: [min, max] this slider can reach on its own with the current film and still go into
  // the camera (precomputed per film); shown as a thin strip, red where it would not fit.
  function slider(g, key, label, lut, range, subject) {
    const row = document.createElement("label");
    row.className = "adj-row" + (lut ? " lut" : "");
    const hsl = ["hH", "hS", "hL"].includes(key);
    const v = hsl ? hslArr(g, key)[ui.hue] : val(g, key);
    const name = document.createElement("span"); name.className = "adj-label"; name.textContent = label;
    const input = document.createElement("input");
    Object.assign(input, { type: "range", min: -100, max: 100, step: 1, value: v });
    input.setAttribute("aria-label", hsl ? HUES[ui.hue][0] + " " + label : label);
    const shown = document.createElement("span"); shown.className = "adj-val mono"; shown.textContent = (v > 0 ? "+" : "") + v;
    input.addEventListener("input", () => { shown.textContent = (+input.value > 0 ? "+" : "") + input.value; });
    input.addEventListener("change", () => update((n) => {
      if (hsl) { const arr = hslArr(n, key).slice(); arr[ui.hue] = +input.value; n[key] = arr; } else n[key] = +input.value;
    }));
    input.addEventListener("dblclick", () => update((n) => { if (hsl) { const arr = hslArr(n, key).slice(); arr[ui.hue] = 0; n[key] = arr; } else n[key] = 0; }));
    let track = input;
    if (range) {
      const [lo, hi, rough] = range, pct = (x) => ((x + 100) / 2).toFixed(1) + "%";  // rough: an estimate still being worked out
      const zone = document.createElement("span");
      zone.className = "adj-zone" + (rough ? " rough" : "");
      zone.style.background = "linear-gradient(to right, var(--zone-bad) 0 " + pct(lo) + ", var(--zone-ok) " + pct(lo) + " " + pct(hi) +
        ", var(--zone-bad) " + pct(hi) + " 100%)";
      track = document.createElement("span");
      track.className = "adj-track";
      track.append(input, zone);
      const span = lo + "～" + (hi > 0 ? "+" : "") + hi;
      const tip = rough ? "粗略估算（只看黑色和白色的位置，正在后台算准）：" + label + "单独拉时大约能写到 " + span + "；以下面的自动检查为准。"
        : (subject || "这卷胶片") + "上，" + label + "单独拉时能写进相机的范围是 " + span + "；几个一起拉时以下面的自动检查为准。";
      row.title = tip;
      const mark = (x) => { const out = x < lo || x > hi; row.classList.toggle("over", out); shown.title = out ? "超出能写进相机的范围" : ""; };
      mark(v);
      input.addEventListener("input", () => mark(+input.value));
    }
    row.append(name, track, shown);
    return row;
  }

  function curveEditor(g) {
    const wrap = document.createElement("div");
    wrap.className = "adj-curve";
    const chans = document.createElement("div");
    chans.className = "adj-chans";
    for (const [k, label] of [["all", "RGB"], ["r", "R"], ["g", "G"], ["b", "B"]]) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "mono" + (ui.ch === k ? " on" : ""); b.textContent = label;
      b.addEventListener("click", () => { ui.ch = k; hooks.rerender(); });
      chans.append(b);
    }
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("class", "adj-svg");
    svg.setAttribute("aria-label", "曲线：点空白处加点，拖动调整，双击删除");
    const pts = curvePts(g, ui.ch), f = spline(pts);
    const color = { all: "var(--text)", r: "#F08A6C", g: "#8FC48A", b: "#7FA7E0" }[ui.ch];
    let d = "";
    for (let i = 0; i <= 50; i++) { const x = i / 50; d += (i ? "L" : "M") + (x * 100).toFixed(1) + " " + ((1 - Math.min(1, Math.max(0, f(x)))) * 100).toFixed(1); }
    svg.innerHTML = '<path d="M25 0V100M50 0V100M75 0V100M0 25H100M0 50H100M0 75H100" style="stroke:var(--curve-grid)" stroke-width="0.6" fill="none" vector-effect="non-scaling-stroke"/>' +
      '<path d="M0 100 L100 0" style="stroke:var(--curve-grid)" stroke-width="0.6" fill="none" vector-effect="non-scaling-stroke"/>';
    const path = document.createElementNS(NS, "path");
    Object.entries({ d, "stroke-width": "2", fill: "none", "vector-effect": "non-scaling-stroke" }).forEach(([k, v]) => path.setAttribute(k, v));
    path.style.stroke = color;  // style, not the attribute: the colour can be a theme variable
    svg.append(path);
    pts.forEach((p, i) => {
      const c = document.createElementNS(NS, "circle");
      Object.entries({ cx: p[0] * 100, cy: (1 - p[1]) * 100, r: 2.4 }).forEach(([k, v]) => c.setAttribute(k, v));
      c.style.fill = "var(--text)";
      c.dataset.i = i;
      svg.append(c);
    });
    const at = (e) => { const r = svg.getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, 1 - (e.clientY - r.top) / r.height))]; };
    svg.addEventListener("pointerdown", (e) => {
      const p = at(e);
      let i = e.target.dataset && e.target.dataset.i !== undefined ? +e.target.dataset.i : -1;
      let list = pts.map((q) => q.slice());
      if (i < 0) { list.push(p); list.sort((a, b) => a[0] - b[0]); i = list.findIndex((q) => q[0] === p[0] && q[1] === p[1]); }
      ui.drag = { list, i };
      svg.setPointerCapture(e.pointerId);
    });
    svg.addEventListener("pointermove", (e) => {
      if (!ui.drag) return;
      const { list, i } = ui.drag, p = at(e);
      const lo = i > 0 ? list[i - 1][0] + 0.01 : 0, hi = i < list.length - 1 ? list[i + 1][0] - 0.01 : 1;
      list[i] = [i === 0 ? 0 : i === list.length - 1 ? 1 : Math.min(hi, Math.max(lo, p[0])), p[1]];
      const ff = spline(list);
      let dd = "";
      for (let k = 0; k <= 50; k++) { const x = k / 50; dd += (k ? "L" : "M") + (x * 100).toFixed(1) + " " + ((1 - Math.min(1, Math.max(0, ff(x)))) * 100).toFixed(1); }
      path.setAttribute("d", dd);
    });
    svg.addEventListener("pointerup", () => {
      if (!ui.drag) return;
      const list = ui.drag.list; ui.drag = null;
      update((n) => { n.curve = Object.assign({}, n.curve || {}, { [ui.ch]: list.map((q) => [round(q[0]), round(q[1])]) }); });
    });
    svg.addEventListener("dblclick", (e) => {
      const i = e.target.dataset && e.target.dataset.i !== undefined ? +e.target.dataset.i : -1;
      if (i <= 0 || i >= pts.length - 1) return;
      update((n) => { const list = pts.filter((_, k) => k !== i); n.curve = Object.assign({}, n.curve || {}, { [ui.ch]: list }); });
    });
    wrap.append(chans, svg);
    return wrap;
  }
  const round = (x) => Math.round(x * 1000) / 1000;

  function update(fn) {
    const current = JSON.parse(JSON.stringify(hooks.get() || {}));
    fn(current);
    hooks.set(isEmpty(current) ? undefined : current);
  }

  function wire(appHooks) {
    hooks = appHooks;
    $("adjReset").addEventListener("click", () => hooks.set(undefined));
  }

  // Plain-language list of what was changed, for the recipe card: [{ text, lutOnly }].
  function describe(g) {
    if (isEmpty(g)) return [];
    const out = [], sign = (v) => (v > 0 ? "+" : "−") + Math.abs(v);
    for (const grp of GROUPS) {
      if (grp.curve) {
        const chans = [["all", "RGB"], ["r", "R"], ["g", "G"], ["b", "B"]].filter(([k]) => {
          const p = curvePts(g, k);
          return p.length > 2 || p[0][1] !== 0 || p[p.length - 1][1] !== 1;
        });
        if (chans.length) out.push({ text: "曲线：" + chans.map(([k, n]) => n + " " + curvePts(g, k).length + " 点").join("、"), lutOnly: false });
        continue;
      }
      if (grp.hsl) {
        HUES.forEach(([name], i) => {
          const parts = [["hH", "色相"], ["hS", "饱和"], ["hL", "明度"]].filter(([k]) => hslArr(g, k)[i]).map(([k, n]) => n + " " + sign(hslArr(g, k)[i]));
          if (parts.length) out.push({ text: name + "：" + parts.join(" "), lutOnly: true });
        });
        if (val(g, "vib")) out.push({ text: "自然饱和 " + sign(val(g, "vib")), lutOnly: true });
        continue;
      }
      const parts = grp.sliders.filter(([k]) => val(g, k)).map(([k, n]) => n + " " + sign(val(g, k)));
      if (parts.length) out.push({ text: grp.title + "：" + parts.join("，"), lutOnly: false });
    }
    return out;
  }

  window.DarkslideAdjust = { wire, render, combine, isEmpty, lutOnly, empty, describe };
})();
