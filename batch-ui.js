(function (root) {
  "use strict";
  const domain =
    typeof module === "undefined" ? root.EventoBatch : require("./batch");
  function session(d, context) {
    d.guard();
    let snapshot = d.snapshot(),
      closed = false,
      saving = false,
      scan = null;
    const entries = [],
      uploaded = new Map();
    const position = { binder: context.binder, startSlot: context.slot || 1 };
    const drafts = d.drafts;
    let pendingDraft, warning = "", timer, completed = false, available = false, writeFailed = false;
    let pendingEdits = 0, writes = 0;
    let conflict, onDraftChange = () => {};
    function warn(error) {
      warning = "Local recovery unavailable. " + error.message;
      onDraftChange();
    }
    const ready = drafts ? drafts.load().then(value => {
      d.guard();
      pendingDraft = value;
      available = true;
    }).catch(warn) : Promise.resolve();
    async function flush() {
      clearTimeout(timer);
      if (!drafts || pendingDraft || completed) return true;
      if (!available) return !entries.length;
      const edits = pendingEdits;
      writes++;
      try {
        d.guard();
        if (entries.length) await drafts.write(entries, position, uploaded);
        else await drafts.clear();
        if (edits === pendingEdits) pendingEdits = 0;
        writeFailed = false;
        if (warning) { warning = ""; onDraftChange(); }
        return true;
      } catch (error) {
        writeFailed = true;
        if (error.name === "DraftConflictError") conflict = error;
        warn(error);
        return false;
      } finally { writes--; }
    }
    function persist() {
      if (!drafts || closed || completed) return;
      pendingEdits++;
      clearTimeout(timer);
      timer = setTimeout(flush, 150);
    }
    async function resume() {
      editable();
      await ready;
      if (!pendingDraft) return;
      const latest = await d.fetchSnapshot();
      editable();
      const saved = pendingDraft;
      if (!saved.entries.every(e => e.file)) throw Error("Saved image files are unavailable.");
      snapshot = latest;
      entries.push(...saved.entries);
      Object.assign(position, saved.position);
      for (const [path, ref] of saved.uploaded) uploaded.set(path, ref);
      pendingDraft = null;
    }
    async function discard() {
      editable();
      await ready;
      editable();
      clearTimeout(timer);
      if (drafts && available) {
        try { await drafts.clear(); } catch (error) { warn(error); throw error; }
      }
      pendingDraft = null;
      entries.length = 0;
      uploaded.clear();
      Object.assign(position, { binder: context.binder, startSlot: context.slot || 1 });
    }
    function editable() {
      d.guard();
      if (closed || saving || scan)
        throw Error("Finish the current operation first");
    }
    function add(files) {
      editable();
      for (const f of files)
        if (
          !["image/jpeg", "image/png", "image/webp"].includes(f.type) ||
          !f.size ||
          f.size > 3500000
        )
          throw Error("Choose PNG, JPEG or still WebP files up to 3.5 MB each");
      function append(file, fingerprint) {
        entries.push({ id: d.id(), file, fingerprint, fields: {}, state: "Ready",
          group: "", versionId: d.id() });
      }
      if (!drafts) { for (const file of files) append(file); return; }
      return (async () => {
        await ready;
        if (pendingDraft) await resume();
        for (const file of files) {
          let key;
          try { key = await drafts.fingerprint(file); } catch (error) { warn(error); }
          editable();
          let matched = key && entries.some(e => e.fingerprint === key);
          if (!matched && file.arrayBuffer) {
            const candidates = entries.filter(e => (!key || !e.fingerprint)
              && e.file.type === file.type && e.file.size === file.size);
            if (candidates.length) {
              const bytes = new Uint8Array(await file.arrayBuffer());
              for (const e of candidates) {
                const saved = new Uint8Array(await e.file.arrayBuffer());
                if (saved.every((value, i) => value === bytes[i])) { matched = true; break; }
              }
            }
          }
          editable();
          if (!matched) append(file, key);
        }
        await flush();
      })();
    }
    async function analyze(onChange, allowance = Infinity, ids) {
      editable();
      const pending = entries.filter(e => ids
        ? ids.includes(e.id)
        : ["Ready", "Failed", "Interrupted"].includes(e.state)
      ).slice(0, allowance);
      const active = new AbortController();
      scan = active;
      const worker = async () => {
        while (pending.length && !active.signal.aborted && !conflict) {
          const e = pending.shift();
          const previous = e.state;
          const group = groups().find(item => item.fileIds.includes(e.id));
          const cardEdits = entries.find(item => item.id === group.id)?.patch;
          e.edits = Object.fromEntries(Object.entries(e.fields)
            .filter(([key, value]) => value !== e.recognized?.[key] ||
              ((key === "col" || key === "rar") && cardEdits?.[key] === value)));
          e.state = "Scanning";
          onChange();
          try {
            const result = await d.recognize(e, active.signal);
            if (!active.signal.aborted) {
              if (!result?.fields) throw Error("No recognized details");
              e.recognized = result.fields;
              e.fields = { ...result.fields, ...e.edits };
              e.warnings = result.warnings || [];
              e.state = "Scanned";
              e.error = "";
            }
          } catch (err) {
            if (!active.signal.aborted) {
              e.error = err.message;
              e.state = previous === "Scanned" ? previous : "Failed";
              if (err.status === 429) pending.length = 0;
            }
          }
          if (active.signal.aborted) e.state = previous === "Scanned" ? previous : "Interrupted";
          if (!closed) await flush();
          if (!closed) onChange();
        }
      };
      async function run() {
        if (drafts && pending.length) {
          await flush();
          if (conflict) throw conflict;
        }
        await Promise.all([worker(), worker()]);
        if (conflict) throw conflict;
      }
      try {
        if (drafts?.withScanLock) await drafts.withScanLock(run);
        else await run();
      } finally {
        scan = null;
      }
    }
    function groups() {
      const partitions = new Map();
      for (const e of entries) {
        const key = e.group.trim();
        if (!partitions.has(key)) partitions.set(key, []);
        partitions.get(key).push(e);
      }
      return [...partitions.values()].flatMap(domain.groupRecognitions);
    }
    function correctMetadata(group, key, value) {
      for (const entry of entries.filter(item => group.fileIds.includes(item.id))) {
        entry.fields[key] = value;
        if (entry.patch) delete entry.patch[key];
      }
      const owner = entries.find(item => item.id === group.id);
      (owner.patch ||= {})[key] = value;
    }
    function rows() {
      if (!entries.length) throw Error("Select at least one image");
      return groups().map((g) => {
        const e = entries.find((x) => x.id === g.id),
          existing = [
            ...snapshot.catalog.cards,
            ...(snapshot.catalog.drafts || []),
          ].find((c) => c.num === g.num);
        const members = g.fileIds.map((id) =>
          entries.find((item) => item.id === id),
        );
        const fileError = (file, field, message) =>
          Object.assign(Error(message), { fileId: file.id, field });
        const unconfirmed = members.find(
          (item) => item.fields.cardType !== "Event",
        );
        if (unconfirmed)
          throw fileError(
            unconfirmed,
            "cardType",
            "Confirm this image shows an Event card",
          );
        const unknownLanguage = members.find(
          (item) => !["jp", "en"].includes(item.fields.language),
        );
        if (unknownLanguage)
          throw fileError(
            unknownLanguage,
            "language",
            "Choose a language for this image",
          );
        const conflict = g.issues.find((msg) =>
          msg.startsWith("Check conflicting "),
        );
        if (conflict) {
          const field = conflict.slice("Check conflicting ".length);
          if (field === "col" || field === "rar")
            throw Object.assign(Error("Choose the " + (field === "col" ? "colour" : "rarity") +
              " for both images in Card details"), { rowId: g.id, field });
          const different = members.find(
            (item) =>
              item.fields[field] && item.fields[field] !== g.fields[field],
          );
          throw fileError(
            different || e,
            field,
            conflict + " on the paired images",
          );
        }
        const action =
          e.action || (!existing && !g.ambiguous ? "new-base" : "");
        if (!action)
          throw Object.assign(
            Error("Choose an action for " + (g.num || "each artwork")),
            { rowId: g.id, field: "action" },
          );
        const row = {
          id: e.id,
          num: g.num,
          action: ["parallel", "manga", "reprint"].includes(action)
            ? "new-version"
            : action,
          kind: action,
          versionId: e.versionId,
          sameAs: e.sameAs || "std",
          place: e.place === true,
          fields:
            action === "new-base"
              ? { ...g.fields, ...e.patch }
              : { ...e.patch },
        };
        for (const [lang, id] of Object.entries(g.images)) {
          const item = entries.find((x) => x.id === id),
            ext = {
              "image/png": ".png",
              "image/webp": ".webp",
              "image/jpeg": ".jpg",
            }[item.file.type];
          const path =
            (lang === "jp" ? "card_images_jp/" : "card_images/") +
            g.num +
            "_" +
            id +
            "_" +
            lang +
            ext;
          row[lang === "jp" ? "image" : "enImage"] = uploaded.get(path) || path;
        }
        return row;
      });
    }
    const preview = () => domain.planImport(snapshot.catalog, rows(), position);
    async function save(confirm) {
      editable();
      preview();
      saving = true;
      try {
        const latest = await d.fetchSnapshot();
        if (latest.etag !== snapshot.etag) {
          snapshot = latest;
          throw Error(
            "Catalog changed. Review the updated preview and confirm again.",
          );
        }
        const plan = preview();
        if (!confirm(plan)) return false;
        for (const row of rows()) {
          const group = groups().find((g) => g.id === row.id);
          for (const [lang, id] of Object.entries(group.images)) {
            const e = entries.find((x) => x.id === id);
            const path = row[lang === "jp" ? "image" : "enImage"];
            if ([...uploaded.values()].includes(path)) continue;
            d.guard();
            const ref = await d.upload(e.file, path);
            if (typeof ref !== "string" || !ref)
              throw Error("Missing uploaded image reference");
            uploaded.set(path, ref);
            await flush();
          }
        }
        d.guard();
        await d.commit(preview().catalog, snapshot);
        completed = true;
        clearTimeout(timer);
        if (available) {
          try { await drafts.clear(); } catch (error) { warn(error); }
        }
        return true;
      } finally {
        saving = false;
      }
    }
    return {
      entries,
      ready, resume, discard, persist, flush,
      get warning() { return warning; },
      get unsaved() { return !!(drafts && entries.length && !completed && (!available || writeFailed || pendingEdits || writes)); },
      get hasDraft() { return !!pendingDraft; },
      set onDraftChange(callback) { onDraftChange = callback; },
      add,
      analyze,
      correctMetadata,
      groups,
      preview,
      save,
      position,
      get catalog() {
        return snapshot.catalog;
      },
      get saving() {
        return saving;
      },
      get scanning() {
        return !!scan;
      },
      stop() {
        scan?.abort();
      },
      close() {
        if (saving) return false;
        void flush();
        closed = true;
        scan?.abort();
        return true;
      },
    };
  }
  function syncFields(current, template) {
    const children = Array.from(current.children);
    let cursor = current.firstElementChild;
    for (const next of Array.from(template.children)) {
      const key = next.dataset.key;
      let live = key
        ? children.find(
            (child) =>
              child.dataset.key === key && child.tagName === next.tagName,
          )
        : cursor;
      if (
        !live ||
        live.tagName !== next.tagName ||
        (!key && live.dataset.key)
      ) {
        current.insertBefore(next, cursor);
        continue;
      }
      if (live !== cursor) current.insertBefore(live, cursor);
      cursor = live.nextElementSibling;
      if (next.attributes) {
        for (const attr of next.attributes) {
          if (attr.name !== "open" && live.getAttribute(attr.name) !== attr.value)
            live.setAttribute(attr.name, attr.value);
        }
      }
      if (next.matches("input,select")) {
        const value = next.value;
        if (next.tagName === "SELECT" && live.innerHTML !== next.innerHTML)
          live.replaceChildren(...next.children);
        if (live.value !== value && live !== document.activeElement)
          live.value = value;
        live.onchange = next.onchange;
        live.oninput = next.oninput;
      } else if (next.tagName === "BUTTON") {
        live.disabled = next.disabled;
        live.onclick = next.onclick;
        if (next.children.length) syncFields(live, next);
        else live.textContent = next.textContent;
      } else if (next.children.length) {
        syncFields(live, next);
      } else if (next.tagName === "IMG") {
        if (live.src !== next.src) live.src = next.src;
        live.alt = next.alt;
      } else if (live.textContent !== next.textContent)
        live.textContent = next.textContent;
    }
    while (cursor) {
      const rest = cursor.nextElementSibling;
      cursor.remove();
      cursor = rest;
    }
  }
  function open(d, context) {
    const s = session(d, context),
      dialog = document.createElement("dialog");
    dialog.className = "batch-import";
    dialog.setAttribute("aria-label", "Import card images");
    document.body.append(dialog);
    const urls = new Map();
    let selected = "", problem = null;
    function artwork(entry, parent) {
      if (!urls.has(entry.id)) urls.set(entry.id, URL.createObjectURL(entry.file));
      const img = node("img", null, parent);
      img.alt = entry.file.name;
      img.src = urls.get(entry.id);
    }
    function disclosure(parent, title) {
      const details = node("details", null, parent);
      details.dataset.key = title;
      node("summary", title, details);
      return details;
    }
    function node(tag, text, parent) {
      const n = document.createElement(tag);
      if (text != null) n.textContent = text;
      if (parent) parent.append(n);
      return n;
    }
    function button(parent, label, fn) {
      const b = node("button", label, parent);
      b.type = "button";
      b.className = "btn";
      b.onclick = fn;
      return b;
    }
    function field(parent, label, value, options, change) {
      const l = node("label", label + " ", parent),
        i = node(options ? "select" : "input", null, l);
      l.dataset.key = label;
      if (options)
        for (const item of options) {
          const [v, t] = Array.isArray(item) ? item : [item, item];
          i.add(new Option(t, v));
        }
      i.value = value ?? "";
      function update(event) {
        change(event.currentTarget.value);
        s.persist();
      }
      if (!options) i.oninput = update;
      i.onchange = (event) => {
        update(event);
        render();
      };
      return i;
    }
    const header = node("header", null, dialog);
    node("h2", "Import card images", header);
    const close = button(header, "×", () => leave());
    close.setAttribute("aria-label", "Close import");
    node(
      "p",
      "Drop JPEG, PNG or WebP images to review their artwork and card details.",
      header,
    );
    const controls = node("fieldset", null, header),
      files = node("input", null, controls);
    button(controls, "Add images", () => files.click());
    files.hidden = true;
    files.type = "file";
    files.multiple = true;
    files.accept = "image/png,image/jpeg,image/webp";
    files.setAttribute("aria-label", "Select card images");
    if (!d.available)
      node(
        "p",
        "Configure a DeepSeek API token in Settings to analyze images, or enter details manually.",
        controls,
      );
    const usage = node("p", "Checking recognition allowance…", controls);
    let remaining = null;
    async function refresh() {
      remaining = null;
      scans.disabled = true;
      try {
        const u = await d.usage();
        remaining = u.remaining;
        usage.textContent = remaining === 0
          ? "All " + u.limit + " monthly scans used. Finish your review by editing the card details."
          : u.remaining + " of " + u.limit + " scans remaining for " + u.month + " UTC.";
      } catch (e) {
        usage.textContent = e.message;
      }
      if (dialog.open) render();
    }
    async function analyze(ids) {
      try {
        await s.analyze(render, remaining, ids);
      } catch (e) {
        error.textContent = e.message;
      } finally {
        if (dialog.open) {
          render();
          refresh();
        }
      }
    }
    const scans = button(controls, "Analyze", () => analyze());
    const stop = button(controls, "Stop scans", () => s.stop());

    const scroll = node("section", null, dialog);
    scroll.className = "batch-scroll";
    const queuePanel = node("aside", null, scroll),
      queueTitle = node("h3", "Review queue", queuePanel),
      queue = node("nav", null, queuePanel),
      detail = node("fieldset", null, scroll),
      footer = node("footer", null, dialog),
      error = node("p", "", footer);
    error.setAttribute("role", "alert");
    const status = node("p", "", footer);
    status.id = "batch-review-status";
    status.setAttribute("role", "status");
    const jump = button(footer, "Review field", () => {});
    jump.hidden = true;
    queue.setAttribute("aria-label", "Cards in import");
    const save = button(footer, "Confirm import", async () => {
      let work;
      try {
        work = s.save((plan) =>
          d.confirm(
            "Import " + plan.changes.length + " reviewed items?",
            summary(plan),
          ),
        );
        render();
        if (await work) {
          if (s.warning) d.confirm("Import saved", s.warning);
          dialog.close();
        }
      } catch (e) {
        error.textContent = e.message;
      } finally {
        if (dialog.open) render();
      }
    });
    save.setAttribute("aria-describedby", status.id);
    function reviewProblem(problem) {
      const item = s.entries.find(
        (e) => e.id === (problem.fileId || problem.rowId),
      );
      const identity = item
        ? [item.fields.num, item.file.name].filter(Boolean).join(" · ")
        : "";
      status.textContent = identity
        ? identity + " · " + problem.message
        : problem.message;
      const isFile = !!problem.fileId || problem.field === "num";
      const labels = isFile
        ? {
            num: "Card number",
            language: "Language",
            cardType: "Card type",
            name: "Printed English name",
            jpName: "Printed Japanese name",
            col: "Recognized colour",
            rar: "Recognized rarity",
          }
        : {
            action: "Action",
            name: "Display name",
            col: "Colour",
            rar: "Rarity",
            status: "Release status",
            releaseDate: "Release date",
            sourceUrl: "Source URL",
            set: "Set code",
            sameAs: "Same artwork as",
            startSlot: "Starting pocket",
          };
      const key = item ? (isFile ? "file-" : "group-") + item.id : "";
      function target() {
        const owner = Array.from(detail.querySelectorAll("[data-key]")).find(el => el.dataset.key === key) || detail;
        return Array.from(owner.querySelectorAll("label")).find(el => el.dataset.key === labels[problem.field])?.querySelector("input,select");
      }
      const input = target();
      if (input) {
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("aria-describedby", status.id);
      }
      if (!labels[problem.field]) return;
      jump.hidden = false;
      jump.textContent = "Review " + (item?.fields.num || item?.file.name || labels[problem.field]);
      jump.onclick = () => {
        selected = item?.id || selected;
        render();
        const input = target();
        if (!input) return;
        for (let el = input.parentElement; el && el !== detail; el = el.parentElement)
          if (el.tagName === "DETAILS") el.open = true;
        input.focus({ preventScroll: true });
        input.scrollIntoView({ block: "nearest" });
      };
    }
    const draft = node("section", null, header);
    draft.className = "batch-draft";
    node("strong", "Unfinished import on this device", draft);
    const recoveryMessage = node("p", "", draft);
    recoveryMessage.setAttribute("role", "alert");
    const resume = button(draft, "Resume import", async () => {
      resume.disabled = true;
      try {
        await s.resume();
        recoveryMessage.textContent = "";
        render();
      } catch (e) {
        recoveryMessage.textContent = e.message;
      } finally { resume.disabled = false; }
    });
    function discardDraft() {
      if (d.confirm("Discard this import?", "Remove its images and reviewed details from this device.")) {
        return s.discard().then(() => {
          for (const url of urls.values()) URL.revokeObjectURL(url);
          urls.clear();
          selected = "";
          error.textContent = recoveryMessage.textContent = "";
          render();
        }).catch(e => { (s.hasDraft ? recoveryMessage : error).textContent = e.message; });
      }
    }
    button(draft, "Discard draft", discardDraft);
    const warning = node("p", "", header);
    warning.setAttribute("role", "status");
    async function leave() {
      if (s.saving) return false;
      if (s.entries.length) {
        const stored = await s.flush();
        if (stored === false && !d.confirm("Leave without saving this draft?", s.warning)) return false;
      }
      dialog.close();
      return true;
    }
    const manual = d.manual && button(footer, "Add card manually", async () => {
      if (await leave()) d.manual();
    });
    const discard = button(footer, "Discard draft", discardDraft);
    dialog.oncancel = (e) => {
      e.preventDefault();
      void leave();
    };
    const flushDraft = () => s.flush?.();
    function warnBeforeUnload(event) {
      if (s.unsaved) {
        event.preventDefault();
        event.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", warnBeforeUnload);
    window.addEventListener("pagehide", flushDraft);
    s.onDraftChange = () => { if (dialog.open) render(); };
    dialog.onclose = () => {
      window.removeEventListener("beforeunload", warnBeforeUnload);
      window.removeEventListener("pagehide", flushDraft);
      s.close();
      for (const url of urls.values()) URL.revokeObjectURL(url);
      dialog.remove();
    };
    async function add(list) {
      if (s.hasDraft || loading) return;
      try {
        await s.add(Array.from(list));
        error.textContent = "";
        render();
      } catch (e) {
        error.textContent = e.message;
      }
    }
    files.onchange = () => {
      add(files.files);
      files.value = "";
    };
    dialog.ondragover = (e) => e.preventDefault();
    dialog.ondrop = (e) => {
      e.preventDefault();
      add(e.dataTransfer.files);
    };
    function summary(plan) {
      const actions = {
        "new-base": "Add base",
        "update-base": "Update base",
        parallel: "Add AA",
        manga: "Add Manga",
        reprint: "Add same-art reprint",
      };
      const changes = plan.changes.map(
        (c) =>
          (actions[c.kind] || actions[c.action]) +
          " " +
          c.num +
          " (" +
          c.languages.join(" + ") +
          ")" +
          (c.replacements.length
            ? " · replace " + c.replacements.join(" + ")
            : ""),
      );
      const slots = plan.placements.map(
        (p) =>
          p.num +
          " → page " +
          p.page +
          " R" +
          p.row +
          "C" +
          p.col +
          " (slot " +
          p.slot +
          ")",
      );
      if (plan.skippedSlots.length)
        slots.push("Skipped occupied pockets: " + plan.skippedSlots.join(", "));
      return [...changes, ...slots].join(". ");
    }
    let loading = !!s.ready;
    function render() {
      const template = document.createElement("fieldset");
      let plan;
      problem = null;
      try { plan = s.preview(); } catch (e) { problem = e; }
      const groups = s.groups();
      const current = groups.find(g => g.fileIds.includes(selected)) || groups[0];
      if (current && !current.fileIds.includes(selected)) selected = current.id;
      const entries = new Map(s.entries.map(e => [e.id, e]));
      const list = node("nav");
      queueTitle.textContent = "Review queue" + (groups.length ? " (" + groups.length + ")" : "");
      for (const group of groups) {
        const entry = entries.get(group.id);
        const row = button(list, "", () => {
          selected = group.id;
          render();
          detail.scrollTop = 0;
        });
        row.dataset.key = group.id;
        row.setAttribute("aria-current", String(current === group));
        const thumbs = node("span", null, row);
        thumbs.className = "batch-thumbs";
        thumbs.setAttribute("aria-hidden", "true");
        for (const id of group.fileIds) artwork(entries.get(id), thumbs);
        const copy = node("span", null, row);
        node("strong", group.num || entry.file.name, copy);
        const needsReview = !plan && (!group.num ||
          group.fileIds.some(id => entries.get(id).fields.cardType !== "Event" ||
            !["jp", "en"].includes(entries.get(id).fields.language)) ||
          group.issues.some(issue => issue.startsWith("Check conflicting ")) ||
          (group.ambiguous && !entry.action) ||
          group.fileIds.includes(problem?.fileId || problem?.rowId));
        const state = group.fileIds.length === 2 ? "Paired artwork" : "Single image";
        const badge = node("small", plan ? "Ready · " + state : needsReview ? "Needs review" : state, copy);
        badge.className = plan ? "batch-ready" : needsReview ? "batch-attention" : "";

      }
      syncFields(queue, list);
      if (!current) {
        const empty = node("div", null, template);
        empty.className = "batch-empty";
        node("h3", "Choose your card images", empty);
        node("p", "Drop a batch here or choose Add images. Japanese and English artwork can share one card.", empty);
      }
      const placement = disclosure(template, "Binder placement");

      field(
        placement,
        "Binder",
        s.position.binder,
        s.catalog.binders.map((b) => [b.id, b.name]),
        (v) => (s.position.binder = v),
      );
      field(
        placement,
        "Starting pocket",
        s.position.startSlot,
        null,
        (v) => (s.position.startSlot = Number(v)),
      );
      if (current) {
        const title = node("h3", current.num || "Unidentified card", template);
        title.className = "batch-card-title";
      }
      const pair = node("div", null, template);
      pair.className = "batch-pair";
      for (const id of (current?.fileIds || []).slice().sort((a,b) => Number(entries.get(b).fields.language === "jp") - Number(entries.get(a).fields.language === "jp"))) {
        const e = entries.get(id);
        const box = node("section", null, pair);
        box.dataset.key = "file-" + e.id;
        const figure = node("figure", null, box);
        artwork(e, figure);
        const caption = node("figcaption", null, figure);
        node("strong", ({jp:"Japanese",en:"English"})[e.fields.language] || "Unassigned language", caption);
        node("small", e.file.name, caption);
        if (e.error) node("p", e.error, box);
        for (const msg of e.warnings || []) node("p", msg, box);
        const correction = disclosure(box, "Image details");
        field(
          correction,
          "Language",
          e.fields.language,
          ["", "jp", "en"],
          (v) => (e.fields.language = v),
        );
        field(
          correction,
          "Card number",
          e.fields.num,
          null,
          (v) => (e.fields.num = v.trim().toUpperCase()),
        );
        field(
          correction,
          "Card type",
          e.fields.cardType,
          ["", "Event", "Character", "Leader", "Stage"],
          (v) => (e.fields.cardType = v),
        );
        field(
          correction,
          "Artwork group (match JP/EN when ambiguous)",
          e.group,
          null,
          (v) => (e.group = v),
        );
        for (const [k, label, opts] of [
          ["name", "Printed English name"],
          ["jpName", "Printed Japanese name"],
          [
            "col",
            "Recognized colour",
            ["", "Red", "Green", "Blue", "Purple", "Black", "Yellow"],
          ],
          ["rar", "Recognized rarity", ["", ...d.rarities]],
        ])
          field(correction, label, e.fields[k], opts, (v) => (e.fields[k] = v));
        if (e.state === "Scanned") {
          const retry = button(correction, "Reanalyze image", () => analyze([e.id]));
          retry.disabled = !d.available || !remaining || s.scanning || s.saving;
          retry.title = remaining === 0 ? "Monthly scan allowance exhausted" : "Uses one scan";
        }
        button(correction, "Remove image", () => {
          s.entries.splice(s.entries.indexOf(e), 1);
          URL.revokeObjectURL(urls.get(e.id));
          urls.delete(e.id);
          s.persist?.();
          render();
        });
      }
      for (const g of current ? [current] : []) {
        const e = s.entries.find((x) => x.id === g.id),
          existing = [...s.catalog.cards, ...(s.catalog.drafts || [])].find(
            (c) => c.num === g.num,
          );
        const box = node("fieldset", null, template);
        box.dataset.key = "group-" + g.id;
        box.className = "batch-metadata";
        node("legend", "Card details", box);
        for (const issue of g.issues) {
          const key = issue === "Check conflicting col" ? "col" : issue === "Check conflicting rar" ? "rar" : null;
          if (!key) { node("p", issue, box); continue; }
          const readings = g.fileIds.map(id => {
            const fields = entries.get(id).fields;
            return fields.language.toUpperCase() + " " + fields[key];
          });
          node("p", (key === "col" ? "Colour" : "Rarity") + " differs: " + readings.join(", "), box);
        }
        const action =
          e.action || (!existing && !g.ambiguous ? "new-base" : "");
        field(
          box,
          "Action",
          action,
          [
            ["", "Choose"],
            ...(!existing
              ? [["new-base", "New base"]]
              : [["update-base", "Update base images"]]),
            ["parallel", "Add AA"],
            ["manga", "Add Manga"],
            ["reprint", "Add same-art reprint"],
          ],
          (v) => {
            e.action = v;
            e.patch = Object.fromEntries(Object.entries(e.patch || {})
              .filter(([key]) => key === "col" || key === "rar"));
          },
        );
        const version = ["parallel", "manga", "reprint"].includes(action);
        if (action === "reprint")
          field(
            box,
            "Same artwork as",
            e.sameAs || "std",
            [
              "std",
              ...d.alts(existing || {}).map((v) => v.id),
              ...s.entries
                .filter(
                  (item) =>
                    item.fields.num === g.num &&
                    ["parallel", "manga"].includes(item.action),
                )
                .map((item) => [item.versionId, item.file.name]),
            ],
            (v) => (e.sameAs = v),
          );
        if (action === "update-base" && existing?.slot == null)
          field(
            box,
            "Place in binder",
            e.place ? "Yes" : "No",
            ["No", "Yes"],
            (v) => (e.place = v === "Yes"),
          );
        e.patch ||= {};
        const props = version
          ? [
              ["label", "Version label"],
              ["source", "Print source"],
              ["finish", "Finish"],
            ]
          : [
              ["name", "Display name"],
              ["jpName", "Japanese name"],
              [
                "col",
                "Colour",
                ["", "Red", "Green", "Blue", "Purple", "Black", "Yellow"],
              ],
              ["rar", "Rarity", ["", ...d.rarities]],
              ["set", "Set code"],
            ];
        if (version) {
          if (g.issues.includes("Check conflicting col"))
            props.push(["col", "Colour", ["", "Red", "Green", "Blue", "Purple", "Black", "Yellow"]]);
          if (g.issues.includes("Check conflicting rar"))
            props.push(["rar", "Rarity", ["", ...d.rarities]]);
        }
        props.push(
          [
            "status",
            "Release status",
            existing?.status === "draft" && !version
              ? ["draft", "announced", "released"]
              : ["announced", "released"],
          ],
          ["releaseDate", "Release date"],
          ["sourceUrl", "Source URL"],
        );
        let advanced;
        for (const [k, label, opts] of props) {
          if (k === "status") advanced = disclosure(box, "Release and source");
          const original =
            existing && !version
              ? k === "jpName"
                ? existing.status === "draft"
                  ? (existing.jpName ?? s.catalog.share?.[g.num]?.jp)
                  : (s.catalog.share?.[g.num]?.jp ?? existing.jpName)
                : existing[k]
              : undefined;
          const value =
            e.patch[k] ??
            original ??
            (k === "status"
              ? existing && !version
                ? "released"
                : "announced"
              : action === "new-base"
                ? g.fields[k]
                : "");
          if (k === "col" || k === "rar") {
            const disputed = g.issues.includes("Check conflicting " + k);
            field(box, label, disputed ? "" : value, opts, v => s.correctMetadata(g, k, v));
          } else field(advanced || box, label, value, opts, (v) => (e.patch[k] = v));
        }
        if (existing && action === "update-base")
          node(
            "p",
            "Replace selected base images. Other artwork stays in place.",
            box,
          );
      }
      if (current) template.append(placement);
      else placement.remove();
      syncFields(detail, template);
      for (const input of detail.querySelectorAll('[aria-invalid="true"]')) {
        input.removeAttribute("aria-invalid");
        input.removeAttribute("aria-describedby");
      }
      jump.hidden = true;
      const valid = !!plan;
      if (plan) {
        status.textContent =
          plan.changes.length +
          " reviewed item" +
          (plan.changes.length === 1 ? "" : "s") +
          " ready to import.";
      } else reviewProblem(problem);
      if (s.scanning || s.saving) {
        status.textContent = s.scanning
          ? "Analyzing images · " + s.entries.filter(e => e.state === "Scanned").length + " of " + s.entries.length + " scanned"
          : "Saving import…";
        jump.hidden = true;
      }
      const pending = s.entries.filter((e) =>
        ["Ready", "Failed", "Interrupted"].includes(e.state),
      ).length;
      const count = Math.min(pending, remaining ?? pending);
      scans.textContent = remaining === 0
        ? "Scan allowance exhausted"
        : "Analyze " + (count < pending ? count + " of " + pending : count) +
          " image" + (pending === 1 ? "" : "s");
      scans.disabled = remaining === null || !count || !d.available;
      stop.hidden = !s.scanning;
      detail.disabled = s.saving || s.scanning;
      controls.disabled = loading || s.hasDraft || s.saving;
      scans.disabled ||= s.scanning;
      controls.querySelector("button").disabled = s.scanning;
      draft.hidden = !s.hasDraft;
      dialog.classList.toggle("batch-recovering", s.hasDraft);
      scroll.hidden = footer.hidden = !!s.hasDraft;
      warning.hidden = !s.warning;
      warning.textContent = s.warning || "";
      discard.hidden = !s.entries.length || !s.discard;
      discard.disabled = s.saving || s.scanning;
      if (manual) manual.hidden = !!s.entries.length;
      close.disabled = s.saving;
      save.disabled = !valid || s.saving || s.scanning;
    }
    render();
    dialog.showModal();
    Promise.resolve(s.ready).finally(() => {
      loading = false;
      if (dialog.open) {
        render();
        if (s.hasDraft) resume.focus();
      }
    });
    refresh();
    return s;
  }
  const api = { session, open, syncFields };
  if (typeof module !== "undefined") module.exports = api;
  else root.BatchImporter = api;
})(typeof window === "undefined" ? globalThis : window);
