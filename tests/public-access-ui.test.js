const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const RevisionStore=require('../save.js');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const access=html.slice(html.indexOf("  $('access-btn').onclick"),html.indexOf('\n\n  function el('));
function accessHarness(canEdit,response={ok:true}) {
  const calls=[];let reloads=0;
  const submit={disabled:false};
  const ids=Object.fromEntries(['access-btn','access-error','access-dialog','access-passphrase','access-cancel','access-form','access-passkey'].map(id=>[id,{value:'',textContent:'',focus(){},showModal(){this.open=true;},close(){this.open=false;},addEventListener(){},querySelector(){return submit;}}]));
  const stores={pending:null};
  new Function('$','canEdit','collectionStore','catalogStore','saving','setStatus','fetch','location','window','PublicKeyCredential','navigator',access)(id=>ids[id],canEdit,stores,stores,false,t=>calls.push(t),async(url,options)=>{calls.push([url,options]);return url==='/webauthn/login/options'?{...response,json:async()=>({token:'challenge-token',options:{challenge:'challenge'}})}:response;},{reload(){reloads++;}},{PublicKeyCredential:{}},{parseRequestOptionsFromJSON:o=>o},{credentials:{get:async()=>({toJSON:()=>({id:'enrolled-device'})})}});
  return {ids,calls,stores,submit,reloads:()=>reloads};
}
test('anonymous opening asks only on explicit unlock and clears cancelled credential',async()=>{
  const h=accessHarness(false);assert.deepEqual(h.calls,[]);
  await h.ids['access-btn'].onclick();assert.equal(h.ids['access-dialog'].open,true);
  h.ids['access-passphrase'].value='a-secret';h.ids['access-cancel'].onclick();assert.equal(h.ids['access-passphrase'].value,'');assert.equal(h.reloads(),0);
});
test('unlock posts credential once and reloads canonical authenticated state',async()=>{
  const h=accessHarness(false);h.ids['access-passphrase'].value='a-secret';
  await h.ids['access-form'].onsubmit.call(h.ids['access-form'],{preventDefault(){}});
  assert.equal(h.calls[0][0],'/login');assert.deepEqual(JSON.parse(h.calls[0][1].body),{passcode:'a-secret'});
  assert.equal(h.reloads(),1);assert.equal(h.ids['access-passphrase'].value,'');assert.equal(h.submit.disabled,false);
  assert.doesNotMatch(access,/localStorage|sessionStorage|location\.(?:hash|search)/);
});
test('bad passphrase stays locked with useful error and no reload',async()=>{
  const h=accessHarness(false,{ok:false,status:401});
  await h.ids['access-form'].onsubmit.call(h.ids['access-form'],{preventDefault(){}});
  assert.match(h.ids['access-error'].textContent,/passphrase did not work/);assert.equal(h.reloads(),0);assert.equal(h.submit.disabled,false);
});
test('lock clears server cookie and reloads, pending edits prevent accidental loss',async()=>{
  const h=accessHarness(true);h.stores.pending={draft:true};await h.ids['access-btn'].onclick();assert.equal(h.reloads(),0);assert.equal(h.calls.length,1);
  h.stores.pending=null;await h.ids['access-btn'].onclick();assert.equal(h.calls[1][0],'/logout');assert.equal(h.reloads(),1);
});
test('anonymous store cannot read, restore, discard or overwrite editor pending drafts',async()=>{
  const pending=JSON.stringify({etag:'private',data:{note:'private'}}),writes=[],requests=[];
  const store=new RevisionStore('/api/collection',{canWrite:()=>false,fetch:async(...args)=>requests.push(args),storage:{getItem(){throw Error('must not read private browser draft');},setItem(...x){writes.push(x);},removeItem(...x){writes.push(x);}}});
  assert.equal(store.recovery(),null);assert.throws(()=>store.stage({}),/Unlock editing/);assert.throws(()=>store.restore(JSON.parse(pending)),/Unlock editing/);store.discard();await assert.rejects(store.save({}),/Unlock editing/);
  assert.deepEqual(writes,[]);assert.deepEqual(requests,[]);
});
test('capability check precedes canonical data loads and fails closed',()=>{
  const boot=html.slice(html.indexOf('  /* ---------- boot ---------- */'));
  assert.ok(boot.indexOf("fetch('/api/access'")<boot.indexOf('catalogStore.load()'));
  assert.match(boot,/canEdit=access.canEdit===true/);assert.match(boot,/catch\(function\(\)\{return \{canEdit:false\}/);
});


test('enrolled passkey can unlock without a passphrase and reload full state',async()=>{
  const h=accessHarness(false);assert.equal(h.ids['access-passkey'].hidden,false);
  await h.ids['access-passkey'].onclick();
  assert.equal(h.calls[0][0],'/webauthn/login/options');assert.equal(h.calls[1][0],'/webauthn/login/verify');
  assert.deepEqual(JSON.parse(h.calls[1][1].body),{token:'challenge-token',credential:{id:'enrolled-device'}});
  assert.equal(h.reloads(),1);assert.equal(h.ids['access-passkey'].disabled,false);
});
test('failed passkey unlock keeps public state and offers passphrase fallback',async()=>{
  const h=accessHarness(false,{ok:false,status:401});await h.ids['access-passkey'].onclick();
  assert.equal(h.reloads(),0);assert.match(h.ids['access-error'].textContent,/passphrase instead/);assert.equal(h.ids['access-passkey'].disabled,false);
});
