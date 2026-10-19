const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const helper = html.slice(html.indexOf('  function errorGuidance('), html.indexOf('  function setStatus('));
const guidance = new Function(helper + ';return errorGuidance;')();
const hosted = {protocol:'https:',hostname:'evento.peonbox.xyz'};
const local = {protocol:'http:',hostname:'127.0.0.1'};

test('validation errors retain their cause without inventing a connection failure', () => {
  const message = guidance('Invalid release code (400)', hosted, false, false);
  assert.equal(message, 'Invalid release code (400)');
  assert.doesNotMatch(message, /server|connection|not saved|python3/);
});
test('unsaved status depends on pending changes', () => {
  assert.match(guidance('Save failed (500)', hosted, true, false), /^Changes are not saved yet\. Save failed \(500\)/);
  assert.doesNotMatch(guidance('Name required', hosted, false, false), /not saved/);
});
test('network recovery respects local versus hosted context', () => {
  assert.match(guidance('Failed to fetch', local, true, false), /python3 server\.py/);
  assert.match(guidance('Failed to fetch', hosted, true, false), /hosted service/);
  assert.doesNotMatch(guidance('Failed to fetch', hosted, true, false), /python3|localhost|terminal/);
  assert.match(guidance('Failed to fetch', {protocol:'file:',hostname:''}, false, true), /^Could not load.*python3 server\.py/);
});
test('HTTP failures get targeted recovery instead of local restart instructions', () => {
  assert.match(guidance('Save failed (403)', hosted, true, false), /Unlock editing/);
  assert.match(guidance('Conflict. Newer server edits were preserved.', hosted, true, false), /Export pending changes/);
  assert.doesNotMatch(guidance('Load failed (500)', local, false, true), /python3/);
});
test('startup preserves the error and no obsolete launcher instructions remain', () => {
  assert.match(html, /grid\.textContent = errorGuidance\(message,location/);
  assert.match(html, /\$\('warn'\)\.textContent=errorGuidance/);
  assert.doesNotMatch(html, /Start Binder\.bat/);
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '../README.md'), 'utf8'), /Start Binder\.bat|```powershell/);
});

test('release shorthand is normalized and invalid published releases fail before saving', () => {
  const body=html.slice(html.indexOf('  function releaseCode('),html.indexOf('  function errorGuidance('));
  const releaseCode=new Function(body+';return releaseCode;')();
  for(const [input,expected] of [[' op12 ','OP-12'],['st01','ST-01'],['PRB02','PRB-02'],['eb04','EB-04'],['P-2207','P-2207']]) assert.equal(releaseCode(input,true),expected);
  assert.equal(releaseCode('',false),'');
  for(const code of ['', 'Romance Dawn', 'P-134-card']) assert.throws(()=>releaseCode(code,true),/Release \/ set/);
  assert.match(html,/set:\s*releaseCode\(set.value,status.value\s*===\s*["']released["']\)/);
});


test('transport errors emitted by RevisionStore receive context-aware recovery', () => {
  const saveSource=fs.readFileSync(path.join(__dirname,'../save.js'),'utf8');
  const message=saveSource.match(/throw Error\("(Cannot connect to the binder server[^"\n]+)"\)/)[1];
  assert.match(guidance(message,local,true,false),/python3 server\.py/);
  assert.match(guidance(message,hosted,true,false),/hosted service/);
  assert.doesNotMatch(guidance(message,hosted,true,false),/python3|localhost|terminal/);
});


test('card editor distinguishes an active record from private drafts sharing its number', () => {
  const body=html.slice(html.indexOf('  function activeCardRecord('),html.indexOf('  function releaseCode('));
  const activeCardRecord=new Function(body+';return activeCardRecord;')();
  const legacy={num:'P-134'}, published={num:'P-135',id:'v_published'};
  const rows=[legacy,published];
  assert.equal(activeCardRecord(rows,legacy),legacy);
  assert.equal(activeCardRecord(rows,{...published}),published);
  assert.equal(activeCardRecord(rows,{num:'P-134',id:'v_draft'}),null);
  assert.equal(activeCardRecord(rows,{num:'P-135',id:'v_otherdraft'}),null);
  assert.equal(activeCardRecord(rows,{num:'P-134'}),null);
  assert.match(html,/Workflow.stageCard\(cards,catalog.drafts\|\|\[\],c,editingActive&&editingActive.num,draft\)/);
  assert.match(html,/if\(editingActive && official!==editingActive.num\)/);
  assert.doesNotMatch(html,/prior.id !== identity && \(!draft \|\| draft.num !== official\)/);
});
