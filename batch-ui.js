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
      for (const file of files)
        entries.push({
          id: d.id(),
          file,
          fields: {},
          state: "Ready",
          group: "",
          versionId: d.id(),
        });
    }
    async function analyze(onChange, allowance = Infinity) {
      editable();
      const pending = entries.filter((e) =>
        ["Ready", "Failed", "Interrupted"].includes(e.state),
      ).slice(0, allowance);
      const active = new AbortController();
      scan = active;
      const worker = async () => {
        while (pending.length && !active.signal.aborted) {
          const e = pending.shift();
          e.state = "Scanning";
          onChange();
          try {
            const result = await d.recognize(e, active.signal);
            if (!active.signal.aborted) {
              if (!result?.fields) throw Error("No recognized details");
              e.fields = {
                ...result.fields,
                ...Object.fromEntries(
                  Object.entries(e.fields).filter(([, v]) => v),
                ),
              };
              e.warnings = result.warnings || [];
              e.state = "Scanned";
              e.error = "";
            }
          } catch (err) {
            if (!active.signal.aborted) {
              e.error = err.message;
              e.state = "Failed";
              if (err.status === 429) pending.length = 0;
            }
          }
          if (active.signal.aborted) e.state = "Interrupted";
          if (!closed) onChange();
        }
      };
      try {
        await Promise.all([worker(), worker()]);
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
          }
        }
        d.guard();
        await d.commit(preview().catalog, snapshot);
        return true;
      } finally {
        saving = false;
      }
    }
    return {
      entries,
      add,
      analyze,
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
      if (next.matches("input,select")) {
        const value = next.value;
        if (next.tagName === "SELECT" && live.innerHTML !== next.innerHTML)
          live.replaceChildren(...next.children);
        if (live.value !== value && live !== document.activeElement)
          live.value = value;
        live.onchange = next.onchange;
      } else if (next.tagName === "BUTTON") {
        live.onclick = next.onclick;
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
      i.onchange = (event) => {
        change(event.currentTarget.value);
        render();
      };
      return i;
    }
    const header = node("header", null, dialog);
    node("h2", "Import card images", header);
    node(
      "p",
      "Select or drop PNG, JPEG or still WebP images. Check that paired JP and EN artwork matches.",
      header,
    );
    const controls = node("fieldset", null, header),
      files = node("input", null, controls);
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
        usage.textContent =
          u.remaining +
          " of " +
          u.limit +
          " scans remaining for " +
          u.month +
          " UTC." +
          (remaining === 0
            ? " Monthly scan allowance exhausted. Enter the card type, language and card details below, then choose Confirm import."
            : "");
      } catch (e) {
        usage.textContent = e.message;
      }
      if (dialog.open) render();
    }
    const scans = button(controls, "Analyze", async () => {
      try {
        await s.analyze(render, remaining);
      } catch (e) {
        error.textContent = e.message;
      } finally {
        if (dialog.open) {
          render();
          refresh();
        }
      }
    });
    const stop = button(header, "Stop scans", () => s.stop());
    node(
      "small",
      " In-flight scans may count. Failed scans retry only when you select Analyze.",
      header,
    );
    const scroll = node("section", null, dialog);
    scroll.className = "batch-scroll";
    const detail = node("fieldset", null, scroll),
      review = node("div", null, scroll),
      footer = node("footer", null, dialog),
      error = node("p", "", footer);
    error.setAttribute("role", "alert");
    const status = node("p", "", footer);
    status.id = "batch-review-status";
    status.setAttribute("role", "status");
    const jump = button(footer, "Review field", () => {});
    jump.hidden = true;
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
        if (await work) dialog.close();
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
      const owner = key
        ? Array.from(detail.children).find((box) => box.dataset.key === key)
        : detail;
      const label =
        owner &&
        Array.from(owner.children).find(
          (child) =>
            child.tagName === "LABEL" &&
            child.dataset.key === labels[problem.field],
        );
      const input = label?.querySelector("input,select");
      if (!input) return;
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", status.id);
      jump.hidden = false;
      jump.textContent =
        "Review " +
        (item?.fields.num || item?.file.name || labels[problem.field]);
      jump.onclick = () => {
        input.focus({ preventScroll: true });
        input.scrollIntoView({ block: "center" });
      };
    }
    if (d.manual)
      button(footer, "Add card manually", () => {
        if (
          !s.saving &&
          (!s.entries.length ||
            d.confirm(
              "Leave image import?",
              "Selected images will be discarded.",
            ))
        ) {
          dialog.close();
          d.manual();
        }
      });
    button(footer, "Cancel", () => {
      if (!s.saving) dialog.close();
    });
    dialog.oncancel = (e) => {
      if (s.saving) e.preventDefault();
    };
    dialog.onclose = () => {
      s.close();
      for (const url of urls.values()) URL.revokeObjectURL(url);
      dialog.remove();
    };
    function add(list) {
      try {
        s.add(Array.from(list));
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
    function render() {
      const template = document.createElement("fieldset");
      review.replaceChildren();
      field(
        template,
        "Binder",
        s.position.binder,
        s.catalog.binders.map((b) => [b.id, b.name]),
        (v) => (s.position.binder = v),
      );
      field(
        template,
        "Starting pocket",
        s.position.startSlot,
        null,
        (v) => (s.position.startSlot = Number(v)),
      );
      for (const e of s.entries) {
        const box = node("fieldset", null, template);
        box.dataset.key = "file-" + e.id;
        node("legend", e.file.name + " · " + e.state, box);
        if (!urls.has(e.id)) urls.set(e.id, URL.createObjectURL(e.file));
        const img = node("img", null, box);
        img.loading = "lazy";
        img.alt = e.file.name;
        img.src = urls.get(e.id);
        if (e.error) node("p", e.error, box);
        for (const msg of e.warnings || []) node("p", msg, box);
        field(
          box,
          "Language",
          e.fields.language,
          ["", "jp", "en"],
          (v) => (e.fields.language = v),
        );
        field(
          box,
          "Card number",
          e.fields.num,
          null,
          (v) => (e.fields.num = v.trim().toUpperCase()),
        );
        field(
          box,
          "Card type",
          e.fields.cardType,
          ["", "Event", "Character", "Leader", "Stage"],
          (v) => (e.fields.cardType = v),
        );
        field(
          box,
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
          field(box, label, e.fields[k], opts, (v) => (e.fields[k] = v));
        button(box, "Remove image", () => {
          s.entries.splice(s.entries.indexOf(e), 1);
          URL.revokeObjectURL(urls.get(e.id));
          urls.delete(e.id);
          render();
        });
      }
      for (const g of s.groups()) {
        const e = s.entries.find((x) => x.id === g.id),
          existing = [...s.catalog.cards, ...(s.catalog.drafts || [])].find(
            (c) => c.num === g.num,
          );
        const box = node("fieldset", null, template);
        box.dataset.key = "group-" + g.id;
        node(
          "legend",
          (g.num || "Unknown card") +
            " · " +
            g.fileIds
              .map((id) => s.entries.find((x) => x.id === id).file.name)
              .join(" + "),
          box,
        );
        for (const issue of g.issues) node("p", issue, box);
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
            e.patch = {};
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
        for (const [k, label, opts] of props) {
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
          field(box, label, value, opts, (v) => (e.patch[k] = v));
        }
        if (existing && action === "update-base")
          node(
            "p",
            "Replace selected base images. Other artwork stays in place.",
            box,
          );
      }
      syncFields(detail, template);
      for (const input of detail.querySelectorAll('[aria-invalid="true"]')) {
        input.removeAttribute("aria-invalid");
        input.removeAttribute("aria-describedby");
      }
      jump.hidden = true;
      let valid = false;
      try {
        const plan = s.preview();
        node("p", summary(plan), review);
        valid = true;
        status.textContent =
          plan.changes.length +
          " reviewed item" +
          (plan.changes.length === 1 ? "" : "s") +
          " ready to import.";
      } catch (e) {
        reviewProblem(e);
      }
      if (s.scanning || s.saving) {
        status.textContent = s.scanning
          ? "Analyzing images…"
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
      stop.disabled = !s.scanning;
      detail.disabled = controls.disabled = s.saving || s.scanning;
      save.disabled = !valid || s.saving || s.scanning;
    }
    render();
    dialog.showModal();
    refresh();
    return s;
  }
  const api = { session, open, syncFields };
  if (typeof module !== "undefined") module.exports = api;
  else root.BatchImporter = api;
})(typeof window === "undefined" ? globalThis : window);
