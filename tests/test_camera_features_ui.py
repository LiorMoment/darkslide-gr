"""柔光 / 65:24 switches on the page (web/app.js in a Node VM, see test_menu_order_ui): a switch the model
cannot take stays off and nothing is sent; once allowed it turns on, the draft carries it, it counts as an
edit and undo/redo bring it back. Skipped when Node is not installed."""
import json
import subprocess
import unittest

from test_menu_order_ui import HARNESS, NODE, WEB

BASE = HARNESS[:HARNESS.index("const scenarios =")] + r'''
vm.runInContext(`renderAll = () => {}; scheduleDraft = () => calls.push(["draft", JSON.parse(JSON.stringify(draftBody()))]);`, ctx);
const scenarios = {
  locked: `state.project.capabilities = { menu_order: true, menu_order_write: true, soft_glow: false, wide_ratio: false };
    setFeature("soft_glow", true); calls.push(["state", state.features, hasEdits(), draftBody()]);`,
  open: `state.project.capabilities = { menu_order: true, menu_order_write: true, soft_glow: true, wide_ratio: true };
    setFeature("soft_glow", true); setFeature("wide_ratio", true);
    calls.push(["on", state.features, hasEdits(), draftBody()]);
    undo(); calls.push(["undo", state.features]);
    redo(); calls.push(["redo", state.features]);
    setFeature("soft_glow", false); calls.push(["off", state.features, draftBody()]);`,
  old_server: `state.project.capabilities = { menu_order: true, menu_order_write: true }; calls.push(["body", draftBody()]);`,
};
(async () => {
  await vm.runInContext(scenarios[scenario], ctx);
  process.stdout.write(JSON.stringify({ calls: ctx.calls }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
'''


@unittest.skipUnless(NODE, "node not installed")
class Switches(unittest.TestCase):
    def run_case(self, name):
        out = subprocess.run([NODE, "-e", BASE, str(WEB), name], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)["calls"]

    def test_locked_switch_stays_off(self):
        calls = self.run_case("locked")
        self.assertIn(["toast", "这项功能还在研发，暂时不能装进相机", True], calls)
        state = [c for c in calls if c[0] == "state"][0]
        self.assertEqual(state[1], {})
        self.assertFalse(state[2])
        self.assertEqual(state[3]["camera_features"], {})
        self.assertFalse([c for c in calls if c[0] == "draft"])

    def test_open_switches_save_and_undo(self):
        calls = self.run_case("open")
        on = [c for c in calls if c[0] == "on"][0]
        self.assertEqual(on[1], {"soft_glow": True, "wide_ratio": True})
        self.assertTrue(on[2])
        self.assertEqual(on[3]["camera_features"], {"soft_glow": True, "wide_ratio": True})
        self.assertEqual([c for c in calls if c[0] == "undo"][0][1], {"soft_glow": True})
        self.assertEqual([c for c in calls if c[0] == "redo"][0][1], {"soft_glow": True, "wide_ratio": True})
        off = [c for c in calls if c[0] == "off"][0]
        self.assertEqual(off[1], {"wide_ratio": True})
        self.assertEqual(off[2]["camera_features"], {"wide_ratio": True})

    def test_older_server_gets_no_new_field(self):
        body = [c for c in self.run_case("old_server") if c[0] == "body"][0][1]
        self.assertNotIn("camera_features", body)


if __name__ == "__main__":
    unittest.main()
