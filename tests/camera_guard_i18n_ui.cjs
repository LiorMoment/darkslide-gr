// Validate the real translation bundle and runtime for guard messages only.
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const app = path.join(__dirname, '..');
const data = fs.readFileSync(path.join(app,'web','i18n-data.js'),'utf8');
const code = fs.readFileSync(path.join(app,'web','i18n.js'),'utf8');
function translator(lang) {
 const noop=()=>{}; const window={alert:noop,confirm:noop,prompt:noop,addEventListener:noop};
 const ctx={window,navigator:{languages:[lang]},localStorage:{getItem:()=>lang,setItem:noop},document:{readyState:'loading',addEventListener:noop},MutationObserver:function(){}};
 vm.createContext(ctx);vm.runInContext(data,ctx);vm.runInContext(code,ctx);return window.DarkslideI18n.t;
}
const cases={en:['wide ratios','Date stamp'],ja:['ワイド','日付'],ko:['와이드','날짜'], 'zh-Hant':['寬幅','日期戳記']};
for(const [lang,want] of Object.entries(cases)){
 const t=translator(lang),wide=t('16:9 和 65:24 宽幅的更新/恢复证据还没齐，这次不能生成；请先把开关关掉');
 const both=t('16:9 和 65:24 宽幅、日期印记的更新/恢复证据还没齐，这次不能生成；请先把开关关掉');
 assert(wide.includes(want[0]),lang+' wide label not translated: '+wide);
 assert(both.includes(want[0])&&both.includes(want[1]),lang+' labels not translated: '+both);
 assert(!both.includes('更新/恢复证据还没齐'),lang+' message not translated: '+both);
 console.log(lang+' PASS: '+both);
}
// The recorded flash-back is one test build on one camera; preserve that
// scope and the need to verify the user's configuration in every language.
const evidence = '一个测试包在一台 GR III 上刷回过；本次配置仍需验证。';
const evidenceCases = {
 en: ['One test build', 'this configuration still needs testing'],
 ja: ['テスト版1つ', '今回の構成'],
 ko: ['테스트 빌드 하나', '이번 구성'],
 'zh-Hant': ['一個測試包', '本次設定'],
};
for (const [lang, parts] of Object.entries(evidenceCases)) {
 const got=translator(lang)(evidence);
 for (const part of parts) assert(got.includes(part),lang+' evidence scope missing: '+got);
}
