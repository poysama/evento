const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH });
  try {
    const context = await browser.newContext();
    let page = await context.newPage();
    await page.context().route("https://evento.test/**", route => route.fulfill({
      contentType: "text/html", body: "<title>Local draft fixture</title>"
    }));
    async function open() {
      await page.goto("https://evento.test/");
      await page.addScriptTag({ path: "batch-draft.js" });
    }
    await open();
    await page.evaluate(async () => {
      const store = BatchDraft.create();
      await store.load();
      await store.write([{ id: "a", versionId: "b", state: "Scanned", group: "",
        fields: { name: "Saved" }, file: new File(["bytes"], "card.png", {type:"image/png"})
      }], {binder:"main", startSlot:4}, new Map([["a.png", "a.png?v=1"]]));
    });
    await open();
    const restored = await page.evaluate(async () => {
      const first = BatchDraft.create(), second = BatchDraft.create();
      const snapshot = await first.load();
      await second.load();
      const entry = snapshot.entries[0];
      const content = await entry.file.text();
      entry.fields.name = "Updated";
      await first.write([entry], snapshot.position, new Map(snapshot.uploaded));
      let conflict;
      try { await second.clear(); } catch (error) { conflict = error.message; }
      const updated = await first.load();
      await first.clear();
      return { name:entry.file.name, content, updated:updated.entries[0].fields.name,
        position:updated.position.startSlot, uploaded:updated.uploaded, conflict, cleared:await first.load() };
    });
    assert.deepEqual(restored, { name:"card.png", content:"bytes", updated:"Updated",
      position:4, uploaded:[["a.png","a.png?v=1"]], conflict:"Another tab changed the saved batch. Reopen it to continue.", cleared:null });
    async function openImporter() {
      await open();
      for (const path of ["workflow.js", "batch.js", "batch-ui.js"]) await page.addScriptTag({path});
      await page.evaluate(() => {
        const snapshot = {etag:"v1", catalog:{cards:[], drafts:[], share:{}, binders:[{id:"main", name:"Main"}]}};
        window.scans = 0;
        window.batch = BatchImporter.open({
          guard(){}, drafts:BatchDraft.create(), snapshot:() => snapshot, fetchSnapshot:async () => snapshot,
          id:() => crypto.randomUUID(), rarities:["R"], alts:() => [], available:true, confirm:() => true,
          usage:async () => ({remaining:1, limit:1, month:"fixture"}),
          recognize:async () => { scans++; return { fields:{num:"P-001", language:"en", cardType:"Event", name:"Reviewed", col:"Red", rar:"R"} }; },
          upload:async (_, path) => path, commit:async () => {}
        }, {binder:"main", slot:1});
      });
    }
    await openImporter();
    await page.getByLabel("Select card images").setInputFiles({name:"card.png", mimeType:"image/png", buffer:Buffer.from("saved image")});
    await page.getByRole("button", {name:/^Analyze 1 image/}).click();
    await page.waitForFunction(() => !batch.scanning && batch.entries[0]?.state === "Scanned");
    await page.getByRole("button",{name:"Close import"}).click();
    await page.waitForFunction(() => !document.querySelector("dialog"));
    await openImporter();
    await page.getByRole("button",{name:"Resume import"}).click();
    await page.waitForFunction(() => batch.entries.length === 1);
    assert.deepEqual(await page.evaluate(async () => [batch.entries[0].fields.name,
      await batch.entries[0].file.text(), scans]), ["Reviewed", "saved image", 0]);
    await page.getByLabel("Select card images").setInputFiles({name:"renamed.png", mimeType:"image/png", buffer:Buffer.from("saved image")});
    await page.getByRole("button",{name:"Confirm import"}).click();
    await page.waitForFunction(() => !document.querySelector("dialog"));
    await openImporter();
    await page.evaluate(() => batch.ready);
    assert.equal(await page.evaluate(() => batch.hasDraft), false);
    await page.getByLabel("Select card images").setInputFiles({name:"new.png", mimeType:"image/png", buffer:Buffer.from("another image")});
    await page.waitForFunction(() => batch.entries.length === 1);
    await page.evaluate(() => batch.flush());
    const first = page;
    page = await first.context().newPage();
    await openImporter();
    await page.getByRole("button",{name:"Resume import"}).click();
    await page.waitForFunction(() => batch.entries.length === 1);
    const run = tab => tab.evaluate(() => batch.analyze(() => {}).then(() => "ok", e => e.message));
    const results = await Promise.all([run(first), run(page)]);
    assert.equal(results.filter(value => value === "ok").length, 1);
    assert.match(results.find(value => value !== "ok"), /Another tab/);
    assert.equal(await first.evaluate(() => scans) + await page.evaluate(() => scans), 1);
    const unsupported = await page.evaluate(async () => {
      Object.defineProperty(navigator, "locks", {configurable:true, value:undefined});
      const before = scans;
      const message = await batch.analyze(() => {}, 1, [batch.entries[0].id]).then(() => "", e => e.message);
      return {calls:scans - before, message, retained:await batch.entries[0].file.text()};
    });
    assert.equal(unsupported.calls, 0);
    assert.match(unsupported.message, /Web Locks on HTTPS or localhost.*manually/);
    assert.equal(unsupported.retained, "another image");
    console.log("Missing Web Locks blocks recognition and retains the file for manual entry.");
    console.log("Concurrent Analyze in two tabs sends one recognition request.");
    console.log("Modal reopened with its reviewed file, zero rescans, and cleared the draft after save.");
    console.log("Browser reload preserves File data and edits. Concurrent discard preserves the newer draft.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
