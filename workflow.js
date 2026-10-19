(function (root) {
  "use strict";
  function card(raw) {
    var c = JSON.parse(JSON.stringify(raw));
    if (!Array.isArray(c.variants))
      c.variants = (c.alt || []).map(function (id) {
        return {
          id: id,
          kind: c.manga === id ? "manga" : "parallel",
          label: c.manga === id ? "Manga" : "Parallel",
          source: (c.altsrc || {})[id] || "",
          artType: (c.alttype || {})[id] || "",
          sameAs: (c.altdup || {})[id] || "",
          specialFoil: (c.altstar || []).includes(id),
          sameDrawing: (c.altsame || []).includes(id),
        };
      });
    c.variants.forEach((v) => {
      if ((c.altsame || []).includes(v.id)) v.sameDrawing = true;
      if (!v.sameAs && (c.altdup || {})[v.id]) v.sameAs = c.altdup[v.id];
    });
    var identities = {};
    c.variants.forEach((v) => identities[v.id] = artworkId(c, v.id));
    c.variants.forEach((v) => {
      v.artworkId = identities[v.id];
      if (v.artworkId !== v.id) v.kind = "reprint";
    });
    c.alt = alternateVersions(c).map((v) => v.id);
    c.altsrc = {};
    c.variants.forEach((v) => {
      c.altsrc[v.id] = v.source || "";
      if (v.kind === "manga") c.manga = v.id;
    });
    return c;
  }
  function artworkId(c, version) {
    if (!version || version === "std") return "std";
    var original = version, seen = new Set(), rows = new Map((c.variants || []).map(v => [v.id, v]));
    while (rows.has(version) && !seen.has(version)) {
      seen.add(version);
      var v = rows.get(version);
      if (v.sameDrawing || (c.altsame || []).includes(version)) return "std";
      var target = v.sameAs || (c.altdup || {})[version];
      if (!target) return v.kind === "reprint" && v.artworkId === "std" ? "std" : version;
      if (target === "std") return "std";
      version = target;
    }
    return original;
  }
  function alternateVersions(c) {
    if (!Array.isArray(c.variants)) c = card(c);
    return c.variants.filter(v => artworkId(c, v.id) === v.id);
  }
  function printSources(c, artwork = "std") {
    if (!Array.isArray(c.variants)) c = card(c);
    return c.variants.filter(v => artworkId(c, v.id) === artwork);
  }
  function removeVersion(raw, versionId) {
    if (!versionId || versionId === "std")
      throw Error("The base card cannot be removed as a version");
    var c = card(raw);
    if (!c.variants.some(v => v.id === versionId))
      throw Error("Version not found");
    var root = artworkId(c, versionId);
    var removedIds = c.variants
      .filter(v => v.id === versionId || (root === versionId && artworkId(c, v.id) === root))
      .map(v => v.id);
    var removed = new Set(removedIds);
    c.variants = c.variants.filter(v => !removed.has(v.id));
    c.variants.forEach(v => {
      if (removed.has(v.sameAs)) v.sameAs = root;
    });
    c.alt = (c.alt || []).filter(id => !removed.has(id));
    ["altsame", "altstar"].forEach(key => {
      if (Array.isArray(c[key])) c[key] = c[key].filter(id => !removed.has(id));
    });
    ["altsrc", "alttype", "altdup"].forEach(key => {
      if (!c[key] || typeof c[key] !== "object") return;
      Object.keys(c[key]).forEach(id => {
        if (removed.has(id)) delete c[key][id];
        else if (key === "altdup" && removed.has(c[key][id])) {
          if (removed.has(root)) delete c[key][id];
          else c[key][id] = root;
        }
      });
    });
    if (removed.has(c.manga)) delete c.manga;
    return { card: card(c), removedIds: removedIds };
  }
  function effectiveState(c, e, artwork = "std") {
    var states = printSources(c, artwork).map(v => (e.alt || {})[v.id]);
    if (artwork === "std") states.push(e.jp ? "have" : e.ordered ? "ordered" : e.baseWish === "skip" ? "skip" : "want");
    if (e.manga && artwork === c.manga) states.push("have");
    return ["have", "ordered", "want", "skip"].find(s => states.includes(s)) || "untracked";
  }
  function state(collection, num) {
    var e = collection[num];
    return e === true ? { jp: true } : e || {};
  }
  function status(c, v, products) {
    if (c.status === "draft") return "draft";
    return (
      (v && v.status) ||
      c.status ||
      (
        (products || []).find((p) => p.code === ((v && v.source) || c.set)) ||
        {}
      ).status ||
      "released"
    );
  }
  function eligible(s, up) {
    return s === "released" || (up && s === "announced");
  }
  function items(cards, collection, up, products, rawPrints) {
    var out = [];
    cards.forEach((raw) => {
      var c = card(raw),
        e = JSON.parse(JSON.stringify(state(collection, c.num)));
      if (e.manga && c.manga) {
        e.alt = e.alt || {};
        if (!e.alt[c.manga]) e.alt[c.manga] = "have";
      }
      var s = status(c, null, products);
      if (eligible(s, up))
        out.push({
          id: c.num + ":std",
          num: c.num,
          version: "std",
          status: s,
          card: c,
          state: rawPrints
            ? e.jp ? "have" : e.ordered ? "ordered" : e.baseWish === "skip" ? "skip" : "want"
            : effectiveState(c, e, "std"),
        });
      (rawPrints ? c.variants : alternateVersions(c)).forEach((v) => {
        var vs = status(c, v, products);
        if (eligible(vs, up))
          out.push({
            id: c.num + ":" + v.id,
            num: c.num,
            version: v.id,
            status: vs,
            card: c,
            variant: v,
            state: rawPrints ? (e.alt || {})[v.id] || "untracked" : effectiveState(c, e, v.id),
          });
      });
    });
    return out;
  }
  function upsert(rows, record, key) {
    var found = false;
    var result = rows.map(function (row) {
      if (row[key] !== record[key]) return row;
      found = true;
      return record;
    });
    if (!found) result.push(record);
    return result;
  }
  function artwork(card, version) {
    var variant = (card.variants || []).find(function (v) {
      return v.id === version;
    });
    return (
      (variant && variant.image) ||
      (version
        ? "card_images_jp/" + card.num + "_" + version + ".png"
        : card.image || "card_images_jp/" + card.num + ".png")
    );
  }
  function change(collection, selected, status) {
    var next = JSON.parse(JSON.stringify(collection)),
      patches = [];
    selected.forEach(function (item) {
      var entry = next[item.num] === true ? { jp: true } : next[item.num] || {};
      var before = JSON.parse(JSON.stringify(entry));
      var keys =
        item.version === "std" ? ["jp", "ordered", "baseWish"] : ["alt"];
      if (item.version === "std") {
        delete entry.jp;
        delete entry.ordered;
        delete entry.baseWish;
        if (status === "have") entry.jp = true;
        if (status === "ordered") entry.ordered = true;
        if (status === "skip") entry.baseWish = "skip";
      } else {
        entry.alt = entry.alt || {};
        if (status === "untracked") delete entry.alt[item.version];
        else entry.alt[item.version] = status;
        if (!Object.keys(entry.alt).length) delete entry.alt;
      }
      if (item.version === "std") {
        var previous = {},
          current = {};
        keys.forEach(function (key) {
          if (before[key] !== undefined) previous[key] = before[key];
          if (entry[key] !== undefined) current[key] = entry[key];
        });
        patches.push({
          num: item.num,
          key: "base",
          before: previous,
          after: current,
        });
      } else
        patches.push({
          num: item.num,
          key: "alt",
          version: item.version,
          before: (before.alt || {})[item.version],
          after: (entry.alt || {})[item.version],
        });
      if (
        entry.show &&
        entry.show !== "std" &&
        (!entry.alt || entry.alt[entry.show] !== "have")
      )
        delete entry.show;
      var orderNote =
        item.version === "std"
          ? entry.note
          : (entry.altNotes || {})[item.version];
      entry.history = (entry.history || [])
        .concat([
          {
            version: item.version,
            status: status,
            ...(orderNote ? { note: orderNote } : {}),
            at: new Date().toISOString(),
          },
        ])
        .slice(-100);
      next[item.num] = entry;
    });
    return { collection: next, patches: patches };
  }
  function undo(collection, patches) {
    var next = JSON.parse(JSON.stringify(collection));
    patches
      .slice()
      .reverse()
      .forEach(function (patch) {
        var entry = next[patch.num];
        if (!entry || typeof entry !== "object") return;
        if (patch.key === "base") {
          var current = {};
          ["jp", "ordered", "baseWish"].forEach(function (key) {
            if (entry[key] !== undefined) current[key] = entry[key];
          });
          if (JSON.stringify(current) !== JSON.stringify(patch.after)) return;
          ["jp", "ordered", "baseWish"].forEach(function (key) {
            delete entry[key];
            if (patch.before[key] !== undefined) entry[key] = patch.before[key];
          });
          return;
        }
        var target = patch.key === "alt" ? entry.alt || {} : entry,
          key = patch.version || patch.key;
        if (JSON.stringify(target[key]) !== JSON.stringify(patch.after)) return;
        if (patch.before === undefined) delete target[key];
        else target[key] = patch.before;
        if (patch.key === "alt") {
          if (Object.keys(target).length) entry.alt = target;
          else delete entry.alt;
        }
      });
    return next;
  }

  function complete(entry, catalogCard) {
    var e = entry === true ? { jp: true } : entry || {};
    if (e.jp) return true;
    if (!catalogCard) return Object.values(e.alt || {}).includes("have");
    var c = card(catalogCard);
    return c.variants.some(v => (e.alt || {})[v.id] === "have") ||
      !!(e.manga && c.variants.some(v => v.id === c.manga));
  }

  function projection(cards, collection, products) {
    var base = items(cards, collection, false, products).filter(
      function (item) {
        return item.version === "std";
      },
    );
    var placed = base.filter(function (item) {
      return item.card.slot != null;
    });
    return {
      cards: base.map(function (item) {
        return item.card;
      }),
      total: base.length,
      baseOwned: base.filter(function (item) {
        return item.state === "have";
      }).length,
      binderTotal: placed.length,
      binderOwned: placed.filter(function (item) {
        var e = state(collection, item.num);
        return complete(e, item.card);
      }).length,
      ordered: base.filter(function (item) {
        return item.state === "ordered";
      }).length,
      missingBase: base
        .filter(function (item) {
          return item.state !== "have";
        })
        .map(function (item) {
          return item.card;
        }),
      wantedBase: base
        .filter(function (item) {
          return item.state === "want";
        })
        .map(function (item) {
          return item.card;
        }),
    };
  }
  var api = {
    removeVersion: removeVersion,
    artworkId: artworkId,
    alternateVersions: alternateVersions,
    printSources: printSources,
    effectiveState: effectiveState,
    printItems: (cards, col, up, products) => items(cards, col, up, products, true),
    projection: projection,
    shoppingSummary: function (projection) {
      var complete = projection.baseOwned === projection.total;
      return {
        complete: complete,
        owned: projection.baseOwned,
        total: projection.total,
        toBuy: projection.wantedBase.length,
        emptyText: complete
          ? "All released Japanese base prints are owned."
          : "No active purchases. Remaining bases are ordered or skipped.",
      };
    },
    orderNote: function (collection, item, text) {
      var next = JSON.parse(JSON.stringify(collection)),
        entry = next[item.num] || {},
        value = String(text || "")
          .replace(/\s+/g, " ")
          .trim()
          .slice(0, 200);
      if (item.version === "std") {
        if (value) entry.note = value;
        else delete entry.note;
      } else {
        entry.altNotes = entry.altNotes || {};
        if (value) entry.altNotes[item.version] = value;
        else delete entry.altNotes[item.version];
        if (!Object.keys(entry.altNotes).length) delete entry.altNotes;
      }
      if (Object.keys(entry).length) next[item.num] = entry;
      else delete next[item.num];
      return next;
    },
    upsert: upsert,
    artwork: artwork,
    change: change,
    undo: undo,
    mergeProduct: (old, fields) => Object.assign({}, old, fields),
    stageCard: function (rows, drafts, card, originalNumber, originalCard) {
      function sameRecord(row) {
        if (row.id && card.id) return row.id === card.id;
        return (
          row === originalCard &&
          !row.id &&
          row.num === originalNumber
        );
      }
      if (
        card.num &&
        rows.some(function (row) {
          return row.num === card.num && !sameRecord(row);
        })
      )
        throw Error("Card number already exists");
      var next = rows.filter(function (row) {
        return !sameRecord(row);
      });
      var nextDrafts = drafts.filter(function (row) {
        return row.id !== card.id;
      });
      if (card.num && card.status !== "draft") next.push(card);
      else nextDrafts.push(card);
      return { cards: next, drafts: nextDrafts };
    },
    prepareCard: function (fields, status) {
      var result = Object.assign({ slot: null, rar: "C", foil: false }, fields);
      result.foil = !!result.foil;
      if (status === "inherit") delete result.status;
      else result.status = status;
      return result;
    },
    card: card,
    id: () =>
      "v_" +
      (globalThis.crypto && crypto.randomUUID
        ? crypto.randomUUID().replace(/-/g, "")
        : Date.now().toString(36) + Math.random().toString(36).slice(2)),
    items: items,
    wishlist: (cards, col, up, products) =>
      items(cards, col, up, products).filter((x) => x.state === "want"),
    complete: complete,
  };
  if (typeof module !== "undefined") module.exports = api;
  else root.Workflow = api;
})(typeof window === "undefined" ? globalThis : window);
