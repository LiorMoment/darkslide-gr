"""Real sidebar rendering and lifecycle, using a minimal DOM double.

1.0 lists the presets in DARKSLIDE GR's own order (user, 2026-10-14: 负片 right under 正片) while the
camera menu keeps its factory order; once the user sorts the camera menu the list follows that order
(user, 2026-10-15), 自定义 1/2 last. Since 2026-10-24 the menu is sorted right in this list, so a model that
can be sorted always lists the camera's own menu order (负片 before 正片 in the factory order); a model that
cannot be sorted keeps the 1.0 list. Identities (slot numbers, project keys) never move."""
import json
import subprocess
import unittest
from test_menu_order_ui import HARNESS, NODE, WEB

BASE = HARNESS.replace('replaceChildren() {}, append() {},',
    'children: [], attrs: {}, events: {}, replaceChildren(...n) { o.children=n; }, append(n) { o.children.push(n); },'
).replace('addEventListener() {}, removeEventListener() {}, focus() {}, setAttribute() {}, removeAttribute() {},',
    'addEventListener(k,f) { o.events[k]=f; }, removeEventListener() {}, focus() { document.activeElement=o; }, setAttribute(k,v) { o.attrs[k]=String(v); }, getAttribute(k) { return o.attrs[k] ?? null; }, removeAttribute() {},')
BASE = BASE[:BASE.index('const scenarios =')] + r'''
vm.runInContext(`
  state.template.slots = Array.from({length:14}, (_,i) => ({no:i+1,name:'Preset '+(i+1),abbr:'P'+(i+1),rename:'open',icon:'open'}));
  state.template.firmware = '2.10'; state.template.model = 'GR III';
  state.changes = {12:{name:'My Negative',icon:{asset:'negative.png'},film:'film-negative'}};
  state.sel=12; state.factory=null;
  renderAll=()=>renderSlots();
  maybeGuide=()=>{};
  window.DarkslideOrder.wire({status:()=>state.status,get:()=>state.order,set:setOrder,
    supported:()=>!!(state.project.capabilities||{}).menu_order,note:()=>'',
    fixed:()=>[13,14],label:no=>({name:slotOf(no).name,icon:document.createElement('span')})});
  const nameOf=b=>b.children.find(c=>c.className==='slot-name');
  const identityOf=b=>state.template.slots.find(s=>(state.changes[s.no]?.name ?? s.name)===nameOf(b).textContent).no;
  globalThis.inspect=()=>({
    ids:$('slots').children.map(b=>identityOf(b)),
    slots:$('slots').children.map(b=>Number(b.getAttribute('data-slot'))),
    selected:$('slots').children.filter(b=>b.getAttribute('aria-selected')==='true').map(b=>identityOf(b)),
    focus:document.activeElement && document.activeElement.getAttribute && document.activeElement.getAttribute('data-slot'),
    names:$('slots').children.map(b=>nameOf(b).textContent),
    icon:$('slots').children.find(b=>identityOf(b)===12).children.find(c=>String(c.className).split(' ')[0]==='tile').children[0].getAttribute('src'),
    template:state.template.slots.map(s=>s.no),changes:state.changes,payload:('menu_order' in draftBody())?draftBody().menu_order:'absent'
  });
`,ctx);
const scenarios={
  factory:`renderSlots(); calls.push(inspect());`,
  lifecycle:`renderSlots(); $('slots').children.find(b=>b.getAttribute('data-slot')==='12').focus();
    setOrder([12,1,2,3,4,5,6,7,8,9,10,11]); calls.push(inspect());
    undo(); calls.push(inspect()); redo(); calls.push(inspect()); setOrder(null); calls.push(inspect());`,
  reopen:`(async()=>{await loadProject({id:'p_synthetic',title:'test',input_sha256:'fake',template_id:'gr3-2.10-reviewed-1',
    capabilities:{menu_order:true,menu_order_write:true,full_firmware:true,camera_fit:true,note:''},menu_language:'en',draft:{changes:state.changes,
    menu_order:window.DarkslideOrder.payload([11,10,9,8,7,12,6,5,4,3,2,1])}});
    calls.length=0; calls.push(inspect()); $('slots').children[0].events.click(); calls.push(inspect());})()`,
  unsupported:`state.project.capabilities={menu_order:false}; state.order=[11,10,9,8,7,12,6,5,4,3,2,1]; renderSlots(); calls.push(inspect());`,
  copy:`renderSlots(); const copy=window.DarkslideOrder.current(); copy.reverse(); setOrder([2,1,3,4,5,6,12,7,8,9,10,11]);
    window.DarkslideOrder.current().reverse(); renderSlots(); calls.push(inspect());`
};
(async()=>{await vm.runInContext(scenarios[scenario],ctx); process.stdout.write(JSON.stringify(ctx.calls));})()
.catch(e=>{console.error(e.stack);process.exit(1);});
'''
LISTED = [1, 2, 3, 4, 5, 6, 7, 12, 8, 9, 10, 11, 13, 14]   # 1.0 list: 负片 under 正片
CAMERA = [1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11, 13, 14]   # the camera's factory menu order
MOVED = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14]


@unittest.skipUnless(NODE, 'node not installed')
class SidebarOrder(unittest.TestCase):
    def run_case(self, name):
        out = subprocess.run([NODE, '-e', BASE, str(WEB), name], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def identity(self, row):
        self.assertEqual(row['template'], list(range(1, 15)))
        self.assertEqual(row['slots'], row['ids'])
        self.assertEqual(row['names'][row['ids'].index(12)], 'My Negative')
        self.assertTrue(row['icon'].endswith('/negative.png'))
        self.assertEqual(row['changes']['12']['film'], 'film-negative')

    def test_factory_order_lists_the_camera_menu(self):
        row = self.run_case('factory')[0]
        self.assertEqual(row['ids'], CAMERA)
        self.assertIsNone(row['payload'])
        self.identity(row)

    def test_move_undo_redo_reset_and_focus(self):
        rows = self.run_case('lifecycle')
        self.assertEqual(len(rows), 4)
        for row, expected in zip(rows, [MOVED, CAMERA, MOVED, CAMERA]):
            self.assertEqual(row['ids'], expected)
            self.assertEqual(row['selected'], [12])
            self.assertEqual(row['focus'], '12')
            self.identity(row)
        self.assertEqual(rows[0]['payload']['still_slots'], MOVED[:12])
        self.assertEqual(rows[0]['payload']['video_slots'], [n for n in MOVED[:12] if n not in (4, 10)])
        self.assertIsNone(rows[-1]['payload'])

    def test_reopen_and_click(self):
        before, after = self.run_case('reopen')
        self.assertEqual(before['ids'], [11, 10, 9, 8, 7, 12, 6, 5, 4, 3, 2, 1, 13, 14])
        self.assertEqual(after['selected'], [11])
        self.assertEqual(before['payload']['still_slots'], before['ids'][:12])
        self.identity(before)
        self.identity(after)

    def test_a_model_that_cannot_sort_keeps_the_1_0_list(self):
        row = self.run_case('unsupported')[0]
        self.assertEqual(row['ids'], LISTED)
        self.assertEqual(row['payload'], 'absent')   # the draft write leaves a stored order alone

    def test_order_is_a_copy(self):
        self.assertEqual(self.run_case('copy')[0]['ids'], [2, 1, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11, 13, 14])


if __name__ == '__main__':
    unittest.main()
