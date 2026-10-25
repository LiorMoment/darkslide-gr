"""下一版 guides (web/app.js in a Node VM, see test_menu_order_ui): the "这一版新增" card once, then the 3-step
tour of 菜单排序 / 画幅 / 柔光, then the 5-step editing guide only for someone who never had it. 以后再说 and 不再提示
end them; whoever saw the 1.0 card still gets the new one; the "?" replays tour then editing guide.
Also: the server accepts the new preference keys. Skipped when Node is not installed."""
import json
import subprocess
import unittest

from darkslide import server
from test_menu_order_ui import HARNESS, NODE, WEB

BASE = HARNESS[:HARNESS.index("const scenarios =")] + r'''
vm.runInContext(`
  requestAnimationFrame = (f) => f();
  globalThis.flows = []; globalThis.prefsSet = [];
  setPref = async (k, v) => { prefsSet.push([k, v]); };
  setPrefs = async (pairs) => { for (const p of pairs) prefsSet.push(p); };
  renderAll = () => {};
  globalThis.answers = {};
  window.DarkslideGuide.start = (name, done) => { flows.push(name); const a = answers[name] || [false, "next"]; Promise.resolve().then(() => done(a[0], a[1])); };
  window.DarkslideFilm.SLOTS = [8, 9, 10, 11];
`, ctx);
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };
const scenarios = {
  fresh: `state.status.prefs = {}; maybeWorkGuide();`,
  old_user: `state.status.prefs = { guide_v1_news_seen: true, guide_editor_seen: true }; maybeWorkGuide();`,
  later: `state.status.prefs = {}; answers.news = [false, "skip"]; maybeWorkGuide();`,
  never: `state.status.prefs = {}; answers.news = [true, "skip"]; maybeWorkGuide();`,
  tour_closed: `state.status.prefs = {}; answers.tour = [false, "close"]; maybeWorkGuide();`,
  off: `state.status.prefs = { guide_off: true }; maybeWorkGuide();`,
  seen_all: `state.status.prefs = { guide_v2_news_seen: true, guide_editor_seen: true }; maybeWorkGuide();`,
  replay: `state.status.prefs = { guide_v2_news_seen: true, guide_editor_seen: true }; startTour(startEditorGuide);`,
};
(async () => {
  vm.runInContext(scenarios[scenario], ctx);
  await settle();
  process.stdout.write(JSON.stringify({ flows: ctx.flows, prefs: ctx.prefsSet }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
'''


@unittest.skipUnless(NODE, "node not installed")
class GuideFlow(unittest.TestCase):
    def run_case(self, name):
        out = subprocess.run([NODE, "-e", BASE, str(WEB), name], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_new_user_gets_news_tour_then_editing_guide(self):
        got = self.run_case("fresh")
        self.assertEqual(got["flows"], ["news", "tour", "editor"])
        self.assertIn(["guide_v2_news_seen", True], got["prefs"])
        self.assertIn(["guide_v1_news_seen", True], got["prefs"])
        self.assertIn(["guide_tour_seen", True], got["prefs"])
        self.assertIn(["guide_editor_seen", True], got["prefs"])

    def test_old_user_gets_news_and_tour_only(self):
        self.assertEqual(self.run_case("old_user")["flows"], ["news", "tour"])

    def test_later_closes_everything_for_now(self):
        got = self.run_case("later")
        self.assertEqual(got["flows"], ["news"])
        self.assertIn(["guide_editor_seen", True], got["prefs"])
        self.assertNotIn(["guide_off", True], got["prefs"])

    def test_never_again_turns_guides_off(self):
        got = self.run_case("never")
        self.assertEqual(got["flows"], ["news"])
        self.assertIn(["guide_off", True], got["prefs"])

    def test_closing_the_tour_does_not_chain_on(self):
        self.assertEqual(self.run_case("tour_closed")["flows"], ["news", "tour"])

    def test_guides_off_or_all_seen_show_nothing(self):
        self.assertEqual(self.run_case("off")["flows"], [])
        self.assertEqual(self.run_case("seen_all")["flows"], [])

    def test_question_mark_replays_tour_then_editing(self):
        self.assertEqual(self.run_case("replay")["flows"], ["tour", "editor"])


class Keys(unittest.TestCase):
    def test_server_accepts_new_guide_keys(self):
        for k in ("guide_v2_news_seen", "guide_tour_seen"):
            self.assertIn(k, server.PREF_KEYS)


if __name__ == "__main__":
    unittest.main()
