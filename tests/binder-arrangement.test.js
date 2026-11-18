const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const EventoBatch = require('../batch.js');
const html = fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const source = html.slice(html.indexOf('  var movingPocket'), html.indexOf('  function tile('));
class Element {
  constructor(tag) { this.tag = tag; this.events = {}; this.children = []; this.dataset = {}; this.classList = {add(){},remove(){}}; }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  addEventListener(type,fn) { this.events[type] = fn; }
  emit(type,data={}) { return this.events[type]?.({pointerId:1,button:0,isPrimary:true,clientX:0,clientY:0,stopPropagation(){},...data}); }
  setPointerCapture() {}
  setAttribute() {}
  showModal() {}
  remove() {}
  close() { this.emit('close'); }
}
function fixture() {
  const cards = [{num:'B',slot:3,variants:[{id:'p1'}]}, {num:'A',slot:1,image:'image'}, {num:'C',binder:'other',slot:1}];
  const catalog = {cards,binders:[{id:'main'},{id:'other'}]}, writes=[], questions=[], errors=[];
  const document = new Element(); document.body = new Element();
  const env = {document, EventoBatch, PER:16, currentBinder:'main',page:1,pageGen:4,editMode:true,canEdit:true,
    catalogStore:{etag:'old'}, bySlot:{1:cards[1],3:cards[0]}, grid:{contains:()=>true},
    el:tag=>new Element(tag), assertCatalogReady(){if(env.pending)throw Error('pending');},
    async fetchCatalogSnapshot(){return {catalog,etag:'old'};},
    async commitCatalogCandidate(candidate,snapshot){if(env.failure)throw Error('save failed');writes.push(candidate);},
    confirmEdit(action,target){questions.push([action,target]);return !env.cancel;}, setStatus:error=>errors.push(error)
  };
  vm.createContext(env); vm.runInContext(source,env);
  const handle=env.pocketHandle(cards[1]);
  function down(){handle.emit('pointerdown');}
  function finish(slot){const tile=new Element();tile.closest=()=>tile;tile.dataset.slot=slot;document.elementFromPoint=()=>tile;
    handle.emit('pointermove',{clientX:20});handle.emit('pointerup',{clientX:20});}
  return {env,handle,document,catalog,questions,errors,writes,down,finish};
}
const settled=()=>new Promise(resolve=>setImmediate(resolve));
test('drag swaps occupied pockets and keeps catalog order, metadata and source intact',async()=>{
  const h=fixture(), original=structuredClone(h.catalog);
  h.down();h.finish(3);h.handle.emit('click',{detail:1});await settled();
  assert.equal(h.document.body.children.length,0);assert.equal(h.questions.length,1);assert.equal(h.writes.length,1);
  original.cards[0].slot=1;original.cards[1].slot=3;assert.deepEqual(h.writes[0],original);
  assert.deepEqual(h.catalog.cards.map(c=>c.slot),[3,1,1]);
});
test('keyboard chooser moves to an empty pocket',async()=>{
  const h=fixture();h.handle.emit('click',{detail:0});
  const [label,move]=h.document.body.children[0].children;label.children[0].value='8';move.onclick();
  await settled();assert.deepEqual(h.writes[0].cards.map(c=>c.slot),[3,8,1]);assert.equal(h.questions.length,1);
});
test('cancel, invalid target, same slot, changed context and pending writes leave state untouched',async()=>{
  for (const scenario of ['escape','pointercancel','same','wrongPage','capacity','binder','page','locked','edit','stale','pending','cancel','failure']) {
    const h=fixture();h.down();
    if(scenario==='escape')h.document.emit('keydown',{key:'Escape'});
    if(scenario==='pointercancel')h.handle.emit('pointercancel');
    if(scenario==='binder')h.env.currentBinder='other';
    if(scenario==='page')h.env.page=2;
    if(scenario==='locked')h.env.canEdit=false;
    if(scenario==='edit')h.env.editMode=false;
    if(scenario==='stale')h.env.catalogStore.etag='new';
    h.env.pending=scenario==='pending';h.env.cancel=scenario==='cancel';h.env.failure=scenario==='failure';
    h.finish(({same:1,wrongPage:17,capacity:1025})[scenario]||3);await settled();
    assert.equal(h.writes.length,0,scenario);assert.deepEqual(h.catalog.cards.map(c=>c.slot),[3,1,1],scenario);
    assert.equal(h.questions.length,['cancel','failure'].includes(scenario)?1:0,scenario);
    if(scenario==='failure')assert.deepEqual(h.errors,['save failed']);
  }
});

test('preview coordinates and set jumps use physical pockets after an unsorted import',()=>{
  const records=[{num:'late',set:'OP',slot:34},{num:'other',set:'EX',slot:1,binder:'other'},{num:'early',set:'OP',slot:6},{num:'loose',set:'EX',slot:null}];
  const ss=new Element(), pvEl=new Element();const placeholder={value:''};ss.firstElementChild=placeholder;ss.value='2';ss.replaceChildren=(...children)=>{ss.children=children;};
  const env={cards:records,EventoBatch,Workflow:{card:c=>c},currentBinder:'main',PER:16,pvEl,artMode:'en',
    $:()=>ss,render(){},el:(tag,cls,text)=>({cls,text}),pageOf:s=>Math.ceil(s/16),artImg(){},
    complete:()=>false,ordered:()=>false,jpText:()=>'',tagged:()=>false};
  vm.createContext(env);
  vm.runInContext(html.slice(html.indexOf('  function rebuild()'),html.indexOf('    if(typeof PROD',html.indexOf('  function rebuild()'))) + '}',env);
  vm.runInContext(html.slice(html.indexOf('  function preview(n)'),html.indexOf("  grid.addEventListener('mouseover'")),env);
  env.rebuild();assert.deepEqual(ss.children.map(option=>option.value),['',1]);assert.equal(ss.value,'');
  env.preview('early');assert.match(pvEl.children.find(n=>n.cls==='meta').text,/R2C2$/);
  pvEl.children=[];env.preview('loose');assert.match(pvEl.children.find(n=>n.cls==='meta').text,/Unplaced$/);
});
