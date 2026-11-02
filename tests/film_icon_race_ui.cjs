// Film icon syncs that overlap (web/app.js syncFilmIcon / filmIconReady / trackIconSync), run with the
// page's real code and requests finished in a chosen order: an older failed request must not put the
// stand-in plate back over the user's icon a newer one restored; a load reported while a stand-in is still
// on its way must not be missed; a sync never writes into another project or over the user's own icon.
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
function slice(from, to) {
  const a = code.indexOf(from), b = code.indexOf(to, a);
  assert(a >= 0 && b > a, from + ' not found in web/app.js');
  return code.slice(a, b);
}
const SRC = [slice('function trackIconSync(', '\n// Picking a built-in film also renames'),
             slice('async function syncFilmIcon(', '\nfunction setEdit(')].join('\n');

function gate() { let open; const p = new Promise((r) => { open = r; }); return { p, open }; }
const tick = () => new Promise((r) => setImmediate(r));
// let every sync that is not held at a gate run to its end (held ones stay pending)
async function settle() { for (let i = 0; i < 50; i++) await tick(); }

// net(n): what the n-th icon request does: { ok: true|false, wait: gate or undefined }
function page(changes, net, extra = {}) {
  let calls = 0;
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
    uploadIcon: async () => ({ asset: 'user-icon' }),
    uploadPng: async (c, kind) => ({ asset: kind + ':' + (typeof c === 'string' ? c : 'own') }),
    toast: () => {},
    setEdit: (no, key, value) => { ctx.state.changes[no] = Object.assign({}, ctx.state.changes[no], { [key]: value }); },
  };
  Object.assign(ctx, extra);
  vm.createContext(ctx);
  vm.runInContext(SRC + '\n' + slice('function filmIconReady(', '\n// Picking a built-in film also renames') +
    '\nthis.track = trackIconSync; this.ready = filmIconReady; this.filmsReady = filmsReady;', ctx);
  ctx.calls = () => calls;
  return ctx;
}
const icon = (ctx, no = 9) => { const i = ctx.state.changes[no].icon; return [i.asset, i.fallback]; };
const STAND_IN = { asset: 'icon:plate-small', film: 'gold200', fallback: true, style: 'image' };

(async () => {
  // 1. older failed request finishes after a newer one that worked: the user's icon stays
  {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: STAND_IN } }, (n) => (n === 1 ? { ok: false, wait: slow } : { ok: true }));
    ctx.track(9, 'gold200'); await tick();
    ctx.track(9, 'gold200'); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined], 'old failure must not overwrite');
  }
  // 1b. the other way round: an older request that worked finishes after a newer failed one: the user's
  // icon still replaces the stand-in plate
  {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, (n) => (n === 1 ? { ok: true, wait: slow } : { ok: false }));
    ctx.track(9, 'gold200'); await tick();
    ctx.state.changes[9].icon = STAND_IN;  // as if the preset had got its plate meanwhile
    ctx.track(9, 'gold200'); await settle();
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }
  // 2. the user's icon loads while the stand-in plate is still on its way: retried once, user's icon
  {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, (n) => (n === 1 ? { ok: false, wait: slow } : { ok: true }));
    ctx.track(9, 'gold200'); await tick();
    ctx.ready('gold200');                       // finds no stand-in yet
    assert.strictEqual(ctx.calls(), 1);
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
    assert.strictEqual(ctx.calls(), 2, 'exactly one retry');
  }
  // 2b. same, but the icon keeps failing: one retry, then it stops with the stand-in (no loop)
  {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, (n) => (n === 1 ? { ok: false, wait: slow } : { ok: false }));
    ctx.track(9, 'gold200'); await tick();
    ctx.ready('gold200');
    slow.open(); await settle(); await settle();
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
    assert.strictEqual(ctx.calls(), 2, 'one retry only');
  }
  // 2c. a load reported before the sync started is not a reason to retry
  {
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, () => ({ ok: false }));
    ctx.ready('gold200');
    ctx.track(9, 'gold200'); await settle();
    assert.strictEqual(ctx.calls(), 1);
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
  }
  // 2d. a stand-in plate and an icon that still will not decode (it loads, so a retry starts on every opening):
  // nothing stored, nothing uploaded, the project stays as it was
  {
    const ctx = page({ 9: { film: 'gold200', icon: STAND_IN } }, () => ({ ok: true }),
      { loadImage: async () => { throw new Error('bad'); } });
    let writes = 0; const setEdit = ctx.setEdit; ctx.setEdit = (...a) => { writes++; setEdit(...a); };
    let uploads = 0; const png = ctx.uploadPng; ctx.uploadPng = (...a) => { uploads++; return png(...a); };
    ctx.ready('gold200'); await settle();
    assert.strictEqual(ctx.calls(), 1, 'tried once');
    assert.deepStrictEqual([writes, uploads], [0, 0]);
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
  }
  // 3. another project opened while a sync is on its way: it writes nothing into either
  {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: STAND_IN } }, () => ({ ok: true, wait: slow }));
    ctx.track(9, 'gold200'); await tick();
    const projectB = { 9: { film: 'gold200', icon: STAND_IN } };
    ctx.state.project = { id: 'B' }; ctx.state.opened++; ctx.state.changes = projectB;
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true], 'project B untouched');
    // the same project opened again (a new project object) counts as another one too
    const slow2 = gate();
    const ctx2 = page({ 9: { film: 'gold200', icon: STAND_IN } }, () => ({ ok: true, wait: slow2 }));
    ctx2.track(9, 'gold200'); await tick();
    ctx2.state.project = { id: 'A' }; ctx2.state.opened++; ctx2.state.changes = { 9: { film: 'gold200', icon: STAND_IN } };
    slow2.open(); await settle();
    assert.deepStrictEqual(icon(ctx2), ['icon:plate-small', true]);
  }
  // 3c. a draft save replaces state.project with the server's copy of the same, still open project:
  // the sync still writes (every film pick saves a draft about half a second later)
  {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, () => ({ ok: true, wait: slow }));
    ctx.track(9, 'gold200'); await tick();
    ctx.state.project = Object.assign({}, ctx.state.project, { unsaved: true });
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }
  // 4. the user uploads their own icon while a film icon sync is on its way: theirs stays, in any order
  for (const ok of [true, false]) {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: STAND_IN } }, () => ({ ok, wait: slow }));
    ctx.track(9, 'gold200'); await tick();
    ctx.state.changes[9] = Object.assign({}, ctx.state.changes[9], { icon: { asset: 'mine', style: 'image' } });
    ctx.ready('gold200');
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['mine', undefined]);
  }
  // 5. a broken file, then fixed: stand-in plate, and the user's icon once it loads
  {
    let readable = false;
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, () => ({ ok: true }),
      { loadImage: async () => { if (!readable) throw new Error('bad'); return { naturalWidth: 400, naturalHeight: 300 }; } });
    ctx.track(9, 'gold200'); await settle();
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
    readable = true; ctx.ready('gold200'); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }
  // 6. a dropped request, then the network is back: picking the film again brings the user's icon
  {
    let up = false;
    const ctx = page({ 9: { film: 'gold200', icon: { asset: 'old', film: 'portra400' } } }, () => ({ ok: up }));
    ctx.track(9, 'gold200'); await settle();
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
    up = true; ctx.track(9, 'gold200'); await settle();
    assert.deepStrictEqual(icon(ctx), ['user-icon', undefined]);
  }
  // 7. the user picks another film while the sync is on its way: nothing written for the old one
  {
    const slow = gate();
    const ctx = page({ 9: { film: 'gold200', icon: STAND_IN } }, () => ({ ok: true, wait: slow }));
    ctx.track(9, 'gold200'); await tick();
    ctx.state.changes[9] = Object.assign({}, ctx.state.changes[9], { film: 'portra400' });
    slow.open(); await settle();
    assert.deepStrictEqual(icon(ctx), ['icon:plate-small', true]);
  }
  // 8. a project opened with a film whose icon was never put on (page closed, or another project opened, while
  // it was on its way): when the film list is ready that preset gets its own film's icon; matching icons and
  // the user's own uploaded icon are left alone
  {
    const ctx = page({
      7: { film: 'gold200', icon: { asset: 'ektar-icon', film: 'ektar100' } },
      8: { film: 'gold200', icon: { asset: 'gold-icon', film: 'gold200' } },
      9: { film: 'gold200', icon: { asset: 'mine', style: 'image' } },
    }, () => ({ ok: true }));
    ctx.filmsReady(); await settle();
    assert.deepStrictEqual([7, 8, 9].map((n) => icon(ctx, n)[0]), ['user-icon', 'gold-icon', 'mine']);
    assert.strictEqual(ctx.calls(), 1, 'only the mismatched preset');
    ctx.state.project = null; ctx.state.changes[7].icon = { asset: 'x', film: 'ektar100' };
    ctx.filmsReady(); await settle();
    assert.strictEqual(ctx.calls(), 1, 'no project open: nothing');
  }
  console.log('PASS overlapping film icon syncs: newest wins, user icon restored, own icons and other projects untouched');
})().catch((e) => { console.error(e); process.exit(1); });
