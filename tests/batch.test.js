const { test } = require("node:test");
const assert = require("node:assert/strict");
const B = require("../batch");
const catalog = {
  binders: [{ id: "main" }, { id: "other" }],
  cards: [
    { num: "P-001", slot: 17 },
    { num: "P-002", slot: 15 },
    { num: "P-003", slot: 16, binder: "other" },
  ],
};
const row = (n) => ({
  id: n,
  num: "OP18-" + n,
  action: "new-base",
  fields: { name: n, col: "Red", rar: "C" },
});
test("natural order fills forward across occupied page boundaries", () => {
  const { placements, catalog: next } = B.planImport(
    catalog,
    ["010", "002", "001"].map(row),
    { binder: "main", startSlot: 15 },
  );
  assert.deepEqual(
    placements.map((p) => [p.num, p.slot]),
    [
      ["OP18-001", 16],
      ["OP18-002", 18],
      ["OP18-010", 19],
    ],
  );
  assert.deepEqual(next.cards.slice(0, 3), catalog.cards);
  assert.throws(
    () =>
      B.planImport(catalog, ["010", "002"].map(row), {
        binder: "main",
        startSlot: 1024,
      }),
    /Insufficient/,
  );
});
test("one JP and EN pair, ambiguous artwork retains each file", () => {
  const e = (id, language) => ({ id, fields: { num: "P-001", language } });
  const pair = [e("a", "en"), e("b", "jp")];
  assert.deepEqual(B.groupRecognitions(pair)[0].images, { en: "a", jp: "b" });
  const groups = B.groupRecognitions([...pair, e("c", "jp")]);
  assert.deepEqual(
    groups.map((g) => g.id),
    ["a", "b", "c"],
  );
  assert.ok(groups.every((g) => g.ambiguous));
});
test("swaps only slot values and binder navigation uses global indices", () => {
  const args = { binder: "main", page: 1, fromSlot: 15, toSlot: 2 };
  const next = B.swapPageSlots(catalog, args);
  assert.equal(next.cards[1].slot, 2);
  assert.equal(catalog.cards[1].slot, 15);
  assert.deepEqual(B.binderIndices(next.cards, "main"), [1, 0]);
  assert.throws(
    () => B.swapPageSlots(next, { ...args, toSlot: 17 }),
    /this page/,
  );
});
test("occupied swaps, no-op and invalid captured page or binder", () => {
  const input = {
    ...catalog,
    cards: [
      { num: "P-001", slot: 1 },
      { num: "P-002", slot: 2 },
    ],
  };
  const change = { binder: "main", page: 1, fromSlot: 1, toSlot: 2 };
  assert.deepEqual(
    B.swapPageSlots(input, change).cards.map((c) => c.slot),
    [2, 1],
  );
  assert.deepEqual(B.swapPageSlots(input, { ...change, toSlot: 1 }), input);
  for (const bad of [
    { binder: "missing" },
    { fromSlot: 3 },
    { toSlot: 1025 },
    { page: 2 },
  ])
    assert.throws(() => B.swapPageSlots(input, { ...change, ...bad }));
});
test("duplicate bases and occupied slots fail, unplaced existing base requires opt in", () => {
  const args = { binder: "main", startSlot: 15 };
  assert.throws(
    () =>
      B.planImport(
        catalog,
        [row("001"), { ...row("001"), id: "different" }],
        args,
      ),
    /one base/,
  );
  assert.throws(
    () =>
      B.planImport(
        { ...catalog, cards: [...catalog.cards, { num: "P-004", slot: 15 }] },
        [],
        args,
      ),
    /Duplicate binder slot/,
  );
  const input = {
    ...catalog,
    cards: [
      {
        ...row("001").fields,
        num: "OP18-001",
        set: "OP-18",
        slot: null,
        noen: true,
      },
    ],
  };
  const update = {
    id: "u",
    num: "OP18-001",
    action: "update-base",
    enImage: "card_images/en.png",
    fields: { rar: "R" },
  };
  const plan = B.planImport(input, [update], args);
  assert.equal(plan.catalog.cards[0].slot, null);
  assert.equal(plan.catalog.cards[0].noen, undefined);
  assert.equal(
    B.planImport(input, [{ ...update, place: true }], args).catalog.cards[0]
      .slot,
    15,
  );
});
test("reviewed release fields survive, reprints must reference actual artwork, versions allocate no pockets", () => {
  const args = { binder: "main", startSlot: 1 },
    base = row("001");
  const art = {
    id: "a",
    versionId: "v_a",
    num: base.num,
    action: "new-version",
    kind: "parallel",
    fields: {
      finish: "Gold",
      status: "released",
      releaseDate: "2024-02-29",
      sourceUrl: "https://example.org/card",
    },
  };
  const print = {
    ...art,
    id: "b",
    versionId: "v_b",
    kind: "reprint",
    sameAs: "v_a",
  };
  const plan = B.planImport(catalog, [print, art, base], args);
  const variants = plan.catalog.cards.at(-1).variants;
  assert.equal(plan.placements.length, 1);
  for (const [key, value] of Object.entries(art.fields))
    assert.equal(variants[0][key], value);
  assert.throws(
    () =>
      B.planImport(
        catalog,
        [
          base,
          art,
          print,
          { ...print, id: "c", versionId: "v_c", sameAs: "v_b" },
        ],
        args,
      ),
    /matching artwork/,
  );
  for (const fields of [
    { releaseDate: "2026-02-30" },
    { releaseDate: "0000-01-01" },
    { sourceUrl: "invalid" },
    { rar: "arbitrary" },
  ])
    assert.throws(() =>
      B.planImport(
        catalog,
        [{ ...base, fields: { ...base.fields, ...fields } }],
        args,
      ),
    );
});
test("unknown number/language and independent EN-only and JP-only entries remain editable", () => {
  const entries = [
    { id: "x", fields: {} },
    { id: "y", fields: { num: "P-001", language: "en" } },
    { id: "z", fields: { num: "P-002", language: "jp" } },
  ];
  const groups = B.groupRecognitions(entries);
  assert.deepEqual(
    groups.map((g) => g.fileIds),
    [["x"], ["y"], ["z"]],
  );
  assert.equal(groups[0].issues.length, 2);
  assert.deepEqual(groups[1].images, { en: "y" });
  assert.deepEqual(groups[2].images, { jp: "z" });
});
test("promoted private draft reserves its saved slot and preserves curated series and name", () => {
  const base = {
    id: "draft",
    num: "P-004",
    name: "English",
    jpName: "日本語",
    sid: 123,
    rar: "R",
    col: "Red",
    set: "OP-04",
    slot: 16,
    status: "draft",
  };
  const c = { ...catalog, share: {}, drafts: [base] },
    args = { binder: "main", startSlot: 15 };
  const promote = {
    id: "promote",
    num: base.num,
    action: "update-base",
    fields: { status: "announced" },
  };
  const plan = B.planImport(
    c,
    [
      promote,
      { ...row("001"), fields: { ...row("001").fields, set: "OP-18" } },
    ],
    args,
  );
  assert.equal(plan.placements[0].slot, 18);
  const stale = { ...c, share: { [base.num]: { jp: "Old", sid: 1, alt: [] } } };
  const restored = B.planImport(stale, [promote], args).catalog.share[base.num];
  assert.deepEqual(restored, { jp: "日本語", sid: 123, alt: [] });
  const patched = B.planImport(
    stale,
    [{ ...promote, fields: { ...promote.fields, jpName: "Edited" } }],
    args,
  );
  assert.equal(patched.catalog.share[base.num].jp, "Edited");
  assert.equal(plan.catalog.share[base.num].sid, 123);
  assert.equal(plan.catalog.share[base.num].jp, "日本語");
  assert.equal(plan.catalog.share["OP18-001"].sid, 550118);
  assert.throws(
    () =>
      B.planImport({ ...c, drafts: [{ ...base, slot: 17 }] }, [promote], args),
    (error) => /saved pocket/.test(error.message) && error.field === null,
  );
  for (const sourceUrl of [
    "HTTP://example.org",
    "https://example.org/" + "x".repeat(2000),
  ])
    assert.throws(
      () =>
        B.planImport(
          c,
          [{ ...promote, fields: { ...promote.fields, sourceUrl } }],
          args,
        ),
      /source URL/,
    );
});
