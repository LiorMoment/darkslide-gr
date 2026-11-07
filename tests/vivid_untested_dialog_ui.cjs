// The 未实测 dialog before 生成 (web/app.js generateNow), run with the page's real code: a film on Vivid asks on every
// firmware, also GR III 2.10, which otherwise asks nothing, and the wording fits the model and version.
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const a = code.indexOf('async function generateNow()'), b = code.indexOf('\nfunction finishGeneration(', a);
assert(a > 0 && b > a, 'generateNow not found');

function page({ model, firmware, file, candidate = false, newFirmware = false, variant, changes, features = {}, order = false, seen = true }) {
  const els = {};
  const el = (id) => els[id] || (els[id] = { id, dataset: {}, hidden: false, innerHTML: 'html ' + id, textContent: '', checked: false, disabled: false, opened: false, showModal() { this.opened = true; } });
  const state = {
    project: { id: 'P', menu_language: 'zh-CN', camera_variant: variant, target_body: variant, capabilities: { candidate_generation: candidate, full_firmware: !candidate, new_firmware: newFirmware } },
    template: { model, firmware, slots: [], official_inputs: [{ filename: file }] }, changes, features, status: { prefs: { preflight_seen: seen } }, order: null,
  };
  const ctx = {
    state, $: el, toast() {}, orderOn: () => order, orderWrites: () => true, hasEdits: () => true, Names: { ok: () => true }, nameMax: () => 8,
    window: { DarkslideOrder: { payload: () => (order ? [1] : null) } },
    saveProject: async () => { ctx.built = true; return false; },
  };
  vm.createContext(ctx);
  vm.runInContext(code.slice(a, b) + '\nthis.go = generateNow;', ctx);
  return { go: ctx.go, el, ctx };
}
const FILM = { film: 'portra160' };
(async () => {
  // GR III 2.10 without Vivid: no dialog, as before
  let p = page({ model: 'GR III', firmware: '2.10', file: 'fwdc239b.bin', variant: 'GR III', changes: { 8: FILM } });
  await p.go();
  assert.strictEqual(p.el('untestedDialog').opened, false);
  assert.strictEqual(p.ctx.built, true);
  // GR III 2.10 with Vivid: asks, in words for GR III 2.10
  p = page({ model: 'GR III', firmware: '2.10', file: 'fwdc239b.bin', variant: 'GR III', changes: { 2: FILM } });
  await p.go();
  assert.strictEqual(p.el('untestedDialog').opened, true);
  assert.strictEqual(p.ctx.built, undefined);
  assert.strictEqual(p.el('untestedVivid').hidden, false);
  assert.strictEqual(p.el('untestedVariant').hidden, true);
  assert.ok(!/IIIx/.test(p.el('untestedHead').textContent));
  assert.ok(/2\.10/.test(p.el('untestedRescue').textContent) && /fwdc239b/.test(p.el('untestedRescue').textContent));
  // GR IIIx 1.60 standard: tested by a user without Vivid (no dialog), asks with Vivid
  p = page({ model: 'GR IIIx', firmware: '1.60', file: 'fwdc243b.bin', candidate: true, variant: 'GR IIIx', changes: { 8: FILM } });
  await p.go();
  assert.strictEqual(p.el('untestedDialog').opened, false);
  p = page({ model: 'GR IIIx', firmware: '1.60', file: 'fwdc243b.bin', candidate: true, variant: 'GR IIIx', changes: { 2: FILM } });
  await p.go();
  assert.strictEqual(p.el('untestedDialog').opened, true);
  assert.strictEqual(p.el('untestedVivid').hidden, false);
  // GR IIIx 1.70 on the bodies a user has flashed (standard, Urban Edition): asks like 1.60, only for the Vivid film,
  // and then names the 1.70 rescue file; the HDF body still asks every time as new firmware
  for (const variant of ['GR IIIx', 'GR IIIx Urban Edition']) {
    p = page({ model: 'GR IIIx', firmware: '1.70', file: 'fwdc243b.bin', candidate: true, newFirmware: true, variant, changes: { 8: FILM } });
    await p.go();
    assert.strictEqual(p.el('untestedDialog').opened, false, variant);
    p = page({ model: 'GR IIIx', firmware: '1.70', file: 'fwdc243b.bin', candidate: true, newFirmware: true, variant, changes: { 2: FILM } });
    await p.go();
    assert.strictEqual(p.el('untestedDialog').opened, true, variant);
    assert.strictEqual(p.el('untestedVivid').hidden, false);
    assert.strictEqual(p.el('untestedVariant').hidden, true);
    assert.ok(p.el('untestedRescue').textContent.includes('1.70') && p.el('untestedRescue').textContent.includes('fwdc243b.bin'));
  }
  p = page({ model: 'GR IIIx', firmware: '1.70', file: 'fwdc243b.bin', candidate: true, newFirmware: true, variant: 'GR IIIx HDF', changes: { 8: FILM } });
  await p.go();
  assert.strictEqual(p.el('untestedDialog').opened, true);
  assert.ok(p.el('untestedHead').textContent.includes('1.70'));
  // GR III 2.20: no camera report yet, always asks; the Vivid line only with a film on Vivid
  for (const [model, firmware, file, candidate] of [['GR III', '2.20', 'fwdc239b.bin', false]]) {
    p = page({ model, firmware, file, candidate, newFirmware: true, variant: model, changes: { 8: FILM } });
    await p.go();
    assert.strictEqual(p.el('untestedDialog').opened, true);
    assert.strictEqual(p.el('untestedVivid').hidden, true);
    p = page({ model, firmware, file, candidate, newFirmware: true, variant: model, changes: { 2: FILM } });
    await p.go();
    assert.strictEqual(p.el('untestedVivid').hidden, false);
    assert.ok(p.el('untestedHead').textContent.includes(firmware));
    assert.ok(p.el('untestedRescue').textContent.includes(file));
  }
  // an icon or a name on Vivid without a film is not the fifth film position
  p = page({ model: 'GR III', firmware: '2.10', file: 'fwdc239b.bin', variant: 'GR III', changes: { 2: { name: 'Mine' } } });
  await p.go();
  assert.strictEqual(p.el('untestedDialog').opened, false);
  // the first-build reminder names the right version and what has been on a camera
  const VERSIONS = [['GR III', '2.10', 'fwdc239b.bin', false, false], ['GR III', '2.20', 'fwdc239b.bin', false, true],
    ['GR IIIx', '1.60', 'fwdc243b.bin', true, false], ['GR IIIx', '1.70', 'fwdc243b.bin', true, true]];
  for (const [model, firmware, file, candidate, newFirmware] of VERSIONS) {
    p = page({ model, firmware, file, candidate, newFirmware, variant: model, changes: { 2: FILM }, seen: false });
    await p.go();
    const said = p.el('preflightRecovery').textContent;
    assert.strictEqual(p.el('preflightDialog').opened, true, firmware);
    assert.ok(said.includes(firmware) && said.includes(file), firmware + ': ' + said);
    for (const other of ['2.10', '2.20', '1.60', '1.70']) if (other !== firmware) assert.ok(!said.includes(other), firmware + ' mentions ' + other);
    // GR IIIx 1.60 and 1.70 have been flashed by users; GR III 2.20 has not been on a camera yet
    assert.strictEqual(/已有用户实机刷入使用/.test(said), firmware === '1.60' || firmware === '1.70', firmware);
    assert.strictEqual(/没有在任何相机上实测/.test(said), firmware === '2.20', firmware);
  }
  // Vivid together with 柔光 or 菜单排序: the GR IIIx wording only on GR IIIx 1.60 / 1.70
  for (const extra of [{ order: true }, { features: { soft_glow: true }, order: true }]) {
    for (const [model, firmware, file, candidate, newFirmware] of VERSIONS) {
      p = page({ model, firmware, file, candidate, newFirmware, variant: model, changes: { 2: FILM }, ...extra });
      await p.go();
      assert.strictEqual(p.el('untestedDialog').opened, true, firmware);
      assert.strictEqual(p.el('untestedVivid').hidden, false, firmware);
      assert.strictEqual(p.el('untestedFeatures').hidden, firmware !== '1.60' && firmware !== '1.70', firmware);
      if (firmware === '2.10') assert.strictEqual(p.el('untestedVariant').hidden, true);
    }
  }
  // 菜单排序 without Vivid on GR III 2.10: no dialog, as before
  p = page({ model: 'GR III', firmware: '2.10', file: 'fwdc239b.bin', variant: 'GR III', changes: { 8: FILM }, order: true });
  await p.go();
  assert.strictEqual(p.el('untestedDialog').opened, false);
  // 柔光 alone asks on all four firmwares: this build has not been on a camera, and it names 柔光 itself
  for (const [model, firmware, file, candidate, newFirmware] of VERSIONS) {
    p = page({ model, firmware, file, candidate, newFirmware, variant: model, changes: {}, features: { soft_glow: true } });
    await p.go();
    assert.strictEqual(p.el('untestedDialog').opened, true, firmware);
    assert.strictEqual(p.el('untestedGlow').hidden, false, firmware);
    assert.strictEqual(p.ctx.built, undefined, firmware);
    assert.strictEqual(p.el('untestedFeatures').hidden, true, firmware);
  }
  // the same page going from another version to a GR IIIx 1.60 project gets its own wording back
  for (const [m, f, fl, c, n] of [VERSIONS[1], VERSIONS[0], VERSIONS[3]]) {
    p = page({ model: m, firmware: f, file: fl, candidate: c, newFirmware: n, variant: m, changes: { 2: FILM } });
    await p.go();
    p.ctx.state.project.capabilities = { candidate_generation: true, full_firmware: false, new_firmware: false };
    p.ctx.state.project.camera_variant = 'GR IIIx Urban';
    p.ctx.state.template = { model: 'GR IIIx', firmware: '1.60', slots: [], official_inputs: [{ filename: 'fwdc243b.bin' }] };
    p.ctx.state.untestedAck = false;
    await p.go();
    assert.strictEqual(p.el('untestedHead').innerHTML, 'html untestedHead', f);
    assert.strictEqual(p.el('untestedRescue').innerHTML, 'html untestedRescue', f);
  }
  // 画幅: this build has not been on a camera, so every build with it asks
  const WIDE_GLOW = { wide_ratio: true, soft_glow: true };
  for (const [variant, features] of [['GR III', WIDE_GLOW], ['GR III', { wide_ratio: true }], ['GR III Street Edition', WIDE_GLOW], ['GR III Diary Edition', { wide_ratio: true }]]) {
    p = page({ model: 'GR III', firmware: '2.10', file: 'fwdc239b.bin', variant, changes: { 8: FILM }, features });
    await p.go();
    assert.strictEqual(p.el('untestedDialog').opened, true, variant);
    assert.strictEqual(p.el('untestedWide').hidden, false, variant);
    assert.strictEqual(p.ctx.built, undefined, variant);
  }
  p = page({ model: 'GR IIIx', firmware: '1.60', file: 'fwdc243b.bin', candidate: true, variant: 'GR IIIx', changes: { 8: FILM }, features: { wide_ratio: true } });
  await p.go();
  assert.strictEqual(p.el('untestedDialog').opened, true);
  assert.strictEqual(p.el('untestedWide').hidden, false);
  console.log('PASS a film on Vivid asks on every firmware');
})().catch((e) => { console.error(e); process.exit(1); });
