const test = require("node:test"),
  assert = require("node:assert/strict"),
  W = require("../workflow.js");
test("base independent of parallel ownership and orders", () => {
  const cards = [{ num: "A", alt: ["p1"] }];
  assert.deepEqual(
    W.wishlist(cards, { A: { alt: { p1: "have" } } }).map((x) => x.id),
    ["A:std"],
  );
  assert.equal(W.wishlist(cards, { A: { ordered: true } }).length, 0);
});
test("drafts excluded and upcoming opt in", () => {
  const cards = [
      { num: "A", status: "draft" },
      { num: "B", status: "announced" },
      { num: "C", variants: [{ id: "v_manual", status: "announced" }] },
    ],
    col = { C: { jp: true, alt: { v_manual: "want" } } };
  assert.equal(W.wishlist(cards, col).length, 0);
  assert.equal(W.wishlist(cards, col, true).length, 2);
});
test("stable legacy and custom identity", () => {
  const c = W.card({ num: "X", alt: ["p1"], altsrc: { p1: "Set" } });
  assert.equal(W.card(c).variants[0].id, "p1");
  assert.match(W.id(), /^v_[a-zA-Z0-9_-]+$/);
});
test("parent draft cannot be exposed by released variant", () => {
  assert.deepEqual(
    W.wishlist(
      [
        {
          num: "A",
          status: "draft",
          variants: [{ id: "p1", kind: "parallel", status: "released" }],
        },
      ],
      { A: { alt: { p1: "want" } } },
      true,
    ),
    [],
  );
});
test("product code source inheritance", () => {
  const cards = [
      {
        num: "A",
        set: "OLD",
        variants: [{ id: "p1", kind: "parallel", source: "NEXT" }],
      },
    ],
    col = { A: { jp: true, alt: { p1: "want" } } },
    products = [{ code: "NEXT", status: "announced" }];
  assert.equal(W.wishlist(cards, col, false, products).length, 0);
  assert.equal(W.wishlist(cards, col, true, products)[0].status, "announced");
});
test("legacy manga have preserves missing base and art metadata", () => {
  const cards = [
      {
        num: "A",
        manga: "p2",
        alt: ["p1", "p2"],
        alttype: { p1: "Comic" },
        altdup: { p1: "p2" },
        altstar: ["p1"],
        altsame: ["p1"],
      },
    ],
    col = { A: { manga: true } };
  const items = W.items(cards, col);
  assert.equal(items.find((x) => x.version === "p2").state, "have");
  assert.deepEqual(
    W.wishlist(cards, col).map((x) => x.id),
    ["A:std"],
  );
  const v = W.card(cards[0]).variants[0];
  assert.equal(v.artType, "Comic");
  assert.equal(v.sameAs, "p2");
  assert.equal(v.specialFoil, true);
  assert.equal(v.sameDrawing, true);
});
for (const fixture of require("./wishlist_cases.json")) {
  test("shared fixture: " + fixture.name, () =>
    assert.deepEqual(
      W.wishlist(
        fixture.catalog.cards,
        fixture.collection,
        fixture.upcoming,
        fixture.catalog.products.products,
      ).map((x) => x.id),
      fixture.expected,
    ),
  );
}
test("clearing final parallel removes empty alt map", () => {
  const changed = W.change(
    { A: { alt: { p1: "have" } } },
    [{ num: "A", version: "p1" }],
    "untracked",
  );
  assert.equal(changed.collection.A.alt, undefined);
});
test("Undo preserves subsequent unrelated edits and guards changed versions", () => {
  const result = W.change(
    { A: { jp: true, alt: { p1: "ordered" } }, B: { jp: true } },
    [{ num: "A", version: "p1" }],
    "have",
  );
  result.collection.A.note = "later";
  result.collection.B.en = true;
  let undone = W.undo(result.collection, result.patches);
  assert.equal(undone.A.alt.p1, "ordered");
  assert.equal(undone.A.note, "later");
  assert.equal(undone.B.en, true);
  result.collection.A.alt.p1 = "want";
  undone = W.undo(result.collection, result.patches);
  assert.equal(undone.A.alt.p1, "want");
});
test("identity upsert survives cloned indexes and repeated save attempts", () => {
  let rows = [{ num: "A", name: "old" }];
  const stale = rows[0];
  rows = rows.map((x) => ({ ...x }));
  rows = W.upsert(rows, { ...stale, name: "new" }, "num");
  rows = W.upsert(rows, { num: "A", name: "retry" }, "num");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "retry");
});
test("canonical JPEG image reference and product provenance survive edits", () => {
  assert.equal(
    W.artwork(
      {
        num: "A",
        variants: [{ id: "v_x", image: "card_images_jp/A_v_x.jpg?v=123" }],
      },
      "v_x",
    ),
    "card_images_jp/A_v_x.jpg?v=123",
  );
  const product = W.mergeProduct(
    {
      code: "NEXT",
      status: "announced",
      sourceUrl: "https://example.test",
      releaseDate: "2027-01-01",
    },
    { name: "Updated" },
  );
  assert.equal(product.status, "announced");
  assert.equal(product.releaseDate, "2027-01-01");
  assert.equal(product.sourceUrl, "https://example.test");
});
test("Undo guards base state atomically against later order changes", () => {
  const result = W.change(
    { A: { jp: true } },
    [{ num: "A", version: "std" }],
    "want",
  );
  result.collection.A.ordered = true;
  const undone = W.undo(result.collection, result.patches);
  assert.equal(undone.A.jp, undefined);
  assert.equal(undone.A.ordered, true);
});
test("new inherited card payload has required foil and no literal inherit status", () => {
  const c = W.prepareCard(
    { num: "OP99-001", name: "Upcoming event", set: "OP-99", col: "Red" },
    "inherit",
  );
  assert.equal(c.foil, false);
  assert.equal(c.rar, "C");
  assert.equal(c.slot, null);
  assert.equal(Object.hasOwn(c, "status"), false);
});
test("failed numbered save then number correction replaces editor instance", () => {
  let state = W.stageCard([], [], {
    id: "v_session",
    num: "OP99-001",
    name: "A",
  });
  state = W.stageCard(state.cards, state.drafts, {
    id: "v_session",
    num: "OP99-002",
    name: "B",
  });
  assert.deepEqual(
    state.cards.map((x) => x.num),
    ["OP99-002"],
  );
  assert.equal(state.drafts.length, 0);
});
test("failed unnumbered draft then official number promotes same instance", () => {
  let state = W.stageCard([], [], {
    id: "v_session",
    name: "Draft",
    status: "draft",
  });
  state = W.stageCard(state.cards, state.drafts, {
    id: "v_session",
    num: "OP99-001",
    name: "Announced",
  });
  assert.equal(state.cards.length, 1);
  assert.equal(state.drafts.length, 0);
  assert.throws(
    () =>
      W.stageCard(state.cards, state.drafts, {
        id: "v_other",
        num: "OP99-001",
      }),
    /already exists/,
  );
});
test("released projection separates base target from binder slots and shopping", () => {
  const cards = [
      { num: "A", slot: 1, set: "OLD", alt: ["p1"] },
      { num: "B", slot: 2, set: "OLD" },
      { num: "C", slot: 3, set: "NEXT" },
      { num: "D", slot: 4, status: "draft", set: "OLD" },
      { num: "E", slot: null, set: "OLD" },
    ],
    col = {
      A: { alt: { p1: "have" } },
      B: { ordered: true },
      E: { baseWish: "skip" },
    },
    products = [{ code: "OLD" }, { code: "NEXT", status: "announced" }];
  const p = W.projection(cards, col, products);
  assert.equal(p.total, 3);
  assert.equal(p.baseOwned, 0);
  assert.equal(p.binderTotal, 2);
  assert.equal(p.binderOwned, 1);
  assert.deepEqual(
    p.wantedBase.map((x) => x.num),
    ["A"],
  );
  assert.deepEqual(
    p.missingBase.map((x) => x.num),
    ["A", "B", "E"],
  );
});
test("order note survives receiving and Undo; clearing deletes optional fields", () => {
  const item = { num: "A", version: "std" };
  let col = W.orderNote(
    { A: { ordered: true } },
    item,
    "  Parcel 42   from shop  ",
  );
  assert.equal(col.A.note, "Parcel 42 from shop");
  let result = W.change(col, [item], "have");
  assert.equal(result.collection.A.history.at(-1).note, "Parcel 42 from shop");
  col = W.undo(result.collection, result.patches);
  assert.equal(col.A.note, "Parcel 42 from shop");
  assert.equal(col.A.ordered, true);
  col = W.orderNote(col, item, "   ");
  assert.equal(Object.hasOwn(col.A, "note"), false);
  col = W.orderNote(
    { A: { alt: { p1: "ordered" }, altNotes: { p1: "shop" } } },
    { num: "A", version: "p1" },
    "",
  );
  assert.equal(Object.hasOwn(col.A, "altNotes"), false);
});
test("ordered and skipped bases never imply shopping completion", () => {
  const p = W.projection(
      [
        { num: "A", set: "OLD" },
        { num: "B", set: "OLD" },
      ],
      { A: { ordered: true }, B: { baseWish: "skip" } },
      [],
    ),
    summary = W.shoppingSummary(p);
  assert.equal(summary.complete, false);
  assert.equal(summary.owned, 0);
  assert.equal(summary.total, 2);
  assert.equal(summary.toBuy, 0);
  assert.equal(
    summary.emptyText,
    "No active purchases. Remaining bases are ordered or skipped.",
  );
  const full = W.shoppingSummary(
    W.projection([{ num: "A" }], { A: { jp: true } }, []),
  );
  assert.equal(full.complete, true);
});
