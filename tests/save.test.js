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
