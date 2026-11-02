// Projects opened out of order, 生成 and film icon syncs (web/app.js), run with the page's real code and
// requests finished in a chosen order: an opening overtaken by a later one stops (no film data, callbacks
// or generation resume for it), 生成 waits for every icon sync including retries started meanwhile and
// holds new automatic ones until it is over, and a sync never uploads into a project opened after it began.
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
function slice(from, to) {
  const a = code.indexOf(from), b = code.indexOf(to, a);
  assert(a >= 0 && b > a, from + ' not found in web/app.js');
  return code.slice(a, b);
}
const LOAD = slice('async function loadProject(p)', '\nfunction renderProjectBody()');
const SYNCS = slice('function trackIconSync(', '\n// Picking a built-in film also renames') + '\n' +
              slice('async function syncFilmIcon(', '\nfunction setEdit(');
const GENERATE = slice('async function generate()', '\nasync function generateNow()');

function gate() { let open; const p = new Promise((r) => { open = r; }); return { p, open }; }
const tick = () => new Promise((r) => setImmediate(r));
async function settle() { for (let i = 0; i < 50; i++) await tick(); }

// ---- 1. loadProject: openings that finish out of order -------------------------------------------
function el() {
  return { dataset: {}, querySelector: () => el(), classList: { toggle() {} },
    set textContent(v) {}, set disabled(v) {}, set hidden(v) {}, set checked(v) {}, set title(v) {}, set value(v) {} };
}
function opener() {
  const factory = {}, log = [];
  const ctx = {
    state: { status: {}, template: {}, view: 'split' }, $: () => el(),
    invalidateFilmPrepare() {}, renderMenuLanguage() {}, renderProjectBody() {}, renderAll() {}, toast() {},
    show: () => log.push('show ' + ctx.state.project.id), maybeGuide() {},
    resumeGeneration: (id) => log.push('resume ' + id),
    api: (_, url) => new Promise((resolve) => { (factory[url] = factory[url] || []).push(resolve); }),
    window: { DarkslideOrder: { fromServer: () => null },
              DarkslideFilm: { load: (id) => log.push('film ' + id + ' while ' + ctx.state.project.id) } },
  };
  vm.createContext(ctx);
  vm.runInContext(LOAD + '\nthis.loadProject = loadProject;', ctx);
  const answer = (sha, value) => factory['/api/factory/' + sha].shift()(value);
  return { ctx, log, answer };
}
const project = (id) => ({ id, input_sha256: id, title: id, template: { model: 'x', firmware: 'v' }, draft: { changes: {} }, capabilities: {} });

// ---- 2. icon syncs, 生成 -----------------------------------------------------------------------
// net(n): the n-th icon request { ok, wait }; upload(n, kind, pid): may wait at a gate
function page(changes, net, extra = {}) {
  let calls = 0;
  const uploads = [];
  const ctx = {
    VIS: { x: 1, y: 2, w: 38, h: 36 },
    state: { project: { id: 'A' }, opened: 1, changes },
    slotOf: () => ({}), editable: () => true,
    window: { DarkslideFilm: { plateIcon: () => ({ source: 'plate-source', small: 'plate-small', crop: { sx: 8 } }), iconUrl: () => '/api/film-icons/gold200?v=1', iconMissed: () => false },
              DarkslidePixels: { decode: async () => 'px' } },
    document: { createElement: () => ({ width: 0, height: 0, getContext: () => ({ drawImage() {} }) }) },
    fetch: async () => {
      const step = net(++calls) || { ok: true };
      if (step.wait) await step.wait.p;
      if (!step.ok) throw new TypeError('Failed to fetch');
      return { ok: true, blob: async () => ({}) };
    },
    loadImage: async () => ({ naturalWidth: 400, naturalHeight: 300 }),
    drawIcon: () => 'own-pixels',
    uploadIcon: async (px, pid) => { uploads.push(['icon', pid]); return { asset: 'user-icon' }; },
    uploadPng: async (c, kind, pid) => { uploads.push([kind, pid]); if (extra.uploadGate && kind === 'source') await extra.uploadGate.p; return { asset: kind + ':' + (typeof c === 'string' ? c : 'own') }; },
    toast: () => {},
    setEdit: (no, key, value) => { ctx.state.changes[no] = Object.assign({}, ctx.state.changes[no], { [key]: value }); },
    generateNow: async () => { ctx.atBuild.push({ icon: Object.assign({}, ctx.state.changes[9].icon), pending: ctx.state.iconSyncs ? ctx.state.iconSyncs.size : 0 });
      if (extra.duringBuild) await extra.duringBuild(ctx); },
    atBuild: [],
  };
  vm.createContext(ctx);
  vm.runInContext(SYNCS + '\n' + GENERATE +
    '\nthis.track = trackIconSync; this.ready = filmIconReady; this.filmsReady = filmsReady; this.generate = generate;', ctx);
  ctx.calls = () => calls;
  ctx.uploads = uploads;
  return ctx;
}
const icon = (ctx, no = 9) => { const i = ctx.state.changes[no].icon; return [i.asset, i.fallback]; };

(async () => {
  // 1a. A opened, B opened, B's factory answers first, then A's: only B carries on
  {
    const { ctx, log, answer } = opener();
    const a = ctx.loadProject(project('A')), b = ctx.loadProject(project('B'));
    answer('B', { b: 1 }); await b;
    answer('A', { a: 1 }); await a;
    assert.deepStrictEqual(log, ['show B', 'film B while B', 'resume B']);
    assert.deepStrictEqual(ctx.state.factory, { b: 1 }, "A's factory data is not used for B");
    assert.strictEqual(ctx.state.project.id, 'B');
  }
  // 1b. A's factory answers first: A carries on until B is opened, then B (the normal order)
  {
    const { ctx, log, answer } = opener();
    const a = ctx.loadProject(project('A'));
    answer('A', { a: 1 }); await a;
    const b = ctx.loadProject(project('B'));
    answer('B', { b: 1 }); await b;
    assert.deepStrictEqual(log, ['show A', 'film A while A', 'resume A', 'show B', 'film B while B', 'resume B']);
  }
  // 1c. the same project opened twice, the first opening answering last: it stops, the second carries on
  {
    const { ctx, log, answer } = opener();
    const a1 = ctx.loadProject(project('A')), a2 = ctx.loadProject(project('A'));
    answer('A', { first: 1 });  // the first request is answered after the second one below
    await tick();
    // both requests were made; the first resolved, but the second opening is the newer one
    await a1;
    assert.deepStrictEqual(log, [], 'first opening stops');
    answer('A', { second: 1 }); await a2;
    assert.deepStrictEqual(log, ['show A', 'film A while A', 'resume A']);
    assert.deepStrictEqual(ctx.state.factory, { second: 1 });
  }

  // 2a. film list callbacks for another project do nothing
  {
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'ektar', film: 'ektar100' } } }, () => ({ ok: true }));
    ctx.state.project = { id: 'B' };
    ctx.filmsReady('A'); ctx.ready('gold200', 'A'); await settle();
    assert.strictEqual(ctx.calls(), 0);
    assert.strictEqual((ctx.state.iconReadyAt || {}).gold200, undefined);
    ctx.filmsReady('B'); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined], 'its own film list does');
  }

  // 3a. 生成 waits for the retry a waited-for sync started (review order): it starts on the user's icon
  {
    const first = gate(), second = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } },
      (n) => (n === 1 ? { ok: false, wait: first } : { ok: true, wait: second }));
    ctx.track(9, 'gold200'); await tick();
    const gen = ctx.generate();
    ctx.ready('gold200');           // the user's icon loads while the stand-in is on its way
    first.open(); await settle();   // stand-in stored, retry started and held at its request
    assert.strictEqual(ctx.atBuild.length, 0, 'not started while the retry runs');
    second.open(); await gen;
    assert.deepStrictEqual(ctx.atBuild, [{ icon: { asset: 'user-icon', source: 'source:own', style: 'image', crop: ctx.atBuild[0].icon.crop, film: 'gold200' }, pending: 0 }]);
  }
  // 3b. an automatic sync arriving during the build waits until it is over, then runs
  {
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'icon:plate-small', film: 'gold200', fallback: true } } }, () => ({ ok: true }), {
      duringBuild: async (c) => { c.ready('gold200'); c.filmsReady('A'); await settle(); c.duringCalls = c.calls(); } });
    await ctx.generate(); await settle();
    assert.strictEqual(ctx.duringCalls, 0, 'nothing ran during the build');
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined], 'it ran afterwards');
    assert.strictEqual(ctx.state.iconHold, false);
  }
  // 3c. a build that stops early (a dialog, a refusal) releases the hold too
  {
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'icon:plate-small', film: 'gold200', fallback: true } } }, () => ({ ok: true }),
      { duringBuild: async () => { throw new Error('stopped'); } });
    await ctx.generate().catch(() => {});
    assert.strictEqual(ctx.state.iconHold, false);
    ctx.ready('gold200'); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }

  // 4a. another project opened while the icon request runs: nothing uploaded at all
  {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, () => ({ ok: true, wait: slow }));
    ctx.track(9, 'gold200'); await tick();
    ctx.state.project = { id: 'B' }; ctx.state.opened++; ctx.state.changes = { 9: { film: 'gold200', icon: { asset: 'b', film: 'gold200' } } };
    slow.open(); await settle();
    assert.deepStrictEqual(ctx.uploads, []);
    assert.deepStrictEqual(icon(ctx), ['b', undefined]);
  }
  // 4b. opened between the two uploads: the second is not made, and the first went to A
  {
    const up = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, () => ({ ok: true }), { uploadGate: up });
    ctx.track(9, 'gold200'); await settle();
    ctx.state.project = { id: 'B' }; ctx.state.opened++;
    up.open(); await settle();
    assert.deepStrictEqual(ctx.uploads, [['source', 'A']]);
  }
  // 4c. a draft save (state.project replaced by the same project) does not stop the uploads
  {
    const up = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, () => ({ ok: true }), { uploadGate: up });
    ctx.track(9, 'gold200'); await settle();
    ctx.state.project = Object.assign({}, ctx.state.project, { unsaved: true });
    up.open(); await settle();
    assert.deepStrictEqual(ctx.uploads, [['source', 'A'], ['icon', 'A']]);
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }

  // 5. the user's own icon: never touched by syncs, retries, held syncs or film list callbacks
  {
    const first = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } }, 10: { film: 'gold200', icon: { asset: 'mine', style: 'image' } } },
      (n) => (n === 1 ? { ok: false, wait: first } : { ok: true }), {
        duringBuild: async (c) => { c.state.changes[9] = Object.assign({}, c.state.changes[9], { icon: { asset: 'mine9', style: 'image' } }); c.ready('gold200'); c.filmsReady('A'); } });
    ctx.track(9, 'gold200'); ctx.track(10, 'gold200'); await tick();
    ctx.ready('gold200');
    first.open(); await settle();
    await ctx.generate(); await settle();
    assert.deepStrictEqual([icon(ctx, 9)[0], icon(ctx, 10)[0]], ['mine9', 'mine']);
  }

  // 6. 生成 clicked while an icon sync runs, then the project is switched, closed or opened again before
  // the wait ends: no build at all; nothing switched: one build
  for (const [what, change, builds] of [
    ['another project', (c) => { c.state.project = { id: 'B' }; c.state.opened++; }, 0],
    ['closed', (c) => { c.state.project = null; }, 0],
    ['opened again', (c) => { c.state.project = { id: 'A' }; c.state.opened++; }, 0],
    ['nothing', () => {}, 1]]) {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, () => ({ ok: true, wait: slow }));
    ctx.track(9, 'gold200'); await tick();
    const gen = ctx.generate();
    change(ctx);
    slow.open(); await gen; await settle();
    assert.strictEqual(ctx.atBuild.length, builds, what);
    assert.strictEqual(ctx.state.iconHold, false, what);
    assert.strictEqual(ctx.state.generateBusy, false, what);
  }

  // 7a. syncs held during A's build (one with a film, one without), and B opened before the build ends:
  // dropped, B's film icon and B's own icon untouched
  {
    const ctx = page({ 9: { film: undefined, icon: { asset: 'a-old', film: 'ektar100' } }, 10: { film: 'gold200', icon: { asset: 'icon:plate-small', film: 'gold200', fallback: true } } },
      () => ({ ok: true }), { duringBuild: async (c) => {
        c.track(9, undefined); c.ready('gold200', 'A');
        assert.strictEqual(JSON.stringify(c.state.iconLater.map((x) => x.slice(0, 2))), JSON.stringify([[9, null], [10, 'gold200']]));
        c.state.project = { id: 'B' }; c.state.opened++;
        c.state.changes = { 9: { film: 'gold200', icon: { asset: 'b-film', film: 'gold200' } }, 10: { film: 'gold200', icon: { asset: 'b-mine', style: 'image' } } };
      } });
    await ctx.generate(); await settle();
    assert.deepStrictEqual([icon(ctx, 9)[0], icon(ctx, 10)[0]], ['b-film', 'b-mine']);
    assert.strictEqual(ctx.calls(), 0);
    assert.strictEqual(ctx.state.iconLater.length, 0);
  }
  // 7b. a sync for a film the preset no longer has does nothing, also the one without a film (it would
  // otherwise remove the icon of the film the preset has now)
  {
    const ctx = page({ 9: { film: 'portra400', icon: { asset: 'p', film: 'portra400' } } }, () => ({ ok: true }));
    ctx.track(9, 'gold200'); ctx.track(9, undefined); await settle();
    assert.deepStrictEqual(icon(ctx), ['p', undefined]);
    assert.strictEqual(ctx.calls(), 0);
    // the matching one still works: the film taken off the preset takes its film icon with it
    ctx.state.changes[9] = { icon: { asset: 'p', film: 'portra400' } };
    ctx.track(9, undefined); await settle();
    assert.strictEqual(ctx.state.changes[9].icon, undefined);
  }

  // 8. two 生成 at once: the second does nothing; the hold lasts until the first is over
  {
    const build = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'icon:plate-small', film: 'gold200', fallback: true } } }, () => ({ ok: true }),
      { duringBuild: async () => { await build.p; } });
    const first = ctx.generate(); await tick();
    const second = ctx.generate(); await second;
    assert.strictEqual(ctx.atBuild.length, 1, 'one build');
    assert.strictEqual(ctx.state.iconHold, true, 'still held');
    ctx.ready('gold200', 'A'); await settle();
    assert.strictEqual(ctx.calls(), 0, 'held while the first build runs');
    build.open(); await first; await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
    assert.strictEqual(ctx.state.generateBusy, false);
    await ctx.generate();
    assert.strictEqual(ctx.atBuild.length, 2, 'a later 生成 runs again');
  }
  console.log('PASS out-of-order openings, 生成 waits and holds icon syncs, no uploads into a later project');
})().catch((e) => { console.error(e); process.exit(1); });
