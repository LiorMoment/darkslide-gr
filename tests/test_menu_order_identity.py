"""All positions retain fixed preset identity across UI and generation requests."""
import json
import subprocess
import unittest

from test_menu_order_sidebar import BASE, NODE, WEB
from test_menu_order import FACTORY, order, synthetic_payload
from darkslide import generate, menu_order, menu_order_verify, template

# Rotate a reversed list so each fixed identity visits all twelve menu positions.
ORDERS = [FACTORY[::-1][i:] + FACTORY[::-1][:i] for i in range(12)]


class RequestIdentity(unittest.TestCase):
    def test_names_and_films_keep_fixed_ids_at_every_position(self):
        t = template.load()
        changes = {str(n): {'name': 'P' + str(n)} for n in range(2, 13)}
        for n in (8, 9, 10, 11):
            changes[str(n)]['film'] = 'identity-film-' + str(n)
        before = json.dumps(t, sort_keys=True)
        # Only request construction (the 1.0 film route includes the films): no writer, no firmware.
        reference = generate.edits_from_changes(None, 'synthetic', changes, t, include_films=True)
        for seq in ORDERS:
            with self.subTest(order=seq):
                edits = generate.edits_from_changes(None, 'synthetic', changes, t, include_films=True, order=order(seq))
                self.assertEqual([e for e in edits if e['kind'] != 'menu_order'], reference)
                self.assertEqual([e for e in edits if e['kind'] == 'menu_order'], [menu_order.edit_for(order(seq))])
                for e in edits:
                    if e['kind'] == 'name':
                        slot = template.slot(t, e['slot'])
                        self.assertEqual(e['string_id'], slot['string_id'])
                        self.assertEqual(e['text'], 'P' + str(e['slot']))
                    if e['kind'] == 'film':
                        self.assertEqual(e['film'], 'identity-film-' + str(e['slot']))
        self.assertEqual(json.dumps(t, sort_keys=True), before)

    def test_all_positions_decode_to_the_same_identity_in_all_seven_tables(self):
        t, base = template.load(), synthetic_payload()
        # Fixed RAW -> editor identity contract, intentionally not writer-derived.
        raw_identity = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 12, 8: 7, 9: 8, 10: 9, 11: 10, 12: 11}
        for seq in ORDERS:
            request = order(seq)
            changed, _ = menu_order.write_tables(base, request, t)
            menu_order_verify._compare_sorting_only(base, changed, request, False)
            for at in menu_order_verify.STILL_OFFSETS:
                self.assertEqual([x - 10 for x in changed[at:at + 12]], seq)
            for at in menu_order_verify.VIDEO_OFFSETS:
                self.assertEqual([x - 10 for x in changed[at:at + 10]], [s for s in seq if s not in (4, 10)])
            at = menu_order_verify.RAW_OFFSET
            self.assertEqual([raw_identity[x] for x in changed[at:at + 12]], seq)


@unittest.skipUnless(NODE, 'node not installed')
class SidebarIdentity(unittest.TestCase):
    def test_click_and_edit_every_preset_at_every_position(self):
        scenario = r'''
  identities: `(() => {
    const original = JSON.stringify(state.template);
    const base = [11,10,9,8,7,12,6,5,4,3,2,1];
    const results = [];
    const nameOf = (b) => b.children.find((c) => c.className === 'slot-name');
    for (let rotation=0; rotation<12; rotation++) {
      const seq=base.slice(rotation).concat(base.slice(0,rotation));
      setOrder(seq);
      for (let pos=0; pos<14; pos++) {
        const expected=pos<12?seq[pos]:pos-12+13;
        $('slots').children[pos].events.click();
        if (state.sel!==expected) throw Error('Selection identity changed');
        const before=JSON.parse(JSON.stringify(state.changes));
        setEdit(state.sel,'name','Changed '+expected);
        for (const key of Object.keys(before)) {
          if (Number(key)!==expected && JSON.stringify(before[key])!==JSON.stringify(state.changes[key]))
            throw Error('Another preset was edited');
        }
        if (state.changes[expected].name!=='Changed '+expected) throw Error('Edit went to wrong preset');
        const visible=nameOf($('slots').children[pos]).textContent;
        if (visible!=='Changed '+expected) throw Error('Visible name disagrees with edited preset');
        if ($('slots').children[pos].getAttribute('data-slot')!==String(expected)) throw Error('Row slot disagrees');
        results.push({pos,expected});
      }
    }
    if (JSON.stringify(state.template)!==original) throw Error('Template mutated');
    calls.push({checks:results.length});
  })()`,
'''
        harness = BASE.replace('const scenarios={', 'const scenarios={' + scenario)
        out = subprocess.run([NODE, '-e', harness, str(WEB), 'identities'], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [{'checks': 168}])


if __name__ == '__main__':
    unittest.main()
