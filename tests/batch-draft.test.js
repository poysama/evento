const { test } = require("node:test");
const assert = require("node:assert/strict");
const { record, fingerprint } = require("../batch-draft");

test("fingerprints distinguish different content and match renamed copies", async () => {
  const file = (name, bytes) => new File([bytes], name, { type: "image/png" });
  const first = await fingerprint(file("card.png", "same"));
  assert.equal(await fingerprint(file("copy.png", "same")), first);
  assert.notEqual(await fingerprint(file("card.png", "diff")), first);
});

test("draft schema retains review fields, edits, position and uploads only", () => {
  const entry = {
    id: "a", versionId: "v1", file: { name: "card.png", size: 42 }, group: "pair",
    action: "reprint", sameAs: "std", place: true, state: "Scanning", token: "secret",
    fields: { num: "P-001", raw: "secret" }, recognized: { num: "P-001" },
    edits: { name: "" }, patch: { releaseDate: "", source: "Set", raw: "secret" }
  };
  const draft = record([entry], { binder: "main", startSlot: 6, key: "secret" },
    new Map([["image.png", "image.png?v=1"]]));
  assert.doesNotMatch(JSON.stringify(draft), /secret/);
  assert.equal(draft.entries[0].state, "Interrupted");
  assert.deepEqual(draft.entries[0].edits, { name: "" });
  assert.deepEqual(draft.entries[0].patch, { releaseDate: "", source: "Set" });
  assert.deepEqual(draft.position, { binder: "main", startSlot: 6 });
  assert.deepEqual(draft.uploaded, [["image.png", "image.png?v=1"]]);
  const fullSize = { ...entry, file: { ...entry.file, size: 3_500_000 } };
  assert.equal(record(Array(80).fill(fullSize), {}, []).entries.length, 80);
  assert.throws(() => record(Array(81).fill(entry), {}, []), /80 images/);
});

test("recognition requires a browser scan lock even with no active writer", () => {
  const {create} = require("../batch-draft");
  const original = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  let calls = 0;
  try {
    Object.defineProperty(globalThis, "navigator", {configurable:true, value:{}});
    assert.throws(() => create().withScanLock(() => calls++), /HTTPS or localhost.*manually/);
    assert.equal(calls, 0);
  } finally {
    if (original) Object.defineProperty(globalThis, "navigator", original);
    else delete globalThis.navigator;
  }
});
