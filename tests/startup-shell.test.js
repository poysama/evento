const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
const boot=html.slice(html.indexOf('  /* ---------- boot ---------- */'),html.lastIndexOf('})();'));
const legacy=['sumbtn','donebtn','ordbtn','altbtn','pricebtn','shbtn','editbtn','setsbtn'];
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject};}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
 const elements=[],ids={};
 for(const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)){
  const attrs=match[2],id=/\bid="([^"]+)"/.exec(attrs)?.[1];
  const node={tag:match[1],id,hidden:/\bhidden(?:\s|=|$)/.test(attrs),disabled:/\bdisabled(?:\s|=|$)/.test(attrs),textContent:'',children:[],attrs:{},appendChild(n){this.children.push(n)},setAttribute(k,v){this.attrs[k]=v},startup:/\bdata-startup-control(?:\s|=|$)/.test(attrs)};
  elements.push(node);if(id)ids[id]=node;
 }
 const classes=new Set(/<body[^>]*class="([^"]+)"/.exec(html)[1].split(/\s+/));
 const access=deferred(),catalog=deferred(),collection=deferred(),calls=[];
 const ctx={console,canEdit:false,recognitionAvailable:false,cards:[],col:{},catalog:{},byNum:{},bySlot:{},jpArt:{},products:{},share:{},binders:[],currentBinder:'main',firstPage:{},N:64,page:1,
  $:id=>ids[id],grid:ids.grid,document:{body:{classList:{add:n=>classes.add(n),remove:n=>classes.delete(n),toggle(n,value){value?classes.add(n):classes.delete(n)}}},querySelectorAll:s=>s==='[data-startup-control]'?elements.filter(n=>n.startup):[]},
  location:{hash:'#p=21',reload(){calls.push('reload')}},localStorage:{getItem(){return null}},
  fetch(url){calls.push(url);return url==='/api/access'?access.promise:Promise.resolve({ok:true,json:async()=>[]})},
  catalogStore:{load(){calls.push('catalog');return catalog.promise},pending:null},collectionStore:{load(){calls.push('collection');return collection.promise},pending:null},
  migrate:x=>x,renderBinders(){},makeArtSeg(){},el:tag=>({tag,children:[],appendChild(n){this.children.push(n)}}),pageOf:slot=>Math.ceil(slot/16),Workflow:require('../workflow.js'),
  rebuild(){},render(){},pkRefresh(){},recoverPending(){},setupWorkflow(){calls.push('setup');},setStatus(message,error){ids.status.textContent=message;ids.status.error=error;},errorGuidance:message=>'Could not load: '+message,
 };
 vm.createContext(ctx);vm.runInContext(boot,ctx);
 return{ctx,ids,classes,access,catalog,collection,calls,elements};
}
function resolveAccess(h,canEdit){h.access.resolve({ok:true,json:async()=>({canEdit})});}
function resolveData(h){h.catalog.resolve({cards:[{num:'ST01-014',name:'Guard Point',slot:1,set:'ST-01'}],products:{products:[]},share:{},binders:[{id:'main'}]});h.collection.resolve({});}
test('served first paint has modern inert navigation and no visible legacy tools',async()=>{
 const h=harness();for(const id of legacy)assert.equal(h.ids[id].hidden,true,id);
 assert.ok(h.ids['workflow-loading-nav']);const nav=html.match(/<nav[^>]*id="workflow-loading-nav"[^>]*>([\s\S]*?)<\/nav>/)[1];for(const button of nav.matchAll(/<button([^>]*)>/g))assert.match(button[1],/\bdisabled\b/);assert.equal(h.ids['startup-panel'].hidden,false);assert.equal(h.ids['startup-retry'].hidden,false);
 assert.equal(h.calls.includes('catalog'),false);assert.equal(h.calls.includes('setup'),false);
 resolveAccess(h,true);await settle();assert.equal(h.classes.has('view-only'),false);
 for(const id of legacy)assert.equal(h.ids[id].hidden,true,id+' while editor data is pending');
 assert.equal(h.calls.includes('setup'),false);
 resolveData(h);await settle();assert.equal(h.calls.filter(x=>x==='setup').length,1);assert.equal(h.ids['startup-panel'].hidden,true);
});
test('rejected catalog keeps a visible error/reload shell without exposing old tools',async()=>{
 const h=harness();resolveAccess(h,true);await settle();h.catalog.reject(Error('Catalog load failed (503)'));h.collection.resolve({});await settle();
 assert.equal(h.calls.includes('setup'),false);assert.equal(h.ids['startup-panel'].hidden,false);assert.match(h.ids['startup-message'].textContent,/Catalog load failed/);
 assert.equal(h.ids['startup-message'].attrs.role,'alert');assert.equal(h.ids['startup-retry'].hidden,false);for(const id of legacy)assert.equal(h.ids[id].hidden,true);
 assert.match(h.ctx.grid.textContent,/Catalog load failed/);
});
test('public and failed-access loads complete without adding editor access',async()=>{
 for(const rejected of [false,true]){const h=harness();if(rejected)h.access.reject(Error('Network'));else resolveAccess(h,false);await settle();resolveData(h);await settle();assert.equal(h.ctx.canEdit,false);assert.equal(h.classes.has('view-only'),true);assert.equal(h.ids['startup-panel'].hidden,true);assert.equal(h.calls.filter(x=>x==='setup').length,1);}
});
test('initial shell offers reload even if main bootstrap fails and includes no-JS guidance',()=>{
 assert.match(html,/<button[^>]*id="startup-retry"[^>]*onclick="location\.reload\(\)"/);
 assert.match(html,/<noscript>[\s\S]*Enable JavaScript[\s\S]*<\/noscript>/);
});

test('collection failure stays recoverable after catalog resolves',async()=>{
 const h=harness();resolveAccess(h,true);await settle();h.catalog.resolve({cards:[]});h.collection.reject(Error('Collection unavailable'));await settle();assert.equal(h.calls.includes('setup'),false);assert.equal(h.ids['startup-panel'].hidden,false);assert.match(h.ids['startup-message'].textContent,/Collection unavailable/);for(const id of legacy)assert.equal(h.ids[id].hidden,true);
});
