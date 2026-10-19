const test = require("node:test"),
  assert = require("node:assert/strict"),
  Store = require("../save.js");
function storage() {
  const m = new Map();
  return {
    getItem: (k) => m.get(k),
    setItem: (k, v) => m.set(k, v),
    removeItem: (k) => m.delete(k),
  };
}
function response(status, etag, data = {}) {
  return {
    ok: status < 300,
    status,
    headers: { get: () => etag },
    json: async () => data,
  };
}
test("conditional writes and pending failures survive reload", async () => {
  let request;
  const disk = storage(),
    store = new Store("/collection", {
      storage: disk,
      fetch: async (url, opts) => {
        request = opts;
        return opts.method ? response(500) : response(200, '"r1"');
      },
    });
  await store.load();
  await assert.rejects(store.save({ A: { jp: true } }));
  assert.equal(request.headers["If-Match"], '"r1"');
  assert.equal(store.recovery().data.A.jp, true);
  assert.ok(store.pending);
});
test("conflicts are never automatically replayed", async () => {
  let writes = 0;
  const store = new Store("/collection", {
    storage: storage(),
    fetch: async (url, opts) => {
      if (opts.method) {
        writes++;
        return response(409);
      }
      return response(200, '"new"');
    },
  });
  await store.load();
  await assert.rejects(store.save({ A: {} }));
  await assert.rejects(store.save());
  assert.equal(writes, 1);
  assert.throws(() => store.restore({ etag: '"old"', data: {} }));
});
test("serial queue preserves newer changes during in-flight save", async () => {
  let finish;
  let writes = [];
  const store = new Store("/collection", {
    storage: storage(),
    fetch: async (url, opts) => {
      if (!opts.method) return response(200, '"1"');
      writes.push(JSON.parse(opts.body));
      if (writes.length === 1) await new Promise((r) => (finish = r));
      return response(200, '"' + (writes.length + 1) + '"');
    },
  });
  await store.load();
  const saving = store.save({ A: 1 });
  store.stage({ A: 2 });
  finish();
  await saving;
  assert.deepEqual(writes, [{ A: 1 }, { A: 2 }]);
  assert.equal(store.pending, null);
  assert.equal(store.recovery(), null);
});

async function failedSave(reply) {
  const states = [];
  const store = new Store("/catalog", {
    storage: storage(),
    onchange: (state) => states.push(state),
    fetch: async (url, opts) => {
      if (!opts.method) return response(200, '"1"');
      if (reply instanceof Error) throw reply;
      return reply;
    },
  });
  await store.load();
  let error;
  try { await store.save({ cards: [] }); } catch (e) { error = e; }
  assert.ok(error);
  assert.deepEqual(store.recovery().data, { cards: [] });
  assert.equal(states.at(-1), error.message);
  return { store, error };
}
test("validation failures preserve bounded server detail", async () => {
  const { error } = await failedSave(response(400, null, { error: "  Invalid release code.  " }));
  assert.equal(error.message, "Changes were not saved. Invalid release code.");
  const fallback = await failedSave(response(400, null, { message: "Missing card number." }));
  assert.match(fallback.error.message, /Missing card number/);
  const long = await failedSave(response(400, null, { error: "x".repeat(1000) }));
  assert.ok(long.error.message.length < 300);
});
test("missing, malformed, or non-string error bodies use status fallback", async () => {
  const noJson = response(400); delete noJson.json;
  const invalidJson = response(400); invalidJson.json = async () => { throw Error("HTML response"); };
  for (const reply of [noJson, invalidJson, response(400, null, { error: { token: "secret" } })]) {
    const { error } = await failedSave(reply);
    assert.equal(error.message, "Changes were not saved. Check the card details and try again (400).");
  }
});
test("authentication and permission errors give specific recovery guidance", async () => {
  const expired = await failedSave(response(401, null, { error: "private server detail" }));
  assert.equal(expired.error.message, "Editing access expired. Reload the page, unlock editing, then restore or export your pending changes.");
  const denied = await failedSave(response(403));
  assert.equal(denied.error.message, "You do not have permission to save these changes. Reload the page and unlock with authorized access, then restore or export your pending changes.");
});
test("network errors are distinct from HTTP failures", async () => {
  const { error } = await failedSave(new TypeError("Failed to fetch"));
  assert.equal(error.message, "Cannot connect to the binder server. Your changes are pending. Check your connection and try again.");
  const server = await failedSave(response(500, null, { error: "token=secret" }));
  assert.equal(server.error.message, "Save failed (500). Your changes are still pending.");
});
test("precondition conflicts retain pending edits and block retries", async () => {
  const { store, error } = await failedSave(response(412));
  assert.match(error.message, /Newer server edits were preserved/);
  assert.equal(store.conflict, true);
  await assert.rejects(store.save(), /Conflict/);
});
