const test = require("node:test");
const assert = require("node:assert/strict");
const { session } = require("../batch-ui");
const file = (name = "scan.webp", type = "image/webp") => ({
  name,
  type,
  size: 42,
});
const fields = (num = "P-001", language = "jp") => ({
  num,
  language,
  col: "Red",
  rar: "R",
  cardType: "Event",
  ...(language === "jp" ? { jpName: "赤" } : { name: "Red" }),
});
function setup(options = {}) {
  let serial = 0;
  const initial = {
    etag: "one",
    catalog: {
      schema: 2,
      cards: [],
      drafts: [],
      binders: [{ id: "main", name: "Main" }],
      share: {},
    },
  };
  const uploads = [],
    commits = [];
  const deps = {
    guard() {},
    id: () => "v_" + ++serial,
    snapshot: () => initial,
    fetchSnapshot: async () => initial,
    recognize: async () => ({ fields: fields() }),
    upload: async (f, p) => {
      uploads.push({ f, p });
      return p + "?v=saved";
    },
    commit: async (c, s) => commits.push({ c, s }),
    ...options,
  };
  return {
    s: session(deps, { binder: "main", slot: 16 }),
    deps,
    uploads,
    commits,
    initial,
  };
}
test("mixed formats and repeated filenames keep original files and dual-language references", async () => {
  const h = setup(),
    jp = file(),
    en = file("scan.webp", "image/png");
  h.s.add([jp, en]);
  h.s.entries[0].fields = fields();
  h.s.entries[1].fields = fields("P-001", "en");
  assert.equal(h.s.groups().length, 1);
  assert.equal(h.s.preview().placements[0].slot, 16);
  assert.equal(h.uploads.length, 0);
  assert.equal(await h.s.save(() => false), false);
  assert.deepEqual(h.uploads, []);
  await h.s.save(() => true);
  assert.deepEqual(
    h.uploads.map((u) => u.f),
    [jp, en],
  );
  assert.match(h.commits[0].c.cards[0].image, /\.webp\?v=saved$/);
  assert.match(h.commits[0].c.cards[0].enImage, /\.png\?v=saved$/);
  assert.equal(h.commits[0].c.cards[0].name, "Red");
  assert.equal(JSON.stringify(h.commits[0].c).includes("image/webp"), false);
});
test("invalid selection and unresolved language or non-Event block all writes", async () => {
  const h = setup();
  assert.throws(
    () => h.s.add([file("vector.svg", "image/svg+xml")]),
    /Choose PNG/,
  );
  h.s.add([file()]);
  h.s.entries[0].fields = { ...fields(), cardType: "Stage" };
  await assert.rejects(
    h.s.save(() => true),
    /Event/,
  );
  h.s.entries[0].fields = { ...fields(), language: "" };
  await assert.rejects(
    h.s.save(() => true),
    /language/,
  );
  assert.equal(h.uploads.length, 0);
  h.s.entries[0].fields = fields();
  await h.s.save(() => true);
  assert.equal(h.commits[0].c.cards[0].name, "赤");
});
test("partial upload retries reuse successful reference and only one catalog commit", async () => {
  const h = setup(),
    calls = [];
  let fail = true;
  h.deps.upload = async (f, p) => {
    calls.push(p);
    if (f.name === "en" && fail) throw Error("offline");
    return p + "?v=saved";
  };
  h.s.add([file("jp"), file("en")]);
  h.s.entries[0].fields = fields();
  h.s.entries[1].fields = fields("P-001", "en");
  await assert.rejects(
    h.s.save(() => true),
    /offline/,
  );
  assert.equal(h.commits.length, 0);
  fail = false;
  await h.s.save(() => true);
  assert.equal(h.commits.length, 1);
  assert.equal(calls.length, 3);
  assert.equal(calls[1], calls[2]);
});
test("preflight change needs second confirmation and preserves newer curated metadata", async () => {
  const h = setup();
  const base = { ...fields(), name: "Old", set: "OP-01", slot: 4, foil: false };
  h.initial.catalog.cards.push(base);
  h.s.add([file()]);
  Object.assign(h.s.entries[0], {
    fields: fields(),
    action: "update-base",
    patch: { rar: "SR" },
  });
  const latest = structuredClone(h.initial);
  latest.etag = "two";
  latest.catalog.cards[0].name = "Curated in other tab";
  h.deps.fetchSnapshot = async () => latest;
  await assert.rejects(
    h.s.save(() => true),
    /changed/,
  );
  assert.equal(h.uploads.length, 0);
  await h.s.save(() => true);
  assert.equal(h.commits[0].s.etag, "two");
  assert.equal(h.commits[0].c.cards[0].name, "Curated in other tab");
  assert.equal(h.commits[0].c.cards[0].rar, "SR");
});
test("conflict and pending recovery forbid blind save and preserve upload references", async () => {
  let pending = false;
  const h = setup({
    guard() {
      if (pending) throw Error("Resolve pending");
    },
    commit: async () => {
      pending = true;
      throw Error("Conflict");
    },
  });
  h.s.add([file()]);
  h.s.entries[0].fields = fields();
  await assert.rejects(
    h.s.save(() => true),
    /Conflict/,
  );
  await assert.rejects(
    h.s.save(() => true),
    /pending/,
  );
  assert.equal(h.uploads.length, 1);
  assert.throws(
    () =>
      setup({
        guard() {
          throw Error("Unlock editing");
        },
      }),
    /Unlock editing/,
  );
});
test("cancel aborts two running scans without starting more or reviving closed tray", async () => {
  const started = [],
    release = [];
  const h = setup({
    recognize: (e, signal) =>
      new Promise((resolve) => {
        started.push(signal);
        release.push(resolve);
      }),
  });
  h.s.add([file(), file(), file()]);
  let rendered = 0;
  const work = h.s.analyze(() => rendered++);
  assert.equal(started.length, 2);
  h.s.close();
  release.forEach((r) => r({ fields: fields() }));
  await work;
  assert.ok(started.every((s) => s.aborted));
  assert.equal(started.length, 2);
  assert.equal(rendered, 2);
});
test("exhaustion stops queue, completed results survive explicit retry", async () => {
  let quota = true,
    calls = [];
  const h = setup({
    recognize: async (e) => {
      calls.push(e.id);
      if (quota && e.file.name === "1")
        throw Object.assign(Error("Exhausted"), { status: 429 });
      return { fields: fields() };
    },
  });
  h.s.add([file("1"), file("2"), file("3"), file("4")]);
  await h.s.analyze(() => {});
  assert.ok(calls.length < 4);
  const done = h.s.entries
    .filter((e) => e.state === "Scanned")
    .map((e) => e.id);
  quota = false;
  await h.s.analyze(() => {});
  assert.ok(done.every((id) => calls.filter((x) => x === id).length === 1));
});
test("ambiguous same-number artwork remains independently classifiable and pairable", () => {
  const h = setup();
  h.s.add([file(), file(), file()]);
  h.s.entries.forEach(
    (e, i) => (e.fields = fields("P-001", i === 2 ? "en" : "jp")),
  );
  assert.equal(h.s.groups().length, 3);
  assert.throws(() => h.s.preview(), /Choose an action/);
  h.s.entries[0].group = h.s.entries[2].group = "base";
  h.s.entries[1].action = "manga";
  const plan = h.s.preview();
  assert.equal(plan.catalog.cards.length, 1);
  assert.equal(plan.catalog.cards[0].variants[0].kind, "manga");
  assert.equal(plan.placements.length, 1);
});
test("single numbered private draft stays private and ambiguous drafts require Settings", () => {
  const h = setup();
  h.initial.catalog.drafts.push({
    num: "P-001",
    id: "draft",
    status: "draft",
    name: "Curated",
  });
  h.s.add([file()]);
  Object.assign(h.s.entries[0], { fields: fields(), action: "update-base" });
  assert.equal(h.s.preview().catalog.drafts[0].name, "Curated");
  assert.deepEqual(h.s.preview().placements, []);
  h.initial.catalog.drafts.push({
    ...h.initial.catalog.drafts[0],
    id: "other",
  });
  assert.throws(() => h.s.preview(), (error) => /Several unfinished/.test(error.message) && error.field === null);
});
test("real revision adapters validate detached reads and retain failed candidates for recovery", async () => {
  const fs = require("node:fs"),
    path = require("node:path"),
    RevisionStore = require("../save");
  const html = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8");
  const src = html.slice(
    html.indexOf("  function assertCatalogReady()"),
    html.indexOf("  function saveCatalog()"),
  );
  const data = {
    schema: 2,
    cards: [],
    drafts: [],
    binders: [],
    share: {},
    products: { sources: {}, products: [] },
  };
  const env = {
    canEdit: true,
    catalog: data,
    cards: [],
    products: {},
    share: {},
    binders: [],
    setStatus() {},
    renderBinders() {},
    rebuild() {},
    workflowRedraw() {},
    recoverPending() {},
    fetch: null,
    catalogStore: null,
  };
  const storage = new Map();
  let etag = '"old"',
    body = data,
    status = 200,
    putStatus = 409,
    puts = 0;
  env.fetch = async (url, opts) => {
    if (opts.method === "PUT") {
      puts++;
      assert.equal(opts.headers["If-Match"], etag);
      return {
        ok: putStatus === 200,
        status: putStatus,
        headers: { get: () => '"saved"' },
      };
    }
    return {
      ok: status === 200,
      status,
      headers: { get: () => etag },
      json: async () => body,
    };
  };
  env.catalogStore = new RevisionStore("/api/catalog", {
    fetch: env.fetch,
    storage: {
      getItem: (k) => storage.get(k),
      setItem: (k, v) => storage.set(k, v),
      removeItem: (k) => storage.delete(k),
    },
  });
  env.catalogStore.etag = '"review"';
  const api = new Function(
    "env",
    "with(env){" +
      src +
      ";return {read:fetchCatalogSnapshot,commit:commitCatalogCandidate};}",
  )(env);
  for (const invalid of [
    null,
    { ...data, share: "bad" },
    { ...data, products: {} },
  ]) {
    body = invalid;
    await assert.rejects(api.read(), /Private catalog/);
    assert.equal(env.catalogStore.etag, '"review"');
  }
  body = data;
  etag = '"public-abc"';
  await assert.rejects(api.read(), /Private catalog/);
  etag = '"latest"';
  const snap = await api.read(),
    candidate = { ...data, marker: "candidate" };
  await assert.rejects(api.commit(candidate, snap), /Conflict/);
  assert.equal(puts, 1);
  assert.equal(env.catalog, data);
  assert.deepEqual(env.catalogStore.recovery().data, candidate);
  await assert.rejects(api.commit(candidate, snap), /Resolve pending/);
  assert.equal(puts, 1);
  env.catalogStore.discard();
  putStatus = 200;
  await api.commit(candidate, snap);
  assert.equal(env.catalog, candidate);
  assert.equal(env.catalogStore.recovery(), null);
  assert.equal(puts, 2);
});
test("successful catalog retry adopts its candidate when collection retry fails", async () => {
  const fs = require("node:fs"),
    path = require("node:path");
  const html = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8");
  const source = html.slice(
    html.indexOf('    var retry = button("Retry save"'),
    html.indexOf("    retry.id ="),
  );
  let adopted,
    recovered = false,
    status;
  const candidate = { marker: "saved" };
  const env = {
    button: (label, cb) => cb,
    catalogStore: { pending: candidate, save: async () => {} },
    collectionStore: {
      save: async () => {
        throw Error("collection offline");
      },
    },
    $: () => null,
    adoptCatalogCandidate: (value) => (adopted = value),
    setStatus: (msg) => (status = msg),
    recoverPending: () => (recovered = true),
  };
  const retry = new Function("env", "with(env){" + source + "return retry;}")(
    env,
  );
  await retry();
  assert.equal(adopted, candidate);
  assert.equal(status, "collection offline");
  assert.equal(recovered, true);
});
test("preview reconciliation retains focused controls, live input and tab order", () => {
  class Element {
    constructor(tag, key = "") {
      this.tagName = tag;
      this.dataset = { key };
      this.children = [];
      this.value = "";
      this.textContent = "";
    }
    get firstElementChild() {
      return this.children[0] || null;
    }
    get nextElementSibling() {
      return (
        this.parent?.children[this.parent.children.indexOf(this) + 1] || null
      );
    }
    insertBefore(child, cursor) {
      child.remove();
      const index = cursor
        ? this.children.indexOf(cursor)
        : this.children.length;
      this.children.splice(index, 0, child);
      child.parent = this;
    }
    remove() {
      if (this.parent) {
        const i = this.parent.children.indexOf(this);
        this.parent.children.splice(i, 1);
        this.parent = null;
      }
    }
    matches() {
      return ["INPUT", "SELECT"].includes(this.tagName);
    }
  }
  function form() {
    const section = new Element("FIELDSET");
    for (const field of ["Name", "Japanese name"]) {
      const label = new Element("LABEL", field);
      const input = new Element("INPUT");
      label.insertBefore(input, null);
      section.insertBefore(label, null);
    }
    return section;
  }
  const live = form(),
    updated = form(),
    input = live.children[0].firstElementChild,
    next = live.children[1].firstElementChild;
  const doc = global.document;
  global.document = { activeElement: input };
  try {
    input.value = "unsaved keystrokes";
    updated.children[1].firstElementChild.value = "updated";
    require("../batch-ui").syncFields(live, updated);
    assert.equal(live.children[0].firstElementChild, input);
    assert.equal(input.value, "unsaved keystrokes");
    assert.equal(live.children[1].firstElementChild, next);
    assert.equal(next.value, "updated");
  } finally {
    if (doc === undefined) delete global.document;
    else global.document = doc;
  }
});
test("completed paired batch identifies the specific unresolved file and keeps it editable", () => {
  const { s } = setup();
  s.add([file("first.webp"), file("second.png", "image/png")]);
  const [jp, en] = s.entries;
  jp.fields = fields("OP18-116", "jp");
  en.fields = fields("OP18-116", "en");
  delete en.fields.cardType;
  assert.throws(
    () => s.preview(),
    (e) =>
      e.fileId === en.id &&
      e.field === "cardType" &&
      e.message.includes("Event"),
  );
  en.fields.cardType = "Event";
  en.fields.col = "Blue";
  assert.throws(
    () => s.preview(),
    (e) => e.fileId === en.id && e.field === "col",
  );
  en.fields.col = "Red";
  assert.equal(s.preview().placements.length, 1);
});
test("missing group-level metadata and invalid edited values identify the exact review field", () => {
  const { s } = setup();
  s.add([file()]);
  const [entry] = s.entries;
  entry.fields = fields();
  entry.fields.rar = "";
  assert.throws(
    () => s.preview(),
    (e) => e.rowId === entry.id && e.field === "rar",
  );
  entry.patch = { rar: "R", releaseDate: "2026-02-30" };
  assert.throws(
    () => s.preview(),
    (e) => e.rowId === entry.id && e.field === "releaseDate",
  );
  entry.patch.releaseDate = "";
  assert.equal(s.preview().placements.length, 1);
});
test("only starts as many pending files as the remaining allowance", async () => {
  const h = setup();
  h.s.add([file(), file(), file()]);
  await h.s.analyze(() => {}, 0);
  assert.deepEqual(h.s.entries.map((entry) => entry.state), ["Ready", "Ready", "Ready"]);
  await h.s.analyze(() => {}, 1);
  assert.deepEqual(h.s.entries.map((entry) => entry.state), ["Scanned", "Ready", "Ready"]);
});
