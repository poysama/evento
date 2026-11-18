const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Workflow = require('../workflow.js');
const html = fs.readFileSync(require('node:path').join(__dirname, '../index.html'), 'utf8');
const recognition = html.slice(html.indexOf('    function recognitionEditor(context)'), html.indexOf('    function versionEditor('));
const versions = html.slice(html.indexOf('    function versionEditor('), html.indexOf('    function releaseEditor('));
const cards = html.slice(html.indexOf('    function cardEditor('), html.indexOf('    function placement('));
const uploader = html.slice(html.indexOf('  async function uploadImage('), html.indexOf('  function openCardEditor('));

function harness(existing, uploadError) {
  class Node {
    constructor(tag, cls = '', text = '') { Object.assign(this, {tag, className:cls, text, children:[], files:[], value:'', checked:false, style:{}}); }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    insertBefore(child, before) { this.children = this.children.filter(n => n !== child); child.parentNode = this; this.children.splice(this.children.indexOf(before), 0, child); }
    setAttribute() {}
    set textContent(text) { this.text = text; this.children = []; }
    get textContent() { return this.text + this.children.map(n => n.textContent).join(''); }
    all() { return this.children.flatMap(child => [child, ...child.all()]); }
  }
  let root, action, responseIndex = 0;
  const requests = [], confirmations = [], created = [], revoked = [], listeners = [];
  const ctx = {
    Workflow, canEdit:true, recognitionAvailable:true, mode:'Binder', products:{products:[]},
    cards:existing ? [existing] : [], byNum:existing ? {[existing.num]:existing} : {}, catalog:{drafts:[]}, share:{}, jpArt:{},
    binders:[{id:'main'}], currentBinder:'main', SLOTS:1024, RARS:['C','R'], catalogStore:{pending:null,running:null}, collectionStore:{pending:null},
    activeCardRecord:(rows, record) => rows.find(card => card === record) || null,
    modal() { root = new Node('div'); return root; },
    el:(tag, cls, text) => new Node(tag, cls, text),
    control(parent, label, type, value) { const n = new Node(type); n.label = label; n.value = value == null ? '' : String(value); parent.appendChild(n); return n; },
    choose(parent, label, values, value) { const n = ctx.control(parent, label, 'select', value || values[0]); n.options = values; return n; },
    button(text, fn) { const n = new Node('button', '', text); n.onclick = fn; return n; },
    submit(parent, fn) { action = fn; },
    confirmEdit(...args) { confirmations.push(args); return true; },
    releaseCode:(value, required) => { if (required && !value) throw Error('Release required'); return value; },
    deriveSid:() => 1, setStatus() {}, saveCatalog:async () => { ctx.saves++; }, saves:0,
    editTarget:num => num, versionReference:() => 'std', altState:() => '', setAlt() {}, flush:async () => {},
    edDlg:{addEventListener(name, fn) { listeners.push(fn); },close() { listeners.forEach(fn => fn()); }},
    URL:{createObjectURL(file) { const u = 'blob:' + file.name + ':' + created.length; created.push(u); return u; },revokeObjectURL:url => revoked.push(url)},
    FileReader:function() { this.readAsDataURL = file => { this.result = 'data:' + file.type + ';base64,' + file.data; this.onload(); }; },
    fetch:async (url, options) => {
      const body = JSON.parse(options.body); requests.push({url, body});
      const payload = url === '/api/image' ? {image:body.path + '?v=saved'} : {fields:{num:'P-134',name:responseIndex++ ? 'Card' : null,jpName:'カード',col:'Red',rar:'R',language:responseIndex === 1 ? 'jp' : 'en',cardType:'Event'},warnings:[]};
      return {ok:!(uploadError && url === '/api/image'),json:async () => uploadError && url === '/api/image' ? {error:uploadError} : payload};
    },
  };
  vm.createContext(ctx); vm.runInContext(uploader + recognition + versions + cards, ctx);
  return {ctx, requests, confirmations, created, revoked, root:() => root, action:() => action(), field:label => root.all().find(n => n.label === label), button:text => root.all().find(n => n.tag === 'button' && n.text === text)};
}
const file = (name, type = 'image/webp') => ({name, type, size:100, data:'d2VicC1vcmlnaW5hbA=='});
function fillCard(h) { h.field('Official card number').value = 'P-134'; h.field('Name').value = 'Card'; h.field('Colour').value = 'Red'; h.field('Rarity').value = 'R'; }
function imageFields(h) { return h.root().all().filter(n => n.tag === 'file'); }

test('dual WebP recognition retains originals through editable review and confirmed base save', async () => {
  const h = harness(), jp = file('jp.webp'), en = file('en.webp');
  h.ctx.recognitionEditor({slot:12,binder:'main'});
  const input = imageFields(h)[0]; assert.match(input.accept, /image\/webp/); input.files = [jp,en]; input.onchange();
  assert.equal(h.requests.length,0); await h.button('Analyze images').onclick();
  assert.equal(h.requests.length,2); h.requests.forEach(r => { assert.equal(r.url,'/api/recognize'); assert.equal(r.body.mime,'image/webp'); assert.equal(r.body.data,jp.data); });
  h.button('Continue to card editor').onclick(); fillCard(h); assert.equal(h.confirmations.length,0);
  await h.action();
  const uploads = h.requests.filter(r => r.url === '/api/image'); assert.equal(uploads.length,2);
  assert.match(uploads[0].body.path,/^card_images_jp\/P-134_.*_jp\.webp$/); assert.match(uploads[1].body.path,/^card_images\/P-134_.*_en\.webp$/);
  uploads.forEach(r => assert.equal(r.body.data,jp.data)); assert.equal(h.ctx.saves,1); assert.equal(h.ctx.cards[0].slot,12);
  assert.equal(h.ctx.cards[0].image,uploads[0].body.path+'?v=saved'); assert.equal(h.ctx.cards[0].enImage,uploads[1].body.path+'?v=saved');
  h.ctx.edDlg.close(); h.created.forEach(url => assert.ok(h.revoked.includes(url)));
});
test('manual card upload preserves WebP and mixed English PNG extensions', async () => {
  const h = harness(); h.ctx.cardEditor(null, {initialFiles:{jp:file('jp.webp'),en:file('en.png','image/png')}}); fillCard(h); await h.action();
  assert.match(h.requests[0].body.path,/_jp\.webp$/); assert.match(h.requests[1].body.path,/_en\.png$/); assert.equal(h.ctx.saves,1);
  imageFields(h).forEach(input => assert.match(input.accept,/image\/webp/));
});
test('new AA and Manga version uploads retain both WebP images and preview cleanup', async () => {
  for (const kind of ['Different artwork AA','Manga']) {
    const card={num:'P-134',name:'Card',variants:[]}, h=harness(card);
    h.ctx.versionEditor(card,null,{kind,initialFiles:{jp:file('jp.webp'),en:file('en.webp')}});
    imageFields(h).forEach(input => assert.match(input.accept,/image\/webp/)); await h.action();
    assert.equal(h.ctx.saves,1); assert.equal(card.variants.length,1); assert.match(card.variants[0].image,/_jp\.webp\?v=saved$/); assert.match(card.variants[0].enImage,/_en\.webp\?v=saved$/);
    h.ctx.edDlg.close(); h.created.forEach(url => assert.ok(h.revoked.includes(url)));
  }
});
test('unsupported image types remain blocked before recognition or manual uploads', async () => {
  const h = harness(); h.ctx.recognitionEditor(); const input=imageFields(h)[0]; input.files=[file('vector.svg','image/svg+xml')];input.onchange();await h.button('Analyze images').onclick();assert.equal(h.requests.length,0);
  h.ctx.cardEditor(null,{initialFiles:{jp:file('vector.svg','image/svg+xml')}});fillCard(h);await assert.rejects(h.action(),/Choose PNG/);assert.equal(h.requests.length,0);assert.equal(h.ctx.saves,0);
});

test('still-image guidance is visible and server rejection reaches the form unchanged', async () => {
  const message='Animated WebP is not supported';
  const h=harness(undefined,message);h.ctx.recognitionEditor();assert.match(h.root().textContent,/Animated WebP is not supported/);
  h.ctx.cardEditor(null,{initialFiles:{jp:file('animated.webp')}});fillCard(h);assert.match(h.root().textContent,/Animated WebP is not supported/);await assert.rejects(h.action(),error=>error.message===message);assert.equal(h.ctx.saves,0);
  const version=harness({num:'P-134',name:'Card',variants:[]});version.ctx.versionEditor(version.ctx.cards[0]);assert.match(version.root().textContent,/Animated WebP is not supported/);
});
