"use strict";

const $ = (id) => document.getElementById(id);
const Names = window.DarkslideNames;
const VIS = { x: 1, y: 2, w: 38, h: 36 };

const state = {
  status: null,
  template: null,
  firmware: null,
  factory: null,
  project: null,
  changes: {},
  order: null,     // 相机菜单顺序：12 个固定预设号；null = 原厂顺序
  features: {},    // 柔光 / 65:24 宽幅：整台相机的开关，开着的才有键（soft_glow, wide_ratio）
  orderProblem: null,  // 已存的菜单顺序读不出时服务器给的原因；用户动过顺序或保存后清掉
  sel: 7,
  undo: [],
  redo: [],
  saveTimer: null,
  knownRows: null,
  revealAsset: null,
  enterSlots: false,
  draftState: "",
  generating: null,  // the id of the project whose 生成 this page is waiting for
  feature: "ic",
  view: "split",   // 左右对比 / 只看调后 / 相机近似
};

// ---------------------------------------------------------------- api
async function api(method, path, body, headers) {
  const opts = { method, headers: headers || {} };
  if (body instanceof Blob || body instanceof ArrayBuffer) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || "请求失败（" + res.status + "）");
    err.data = data;
    throw err;
  }
  return data;
}

// 生成 and 拟合 can take longer than a browser waits for one reply (WebKit, the engine of Safari, gives up
// after 60 seconds without an answer and the page shows "Load failed"). They are handed in as a job and
// asked about every JOB_POLL_MS: api.job(pid, task, {body, lut, live}) resolves with what the plain endpoint
// would have answered, or throws the same Error (message, and .data with the checks). `live()` false
// stops waiting (the job itself keeps going on the computer) and resolves with undefined.
const JOB_POLL_MS = 700;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function apiJob(pid, task, opts = {}) {
  const request = { task };
  if (opts.lut) request.lut = opts.lut;
  if (opts.body !== undefined) request.body = opts.body;
  return pollJob(pid, await api("POST", "/api/projects/" + pid + "/jobs", request), opts);
}
async function pollJob(pid, job, opts = {}) {
  let slips = 0;
  while (job.state === "queued" || job.state === "running") {
    await sleep(JOB_POLL_MS);
    if (opts.live && !opts.live()) return undefined;
    try {
      job = await api("GET", "/api/projects/" + pid + "/jobs/" + job.job);
      slips = 0;
    } catch (e) {
      if (e.data || ++slips > 5) throw e;  // the computer answered with an error, or kept not answering
    }
  }
  if (job.state === "done") return job.result;
  const err = new Error(job.state === "failed" ? (job.error || "请求失败") : "任务已丢失，可能是程序重启过，请再试一次");
  err.data = job.body || {};
  throw err;
}
api.job = apiJob;

function toast(msg, bad) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, bad ? 5000 : 2200);
}

function show(view) {
  if (view === "viewRescue" && show.current !== "viewRescue") show.previous = show.current;
  show.current = view;
  for (const id of ["viewStart", "viewWork", "viewDone", "viewRescue", "viewQuit"]) $(id).hidden = id !== view;
  $("cameraChip").hidden = view === "viewStart" || view === "viewQuit";
  // In a project the model is chosen right here in the top-right card; elsewhere it is just shown.
  const inProject = !!state.project && view !== "viewStart";
  $("projectBody").hidden = !inProject;
  $("chipModel").hidden = inProject;
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of [].concat(children || [])) if (c) node.append(c);
  return node;
}

const slotOf = (no) => state.template.slots.find((s) => s.no === no);

// Order of the preset list in Darkslide only (user, 2026-10-14): 负片 (12) right under 正片 (7).
// Slot numbers, project keys and the camera's own menu order are unchanged.
function listedSlots() {
  const slots = state.template.slots.filter((s) => s.no !== 12);
  const neg = slotOf(12), at = slots.findIndex((s) => s.no === 7);
  if (neg && at >= 0) slots.splice(at + 1, 0, neg);
  return neg && at < 0 ? state.template.slots : slots;
}
// Once the user sorts the camera's Image Control menu the list follows that order (user, 2026-10-15),
// 自定义 1/2 last; with the factory order it stays as above. Identities never move.
// Since the column is where the menu is sorted (user, 2026-10-24), a model that can be sorted always lists
// the camera's own menu order, the factory one included (负片 then sits before 正片, as in the camera).
function sidebarSlots() {
  const order = orderOn() && (state.order || ((state.status && state.status.menu_order) || {}).factory_still);
  if (!order) return listedSlots();
  return order.map(slotOf).concat(state.template.slots.filter((s) => s.no > 12));
}
const editable = (slot, what) => slot[what] === "test" || slot[what] === "open";
const assetUrl = (asset) => "/api/projects/" + state.project.id + "/assets/" + asset;
const factoryUrl = (no) => state.factory ? "/api/factory/" + state.project.input_sha256 + "/icons/" + no + ".png" : null;

// ---------------------------------------------------------------- start
// Camera choice: a series (one firmware file per series) and a model inside it. The base GR III and the
// GR III HDF have been tested on a camera; any other model asks the user to confirm before going on.
const RICOH_FW = "https://www.ricoh-imaging.co.jp/resources/japan/support/download/digital/FIRMWARE/";
const SERIES = [
  { name: "GR III 系列", short: "GR III", desc: "28mm · 固件 2.10 · 4 个型号共用", tag: "可用", ready: true,
    fw: "fwdc239b.bin", ver: "2.10", zip: "gr3_v210.zip", licence: "https://www.ricoh-imaging.co.jp/english/support/digital/gr3_s.html",
    note: "同一个固件文件通用于 GR III、街拍版、日记版、HDF。",
    models: [["GR III", "标准版", true], ["GR III Street Edition", "街拍版"], ["GR III Diary Edition", "日记版"], ["GR III HDF", "带柔光滤镜", true]] },
  { name: "GR IIIx 系列", short: "GR IIIx", desc: "40mm · 固件 1.60 · 3 个型号共用", tag: "可用", ready: false,
    fw: "fwdc243b.bin", ver: "1.60", zip: "gr3x_v160.zip", licence: "https://www.ricoh-imaging.co.jp/english/support/digital/gr3x_s.html",
    note: "同一个固件文件通用于 GR IIIx、城市版、HDF。",
    models: [["GR IIIx", "标准版", true], ["GR IIIx Urban Edition", "城市版"], ["GR IIIx HDF", "带柔光滤镜"]] },
  { name: "GR IV", short: "GR IV", desc: "28mm · 新一代", tag: "适配中", off: true, models: [] },
];
const CAM_KEY = "darkslide.camera";
function loadCam() {
  try { const c = JSON.parse(localStorage.getItem(CAM_KEY) || "{}"); if (SERIES[c.s] && !SERIES[c.s].off) return { s: c.s, v: c.v || 0, ok: c.ok || {} }; } catch (e) { /* first visit */ }
  return { s: 0, v: 0, ok: {} };
}
function saveCam() { try { localStorage.setItem(CAM_KEY, JSON.stringify(state.cam)); } catch (e) { /* not remembered */ } }
function camModel() { const ser = SERIES[state.cam.s]; return ser.models[Math.min(state.cam.v, ser.models.length - 1)]; }
// The 下一步 button waits until an untested model has been confirmed (or the series isn't importable yet).
// Only the missing tick on an untested model: the button stays clickable and reminds instead.
function needsCamConfirm() { const m = camModel(); return SERIES[state.cam.s].ready && !m[2] && !state.cam.ok[m[0]]; }
function remindCamConfirm() {
  toast("先勾选上面黄色框里的确认（刷机风险自己承担），再点下一步", true);
  const bar = $("camConfirm");
  bar.scrollIntoView({ block: "nearest", behavior: "smooth" });
  bar.classList.remove("shake"); void bar.offsetWidth; bar.classList.add("shake");
  $("camConfirmBox").focus({ preventScroll: true });
}
// Wrap a project-pick action: a missing confirmation reminds; any other block keeps the button disabled.
function pickButton(attrs, action) {
  const remind = needsCamConfirm(), blocked = camBlocked();
  return el("button", Object.assign({}, attrs, {
    class: attrs.class + (remind ? " soft-disabled" : ""), disabled: blocked && !remind,
    "aria-disabled": blocked ? "true" : "false",
    onclick: () => { if (needsCamConfirm()) return remindCamConfirm(); if (!camBlocked()) action(); },
  }));
}
function camBlocked() { const m = camModel(); return !SERIES[state.cam.s].ready || (!m[2] && !state.cam.ok[m[0]]) || (state.firmware && state.firmware.model !== SERIES[state.cam.s].short); }

function renderModels() {
  state.cam = state.cam || loadCam();
  const cam = state.cam;
  $("models").replaceChildren(...SERIES.map((x, i) => el("button", {
    class: "model" + (i === cam.s ? " on" : ""), disabled: !!x.off, "aria-pressed": i === cam.s ? "true" : "false",
    onclick: () => { if (i !== cam.s) { cam.s = i; cam.v = 0; saveCam(); renderModels(); } },
  }, [
    (() => { const st = el("div", { class: "model-stage" }); st.innerHTML = window.DarkslideCamera.svg(x.short, x.short.replace(/\W/g, "")); return st; })(),
    el("div", { class: "model-name" }, [el("b", { text: x.name }), el("span", { class: "series-tag " + (x.ready ? "ready" : x.off ? "off" : "warn"), text: x.tag })]),
    el("div", { class: "dim small", style: "width:100%", text: x.desc }),
  ])));
  renderVariants();
}

// Picking a model only refreshes this part; the camera drawings above stay put (no flash).
function renderVariants() {
  const cam = state.cam, ser = SERIES[cam.s], m = camModel();
  $("variantsTitle").textContent = ser.name + " · 选你的型号";
  $("variantsNote").textContent = ser.note;
  $("variants").replaceChildren(...ser.models.map(([name, sub, tested], i) => el("button", {
    class: "variant" + (i === cam.v ? " on" : ""), role: "radio", "aria-checked": i === cam.v ? "true" : "false",
    onclick: () => { if (i !== cam.v) { cam.v = i; saveCam(); renderVariants(); } },
  }, [
    el("span", { class: "variant-name", text: name }),
    el("span", { class: "dim2 xsmall", text: sub }),
    el("span", { class: "variant-tag " + (tested ? "ready" : "warn"), text: tested ? "已实测" : "已适配 · 未实测" }),
  ])));
  const needConfirm = ser.ready && !m[2];
  $("camConfirm").hidden = !needConfirm;
  $("camConfirmName").textContent = m[0];
  $("camConfirmText").textContent = cam.s === 0 ? "和 GR III 用同一个固件，但作者还没在这个型号上实测过。" : "已经适配完，但还没在真机上实测过。";
  $("camConfirmBox").checked = !!cam.ok[m[0]];
  // download link and file names follow the series
  if (window.resetFwGet) window.resetFwGet();
  $("fwDownload").href = RICOH_FW + ser.zip;
  $("fwDownload").textContent = "下载 " + ser.short + " " + ser.ver;
  $("fwHint").textContent = ser.zip + " 不用解压，" + ser.fw + " 也可以";
  $("fwLicence").href = ser.licence;
  $("drop").classList.toggle("off", !ser.ready);
  $("fileInput").disabled = !ser.ready;
  $("dropTitle").textContent = ser.ready ? "拖入官方固件，或点这里选择" : ser.short + " 支持还在合并中，暂时不能导入";
  if (state.firmware && state.firmware.kind === "official") { renderImportMatch(); renderProjectPick(); }
}

// Longest preset name the camera can take for this preset (template name_max, else the original English length).
const nameMax = (s) => s.name_max || s.factory_en.length;

// The imported official file versus the current series choice. Re-run on every series/model
// change, so a corrected choice clears an old "不一致" (and a wrong one clears an old ✓).
function renderImportMatch() {
  const res = state.firmware, out = $("importResult");
  if (!res || res.kind !== "official") return false;
  const chosen = SERIES[state.cam.s].short;
  if (res.model !== chosen) {
    out.className = "import-result small bad";
    out.textContent = "导入的是 " + res.model + "，与选择的 " + chosen + " 不一致，请先重新选择系列和型号。";
    return false;
  }
  out.className = "import-result small ok";
  out.textContent = "✓ " + res.reason + " · SHA " + res.sha256.slice(0, 8) + "…" + res.sha256.slice(-8);
  return true;
}

async function importFile(file) {
  const out = $("importResult");
  out.className = "import-result dim small";
  out.textContent = "正在核对 " + file.name + " …";
  $("projectPick").hidden = true;
  try {
    const buf = await file.arrayBuffer();
    const res = await api("POST", "/api/firmware", buf, { "X-Filename": encodeURIComponent(file.name) });
    state.firmware = res;
    if (res.kind === "official") {
      if (!renderImportMatch()) return;
      renderProjectPick();
      const go = $("projectPick").querySelector(".btn.primary");
      if (go && !needsCamConfirm()) window.DarkslideGuide.showArrow(go);
    } else if (res.kind === "generated") {
      out.className = "import-result small ok";
      out.textContent = res.reason;
      if (res.project_id) openProject(res.project_id);
    } else {
      out.className = "import-result small bad";
      out.textContent = "✕ " + res.reason;
      const drop = $("drop"); drop.classList.remove("shake"); void drop.offsetWidth; drop.classList.add("shake");
    }
  } catch (e) {
    out.className = "import-result small bad";
    out.textContent = "✕ " + e.message;
  }
}

function renderProjectPick() {
  // The primary button is always "下一步": continue the latest project for this
  // firmware, or start a new one. Older projects stay listed above it.
  const sha = state.firmware.sha256;
  if (state.firmware.model !== SERIES[state.cam.s].short) { $("projectPick").hidden = true; return; }
  const projects = (state.status.projects || []).filter((p) => p.input_sha256 === sha);
  const box = $("projectPick");
  const items = projects.map((p) => {
    const b = pickButton({ class: "project-item" }, () => openWithPickedModel(p.id, false));
    b.append(el("span", { text: p.title }), el("span", { class: "dim xsmall", text: p.changes + " 项改动" }));
    const del = el("button", { type: "button", class: "project-del", text: "×", title: "删除这个工程", "aria-label": "删除工程 " + p.title, onclick: () => deleteProject(p) });
    return el("div", { class: "project-row" }, [b, del]);
  });
  items.push(pickButton({ class: "btn primary", title: needsCamConfirm() ? "先勾选上面的确认" : "", text: "下一步" },
    () => (projects.length ? openWithPickedModel(projects[0].id, true) : newProject())));
  if (projects.length) items.push(el("button", { class: "btn ghost small-btn", onclick: newProject, text: "新建工程" }));
  if (state.firmware.model === "GR IIIx") items.push(el("button", {class: "btn ghost small-btn", text: "导入旧版 GR IIIx 工程", onclick: importLegacyStore}));
  box.replaceChildren(...items);
  box.hidden = false;
}

// The page's own confirm and prompt. The browser's confirm()/prompt() are blocked in some embedded
// browsers (some answer "no" at once without showing anything), so the × and the other questions did nothing there.
function askConfirm(text, yes, title, input) {
  const d = $("askDialog"), field = $("askInput");
  $("askTitle").textContent = title || "确认";
  $("askText").textContent = text;
  $("askYes").textContent = yes || "确定";
  field.hidden = !input; field.value = "";
  return new Promise((resolve) => {
    const done = (v) => {
      d.removeEventListener("close", onClose);
      $("askYes").onclick = $("askNo").onclick = null;
      if (d.open) d.close();
      resolve(v);
    };
    const onClose = () => done(input ? null : false);
    $("askYes").onclick = () => done(input ? field.value.trim() || null : true);
    $("askNo").onclick = () => done(input ? null : false);
    d.addEventListener("close", onClose);
    d.showModal();
    (input ? field : $("askNo")).focus();
  });
}
window.askConfirm = askConfirm;

// × on the home list (user, 2026-10-17). Says exactly what goes, and only acts after a yes.
async function deleteProject(p) {
  const builds = (state.status.builds || []).filter((b) => b.project_id === p.id).length;
  const what = "删除工程「" + p.title + "」？它的草稿、保存的版本、上传的图片和导入的 LUT 会一起删掉" +
    (builds ? "，生成过的 " + builds + " 份固件文件也会删掉（已经下载到电脑上的不受影响）" : "") + "。删除后不能恢复。";
  if (!(await askConfirm(what, "删除", "删除工程"))) return;
  try {
    const out = await api("POST", "/api/projects/" + p.id + "/delete");
    state.status.projects = out.projects; state.status.builds = out.builds;
    if (state.project && state.project.id === p.id) state.project = null;
    renderProjectPick();
    toast("已删除「" + p.title + "」");
  } catch (e) { toast(e.message, true); }
}

async function importLegacyStore() {
  const source = await askConfirm("旧版 GR IIIx 数据目录的完整路径（复制导入，保留原目录）", "下一步", "导入旧版 GR IIIx 工程", true);
  if (!source) return;
  try {
    const plan = await api("POST", "/api/legacy-import", {source});
    if (!(await askConfirm("找到 " + plan.projects.length + " 个工程、" + plan.builds.length + " 个历史生成记录。复制导入后，旧用户 LUT 需重新拟合；照片中保存的配方原值保留。继续？", "复制导入", "导入旧版 GR IIIx 工程"))) return;
    await api("POST", "/api/legacy-import", {source, apply: true, source_digest: plan.source_digest});
    state.status = await api("GET", "/api/status");
    renderProjectPick(); toast("旧工程已复制导入，原目录保持不变");
  } catch(e) {toast(e.message,true);}
}

async function newProject() {
  if (camBlocked()) return;
  try {
    const p = await api("POST", "/api/projects", { title: "未命名", input_sha256: state.firmware.sha256, camera_variant: camModel()[0] });
    await loadProject(p);
  } catch (e) { toast(e.message, true); }
}

async function openProject(id) {
  try { await loadProject(await api("GET", "/api/projects/" + id)); } catch (e) { toast(e.message, true); }
}

// From the home page the model just picked there applies (user, 2026-10-17: picked a model, the top
// right did not follow). 下一步 (force) sets it on the project it continues; a project opened from the
// list only takes it when it has no model of its own, so two cameras' projects do not overwrite each other.
async function openWithPickedModel(id, force) {
  const choice = camModel()[0];
  try {
    let p = await api("GET", "/api/projects/" + id);
    if (choice && p.camera_variant !== choice && (force || !p.camera_variant))
      p = await api("PUT", "/api/projects/" + id + "/target-body", { camera_variant: choice });
    await loadProject(p);
  } catch (e) { toast(e.message, true); }
}

async function loadProject(p) {
  invalidateFilmPrepare();
  state.project = p;
  if (p.template) state.template = p.template;
  if (p.detail) state.status.detail = p.detail;
  $("chipModel").textContent = p.camera_variant || state.template.model;
  $("chipFw").textContent = "固件 " + state.template.firmware + " · 官方原件 · 已核对";
  $("projectCapability").textContent = p.capabilities ? p.capabilities.note : "";
  renderMenuLanguage();
  $("btnGenerate").textContent = p.capabilities && p.capabilities.candidate_generation ? "生成离线候选" : "生成";
  $("iiixRecipeOption").hidden = !(p.capabilities && p.capabilities.candidate_recipe_extension);
  $("iiixRecipeExtension").checked = false;
  const cameraButton = $("viewBtns").querySelector('[data-view="camera"]');
  cameraButton.disabled = !!(p.capabilities && !p.capabilities.camera_fit);
  if (cameraButton.disabled && state.view === "camera") state.view = "split";
  $("outputVersion").textContent = state.template.firmware + "（不变）";
  state.changes = JSON.parse(JSON.stringify(p.draft.changes || {}));
  state.order = window.DarkslideOrder.fromServer(p.draft.menu_order);
  state.features = Object.assign({}, p.draft.camera_features || {});
  // A stored order the server could not read is shown as the factory order and left alone on disk
  // until the user moves a preset, restores the factory order or saves (which clears it).
  state.orderProblem = p.draft.menu_order_unreadable || null;
  state.undo = [];
  state.redo = [];
  state.enterSlots = true;
  state.knownRows = null;
  state.draftState = p.unsaved ? "草稿未正式保存" : (p.saved_at ? "已保存 " + p.saved_at.slice(11, 16) : "未保存");
  $("projectTitle").value = p.title;
  renderProjectBody();
  try { state.factory = await api("GET", "/api/factory/" + p.input_sha256); } catch (e) { state.factory = null; }
  show("viewWork");
  renderAll();
  if (state.orderProblem) toast(`已存的菜单顺序无法读取，已按原厂顺序显示，保存后会清除（${state.orderProblem}）`, true);
  else if (p.draft.menu_order_fallback) toast(`草稿里的菜单顺序无法读取，已改用上次保存的顺序（${p.draft.menu_order_fallback}）`, true);
  else if (p.menu_order_unreadable) toast(`上次保存的菜单顺序无法读取，保存后会换成现在的顺序（${p.menu_order_unreadable}）`, true);
  window.DarkslideFilm.load(p.id);
  maybeGuide("work");
  resumeGeneration(p.id);
}

function renderProjectBody() {
  const p = state.project, select = $("projectBody");
  const family = p.template_id.startsWith("gr3x-") ? "GR IIIx" : "GR III";
  const series = SERIES.find(s => s.short === family);
  select.replaceChildren(el("option", { value: "", text: (p.target_body || family) + " · 选择具体型号" }),
    ...series.models.map(m => el("option", { value: m[0], text: m[0] })));
  select.value = p.camera_variant || "";
  select.onchange = async () => {
    if (!select.value) return;
    const pid = p.id, choice = select.value;
    invalidateFilmPrepare();
    select.disabled = true;
    try {
      const updated = await api("PUT", "/api/projects/" + pid + "/target-body", {camera_variant: choice});
      if (state.project && state.project.id === pid) {
        invalidateFilmPrepare();
        state.project = {...state.project, target_body: updated.target_body, camera_variant: updated.camera_variant};
        $("chipModel").textContent = updated.camera_variant || state.template.model;
        renderProjectBody();
        renderAll();  // the factory shutdown picture and other per-model notes follow the new model
      }
    } catch (e) { toast(e.message, true); if (state.project && state.project.id === pid) renderProjectBody(); }
    finally { select.disabled = false; }
  };
}

// ---------------------------------------------------------------- guide
async function setPref(key, value) {
  try { state.status.prefs = await api("POST", "/api/prefs", { key, value }); } catch (e) { /* keep going */ }
}

// Preferences are saved one after another: the server rewrites the whole prefs file per request,
// so two at once could drop one of them.
async function setPrefs(pairs) {
  for (const [key, value] of pairs) await setPref(key, value);
}

function maybeGuide(flow) {
  if (flow === "work") return maybeWorkGuide();
  const prefs = (state.status && state.status.prefs) || {};
  if (prefs.guide_off || prefs["guide_" + flow + "_seen"] || window.DarkslideGuide.active()) return;
  requestAnimationFrame(() => !window.DarkslideGuide.active() && window.DarkslideGuide.start(flow, (forever) => {
    setPrefs([["guide_" + flow + "_seen", true], ...(forever ? [["guide_off", true]] : [])]);
  }));
}

// Workbench (F35; 1.6.0): the "这一版新增" card once, then a 2-step tour of 界面颜色 / 预设列表, then (for
// someone who never had it) the 5-step preset-editing guide. 以后再说 closes them all for good; the "?" on the
// editing page or Settings replays the tour and the editing guide. Whoever saw the 1.0 card still gets this one.
function maybeWorkGuide() {
  const prefs = (state.status && state.status.prefs) || {};
  if (prefs.guide_off || window.DarkslideGuide.active()) return;
  if (!prefs.guide_v3_news_seen) {  // 1.6.0: the new look; whoever saw the 1.5.0 card (v2) gets this one once
    requestAnimationFrame(() => !window.DarkslideGuide.active() && window.DarkslideGuide.start("news", async (forever, how) => {
      if (forever) { await setPrefs([["guide_v3_news_seen", true], ["guide_v2_news_seen", true], ["guide_v1_news_seen", true], ["guide_off", true]]); return; }
      await setPrefs([["guide_v3_news_seen", true], ["guide_v2_news_seen", true], ["guide_v1_news_seen", true]]);
      if (how === "next") startTour(() => { if (!prefs.guide_editor_seen) startEditorGuide(); });
      else if (!prefs.guide_editor_seen) await setPref("guide_editor_seen", true);
    }));
  } else if (!prefs.guide_editor_seen) {
    requestAnimationFrame(() => !window.DarkslideGuide.active() && startEditorGuide());
  }
}

// The new-feature tour; then() runs only when it was gone through to the end.
function startTour(then) {
  if (state.feature !== "ic") { state.feature = "ic"; renderAll(); }
  requestAnimationFrame(() => window.DarkslideGuide.start("tour", async (forever, how) => {
    await setPrefs([["guide_tour_seen", true], ...(forever ? [["guide_off", true]] : [])]);
    if (!forever && how === "next" && then) then();
  }));
}

function startEditorGuide() {
  if (state.feature !== "ic") { state.feature = "ic"; renderAll(); }
  // The tour shows the film tiles, which only open on the four film presets: start on 银流冲洗.
  const films = window.DarkslideFilm ? window.DarkslideFilm.SLOTS : [];
  if (films.length && !films.includes(state.sel)) { state.sel = films[0]; renderAll(); }
  requestAnimationFrame(() => window.DarkslideGuide.start("editor", (forever) => {
    setPrefs([["guide_editor_seen", true], ...(forever ? [["guide_off", true]] : [])]);
  }));
}

// ---------------------------------------------------------------- changes
function snapshot() { return JSON.stringify({ c: state.changes, o: state.order, f: state.features }); }

function restore(snap) {
  const v = JSON.parse(snap);
  state.changes = v.c;
  state.order = v.o;
  state.features = v.f || {};
}

// 柔光 / 65:24 宽幅 (whole-camera switches, user 2026-10-24). A switch can only be turned on when the model's
// capability allows it; until the firmware side is built that is never, and the switch stays greyed.
const CAMERA_FEATURES = [["soft_glow", "柔光", "softSwitch"], ["wide_ratio", "65:24 宽幅", "ratioSwitch"]];
function setFeature(name, on) {
  if (on && !featureOn(name)) { toast("这项功能还在研发，暂时不能装进相机", true); return; }
  state.undo.push(snapshot());
  if (state.undo.length > 200) state.undo.shift();
  state.redo = [];
  const next = Object.assign({}, state.features);
  if (on) next[name] = true; else delete next[name];
  state.features = next;
  scheduleDraft();
  renderAll();
}

// The menu order is one undo step per move, like any other edit. Only null or the 12 presets once each.
function setOrder(order) {
  if (!window.DarkslideOrder.valid(order)) { toast("菜单顺序数据不对，没有改动（请重新调整）", true); return; }
  state.orderProblem = null;  // the user has now decided about the order
  state.undo.push(snapshot());
  if (state.undo.length > 200) state.undo.shift();
  state.redo = [];
  state.order = order;
  scheduleDraft();
  renderAll();
}

function mutate(fn) {
  state.undo.push(snapshot());
  if (state.undo.length > 200) state.undo.shift();
  state.redo = [];
  fn(state.changes);
  for (const k of Object.keys(state.changes)) if (!Object.keys(state.changes[k]).length) delete state.changes[k];
  scheduleDraft();
  renderAll();
}

// Picking a film clears the right column: it starts from 0 on top of the new film.
function setFilm(no, id) {
  const prev = (state.changes[no] || {}).film;
  mutate((c) => {
    const slot = (c[no] = c[no] || {});
    if (id === undefined) delete slot.film; else slot.film = id;
    delete slot.grade;
  });
  // The film icon is put on the preset in the background (about a second). 生成 waits for it: a change
  // landing mid-build made the server refuse it as 「生成期间工程…改变」 (found in testing, 2026-10-26).
  const sync = syncFilmIcon(no, id), pend = state.iconSyncs || (state.iconSyncs = new Set());
  if (sync && sync.finally) { pend.add(sync); sync.finally(() => pend.delete(sync)); }
  syncFilmName(no, id, prev);
}

// Picking a built-in film also renames the preset to that film (user, 2026-10-17: 「名字也帮我自动替换」),
// replacing whatever name was there; the name can still be edited afterwards. Leaving the film for none
// or the user's own LUT drops a name that only came from the old film, back to the factory name.
function syncFilmName(no, id, prev) {
  const s = slotOf(no), F = window.DarkslideFilm;
  if (!s || !editable(s, "rename") || !F || !F.presetName) return;
  const max = nameMax(s), cur = (state.changes[no] || {}).name;
  const name = id ? F.presetName(id, max) : null;
  if (name === undefined) return;  // film list not loaded yet
  if (name) { if (cur !== name) setEdit(no, "name", name); return; }
  if (prev && cur !== undefined && cur === F.presetName(prev, max)) setEdit(no, "name", undefined);
}

// Choosing a built-in film puts its icon on the preset: the user's own picture for that film
// (data folder film_icons/) if there is one, else the film's name plate as a 40×40 icon. A preset
// that already has the user's own icon keeps it; an icon that came from a film follows the film.
async function syncFilmIcon(no, id) {
  const s = slotOf(no), cur = (state.changes[no] || {}).icon;
  if (!s || !editable(s, "icon") || (cur && !cur.film)) return;
  const F = window.DarkslideFilm, plate = id ? F.plateIcon(id) : null;
  if (plate === undefined) return;  // film list not loaded yet
  if (!plate) { if (cur) setEdit(no, "icon", undefined); return; }   // no film, or the user's own LUT
  if (cur && cur.film === id) return;
  try {
    let source = plate.source, small = plate.small, r = plate.crop, pixels = null;
    const url = F.iconUrl(id);
    if (url) {
      const blob = await fetch(url).then((res) => { if (!res.ok) throw new Error("图标读不出来"); return res.blob(); });
      const img = await loadImage(blob).catch(() => { throw new Error("图标读不出来"); });
      const px = await window.DarkslidePixels.decode(blob, img);
      const k = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
      source = document.createElement("canvas");
      source.width = Math.round(img.naturalWidth * k); source.height = Math.round(img.naturalHeight * k);
      source.getContext("2d").drawImage(img, 0, 0, source.width, source.height);
      // the largest centred area with the camera icon's visible shape (38×36), in source pixels
      const w = Math.min(source.width, source.height * VIS.w / VIS.h), h = w * VIS.h / VIS.w;
      r = { sx: (source.width - w) / 2, sy: (source.height - h) / 2, sw: w, sh: h };
      small = document.createElement("canvas");
      small.width = small.height = 40;
      pixels = drawIcon(small, { sx: r.sx / k, sy: r.sy / k, sw: r.sw / k, sh: r.sh / k }, px);
    }
    const up = [await uploadPng(source, "source"), await (pixels ? uploadIcon(pixels) : uploadPng(small, "icon"))];
    const now = state.changes[no] || {};
    if (now.film !== id || (now.icon && !now.icon.film)) return;  // changed again meanwhile
    setEdit(no, "icon", { asset: up[1].asset, source: up[0].asset, style: "image", crop: r, film: id });
  } catch (e) { toast(e.message, true); }
}

function setEdit(no, key, value) {
  mutate((c) => {
    const slot = (c[no] = c[no] || {});
    if (value === undefined) delete slot[key];
    else slot[key] = value;
  });
}

function undo() {
  if (!state.undo.length) return;
  onNameInput.depth = -1;
  state.redo.push(snapshot());
  restore(state.undo.pop());
  scheduleDraft();
  renderAll();
}

function redo() {
  if (!state.redo.length) return;
  onNameInput.depth = -1;
  state.undo.push(snapshot());
  restore(state.redo.pop());
  scheduleDraft();
  renderAll();
}

function scheduleDraft() {
  invalidateFilmPrepare();
  state.draftState = "正在自动保存…";
  renderSaveState();
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(pushDraft, 500);
}

async function pushDraft() {
  try {
    const p = await api("PUT", "/api/projects/" + state.project.id + "/draft", draftBody());
    state.project = p;
    state.draftState = p.unsaved ? "草稿已自动保存" : "已保存";
  } catch (e) {
    state.draftState = "自动保存失败";
    toast(e.message, true);
  }
  renderSaveState();
}

async function saveProject() {
  clearTimeout(state.saveTimer);
  const projectId = state.project.id;
  const before = JSON.stringify(draftBody(true)), body = JSON.parse(before);
  try {
    await api("PUT", "/api/projects/" + projectId + "/draft", body);
    if (!state.project || state.project.id !== projectId) return false;
    const p = await api("POST", "/api/projects/" + projectId + "/save");
    if (!state.project || state.project.id !== projectId || JSON.stringify(draftBody(true)) !== before) return false;
    state.project = p;
    if (state.orderProblem && "menu_order" in body) { state.orderProblem = null; renderAll(); }
    state.draftState = "已保存 " + p.saved_at.slice(11, 16);
    const flash = $("saveState").parentElement;
    flash.classList.remove("flash"); void flash.offsetWidth; flash.classList.add("flash");
    toast("已保存（第 " + p.versions.length + " 版）");
    renderSaveState();
    return true;
  } catch (e) { if (state.project && state.project.id === projectId) toast(e.message, true); }
  renderSaveState();
  return false;
}

// Image Control menu order: only where the project's model can be sorted (GR III 2.10, ordinary and HDF).
function orderOn() { return !!(state.project && state.project.capabilities && state.project.capabilities.menu_order); }
// Whether the order goes into the camera when generating (GR III); GR IIIx only sorts and saves it for now.
function orderWrites() { return !!(state.project && state.project.capabilities && state.project.capabilities.menu_order_write); }
// 柔光 / 65:24: whether the model can take the switch yet, and whether this server knows the switches at all.
function featureOn(name) { return !!(state.project && state.project.capabilities && state.project.capabilities[name]); }
function featuresKnown() { return !!(state.project && state.project.capabilities && "soft_glow" in state.project.capabilities); }

// Every draft write (autosave, ⌘S, quit, page close) sends this. menu_order null = back to the factory
// order. The key is left out, so the server keeps what it has, when the model cannot be sorted, when the
// order in the page is not a usable one (never sent, so renames and the rest still save), and while a
// stored order the server could not read waits for the user. save = true (⌘S, and so 生成) is the
// confirmation the notice asks for: it sends the order shown, which clears the unreadable one.
function draftBody(save) {
  const body = { changes: state.changes };
  if (featuresKnown()) body.camera_features = Object.assign({}, state.features);
  if (!orderOn()) {
    if (state.orderProblem && save) body.menu_order = null;
    return body;
  }
  if (state.orderProblem && !save) return body;
  const order = window.DarkslideOrder.payload(state.order);
  if (order !== undefined) body.menu_order = order;
  return body;
}
function hasEdits() {
  return Object.keys(state.changes).length > 0 || (orderOn() && window.DarkslideOrder.payload(state.order) != null) ||
    Object.keys(state.features || {}).length > 0;
}

// ---------------------------------------------------------------- render
function renderAll() {
  renderFeature();
  renderShutdown();
  renderBanner();
  renderSlots();
  renderEditor();
  renderPresetCard();
  renderSamples();
  renderParams();
  renderChanges();
  renderSaveState();
  window.DarkslideOrder.render();
}

function renderBanner() {
  const t = state.template;
  $("templateBanner").hidden = !!t.reviewed;
  $("templateBanner").textContent = t.review_note || "";
  $("templateState").textContent = t.reviewed ? t.model + " " + t.firmware : "不可用";
}

// The preset column's icon (design boards 25k/25l, user 2026-10-26): a monospace abbreviation tile,
// two lines for the three monochrome variants; the user's own icon, when set, replaces it.
const TILE_TOP = { Soft: "Soft", Hard: "Hard", Hi: "Hi" };
// On screen an icon shows its source picture, filling the rounded square (the corners are cut by the
// frame's radius), not the 40×40 written to the camera (user, 2026-10-26). The camera file is unchanged.
// The <img> starts with the 40×40 and swaps to the full picture once it is drawn; drawings are cached.
const iconViews = new Map();
function iconView(icon) {
  if (!icon) return Promise.resolve(null);
  const key = icon.style === "factory" ? "f:" + icon.text : icon.source && icon.crop ? icon.source + ":" + JSON.stringify(icon.crop) : "";
  if (!key) return Promise.resolve(null);
  if (!iconViews.has(key)) iconViews.set(key, (async () => {
    if (icon.style === "factory") return iconHiRes(icon);
    const im = await new Promise((ok) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => ok(null); i.src = assetUrl(icon.source); });
    if (!im) return null;
    const { sx, sy, sw, sh } = icon.crop, side = Math.min(sw, sh), S = 192;  // the square at the crop's centre
    const c = document.createElement("canvas"), g = c.getContext("2d");
    c.width = c.height = S;
    g.imageSmoothingQuality = "high";
    g.drawImage(im, sx + (sw - side) / 2, sy + (sh - side) / 2, side, side, 0, 0, S, S);
    return c.toDataURL("image/png");
  })().catch(() => null));
  return iconViews.get(key);
}
function iconImg(icon, attrs) {
  const img = el("img", Object.assign({ src: assetUrl(icon.asset), alt: "" }, attrs || {}));
  iconView(icon).then((url) => { if (url) { img.src = url; img.classList.add("full"); } });
  return img;
}

function slotTile(slot) {
  const edit = state.changes[slot.no];
  const box = el("span", { class: "tile", "aria-hidden": "true" });
  if (edit && edit.icon) { box.classList.add("img"); box.append(iconImg(edit.icon)); return box; }
  const abbr = String(slot.abbr || ""), top = TILE_TOP[abbr];
  const main = top ? "BW" : /^\d+$/.test(abbr) ? "C" + abbr : abbr;
  if (top) box.append(el("span", { class: "tile-top", text: top }));
  box.append(el("span", { class: "tile-main len" + Math.min(main.length, 5), text: main }));
  return box;
}

function slotIcon(slot, cls) {
  const edit = state.changes[slot.no];
  const box = el("span", { class: cls, "aria-hidden": "true" });
  if (edit && edit.icon) { box.classList.add("img"); box.append(iconImg(edit.icon)); }
  else if (factoryUrl(slot.no)) { box.classList.add("img"); box.append(el("img", { src: factoryUrl(slot.no), alt: "" })); }
  else box.textContent = slot.abbr;
  return box;
}

function refocus(container, before) {
  // re-rendering replaces the buttons; put focus back on the same item
  if (before === null) return;
  const btn = container.children[before];
  if (btn) btn.focus({ preventScroll: true });
}

function focusedIndex(container) {
  const i = Array.prototype.indexOf.call(container.children, document.activeElement);
  return i >= 0 ? i : null;
}

function renderSlots() {
  // A vertical preset list. Every preset opens; inside, only what this preset allows is enabled.
  // Rows are also dragged here to sort the camera menu: mid-drag the list on screen is left as the drag
  // has put it, and the drag redraws it when it ends.
  if (window.DarkslideOrder.dragging()) return;
  setTimeout(() => { state.enterSlots = false; }, 0);
  // Focus stays on the same preset (not the same row): sorting can move it.
  const list = $("slots"), focused = focusedIndex(list);
  const focusedSlot = focused === null ? null : list.children[focused].getAttribute("data-slot");
  $("slotCount").textContent = state.template.slots.length;
  list.replaceChildren(...sidebarSlots().map((s, i) => {
    const locked = !editable(s, "icon") && !editable(s, "rename");
    const edit = state.changes[s.no];
    const what = editable(s, "rename") ? "图标和预设名可改" : editable(s, "icon") ? "仅图标可改" : "暂不开放";
    // Only exceptions are marked (user 2026-10-25: 「可改」 on every row was clutter): edited, icon only, locked.
    const tag = locked ? null : edit ? null : editable(s, "rename") ? null : "仅图标";
    const name = edit && edit.name !== undefined ? edit.name : s.name;
    const film = window.DarkslideFilm.SLOTS.includes(s.no);
    return el("button", {
      class: "slot open" + (s.no === state.sel ? " on" : "") + (locked ? " is-locked" : "") + (s.no === 13 ? " custom-first" : "") + (state.enterSlots ? " enter" : ""),
      style: state.enterSlots ? "animation-delay:" + i * 20 + "ms" : null,
      "data-slot": s.no,
      role: "option", "aria-selected": s.no === state.sel ? "true" : "false",
      title: [s.name, what, window.DarkslideFilm.SLOTS.includes(s.no) ? "可换胶片 LUT，写进相机" : "", window.DarkslideOrder.hint(s.no)].filter(Boolean).join(" · "),
      onclick: () => { if (window.DarkslideOrder.swallowClick()) return; state.sel = s.no; renderAll(); },
    }, [
      window.DarkslideOrder.grip(s.no),
      slotTile(s),
      el("span", { class: "slot-name", text: name }),
      name !== s.name ? el("span", { class: "slot-sub", text: `原 ${s.name}` }) : null,
      edit ? el("span", { class: "slot-dot", "aria-hidden": "true" }) : null,
      tag ? el("span", { class: "slot-tag", text: tag }) : null,
      locked || film ? el("span", { class: "slot-mark " + (locked ? "lock" : "film"), "aria-hidden": "true" }) : null,  // drawn in app.css
    ]);
  }));
  if (focusedSlot !== null) {
    const button = Array.from(list.children).find((b) => b.getAttribute("data-slot") === focusedSlot);
    if (button) button.focus({ preventScroll: true });
  }
}

function renderEditor() {
  const s = slotOf(state.sel);
  const edit = state.changes[s.no] || {};
  window.DarkslideFilm.render(s, edit);
  window.DarkslideAdjust.render(s, edit.grade, "", window.DarkslideFilm.SLOTS.includes(s.no));
  $("slotMeta").textContent = `原 ${s.name}`;  // no slot number: 「07」 read as the 7th row (user 2026-10-25)
  $("slotMeta").dataset.same = edit.name === undefined ? "1" : "";  // longer languages drop it when it only repeats the title
  $("slotTitle").title = edit.name !== undefined ? `原 ${s.name}` : "";
  $("slotTitle").textContent = edit.name !== undefined ? edit.name : s.name;
  if (factoryUrl(s.no)) $("iconFactory").replaceChildren(el("img", { src: factoryUrl(s.no), alt: s.name + " 原厂图标" }));
  else $("iconFactory").textContent = s.abbr;
  const iconNew = $("iconNew");
  if (edit.icon) {
    iconNew.className = "v1-icon has";
    const reveal = state.revealAsset === edit.icon.asset;
    iconNew.replaceChildren(iconImg(edit.icon, { class: reveal ? "reveal" : null, alt: s.name + " 新图标" }));
    if (reveal) state.revealAsset = null;
  } else {
    iconNew.className = "v1-icon";
    iconNew.replaceChildren(el("span", { text: "未修改" }));
  }
  const iconOpen = editable(s, "icon");
  $("iconField").classList.toggle("locked", !iconOpen);
  for (const id of ["btnUpload", "btnFactoryStyle", "btnIconReset", "iconNew"]) $(id).disabled = !iconOpen;
  $("btnIconReset").disabled = !iconOpen || !edit.icon;
  // Fewer words (user, 2026-10-17): the hint only explains a locked icon.
  $("iconHint").textContent = iconOpen ? "" : "这个预设的图标" + (s.icon === "closed" ? "不开放编辑" : "稍后开放");
  $("iconHint").hidden = iconOpen;
  $("btnFactoryStyle").hidden = !iconOpen;
  $("lookBadge").hidden = !iconOpen && !editable(s, "rename");

  const nameOpen = editable(s, "rename");
  const limit = nameMax(s);
  const input = $("nameInput");
  $("nameField").classList.toggle("locked", !nameOpen);
  input.disabled = !nameOpen;
  input.maxLength = limit; // typing and paste stop at the limit
  input.placeholder = s.factory_en;
  const value = edit.name !== undefined ? edit.name : s.factory_en;
  if (document.activeElement !== input) input.value = value;
  $("btnNameReset").disabled = !nameOpen || edit.name === undefined;
  const checks = nameOpen ? Names.check(value, limit) : [];
  const ok = checks.every((c) => c.ok);
  $("nameBox").classList.toggle("bad", nameOpen && !ok);
  $("nameCount").textContent = nameOpen ? value.length + " / " + limit : "";
  // Only problems are listed (user, 2026-10-17): nothing is shown when the name passes.
  const failed = checks.filter((c) => !c.ok);
  const items = failed.map((c) => el("li", { class: "bad" }, [el("span", { class: "mk", text: "✕" }), el("span", { text: c.message })]));
  $("nameBox").title = nameOpen ? "原厂英文名 " + s.factory_en + "。英文和所选相机菜单语言显示新预设名，其他语言保持原厂；超过 " + s.factory_en.length + " 个字符时，另有一种不常用的语言也会显示新预设名。" : "";
  if (!nameOpen) {
    items.push(el("li", { class: "info" }, [el("span", { class: "mk", text: "i" }), el("span", { text: "这个预设的预设名" + (s.rename === "closed" ? "暂不开放" : "稍后开放") + (s.note ? "：" + s.note : "") })]));
  }
  $("nameChecks").replaceChildren(...items);

  const detail = state.status.detail || {};
  const grainStage = (detail.grain || {})[String(s.no)] || "closed";
  const grainOpen = grainStage === "menu";
  const grainOn = edit.grain === true;
  $("grainField").classList.toggle("locked", !grainOpen);
  $("btnGrain").disabled = !grainOpen;
  $("btnGrain").setAttribute("aria-checked", grainOn ? "true" : "false");
  $("grainLabel").textContent = grainStage === "factory" ? "原厂已有" : grainOn ? "开 · 强度在相机里调" : "关";
  $("grainBadge").textContent = grainStage === "factory" ? "原厂已有" : grainOpen ? "可改" : "研究中";
  $("grainBadge").className = "v1-pill" + (grainOpen && grainStage !== "factory" ? " solid" : "");
  // Short line under the switch (user, 2026-10-17: switch first, a few words below).
  $("grainHint").textContent = grainStage === "factory"
    ? "这个预设本来就有颗粒，在相机的详细设置里调。"
    : grainOpen
      ? "强度在相机里调；颗粒很细，放大才看得清。"
      : "只有负片能开颗粒。";
}

function renderChanges() {
  const rows = [];
  for (const s of listedSlots()) {
    const edit = state.changes[s.no];
    if (!edit) continue;
    if (edit.icon) {
      rows.push(el("div", { class: "change", "data-key": s.no + "i" }, [
        el("span", { class: "thumb" }, iconImg(edit.icon)),
        el("span", { class: "txt" }, [el("span", { text: s.name + " 图标" }), el("span", { text: edit.icon.style === "factory" ? "→ 原厂风格 · " + (edit.icon.text || "") : edit.icon.film ? "→ 胶片图标 · " + window.DarkslideFilm.nameOf(edit.icon.film) : "→ 自定义图片" })]),
        el("button", { "aria-label": "撤销 " + s.name + " 图标改动", text: "×", onclick: (e) => leaveThen(e, () => setEdit(s.no, "icon", undefined)) }),
      ]));
    }
    if (edit.grain === true) {
      rows.push(el("div", { class: "change", "data-key": s.no + "g" }, [
        el("span", { class: "thumb", text: "粒" }),
        el("span", { class: "txt" }, [el("span", { text: s.name + " 颗粒项" }), el("span", { text: "→ 开放 · 档位在相机里调" })]),
        el("button", { "aria-label": "撤销 " + s.name + " 颗粒项改动", text: "×", onclick: (e) => leaveThen(e, () => setEdit(s.no, "grain", undefined)) }),
      ]));
    }
    if (edit.film) {
      const candidate = window.DarkslideFilm.candidateReady();
      const ready = candidate || window.DarkslideFilm.writerReady();
      rows.push(el("div", { class: "change" + (ready ? "" : " pending"), "data-key": s.no + "f" }, [
        el("span", { class: "thumb", text: "片" }),
        el("span", { class: "txt" }, [el("span", { text: s.name + " 胶片" + (candidate ? " · 离线候选" : ready ? "" : " · 写入已接通，实机测试后才参与生成") }), el("span", { text: "→ " + window.DarkslideFilm.nameOf(edit.film) })]),
        el("button", { "aria-label": "撤销 " + s.name + " 胶片改动", text: "×", onclick: (e) => leaveThen(e, () => setEdit(s.no, "film", undefined)) }),
      ]));
    }
    if (edit.grade) {
      const candidate = window.DarkslideFilm.candidateReady() && window.DarkslideFilm.SLOTS.includes(s.no);
      const fit = candidate && window.DarkslideFilm.fitState(s, edit);
      const ready = fit && fit.fit && !["超出范围", "预览不可用", "待拟合", "检查中"].includes(fit.level);
      rows.push(el("div", { class: "change" + (ready ? "" : " pending"), "data-key": s.no + "c" }, [
        el("span", { class: "thumb", text: "色" }),
        el("span", { class: "txt" }, [el("span", { text: s.name + (candidate ? ready ? " 调色 · 离线候选" : " 调色 · 拟合后进入离线候选" : " 调色 · 预览和导出用") }), el("span", { text: "→ 自己调色" + (edit.film ? "（起点 " + window.DarkslideFilm.nameOf(edit.film) + "）" : "") })]),
        el("button", { "aria-label": "撤销 " + s.name + " 调色", text: "×", onclick: (e) => leaveThen(e, () => setEdit(s.no, "grade", undefined)) }),
      ]));
    }
    if (edit.name !== undefined) {
      const ok = Names.ok(edit.name, nameMax(s));
      rows.push(el("div", { class: "change", "data-key": s.no + "n" }, [
        el("span", { class: "thumb", text: "Aa" }),
        el("span", { class: "txt" }, [el("span", { text: s.name + " 预设名" + (ok ? "" : " · 未通过检查") }), el("span", { text: "→ " + edit.name })]),
        el("button", { "aria-label": "撤销 " + s.name + " 预设名改动", text: "×", onclick: (e) => leaveThen(e, () => setEdit(s.no, "name", undefined)) }),
      ]));
    }
  }
  const sd = state.changes.shutdown;
  if (sd) {
    rows.unshift(el("div", { class: "change", "data-key": "shutdown" }, [
      el("span", { class: "thumb wide" }, el("img", { src: assetUrl(sd.asset), alt: "" })),
      el("span", { class: "txt" }, [el("span", { text: "关机画面" }), el("span", { text: "→ 自定义图片" })]),
      el("button", { "aria-label": "撤销关机画面改动", text: "×", onclick: (e) => leaveThen(e, () => setShutdown(undefined)) }),
    ]));
  }
  if (state.orderProblem) {
    rows.unshift(el("div", { class: "change pending", "data-key": "order" }, [
      el("span", { class: "thumb dashed", text: "序" }),
      el("span", { class: "txt" }, [el("span", { text: "影像控制菜单顺序 · 无法读取" }), el("span", { text: "→ 已按原厂顺序显示，保存后会清除" })]),
      orderOn() ? el("button", { "aria-label": "按原厂顺序确认菜单顺序", text: "×", onclick: (e) => leaveThen(e, () => setOrder(null)) }) : null,
    ]));
  } else if (orderOn() && state.order) {
    const d = window.DarkslideOrder.describe(state.order);
    rows.unshift(el("div", { class: "change", "data-key": "order" }, [
      el("span", { class: "thumb", text: "序" }),
      el("span", { class: "txt" }, [el("span", { text: "影像控制菜单顺序 · 未实测" }), el("span", { text: `→ ${d.moved} 个预设换了位置，第一个是${d.first}` })]),
      el("button", { "aria-label": "恢复原厂菜单顺序", text: "×", onclick: (e) => leaveThen(e, () => setOrder(null)) }),
    ]));
  }
  for (const [name, label] of CAMERA_FEATURES.slice().reverse()) {
    if (!state.features[name]) continue;
    rows.unshift(el("div", { class: "change", "data-key": "feature-" + name }, [
      el("span", { class: "thumb", text: name === "soft_glow" ? "柔" : "宽" }),
      el("span", { class: "txt" }, [el("span", { text: label }), el("span", { text: "→ 装进相机，在相机里开关" })]),
      el("button", { "aria-label": "不装" + label, text: "×", onclick: (e) => leaveThen(e, () => setFeature(name, false)) }),
    ]));
  }
  const known = state.knownRows;
  for (const r of rows) if (known && !known.has(r.dataset.key)) r.classList.add("enter");
  state.knownRows = new Set(rows.map((r) => r.dataset.key));
  const n = rows.length;
  $("changeCount").textContent = "本次改动 · " + n;
  $("genCount").textContent = rows.filter((r) => !r.classList.contains("pending")).length + " 项";
  const np = presetKeys().length;
  $("changeSlots").textContent = [np ? np + " 个预设" : "", sd ? "关机画面" : "", state.orderProblem || (orderOn() && state.order) ? "菜单顺序" : "",
    state.features.wide_ratio ? "画幅" : "", state.features.soft_glow ? "柔光" : ""].filter(Boolean).join(" · ");
  $("changeList").replaceChildren(...(rows.length ? rows : [el("div", { class: "empty", text: "还没有改动" })]));
  // the list scrolls inside the fixed sidebar: bring a newly added change into view
  const fresh = $("changeList").querySelector(".change.enter");
  if (fresh) fresh.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  changeListFade();
}

// Fade the bottom edge while more changes are hidden below.
function changeListFade() {
  const l = $("changeList");
  l.classList.toggle("more", l.scrollHeight - l.scrollTop - l.clientHeight > 4);
}

function leaveThen(e, fn) {
  const row = e.currentTarget.closest(".change");
  if (!row || matchMedia("(prefers-reduced-motion: reduce)").matches) { fn(); return; }
  row.classList.add("leave");
  setTimeout(fn, 180);
}

function renderSaveState() {
  $("saveState").textContent = state.draftState;
  const n = (state.status.builds || []).filter((b) => state.project && b.project_id === state.project.id).length;
  $("buildCount").textContent = n;
}

// ---------------------------------------------------------------- name
function onNameInput() {
  const s = slotOf(state.sel);
  const v = $("nameInput").value;
  const current = (state.changes[s.no] || {}).name;
  if (v === s.factory_en) { if (current !== undefined) setEdit(s.no, "name", undefined); else renderEditor(); return; }
  if (v === current) return;
  // collapse consecutive keystrokes into one undo step, but only while the top
  // undo entry is still the one this typing run created (any other edit, undo
  // or redo in between starts a new step)
  const typing = onNameInput.lastSlot === s.no && Date.now() - (onNameInput.lastAt || 0) < 1200 &&
    onNameInput.depth === state.undo.length && state.undo.length > 0;
  if (typing) {
    state.changes[s.no] = Object.assign({}, state.changes[s.no], { name: v });
    state.redo = [];
    scheduleDraft();
    renderSlots(); renderEditor(); renderPresetCard(); renderSamples(); renderChanges();
  } else {
    setEdit(s.no, "name", v);
  }
  onNameInput.lastSlot = s.no;
  onNameInput.lastAt = Date.now();
  onNameInput.depth = state.undo.length;
}

// ---------------------------------------------------------------- icons
async function uploadPng(canvas, kind) {
  const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
  return api("POST", "/api/projects/" + state.project.id + "/assets?kind=" + kind, blob);
}

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("图片打不开"));
    img.src = url;
  });
}

// img is what the crop dialog shows; px the same picture's pixels for the icon itself.
const crop = { img: null, px: null, icon: null, scale: 1, base: 1, ox: 0, oy: 0, drag: null };

function cropClamp() {
  const c = $("cropCanvas");
  const w = crop.img.naturalWidth * crop.scale, h = crop.img.naturalHeight * crop.scale;
  crop.ox = Math.min(0, Math.max(c.width - w, crop.ox));
  crop.oy = Math.min(0, Math.max(c.height - h, crop.oy));
}

function cropRect() {
  const c = $("cropCanvas");
  return { sx: -crop.ox / crop.scale, sy: -crop.oy / crop.scale, sw: c.width / crop.scale, sh: c.height / crop.scale };
}

// The 40×40 icon of the rectangle r of px (DarkslidePixels.decode), drawn into target;
// returns its RGBA. Resized by DarkslidePixels, not the canvas, so every browser makes
// the same icon. The visible area is fully opaque (factory contract: alpha 255 inside, 0 outside).
function drawIcon(target, r, px) {
  const inner = window.DarkslidePixels.resample(px, r, VIS.w, VIS.h);
  const icon = new ImageData(40, 40);
  for (let y = 0; y < VIS.h; y++) icon.data.set(inner.subarray(y * VIS.w * 4, (y + 1) * VIS.w * 4), ((VIS.y + y) * 40 + VIS.x) * 4);
  target.getContext("2d").putImageData(icon, 0, 0);
  return icon.data;
}

// An icon's pixels as the PNG the server keeps, the same bytes in every browser.
function uploadIcon(rgba) {
  return api("POST", "/api/projects/" + state.project.id + "/assets?kind=icon",
    new Blob([window.DarkslidePixels.png(40, 40, rgba)], { type: "image/png" }));
}

function cropRender() {
  const c = $("cropCanvas"), g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  g.imageSmoothingQuality = "high";
  g.drawImage(crop.img, crop.ox, crop.oy, crop.img.naturalWidth * crop.scale, crop.img.naturalHeight * crop.scale);
  const p1 = $("cropPreview1");
  crop.icon = drawIcon(p1, cropRect(), crop.px);
  const p4 = $("cropPreview4").getContext("2d");
  p4.imageSmoothingEnabled = false;
  p4.clearRect(0, 0, 160, 160);
  p4.drawImage(p1, 0, 0, 160, 160);
}

async function startCrop(file) {
  // a picture chosen while the last one is still decoding wins; the last one is dropped
  const turn = crop.turn = (crop.turn || 0) + 1;
  try {
    const img = await loadImage(file);
    const px = await window.DarkslidePixels.decode(file, img);
    if (turn !== crop.turn) return;
    crop.img = img; crop.px = px;
  } catch (e) { if (turn === crop.turn) toast(e.message, true); return; }
  const c = $("cropCanvas");
  crop.base = Math.max(c.width / crop.img.naturalWidth, c.height / crop.img.naturalHeight);
  crop.scale = crop.base;
  $("cropZoom").value = "1";
  crop.ox = (c.width - crop.img.naturalWidth * crop.scale) / 2;
  crop.oy = (c.height - crop.img.naturalHeight * crop.scale) / 2;
  cropRender();
  $("cropDialog").showModal();
}

function zoomTo(z, cx, cy) {
  const c = $("cropCanvas");
  cx = cx === undefined ? c.width / 2 : cx;
  cy = cy === undefined ? c.height / 2 : cy;
  const next = crop.base * Math.min(8, Math.max(1, z));
  crop.ox = cx - (cx - crop.ox) * (next / crop.scale);
  crop.oy = cy - (cy - crop.oy) * (next / crop.scale);
  crop.scale = next;
  $("cropZoom").value = String(next / crop.base);
  cropClamp();
  cropRender();
}

async function applyCrop() {
  const s = slotOf(state.sel);
  const btn = $("cropApply");
  btn.disabled = true;
  try {
    const src = document.createElement("canvas");
    const k = Math.min(1, 1600 / Math.max(crop.img.naturalWidth, crop.img.naturalHeight));
    src.width = Math.round(crop.img.naturalWidth * k);
    src.height = Math.round(crop.img.naturalHeight * k);
    src.getContext("2d").drawImage(crop.img, 0, 0, src.width, src.height);
    const source = await uploadPng(src, "source");
    const icon = await uploadIcon(crop.icon);
    const r = cropRect();
    state.revealAsset = icon.asset;
    setEdit(s.no, "icon", {
      asset: icon.asset, source: source.asset, style: "image",
      crop: { sx: r.sx * k, sy: r.sy * k, sw: r.sw * k, sh: r.sh * k },
    });
    $("cropDialog").close();
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
}

// The user's icon at card size: the camera gets the 40×40 copy, the card is drawn again from the
// original upload (same crop) or, for a 原厂风格 icon, from its text at a larger scale.
async function iconHiRes(icon) {
  const S = 320, c = document.createElement("canvas");
  c.width = c.height = S;
  if (icon && icon.style === "factory" && icon.text) {
    c.getContext("2d").scale(S / 40, S / 40);
    drawFactory(c, icon.text);
    return c.toDataURL("image/png");
  }
  if (!icon || !icon.source || !icon.crop) return null;
  const im = await new Promise((ok) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => ok(null); i.src = assetUrl(icon.source); });
  if (!im) return null;
  // just the cropped picture (the 38×36 shape the camera shows), without the camera's empty margin
  const { sx, sy, sw, sh } = icon.crop, g = c.getContext("2d");
  c.height = Math.round(S * VIS.h / VIS.w);
  g.imageSmoothingQuality = "high";
  g.drawImage(im, sx, sy, sw, sh, 0, 0, c.width, c.height);
  return c.toDataURL("image/png");
}

function drawFactory(canvas, text) {
  const g = canvas.getContext("2d");
  g.clearRect(0, 0, 40, 40);
  g.fillStyle = "#808080";
  g.fillRect(VIS.x, VIS.y, VIS.w, VIS.h);
  g.fillStyle = "#353536";
  g.fillRect(VIS.x + 1, VIS.y + 1, VIS.w - 2, VIS.h - 2);
  g.fillStyle = "#FFFFFF";
  g.textAlign = "center";
  g.textBaseline = "middle";
  let size = 18;
  do {
    g.font = "bold " + size + "px 'Helvetica Neue', Helvetica, Arial, sans-serif";
    size -= 1;
  } while (size > 7 && g.measureText(text).width > VIS.w - 6);
  g.fillText(text, 20, VIS.y + VIS.h / 2 + 1);
}

function factoryRender() {
  const text = $("factoryText").value;
  drawFactory($("factoryPreview1"), text);
  const p4 = $("factoryPreview4").getContext("2d");
  p4.imageSmoothingEnabled = false;
  p4.clearRect(0, 0, 160, 160);
  p4.drawImage($("factoryPreview1"), 0, 0, 160, 160);
}

async function applyFactory() {
  const s = slotOf(state.sel);
  const text = $("factoryText").value.trim();
  if (!text || !/^[\x20-\x7e]{1,5}$/.test(text)) { toast("请输入 1–5 个英文字母、数字或符号", true); return; }
  try {
    const icon = await uploadPng($("factoryPreview1"), "icon");
    state.revealAsset = icon.asset;
    setEdit(s.no, "icon", { asset: icon.asset, style: "factory", text });
    $("factoryDialog").close();
  } catch (e) { toast(e.message, true); }
}

// Read-only check of the saved snapshot. No auto-save, fit, generation or export.
let filmPrepareEpoch = 0;
function invalidateFilmPrepare() {
  filmPrepareEpoch++;
  const status = $("filmPrepareStatus"), slots = $("filmPrepareSlots");
  if (status) status.textContent = "选择已改变，请保存后重新核对。";
  if (slots) slots.replaceChildren();
}
async function prepareFilms() {
  if (!state.project) return;
  const pid = state.project.id, snapshot = JSON.stringify(state.changes), epoch = ++filmPrepareEpoch;
  const valid = () => state.project && state.project.id === pid &&
    JSON.stringify(state.changes) === snapshot && filmPrepareEpoch === epoch;
  $("filmPrepareSlots").replaceChildren();
  $("filmPrepareStatus").textContent = "正在核对已保存选择…";
  try {
    const result = await api("POST", "/api/projects/" + pid + "/film-prepare", {
      expected_changes: JSON.parse(snapshot), request_id: String(epoch)
    });
    if (!valid()) return;
    if (result.project_id !== pid || result.request_id !== String(epoch) ||
        result.production_approved !== false || result.full_image_created !== false ||
        result.decoded_growth_bytes !== 0) throw new Error("核对响应不符合只读要求");
    if (!result.body || result.body.status !== "protected_windows_unchanged" ||
        result.body.compatibility_approved !== false || result.body.device_tested !== false)
      throw new Error(result.body && result.body.summary || "缺少有效的机身保护核对结果");
    for (const row of result.slots) {
      const el = document.createElement("div");
      el.textContent = "位置 " + row.slot + "：" + (row.factory ? "保留原厂" : window.DarkslideFilm.nameOf(row.film));
      $("filmPrepareSlots").append(el);
    }
    $("filmPrepareStatus").textContent = (result.data_verified && result.shared_reference_verified &&
      result.coverage_verified ? "离线数据核对通过。" : "核对未通过。") + result.body.summary + "。" + result.note;
  } catch (e) {
    if (valid()) $("filmPrepareStatus").textContent = "核对未通过：" + e.message;
  }
}

// ---------------------------------------------------------------- generate
// The camera menu language the firmware is made for; 生成 stays off until one is chosen.
// A project without one gets the computer's language preselected (and saved), with a
// note to check it against the camera; picking another one by hand removes the note.
const MENU_LANGS = ["ja", "en", "fr", "de", "es", "pt", "it", "nl", "da", "sv", "fi", "pl", "cs", "hu",
  "tr", "el", "ru", "ko", "zh-TW", "zh-CN", "th"];
function guessMenuLanguage() {
  const tags = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ""];
  for (const tag of tags) {
    const t = String(tag).toLowerCase();
    if (/^zh-(tw|hk|mo)\b|hant/.test(t)) return "zh-TW";
    if (t.startsWith("zh")) return "zh-CN";
    const base = t.split("-")[0];
    if (MENU_LANGS.includes(base)) return base;
  }
  return null;
}

function renderMenuLanguage() {
  const p = state.project || {};
  if (p.id && !p.menu_language && !(state.menuLangTried || {})[p.id]) {
    state.menuLangTried = { ...(state.menuLangTried || {}), [p.id]: true };
    const guess = guessMenuLanguage();
    if (guess) { setMenuLanguage(guess, true); }
  }
  const chosen = p.menu_language || "";
  $("menuLangAuto").hidden = !(p.id && state.menuLangAuto === p.id && chosen);
  $("menuLangSelect").value = chosen;
  $("menuLangBlock").classList.toggle("missing", !chosen);
  const blocked = !!(p.capabilities && !p.capabilities.full_firmware && !p.capabilities.candidate_generation);
  $("btnGenerate").disabled = blocked || !chosen;
  $("btnGenerate").title = chosen ? "" : "先选择相机使用的菜单语言";
}

async function setMenuLanguage(code, auto = false) {
  if (!state.project) return;
  if (!auto) state.menuLangAuto = null;  // chosen by hand: no reminder
  if (state.project.menu_language === code) { renderMenuLanguage(); return; }
  const pid = state.project.id;
  try {
    const p = await api("PUT", "/api/projects/" + pid + "/menu-language", { menu_language: code });
    if (!state.project || state.project.id !== pid) return;  // switched project meanwhile
    if (auto) state.menuLangAuto = pid;
    state.project = { ...state.project, menu_language: p.menu_language };
    renderMenuLanguage();
  } catch (e) { toast(e.message, true); renderMenuLanguage(); }
}

async function generate() {
  if (state.iconSyncs && state.iconSyncs.size) await Promise.allSettled([...state.iconSyncs]);
  if (!state.project.menu_language) { toast("先选择相机使用的菜单语言", true); return; }
  const candidate = !!(state.project.capabilities && state.project.capabilities.candidate_generation);
  const recipeExtension = candidate && !!state.project.capabilities.candidate_recipe_extension && $("iiixRecipeExtension").checked;
  if (state.project.capabilities && !state.project.capabilities.full_firmware && !candidate) {
    toast(state.project.capabilities.note, true); return;
  }
  const bad = state.template.slots.filter((s) => {
    const e = state.changes[s.no];
    return e && e.name !== undefined && !Names.ok(e.name, nameMax(s));
  });
  if (bad.length) { toast(bad.map((s) => s.name).join("、") + " 的预设名没有通过检查", true); return; }
  // Generating saves first, which would clear a stored order that could not be read: the user decides first.
  if (state.orderProblem) {
    toast(orderOn() ? "已存的菜单顺序无法读取：请先在左栏重新拖好顺序或点「恢复原厂」，或按 ⌘S 保存（会按原厂顺序清除它），再生成"
      : "已存的菜单顺序无法读取：请先按 ⌘S 保存（会按原厂顺序清除它），再生成", true);
    return;
  }
  // GR IIIx sorts and saves an order, but cannot write it yet: say so here instead of skipping it.
  if (orderOn() && !orderWrites() && window.DarkslideOrder.payload(state.order) != null) {
    toast("GR IIIx 的菜单顺序还在适配，暂时不能写进相机。要生成的话，请先在左栏点「恢复原厂」。", true);
    return;
  }
  if (!hasEdits() && !recipeExtension) { toast("还没有改动", true); return; }
  if (!(state.status.prefs || {}).preflight_seen) {
    $("preflightRecovery").textContent = candidate
      ? "本次为 GR IIIx 1.60 固件，官方原件为 fwdc243b.bin；标准版已有用户实机刷入使用，城市版、HDF 和刷回官方 1.60 尚未实机验证。"
      : "准备一张只放官方 2.10 原件 fwdc239b.bin 的救机卡。";
    $("preflightDialog").showModal(); return;
  }
  // IIIx models other than the standard one (tested on a camera by a user) ask again every time; so does
  // every IIIx when the build carries 菜单排序 or 柔光, which are tested on GR III only (已适配·未实测).
  const fresh = [orderOn() && window.DarkslideOrder.payload(state.order) != null ? "菜单排序" : "",
    (state.features || {}).soft_glow ? "柔光" : ""].filter(Boolean);
  const untestedVariant = state.project.camera_variant !== "GR IIIx";
  if (candidate && (untestedVariant || fresh.length) && !state.untestedAck) {
    const body = state.project.camera_variant || state.project.target_body || "";
    $("untestedBody").textContent = body ? "（这次是 " + body + "）" : "";
    $("untestedVariant").hidden = !untestedVariant;
    $("untestedFeatures").hidden = !fresh.length;
    $("untestedFeatureNames").textContent = fresh.join("、");
    $("untestedExtension").hidden = !recipeExtension;
    $("untestedBox").checked = false;
    $("untestedOk").disabled = true;
    $("untestedDialog").showModal(); return;
  }
  state.untestedAck = false;
  const projectId = state.project.id;
  if (!(await saveProject()) || !state.project || state.project.id !== projectId || state.project.unsaved) return;
  const expected = JSON.parse(JSON.stringify(state.changes));
  showPending();
  try {
    // Fitting happens here: presets whose 调色 or imported LUT is not fitted yet are fitted first.
    await window.DarkslideFilm.fitPending(state.template.slots, expected, (slot, i, n) => {
      $("doneTitle").textContent = "正在把调色拟合进相机…";
      $("doneText").textContent = "「" + slot.name + "」（" + i + " / " + n + "），每个几秒钟；拟合完接着生成。";
    });
    if (!state.project || state.project.id !== projectId) return;
    $("doneTitle").textContent = "正在生成并核对…";
    $("doneText").textContent = "从官方原件重新生成，然后用另一套代码拆开读回、逐项核对。根据组合可能需要几十秒到几分钟。";
    state.generating = projectId;
    const manifest = await apiJob(projectId, candidate ? "iiix-candidate" : "generate", {
      body: candidate ? {expected_changes: expected, recipe_extension: recipeExtension} : undefined,
      live: () => !!state.project && state.project.id === projectId });
    finishGeneration(projectId, manifest);
  } catch (e) {
    failGeneration(projectId, e);
  } finally {
    if (state.generating === projectId) state.generating = null;
  }
}

function finishGeneration(projectId, manifest) {
  if (!manifest || !state.project || state.project.id !== projectId) return;
  state.status.builds = (state.status.builds || []).filter((b) => b.build_id !== manifest.build_id).concat([{
    build_id: manifest.build_id, project_id: manifest.project_id, output_sha256: manifest.output_sha256, created_at: manifest.created_at }]);
  showResult(true, manifest);
}

function failGeneration(projectId, e) {
  if (state.project && state.project.id === projectId) showResult(false, { error: e.message, checks: (e.data && e.data.checks) || [] });
}

// A page that was reloaded (or a project opened again) while its 生成 is still running picks the job up
// again, instead of leaving it running unseen.
async function resumeGeneration(projectId) {
  if (state.generating === projectId) return;
  let jobs;
  try { jobs = (await api("GET", "/api/projects/" + projectId + "/jobs")).jobs; } catch (e) { return; }
  const job = jobs.find((j) => j.task === "generate" || j.task === "iiix-candidate");
  if (!job || state.generating === projectId || !state.project || state.project.id !== projectId) return;
  state.generating = projectId;
  showPending();
  try {
    finishGeneration(projectId, await pollJob(projectId, job, { live: () => !!state.project && state.project.id === projectId }));
  } catch (e) {
    failGeneration(projectId, e);
  } finally {
    if (state.generating === projectId) state.generating = null;
  }
}

function showPending() {
  $("doneTag").textContent = "正在生成";
  $("doneTitle").textContent = "正在生成并核对…";
  $("doneText").textContent = "从官方原件重新生成，然后用另一套代码拆开读回、逐项核对。根据组合可能需要几十秒到几分钟。";
  // The rescue line shows while generating too: name this series' official version, not 2.10 for GR IIIx.
  $("doneRescueFw").textContent = (state.template && state.template.firmware) || "2.10";
  $("doneTable").replaceChildren();
  $("doneChecks").replaceChildren();
  $("doneMore").hidden = true;
  $("doneDownloads").hidden = true;
  $("dlNameHint").hidden = true;
  $("doneGuide").hidden = true;
  show("viewDone");
}

function showResult(ok, data) {
  $("doneTag").textContent = ok ? "完成" : "未完成";
  $("doneTitle").textContent = ok ? (data.experimental ? "离线候选已生成，机身待测" : "固件已生成") : "还不能生成";
  $("doneText").textContent = ok ? (data.experimental ? "离线核对通过，机身还没实测。刷机风险自负。" : "离线核对全部通过。刷机风险自负。") : data.error;
  // The long note, the table and every check sit in one line that opens on demand; failures open it.
  const checks = data.checks || [];
  const bad = checks.filter((c) => !c.ok).length;
  $("doneDetail").textContent = ok ? (data.verification || "") : "";
  $("doneDetail").hidden = !ok || !data.verification;
  $("doneMoreSum").textContent = ok
    ? "✓ 离线核对 " + checks.length + " 项全部通过 · 查看明细"
    : (bad ? "✕ " + bad + " 项没通过 · 查看明细" : "查看明细");
  $("doneMore").open = !ok && bad > 0;
  $("doneMore").hidden = false;
  const rows = ok
    ? [["工程", data.project_title], ["改动", data.summary.map((x) => x.preset + " " + x.what + " → " +
        (x.what === "胶片" && window.DarkslideFilm ? window.DarkslideFilm.nameOf(x.value) : x.value)).join("；")],
       ["版本", (data.firmware || state.template.firmware) + "（不变）"], ["SHA-256", data.output_sha256],
       ["验证", data.experimental ? "离线核对通过，未实机验证" : "离线核对通过"],
       ...(data.experimental ? [["文件大小", data.output_bytes ? (data.output_bytes / 1048576).toFixed(2) + " MiB" : "见下载文件"], ["配方扩展", data.decoded_growth_bytes ? "解码模块追加 " + data.decoded_growth_bytes + " 字节；曲线自身不扩容" : "未启用；曲线自身不扩容"]] : [])]
    : [["工程", state.project.title], ["模板", state.template.reviewed ? state.template.model + " " + state.template.firmware : "不可用"]];
  $("doneTable").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { class: k === "SHA-256" ? "mono sha" : "", text: v })]));
  // Failed checks first, so they are the first thing seen when the line opens.
  $("doneChecks").replaceChildren(...checks.filter((c) => !c.ok).concat(checks.filter((c) => c.ok)).map((c) => el("li", { class: c.ok ? "" : "bad" }, [
    el("span", { class: "mk", text: c.ok ? "✓" : "✕" }), el("span", { text: c.title + (c.detail ? "（" + c.detail + "）" : "") })])));
  const dl = $("doneDownloads");
  dl.hidden = !ok;
  if (ok) {
    const fname = data.output_filename || state.template.official_inputs[0].filename;
    $("dlNameReal").textContent = fname;
    $("dlNameDup").textContent = fname.replace(/\.bin$/, " (1).bin");
    $("dlFirmware").download = fname;
    $("dlFirmware").href = "/api/builds/" + data.build_id + "/firmware";
    $("dlManifest").href = "/api/builds/" + data.build_id + "/manifest";
  }
  $("dlNameHint").hidden = !ok;
  // Same flashing card for GR III and GR IIIx (Ricoh's IIIx steps are the same; user, 2026-10-17):
  // only the file, the version and what has been tested differ.
  $("doneGuide").hidden = !ok;
  $("doneRescueLine").hidden = ok;  // the guide card already asks about the rescue card
  $("doneRescueFw").textContent = (state.template && state.template.firmware) || "2.10";
  if (ok) {
    guideForSeries();
    $("guideChecks").replaceChildren(...guideChecks(data.summary || []).map((t) => el("li", { text: t })));
    setGuideOpen(guideOpenPref());
  }
  show("viewDone");
}

function guideForSeries() {
  const s = SERIES.find((x) => x.short === state.template.model) || SERIES[0], iiix = s.short === "GR IIIx";
  for (const n of document.querySelectorAll("#doneGuide .guide-fw")) n.textContent = s.fw;
  for (const n of document.querySelectorAll("#doneGuide .guide-ver")) n.textContent = s.ver;
  $("guideBackDl").href = RICOH_FW + s.zip;
  $("guideRescueWhy").textContent = iiix ? "（GR IIIx 还没在真机上实测过，一定要备好）" : "（虽然作者已在自己的 GR III 上实际验证成功，但以防万一）";
  $("guideBackTested").textContent = iiix ? "GR IIIx 上还没实测。" : "已在一台 GR III 上实测。";
}

// After-flash checks follow what this build actually changed.
function guideChecks(summary) {
  const out = [];
  const looks = [...new Set(summary.filter((x) => x.what === "图标" || x.what === "预设名").map((x) => x.preset))];
  if (looks.length) out.push(looks.join("、") + " 的图标和名字");
  const films = [...new Set(summary.filter((x) => x.what === "胶片").map((x) => x.preset))];
  if (films.length) out.push(films.join("、") + " 的胶片颜色，各拍一张看看");
  for (const x of summary.filter((x) => x.what === "颗粒项")) out.push(x.preset + "的颗粒档位");
  if (summary.some((x) => x.what === "关机图")) out.push("关一次机，看关机画面");
  if (summary.some((x) => x.what === "菜单顺序")) out.push("影像控制菜单的顺序（拍照、视频、RAW 显影都看），选中后风格和名字对得上");
  // 柔光: assign it to an ADJ slot first (it does not take one by itself), then look at the icon and the effect.
  if (summary.some((x) => x.preset === "柔光")) out.push("在「ADJ 模式设定」里把柔光放进一个槽，按 ADJ 看柔光图标清不清楚；关和强各拍一张，看亮处有没有晕开");
  out.push("卡再格式化一次，删掉更新文件");
  return out;
}

function guideOpenPref() {
  try { return localStorage.getItem("darkslide.guideCollapsed") !== "1"; } catch (e) { return true; }
}

function setGuideOpen(open) {
  $("guideBody").hidden = !open;
  $("guideShort").hidden = open;
  $("guideToggle").textContent = open ? "我刷过，收起" : "展开步骤";
  try { localStorage.setItem("darkslide.guideCollapsed", open ? "0" : "1"); } catch (e) { /* private mode */ }
}

// ---------------------------------------------------------------- shutdown image
const presetKeys = () => Object.keys(state.changes).filter((k) => k !== "shutdown");
const SD = { w: 720, h: 480 };
let sdView = "camera";
const sd = { img: null, px: null, scale: 1, base: 1, ox: 0, oy: 0, drag: null, result: null, timer: null, seq: 0 };

function setShutdown(value) {
  mutate((c) => { if (value === undefined) delete c.shutdown; else c.shutdown = value; });
}

const SOON = {
  date: ["日期印记", "", "计划中", "机内能不能在照片上印日期还没有研究，这里暂时没有可改的内容。"],
};

// Rail entries. 画幅 and 柔光 are settings of the whole camera, not of a preset (user, 2026-10-24):
// each has its own page showing how it is used in the camera. A switch can be turned on when the model's
// capability says so (柔光 since 2026-10-25); 画幅 is still in development and stays off.
const FEATURES = [["featIC", "ic"], ["featShutdown", "shutdown"], ["featRatio", "ratio"], ["featSoft", "soft"], ["featDate", "date"]];
function renderFeature() {
  const f = state.feature;
  for (const [id, key] of FEATURES) {
    $(id).classList.toggle("on", f === key);
    if (f === key) $(id).setAttribute("aria-current", "page"); else $(id).removeAttribute("aria-current");
  }
  $("plist").hidden = f !== "ic";
  $("paneIC").hidden = f !== "ic";
  $("adjustPanel").hidden = f !== "ic";
  $("paneShutdown").hidden = f !== "shutdown";
  $("paneRatio").hidden = f !== "ratio";
  $("paneSoft").hidden = f !== "soft";
  for (const [name, , id] of CAMERA_FEATURES) {
    const sw = $(id), ready = featureOn(name);
    sw.disabled = !ready;
    sw.setAttribute("aria-checked", state.features[name] ? "true" : "false");
    for (const badge of document.querySelectorAll('[data-dev="' + name + '"]')) badge.hidden = ready;
  }
  // 柔光 is camera-tested on the ordinary GR III only; GR IIIx and HDF bodies say so before generating.
  const softNote = !featureOn("soft_glow") ? "" :
    state.template && state.template.model === "GR IIIx" ? "柔光在 GR IIIx 上已适配、未实测：离线核对都通过了，还没有人在 GR IIIx 上刷过。" :
    /HDF/.test((state.project && state.project.target_body) || "") ? "HDF 机身和普通 GR III 用同一个 2.10 固件；柔光在 HDF 机身上已适配、未实测。" : "";
  $("softBodyNote").textContent = softNote;
  $("softBodyNote").hidden = !softNote;
  $("paneSoon").hidden = !SOON[f];
  if (SOON[f]) {
    const [title, sub, badge, text] = SOON[f];
    $("soonTitle").textContent = title;
    $("soonSub").textContent = sub;
    $("soonBadge").textContent = badge;
    $("soonText").textContent = text;
  }
}

// ---------------------------------------------------------------- preset card, samples, nine params
function renderPresetCard() {
  if (state.feature !== "ic") return;
  const s = slotOf(state.sel);
  const edit = state.changes[s.no] || {};
  const icon = $("pcardIcon");
  icon.className = "pcard-icon";
  if (edit.icon) { icon.classList.add("img"); icon.replaceChildren(iconImg(edit.icon)); }
  else if (factoryUrl(s.no)) { icon.classList.add("img"); icon.replaceChildren(el("img", { src: factoryUrl(s.no), alt: "" })); }
  else icon.textContent = s.abbr;
  const grainStage = ((state.status.detail || {}).grain || {})[String(s.no)] || "closed";
  const grain = grainStage === "factory" ? "颗粒 · 原厂已有" : "颗粒 " + (edit.grain === true ? "开" : "关");
  if ($("pcardChips")) $("pcardChips").replaceChildren(...[edit.film ? "胶片 · " + window.DarkslideFilm.nameOf(edit.film) : "原厂效果", grain, "详细设置 · 相机里调"].map((t) => el("span", { class: "chip-s", text: t })));
}

// The preview shows the user's own photo only (kept in this tab, never uploaded);
// without one the frame stays black and offers 「换照片」.
const samples = { mine: null, ar: 0 };  // ar: the photo's width / height once known

function frameShape(frame) {
  frame.style.setProperty("--ar", String(samples.ar || 1.5));
  return "";
}

function sceneBackground() {
  return samples.mine ? 'url("' + samples.mine + '")' : "#111";
}

const VIEW_NOTES = { split: "左边原图，右边调后", after: "调后的完整效果（导出 LUT 就是这个）", camera: "写进相机大概的样子：拟合过就按相机参数算，否则是估算" };

function renderSamples() {
  if (state.feature !== "ic") return;
  const hadFocus = focusedIndex($("sceneBtns"));
  $("sceneBtns").replaceChildren(el("button", { class: "add", text: samples.mine ? "＋ 换照片" : "＋ 选照片", onclick: () => $("sceneInput").click() }));
  refocus($("sceneBtns"), hadFocus);
  $("sampleBadge").textContent = samples.mine ? "我的照片 · 只在本机" : "还没有照片";
  $("sampleEmpty").hidden = !!samples.mine;
  const s = slotOf(state.sel);
  const edit = state.changes[s.no] || {};
  const bg = sceneBackground() + " center / contain no-repeat";
  const after = filmBackground(edit);
  $("sampleBefore").style.background = bg;
  $("sampleAfter").style.background = after || bg;
  for (const b of $("viewBtns").children) { const on = b.dataset.view === state.view; b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false"); }
  $("sampleFrame").className = "v1-frame " + state.view + frameShape($("sampleFrame"));
  const previewFailure = state.view === "camera" && window.DarkslideFilm.previewError(s.no, edit);
  if (previewFailure && $("zoomDialog").open) $("zoomDialog").close();
  $("viewNote").textContent = previewFailure ? "相机预览不可用：" + previewFailure + "。当前显示原图；可切换到调后查看原值效果。" : VIEW_NOTES[state.view];
  $("sampleBeforeTag").textContent = "原图";
  $("sampleAfterTag").textContent = previewFailure ? "原图 · 相机预览不可用" : state.view === "camera" ? "相机近似" : "调后" + (edit.film ? " · " + window.DarkslideFilm.nameOf(edit.film) : "");
  $("sampleGrain").classList.toggle("on", edit.grain === true);
  window.DarkslideFilm.renderOutput(s, edit);
}

// The chosen film's approximate preview, shown on the user's own photo only.
function filmBackground(edit) {
  const url = window.DarkslideFilm.previewUrl(edit, state.view === "camera", slotOf(state.sel).no);
  return url ? 'url("' + url + '") center / contain no-repeat' : null;
}

function openZoom() {
  const s = slotOf(state.sel);
  const edit = state.changes[s.no] || {};
  const bg = sceneBackground() + " center / contain no-repeat";
  frameShape($("zoomFrame"));
  $("zoomTitle").textContent = (edit.name !== undefined ? edit.name : s.name) + " · " + "我的照片";
  if (state.view === "camera" && window.DarkslideFilm.previewError(s.no, edit)) {
    toast("相机预览不可用，请切换到调后查看原值效果", true);
    return;
  }
  $("zoomAfter").style.background = filmBackground(edit) || bg;
  $("zoomBefore").style.background = bg;
  $("zoomGrain").classList.toggle("on", edit.grain === true);
  $("zoomDialog").showModal();
  setSplit(50);
}

function setSplit(v) {
  $("zoomSplit").value = String(v);
  $("zoomBefore").style.clipPath = "inset(0 " + (100 - v) + "% 0 0)";
  $("zoomDivider").style.left = "calc(" + v + "% - 1px)";
}

// Factory defaults of the camera's detail settings, per preset (display only; never written to firmware).
// Data: templates/<template>.detail.json → factory_params. A null value means "not verified yet".
const PARAM_NAMES = ["饱和度", "色相", "高/低调", "对比度", "高光对比", "阴影对比", "锐度", "暗角", "清晰度"];

function renderParams() {
  if (state.feature !== "ic" || !$("paramsGrid")) return;
  const s = slotOf(state.sel);
  const fp = ((state.status.detail || {}).factory_params || {});
  const entry = (fp.slots || {})[String(s.no)];
  const items = entry ? entry.items : PARAM_NAMES.map((name) => [name, null]);
  $("paramsGrid").style.setProperty("--rows", String(Math.ceil(items.length / 2)));
  $("paramsGrid").replaceChildren(...items.map(([name, value]) => {
    // number = a -4..+4 slider; text = an option (关 / B / 标准 …); null = not verified yet
    const num = typeof value === "number";
    const missing = value === null || value === undefined;
    return el("div", { class: "param" + (num ? "" : missing ? " unknown" : " option") }, [
      el("span", { class: "pn", text: name }),
      el("span", { class: "pt" }, num ? el("span", { class: "pk", style: "left:calc(" + ((value + 4) / 8 * 100) + "% - 5px)" }) : null),
      el("span", { class: "pv", text: missing ? "—" : num ? (value > 0 ? "+" : "") + value : value }),
    ]);
  }));
  const pending = items.some(([, v]) => v === null || v === undefined);
  $("paramsHint").textContent = (entry && entry.note ? entry.note + " " : "") +
    (pending ? "标「—」的原厂值还没核实。" : "") + "这里只显示，在相机菜单里自己调，不写进固件。";
}

function renderShutdown() {
  if (state.feature !== "shutdown") return;
  for (const b of document.querySelectorAll("#paneShutdown [data-body]")) b.classList.toggle("camera", sdView === "camera");
  for (const b of $("sdViewBtns").children) {
    const on = b.dataset.view === sdView;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  }
  const fac = $("sdFactory");
  // Each edition shows its own factory picture (firmware routes: 3 Street, 11 Urban, 12 Diary, 2 normal).
  // Which one an HDF body shows is not verified, so those keep the normal picture and say so.
  const variant = state.project ? state.project.camera_variant || "" : "";
  const route = { "GR III Street Edition": [3, "Street 版"], "GR III Diary Edition": [12, "Diary 版"], "GR IIIx Urban Edition": [11, "Urban 版"] }[variant] || [2, /HDF/.test(variant) ? "普通版；HDF 机身实际显示哪张未核实" : ""];
  $("sdFactoryCap").textContent = "原厂" + (route[1] ? "（" + route[1] + "）" : "");
  const facUrl = state.project ? "/api/factory/" + state.project.input_sha256 + "/shutdown/" + route[0] + ".jpg" : null;
  if (facUrl && fac.dataset.src !== facUrl) {
    fac.dataset.src = facUrl;
    fac.replaceChildren(el("img", { src: facUrl, alt: "原厂关机画面" }));
  }
  const edit = state.changes.shutdown;
  if (edit) {
    const cur = $("sdNew").querySelector("img");
    if (!cur || !cur.src.endsWith(edit.asset)) $("sdNew").replaceChildren(el("img", { src: assetUrl(edit.asset), alt: "新关机画面" }));
    $("sdNewCap").textContent = "新关机图";
  } else {
    $("sdNew").replaceChildren(el("span", { class: "dim small", text: "未修改" }));
    $("sdNewCap").textContent = "新关机图";
  }
  $("btnSdReset").disabled = !edit;
  const note = (t) => el("li", {}, [el("span", { class: "mk", text: "i" }), el("span", { text: t })]);
  const iiix = state.project && state.project.template_id && state.project.template_id.startsWith("gr3x-");
  $("sdNotes").replaceChildren(
    note(iiix ? "普通、Street、Urban、ING、Diary 五种关机样式共用这一张图，不扩容。"
      : "普通、Street、Urban、ING、Diary 五种关机样式共用这一张图，不扩容。新增 Street 路线尚未实机验证。"),
    note("不扩容：写进固件里原有的关机图位置，其他资源不动。"),
    note(iiix ? "GR IIIx 还没在相机上实测过；关机画面的实测都是在 GR III 上做的。"
      : "单独改关机画面，或者和图标、预设名、负片颗粒一起改，都已在一台 GR III 上实测成功。"),
    note("可以和图标、预设名一起生成，一次刷机全部生效。"),
  );
}

function sdClamp() {
  const c = $("sdCanvas");
  const w = sd.img.naturalWidth * sd.scale, h = sd.img.naturalHeight * sd.scale;
  sd.ox = Math.min(0, Math.max(c.width - w, sd.ox));
  sd.oy = Math.min(0, Math.max(c.height - h, sd.oy));
}

function sdRect() {
  const c = $("sdCanvas");
  return { sx: -sd.ox / sd.scale, sy: -sd.oy / sd.scale, sw: c.width / sd.scale, sh: c.height / sd.scale };
}

function sdRender() {
  const c = $("sdCanvas"), g = c.getContext("2d");
  g.fillStyle = "#000"; g.fillRect(0, 0, c.width, c.height);
  g.imageSmoothingQuality = "high";
  g.drawImage(sd.img, sd.ox, sd.oy, sd.img.naturalWidth * sd.scale, sd.img.naturalHeight * sd.scale);
  clearTimeout(sd.timer);
  $("sdStat").textContent = "正在转换…";
  $("sdApply").disabled = true;
  sd.timer = setTimeout(sdEncode, 250);
}

function sdPixels() {
  // 720×480 by DarkslidePixels, the same in every browser; transparent parts become
  // black, like the camera screen
  const data = window.DarkslidePixels.resample(sd.px, sdRect(), SD.w, SD.h);
  const out = document.createElement("canvas");
  out.width = SD.w; out.height = SD.h;
  out.getContext("2d").putImageData(new ImageData(data, SD.w, SD.h), 0, 0);
  return { canvas: out, data };
}

async function sdEncode() {
  const seq = ++sd.seq;
  const budget = (state.status.shutdown || {}).budget || 112505;
  const px = sdPixels();
  const fit = window.DarkslideJpeg.fit(px.data, SD.w, SD.h, budget, 95);
  if (seq !== sd.seq) return;
  if (!fit) { sd.result = null; $("sdStat").textContent = "这张图细节太多，放不进原厂关机图的空间，请换一张"; return; }
  const blob = new Blob([fit.data], { type: "image/jpeg" });
  let bmp;
  try { bmp = await createImageBitmap(blob); } catch (e) { sd.result = null; $("sdStat").textContent = "压缩结果无法解码，不能使用"; return; }
  if (seq !== sd.seq) return;
  if (bmp.width !== SD.w || bmp.height !== SD.h) { sd.result = null; $("sdStat").textContent = "压缩结果尺寸不对，不能使用"; return; }
  const pv = $("sdPreview"), g = pv.getContext("2d");
  g.imageSmoothingQuality = "high";
  g.drawImage(bmp, 0, 0, pv.width, pv.height);
  sd.result = { blob, quality: fit.quality, bytes: fit.data.length, source: px.canvas };
  const word = fit.quality >= 80 ? "画质很好" : fit.quality >= 50 ? "画质良好" : fit.quality >= 25 ? "画质一般，细节会变软" : "画质较差，建议换一张细节少的图";
  $("sdStat").textContent = word;
  $("sdApply").disabled = false;
}

async function startShutdownCrop(file) {
  // a picture chosen while the last one is still decoding wins; the last one is dropped
  const turn = sd.turn = (sd.turn || 0) + 1;
  try {
    const img = await loadImage(file);
    const px = await window.DarkslidePixels.decode(file, img);
    if (turn !== sd.turn) return;
    sd.img = img; sd.px = px;
  } catch (e) { if (turn === sd.turn) toast(e.message, true); return; }
  const c = $("sdCanvas");
  sd.base = Math.max(c.width / sd.img.naturalWidth, c.height / sd.img.naturalHeight);
  sd.scale = sd.base;
  $("sdZoom").value = "1";
  sd.ox = (c.width - sd.img.naturalWidth * sd.scale) / 2;
  sd.oy = (c.height - sd.img.naturalHeight * sd.scale) / 2;
  sd.result = null;
  $("sdDialog").showModal();
  sdRender();
}

function sdZoomTo(z, cx, cy) {
  const c = $("sdCanvas");
  cx = cx === undefined ? c.width / 2 : cx;
  cy = cy === undefined ? c.height / 2 : cy;
  const next = sd.base * Math.min(8, Math.max(1, z));
  sd.ox = cx - (cx - sd.ox) * (next / sd.scale);
  sd.oy = cy - (cy - sd.oy) * (next / sd.scale);
  sd.scale = next;
  $("sdZoom").value = String(next / sd.base);
  sdClamp();
  sdRender();
}

async function applyShutdown() {
  if (!sd.result) return;
  const btn = $("sdApply");
  btn.disabled = true;
  try {
    const jpeg = await api("POST", "/api/projects/" + state.project.id + "/assets?kind=shutdown", sd.result.blob);
    const source = await uploadPng(sd.result.source, "source");
    setShutdown({ asset: jpeg.asset, source: source.asset, quality: sd.result.quality, bytes: jpeg.bytes });
    $("sdDialog").close();
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
}

function wireShutdown() {
  for (const [id, key] of FEATURES) {
    $(id).addEventListener("click", () => { state.feature = key; renderAll(); });
  }
  for (const [name, , id] of CAMERA_FEATURES) $(id).addEventListener("click", () => setFeature(name, !state.features[name]));
  for (const b of $("sdViewBtns").children) b.addEventListener("click", () => { sdView = b.dataset.view; renderShutdown(); });
  $("sampleEmpty").addEventListener("click", (e) => { e.stopPropagation(); $("sceneInput").click(); });
  $("sampleBefore").addEventListener("click", openZoom);
  $("sampleAfter").addEventListener("click", openZoom);
  $("zoomClose").addEventListener("click", () => $("zoomDialog").close());
  $("zoomSplit").addEventListener("input", (e) => setSplit(Number(e.target.value)));
  $("sceneInput").addEventListener("change", (e) => {
    const f = e.target.files[0]; e.target.value = "";
    if (!f) return;
    if (samples.mine) URL.revokeObjectURL(samples.mine);
    samples.mine = URL.createObjectURL(f);   // stays in this browser tab; never uploaded
    samples.ar = 0;
    const probe = new Image();  // naturalWidth/Height follow EXIF orientation, like the backgrounds
    probe.onload = () => { if (probe.src === samples.mine) { samples.ar = probe.naturalWidth / probe.naturalHeight || 0; renderSamples(); } };
    probe.src = samples.mine;
    renderSamples();
  });
  $("btnSdUpload").addEventListener("click", () => $("sdInput").click());
  $("sdInput").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) startShutdownCrop(f); });
  $("btnSdReset").addEventListener("click", () => setShutdown(undefined));
  const c = $("sdCanvas");
  c.addEventListener("pointerdown", (e) => { sd.drag = { x: e.clientX, y: e.clientY, ox: sd.ox, oy: sd.oy }; c.setPointerCapture(e.pointerId); });
  c.addEventListener("pointermove", (e) => {
    if (!sd.drag) return;
    const k = c.width / c.getBoundingClientRect().width;
    sd.ox = sd.drag.ox + (e.clientX - sd.drag.x) * k;
    sd.oy = sd.drag.oy + (e.clientY - sd.drag.y) * k;
    sdClamp(); sdRender();
  });
  c.addEventListener("pointerup", () => { sd.drag = null; });
  c.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = c.getBoundingClientRect(), k = c.width / rect.width;
    sdZoomTo((sd.scale / sd.base) * (e.deltaY < 0 ? 1.08 : 1 / 1.08), (e.clientX - rect.left) * k, (e.clientY - rect.top) * k);
  }, { passive: false });
  $("sdZoom").addEventListener("input", (e) => sdZoomTo(parseFloat(e.target.value)));
  $("sdCancel").addEventListener("click", () => { sd.seq++; $("sdDialog").close(); });
  $("sdApply").addEventListener("click", applyShutdown);
}

// ---------------------------------------------------------------- wire up
function wire() {
  wireShutdown();
  window.DarkslideAdjust.wire({ get: () => (state.changes[state.sel] || {}).grade, set: (g) => setEdit(state.sel, "grade", g), rerender: renderAll,
    limits: () => window.DarkslideFilm.gradeLimitsFor((state.changes[state.sel] || {}).film) });
  window.DarkslideFilm.wire(api, toast, { mine: () => samples.mine, scene: () => null, rerender: renderAll, invalidateCheck: invalidateFilmPrepare, setFilm, slotNo: () => slotOf(state.sel).no, allEdits: () => state.changes });
  const drop = $("drop");
  $("fileInput").addEventListener("change", (e) => e.target.files[0] && importFile(e.target.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    if (!$("drop").classList.contains("off") && e.dataTransfer.files[0]) importFile(e.dataTransfer.files[0]);
  });
  $("camConfirmBox").addEventListener("change", (e) => {
    state.cam.ok[camModel()[0]] = e.target.checked; saveCam();
    if (state.firmware && state.firmware.kind === "official") {
      renderProjectPick();
      const go = $("projectPick").querySelector(".btn.primary");
      if (e.target.checked && go && !camBlocked()) window.DarkslideGuide.showArrow(go);
    }
  });
  $("chipChange").addEventListener("click", () => { show("viewStart"); });
  $("nameInput").addEventListener("input", onNameInput);
  $("btnNameReset").addEventListener("click", () => setEdit(state.sel, "name", undefined));
  $("btnGrain").addEventListener("click", () => {
    const on = (state.changes[state.sel] || {}).grain === true;
    setEdit(state.sel, "grain", on ? undefined : true);
  });
  $("btnUpload").addEventListener("click", () => $("imageInput").click());
  $("iconNew").addEventListener("click", () => $("imageInput").click());
  for (const b of $("viewBtns").children) b.addEventListener("click", () => { state.view = b.dataset.view; renderSamples(); });
  $("sampleFrame").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (samples.mine) openZoom(); else $("sceneInput").click(); } });
  $("btnExportCube").addEventListener("click", () => window.DarkslideFilm.exportCube(slotOf(state.sel), state.changes[state.sel] || {}));
  $("btnFit").addEventListener("click", () => window.DarkslideFilm.fitNow(slotOf(state.sel), state.changes[state.sel] || {}));
  $("btnPull").addEventListener("click", () => window.DarkslideFilm.pullNow(slotOf(state.sel), state.changes[state.sel] || {}));
  window.DarkslideRecipe.wire(toast);
  window.DarkslideOrder.wire({
    status: () => state.status,
    get: () => state.order,
    set: setOrder,
    supported: orderOn,
    redraw: renderSlots,
    // GR IIIx (2026-10-24: both series get sorting): the same column, waiting for its menu tables to be checked.
    unsupported: () => `${(state.template && state.template.model) || "这个型号"} 的菜单排序还在适配中，暂时不能拖动`,
    // HDF bodies run the same GR III 2.10 package, so the tables are the same; still untested there.
    note: () => {
      if (!orderWrites()) return "GR IIIx 可以先排好、存进工程；写进相机还在适配，生成前要先点「恢复原厂」。";
      // GR IIIx 1.60 writes the order too (same seven tables as GR III 2.10, checked 2026-10-25).
      if (state.template && state.template.model === "GR IIIx") return "GR IIIx 的菜单表和 GR III 位置、内容都一样；在 GR IIIx 上还没实测。";
      const body = (state.project && state.project.target_body) || "";
      return /HDF/.test(body) ? body + " 和普通 GR III 用同一个 2.10 固件，菜单表在同一位置；在 HDF 机身上同样未实测。" : "";
    },
    problem: () => state.orderProblem,
    fixed: () => state.template.slots.filter((s) => s.no > 12).map((s) => s.no),
    label: (no) => {
      const s = slotOf(no), edit = state.changes[no] || {};
      const renamed = edit.name !== undefined;
      const sub = [renamed ? `原 ${s.name}` : "", edit.film ? "胶片 · " + window.DarkslideFilm.nameOf(edit.film) : ""].filter(Boolean).join(" · ");
      return { name: renamed ? edit.name : s.name, sub, icon: slotIcon(s, "abbr") };
    },
  });
  $("btnRecipe").addEventListener("click", async () => {
    // card icon: the user's own icon, else the film's name plate, else the preset's factory icon
    const edit = state.changes[state.sel] || {}, img = $("pcardIcon").querySelector("img");
    const plate = edit.icon ? null : window.DarkslideFilm.plateOf(edit.film);
    const own = await iconHiRes(edit.icon);
    window.DarkslideRecipe.open(slotOf(state.sel), edit, own || plate || (img ? img.src : null));
  });
  $("imageInput").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) startCrop(f); });
  $("btnIconReset").addEventListener("click", () => setEdit(state.sel, "icon", undefined));
  $("btnFactoryStyle").addEventListener("click", () => {
    const s = slotOf(state.sel);
    const e = (state.changes[s.no] || {}).icon;
    $("factoryText").value = e && e.style === "factory" ? e.text : s.abbr;
    factoryRender();
    $("factoryDialog").showModal();
  });
  $("factoryText").addEventListener("input", factoryRender);
  $("factoryCancel").addEventListener("click", () => $("factoryDialog").close());
  $("factoryApply").addEventListener("click", applyFactory);

  const cc = $("cropCanvas");
  cc.addEventListener("pointerdown", (e) => { crop.drag = { x: e.clientX, y: e.clientY, ox: crop.ox, oy: crop.oy }; cc.setPointerCapture(e.pointerId); });
  cc.addEventListener("pointermove", (e) => {
    if (!crop.drag) return;
    const k = cc.width / cc.getBoundingClientRect().width;
    crop.ox = crop.drag.ox + (e.clientX - crop.drag.x) * k;
    crop.oy = crop.drag.oy + (e.clientY - crop.drag.y) * k;
    cropClamp(); cropRender();
  });
  cc.addEventListener("pointerup", () => { crop.drag = null; });
  cc.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = cc.getBoundingClientRect(), k = cc.width / rect.width;
    zoomTo((crop.scale / crop.base) * (e.deltaY < 0 ? 1.08 : 1 / 1.08), (e.clientX - rect.left) * k, (e.clientY - rect.top) * k);
  }, { passive: false });
  $("cropZoom").addEventListener("input", (e) => zoomTo(parseFloat(e.target.value)));
  $("cropCancel").addEventListener("click", () => $("cropDialog").close());
  $("cropApply").addEventListener("click", applyCrop);

  $("btnSave").addEventListener("click", saveProject);
  $("btnGenerate").addEventListener("click", generate);
  $("menuLangSelect").addEventListener("change", (e) => { if (e.target.value) setMenuLanguage(e.target.value); });
  $("btnFilmPrepare").addEventListener("click", prepareFilms);
  $("preflightCancel").addEventListener("click", () => $("preflightDialog").close());
  $("preflightOk").addEventListener("click", async () => {
    $("preflightDialog").close();
    try { state.status.prefs = await api("POST", "/api/prefs", { key: "preflight_seen", value: true }); } catch (e) { toast(e.message, true); return; }
    generate();
  });
  $("untestedBox").addEventListener("change", (e) => { $("untestedOk").disabled = !e.target.checked; });
  $("untestedCancel").addEventListener("click", () => $("untestedDialog").close());
  $("untestedOk").addEventListener("click", () => {
    if (!$("untestedBox").checked) return;
    $("untestedDialog").close();
    state.untestedAck = true;
    generate();
  });
  $("btnBack").addEventListener("click", () => show("viewWork"));
  $("guideToggle").addEventListener("click", () => setGuideOpen($("guideBody").hidden));
  $("projectTitle").addEventListener("change", async (e) => {
    try { state.project = await api("PATCH", "/api/projects/" + state.project.id, { title: e.target.value }); } catch (err) { toast(err.message, true); }
  });
  for (const id of ["rescueLink", "rescueLink2", "rescueLink3", "rescueLink4"]) {
    $(id).addEventListener("click", (e) => { e.preventDefault();
      renderRescue(); show("viewRescue"); window.scrollTo(0, 0); });
  }
  $("rescueBack").addEventListener("click", () => show(show.previous || "viewStart"));
  const rd = $("rescueDrop");
  $("rescueInput").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) checkRescue(f); });
  rd.addEventListener("dragover", (e) => { e.preventDefault(); rd.classList.add("over"); });
  rd.addEventListener("dragleave", () => rd.classList.remove("over"));
  rd.addEventListener("drop", (e) => { e.preventDefault(); rd.classList.remove("over"); if (e.dataTransfer.files[0]) checkRescue(e.dataTransfer.files[0]); });
  $("btnSettings").addEventListener("click", () => {
    $("appVersion").textContent = "v" + state.status.version;
    $("dataRoot").textContent = state.status.data_root;
    $("settingsDialog").showModal();
  });
  $("settingsClose").addEventListener("click", () => $("settingsDialog").close());
  $("btnOpenRescue").addEventListener("click", () => { $("settingsDialog").close(); show("viewRescue"); window.scrollTo(0, 0); });
  $("btnEditorGuide").addEventListener("click", () => startTour(startEditorGuide));
  $("btnGuideReset").addEventListener("click", async () => {
    for (const k of ["guide_off", "guide_start_seen", "guide_work_seen", "guide_v1_news_seen", "guide_v2_news_seen", "guide_v3_news_seen", "guide_tour_seen", "guide_editor_seen"]) await setPref(k, false);
    $("settingsDialog").close();
    toast("指引已重新打开");
    if (!$("viewWork").hidden && state.feature !== "ic") { state.feature = "ic"; renderAll(); }
    maybeGuide($("viewWork").hidden ? "start" : "work");
  });
  $("btnQuit").addEventListener("click", quitApp);
  $("btnBuilds").addEventListener("click", openBuilds);
  $("changeList").addEventListener("scroll", changeListFade, { passive: true });
  window.addEventListener("resize", changeListFade);
  $("buildsClose").addEventListener("click", () => $("buildsDialog").close());

  document.addEventListener("keydown", (e) => {
    if ($("viewWork").hidden) return;
    const mod = e.metaKey || e.ctrlKey;
    if (!mod) return;
    const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
    if (e.key.toLowerCase() === "s") { e.preventDefault(); saveProject(); }
    else if (e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  });
  window.addEventListener("beforeunload", () => { if (state.project) pushDraft(); });
}

async function quitApp() {
  const btn = $("btnQuit");
  btn.disabled = true;
  try {
    if (state.project) {
      clearTimeout(state.saveTimer);
      await api("PUT", "/api/projects/" + state.project.id + "/draft", draftBody());
    }
    await api("POST", "/api/quit");
  } catch (e) {
    btn.disabled = false;
    toast("退出失败：" + e.message, true);
    return;
  }
  state.project = null; // stop the beforeunload draft push
  $("settingsDialog").close();
  window.DarkslideGuide.hideArrow();
  $("cameraChip").hidden = true;
  $("btnSettings").hidden = true;
  show("viewQuit");
}

// The rescue page follows the open project's family (else the chosen series): IIIx has no camera test yet.
function rescueTarget() {
  const t = state.template && state.template.official_inputs && state.template.official_inputs[0];
  if (t) return { model: state.template.model, ver: state.template.firmware, file: t.filename };
  const ser = SERIES[(state.cam || loadCam()).s];
  return { model: ser.short, ver: ser.ver, file: ser.fw };
}
function renderRescue() {
  const r = rescueTarget();
  document.querySelectorAll(".rescue-model").forEach((n) => { n.textContent = r.model; });
  document.querySelectorAll(".rescue-ver").forEach((n) => { n.textContent = r.ver; });
  document.querySelectorAll(".rescue-file").forEach((n) => { n.textContent = r.file; });
  $("rescueTested").textContent = r.model === "GR III"
    ? "同一版本可以重复更新：已在一台 GR III 上实测，改过的 2.10 可以刷入 2.10 机身，也可以再刷回官方 2.10 原件。"
    : r.model === "GR IIIx"
    ? "GR IIIx 标准版已有用户实机刷入改过的 1.60 并正常使用；能不能再刷回官方 1.60 原件还没有在真机上验证过，城市版和 HDF 也还没有实测。"
    : r.model + " 属于“已适配 · 未实测”：改过的 " + r.ver + " 能不能刷进机身、能不能再刷回官方 " + r.ver + " 原件，都还没有在真机上验证过。";
}

async function checkRescue(file) {
  const out = $("rescueResult");
  out.className = "import-result small dim";
  out.textContent = "正在核对 " + file.name + " …";
  try {
    const buf = await file.arrayBuffer();
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", buf))).map((b) => b.toString(16).padStart(2, "0")).join("");
    const official = state.template.official_inputs[0];
    if (digest === official.sha256 && file.name.toLowerCase() === official.filename.toLowerCase()) {
      out.className = "import-result small ok";
      out.textContent = "✓ 是官方 " + state.template.model + " " + state.template.firmware + " 原件，可以做救机卡。SHA " + digest.slice(0, 8) + "…" + digest.slice(-8);
    } else if (digest === official.sha256) {
      out.className = "import-result small bad";
      out.textContent = "✕ 内容是官方原件，但文件名要改成 " + official.filename + "（大小写不限）：相机按这个名字在卡的根目录找更新文件";
    } else {
      out.className = "import-result small bad";
      out.textContent = "✕ 不是官方 " + state.template.model + " " + state.template.firmware + " 原件，不能当救机卡用";
    }
  } catch (e) {
    out.className = "import-result small bad";
    out.textContent = "✕ " + e.message;
  }
}

function openBuilds() {
  const mine = (state.status.builds || []).filter((b) => b.project_id === state.project.id).slice().reverse();
  $("buildList").replaceChildren(...(mine.length ? mine.map((b) => el("div", { class: "build-row" }, [
    el("div", { class: "txt" }, [el("span", { text: b.created_at.slice(0, 16).replace("T", " ") }), el("span", { class: "mono xsmall dim", text: "SHA " + b.output_sha256.slice(0, 8) + "…" + b.output_sha256.slice(-8) })]),
    el("a", { class: "btn", href: "/api/builds/" + b.build_id + "/firmware", download: b.output_filename || state.template.official_inputs[0].filename, text: state.project.capabilities && state.project.capabilities.candidate_generation ? "候选固件" : "固件" }),
    el("a", { class: "btn ghost", href: "/api/builds/" + b.build_id + "/manifest", text: "清单" }),
  ])) : [el("div", { class: "empty", text: "这个工程还没有生成过。" })]));
  $("buildsDialog").showModal();
}

async function init() {
  wire();
  renderModels();
  try {
    state.status = await api("GET", "/api/status");
    state.template = state.status.template;
    for (const series of SERIES.filter(s => !s.off)) {
      const capability = (state.status.families || []).find(f => f.model === series.short);
      if (capability) series.ready = capability.import_ready === true;
      if (series.short === "GR IIIx" && series.ready)
        series.note = "同一个固件文件通用于 GR IIIx、城市版、HDF。";
    }
    renderModels();
    maybeGuide("start");
  } catch (e) {
    $("importResult").textContent = "✕ 本地服务没有响应：" + e.message;
  }
}

init();
