const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Workflow = require('../workflow.js');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const setup = html.slice(html.indexOf('  function setupWorkflow() {'), html.indexOf('  /* ---------- boot ---------- */'));

function workspace(canEdit = true) {
  let active = null;
  class Node {
    constructor(tag, cls = '', text = '') { this.tag = tag; this.className = cls; this.text = text; this.children = []; this.attrs = {}; this.hidden = false; this.value = ''; this.classList = {add:()=>{},toggle:()=>{}}; }
    get textContent() { return this.text + this.children.map(c=>c.textContent).join(''); }
    set textContent(v) { this.text = v; this.children = []; }
    get isConnected() { return this === root || !!this.parentElement?.isConnected; }
    appendChild(n) { if(n.parentElement) n.parentElement.children = n.parentElement.children.filter(c=>c !== n); this.children.push(n); n.parentElement = this; return n; }
    append(...nodes) { nodes.forEach(n=>this.appendChild(n)); }
    prepend(n) { this.appendChild(n); this.children = [n,...this.children.filter(c=>c !== n)]; }
    replaceChildren(...nodes) { this.children.forEach(n=>n.parentElement=null); this.children=[]; this.append(...nodes); }
    setAttribute(k,v) { this.attrs[k]=v; }
    getAttribute(k) { return this.attrs[k]; }
    querySelectorAll(tag) { return this.children.flatMap(n=>[...(n.tag===tag?[n]:[]),...n.querySelectorAll(tag)]); }
    focus() { active=this; }
    setSelectionRange() {}
    scrollIntoView() {}
  }
  const root = new Node('body'), top = new Node('header'), stage = new Node('main'), board = new Node('div'), headerRow = new Node('div');
  root.append(top,stage); top.append(headerRow); stage.append(board);
  const accessBar = new Node('div','row access-controls'), accessButton=new Node('button','',canEdit?'Lock editing':'Unlock editing');accessBar.appendChild(accessButton);top.appendChild(accessBar);
  const ids = {};
  for(const id of ['status','editbtn','setsbtn','shbtn','pricebtn','sumbtn','donebtn','ordbtn','altbtn','pkbtn','minebtn']) { ids[id]=new Node(id==='status'?'span':'button','',id); headerRow.appendChild(ids[id]); }
  ids.pkbtn.hidden = true; ids.minebtn.hidden = true;
  const document = {body:root,createTextNode:t=>new Node('text','',t),get activeElement(){return active;},querySelector:s=>({'.top':top,'.stage':stage,'.board':board,'.top>.row':headerRow}[s]),querySelectorAll:()=>[]};
  const cards = [Workflow.card({num:'T-001',name:'First card',set:'OP-01',status:'released',col:'Red',variants:[{id:'p1',kind:'parallel',source:'OP-01'},{id:'p3',kind:'parallel',source:'OP-04'},{id:'p2',kind:'reprint',sameAs:'p1',source:'PRB-02'}]}),Workflow.card({num:'T-002',name:'Second card',set:'OP-02',status:'released',col:'Blue',variants:[]}),Workflow.card({num:'T-003',name:'Wanted card',set:'OP-03',status:'released',variants:[]})];
  let col = {'T-001':{jp:true},'T-002':{ordered:true}};
  const stores={pending:null};
  let editor;
  const execute = new Function('el','$','document','Option','cards','col','products','catalog','binders','Workflow','grid','collectionStore','catalogStore','setStatus','recoverPending','scheduleSave','rebuild','artUrls','openInfo','edDlg', 'var canEdit='+JSON.stringify(canEdit)+';var workflowRedraw, workflowVersionEditor, editMode=false;'+setup+'setupWorkflow();return {redraw:()=>workflowRedraw(),collection:()=>col};');
  const state=execute((t,c,s)=>new Node(t,c,s),id=>ids[id] || null,document,function(t,v){const o=new Node('option','',t);o.value=v;return o;},cards,col,{products:[]},{drafts:[]},[],Workflow,new Node('div'),stores,stores,()=>{},()=>{},()=>{},()=>{},()=>['x'],()=>{},new Node('dialog'));
  const panel=stage.children[0], nav=top.children[0];
  const find=(text,parent=root)=>parent.querySelectorAll('button').find(b=>b.textContent===text);
  return {root,panel,nav,ids,state,find,accessButton,accessBar,active:()=>active, input:label=>panel.querySelectorAll('input').find(i=>i.getAttribute('aria-label')===label), select:label=>panel.querySelectorAll('select').find(i=>i.getAttribute('aria-label')===label)};
}

test('Binder is the default route and preserves hash handling outside workspace',()=>{
  const w=workspace(); assert.equal(w.panel.hidden,true);assert.equal(w.nav.children[0].textContent,'Binder');
  assert.match(html,/exec\(location\.hash\)/); assert.doesNotMatch(setup,/location\.hash\s*=/);
});
test('legacy actions survive search redraws and switching views',()=>{
  const w=workspace();w.find('Wishlist').onclick();assert.ok(w.find('Share wishlist'));assert.ok(w.find('Estimate price'));
  const search=w.input('Search wishlist');search.value='First';search.oninput();assert.ok(w.find('Share wishlist'));
  w.find('Settings').onclick();assert.ok(w.find('Sharing options'));w.find('Collection').onclick();w.find('Settings').onclick();assert.ok(w.find('Sharing options'));assert.equal(w.find('Manage passkeys'),undefined);
});
test('unique artwork search matches an alternate print source',()=>{
  const w=workspace();w.find('Collection').onclick();const source=w.select('sources');source.value='PRB-02';source.onchange();
  assert.equal(w.panel.querySelectorAll('article').length,1);assert.match(w.panel.textContent,/Alt art/);assert.doesNotMatch(w.panel.textContent,/Reprint \/ foil/);
});
test('bulk selection keeps keyboard focus and disables partial type actions',()=>{
  const w=workspace();w.find('Collection').onclick();w.find('Select cards').onclick();assert.equal(w.active().textContent,'Done selecting');const check=w.input('Select T-001 Base');check.checked=true;check.onchange();
  assert.equal(w.active().getAttribute('aria-label'),'Select T-001 Base');assert.equal(w.find('Skip base').disabled,false);assert.equal(w.find('Clear selected print marks').disabled,true);
  const alt=w.input('Select T-001 Alt art');alt.checked=true;alt.onchange();assert.equal(w.find('Skip base').disabled,true);assert.equal(w.find('Clear selected print marks').disabled,true);
  w.find('Clear selection').onclick();assert.match(w.panel.textContent,/0 selected/);
});
test('owned cards open the viewer instead of silently clearing ownership',()=>{
  const w=workspace();w.find('Collection').onclick();const before=JSON.stringify(w.state.collection());w.find('View card').onclick();assert.equal(JSON.stringify(w.state.collection()),before);
});
test('Wishlist offers order and Orders offers receive with editable note',()=>{
  const w=workspace();w.find('Wishlist').onclick();assert.ok(w.find('Mark ordered'));w.find('Orders').onclick();assert.ok(w.find('Received'));assert.ok(w.input('Order note for T-002 Base'));assert.equal(w.find('Add version'),undefined);
});

test('selection focus follows stable artwork identity when a card has two AA',()=>{
  const w=workspace();w.find('Collection').onclick();w.find('Select cards').onclick();
  const second=w.panel.querySelectorAll('input').find(i=>i.getAttribute('data-artwork-id')==='T-001:p3');
  second.checked=true;second.onchange();
  assert.equal(w.active().getAttribute('data-artwork-id'),'T-001:p3');assert.equal(w.active().checked,true);
  assert.equal(w.panel.querySelectorAll('input').find(i=>i.getAttribute('data-artwork-id')==='T-001:p1').checked,false);
});


test('public browsing keeps Binder and card navigation without edit controls',()=>{
  const w=workspace(false);
  assert.equal(w.panel.hidden,true);
  assert.equal(w.find('Settings'),undefined);
  assert.equal(w.find('Orders'),undefined);
  w.find('Collection').onclick();
  for(const label of ['Add card','Select cards','Mark owned','Undo last change','Retry save']) {
    const b=w.find(label);assert.ok(!b || b.hidden,label);
  }
  assert.ok(w.find('View card'));
  w.find('Wishlist').onclick();
  for(const label of ['Mark ordered','Share wishlist','Estimate price','Select cards'])assert.equal(w.find(label),undefined,label);
  assert.deepEqual(w.state.collection(),{'T-001':{jp:true},'T-002':{ordered:true}});
});


test('editing access remains connected and available across every workspace route',()=>{
  for(const editable of [false,true]){
    const w=workspace(editable),label=editable?'Lock editing':'Unlock editing';
    for(const route of editable?['Collection','Wishlist','Orders','Settings','Binder']:['Collection','Wishlist','Binder']){
      w.find(route).onclick();assert.equal(w.accessButton.isConnected,true);assert.equal(w.accessButton.hidden,false);assert.equal(w.find(label),w.accessButton);assert.equal(w.accessBar.parentElement.tag,'header');
    }
  }
  assert.match(html,/\.top>\.row:not\(\.access-controls\)>/);
  assert.match(html,/\.top>\.access-controls\{order:3;display:flex/);
});
