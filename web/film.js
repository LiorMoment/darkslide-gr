"use strict";
// Film picker inside 影像控制: the four replaceable presets choose a built-in film or
// one of the project's own LUTs. Previews use approximate LUTs on the user's own sample
// photo, decoded in this tab; nothing is uploaded. Writing into the camera is decided by
// the local writer's status (off until it has been tested on a camera).
(function () {
  const $ = (id) => document.getElementById(id);
  const SLOTS = [8, 9, 10, 11];
  const PREVIEW_SIDE = 1600;
  const film = { catalog: null, writer: null, luts: [], recipes: [], pid: null, cubes: {}, graded: {}, source: null, busy: {}, camFail: {}, fitting: false, outside: null,
                 gradeLimits: null, live: null, ranges: {}, curveEnds: null, pull: null, estimates: {}, lastGrade: {}, asked: {}, retried: {}, checking: null, checked: {} };
  let api = null, toast = null, hooks = null, loadEpoch = 0;

  function parseCube(text) {
    const lines = text.split("\n");
    const size = parseInt((lines.find((l) => l.startsWith("LUT_3D_SIZE")) || "").split(/\s+/)[1], 10);
    const data = new Float32Array(size * size * size * 3);
    let k = 0;
    for (const l of lines) {
      if (!/^[0-9.\-+]/.test(l.trim())) continue;
      const v = l.trim().split(/\s+/);
      data[k++] = +v[0]; data[k++] = +v[1]; data[k++] = +v[2];
    }
    return { size, data };
  }

  function grade_(src, lut) {
    const out = new ImageData(src.width, src.height);
    const n = lut.size, d = lut.data, m = n - 1;
    const at = (r, g, b, c) => d[((b * n + g) * n + r) * 3 + c];
    for (let i = 0; i < src.data.length; i += 4) {
      const fr = src.data[i] / 255 * m, fg = src.data[i + 1] / 255 * m, fb = src.data[i + 2] / 255 * m;
      const r0 = Math.min(m - 1, fr | 0), g0 = Math.min(m - 1, fg | 0), b0 = Math.min(m - 1, fb | 0);
      const tr = fr - r0, tg = fg - g0, tb = fb - b0;
      for (let c = 0; c < 3; c++) {
        const c00 = at(r0, g0, b0, c) * (1 - tr) + at(r0 + 1, g0, b0, c) * tr;
        const c10 = at(r0, g0 + 1, b0, c) * (1 - tr) + at(r0 + 1, g0 + 1, b0, c) * tr;
        const c01 = at(r0, g0, b0 + 1, c) * (1 - tr) + at(r0 + 1, g0, b0 + 1, c) * tr;
        const c11 = at(r0, g0 + 1, b0 + 1, c) * (1 - tr) + at(r0 + 1, g0 + 1, b0 + 1, c) * tr;
        out.data[i + c] = Math.min(1, Math.max(0, (c00 * (1 - tg) + c10 * tg) * (1 - tb) + (c01 * (1 - tg) + c11 * tg) * tb)) * 255 + 0.5;
      }
      out.data[i + 3] = 255;
    }
    return out;
  }

  const isUser = (id) => /^user_lut_[0-9a-f]{12}$/.test(id || "");

  const isRecipe = (id) => /^user_lut_photo_[0-9a-f]{32}$/.test(id || "");
  const recipeUrl = (id) => "/api/projects/" + film.pid + "/photo-recipes/" + id;

  function nameOf(id) {
    if (!id) return "";
    const saved = film.recipes.find((x) => x.id === id);
    if (saved) return "照片配方 · " + (saved.photo_snapshot.original_id || "胶片");
    const lut = film.luts.find((x) => x.id === id);
    if (lut) return lut.name;
    const f = film.catalog && film.catalog.films.find((x) => x.id === id);
    return f ? f.name : id;
  }

  async function load(pid) {
    const epoch = ++loadEpoch;
    film.pid = pid;
    for (const url of Object.values(film.graded)) URL.revokeObjectURL(url);
    film.busy = {};
    film.graded = {}; film.outside = null; film.luts = []; film.recipes = []; film.cubes = {}; film.capabilities = null; film.camFail = {};
    film.gradeLimits = null; film.live = null; film.ranges = {}; film.curveEnds = null; film.pull = null; film.estimates = {}; film.lastGrade = {}; film.asked = {}; film.retried = {}; film.fitReady = null; film.checking = null; film.checked = {}; clearTimeout(liveTimer);
    if (hooks.invalidateCheck) hooks.invalidateCheck();
    const valid = () => film.pid === pid && loadEpoch === epoch;
    try {
      {
        const cat = await api("GET", "/api/film-catalog");
        if (!valid()) return;
        if (!film.catalog || film.catalog.data_revision !== cat.data_revision) film.cubes = {};
        film.catalog = cat; film.writer = cat.writer;
      }
      const result = await api("GET", "/api/projects/" + pid + "/film-luts");
      if (!valid()) return;
      film.luts = result.luts; film.capabilities = result.capabilities;
      if (film.capabilities && film.capabilities.photo_recipe_read) {
        const recipes = await api("GET", "/api/projects/" + pid + "/photo-recipes");
        if (!valid()) return;
        film.recipes = recipes.recipes;
      }
    } catch (e) { if (valid()) toast("胶片列表读取失败：" + e.message, true); }
    if (!valid()) return;
    hooks.rerender();
    try {
      const result = await api("GET", "/api/projects/" + pid + "/film-check");
      if (!valid()) return;
      film.outside = result.films; hooks.rerender();
    } catch (e) { if (valid()) film.outside = null; }
    try {
      const limits = await api("GET", "/api/projects/" + pid + "/grade-limits");
      if (!valid()) return;
      film.gradeLimits = limits.valid ? limits.limits : null; film.curveEnds = limits.curve_ends || null; hooks.rerender();
    } catch (e) { if (valid()) film.gradeLimits = null; }
    try {
      const setup = await api("GET", "/api/fitting-setup");
      if (!valid()) return;
      film.fitReady = setup.state === "ready"; hooks.rerender();
    } catch (e) { if (valid()) film.fitReady = false; }
  }

  // ---------------------------------------------------------------- write-range check (grade limits)
  // How far each camera-writable slider can go on its own: precomputed for built-in films,
  // computed once in the background for the user's own LUTs (cached on the server).
  function gradeLimitsFor(id) {
    if (!id || isRecipe(id)) return null;
    if (isUser(id)) return userLimits(id);
    if (!film.gradeLimits) return null;
    const t = film.gradeLimits[id];
    return t && t._film_writable !== false ? t : null;
  }
  // A user LUT: exact ranges once worked out (slider by slider as they arrive), and until then a
  // rough estimate drawn dashed ([lo, hi, true]), computed in the page in a few milliseconds.
  function userLimits(id) {
    const r = film.ranges[id], part = (r && r.limits) || {};
    if (part._film_writable === false || !canEstimate(id)) return null;
    const est = estimateLimits(id);
    if (!est && !Object.keys(part).length) return null;
    const out = { _subject: "这个 LUT " };
    for (const k of Object.keys(SLIDER_NAMES)) {
      if (part[k]) out[k] = part[k];
      else if (est && est[k]) out[k] = [est[k][0], est[k][1], true];
    }
    return out;
  }
  function canEstimate(id) {
    const lut = isUser(id) ? film.luts.find((l) => l.id === id) : null;
    return !!(lut && !lut.bake && !lutOutside(lut) && !(film.capabilities && film.capabilities.camera_fit === false));
  }
  // The base LUT if this tab already has it; otherwise start loading it and redraw when it arrives.
  const cubeLoading = new Set();
  function loadedCube(id) {
    const url = cubeUrl(id), base = film.cubes[url];
    if (!base && !cubeLoading.has(url)) {  // one download, however often the page redraws meanwhile
      const epoch = loadEpoch;
      cubeLoading.add(url);
      cubeOf(id).then(() => { if (epoch === loadEpoch) hooks.rerender(); }, () => {}).finally(() => cubeLoading.delete(url));
    }
    return base || null;
  }
  // Rough camera curve ends of base + grade: the curve starts about where black ends up and ends
  // about where white ends up (checked against real fits; colour limits are not covered).
  function roughEnds(base, grade) {
    const d = window.DarkslideAdjust.combine(base, grade, true, 2).data;
    return { first: Math.round(Math.max(d[0], d[1], d[2]) * 16383), last: Math.round(Math.min(d[21], d[22], d[23]) * 16383) };
  }
  function estimateLimits(id) {
    const ends = film.curveEnds;
    if (!ends) return null;
    const key = id + "|" + ends.curve_first_max + "|" + ends.curve_last_min;
    if (film.estimates[key]) return film.estimates[key];
    const base = loadedCube(id);
    if (!base) return null;
    const ok = (k, v) => { const e = roughEnds(base, v ? { [k]: v } : {}); return e.first <= ends.curve_first_max && e.last >= ends.curve_last_min; };
    let out = null;
    if (ok("exp", 0)) {
      out = {};
      for (const k of Object.keys(SLIDER_NAMES)) {
        const side = (sign) => {
          if (ok(k, sign * 100)) return 100;
          let lo = 0, hi = 100;
          while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (ok(k, sign * mid)) lo = mid; else hi = mid; }
          return lo;
        };
        out[k] = [-side(-1), side(1)];
      }
    }
    film.estimates[key] = out;
    return out;
  }
  // Exact ranges are worked out only for the sliders the user drags (the rest keep the estimate),
  // once the fitting component is on this computer (it is never downloaded for this).
  function wantRanges(id) {
    const lut = isUser(id) ? film.luts.find((l) => l.id === id) : null;
    return !!(lut && !lut.bake && (lut.fitted || film.fitReady) && !lutOutside(lut) && !(film.capabilities && film.capabilities.camera_fit === false));
  }
  // Picking a LUT only reads what is already known (nothing is started).
  function ensureRanges(id) {
    if (!film.pid || !wantRanges(id) || film.ranges[id]) return;
    film.ranges[id] = { state: "idle", limits: {} };
    pollRanges(id, film.pid, loadEpoch);
  }
  async function pollRanges(id, pid, epoch) {
    let r;
    try { r = await api("GET", "/api/projects/" + pid + "/film-luts/" + id + "/ranges"); }
    catch (e) { r = { state: "failed", error: e.message, limits: {} }; }
    if (film.pid !== pid || loadEpoch !== epoch) return;
    film.ranges[id] = r;
    hooks.rerender();
    if (r.state === "queued" || r.state === "running") { setTimeout(() => pollRanges(id, pid, epoch), 1500); return; }
    // A slider asked for just as the last job was ending: ask once more.
    const again = (film.asked[id] || []).find((k) => !(k in r.limits) && r.limits._film_writable !== false && !film.retried[id + k]);
    if (again && r.state !== "failed") { film.retried[id + again] = true; askRange(id, again); }
  }
  async function askRange(id, slider) {
    const pid = film.pid, epoch = loadEpoch;
    (film.asked[id] = film.asked[id] || []).includes(slider) || film.asked[id].push(slider);
    let r;
    try { r = await api("POST", "/api/projects/" + pid + "/film-luts/" + id + "/ranges", { slider }); }
    catch (e) { r = { state: "failed", error: e.message, limits: (film.ranges[id] || {}).limits || {} }; }
    if (film.pid !== pid || loadEpoch !== epoch) return;
    const polling = film.ranges[id] && (film.ranges[id].state === "queued" || film.ranges[id].state === "running");
    film.ranges[id] = r;
    hooks.rerender();
    if (!polling && (r.state === "queued" || r.state === "running")) setTimeout(() => pollRanges(id, pid, epoch), 1500);
  }
  // Dragging a slider of a user LUT asks for its exact range, unless it is already known.
  function focusRanges(slot, edit) {
    const id = edit.film, prev = film.lastGrade[slot.no];
    const r = isUser(id) ? film.ranges[id] : null;
    // Until the LUT's range record has loaded, keep the values from before the drag, so a slider moved
    // right after picking the LUT (server busy fitting) is still asked for once the record arrives.
    if (prev && prev.id === id && (!r || !wantRanges(id))) return;
    film.lastGrade[slot.no] = { id, grade: edit.grade || {} };
    if (!r || !prev || prev.id !== id || (r.limits || {})._film_writable === false) return;
    const moved = Object.keys(SLIDER_NAMES).find((k) => (edit.grade || {})[k] !== prev.grade[k]);
    if (!moved || moved in (r.limits || {}) || (film.asked[id] || []).includes(moved)) return;
    askRange(id, moved);
  }
  // What the slider strips of a user LUT are doing right now, for the note under the preview.
  function rangeNote(id) {
    const r = isUser(id) ? film.ranges[id] : null;
    if (!r || !userLimits(id)) return "";
    const limits = r.limits || {}, names = (keys) => keys.map((k) => SLIDER_NAMES[k]).join("、");
    const exact = Object.keys(SLIDER_NAMES).filter((k) => limits[k]);
    const busy = (film.asked[id] || []).filter((k) => !limits[k]);
    if (r.state === "failed") return "这个滑块的准确范围没算出来（" + (r.error || "未知原因") + "），调完仍会自动检查。";
    if ((r.state === "queued" || r.state === "running") && busy.length)
      return "正在算准" + names(busy) + "的范围（一个滑块约 20 秒）。" + (exact.length ? "实线是已算准的：" + names(exact) + "；" : "") + "虚线是粗略估算。";
    return exact.length ? "实线是已算准的范围（" + names(exact) + "），虚线是粗略估算；拖动哪个滑块，就把它算准（约 20 秒）。"
      : "滑轨上的虚线是粗略估算；拖动哪个滑块，就在后台把它算准（约 20 秒）。";
  }
  // Rough guess while dragging, from the graded black and white alone: the camera curve starts
  // about where black ends up and ends about where white ends up. Only a hint; the check decides.
  function endsHint(edit) {
    const ends = film.curveEnds, id = edit.film || null;
    if (!ends || !edit.grade) return "";
    const base = id ? loadedCube(id) : null;
    if (id && !base) return "";
    const { first, last } = roughEnds(base, edit.grade);
    const out = [];
    if (first > ends.curve_first_max) out.push("黑色被抬到约 " + first + "（相机最多 " + ends.curve_first_max + "）");
    if (last < ends.curve_last_min) out.push("白色被压到约 " + last + "（相机至少 " + ends.curve_last_min + "）");
    return out.length ? "粗略估算：" + out.join("，") + "，很可能写不进相机，以自动检查为准。" : "";
  }
  const SLIDER_NAMES = { temp: "冷暖", tint: "绿—品红", exp: "曝光", con: "对比度", hi: "高光", sh: "阴影", white: "白色", black: "黑色",
                         fade: "褪色", sat: "饱和度", splitHi: "高光着色", splitSh: "阴影着色" };
  // Sliders that are already beyond what this film allows on their own.
  function overSliders(id, grade) {
    const t = gradeLimitsFor(id);
    if (!t || !grade) return [];
    return Object.keys(SLIDER_NAMES).filter((k) => typeof grade[k] === "number" && t[k] && (grade[k] < t[k][0] || grade[k] > t[k][1]))
      .map((k) => SLIDER_NAMES[k] + " " + (grade[k] > 0 ? "+" : "") + grade[k] + "（" + (isUser(id) ? "这个 LUT " : "这卷胶片") + "单独最多 " + t[k][0] + "～+" + t[k][1] + (t[k][2] ? "，粗略估算" : "") + "）");
  }

  // After grading stops for a moment, the real combination is fitted on the server (nothing
  // stored): the result says whether it would go into the camera before anyone presses 拟合.
  let liveTimer = null;
  const liveKey = (slotNo, edit) => film.pid + "|" + slotNo + "|" + bakeKey(edit.film, edit.grade);
  function scheduleLiveCheck(slot, edit) {
    if (!film.pid || !SLOTS.includes(slot.no) || window.DarkslideAdjust.isEmpty(edit.grade) || bakeOf(edit)) return;
    if (film.capabilities && film.capabilities.camera_fit === false) return;
    const key = liveKey(slot.no, edit);
    if (film.live && film.live.key === key) return;
    film.live = { key, state: "waiting" };
    clearTimeout(liveTimer);
    const snapshot = JSON.parse(JSON.stringify(edit)), pid = film.pid, epoch = loadEpoch;
    liveTimer = setTimeout(async () => {
      if (!film.live || film.live.key !== key) return;
      film.live = { key, state: "checking" }; hooks.rerender();
      try {
        const base = snapshot.film ? await cubeOf(snapshot.film) : null;
        const lut = window.DarkslideAdjust.combine(base, snapshot.grade, true, 33);
        const result = await api("POST", "/api/projects/" + pid + "/film-grade-check", { cube: cubeText(lut, "check", false) });
        if (film.pid !== pid || loadEpoch !== epoch || !film.live || film.live.key !== key) return;
        film.live = { key, state: result.writable ? "ok" : "bad", result };
      } catch (e) {
        if (film.pid !== pid || loadEpoch !== epoch || !film.live || film.live.key !== key) return;
        film.live = { key, state: /拟合组件/.test(e.message) ? "unavailable" : "error", message: e.message };
      }
      hooks.rerender();
    }, 1200);
  }
  function liveFor(slot, edit) {
    return film.live && film.live.key === liveKey(slot.no, edit) ? film.live : null;
  }

  // Stable text of {film, grade}, matching how the server pairs a 调色 with its fitted LUT.
  function canon(v) {
    if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
    if (v && typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
    return JSON.stringify(v);
  }
  const bakeKey = (filmId, grade) => canon({ film: filmId || null, grade: grade || {} });
  function bakeOf(edit) {
    const want = bakeKey(edit.film, edit.grade);
    return film.luts.find((l) => l.bake && l.fitted && bakeKey(l.bake.film, l.bake.grade) === want) || null;
  }

  // What a camera write would use for this preset: a fitted LUT entry, a built-in id, or null.
  function cameraSource(slotNo, edit) {
    if (!SLOTS.includes(slotNo)) return null;
    if (!window.DarkslideAdjust.isEmpty(edit.grade)) { const b = bakeOf(edit); return b ? { lut: b } : null; }
    if (!edit.film) return null;
    if (isUser(edit.film)) { const l = film.luts.find((x) => x.id === edit.film); return l && l.fitted ? { lut: l } : null; }
    return { builtin: edit.film };
  }

  function cameraUrl(slotNo, edit) {
    const cs = cameraSource(slotNo, edit);
    return !cs ? null : isRecipe(edit.film) && window.DarkslideAdjust.isEmpty(edit.grade) ? recipeUrl(edit.film) + "/install.cube" : cs.lut ? "/api/projects/" + film.pid + "/film-luts/" + cs.lut.id + "/camera.cube"
      : "/api/projects/" + film.pid + "/film-camera/" + cs.builtin + ".cube";
  }

  function previewError(slotNo, edit) {
    return film.camFail[cameraUrl(slotNo, edit)] || null;
  }

  const builtinOutside = (id) => !!(film.outside && film.outside[id] && film.outside[id].length);
  // A fit whose numbers leave RICOH's own range cannot be written (user, 2026-10-14).
  const outside = (fit) => !!(fit && fit.envelope && fit.envelope.violations && fit.envelope.violations.length);
  // An imported LUT is out of range when its stored fit says so (older projects) or when the check
  // before fitting refused it (since 2026-10-17 nothing out of range is fitted or kept).
  const lutOutside = (l) => !!l && (outside(l.fit) || !!(l.check && l.check.writable === false));
  const lutReason = (l) => (l.check && l.check.reason) || (outside(l.fit) ? "超出了理光相机原厂的范围：" + l.fit.envelope.violations[0] : "");

  async function cubeAt(url) {
    const cache = film.cubes, epoch = loadEpoch;
    if (!cache[url]) {
      const res = await fetch(url);
      if (!res.ok) {
        const failure = await res.json().catch(() => ({}));
        throw new Error(failure.error || "预览读取失败");
      }
      const parsed = parseCube(await res.text());
      if (epoch !== loadEpoch) throw new Error("工程已切换，请重新预览");
      cache[url] = parsed;
    }
    return cache[url];
  }

  const cubeUrl = (id) => isRecipe(id) ? recipeUrl(id) + "/camera.cube" : isUser(id) ? "/api/projects/" + film.pid + "/film-luts/" + id + ".cube" : "/api/film-preview/" + id + ".cube";
  async function cubeOf(id) {
    return cubeAt(cubeUrl(id));
  }

  // CSS "linear-gradient(160deg,#a,#b 45%,#c)" drawn onto a canvas, so placeholders can be graded too.
  function drawGradient(css, w, h) {
    const m = /linear-gradient\((\d+)deg,(.*)\)/.exec(css.replace(/\s+/g, " "));
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const g = c.getContext("2d", { willReadFrequently: true });
    const a = (m ? +m[1] : 180) * Math.PI / 180, dx = Math.sin(a), dy = -Math.cos(a);
    const half = (Math.abs(w * dx) + Math.abs(h * dy)) / 2;
    const grad = g.createLinearGradient(w / 2 - dx * half, h / 2 - dy * half, w / 2 + dx * half, h / 2 + dy * half);
    const stops = (m ? m[2] : "#333,#999").split(",").map((t) => t.trim().split(" "));
    stops.forEach(([color, pos], i) => grad.addColorStop(pos ? parseFloat(pos) / 100 : i / (stops.length - 1), color));
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
    return g.getImageData(0, 0, w, h);
  }

  async function sourceImage() {
    const scene = hooks.scene(), url = hooks.mine();
    const key = scene || url;
    if (!key) return null;
    if (!film.source || film.source.key !== key) {
      let img;
      if (scene) img = drawGradient(scene, 1200, 800);
      else {
        const bitmap = await createImageBitmap(await (await fetch(url)).blob());
        const k = Math.min(1, PREVIEW_SIDE / Math.max(bitmap.width, bitmap.height));
        const c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(bitmap.width * k)); c.height = Math.max(1, Math.round(bitmap.height * k));
        const g = c.getContext("2d", { willReadFrequently: true });
        g.drawImage(bitmap, 0, 0, c.width, c.height);
        img = g.getImageData(0, 0, c.width, c.height);
      }
      const t = document.createElement("canvas");
      const k = 120 / Math.max(img.width, img.height);
      t.width = Math.round(img.width * k); t.height = Math.round(img.height * k);
      const full = document.createElement("canvas");
      full.width = img.width; full.height = img.height;
      full.getContext("2d").putImageData(img, 0, 0);
      t.getContext("2d").drawImage(full, 0, 0, t.width, t.height);
      film.source = { key, img, thumb: t.getContext("2d").getImageData(0, 0, t.width, t.height) };
      for (const u of Object.values(film.graded)) URL.revokeObjectURL(u);
      film.graded = {};
    }
    return film.source;
  }


  // Main preview: chosen film (if any) plus the adjustments; camera=true drops LUT-only parts.
  // With camera=true and fitted camera parameters, the preview runs the camera model instead.
  function previewUrl(edit, camera, slotNo) {
    const epoch = loadEpoch, busy = film.busy;
    const id = edit.film || null, grade = edit.grade || null;
    if (!id && window.DarkslideAdjust.isEmpty(grade)) return null;
    const src = hooks.scene() || hooks.mine();
    if (!src) return null;
    const camUrl = camera ? cameraUrl(slotNo, edit) : null;
    if (camUrl && film.camFail[camUrl]) return null;
    const useCam = !!camUrl;
    const key = "p|" + (id || "-") + "|" + JSON.stringify(grade || {}) + "|" + (camera ? 1 : 0) + "|" + (useCam ? camUrl : "") + "|" + src;
    if (film.graded[key]) return film.graded[key];
    if (!film.busy[key]) {
      film.busy[key] = true;
      (async () => {
        try {
          let lut = null;
          if (useCam) {
            try { lut = await cubeAt(camUrl); } catch (e) {
              if (epoch !== loadEpoch) return;
              film.camFail[camUrl] = e.message;
              toast("相机预览不可用：" + e.message, true);
              hooks.rerender();
              return;
            }
            if (epoch !== loadEpoch) return;
          }
          const [srcImg, base] = await Promise.all([sourceImage(), !lut && id ? cubeOf(id) : null]);
          if (!srcImg || epoch !== loadEpoch) return;
          if (!lut) lut = window.DarkslideAdjust.combine(base, grade, camera, 17);
          const c = document.createElement("canvas");
          c.width = srcImg.img.width; c.height = srcImg.img.height;
          c.getContext("2d").putImageData(grade_(srcImg.img, lut), 0, 0);
          const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.9));
          if (epoch !== loadEpoch) return;
          for (const k of Object.keys(film.graded)) if (k.startsWith("p|")) { URL.revokeObjectURL(film.graded[k]); delete film.graded[k]; }
          film.graded[key] = URL.createObjectURL(blob);
          hooks.rerender();
        } catch (e) { if (epoch === loadEpoch) toast(e.message, true); }
        finally { delete busy[key]; }
      })();
    }
    return null;
  }

  // Blob URL of the current sample through this film's preview, or null while it is being made.
  function gradedUrl(id, thumb) {
    const epoch = loadEpoch, busy = film.busy;
    const src = hooks.scene() || hooks.mine();
    if (!id || !src) return null;
    const key = id + "|" + src + (thumb ? "|t" : "");
    if (film.graded[key]) return film.graded[key];
    if (!film.busy[key]) {
      film.busy[key] = true;
      (async () => {
        try {
          const [src, lut] = await Promise.all([sourceImage(), cubeOf(id)]);
          if (!src || epoch !== loadEpoch) return;
          const img = thumb ? src.thumb : src.img;
          const c = document.createElement("canvas");
          c.width = img.width; c.height = img.height;
          c.getContext("2d").putImageData(grade_(img, lut), 0, 0);
          const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.9));
          if (epoch !== loadEpoch) return;
          film.graded[key] = URL.createObjectURL(blob);
          hooks.rerender();
        } catch (e) { if (epoch === loadEpoch) toast(e.message, true); }
        finally { delete busy[key]; }
      })();
    }
    return null;
  }

  function candidateReady() { return !!(film.capabilities && film.capabilities.candidate_generation); }

  // Curve editor: shade the rough no-go corners. The camera curve starts about where black ends up and
  // ends about where white ends up (curve_ends from the original), and the 调整 curve acts on the film's
  // own black..white, so the corners are: left of the film's black above the start limit, right of its
  // white below the end limit. A hint like the slider estimate; the automatic check decides.
  function curveZones(slot, edit) {
    const body = $("adjustBody"), ends = film.curveEnds;
    const on = !!(body && ends && SLOTS.includes(slot.no) && !(film.capabilities && film.capabilities.camera_fit === false));
    if (body) body.classList.toggle("cv-zones", on);
    if (!on) return;
    let black = 0, white = 1;
    if (edit.film) {
      const base = loadedCube(edit.film);
      if (base) { const d = base.data, n = d.length; black = Math.max(d[0], d[1], d[2]); white = Math.min(d[n - 3], d[n - 2], d[n - 1]); }
    }
    const pct = (x) => (Math.max(0, Math.min(1, x)) * 100).toFixed(1) + "%";
    body.style.setProperty("--cv-bx", pct(Math.max(0.04, black)));
    body.style.setProperty("--cv-by", pct(1 - ends.curve_first_max / 16383));
    body.style.setProperty("--cv-wx", pct(Math.max(0.04, 1 - white)));
    body.style.setProperty("--cv-wy", pct(ends.curve_last_min / 16383));
  }

  function render(slot, edit) {
    scheduleLiveCheck(slot, edit);
    curveZones(slot, edit);
    if (SLOTS.includes(slot.no)) {
      ensureRanges(edit.film); focusRanges(slot, edit);
      const lut = isUser(edit.film) ? film.luts.find((l) => l.id === edit.film) : null;
      if (lut) checkLut(slot, lut);
    }
    const allowed = SLOTS.includes(slot.no);
    const open = allowed && !!film.catalog;
    const ready = candidateReady() || !!(film.capabilities && film.capabilities.film_output);
    $("filmField").classList.toggle("locked", !allowed);
    $("filmBadge").textContent = !allowed ? "开发中" : candidateReady() ? "离线候选" : ready ? (film.writer.experimental ? "写入（实验）" : "可改") : "可只读核对";
    $("filmBadge").className = "v1-pill" + (open && ready ? " solid" : "");
    $("filmRecipeImport").hidden = !(film.capabilities && film.capabilities.photo_recipe_read);
    const current = edit.film || null;
    const pick = (id) => () => hooks.setFilm(slot.no, id || undefined);
    const tiles = [];
    if (open) {
      tiles.push(tile("不用", !current, pick(null), "不用胶片：从这个预设原来的颜色开始调", null));
      for (const f of film.catalog.films) tiles.push(tile(f.name, current === f.id, pick(f.id), f.film + " · " + f.print + (builtinOutside(f.id) ? "\n超出理光相机原厂的范围：只能预览和导出，不能写进相机" : ""), f.id, builtinOutside(f.id) ? "超范围" : "", iconUrl(f.id) || plateLines(f)));
      for (const r of film.recipes) tiles.push(tile(nameOf(r.id), current === r.id, pick(r.id), "照片中保存的曲线和矩阵", r.id, "已恢复"));
      for (const l of film.luts.filter((x) => !x.bake)) tiles.push(removable(l, tile(l.name, current === l.id, pick(l.id), lutOutside(l) ? "我的 LUT · 超出理光相机原厂的范围，不能写进相机" : l.fitted ? "我的 LUT · 已拟合成相机参数" : film.fitReady !== false ? "我的 LUT · 选中后自动检查能不能写进相机" : "我的 LUT · 还没拟合：点「拟合」或「生成」时会检查能不能写进相机", l.id, lutOutside(l) ? "超范围" : l.fitted ? "" : "待检查")));
      const add = document.createElement("button");
      add.type = "button"; add.className = "film-tile add"; add.title = "导入 LUT 或 Lightroom 预设";
      add.setAttribute("aria-haspopup", "menu");
      add.setAttribute("aria-label", "添加：LUT 或 Lightroom 预设");
      add.innerHTML = '<span class="plus">＋</span><span>添加</span><span class="sub">LUT · XMP</span>';
      add.addEventListener("click", (e) => { e.stopPropagation(); toggleAddMenu(add); });
      tiles.push(add);
    }
    $("filmChips").replaceChildren(...tiles);
    let hint;
    if (!allowed) hint = "这个预设换胶片目前正在开发中，等稳定之后开放；现在能换胶片的是银流冲洗、怀旧、HDR 色调、交叉冲洗。";
    else if (!film.catalog) hint = "胶片列表读取中…";
    else hint = "";  // user, 2026-10-17: no general instructions here; only the locked-preset note remains
    $("filmField").title = candidateReady() ? "会进入 IIIx 离线候选；机身待测" : ready ? "" : "胶片写入已接通，等实机测试后开启：现在选择会保存，暂不参与生成";
    $("filmHint").textContent = hint;
    $("filmHint").hidden = !hint;
    // Attribution stays out of the way: behind the small ⓘ next to the title (hover, or click on touch screens),
    // in README / THIRD_PARTY_NOTICES, in film_curves/, in every exported .cube and on the recipe card.
    const info = $("filmCreditInfo");
    info.hidden = !film.catalog;
    // Hovering a film shows only the film (user, 2026-10-17); source, licence and the custom-icon
    // folder live behind this ⓘ.
    const dir = film.catalog && film.catalog.icons_dir;
    info.title = film.catalog ? "数据来源与许可：" + film.catalog.credit + "\n近似效果，与厂商无关。" +
      (dir ? "\n想把胶片方块换成自己的图标：图片命名为「胶片编号.png」（如 gold200.png），放进 " + dir : "") : "";
  }

  // Built-in films show a name plate (same look as the factory-style icon), not a graded thumbnail:
  // "Portra 400" -> Portra / 400; a bare number like "5207" takes its type from the stock ("… 250D").
  function plateLines(f) {
    const i = f.name.lastIndexOf(" ");
    if (i > 0) return [f.name.slice(0, i), f.name.slice(i + 1)];
    return [f.name, String(f.film || "").split(" ").pop()];
  }

  // The name plate drawn as a camera icon: 40×40 with the factory icon's visible 38×36 area
  // (grey frame, dark fill, white text), at any scale. S = 40 is exactly what the camera shows.
  const VIS = { x: 1, y: 2, w: 38, h: 36 };
  function plateCanvas(lines, S, lift = 0) {
    const k = S / 40, c = document.createElement("canvas");
    c.width = c.height = S;
    const g = c.getContext("2d");
    g.fillStyle = "#808080"; g.fillRect(VIS.x * k, VIS.y * k, VIS.w * k, VIS.h * k);
    g.fillStyle = "#353536"; g.fillRect((VIS.x + 1) * k, (VIS.y + 1) * k, (VIS.w - 2) * k, (VIS.h - 2) * k);
    g.fillStyle = "#FFFFFF"; g.textAlign = "center"; g.textBaseline = "middle";
    const fit = (text, start) => {
      let size = start;
      do { g.font = "bold " + size * k + "px 'Helvetica Neue', Helvetica, Arial, sans-serif"; size -= 0.5; }
      while (size > 5 && g.measureText(text).width > (VIS.w - 5) * k);
    };
    fit(lines[0], 10); g.fillText(lines[0], 20 * k, (13.5 - lift) * k);
    fit(lines[1] || "", 15); g.fillText(lines[1] || "", 20 * k, (27 - lift) * k);
    return c;
  }

  // Tile picture: the same plate drawn sharp at 160 px; only the preset's camera icon is the 40×40.
  const plates = {};
  function plateUrl(lines) {
    const key = lines.join("\n");
    // text a little higher than on the camera icon: the tile's name strip covers the bottom
    if (!plates[key]) plates[key] = plateCanvas(lines, 160, 4.5).toDataURL("image/png");
    return plates[key];
  }

  function tile(text, on, onclick, title, id, tag, plate) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "film-tile" + (on ? " on" : "") + (id ? "" : " none");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.setAttribute("aria-label", text);
    if (title) b.title = title;
    // plate: the user's own icon (a URL) or the lines of a drawn name plate
    const thumb = typeof plate === "string" ? plate : plate ? plateUrl(plate) : id ? gradedUrl(id, true) : null;
    if (thumb) b.style.backgroundImage = 'url("' + thumb + '")';
    if (typeof plate === "string") b.classList.add("own-icon");  // the user's own icon: name strip on top, the picture's numbers stay visible
    const label = document.createElement("span");
    label.className = "film-tile-label"; label.textContent = text;
    b.append(label);
    if (tag) { const t = document.createElement("span"); t.className = "film-tag" + (tag === "超范围" ? " out" : /待/.test(tag) ? " wait" : ""); t.textContent = tag; b.append(t); }
    b.addEventListener("click", onclick);
    return b;
  }

  // Own LUTs get a small × (top left; the 待拟合/超范围 tag sits top right). Built-in films have none.
  const SLOT_NAMES = { 8: "银流冲洗", 9: "怀旧", 10: "HDR 色调", 11: "交叉冲洗" };
  function removable(l, b) {
    const wrap = document.createElement("div");
    wrap.className = "film-tile-wrap";
    const x = document.createElement("button");
    x.type = "button"; x.className = "film-del"; x.textContent = "×";
    x.title = "从这个工程里删除「" + l.name + "」";
    x.setAttribute("aria-label", "删除 " + l.name);
    x.addEventListener("click", (e) => { e.stopPropagation(); removeLut(l); });
    wrap.append(b, x);
    return wrap;
  }

  async function removeLut(l) {
    const all = hooks.allEdits ? hooks.allEdits() : {};
    const using = Object.keys(all).filter((n) => all[n] && all[n].film === l.id).map((n) => SLOT_NAMES[n] || ("第 " + n + " 号"));
    if (using.length) { toast("「" + l.name + "」正在「" + using.join("」「") + "」上用，先换成别的胶片再删", true); return; }
    if (!(await window.askConfirm("删除「" + l.name + "」？\n只从这个工程里删掉，你电脑上原来的 .cube 文件不受影响。", "删除", "删除 LUT"))) return;
    const pid = film.pid, epoch = loadEpoch;
    if (hooks.invalidateCheck) hooks.invalidateCheck();
    try {
      const result = await api("POST", "/api/projects/" + pid + "/film-luts/" + l.id + "/delete");
      if (film.pid !== pid || loadEpoch !== epoch) return;
      film.luts = result.luts; film.capabilities = result.capabilities;
      if (hooks.invalidateCheck) hooks.invalidateCheck();
      toast("已删除「" + l.name + "」");
      hooks.rerender();
    } catch (e) { toast(e.message, true); }
  }

  // Output row: how close a camera write gets (from the fit), the 拟合 action and the .cube export.
  const LEVEL_PCT = { "高": 88, "中": 60, "低": 30 };
  function fitText(fit) {
    return "平均色差 " + fit.de_mean + "，95% 的颜色在 " + fit.de_p95 + " 以内（CIEDE2000，相机模型上算）。";
  }
  // How close a camera write gets for this preset: { level, note, canFit, detail }.
  function fitState(slot, edit) {
    const failure = previewError(slot.no, edit);
    if (failure) return {level: "预览不可用", note: "相机安装预览未通过：" + failure + "。可切换到调后查看原值效果，或重新核对预览。", canFit: true};
    if (film.capabilities && film.capabilities.camera_fit === false)
      return {level: "—", note: "IIIx 当前可核对内置胶片；相机拟合尚未接入，桌面预览和导出仍可用。", canFit: false, detail: ""};
    const id = edit.film || null, adj = !window.DarkslideAdjust.isEmpty(edit.grade), lutOnly = window.DarkslideAdjust.lutOnly(edit.grade);
    const lut = isUser(id) ? film.luts.find((l) => l.id === id) || null : null;
    // Nothing about output until the project's capabilities have loaded.
    const later = !film.capabilities ? "" : candidateReady() ? "将进入 IIIx 离线候选；机身待测。" : !film.capabilities.film_output ? "当前可保存、拟合及只读核对；胶片完整输出尚未开放。"
      : film.writer.experimental ? "写入已打开（实验）：还没在相机上实测，刷机前准备好救机卡。" : "";
    let level, note, canFit = false;
    if (!id && !adj) { level = "—"; note = "还没选胶片、也没调整：相机保持原厂颜色。"; }
    else if (!SLOTS.includes(slot.no)) { level = "低"; note = "这个预设不能写入颜色：调色只用于预览和导出 .cube。"; }
    else if (adj) {
      const bake = bakeOf(edit);
      if (bake && outside(bake.fit)) {
        level = "超出范围";
        note = "这组颜色超出了理光相机原厂的范围，不能写进相机（" + bake.fit.envelope.violations[0] + "）。可以照常预览和导出 .cube。";
      } else if (bake) {
        level = bake.fit.level;
        note = "胶片＋调整已拟合成相机参数：" + fitText(bake.fit) + (lutOnly ? "HSL 分色和自然饱和度相机做不到，已去掉。" : "") + later;
      } else {
        level = "待拟合"; canFit = true;
        note = "点「生成」时会自动拟合进相机；想先看看相机上的效果，可以点「拟合」。" + (lutOnly ? "HSL 分色和自然饱和度相机做不到，拟合时会去掉。" : "");
        const live = liveFor(slot, edit), over = overSliders(id, edit.grade);
        if (live && live.state === "bad") {
          level = "超出范围"; canFit = false;
          note = "这组调色写不进相机：" + (live.result.reason || "超出理光相机原厂的范围") + "。" +
            (over.length ? "超出" + (isUser(id) ? "这个 LUT " : "这卷胶片") + "可写范围的有：" + over.join("；") + "。" : "几个滑块叠加后超了，往回调一些再看。") + "可以照常预览和导出 .cube。";
        } else if (live && live.state === "ok") {
          note = "自动检查：这组调色可以写进相机（近似程度 " + live.result.level + "），生成时会自动拟合。" + (lutOnly ? "HSL 分色和自然饱和度相机做不到，拟合时会去掉。" : "");
        } else if (live && (live.state === "checking" || live.state === "waiting")) {
          level = "检查中"; canFit = false;  // check first: 拟合 only after it passes
          note = "正在检查这组调色能不能写进相机……" + (over.length ? "（" + over.join("；") + "）" : "") + endsHint(edit);
        } else if (live && live.state === "unavailable") {
          note += "装好拟合组件后，调完会自动检查能不能写进相机。";
        } else if (over.length) {
          note = "这几项超出了" + (isUser(id) ? "这个 LUT " : "这卷胶片") + "单独能写进相机的范围：" + over.join("；") + "。" + note;
        } else {
          note += endsHint(edit);
        }
      }
      if (level !== "超出范围") note += rangeNote(id);
    } else if (isRecipe(id)) {
      level = "已恢复"; canFit = true;
      note = "保留照片中的原始曲线和矩阵；相机预览显示重新安装时的近似效果，尚未实拍验证。";
    } else if (lut && lutOutside(lut)) {
      level = "超出范围";
      note = "这个 LUT 写不进相机（" + lutReason(lut) + "），没有拟合，也没有保存拟合结果。可以照常预览和导出 .cube。";
    } else if (lut && !lut.fitted && (film.checking === id || (film.fitReady !== false && !film.checked[id]))) {
      level = "检查中";
      note = "正在检查这个 LUT 能不能写进相机，能写就直接拟合好（几秒钟）……" + (userLimits(id) ? "滑轨上的虚线是粗略估算。" : "");
    } else if (lut && !lut.fitted) {
      level = "待拟合"; canFit = true;
      note = (film.fitReady ? "这个 LUT 还没换算成相机参数：点「拟合」或「生成」时会检查能不能写进相机，能写才拟合。"
        : "这个 LUT 还没换算成相机参数：点「拟合」或「生成」时会先下载拟合组件，再检查能不能写进相机，能写才拟合。") + "现在可以预览和导出。" +
        (userLimits(id) ? "滑轨上的虚线是粗略估算。" : "");
    } else if (lut) {
      level = lut.fit.level; note = "已拟合成相机参数：" + fitText(lut.fit) + later + rangeNote(id);
    } else if (builtinOutside(id)) {
      level = "超出范围";
      note = "这卷胶片超出了理光相机原厂的范围，不能写进相机（" + film.outside[id][0] + "）。可以照常预览和导出 .cube。";
    } else { level = "高"; note = "这卷胶片已有相机数据（矩阵＋三条曲线＋曲线后矩阵）；实际效果以写入后的实拍为准。" + later; }
    const fit = adj && SLOTS.includes(slot.no) ? (bakeOf(edit) || {}).fit : lut && lut.fitted ? lut.fit : null;
    const canPull = level === "超出范围" && SLOTS.includes(slot.no) && !isRecipe(id) && !(film.capabilities && film.capabilities.camera_fit === false);
    if (canPull) note += "可以点「收进相机范围」，看看改成能写进相机的版本是什么样子。";
    return { level, note, canFit, fit, canPull };
  }

  function renderOutput(slot, edit) {
    const id = edit.film || null, adj = !window.DarkslideAdjust.isEmpty(edit.grade);
    const { level, note, canFit, canPull } = fitState(slot, edit);
    // Distinct colours (user, 2026-10-17): out of range is red, waiting (to fit / being checked) is amber,
    // a low approximation is orange, so the states are not confused.
    const tone = { "超出范围": "var(--st-out)", "待拟合": "var(--st-wait)", "检查中": "var(--st-wait)", "低": "var(--st-low)", "预览不可用": "var(--st-low)", "高": "#EDEDEA" }[level] || "";
    $("fitBadge").textContent = level === "待拟合" || level === "检查中" || level === "超出范围" || level === "已恢复" || level === "预览不可用" ? level : "近似程度 " + level;
    $("fitBadge").style.background = tone;
    $("fitBadge").style.color = level === "超出范围" ? "#fff" : "";
    $("fitBar").style.width = (LEVEL_PCT[level] || (level === "待拟合" || level === "检查中" ? 12 : 0)) + "%";
    $("fitBar").style.background = tone === "#EDEDEA" ? "" : tone;
    $("fitNote").textContent = film.fitting ? film.fittingNote || "正在拟合……" : note;
    $("fitNote").title = $("fitNote").textContent;
    $("btnFit").textContent = isRecipe(id) && !adj ? "核对预览" : "拟合";
    $("btnExportCube").textContent = isRecipe(id) ? "导出配方包" : "导出 .cube";
    $("btnFit").hidden = !canFit;
    $("btnFit").disabled = film.fitting;
    const pulling = film.pull && film.pull.state === "running";
    $("btnPull").hidden = !canPull && !pulling;
    $("btnPull").disabled = !!pulling || film.fitting;
    $("btnPull").textContent = pulling ? "正在找能写进相机的版本……" + (film.pull.total ? Math.min(99, Math.round(100 * film.pull.done / film.pull.total)) + "%" : "") : "收进相机范围";
    $("btnExportCube").disabled = !id && !adj;
    $("btnRecipe").disabled = !id && !adj;
    $("btnRecipe").title = "";
  }

  // What the recipe card says about the film: name, what it is, and whose data it carries.
  function filmInfo(id) {
    if (!id) return null;
    const f = film.catalog && film.catalog.films.find((x) => x.id === id);
    if (f) return { name: f.name, detail: f.film + " · " + f.print, credit: film.catalog.credit, builtin: true };
    const saved = film.recipes.find((x) => x.id === id);
    if (saved) return { name: nameOf(id), detail: "照片中保存的完整配方", credit: "原数据署名和许可随配方包导出", builtin: false };
    const l = film.luts.find((x) => x.id === id);
    return { name: l ? l.name : id, detail: "自己导入的 LUT", credit: "", builtin: false };
  }

  // The preset name a built-in film gives (user, 2026-10-17: picking a film renames the preset too).
  // Camera names are printable ASCII only and each preset has its own length (Retro holds 8), so every
  // film lists its full name first, then shorter forms; the first that fits is used.
  const PRESET_NAMES = {
    portra400: ["Portra 400", "Port 400"], portra160: ["Portra 160", "Port 160"], portra800: ["Portra 800", "Port 800"],
    portra800push1: ["Portra 800+1", "P800+1"], portra800push2: ["Portra 800+2", "P800+2"],
    gold200: ["Gold 200"], ektar100: ["Ektar 100", "Ektar100"], ultramax400: ["UltraMax 400", "UltraMax"],
    pro400h: ["Pro 400H"], xtra400_portraendura: ["X-Tra 400", "X-Tra400"], c200: ["C200"],
    "5207": ["5207"], "5219": ["5219"], "5203": ["5203"], "5213": ["5213"], verita200d: ["Verita 200D", "Verita"],
  };
  function presetName(id, max) {
    if (!film.catalog) return undefined;  // still loading
    const f = id && film.catalog.films.find((x) => x.id === id);
    if (!f) return null;  // no film, or the user's own LUT
    const plain = f.name.replace(/[^\x20-\x7e]/g, "").replace(/ +/g, " ").trim();
    const list = PRESET_NAMES[id] || [plain, plain.replace(/ /g, "")];
    return list.find((n) => n && window.DarkslideNames.ok(n, max)) || null;
  }

  // The name plate of a built-in film as a full square picture, or null for none / the user's own LUTs.
  function plateOf(id) {
    const f = id && film.catalog && film.catalog.films.find((x) => x.id === id);
    if (!f) return null;
    return iconUrl(f.id) || plateIcon(id).card;
  }

  // The film's name plate as a camera icon: small = the 40×40 written to the camera; source + crop =
  // the same plate drawn 8× larger, so the recipe card can show it sharp.
  function plateIcon(id) {
    if (!film.catalog) return undefined;  // still loading: unknown, not "no film"
    const f = film.catalog.films.find((x) => x.id === id);
    if (!f) return null;
    const lines = plateLines(f), source = plateCanvas(lines, 320), k = 8;
    const crop = { sx: VIS.x * k, sy: VIS.y * k, sw: VIS.w * k, sh: VIS.h * k };
    const card = document.createElement("canvas");
    card.width = crop.sw; card.height = crop.sh;
    card.getContext("2d").drawImage(source, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, crop.sw, crop.sh);
    return { small: plateCanvas(lines, 40), source, crop, card: card.toDataURL("image/png") };
  }

  // The user's own icon for a built-in film (<data>/film_icons/<id>.png …), or null.
  function iconUrl(id) {
    const v = film.catalog && film.catalog.icons && film.catalog.icons[id];
    return v ? "/api/film-icons/" + id + "?v=" + v : null;
  }
  // The current preview picture through film + adjustments, as a canvas (for the recipe card).
  async function gradedCanvas(edit) {
    const id = edit.film || null;
    const [src, base] = await Promise.all([sourceImage(), id ? cubeOf(id) : null]);
    if (!src) return null;
    const lut = window.DarkslideAdjust.combine(base, edit.grade, false, 17);
    const c = document.createElement("canvas");
    c.width = src.img.width; c.height = src.img.height;
    c.getContext("2d").putImageData(grade_(src.img, lut), 0, 0);
    return c;
  }

  function cubeText(lut, name, credit) {
    const lines = ['TITLE "' + name.replace(/["\\]/g, "") + '"', "# Made with DARKSLIDE GR (approximate look, not camera data)."];
    if (credit) lines.push("# Film preview derived from spektrafilm by Andrea Volpato, https://github.com/andreavolpato/spektrafilm, CC BY-SA 4.0; modified by LiorMoment.");
    const n = Math.round(Math.cbrt(lut.data.length / 3));
    lines.push("LUT_3D_SIZE " + n, "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0");
    for (let i = 0; i < lut.data.length; i += 3) lines.push(lut.data[i].toFixed(6) + " " + lut.data[i + 1].toFixed(6) + " " + lut.data[i + 2].toFixed(6));
    return lines.join("\n") + "\n";
  }

  // Check first (user, 2026-10-17): an imported LUT is checked as soon as it is on a camera preset.
  // The check is the real fit; in range it is kept, out of range nothing is kept and the server
  // records that it was refused. Needs the fitting component already on this computer.
  function checkLut(slot, lut) {
    if (!film.pid || !SLOTS.includes(slot.no) || lut.bake || lut.fitted || lutOutside(lut) || !film.fitReady || film.fitting ||
        film.checking || film.checked[lut.id] || (film.capabilities && film.capabilities.camera_fit === false)) return;
    const pid = film.pid, epoch = loadEpoch, id = lut.id;
    film.checked[id] = true; film.checking = id;
    setTimeout(async () => {
      let ok = false;
      try { await api.job(pid, "film-fit", { lut: id }); ok = true; }
      catch (e) { if (!/写不进相机|超出了理光/.test(e.message)) toast(e.message, true); }
      if (film.pid !== pid || loadEpoch !== epoch) return;
      try { const r = await api("GET", "/api/projects/" + pid + "/film-luts"); if (film.pid === pid && loadEpoch === epoch) { film.luts = r.luts; film.capabilities = r.capabilities; } } catch (e) {}
      film.checking = null;
      if (hooks.invalidateCheck) hooks.invalidateCheck();
      if (ok) toast("检查通过：这个 LUT 可以写进相机，已经拟合好");
      hooks.rerender();
    }, 0);
  }

  // 拟合: a user LUT is fitted as is; a 调色 is baked (film + camera-capable adjustments) and fitted.
  // The camera-model component is downloaded once, after the user agrees (or by their AI assistant).
  async function ensureComponent() {
    const st = await api("GET", "/api/fitting-setup");
    if (st.state === "unsupported" || st.state === "failed") throw new Error(st.note);
    if (st.state !== "missing") return true;
    if (!(await window.askConfirm(st.note + "\n\n从 PyPI 官方下载 Unicorn " + st.version + "，核对指纹后放进 DARKSLIDE GR 的数据文件夹，不改动电脑上的其他东西。", "下载", "下载拟合组件"))) return false;
    film.fittingNote = "正在下载拟合组件……";
    hooks.rerender();
    await api("POST", "/api/fitting-setup");
    film.fittingNote = "";
    return true;
  }

  async function fitNow(slot, edit) {
    if (film.fitting || !film.pid) return;
    const pid = film.pid, epoch = loadEpoch;
    edit = JSON.parse(JSON.stringify(edit));
    if (hooks.invalidateCheck) hooks.invalidateCheck();
    film.fitting = true; hooks.rerender();
    try {
      if (!(await ensureComponent()) || film.pid !== pid || loadEpoch !== epoch) return;
      const id = edit.film || null;
      if (!window.DarkslideAdjust.isEmpty(edit.grade)) {
        const base = id ? await cubeOf(id) : null;
        if (film.pid !== pid || loadEpoch !== epoch) return;
        const lut = window.DarkslideAdjust.combine(base, edit.grade, true, 33);
        const name = slot.name + " 调色" + (id ? "（" + nameOf(id) + "）" : "");
        await api.job(pid, "film-bake",
                      { body: { cube: cubeText(lut, name, id && !isUser(id) && !isRecipe(id)), name, film: id, grade: edit.grade } });
      } else if (isRecipe(id)) {
        await cubeOf(id); await cubeAt(recipeUrl(id) + "/install.cube");
      } else if (isUser(id)) {
        await api.job(pid, "film-fit", { lut: id });
      } else if (cameraUrl(slot.no, edit)) {
        await cubeAt(cameraUrl(slot.no, edit));
      }
      const result = await api("GET", "/api/projects/" + pid + "/film-luts");
      if (film.pid !== pid || loadEpoch !== epoch) return;
      film.luts = result.luts; film.capabilities = result.capabilities;
      const url = cameraUrl(slot.no, edit);
      if (url) { delete film.camFail[url]; delete film.cubes[url]; }
      if (hooks.invalidateCheck) hooks.invalidateCheck();
      toast(isRecipe(id) ? "照片配方预览已就绪" : "已拟合成相机参数");
    } catch (e) {
      toast(e.message, true);
      // A refused fit leaves a record (imported LUT) or nothing (调色): show the current state.
      try { const r = await api("GET", "/api/projects/" + pid + "/film-luts"); if (film.pid === pid && loadEpoch === epoch) film.luts = r.luts; } catch (e2) {}
    }
    finally { film.fitting = false; film.fittingNote = ""; hooks.rerender(); }
  }

  // 生成 fits whatever still needs it (user, 2026-10-17: fitting happens at 生成, no need to press 拟合
  // first): each camera preset whose 调色 has no fitted result yet, and each imported LUT not fitted yet.
  // The server refuses anything out of range (nothing kept); that stops 生成 with the preset's name.
  async function fitPending(slots, changes, progress) {
    const todo = [];
    for (const slot of slots) {
      if (!SLOTS.includes(slot.no)) continue;
      const edit = changes[slot.no] || {}, id = edit.film || null;
      if (!window.DarkslideAdjust.isEmpty(edit.grade)) {
        if (!bakeOf(edit)) todo.push({ slot, edit, kind: "bake" });
      } else if (isUser(id)) {
        const l = film.luts.find((x) => x.id === id);
        if (l && lutOutside(l)) throw new Error("「" + slot.name + "」用的 LUT 写不进相机（" + lutReason(l) + "）。换一个，或者点「收进相机范围」。");
        if (l && !l.bake && !l.fitted) todo.push({ slot, edit, kind: "lut" });
      }
    }
    if (!todo.length) return 0;
    if (!(await ensureComponent())) throw new Error("要把调色写进相机，需要先下载拟合组件。");
    const pid = film.pid;
    try {
      for (let i = 0; i < todo.length; i++) {
        const { slot, edit, kind } = todo[i], id = edit.film || null;
        if (progress) progress(slot, i + 1, todo.length);
        try {
          if (kind === "bake") {
            const base = id ? await cubeOf(id) : null;
            const lut = window.DarkslideAdjust.combine(base, edit.grade, true, 33);
            const name = slot.name + " 调色" + (id ? "（" + nameOf(id) + "）" : "");
            await api.job(pid, "film-bake", { body: { cube: cubeText(lut, name, id && !isUser(id) && !isRecipe(id)), name, film: id, grade: edit.grade } });
          } else {
            await api.job(pid, "film-fit", { lut: id });
          }
        } catch (e) { throw new Error("「" + slot.name + "」" + e.message); }
      }
    } finally {
      try {
        const r = await api("GET", "/api/projects/" + pid + "/film-luts");
        if (film.pid === pid) { film.luts = r.luts; film.capabilities = r.capabilities; }
      } catch (e) {}
      if (hooks.invalidateCheck) hooks.invalidateCheck();
    }
    return todo.length;
  }

  async function exportCube(slot, edit) {
    const pid = film.pid, epoch = loadEpoch;
    const id = edit.film || null;
    if (!id && window.DarkslideAdjust.isEmpty(edit.grade)) return;
    try {
      const base = id ? await cubeOf(id) : null;
      if (epoch !== loadEpoch || pid !== film.pid) return;
      const lut = window.DarkslideAdjust.combine(base, edit.grade, false, 33);
      const name = (id ? nameOf(id) : "") + (window.DarkslideAdjust.isEmpty(edit.grade) ? "" : (id ? " 调色" : "我的调色"));
      if (isRecipe(id)) {
        const response = await fetch("/api/projects/" + pid + "/photo-recipes/" + id + "/export", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cube: cubeText(lut, name, false), grade: edit.grade || {} }) });
        if (!response.ok) throw new Error((await response.json()).error || "配方导出失败");
        const link = document.createElement("a");
        link.href = URL.createObjectURL(await response.blob()); link.download = "darkslide-photo-recipe.zip";
        link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); return;
      }
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([cubeText(lut, name, id && !isUser(id) && !isRecipe(id))], { type: "text/plain" }));
      a.download = (name || "darkslide").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_") + ".cube";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch (e) { toast(e.message, true); }
  }


  // 「收进相机范围」: the server looks for the smallest change that makes this look writable
  // (black/white pulled in first, then colours moved towards the camera's own). Nothing is stored
  // until the user picks the new version next to the original; the original stays as it is.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function pullNow(slot, edit) {
    if (!film.pid || (film.pull && film.pull.state === "running")) return;
    const pid = film.pid, epoch = loadEpoch, no = slot.no;
    edit = JSON.parse(JSON.stringify(edit));
    const key = no + "|" + bakeKey(edit.film, edit.grade), id = edit.film || null;
    const live = () => film.pid === pid && loadEpoch === epoch && film.pull && film.pull.key === key;
    film.pull = { key, state: "running", done: 0, total: 0 }; hooks.rerender();
    try {
      if (!(await ensureComponent()) || !live()) { film.pull = null; return; }
      const base = id ? await cubeOf(id) : null;
      const cube = cubeText(window.DarkslideAdjust.combine(base, edit.grade, true, 33), "pull", false);
      let r = await api("POST", "/api/projects/" + pid + "/film-fit-into", { cube });
      while (live() && (r.state === "queued" || r.state === "running")) {
        Object.assign(film.pull, { done: r.done || 0, total: r.total || 0 }); hooks.rerender();
        await sleep(1500);
        r = await api("GET", "/api/projects/" + pid + "/film-fit-into/" + r.job);
      }
      if (!live()) return;
      film.pull = null; hooks.rerender();
      if (r.state !== "done") throw new Error(r.error || "没有找到能写进相机的版本");
      if (r.result.already) { toast("这组颜色其实已经在相机范围内了，直接点「拟合」就行"); return; }
      await showPull(slot, edit, r.result);
    } catch (e) {
      if (film.pull && film.pull.key === key) film.pull = null;
      hooks.rerender(); toast(e.message, true);
    }
  }

  // A small colour chart for when there is no sample photo: hues across, light to dark down,
  // with a grey ramp at the bottom.
  function chartImage() {
    const w = 480, h = 300, img = new ImageData(w, h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let rgb;
      if (y >= h - 40) { const v = x / (w - 1); rgb = [v, v, v]; }
      else {
        const hue = x / w * 6, l = 1 - y / (h - 40), c = (1 - Math.abs(2 * l - 1)) * 0.85, xx = c * (1 - Math.abs(hue % 2 - 1)), m = l - c / 2;
        const t = [[c, xx, 0], [xx, c, 0], [0, c, xx], [0, xx, c], [xx, 0, c], [c, 0, xx]][Math.floor(hue) % 6];
        rgb = t.map((v) => v + m);
      }
      const i = (y * w + x) * 4;
      img.data[i] = rgb[0] * 255; img.data[i + 1] = rgb[1] * 255; img.data[i + 2] = rgb[2] * 255; img.data[i + 3] = 255;
    }
    return img;
  }
  function lookUrl(img, lut) {
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    c.getContext("2d").putImageData(grade_(img, lut), 0, 0);
    return c.toDataURL("image/jpeg", 0.88);
  }

  async function showPull(slot, edit, result) {
    const id = edit.film || null, adj = !window.DarkslideAdjust.isEmpty(edit.grade), lutOnly = window.DarkslideAdjust.lutOnly(edit.grade);
    const src = await sourceImage(), img = src ? src.img : chartImage();
    const base = id ? await cubeOf(id) : null;
    $("pullBefore").src = lookUrl(img, window.DarkslideAdjust.combine(base, edit.grade, false, 17));
    $("pullAfter").src = lookUrl(img, parseCube(result.cube));
    const how = [];
    if (result.levels > 0) how.push("黑色压低、白色提亮 " + Math.round(result.levels * 100) + "%");
    if (result.blend > 0) how.push("颜色向相机原本的颜色靠拢 " + Math.round(result.blend * 100) + "%");
    $("pullWas").textContent = "原来写不进相机：" + result.was;
    $("pullHow").textContent = "新版本：" + how.join("，") + "。和原来的平均色差 " + result.change_de + "（CIEDE2000），装进相机的近似程度 " + result.check.level + "。" +
      (result.change_de > 15 ? "变化很大，可能已经不太像原来的颜色了。" : "");
    $("pullKeep").textContent = (adj ? "原来的胶片和调整保留不动。选用后会存成一个新的 LUT 换到这个预设上，右边的调整清零（已经算进新版本）" : "原来的 LUT 保留不动。选用后会存成一个新的 LUT 换到这个预设上") +
      (lutOnly ? "；HSL 分色和自然饱和度相机做不到，新版本里没有。" : "。") + (src ? "" : "没有放自己的样片，这里用色卡对比。");
    const name = (adj ? slot.name + " 调色" + (id ? "（" + nameOf(id) + "）" : "") : nameOf(id)) + " · 相机范围版";
    const dialog = $("pullDialog"), pid = film.pid, epoch = loadEpoch;
    $("pullUse").onclick = async () => {
      dialog.close();
      if (film.pid !== pid || loadEpoch !== epoch) return;
      try {
        const text = 'TITLE "' + name.replace(/["\\]/g, "") + '"\n# Pulled into the camera range by DARKSLIDE GR (approximate look, not camera data).\n' + result.cube;
        const lut = await keepLut(new Blob([text]), name + ".cube", "已存成「%s」，正在拟合……");
        if (lut && hooks.slotNo() === slot.no) await fitNow(slot, { film: lut.id });
      } catch (e) { toast(e.message, true); }
    };
    $("pullSkip").onclick = () => dialog.close();
    dialog.showModal();
  }

  // ＋ 添加: a small menu of the three ways to get a look in (.cube, reference photo, .xmp).
  function toggleAddMenu(anchor) {
    const menu = $("filmAddMenu");
    if (!menu.hidden) { menu.hidden = true; return; }
    const box = $("filmField").getBoundingClientRect(), r = anchor.getBoundingClientRect();
    menu.style.left = Math.max(0, Math.min(r.left - box.left, box.width - 260)) + "px";
    menu.style.bottom = (box.bottom - r.top + 6) + "px";
    menu.hidden = false;
    menu.querySelector("button").focus();
  }

  // Keep a .cube with the project, choose it for this preset when it can take a film.
  async function keepLut(body, filename, message) {
    const pid = film.pid, epoch = loadEpoch, no = hooks.slotNo();
    if (hooks.invalidateCheck) hooks.invalidateCheck();
    const lut = await api("POST", "/api/projects/" + pid + "/film-luts", body, { "X-Filename": encodeURIComponent(filename) });
    const result = await api("GET", "/api/projects/" + pid + "/film-luts");
    if (film.pid !== pid || loadEpoch !== epoch) return;
    film.luts = result.luts; film.capabilities = result.capabilities;
    if (hooks.invalidateCheck) hooks.invalidateCheck();
    if (SLOTS.includes(no) && hooks.slotNo() === no) hooks.setFilm(no, lut.id);
    toast(message.replace("%s", lut.name));
    hooks.rerender();
    return lut;
  }

  async function importRecipe(input) {
    const file = (input.files || [])[0], pid = film.pid, epoch = loadEpoch, no = hooks.slotNo();
    input.value = "";
    if (!file || !pid) return;
    try {
      if (file.size > 64 * 1024 * 1024) throw new Error("界面导入上限为 64 MiB");
      const profile = await api("POST", "/api/projects/" + pid + "/photo-recipes", await file.arrayBuffer());
      if (film.pid !== pid || loadEpoch !== epoch) return;
      const recipes = (await api("GET", "/api/projects/" + pid + "/photo-recipes")).recipes;
      if (film.pid !== pid || loadEpoch !== epoch) return;
      film.recipes = recipes;
      if (hooks.invalidateCheck) hooks.invalidateCheck();
      if (SLOTS.includes(no)) hooks.setFilm(no, profile.id);
      toast("已恢复照片胶片配方，无需拟合；原片保持不变");
      hooks.rerender();
    } catch (e) { toast(e.message, true); }
  }

  async function importLut(input) {
    const file = (input.files || [])[0];
    input.value = "";
    if (!file || !film.pid) return;
    try {
      await keepLut(await file.arrayBuffer(), file.name, "已导入「%s」，会自动检查能不能写进相机");
    } catch (e) { toast(e.message, true); }
  }

  async function importXmp(input) {
    const file = (input.files || [])[0];
    input.value = "";
    if (!file || !film.pid) return;
    try {
      const out = await api("POST", "/api/xmp-lut", await file.arrayBuffer(), { "X-Filename": encodeURIComponent(file.name) });
      const skipped = out.skipped.length ? "；跳过：" + out.skipped.join("、") : "";
      await keepLut(new Blob([out.cube]), out.name + ".cube", "已把 Lightroom 预设换成「%s」（近似，用了：" + (out.used.join("、") || "无") + skipped + "）");
    } catch (e) { toast(e.message, true); }
  }

  // RGB8 of an image scaled to at most `side` px, as base64 for the local learner.
  function pixelsOf(source, side) {
    const w0 = source.width, h0 = source.height, k = Math.min(1, side / Math.max(w0, h0));
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w0 * k)); c.height = Math.max(1, Math.round(h0 * k));
    const g = c.getContext("2d", { willReadFrequently: true });
    if (source instanceof ImageData) {
      const full = document.createElement("canvas");
      full.width = w0; full.height = h0;
      full.getContext("2d").putImageData(source, 0, 0);
      g.drawImage(full, 0, 0, c.width, c.height);
    } else g.drawImage(source, 0, 0, c.width, c.height);
    const d = g.getImageData(0, 0, c.width, c.height).data, rgb = new Uint8Array(c.width * c.height * 3);
    for (let i = 0, j = 0; i < d.length; i += 4, j += 3) { rgb[j] = d[i]; rgb[j + 1] = d[i + 1]; rgb[j + 2] = d[i + 2]; }
    let bin = "";
    for (let i = 0; i < rgb.length; i += 0x8000) bin += String.fromCharCode.apply(null, rgb.subarray(i, i + 0x8000));
    return { w: c.width, h: c.height, rgb: btoa(bin) };
  }

  // Reference photo: learn its tone and colour leaning against the current preview picture
  // (best: 「＋ 换照片」 with the user's own shot), bake a .cube and keep it like an import.
  async function importRef(input) {
    const file = (input.files || [])[0];
    input.value = "";
    if (!file || !film.pid) return;
    try {
      const [ref, src] = await Promise.all([createImageBitmap(file), sourceImage()]);
      if (!src) throw new Error("先在上面「＋ 选照片」选一张你自己拍的照片，作为学习的起点");
      toast("正在从参考照片学影调和色彩……");
      const learned = await api("POST", "/api/photo-lut/learn", { reference: pixelsOf(ref, 512), sources: [pixelsOf(src.img, 512)] });
      const name = "参考色 · " + file.name.replace(/\.[^.]+$/, "").slice(0, 40);
      const baked = await api("POST", "/api/photo-lut/bake", { model: learned.model, params: learned.defaults, name });
      const note = learned.warnings || [];
      await keepLut(new Blob([baked.cube]), name + ".cube", "已从参考照片生成「%s」（近似）" + (note.length ? "：" + note.join("；") : ""));
    } catch (e) { toast(e.message, true); }
  }

  function wire(apiFn, toastFn, appHooks) {
    api = apiFn; toast = toastFn; hooks = appHooks;
    $("filmRecipeInput").addEventListener("change", (e) => importRecipe(e.target));
    $("filmLutInput").addEventListener("change", (e) => importLut(e.target));
    $("filmXmpInput").addEventListener("change", (e) => importXmp(e.target));
    // 「拍同款」(learn from a photo) is parked for a later version; its menu entry is hidden in 1.0.
    $("filmCreditInfo").addEventListener("click", (e) => { if (e.currentTarget.title) toast(e.currentTarget.title); });
    const refInput = $("filmRefInput");
    if (refInput) refInput.addEventListener("change", (e) => importRef(e.target));
    const menu = $("filmAddMenu");
    menu.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-pick]");
      if (!b) return;
      menu.hidden = true;
      $(b.dataset.pick).click();
    });
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) menu.hidden = true; });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) menu.hidden = true; });
  }

  window.DarkslideFilm = { gradeLimitsFor, pullNow, fitPending, presetName, wire, load, render, renderOutput, exportCube, fitNow, fitState, filmInfo, plateOf, plateIcon, iconUrl, gradedCanvas, gradedUrl, previewUrl, previewError, nameOf, candidateReady, writerReady: () => !!(film.capabilities && film.capabilities.film_output), SLOTS };
})();
