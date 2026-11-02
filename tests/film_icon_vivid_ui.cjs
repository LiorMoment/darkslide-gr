// Film icon syncs on the Vivid position (preset 2), next to the four older positions, with the page's real code
// (web/app.js, web/film.js) and requests finished in a chosen order: stand-in plate and its recovery, an older
// failure after a newer success, a load reported mid-sync, another project opened, 生成 waiting and holding, and
// the user's own icon.
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const web = path.join(__dirname, '..', 'web');
const code = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
const filmCode = fs.readFileSync(path.join(web, 'film.js'), 'utf8');
function slice(from, to) {
  const a = code.indexOf(from), b = code.indexOf(to, a);
  assert(a >= 0 && b > a, from + ' not found in web/app.js');
  return code.slice(a, b);
}
const SRC = slice('function trackIconSync(', '\n// Picking a built-in film also renames') + '\n' +
            slice('async function syncFilmIcon(', '\nfunction setEdit(') + '\n' +
            slice('async function generate()', '\nasync function generateNow()');

function gate() { let open; const p = new Promise((r) => { open = r; }); return { p, open }; }
const tick = () => new Promise((r) => setImmediate(r));
async function settle() { for (let i = 0; i < 50; i++) await tick(); }

function page(changes, net, extra = {}) {
  let calls = 0;
  const uploads = [];
  const ctx = {
    VIS: { x: 1, y: 2, w: 38, h: 36 },
    state: { project: { id: 'A' }, opened: 1, changes },
    slotOf: () => ({}), editable: () => true,
    window: { DarkslideFilm: { plateIcon: () => ({ source: 'plate-source', small: 'plate-small', crop: { sx: 8 } }), iconUrl: () => '/api/film-icons/portra160?v=1', iconMissed: () => false },
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
    uploadPng: async (c, kind, pid) => { uploads.push([kind, pid]); return { asset: kind + ':' + (typeof c === 'string' ? c : 'own') }; },
    toast: () => {},
    setEdit: (no, key, value) => { ctx.state.changes[no] = Object.assign({}, ctx.state.changes[no], { [key]: value }); },
    generateNow: async () => { ctx.builds.push(JSON.stringify(ctx.state.changes[2].icon)); if (extra.duringBuild) await extra.duringBuild(ctx); },
    builds: [],
  };
  vm.createContext(ctx);
  vm.runInContext(SRC + '\nthis.track = trackIconSync; this.ready = filmIconReady; this.filmsReady = filmsReady; this.generate = generate;', ctx);
  ctx.calls = () => calls;
  ctx.uploads = uploads;
  return ctx;
}
const icon = (ctx, no = 2) => { const i = ctx.state.changes[no].icon; return [i.asset, i.fallback]; };
const OLD = { asset: 'old', film: 'gold200' };
const STAND_IN = { asset: 'icon:plate-small', film: 'portra160', fallback: true, style: 'image' };

(async () => {
  // the picker offers Vivid as a film position, with its name, next to the older four
  assert(/const SLOTS = \[2, 8, 9, 10, 11\]/.test(filmCode), 'film.js SLOTS');
  assert(/SLOT_NAMES = \{ 2: "鲜艳"/.test(filmCode), 'film.js SLOT_NAMES');

  // a dropped request on Vivid: stand-in plate; the icon loads later: the user's icon comes back
  {
    let up = false;
    const ctx = page({ 2: { film: 'portra160', icon: OLD } }, () => ({ ok: up }));
    ctx.track(2, 'portra160'); await settle();
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
    up = true; ctx.ready('portra160', 'A'); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }
  // an older failure finishing after a newer success on Vivid: the user's icon stays
  {
    const slow = gate();
    const ctx = page({ 2: { film: 'portra160', icon: STAND_IN } }, (n) => (n === 1 ? { ok: false, wait: slow } : { ok: true }));
    ctx.track(2, 'portra160'); await tick();
    ctx.track(2, 'portra160'); await settle();
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }
  // the icon loads while Vivid's stand-in is on its way: one retry, the user's icon
  {
    const slow = gate();
    const ctx = page({ 2: { film: 'portra160', icon: OLD } }, (n) => (n === 1 ? { ok: false, wait: slow } : { ok: true }));
    ctx.track(2, 'portra160'); await tick();
    ctx.ready('portra160', 'A');
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
    assert.strictEqual(ctx.calls(), 2);
  }
  // a project opened with Vivid's film changed but its icon from the old film: put right, only that preset
  {
    const ctx = page({ 2: { film: 'portra160', icon: OLD }, 8: { film: 'gold200', icon: { asset: 'g', film: 'gold200' } }, 9: { film: 'portra400', icon: { asset: 'mine', style: 'image' } } }, () => ({ ok: true }));
    ctx.filmsReady('A'); await settle();
    assert.deepStrictEqual([icon(ctx, 2)[0], icon(ctx, 8)[0], icon(ctx, 9)[0]], ['user-icon', 'g', 'mine']);
    assert.strictEqual(ctx.calls(), 1);
  }
  // another project (say another firmware version) opened while Vivid's icon is on its way: nothing uploaded,
  // nothing written; film list callbacks of the old project do nothing either
  {
    const slow = gate();
    const ctx = page({ 2: { film: 'portra160', icon: OLD } }, () => ({ ok: true, wait: slow }));
    ctx.track(2, 'portra160'); await tick();
    ctx.state.project = { id: 'B' }; ctx.state.opened++;
    ctx.state.changes = { 2: { film: 'portra160', icon: STAND_IN } };
    slow.open(); await settle();
    assert.strictEqual(ctx.uploads.length, 0);
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
    ctx.ready('portra160', 'A'); ctx.filmsReady('A'); await settle();
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
  }
  // 生成 waits for Vivid's retry, and holds an automatic sync on Vivid until it is over
  {
    const first = gate(), second = gate();
    const ctx = page({ 2: { film: 'portra160', icon: OLD } }, (n) => (n === 1 ? { ok: false, wait: first } : { ok: true, wait: second }));
    ctx.track(2, 'portra160'); await tick();
    const gen = ctx.generate();
    ctx.ready('portra160', 'A');
    first.open(); await settle();
    assert.strictEqual(ctx.builds.length, 0, 'not while the retry runs');
    second.open(); await gen;
    assert.strictEqual(ctx.builds.length, 1);
    assert.strictEqual(JSON.parse(ctx.builds[0]).asset, 'user-icon');
  }
  {
    const ctx = page({ 2: { film: 'portra160', icon: STAND_IN } }, () => ({ ok: true }), {
      duringBuild: async (c) => { c.ready('portra160', 'A'); await settle(); c.during = c.calls(); } });
    await ctx.generate(); await settle();
    assert.strictEqual(ctx.during, 0);
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }
  // a sync held during A's build is dropped when B is open by the end: B's Vivid icon stays, also a no-film one
  {
    const ctx = page({ 2: { film: 'portra160', icon: STAND_IN } }, () => ({ ok: true }), {
      duringBuild: async (c) => {
        c.ready('portra160', 'A'); c.track(2, undefined);
        c.state.project = { id: 'B' }; c.state.opened++;
        c.state.changes = { 2: { film: 'portra160', icon: { asset: 'b-film', film: 'portra160' } } };
      } });
    await ctx.generate(); await settle();
    assert.deepStrictEqual(icon(ctx), ['b-film', undefined]);
    assert.strictEqual(ctx.calls(), 0);
  }
  // the user's own icon on Vivid: untouched by any of it, in any order
  for (const ok of [true, false]) {
    const slow = gate();
    const ctx = page({ 2: { film: 'portra160', icon: STAND_IN } }, () => ({ ok, wait: slow }), {
      duringBuild: async (c) => { c.ready('portra160', 'A'); c.filmsReady('A'); c.track(2, 'portra160'); } });
    ctx.track(2, 'portra160'); await tick();
    ctx.state.changes[2] = Object.assign({}, ctx.state.changes[2], { icon: { asset: 'mine', style: 'image' } });
    ctx.ready('portra160', 'A');
    slow.open(); await settle();
    await ctx.generate(); await settle();
    assert.deepStrictEqual(icon(ctx), ['mine', undefined]);
  }
  // taking the film off Vivid takes its film icon with it, never the user's own
  {
    const ctx = page({ 2: { icon: { asset: 'p', film: 'portra160' } } }, () => ({ ok: true }));
    ctx.track(2, undefined); await settle();
    assert.strictEqual(ctx.state.changes[2].icon, undefined);
    const own = page({ 2: { icon: { asset: 'mine', style: 'image' } } }, () => ({ ok: true }));
    own.track(2, undefined); await settle();
    assert.deepStrictEqual(icon(own), ['mine', undefined]);
  }
  console.log('PASS film icon syncs on the Vivid position');
})().catch((e) => { console.error(e); process.exit(1); });
