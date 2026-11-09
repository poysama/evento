const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const Workflow=require('../workflow.js');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const artworkCode=html.slice(html.indexOf('  function vwShowArt('),html.indexOf('  function resetViewerControls()'));
const viewerCode=html.slice(html.indexOf('  function resetViewerControls()'),html.indexOf('  function fillInfo()'));
function viewer(rawCards,collection={},options={}){
  class Node{
    constructor(text=''){this.textContent=text;this.children=[];this.attrs={};this.scrollTop=0;this.scrollLeft=0;}
    appendChild(node){this.children.push(node);return node;}
    replaceChildren(...nodes){this.children=nodes;}
    setAttribute(name,value){this.attrs[name]=value;}
  }
  const nodes=['header','controls','root','name','meta','meta2','wrap','prev','next','editVersion','note','jp','ord','onote','en','kr','foil','art','row','altp','ssb','altt','altd','asb','aw','ao','ah','altr','vv','sources','sourceButtons'];
  const vw=Object.fromEntries(nodes.map(name=>[name,new Node()]));
  const cards=rawCards.map(Workflow.card),byNum=Object.fromEntries(cards.map(card=>[card.num,card]));
  const rendered=[];let dialogs=0;
  const dlg={open:false,showModal(){this.open=true;dialogs++;}};
  const has=(num,key)=>!!collection[num]?.[key];
  const artworkState=(card,id)=>Workflow.effectiveState(card,collection[card.num]||{},id||'std');
  const source="var vwAlt=null,vwMode='jp',artMode="+JSON.stringify(options.language||'jp')+",vidx=0,openNum=null,currentBinder='main',canEdit="+JSON.stringify(options.canEdit!==false)+";"+artworkCode+viewerCode+"return {open:openInfo,next:()=>vstep(1),previous:()=>vstep(-1),update:()=>vwUpdate(false),close:()=>{dlg.open=false},selection:()=>vwAlt,mode:()=>vwMode,card:()=>cards[vidx].num};";
  const params={Workflow,PER:16,pageOf:slot=>Math.floor((slot-1)/16)+1,cards,byNum,col:collection,vw,dlg,document:{activeElement:null},el:(tag,cls,text)=>new Node(text||''),artImg:(image,num,language,version)=>rendered.push({num,language,version}),syncVwSeg(){},setBinder:id=>{},vwBuild(){throw Error('prebuilt test viewer expected');},artworkState,has,complete:num=>has(num,'jp')||Workflow.alternateVersions(byNum[num]).some(v=>artworkState(byNum[num],v.id)==='have'),tagged:has,ordered:num=>has(num,'ordered')&&!has(num,'jp'),noteOf:()=>'',jpText:()=>'',anyHave:num=>Workflow.alternateVersions(byNum[num]).some(v=>artworkState(byNum[num],v.id)==='have'),shownVer:num=>collection[num]?.show||'',versionDisplayState:(card,id)=>Workflow.artworkId(card,id)===id?artworkState(card,id):collection[card.num]?.alt?.[id]||'',altState:(num,id)=>collection[num]?.alt?.[id]||'',verBase:(card,id)=>id,verLabel:(card,id)=>id,verNote:()=>'',vwShowingJP:()=>true,preloadArt(){}};
  const api=new Function(...Object.keys(params),source)(...Object.values(params));
  return {...api,vw,rendered,dialogs:()=>dialogs};
}
const card=(num,variants)=>({num,name:num,set:'OP-01',rar:'R',slot:1,variants});
test('generic card opening and navigation default to owned alternate artwork',()=>{
  const h=viewer([card('A',[{id:'p1',kind:'parallel',image:'jp'}]),card('B',[{id:'m',kind:'manga',image:'manga'}])],{A:{jp:true,alt:{p1:'have'}},B:{alt:{m:'have'}}});
  h.open('A');assert.equal(h.selection(),'p1');assert.equal(h.rendered.at(-1).version,'p1');
  h.next();assert.equal(h.card(),'B');assert.equal(h.selection(),'m');
  h.previous();assert.equal(h.selection(),'p1');
});
test('owned canonical artwork includes source aliases and legacy Manga ownership',()=>{
  const alias=viewer([card('A',[{id:'p1',kind:'parallel'},{id:'source',kind:'reprint',sameAs:'p1'}])],{A:{alt:{source:'have'}}});alias.open('A');assert.equal(alias.selection(),'p1');
  const legacy=viewer([{...card('M',[{id:'m',kind:'manga'}]),manga:'m'}],{M:{manga:true}});legacy.open('M');assert.equal(legacy.selection(),'m');
});
test('owned binder artwork preference wins, otherwise catalog order wins and Standard binder override stays separate',()=>{
  const c=card('A',[{id:'p1',kind:'parallel'},{id:'m',kind:'manga'},{id:'alias',kind:'reprint',sameAs:'m'}]);
  for(const [show,expected] of [['m','m'],['alias','m'],['std','p1'],['removed','p1'],[undefined,'p1']]){
    const h=viewer([c],{A:{show,alt:{p1:'have',m:'have',alias:'have'}}});h.open('A');assert.equal(h.selection(),expected,String(show));
  }
});
test('Wanted, Ordered, base reprints and removed artwork are not owned alternate defaults',()=>{
  const h=viewer([card('A',[{id:'p1',kind:'parallel'},{id:'p2',kind:'manga'},{id:'base',kind:'reprint',sameAs:'std'}])],{A:{jp:true,show:'p1',alt:{p1:'want',p2:'ordered',base:'have',removed:'have'}}});h.open('A');assert.equal(h.selection(),null);
});
test('explicit Standard and requested unowned versions override the automatic choice without flashing owned artwork',()=>{
  const h=viewer([card('A',[{id:'owned',kind:'parallel',image:'jp'},{id:'wanted',kind:'parallel',image:'jp',enImage:'en'}])],{A:{alt:{owned:'have',wanted:'want'}}},{language:'en'});
  h.open('A','std');assert.equal(h.selection(),null);assert.equal(h.mode(),'en');assert.deepEqual(h.rendered,[{num:'A',language:'en',version:null}]);
  h.close();h.open('A','wanted');assert.equal(h.selection(),'wanted');assert.equal(h.rendered.at(-1).version,'wanted');
});
test('manual version selection remains until another card is opened and language fallback is preserved',()=>{
  const h=viewer([card('A',[{id:'jp',kind:'parallel',image:'jp'},{id:'en',kind:'parallel',enImage:'en'}]),card('B',[])],{A:{alt:{en:'have'}}},{language:'jp',canEdit:false});
  h.open('A');assert.equal(h.selection(),'en');assert.equal(h.mode(),'en');
  h.vw.vv.children.find(button=>button.textContent==='jp').onclick();h.update();assert.equal(h.selection(),'jp');
  h.next();assert.equal(h.selection(),null);h.previous();assert.equal(h.selection(),'en');
});
