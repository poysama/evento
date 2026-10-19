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
test('artwork roots exclude base reprints and resolve alias ownership', () => {
 const c={num:'A',alt:['p1','p2','p3'],altsame:['p1'],altdup:{p2:'p1',p3:'p2'}};
 assert.deepEqual(W.card(c).alt, []);
 assert.equal(W.printSources(c).length,3);
 assert.equal(W.items([c],{A:{alt:{p3:'have'}}})[0].state,'have');
 const aa={num:'B',variants:[{id:'p1',kind:'parallel'},{id:'p2',kind:'parallel',sameAs:'p1'}]};
 assert.deepEqual(W.card(aa).alt,['p1']);
 assert.equal(W.wishlist([aa],{B:{jp:true,alt:{p1:'want',p2:'have'}}}).length,0);
});

test("source-only orders do not invent a standard print order", () => {
  const cards = [{ num: "A", variants: [{ id: "p2", kind: "reprint", sameAs: "std" }] }];
  const collection = { A: { alt: { p2: "ordered" } } };
  const prints = W.printItems(cards, collection);
  assert.equal(prints.find(item => item.version === "std").state, "want");
  const orders = prints.filter(item => item.state === "ordered");
  assert.deepEqual(orders.map(item => item.version), ["p2"]);
  const received = W.change(collection, orders, "have").collection;
  assert.equal(received.A.alt.p2, "have");
  assert.equal(received.A.jp, undefined);
  assert.equal(W.printItems(cards, received).filter(item => item.state === "ordered").length, 0);
  assert.equal(W.items(cards, received)[0].state, "have");
});
test("separate duplicate artwork print orders remain distinct", () => {
  const cards = [{ num: "A", variants: [
    { id: "p1", kind: "parallel" },
    { id: "p2", kind: "reprint", sameAs: "p1" },
    { id: "p3", kind: "reprint", sameAs: "p1" },
  ] }];
  const collection = { A: { alt: { p2: "ordered", p3: "ordered" } } };
  const orders = W.printItems(cards, collection).filter(item => item.state === "ordered");
  assert.deepEqual(orders.map(item => item.version), ["p2", "p3"]);
  const received = W.change(collection, [orders[0]], "have").collection;
  assert.deepEqual(W.printItems(cards, received).filter(item => item.state === "ordered").map(item => item.version), ["p3"]);
  assert.equal(received.A.jp, undefined);
});


test("removing an artwork cascades source prints and clears every legacy field", () => {
  const raw = { num: "A", name: "Keep", slot: 42, custom: { keep: true },
    alt: ["p1", "p2", "p3", "p4"], manga: "p1",
    altdup: { p2: "p1", p3: "p2", obsolete: "p1" },
    altsame: ["p4"], altstar: ["p1", "p3", "p4"],
    altsrc: { p1: "ONE", p2: "TWO", p3: "THREE", p4: "FOUR" },
    alttype: { p1: "Manga", p3: "Foil", p4: "Gold" } };
  const before = JSON.stringify(raw);
  const result = W.removeVersion(raw, "p1");
  assert.deepEqual(result.removedIds, ["p1", "p2", "p3"]);
  assert.equal(JSON.stringify(raw), before);
  assert.deepEqual(result.card.variants.map(v => v.id), ["p4"]);
  assert.deepEqual(result.card.alt, []);
  assert.equal(result.card.manga, undefined);
  assert.deepEqual(result.card.altdup, {});
  assert.deepEqual(result.card.altstar, ["p4"]);
  assert.deepEqual(result.card.alttype, { p4: "Gold" });
  assert.deepEqual(result.card.altsrc, { p4: "FOUR" });
  assert.equal(result.card.slot, 42);
  assert.deepEqual(result.card.custom, { keep: true });
  assert.deepEqual(W.card(JSON.parse(JSON.stringify(result.card))), result.card);
});
test("removing a reprint preserves its root and reparents dependent sources", () => {
  const raw = { num: "A", variants: [
    { id: "p1", kind: "parallel" },
    { id: "p2", kind: "reprint", sameAs: "p1" },
    { id: "p3", kind: "reprint", sameAs: "p2" },
    { id: "p4", kind: "reprint", sameAs: "p3" },
  ], altdup: { p3: "p2", p4: "p3" } };
  const result = W.removeVersion(raw, "p2");
  assert.deepEqual(result.removedIds, ["p2"]);
  assert.deepEqual(result.card.variants.map(v => v.id), ["p1", "p3", "p4"]);
  assert.equal(result.card.variants[1].sameAs, "p1");
  assert.equal(result.card.altdup.p3, "p1");
  assert.equal(W.artworkId(result.card, "p4"), "p1");
  assert.deepEqual(W.card(result.card), result.card);
});
test("removing a base-art reprint preserves other base-art prints", () => {
  const raw = { num: "A", alt: ["p1", "p2"], altsame: ["p1"], altdup: { p2: "p1" } };
  const result = W.removeVersion(raw, "p1");
  assert.deepEqual(result.removedIds, ["p1"]);
  assert.equal(W.artworkId(result.card, "p2"), "std");
  assert.equal(result.card.altdup.p2, "std");
  assert.deepEqual(result.card.alt, []);
});
test("version removal rejects base and unknown IDs", () => {
  const c = { num: "A", alt: ["p1"] };
  for (const id of ["std", "", undefined]) assert.throws(() => W.removeVersion(c, id), /base card/);
  assert.throws(() => W.removeVersion(c, "p9"), /not found/);
  const result = W.removeVersion(c, "p1");
  assert.deepEqual(result.card.variants, []);
  assert.deepEqual(W.card(result.card).alt, []);
});


test("removed owned versions do not fill binder slots while raw ownership survives", () => {
  const original = { num: "A", slot: 1, manga: "p1", alt: ["p1", "p2"],
    variants: [{ id: "p1", kind: "manga" }, { id: "p2", kind: "parallel" }] };
  const collection = { A: { alt: { p1: "have" }, manga: true, history: [{ version: "p1", status: "have" }] } };
  const before = JSON.stringify(collection);
  const updated = W.removeVersion(original, "p1").card;
  assert.equal(W.projection([updated], collection).binderOwned, 0);
  assert.equal(W.complete(collection.A, updated), false);
  assert.equal(JSON.stringify(collection), before);
  assert.equal(W.projection([updated], { A: { jp: true, alt: { p1: "have" } } }).binderOwned, 1);
  assert.equal(W.projection([updated], { A: { alt: { p1: "have", p2: "have" } } }).binderOwned, 1);
  assert.equal(W.projection([updated], { A: true }).binderOwned, 1);
});
test("stale legacy Manga marker cannot fill a binder slot", () => {
  const c = { num: "A", slot: 1, manga: "deleted", variants: [] };
  assert.equal(W.projection([c], { A: { manga: true } }).binderOwned, 0);
  assert.equal(W.complete({ manga: true }, c), false);
});

test("numbered private draft retains identity until published and can return to draft", () => {
  const draft = W.prepareCard({ id: "v_p134", num: "P-134", name: "Spoiled event", set: "", col: "Red" }, "draft");
  let state = W.stageCard([], [], draft);
  assert.equal(state.cards.length, 0);
  assert.equal(state.drafts[0].num, "P-134");
  state = W.stageCard(state.cards, state.drafts, { ...draft, name: "Edited draft" });
  assert.equal(state.drafts.length, 1);
  assert.equal(state.drafts[0].id, "v_p134");
  const published = { ...draft, status: "announced", set: "P-2026" };
  state = W.stageCard(state.cards, state.drafts, published);
  state = W.stageCard(state.cards, state.drafts, published, "P-134");
  assert.equal(state.cards.length, 1);
  assert.equal(state.drafts.length, 0);
  assert.throws(() => W.stageCard(state.cards, [], { ...draft, id: "v_other" }), /already exists/);
  state = W.stageCard(state.cards, state.drafts, draft, "P-134");
  assert.equal(state.cards.length, 0);
  assert.equal(state.drafts.length, 1);
});

test("publishing a second numbered private draft cannot replace another identity", () => {
  const a = { id: "v_a", num: "P-134", name: "A", status: "draft" };
  const b = { id: "v_b", num: "P-134", name: "B", status: "draft" };
  let state = W.stageCard([], [], a);
  state = W.stageCard(state.cards, state.drafts, b);
  state = W.stageCard(state.cards, state.drafts, { ...a, status: "announced" }, a.num, a);
  const before = JSON.stringify(state);
  assert.throws(() => W.stageCard(state.cards, state.drafts, { ...b, status: "announced" }, b.num, b), /already exists/);
  assert.equal(JSON.stringify(state), before);
  state = W.stageCard(state.cards, state.drafts, { ...state.cards[0], name: "Edited A" }, a.num);
  assert.equal(state.cards.length, 1);
  assert.equal(state.cards[0].name, "Edited A");
  assert.equal(state.drafts[0].id, "v_b");
});
test("legacy active edits require the actual original record", () => {
  const legacy = { num: "P-134", name: "Legacy" };
  const other = { num: "P-135", name: "Other" };
  const draft = { id: "v_draft", num: "P-134", status: "draft" };
  assert.throws(() => W.stageCard([legacy, other], [draft], { ...draft, status: "announced" }, "P-134", draft), /already exists/);
  const state = W.stageCard([legacy, other], [], { ...legacy, id: "v_edit", name: "Edited" }, "P-134", legacy);
  assert.equal(state.cards.length, 2);
  assert.equal(state.cards.find(c => c.num === "P-134").name, "Edited");
  assert.equal(state.cards.find(c => c.num === "P-135"), other);
});
