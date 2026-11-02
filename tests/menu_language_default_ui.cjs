// The camera menu language a new project gets preselected (web/app.js guessMenuLanguage), run with the
// page's real code: the interface language (top right) comes first, the browser's languages only when the
// interface language is not known (user, 2026-10-29).
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const start = code.indexOf('const MENU_LANGS'), end = code.indexOf('function renderMenuLanguage');
assert(start > 0 && end > start, 'menu language helpers not found in web/app.js');

function guess(ui, languages) {
  const ctx = { navigator: { languages, language: languages[0] || '' }, window: {} };
  if (ui !== undefined) ctx.window.DarkslideI18n = { lang: () => ui };
  vm.createContext(ctx);
  vm.runInContext(code.slice(start, end) + '\nthis.guessMenuLanguage = guessMenuLanguage;', ctx);
  return ctx.guessMenuLanguage();
}

// the reported case: interface in 简体中文, browser preferring Japanese -> 简体中文 menu
assert.strictEqual(guess('zh-Hans', ['ja-JP', 'zh-Hans-JP']), 'zh-CN');
// every interface language maps to its camera menu language, whatever the browser says
for (const [ui, menu] of [['zh-Hans', 'zh-CN'], ['zh-Hant', 'zh-TW'], ['en', 'en'], ['ja', 'ja'], ['ko', 'ko']]) {
  assert.strictEqual(guess(ui, ['fr-FR', 'de-DE']), menu, ui);
}
// no interface language (i18n not loaded) or an unknown one: the browser's languages, as before
for (const ui of [undefined, 'xx', '']) {
  assert.strictEqual(guess(ui, ['zh-Hans-JP', 'ja-JP']), 'zh-CN');
  assert.strictEqual(guess(ui, ['ja-JP']), 'ja');
  assert.strictEqual(guess(ui, ['zh-HK']), 'zh-TW');
  assert.strictEqual(guess(ui, ['fr-FR']), 'fr');
  assert.strictEqual(guess(ui, ['xx-YY']), null);
}
console.log('PASS menu language default follows the interface language');
