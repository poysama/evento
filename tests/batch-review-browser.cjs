const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const css = fs.readFileSync("index.html", "utf8").match(/<style>([\s\S]*?)<\/style>/)[1];
    await page.setContent('<meta name="viewport" content="width=device-width, initial-scale=1"><style>' + css + '</style>');
    for (const path of ["workflow.js", "batch.js", "batch-ui.js"])
      await page.addScriptTag({ path });
    await page.evaluate(() => {
      const snapshot = { catalog: { cards: [], binders: [{ id: "main", name: "Main" }], share: {} }, etag: "v1" };
      let id = 0;
      window.calls = { scans: 0, saves: 0 };
      window.batch = BatchImporter.open({
        guard() {}, id: () => "v_" + ++id, alts: () => [], rarities: ["R"], available: true,
        snapshot: () => snapshot, fetchSnapshot: async () => snapshot,
        usage: async () => ({ remaining: 0, limit: 40, month: "2026-11" }),
        recognize() { calls.scans++; throw Error("Unexpected recognition"); },
        upload: async (_, path) => path, commit: async () => { calls.saves++; }, confirm: () => true,
      }, { binder: "main", slot: 1 });
    });
    const buffer = process.env.CARD_FIXTURE ? fs.readFileSync(process.env.CARD_FIXTURE) : Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==","base64");
    const files = Array.from({ length: 20 }, (_, i) => ["jp", "en"].map(lang => ({
      name: 'OP15-' + String(i+100) + '_' + lang + '.png', mimeType: 'image/png', buffer,
    }))).flat();
    await page.getByLabel("Select card images").setInputFiles(files);
    await page.waitForFunction(() => batch.entries.length === 40);
    await page.evaluate(() => {
      batch.entries.forEach((entry,i) => {
        entry.fields = { num: entry.file.name.split("_")[0], language: i%2 ? "en" : "jp", cardType: i===7 ? "" : "Event", col:"Red", rar:"R", name:"Gum-Gum Giant" };
        entry.state = "Scanned";
      });
      document.querySelector(".batch-scroll nav button").click();
    });
    const queue = page.locator(".batch-scroll nav button");
    assert.equal(await queue.count(), 20);
    assert.equal(await page.locator(".batch-pair figure").count(), 2);
    assert.equal(await page.getByRole("button", {name:"Scan allowance exhausted"}).isDisabled(),true);
    await queue.nth(1).focus();
    await page.keyboard.press("Enter");
    assert.equal(await queue.nth(1).getAttribute("aria-current"),"true");
    for (const [name,width,height] of [["desktop",1280,900],["mobile",390,844]]) {
      await page.setViewportSize({width,height});
      assert.equal(await page.locator("dialog").evaluate(el => el.scrollWidth<=el.clientWidth),true);
      if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: process.env.SCREENSHOT_DIR + "/evento-batch-" + name + ".png" });
    }
    await page.getByRole("button",{name:"Review OP15-103"}).click();
    const field = page.locator(":focus");
    assert.equal(await field.evaluate(el => el.closest("label").dataset.key), "Card type");
    await field.selectOption("Event");
    assert.equal(await page.getByRole("button",{name:"Reanalyze image",exact:true}).first().isDisabled(),true);
    assert.equal(await page.locator(".batch-scroll nav .batch-ready").count(),20);
    await page.getByRole("button",{name:"Confirm import"}).click();
    await page.locator("dialog").waitFor({state:"detached"});
    assert.deepEqual(await page.evaluate(() => calls),{scans:0,saves:1});
    await page.evaluate(() => {
      const file = new File(["local"], "paused.png", { type: "image/png" });
      const saved = { entries:[{ id:"saved", file, versionId:"v_saved", group:"", state:"Scanned",
        fields:{ num:"OP15-110", cardType:"Event", language:"en", name:"Saved name", col:"Red", rar:"R" } }],
        position:{ binder:"main", startSlot:1 }, uploaded:[] };
      const snap = { catalog:{ cards:[], share:{}, binders:[{id:"main",name:"Main"}] }, etag:"v1" };
      window.recovery = { clears:0, writes:0, scans:0 };
      window.reopen = () => BatchImporter.open({
        guard(){}, id:()=>"fixture", available:true, alts:()=>[], rarities:["R"], snapshot:()=>snap, fetchSnapshot:async()=>snap,
        drafts:{load:async()=>saved, clear:async()=>{recovery.clears++}, write:async()=>{recovery.writes++}},
        usage:async()=>({remaining:40,limit:80,month:"2026-11"}),
        recognize:async()=>{recovery.scans++;return { fields:saved.entries[0].fields };}, confirm:()=>true
      },{binder:"main",slot:1});
      reopen();
    });
    await page.getByRole("button",{name:"Resume import"}).click();
    assert.equal(await page.locator(".batch-card-title").innerText(),"OP15-110");
    assert.equal(await page.evaluate(()=>recovery.scans),0);
    await page.getByText("Image details",{exact:true}).click();
    await page.getByRole("button",{name:"Reanalyze image"}).click();
    await page.waitForFunction(()=>recovery.scans===1);
    await page.getByRole("button",{name:"Close import"}).click();
    await page.locator("dialog").waitFor({state:"detached"});
    await page.evaluate(()=>{reopen()});
    await page.getByRole("button",{name:"Discard draft"}).click();
    await page.getByText("Choose your card images",{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>recovery.clears),1);
    await page.getByRole("button",{name:"Close import"}).click();
    await page.locator("dialog").waitFor({state:"detached"});
    await page.evaluate(()=>{
      const snap={catalog:{cards:[],share:{},binders:[{id:"main",name:"Main"}]},etag:"v1"};
      window.leaveAnyway=false;
      BatchImporter.open({
        guard(){},id:()=>"localid",alts:()=>[],rarities:["R"],available:true,snapshot:()=>snap,
        usage:async()=>({remaining:0,limit:40,month:"2026-11"}),confirm:()=>leaveAnyway,
        drafts:{load:async()=>null,fingerprint:async()=>"hash",write:async()=>{throw Error("Device storage full")},clear:async()=>{}}
      },{binder:"main",slot:1});
    });
    await page.getByLabel("Select card images").setInputFiles(files[0]);
    await page.getByText(/Local recovery unavailable.*Device storage full/).waitFor({state:"visible"});
    await page.keyboard.press("Escape");
    assert.ok(await page.locator("dialog").isVisible());
    await page.evaluate(()=>{window.leaveAnyway=true});
    await page.getByRole("button",{name:"Close import"}).click();
    await page.locator("dialog").waitFor({state:"detached"});
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
