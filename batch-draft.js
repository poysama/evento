(function (root) {
  "use strict";
  const fieldNames = ["language", "num", "cardType", "name", "jpName", "col", "rar"];
  const patchNames = ["label", "source", "finish", "name", "jpName", "col", "rar", "set", "status", "releaseDate", "sourceUrl"];
  const pick = (value, keys) => Object.fromEntries(keys.filter(k =>
    typeof value?.[k] === "string" || value?.[k] === null).map(k => [k, value[k]]));
  function record(entries, position, uploaded) {
    if (entries.length > 80 || entries.reduce((sum, e) => sum + e.file.size, 0) > 280_000_000)
      throw Error("Local recovery supports up to 80 images and 280 MB per batch.");
    return {
      entries: entries.map(e => ({
        ...pick(e, ["id", "versionId", "fingerprint", "group", "action", "sameAs"]),
        name: e.file.name, fields: pick(e.fields, fieldNames), edits: pick(e.edits, fieldNames),
        recognized: pick(e.recognized, fieldNames), patch: pick(e.patch, patchNames),
        place: e.place === true, state: e.state === "Scanning" ? "Interrupted" : e.state
      })),
      position: { binder: position.binder, startSlot: position.startSlot },
      uploaded: Array.from(uploaded)
    };
  }
  async function fingerprint(file) {
    if (!root.crypto?.subtle) return undefined;
    const bytes = await root.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return file.type + ":" + Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, "0")).join("");
  }
  let connection;
  function database() {
    if (!connection) connection = new Promise((resolve, reject) => {
      const req = root.indexedDB.open("evento-batch-import", 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore("draft");
        req.result.createObjectStore("files");
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }).catch(error => { connection = null; throw error; });
    return connection;
  }
  let queue = Promise.resolve();
  function schedule(work) {
    const operation = queue.then(database).then(work);
    queue = operation.catch(() => {});
    return operation;
  }
  function create() {
    let revision = null;
    function transaction(mode, apply) {
      return schedule(db => new Promise((resolve, reject) => {
        const tx = db.transaction(["draft", "files"], mode);
        const drafts = tx.objectStore("draft"), files = tx.objectStore("files");
        const read = drafts.get("current");
        let result, error, next;
        tx.oncomplete = () => { revision = next; resolve(result); };
        tx.onabort = () => reject(error || tx.error || Error("Local storage unavailable"));
        read.onsuccess = () => {
          const previous = read.result;
          next = previous?.revision ?? null;
          if (mode === "readwrite" && revision !== next) {
            error = Object.assign(Error("Another tab changed the saved batch. Reopen it to continue."), {name:"DraftConflictError"});
            tx.abort();
            return;
          }
          try {
            result = apply(previous, drafts, files);
            if (mode === "readwrite") next = result;
          } catch (cause) {
            error = cause;
            tx.abort();
          }
        };
      }));
    }
    return {
      load: () => transaction("readonly", (previous, _, files) => {
        if (!previous) return null;
        for (const e of previous.entries) {
          const req = files.get(e.id);
          req.onsuccess = () => { e.file = req.result; };
        }
        return previous;
      }),
      write(entries, position, uploaded) {
        const draft = record(entries, position, uploaded);
        const blobs = new Map(entries.map(e => [e.id, e.file]));
        return transaction("readwrite", (previous, drafts, files) => {
          const existing = new Set(previous?.entries.map(e => e.id));
          for (const [id, file] of blobs)
            if (!existing.has(id)) files.put(file, id);
          for (const id of existing)
            if (!blobs.has(id)) files.delete(id);
          draft.revision = Array.from(root.crypto.getRandomValues(new Uint32Array(4))).join("-");
          drafts.put(draft, "current");
          return draft.revision;
        });
      },
      clear: () => transaction("readwrite", (_, drafts, files) => {
        drafts.delete("current");
        files.clear();
        return null;
      }),
      withScanLock(run) {
        if (!root.navigator?.locks)
          throw Error("Scanning needs a browser with Web Locks on HTTPS or localhost. You can still enter card details manually.");
        return root.navigator.locks.request("evento-import-scan", {ifAvailable:true}, lock => {
          if (!lock) throw Error("Another tab is scanning this batch. Wait for it to finish.");
          return run();
        });
      },
      fingerprint
    };
  }
  const api = { create, record, fingerprint };
  if (typeof module !== "undefined") module.exports = api;
  else root.BatchDraft = api;
})(globalThis);
