// Server messages the page shows behind a status mark ("✕ " + reason, web/app.js importFile) are translated
// whole, with the page's real web/i18n.js and bundle; before 2026-10-30 the mark stopped the whole-message
// match and the text came out pieced together from fragments ("不是支持的 OfficialFirmware").
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const web = path.join(__dirname, '..', 'web');
const data = fs.readFileSync(path.join(web, 'i18n-data.js'), 'utf8');
const code = fs.readFileSync(path.join(web, 'i18n.js'), 'utf8');

function translator(lang) {
  const noop = () => {};
  const window = { alert: noop, confirm: noop, prompt: noop, addEventListener: noop };
  const ctx = {
    window, navigator: { languages: [lang] }, localStorage: { getItem: () => lang, setItem: noop },
    document: { readyState: 'loading', addEventListener: noop }, MutationObserver: function () {},
  };
  vm.createContext(ctx);
  vm.runInContext(data, ctx);
  vm.runInContext(code, ctx);
  return window.DarkslideI18n.t;
}

const LIST = 'GR III 2.10 / 2.20 · GR IIIx 1.60 / 1.70';
const cases = {
  en: ['✕ Not a supported official firmware. Supported: ', '✕ Looks like Ricoh firmware, but not a supported official original. Supported: '],
  ja: ['✕ 対応している公式ファームウェアではありません。対応：', '✕ RICOH のファームウェアのようですが、対応している公式ファームウェア（原本）ではありません。対応：'],
  ko: ['✕ 지원하는 공식 펌웨어가 아닙니다. 지원: ', '✕ RICOH 펌웨어로 보이지만 지원하는 공식 원본 펌웨어가 아닙니다. 지원: '],
  'zh-Hant': ['✕ 不是支援的官方韌體。支援：', '✕ 看起來是理光韌體，但不是支援的官方原始檔。支援：'],
};
for (const [lang, [other, ricoh]] of Object.entries(cases)) {
  const t = translator(lang);
  assert.strictEqual(t('✕ 不是支持的官方固件。支持：' + LIST), other + LIST, lang);
  assert.strictEqual(t('✕ 看起来是理光固件，但不是支持的官方原件。支持：' + LIST), ricoh + LIST, lang);
  // the same message without a mark still translates as before
  assert.strictEqual(t('不是支持的官方固件。支持：' + LIST), other.slice(2) + LIST, lang);
}
// 简体中文 is the source: left alone, mark included
assert.strictEqual(translator('zh-Hans')('✕ 不是支持的官方固件。支持：' + LIST), '✕ 不是支持的官方固件。支持：' + LIST);
// a source string that itself starts with the mark keeps its own translation
const en = translator('en');
const own = Object.keys(JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'tools', 'i18n', 'en.json'), 'utf8')))
  .find((k) => /^✕ \S/.test(k) && !/%|\{\d/.test(k));
if (own) assert.notStrictEqual(en(own), own);
console.log('PASS status-marked server messages translate whole in every language');
