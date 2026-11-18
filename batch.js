(function (root) {
  "use strict";
  const workflow =
    typeof module === "undefined" ? root.Workflow : require("./workflow.js");
  const compareCardNumbers = (a, b) =>
    a.localeCompare(b, "en", { numeric: true });
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const validSlot = (slot) =>
    Number.isInteger(slot) && slot >= 1 && slot <= 1024;
  const pageOf = (slot) => Math.ceil(slot / 16);
  function deriveSid(set) {
    if (String(set).indexOf("P-") === 0) return 550901;
    var m = /^([A-Z]{2,3})-(\d{2})$/.exec(set);
    if (m) {
      var b = { ST: 550000, OP: 550100, EB: 550200, PRB: 550300 }[m[1]];
      if (b) return b + +m[2];
    }
    return null;
  }

  function groupRecognitions(entries) {
    const buckets = new Map(),
      groups = [];
    for (const entry of entries) {
      const num = (entry.fields?.num || "").trim().toUpperCase();
      const key = num || Symbol();
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(entry);
    }
    for (const bucket of buckets.values()) {
      const pair =
        bucket.length === 2 &&
        new Set(bucket.map((e) => e.fields?.language)).size === 2 &&
        bucket.every((e) => ["jp", "en"].includes(e.fields?.language));
      const ambiguous = bucket.length > 1 && !pair;
      for (const members of pair ? [bucket] : bucket.map((e) => [e])) {
        const fields = {},
          images = {},
          issues = [];
        for (const e of members) {
          if (e.error) issues.push(e.error);
          for (const [key, value] of Object.entries(e.fields || {})) {
            if (!value || key === "language") continue;
            if (fields[key] && fields[key] !== value)
              issues.push("Check conflicting " + key);
            else fields[key] = value;
          }
          if (["jp", "en"].includes(e.fields?.language))
            images[e.fields.language] = e.id;
          else issues.push("Choose image language");
        }
        if (!fields.num) issues.push("Enter card number");
        if (ambiguous) issues.push("Classify each artwork");
        groups.push({
          id: members[0].id,
          num: (fields.num || "").toUpperCase(),
          fileIds: members.map((e) => e.id),
          images,
          fields,
          issues,
          ambiguous,
        });
      }
    }
    return groups;
  }
  function binderIndices(cards, binder) {
    return cards
      .map((c, i) => i)
      .filter(
        (i) =>
          (cards[i].binder || "main") === binder && validSlot(cards[i].slot),
      )
      .sort((i, j) => cards[i].slot - cards[j].slot);
  }
  function checkBinder(catalog, binder) {
    if (!catalog.binders.some((b) => b.id === binder))
      throw Error("Choose an existing binder");
  }
  function swapPageSlots(catalog, { binder, page, fromSlot, toSlot }) {
    checkBinder(catalog, binder);
    if (![fromSlot, toSlot].every((s) => validSlot(s) && pageOf(s) === page))
      throw Error("Choose a pocket on this page");
    const next = clone(catalog),
      inBinder = next.cards.filter((c) => (c.binder || "main") === binder);
    const src = inBinder.find((c) => c.slot === fromSlot),
      dst = inBinder.find((c) => c.slot === toSlot);
    if (!src) throw Error("Source pocket is empty");
    src.slot = toSlot;
    if (dst) dst.slot = fromSlot;
    return next;
  }
  function planImport(catalog, rows, { binder, startSlot }) {
    checkBinder(catalog, binder);
    if (!validSlot(startSlot))
      throw Error("Choose a starting pocket from 1 to 1024");
    const next = clone(catalog),
      byNum = new Map(),
      occupied = new Set(),
      allocate = [],
      changes = [],
      placements = [],
      skippedSlots = [];
    for (const c of next.cards) {
      if (byNum.has(c.num)) throw Error("Duplicate card number " + c.num);
      byNum.set(c.num, c);
      if ((c.binder || "main") === binder && c.slot != null) {
        if (occupied.has(c.slot))
          throw Error("Duplicate binder slot " + c.slot);
        occupied.add(c.slot);
      }
    }
    const bases = new Set(),
      ids = new Set();
    for (const r of [...rows].sort(
      (a, b) =>
        Number(b.action === "new-base") - Number(a.action === "new-base") ||
        Number(a.kind === "reprint") - Number(b.kind === "reprint") ||
        compareCardNumbers(a.num, b.num),
    )) {
      if (!r.id || ids.has(r.id)) throw Error("Choose a unique import row");
      ids.add(r.id);
      if (!/^(?:[A-Z]{2,3}\d{2}|P)-\d{3}$/.test(r.num))
        throw Error("Enter an official card number");
      let c = byNum.get(r.num);
      const drafts = (next.drafts || []).filter((d) => d.num === r.num);
      if (!c && drafts.length > 1)
        throw Error(
          "Several unfinished cards use " + r.num + ". Choose one in Settings.",
        );
      const draft = !c && drafts[0];
      if (r.action === "update-base" && draft) c = draft;
      const f = r.fields || {},
        images = {},
        replacements = [];
      if (f.status === "draft" && !draft)
        throw Error("Choose an active release status");
      for (const key of ["image", "enImage"])
        if (r[key] != null) {
          if (
            typeof r[key] !== "string" ||
            !/^(?:card_images|card_images_jp)\/[A-Za-z0-9_-]+\.(?:png|jpg|webp)(?:\?v=[A-Za-z0-9_-]+)?$/.test(
              r[key],
            )
          )
            throw Error("Invalid image path");
          images[key] = r[key];
        }
      if (
        f.sourceUrl &&
        (typeof f.sourceUrl !== "string" ||
          f.sourceUrl.length > 2000 ||
          !/^https?:\/\//.test(f.sourceUrl))
      )
        throw Error("Use an HTTP or HTTPS source URL");
      if (f.status && !["draft", "announced", "released"].includes(f.status))
        throw Error("Choose a release status");
      if (
        f.releaseDate &&
        (!/^(?!0000)\d{4}-\d{2}-\d{2}$/.test(f.releaseDate) ||
          !Number.isFinite(Date.parse(f.releaseDate)) ||
          new Date(f.releaseDate).toISOString().slice(0, 10) !== f.releaseDate)
      )
        throw Error("Enter a valid release date");
      if (r.action === "new-version") {
        if (!c) throw Error("Choose an existing base for " + r.num);
        const normalized = workflow.card(c),
          variants = normalized.variants;
        if (
          !/^(?:p\d{1,2}|v_[A-Za-z0-9_-]+)$/.test(r.versionId || "") ||
          r.versionId === "std" ||
          variants.some((v) => v.id === r.versionId)
        )
          throw Error("Choose a unique version ID");
        if (!["parallel", "manga", "reprint"].includes(r.kind))
          throw Error("Choose an artwork type");
        if (
          r.kind === "reprint" &&
          r.sameAs !== "std" &&
          !workflow.alternateVersions(normalized).some((v) => v.id === r.sameAs)
        )
          throw Error("Choose the matching artwork");
        variants.push({
          ...images,
          id: r.versionId,
          kind: r.kind,
          label: f.label || r.kind,
          source: f.source || "",
          status: f.status || "announced",
          finish: f.finish || "",
          releaseDate: f.releaseDate || "",
          sourceUrl: f.sourceUrl || "",
          sameAs: r.kind === "reprint" ? r.sameAs : "",
        });
        Object.assign(c, workflow.card(normalized));
      } else {
        if (bases.has(r.num))
          throw Error("Choose one base action for " + r.num);
        bases.add(r.num);
        if (r.action === "new-base") {
          if (c || next.drafts?.some((d) => d.num === r.num))
            throw Error("Card number already exists: " + r.num);
          c = {
            num: r.num,
            slot: null,
            set: "",
            status: "announced",
            foil: false,
          };
          next.cards.push(c);
          byNum.set(c.num, c);
        } else if (r.action !== "update-base" || !c)
          throw Error("Choose an action for " + r.num);
        for (const key of [
          "name",
          "col",
          "rar",
          "set",
          "sourceUrl",
          "releaseDate",
          "status",
        ])
          if (key in f) c[key] = f[key];
        if (!c.name) c.name = f.jpName;
        if (
          c.status !== "draft" &&
          (!c.name?.trim() ||
            !["Red", "Green", "Blue", "Purple", "Black", "Yellow"].includes(
              c.col,
            ) ||
            !["C", "UC", "R", "SR", "P"].includes(c.rar))
        )
          throw Error("Choose a name, colour and rarity for " + r.num);
        if (
          c.status !== "draft" &&
          !(c.set === "" && c.status === "announced") &&
          !/^(?:[A-Z]{2,3}-\d{2}|P-\d{3,4})$/.test(c.set)
        )
          throw Error("Enter a release set code");
        for (const key of Object.keys(images))
          if (c[key]) replacements.push(key === "image" ? "JP" : "EN");
        Object.assign(c, images);
        if (images.enImage) delete c.noen;
        if (draft && c.status !== "draft") {
          c.foil = !!c.foil;
          if (c.slot != null) {
            const draftBinder = c.binder || "main";
            checkBinder(next, draftBinder);
            if (
              !validSlot(c.slot) ||
              next.cards.some(
                (other) =>
                  (other.binder || "main") === draftBinder &&
                  other.slot === c.slot,
              )
            )
              throw Error(
                "The unfinished card's saved pocket is unavailable. Update its placement in Settings.",
              );
            if (draftBinder === binder) occupied.add(c.slot);
          }
          next.drafts = next.drafts.filter((d) => d !== draft);
          next.cards.push(c);
          byNum.set(c.num, c);
        }
        if (c.status === "draft" && "jpName" in f) c.jpName = f.jpName;
        if (
          c.status !== "draft" &&
          (r.action === "new-base" || (r.place && c.slot == null))
        )
          allocate.push(c);
      }
      if (next.share && c.status !== "draft") {
        const info = (next.share[c.num] ||= {
          jp: draft?.jpName || "",
          sid: draft?.sid ?? deriveSid(c.set) ?? 0,
          alt: (c.alt || []).slice(),
        });
        if (draft) {
          if (typeof draft.jpName === "string") info.jp = draft.jpName;
          if ("sid" in draft) info.sid = draft.sid ?? deriveSid(c.set) ?? 0;
        }
        if (r.action !== "new-version" && "jpName" in f) info.jp = f.jpName;
        if (r.action === "new-version") info.alt = c.alt.slice();
      }
      changes.push({
        id: r.id,
        num: r.num,
        action: r.action,
        kind: r.kind,
        replacements,
        languages: Object.keys(images).map((k) =>
          k === "image" ? "JP" : "EN",
        ),
      });
    }
    let slot = startSlot;
    for (const c of allocate.sort((a, b) => compareCardNumbers(a.num, b.num))) {
      while (occupied.has(slot)) {
        skippedSlots.push(slot);
        slot++;
      }
      if (!validSlot(slot))
        throw Error(
          "Insufficient empty pockets: need " +
            (allocate.length - placements.length) +
            " more after the starting slot",
        );
      c.slot = slot;
      c.binder = binder;
      placements.push({
        num: c.num,
        binder,
        slot,
        page: pageOf(slot),
        row: Math.floor(((slot - 1) % 16) / 4) + 1,
        col: ((slot - 1) % 4) + 1,
      });
      slot++;
    }
    return { catalog: next, placements, changes, skippedSlots };
  }
  const api = {
    compareCardNumbers,
    deriveSid,
    groupRecognitions,
    planImport,
    swapPageSlots,
    binderIndices,
  };
  if (typeof module !== "undefined") module.exports = api;
  else root.EventoBatch = api;
})(typeof window === "undefined" ? globalThis : window);
