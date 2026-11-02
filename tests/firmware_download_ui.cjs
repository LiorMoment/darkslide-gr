// Start page: every official firmware a series accepts is offered, newest first, with the hint to
// pick the camera's own version; a single-version series keeps one link and no hint.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const code = fs.readFileSync('app/web/app.js', 'utf8');
const nodes = {};
const node = (id) => nodes[id] || (nodes[id] = { id, textContent: '', hidden: false, children: [], disabled: false, href: '',
  classList: { toggle() {}, add() {}, remove() {} }, replaceChildren(...v) { this.children = v; } });
const ctx = { state: { cam: { s: 0, v: 0, ok: {} }, firmware: null }, $: node, window: {},
  document: { createTextNode: (t) => ({ text: t }) },
  el: (tag, attrs) => Object.assign({ tag }, attrs), saveCam() {}, renderImportMatch() {}, renderProjectPick() {} };
vm.createContext(ctx);
const start = code.indexOf('const RICOH_FW =');
vm.runInContext(code.slice(start, code.indexOf('\n];', start) + 3), ctx);           // RICOH_FW, firmwareDownloads, SERIES
vm.runInContext('this.SERIES = SERIES; this.firmwareDownloads = firmwareDownloads;', ctx);
vm.runInContext(code.slice(code.indexOf('function camModel('), code.indexOf('\n}', code.indexOf('function camModel(')) + 2), ctx);
vm.runInContext(code.slice(code.indexOf('function renderVariants()'), code.indexOf('// Longest preset name')), ctx);

const versions = (i) => ctx.firmwareDownloads(ctx.SERIES[i]).map(([v]) => v);
assert.equal(JSON.stringify(versions(0)), JSON.stringify(['2.20', '2.10']));
assert.equal(JSON.stringify(versions(1)), JSON.stringify(['1.70', '1.60']));
assert.equal(JSON.stringify(ctx.firmwareDownloads({ ver: '1.11', zip: 'gr4_v111.zip' })), JSON.stringify([['1.11', 'gr4_v111.zip']]));

ctx.SERIES[0].ready = true;
ctx.renderVariants();
let links = node('fwLinks').children.filter((c) => c.tag === 'a');
assert.equal(JSON.stringify(links.map((a) => [a.text, a.href.split('/').pop(), a.class])),
  JSON.stringify([['下载 GR III 2.20', 'gr3_v220.zip', 'fw-dl'], ['2.10', 'gr3_v210.zip', 'fw-dl']]));
assert.equal(node('fwWhich').hidden, false);
assert.equal(node('fwHint').textContent, 'gr3_v220.zip / gr3_v210.zip 不用解压，fwdc239b.bin 也可以');

ctx.state.cam.s = 1; ctx.SERIES[1].ready = true;
ctx.renderVariants();
links = node('fwLinks').children.filter((c) => c.tag === 'a');
assert.equal(JSON.stringify(links.map((a) => a.text)), JSON.stringify(['下载 GR IIIx 1.70', '1.60']));
assert.equal(node('fwHint').textContent, 'gr3x_v170.zip / gr3x_v160.zip 不用解压，fwdc243b.bin 也可以');

// a single-version series (helper only: the GR IV card is not selectable yet) keeps one link
assert.equal(ctx.firmwareDownloads({ ver: '1.11', zip: 'gr4_v111.zip' }).length, 1);
// Importing only a 2.20 / 1.70 original (the server's real answer): accepted with its own version,
// a wrong series still says so.
vm.runInContext(code.slice(code.indexOf('function renderImportMatch()'), code.indexOf('async function importFile(')), ctx);
for (const [s, res] of [[0, { kind: 'official', model: 'GR III', firmware: '2.20', reason: '官方 GR III 2.20 原件',
                               sha256: '5c53760b3b564c87239dbf7f5c94d0f6fd91accd156ec4ba672ebfd6b861793e' }],
                        [1, { kind: 'official', model: 'GR IIIx', firmware: '1.70', reason: '官方 GR IIIx 1.70 原件',
                               sha256: 'aa8e3ae1349d94e83be381c20530e41d5e57b75c27ef24ba81c7b017d6c11592' }]]) {
  ctx.state.cam.s = s; ctx.state.firmware = res;
  assert.equal(ctx.renderImportMatch(), true);
  assert.match(node('importResult').className, /\bok\b/);
  assert.ok(node('importResult').textContent.startsWith('✓ ' + res.reason));
  assert.doesNotMatch(node('importResult').textContent, /还没有导入/);
  ctx.state.cam.s = 1 - s;
  assert.equal(ctx.renderImportMatch(), false);
  assert.match(node('importResult').textContent, /不一致/);
}
console.log('PASS firmware downloads: GR III 2.20 · 2.10 and GR IIIx 1.70 · 1.60 newest first, version hint only with several, single-version series unchanged; only-2.20 / only-1.70 imports accepted with their version');
