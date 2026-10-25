"""F31 front-end entry points: generate, save, quit and page close carry the menu order;
a model that cannot be sorted says so and sends nothing.

Runs the real web/app.js and web/order.js in a Node VM with a stub page and a recording
api(); nothing reaches a server. Skipped when Node is not installed (Darkslide itself
does not need it)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const web = process.argv[1], scenario = process.argv[2];
const els = {};
const element = (id) => {
  const o = { id, hidden: false, disabled: false, textContent: "", value: "", dataset: {}, style: {}, open: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, focus() {}, setAttribute() {}, removeAttribute() {},
    replaceChildren() {}, append() {}, querySelector() { return element("q"); }, querySelectorAll() { return []; },
    showModal() { o.modal = true; o.open = true; }, close() { o.closed = true; o.open = false; }, offsetWidth: 0 };
  o.parentElement = o;
  return o;
};
const document = { getElementById: (id) => (els[id] = els[id] || element(id)), activeElement: null,
  createElement: () => element("new"), querySelector: () => null, querySelectorAll: () => [], addEventListener() {} };
const window = { DarkslideNames: { ok: () => true }, DarkslideGuide: { hideArrow() {}, showArrow() {}, active: () => false },
  DarkslideFilm: { SLOTS: [8, 9, 10, 11], nameOf: (x) => x, load() {}, fitPending: async () => {},
    candidateReady: () => false, writerReady: () => false },
  addEventListener() {} };
const ctx = vm.createContext({ window, document, setTimeout: () => 0, clearTimeout() {}, console,
  requestAnimationFrame() {}, matchMedia: () => ({ matches: true }) });
vm.runInContext(fs.readFileSync(web + "/order.js", "utf8"), ctx);
vm.runInContext(fs.readFileSync(web + "/app.js", "utf8").replace(/\binit\(\);\s*$/, ""), ctx);
vm.runInContext(`
  globalThis.calls = [];
  globalThis.CAPS = { menu_order: true, menu_order_write: true, full_firmware: true };
  toast = (msg, bad) => calls.push(["toast", msg, !!bad]);
  api = async (method, path, body) => {
    calls.push(["api", method, path, body === undefined ? null : JSON.parse(JSON.stringify(body))]);
    if (path.endsWith("/save")) return { id: "p_synthetic", saved_at: "2026-10-18T00:00:00", versions: [{}], unsaved: false, capabilities: CAPS, menu_language: "en" };
    // 生成 goes through the job API (since 1.3.3): a finished job hands back what /generate used to answer.
    if (method === "POST" && path.endsWith("/jobs")) return { job: "j_x", state: "done", result: { build_id: "b_x", project_id: "p_synthetic", output_sha256: "0", created_at: "t" } };
    if (path.endsWith("/jobs")) return { jobs: [] };
    return { id: "p_synthetic", unsaved: true, capabilities: CAPS, menu_language: "en" };
  };
  showPending = () => calls.push(["pending"]);
  showResult = (ok) => calls.push(["result", ok]);
  show = (view) => calls.push(["show", view]);
  state.template = { slots: [] };
  state.status = { prefs: { preflight_seen: true }, builds: [],
    menu_order: { schema: 1, factory_still: [1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11], video_excluded: [4, 10] } };
  state.project = { id: "p_synthetic", unsaved: false, capabilities: CAPS, menu_language: "en" };
  state.changes = {};
  state.order = null;
  window.DarkslideOrder.wire({ status: () => state.status, get: () => state.order, set: (o) => { state.order = o; },
    supported: () => !!state.project.capabilities.menu_order, note: () => "", redraw() {},
    unsupported: () => "GR IIIx 的菜单排序还在适配中，暂时不能拖动",
    fixed: () => [], label: () => ({ name: "x", sub: "", icon: document.createElement("span") }) });
`, ctx);
const scenarios = {
  empty: "generate()",
  sort_generate: "state.order = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]; generate()",
  sort_preflight: "state.order = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]; state.status.prefs = {}; generate()",
  factory_list_generate: "state.order = [1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11]; generate()",
  quit_with_order: "state.order = [11, 10, 9, 8, 7, 12, 6, 5, 4, 3, 2, 1]; quitApp()",
  quit_with_factory_order: "state.order = null; quitApp()",
  page_close: "state.order = [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11]; pushDraft()",
  save_carries_order: "state.order = [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11]; saveProject().then((ok) => calls.push(['saved', ok]))",
  iiix_generate: `state.project.capabilities = { menu_order: true, menu_order_write: false, full_firmware: true };
    CAPS.menu_order_write = false; state.changes = { 2: { name: "V" } };
    state.order = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]; generate()`,
  unsupported: `state.project.capabilities = { menu_order: false, full_firmware: true }; CAPS.menu_order = false;
    state.order = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
    window.DarkslideOrder.render();
    calls.push(["entry", $("orderBar").hidden, $("orderUnsupported").hidden, window.DarkslideOrder.grip(1), $("orderUnsupported").textContent]);
    pushDraft().then(() => generate())`,
};
(async () => {
  await vm.runInContext(scenarios[scenario], ctx);
  await new Promise((r) => setImmediate(r));
  process.stdout.write(JSON.stringify({ calls: ctx.calls, preflight: !!(els.preflightDialog || {}).modal }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node not installed")
class FrontEndEntries(unittest.TestCase):
    def run_case(self, scenario):
        out = subprocess.run([NODE, "-e", HARNESS, str(WEB), scenario], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def apis(self, got):
        return [c[1:] for c in got["calls"] if c[0] == "api"]

    def test_empty_project_is_still_refused(self):
        got = self.run_case("empty")
        self.assertEqual(got["calls"], [["toast", "还没有改动", True]])

    def test_sort_only_saves_then_generates(self):
        calls = self.apis(self.run_case("sort_generate"))
        self.assertEqual([c[:2] for c in calls], [["PUT", "/api/projects/p_synthetic/draft"],
                                                  ["POST", "/api/projects/p_synthetic/save"],
                                                  ["POST", "/api/projects/p_synthetic/jobs"]])
        self.assertEqual(calls[0][2], {"changes": {}, "menu_order": {
            "schema": 1, "still_slots": [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
            "video_slots": [12, 1, 2, 3, 5, 6, 7, 8, 9, 11]}})

    def test_sort_only_reaches_the_preflight(self):
        got = self.run_case("sort_preflight")
        self.assertTrue(got["preflight"])
        self.assertEqual(got["calls"], [])

    def test_factory_order_list_is_not_an_edit(self):
        got = self.run_case("factory_list_generate")
        self.assertEqual(got["calls"], [["toast", "还没有改动", True]])

    # These set state.order directly (what the dialog would have left there); the dialog's own paths
    # (drag, Alt+↑/↓, 恢复原厂顺序, quitting right after a real drag) are in DialogInteractions below.
    def test_quit_sends_the_order_held_by_the_page(self):
        calls = self.apis(self.run_case("quit_with_order"))
        self.assertEqual([c[:2] for c in calls], [["PUT", "/api/projects/p_synthetic/draft"], ["POST", "/api/quit"]])
        self.assertEqual(calls[0][2]["menu_order"]["still_slots"], [11, 10, 9, 8, 7, 12, 6, 5, 4, 3, 2, 1])
        self.assertEqual(calls[0][2]["menu_order"]["video_slots"], [11, 9, 8, 7, 12, 6, 5, 3, 2, 1])

    def test_quit_with_the_factory_order_clears_it(self):
        calls = self.apis(self.run_case("quit_with_factory_order"))
        self.assertEqual(calls[0][:2], ["PUT", "/api/projects/p_synthetic/draft"])
        # explicit null (not a missing key, which the server reads as "keep")
        self.assertIn("menu_order", calls[0][2])
        self.assertIsNone(calls[0][2]["menu_order"])

    def test_page_close_push_carries_the_order(self):
        calls = self.apis(self.run_case("page_close"))
        self.assertEqual(calls[0][2]["menu_order"]["still_slots"], [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11])

    def test_save_carries_the_order(self):
        got = self.run_case("save_carries_order")
        calls = self.apis(got)
        self.assertEqual([c[:2] for c in calls], [["PUT", "/api/projects/p_synthetic/draft"],
                                                  ["POST", "/api/projects/p_synthetic/save"]])
        self.assertEqual(calls[0][2]["menu_order"]["still_slots"], [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11])
        self.assertIn(["saved", True], got["calls"])

    def test_iiix_with_an_order_is_told_before_generating(self):
        # GR IIIx sorts and saves, but the order cannot be written yet: the page says so and sends nothing.
        got = self.run_case("iiix_generate")
        self.assertEqual(self.apis(got), [])
        self.assertIn(["toast", "GR IIIx 的菜单顺序还在适配，暂时不能写进相机。要生成的话，请先在左栏点「恢复原厂」。", True], got["calls"])

    def test_a_model_that_cannot_sort_says_so_and_sends_nothing(self):
        got = self.run_case("unsupported")
        entry = [c for c in got["calls"] if c[0] == "entry"][0]
        self.assertEqual(entry, ["entry", True, False, None, "GR IIIx 的菜单排序还在适配中，暂时不能拖动"])
        calls = self.apis(got)
        # the draft write leaves the stored order alone (no menu_order key) and the order is not an edit
        self.assertEqual(calls, [["PUT", "/api/projects/p_synthetic/draft", {"changes": {}}]])
        self.assertIn(["toast", "还没有改动", True], got["calls"])


@unittest.skipUnless(NODE, "node not installed")
class ColumnInteractions(unittest.TestCase):
    """Sorting in the preset column, through order.js's real event handlers (tests/menu_order_column_ui.cjs):
    Alt+↑/↓ direction, drag and commit, drag back to the factory order (null, no 「已调整」), 恢复原厂顺序,
    undo/redo, a redraw in the middle of a drag (used to leave 13 rows), the integrity check before a
    commit, other edits still saving next to a bad order, and a stored order that cannot be read."""

    def test_column_paths(self):
        script = Path(__file__).with_name("menu_order_column_ui.cjs")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("PASS menu order column", out.stdout)


class Page(unittest.TestCase):
    """The page itself: the sorting line in the preset column, wording, script order, no browser dialogs."""

    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.order = (WEB / "order.js").read_text(encoding="utf-8")

    def test_column_entry_and_wording(self):
        # 2026-10-24: sorted right in the preset column, no dialog any more.
        plist = self.html[self.html.index('id="plist"'):self.html.index("</nav>", self.html.index('id="plist"'))]
        for needle in ('id="orderBar"', 'id="orderState"', 'id="orderReset"', 'id="orderUnsupported"', 'id="slots"',
                       'id="orderHint"', 'id="orderProblem"', 'id="orderBodyNote"', 'id="orderLive"', "未实测"):
            self.assertIn(needle, plist)
        for gone in ('id="btnOrder"', 'id="orderDialog"', 'id="orderList"', 'id="orderClose"'):
            self.assertNotIn(gone, self.html)

    def test_script_order(self):
        scripts = re.findall(r'<script src="([^"]+)"></script>', self.html)
        self.assertLess(scripts.index("order.js"), scripts.index("app.js"))

    def test_no_browser_confirm_or_prompt(self):
        self.assertIsNone(re.search(r"\b(confirm|prompt|alert)\(", self.order))


if __name__ == "__main__":
    unittest.main()
