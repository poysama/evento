(function (root) {
  "use strict";
  async function saveFailure(response) {
    if (response.status === 401)
      return Error("Editing access expired. Reload the page, unlock editing, then restore or export your pending changes.");
    if (response.status === 403)
      return Error("You do not have permission to save these changes. Reload the page and unlock with authorized access, then restore or export your pending changes.");
    if (response.status === 400) {
      let detail = "";
      try {
        const body = await response.json();
        const value = body && (typeof body.error === "string" ? body.error : body.message);
        if (typeof value === "string")
          detail = value.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 240);
      } catch (e) {}
      return Error(detail
        ? "Changes were not saved. " + detail
        : "Changes were not saved. Check the card details and try again (400).");
    }
    return Error("Save failed (" + response.status + "). Your changes are still pending.");
  }
  class RevisionStore {
    constructor(url, options = {}) {
      this.url = url;
      this.canWrite = options.canWrite || function () { return true; };
      this.fetch = options.fetch || root.fetch.bind(root);
      this.storage = options.storage || root.localStorage;
      this.key = "evento.pending." + url;
      this.etag = null;
      this.pending = null;
      this.running = null;
      this.conflict = false;
      this.onchange = options.onchange || function () {};
    }
    async load() {
      const r = await this.fetch(this.url, { cache: "no-store" });
      if (!r.ok) throw Error("Load failed (" + r.status + ")");
      this.etag = r.headers.get("ETag");
      if (!this.etag) throw Error("Server revision missing");
      return r.json();
    }
    recovery() {
      if (!this.canWrite()) return null;
      try {
        return JSON.parse(this.storage.getItem(this.key) || "null");
      } catch (e) {
        return null;
      }
    }
    stage(data) {
      if (!this.canWrite()) throw Error("Unlock editing before saving changes.");
      this.pending = JSON.parse(JSON.stringify(data));
      try {
        this.storage.setItem(
          this.key,
          JSON.stringify({
            etag: this.etag,
            data: this.pending,
            at: new Date().toISOString(),
          }),
        );
      } catch (e) {}
      this.onchange("unsaved");
    }
    discard() {
      if (!this.canWrite()) return;
      this.pending = null;
      this.conflict = false;
      try {
        this.storage.removeItem(this.key);
      } catch (e) {}
      this.onchange("saved");
    }
    restore(saved) {
      if (!this.canWrite()) throw Error("Unlock editing before restoring changes.");
      if (saved.etag !== this.etag) {
        this.conflict = true;
        throw Error(
          "Recovery revision differs. Export your pending data and reload before merging.",
        );
      }
      this.stage(saved.data);
      return saved.data;
    }
    async save(data) {
      if (!this.canWrite()) throw Error("Unlock editing before saving changes.");
      if (data !== undefined) this.stage(data);
      if (this.conflict)
        throw Error(
          "Conflict. Reload and reconcile pending edits before saving.",
        );
      if (this.running) return this.running;
      if (!this.pending) return;
      this.running = this.drain();
      try {
        return await this.running;
      } finally {
        this.running = null;
      }
    }
    async drain() {
      while (this.pending) {
        if (!this.canWrite()) throw Error("Unlock editing before saving changes.");
        const snapshot = this.pending;
        this.onchange("saving");
        let r;
        try {
          try {
            r = await this.fetch(this.url, {
              method: "PUT",
              headers: {
                "Content-Type": "application/json",
                "If-Match": this.etag,
              },
              body: JSON.stringify(snapshot),
            });
          } catch (e) {
            throw Error("Cannot connect to the binder server. Your changes are pending. Check your connection and try again.");
          }
          if (r.status === 409 || r.status === 412) {
            this.conflict = true;
            throw Error(
              "Conflict. Newer server edits were preserved. Export pending changes, reload and reconcile.",
            );
          }
          if (!r.ok) throw await saveFailure(r);
          const revision = r.headers.get("ETag");
          if (!revision) throw Error("Saved response missing revision");
          this.etag = revision;
          if (this.pending === snapshot) this.discard();
          else {
            try {
              this.storage.setItem(
                this.key,
                JSON.stringify({ etag: this.etag, data: this.pending }),
              );
            } catch (e) {}
          }
        } catch (e) {
          this.onchange(e.message);
          throw e;
        }
      }
    }
  }
  if (typeof module !== "undefined") module.exports = RevisionStore;
  else root.RevisionStore = RevisionStore;
})(typeof window === "undefined" ? globalThis : window);
