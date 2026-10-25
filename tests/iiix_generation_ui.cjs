// Execute the real save/generate functions with deferred responses.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const code=fs.readFileSync('app/web/app.js','utf8'),nodes={};
const node=id=>nodes[id]||(nodes[id]={checked:false,parentElement:{classList:{remove(){},add(){}}}});
let calls=[],pending=[],result=null,saved=true,shown=0;
const ctx={$:node,clearTimeout(){},renderSaveState(){},toast(){},showPending(){},showResult:(ok,data)=>result={ok,data},Names:{ok:()=>true},nameMax:()=>12,
 state:{project:{id:'P',menu_language:'en',capabilities:{candidate_generation:true,full_firmware:false}},changes:{8:{film:'gold200'}},template:{slots:[]},status:{prefs:{preflight_seen:true}}},
 api:(...a)=>{calls.push(a);return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
 // Since PR #5 生成 is a job the page polls (apiJob); here it stands for the plain request it replaces.
 apiJob:(pid,task,o)=>ctx.api('POST','/api/projects/'+pid+'/'+task,o.body),
 window:{DarkslideFilm:{fitPending:async()=>0}}};  // 生成 fits pending presets first (nothing pending here)
vm.createContext(ctx);
vm.runInContext(code.slice(code.indexOf('async function saveProject()'),code.indexOf('// ---------------------------------------------------------------- render')),ctx);
vm.runInContext(code.slice(code.indexOf('async function generate()'),code.indexOf('function showPending()')),ctx);
const tick=()=>new Promise(r=>setImmediate(r));
node('untestedDialog').showModal=()=>{shown++;};
// Untested-risk confirmation: first click only opens the dialog, no request; then confirm once.
const confirm=async()=>{shown=0;const before=calls.length;await ctx.generate();assert.equal(shown,1);assert.equal(calls.length,before);
 assert.equal(node('untestedOk').disabled,true);assert.equal(node('untestedBox').checked,false);ctx.state.untestedAck=true;};
(async()=>{
 let p=ctx.saveProject();ctx.state.project={id:'Q',menu_language:'en'};pending.shift().resolve({});await p;assert.equal(calls.length,1);assert.equal(ctx.state.project.id,'Q');
 calls=[];ctx.state.project={id:'P',menu_language:'en',capabilities:{candidate_generation:true,full_firmware:false}};ctx.saveProject=async()=>saved;
 node('iiixRecipeExtension').checked=true;ctx.state.changes={8:{film:'gold200'}};
 ctx.state.status.prefs={};node('preflightDialog').showModal=()=>{};await ctx.generate();assert.match(node('preflightRecovery').textContent,/fwdc243b/);assert.doesNotMatch(node('preflightRecovery').textContent,/fwdc239b/);
 ctx.state.status.prefs={preflight_seen:true};ctx.state.changes={};
 // 1.0: recipe module off (capability false) - a stray ticked box neither enables an empty build nor reaches the API.
 shown=0;await ctx.generate();assert.equal(shown,0);assert.equal(calls.length,0);
 ctx.state.changes={8:{film:'gold200'}};await confirm();assert.equal(node('untestedExtension').hidden,true);p=ctx.generate();await tick();
 assert.equal(calls[0][1],'/api/projects/P/iiix-candidate');assert.equal(calls[0][2].recipe_extension,false);
 pending.shift().resolve({build_id:'A',project_id:'P'});await p;assert.equal(result.data.build_id,'A');calls=[];result=null;
 // With the module switched on (future), the UI still sends exactly what the box says.
 ctx.state.project={id:'P',menu_language:'en',capabilities:{candidate_generation:true,full_firmware:false,candidate_recipe_extension:true}};ctx.state.changes={};
 await confirm();assert.equal(node('untestedExtension').hidden,false);p=ctx.generate();await tick();assert.equal(ctx.state.untestedAck,false);
 assert.equal(calls[0][1],'/api/projects/P/iiix-candidate');assert.equal(calls[0][2].recipe_extension,true);assert.equal(Object.keys(calls[0][2].expected_changes).length,0);
 ctx.state.project={id:'Q',menu_language:'en'};pending.shift().resolve({build_id:'B',project_id:'P'});await p;assert.equal(result,null);
 calls=[];ctx.state.project={id:'P',menu_language:'en',capabilities:{candidate_generation:true}};ctx.state.changes={8:{film:'gold200'}};node('iiixRecipeExtension').checked=false;
 await confirm();assert.equal(node('untestedExtension').hidden,true);p=ctx.generate();await tick();assert.equal(calls[0][2].recipe_extension,false);pending.shift().resolve({build_id:'C',project_id:'P'});await p;assert.equal(result.data.build_id,'C');
 calls=[];saved=false;await confirm();await ctx.generate();assert.equal(calls.length,0);assert.equal(ctx.state.untestedAck,false);
 // Since 1.3.1 the standard GR IIIx is camera-tested: no untested-risk dialog, straight to the request.
 // The Urban and HDF editions still ask every time.
 saved=true;calls=[];shown=0;ctx.state.untestedAck=false;node('iiixRecipeExtension').checked=false;ctx.state.changes={8:{film:'gold200'}};
 ctx.state.project={id:'P',menu_language:'en',camera_variant:'GR IIIx',capabilities:{candidate_generation:true,full_firmware:false}};
 p=ctx.generate();await tick();assert.equal(shown,0);assert.equal(calls.length,1);pending.shift().resolve({build_id:'D',project_id:'P'});await p;
 // 2026-10-25: 柔光 / 菜单排序 are tested on GR III only, so the standard GR IIIx asks again when the build carries one.
 calls=[];shown=0;ctx.state.untestedAck=false;ctx.state.features={soft_glow:true};
 ctx.state.project={id:'P',menu_language:'en',camera_variant:'GR IIIx',capabilities:{candidate_generation:true,full_firmware:false}};
 await ctx.generate();await tick();assert.equal(shown,1);assert.equal(calls.length,0);
 assert.equal(node('untestedFeatures').hidden,false);assert.equal(node('untestedVariant').hidden,true);assert.equal(node('untestedFeatureNames').textContent,'柔光');
 ctx.state.features={};
 for(const variant of ['GR IIIx Urban Edition','GR IIIx HDF']){
  calls=[];ctx.state.untestedAck=false;ctx.state.project={id:'P',menu_language:'en',camera_variant:variant,capabilities:{candidate_generation:true,full_firmware:false}};
  await confirm();
 }
 console.log('PASS candidate UI: untested-risk confirmation every time (not for the camera-tested standard GR IIIx unless 柔光/排序 are in the build), recipe module off by capability, explicit extension when on, empty restore-only, saved-state barrier, project switch isolation');
})().catch(e=>{console.error(e);process.exitCode=1});
