// Runs the page's real apiJob/pollJob (web/app.js) against a scripted server: the job is handed in
// once, polled until it is done, and a refusal throws the Error the plain request would have thrown.
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const start = code.indexOf('const JOB_POLL_MS'), end = code.indexOf('api.job = apiJob;');
assert(start > 0 && end > start, 'job helpers not found in web/app.js');

function make(script) {
  const calls = [];
  const ctx = {
    setTimeout: (fn) => setImmediate(fn),  // do not wait the real 700 ms
    api: async (method, url, body) => {
      calls.push([method, url, body]);
      const next = script.shift();
      if (next instanceof Error) throw next;
      return next;
    },
  };
  vm.createContext(ctx);
  vm.runInContext(code.slice(start, end) + '\nthis.apiJob = apiJob; this.pollJob = pollJob; this.JOB_POLL_MS = JOB_POLL_MS;', ctx);
  return { ctx, calls };
}
const same = (a, b) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b));
const answerError = (message) => Object.assign(new Error(message), { data: { error: message } });

(async () => {
  // queued -> running -> done: one POST, then GETs, and the result of the job
  let { ctx, calls } = make([{ job: 'j1', state: 'queued' }, { job: 'j1', state: 'running' }, { job: 'j1', state: 'done', result: { build_id: 'b' } }]);
  same(await ctx.apiJob('P', 'generate'), { build_id: 'b' });
  same(calls.map((c) => c.slice(0, 2)), [['POST', '/api/projects/P/jobs'], ['GET', '/api/projects/P/jobs/j1'], ['GET', '/api/projects/P/jobs/j1']]);
  same(calls[0][2], { task: 'generate' });
  assert(ctx.JOB_POLL_MS >= 500 && ctx.JOB_POLL_MS <= 1000, 'polls every 0.5 to 1 second');

  // the body and the LUT go along as given
  ({ ctx, calls } = make([{ job: 'j', state: 'done', result: {} }]));
  await ctx.apiJob('P', 'film-fit', { lut: 'user_lut_0123456789ab' });
  same(calls[0][2], { task: 'film-fit', lut: 'user_lut_0123456789ab' });
  ({ ctx, calls } = make([{ job: 'j', state: 'done', result: {} }]));
  await ctx.apiJob('P', 'iiix-candidate', { body: { expected_changes: {}, recipe_extension: false } });
  same(calls[0][2], { task: 'iiix-candidate', body: { expected_changes: {}, recipe_extension: false } });

  // a refused job throws the plain request's Error: message and the whole body (checks)
  const body = { error: '还不能生成', checks: [{ ok: false, title: 'x' }] };
  ({ ctx } = make([{ job: 'j', state: 'running' }, { job: 'j', state: 'failed', status: 422, error: body.error, body }]));
  await assert.rejects(ctx.apiJob('P', 'generate'), (e) => e.message === '还不能生成' && JSON.stringify(e.data) === JSON.stringify(body));

  // a refusal when handing in (another generation is running) is the server's own error
  ({ ctx } = make([answerError('已有生成正在进行，请稍后再试')]));
  await assert.rejects(ctx.apiJob('P', 'generate'), (e) => e.message === '已有生成正在进行，请稍后再试');

  // a job the server no longer knows (it was restarted) is said so
  ({ ctx } = make([{ job: 'j', state: 'running' }, { job: 'j', state: 'none' }]));
  await assert.rejects(ctx.apiJob('P', 'generate'), (e) => /任务已丢失/.test(e.message));

  // a poll that gets no answer is tried again; the server answering with an error is not
  const lost = new TypeError('Load failed');
  ({ ctx } = make([{ job: 'j', state: 'running' }, lost, lost, { job: 'j', state: 'done', result: { ok: 1 } }]));
  same(await ctx.apiJob('P', 'generate'), { ok: 1 });
  ({ ctx } = make([{ job: 'j', state: 'running' }, lost, lost, lost, lost, lost, lost, { job: 'j', state: 'done', result: {} }]));
  await assert.rejects(ctx.apiJob('P', 'generate'), (e) => e === lost);
  ({ ctx } = make([{ job: 'j', state: 'running' }, answerError('出错了')]));
  await assert.rejects(ctx.apiJob('P', 'generate'), (e) => e.message === '出错了');

  // leaving the project stops the waiting (the job itself goes on, on the computer)
  let alive = true;
  ({ ctx, calls } = make([{ job: 'j', state: 'running' }, { job: 'j', state: 'running' }]));
  const waiting = ctx.apiJob('P', 'generate', { live: () => alive });
  alive = false;
  assert.strictEqual(await waiting, undefined);
  assert.strictEqual(calls.length, 1);
  console.log('PASS job polling: one hand-in, 0.7 s polls, plain errors with checks, lost jobs, retried polls, stop when left');
})().catch((e) => { console.error(e); process.exitCode = 1; });
