// The film icon put on a preset (web/app.js syncFilmIcon / filmIconReady), run with the page's real code:
// a user's icon that cannot be read just then (dropped request, broken file, failed load check) gives the
// film's name plate marked fallback, and the user's icon comes back once it loads; a preset with the user's
// own uploaded icon is never touched (review, 2026-10-30).
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
function slice(from, to) {
  const a = code.indexOf(from), b = code.indexOf(to, a);
  assert(a >= 0 && b > a, from + ' not found in web/app.js');
  return code.slice(a, b);
}
const SYNC = slice('async function syncFilmIcon(', '\nfunction setEdit(');
const READY = slice('function filmIconReady(', '\n// Picking a built-in film also renames');

// fetchOk: the request works; readable: the picture decodes; url: the catalog lists a user's icon;
// missed: the load check failed on this page load
function page({ changes, url = '/api/film-icons/gold200?v=1', missed = false, fetchOk = true, readable = true }) {
  const edits = [], notices = [];
  const canvas = () => ({ width: 0, height: 0, getContext: () => ({ drawImage() {} }) });
  const ctx = {
    VIS: { x: 1, y: 2, w: 38, h: 36 },
    state: { project: { id: 'P' }, opened: 1, changes }, slotOf: () => ({}), editable: () => true,
    window: { DarkslideFilm: { plateIcon: () => ({ source: 'plate-source', small: 'plate-small', crop: { sx: 8 } }), iconUrl: () => url, iconMissed: () => missed },
              DarkslidePixels: { decode: async () => 'px' } },
    document: { createElement: canvas },
    fetch: async () => { if (!fetchOk) throw new TypeError('Failed to fetch'); return { ok: true, blob: async () => ({}) }; },
    loadImage: async () => { if (!readable) throw new Error('bad'); return { naturalWidth: 400, naturalHeight: 300 }; },
    drawIcon: () => 'own-pixels',
    uploadIcon: async () => ({ asset: 'icon:own' }),
    uploadPng: async (c, kind) => ({ asset: kind + ':' + (typeof c === 'string' ? c : 'own') }),
    toast: (...v) => notices.push(v),
    setEdit: (no, key, value) => { edits.push([no, key, value]); changes[no] = Object.assign({}, changes[no], { [key]: value }); },
  };
  vm.createContext(ctx);
  vm.runInContext(SYNC + '\nthis.sync = syncFilmIcon;', ctx);
  return { sync: ctx.sync, edits, notices };
}

(async () => {
  // a dropped request while picking gold200: its plate, marked fallback, with a notice
  let p = page({ changes: { 9: { film: 'gold200', icon: { film: 'portra400', asset: 'old' } } }, fetchOk: false });
  await p.sync(9, 'gold200');
  assert.deepStrictEqual(p.edits.map((e) => [e[2].asset, e[2].film, e[2].fallback]), [['icon:plate-small', 'gold200', true]]);
  assert.strictEqual(p.notices.length, 1);

  // a broken file: the same
  p = page({ changes: { 9: { film: 'gold200' } }, readable: false });
  await p.sync(9, 'gold200');
  assert.strictEqual(p.edits[0][2].fallback, true);

  // the load check failed (no URL this page load): plate, marked fallback, no notice
  p = page({ changes: { 9: { film: 'gold200' } }, url: null, missed: true });
  await p.sync(9, 'gold200');
  assert.deepStrictEqual([p.edits[0][2].asset, p.edits[0][2].fallback, p.notices.length], ['icon:plate-small', true, 0]);

  // no icon file at all: the plate is the real icon, not a stand-in
  p = page({ changes: { 9: { film: 'gold200' } }, url: null, missed: false });
  await p.sync(9, 'gold200');
  assert.strictEqual(p.edits[0][2].fallback, undefined);

  // the same film again with a stand-in plate and the user's icon readable now: the user's icon comes back
  const standIn = { asset: 'icon:plate-small', film: 'gold200', fallback: true };
  p = page({ changes: { 9: { film: 'gold200', icon: standIn } } });
  await p.sync(9, 'gold200');
  assert.deepStrictEqual(p.edits.map((e) => [e[2].asset, e[2].film, e[2].fallback]), [['icon:own', 'gold200', undefined]]);

  // ... but not while it still cannot be read (no URL): nothing changes
  p = page({ changes: { 9: { film: 'gold200', icon: standIn } }, url: null, missed: true });
  await p.sync(9, 'gold200');
  assert.strictEqual(p.edits.length, 0);

  // the same film again with its real icon: nothing changes
  p = page({ changes: { 9: { film: 'gold200', icon: { asset: 'icon:own', film: 'gold200' } } } });
  await p.sync(9, 'gold200');
  assert.strictEqual(p.edits.length, 0);

  // the user's own uploaded icon (no film mark): never touched, whatever happens to the film icons
  for (const opts of [{}, { fetchOk: false }, { url: null, missed: true }]) {
    p = page(Object.assign({ changes: { 9: { film: 'gold200', icon: { asset: 'mine', style: 'image' } } } }, opts));
    await p.sync(9, 'gold200');
    assert.strictEqual(p.edits.length, 0);
  }

  // filmIconReady: only presets on that film whose icon is a stand-in plate for it are synced again
  const synced = [];
  const ctx = {
    state: { project: {}, changes: {
      7: { film: 'gold200', icon: { film: 'gold200', fallback: true } },
      8: { film: 'gold200', icon: { film: 'gold200' } },
      9: { film: 'gold200', icon: { asset: 'mine', style: 'image' } },
      10: { film: 'portra400', icon: { film: 'portra400', fallback: true } },
      11: { film: 'gold200', icon: { film: 'portra400', fallback: true } },
    } },
    trackIconSync: (no, id) => synced.push([no, id]),
  };
  vm.createContext(ctx);
  vm.runInContext(READY + '\nthis.ready = filmIconReady;', ctx);
  ctx.ready('gold200');
  assert.deepStrictEqual(synced, [[7, 'gold200']]);
  ctx.state.project = null; synced.length = 0; ctx.ready('gold200');
  assert.strictEqual(synced.length, 0, 'no project open: nothing');

  console.log('PASS film icon stand-in plates come back, own icons untouched');
})().catch((e) => { console.error(e); process.exit(1); });
