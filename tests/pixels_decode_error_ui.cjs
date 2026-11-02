// A picture the browser shows but cannot decode to pixels (web/pixels.js decode): the page says 「图片打不开」
// like every other unreadable picture, not the browser's English error (found in testing, 2026-10-30).
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'pixels.js'), 'utf8');
const ctx = {
  window: {},
  createImageBitmap: async () => { throw new Error('The source image could not be decoded.'); },
  document: { createElement: () => { throw new Error('not reached'); } },
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
const png = { arrayBuffer: async () => new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]).buffer };
(async () => {
  await assert.rejects(ctx.window.DarkslidePixels.decode(png, { naturalWidth: 10, naturalHeight: 10 }), (e) => e.message === '图片打不开');
  console.log('PASS undecodable picture reports 图片打不开');
})().catch((e) => { console.error(e); process.exit(1); });
