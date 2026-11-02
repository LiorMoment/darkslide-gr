// Execute the real picker and fit queue; fake only DOM, transport and image preview.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const els = {}, choices = [], jobs = [];
const element = () => {
  const classes = new Set();
  const e = {children: [], events: {}, style: {setProperty() {}}, dataset: {},
    classList: {add: (x) => classes.add(x), remove: (x) => classes.delete(x), contains: (x) => classes.has(x),
      toggle: (x, on) => on ? classes.add(x) : classes.delete(x)},
    addEventListener: (type, fn) => { e.events[type] = fn; }, setAttribute() {},
    append: (...items) => e.children.push(...items), replaceChildren: (...items) => { e.children = items; },
    querySelector() { return null; }, contains() { return false; }};
  return e;
};
const document = {getElementById: (id) => els[id] || (els[id] = element()), createElement: element, addEventListener() {}};
const window = {DarkslideAdjust: {isEmpty: (v) => !v || !Object.keys(v).length}, DarkslideGradeEstimate: null};
const context = vm.createContext({window, document, console, URL, setTimeout: () => 0, clearTimeout() {}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/film.js'), 'utf8'), context);
const film = window.DarkslideFilm;
const api = async (method, url) => {
  if (url === '/api/film-catalog') return {data_revision: 'test', writer: {experimental: false}, credit: 'test',
    films: [{id: 'portra160', name: 'Portra 160', film: 'Kodak Portra 160', print: 'test'}]};
  if (url.endsWith('/film-luts')) return {luts: [{id: 'user_lut_123456789abc', name: 'Mine', fitted: false}],
    capabilities: {film_output: true, camera_fit: true}};
  if (url.endsWith('/film-check')) return {films: {}};
  if (url.endsWith('/grade-limits')) return {valid: false};
  if (url === '/api/fitting-setup') return {state: 'ready'};
  throw Error('unexpected API: ' + url);
};
api.job = async (...args) => jobs.push(args);
film.wire(api, (message, bad) => { if (bad) throw Error(message); }, {
  rerender() {}, invalidateCheck() {}, setFilm: (no, id) => choices.push([no, id === undefined ? null : id]),
  scene: () => null, mine: () => null, slotNo: () => 2, edit: () => ({}), allEdits: () => ({})});
(async () => {
  await film.load('p_test');
  film.render({no: 2, name: '鲜艳'}, {});
  assert.strictEqual(els.filmField.classList.contains('locked'), false);
  const tiles = els.filmChips.children;
  assert.ok(!tiles[1].disabled);
  tiles[1].events.click();
  tiles[0].events.click();
  for (const no of [7, 12]) {
    film.render({no, name: no === 7 ? '正片' : '负片'}, {});
    assert.strictEqual(els.filmField.classList.contains('locked'), true);
    assert.ok(els.filmChips.children.every((tile) => tile.disabled));
    assert.ok(els.filmHint.textContent.includes('鲜艳'));
  }
  const fitted = await film.fitPending([{no: 2, name: '鲜艳'}, {no: 7, name: '正片'}, {no: 12, name: '负片'}], {
    2: {film: 'user_lut_123456789abc'}, 7: {grade: {temp: 4}}, 12: {grade: {temp: 4}}});
  console.log(JSON.stringify({status: 'PASS', slots: film.SLOTS, selection: choices, fitted, jobs}));
})().catch((e) => { console.error(e.stack || e); process.exit(1); });
