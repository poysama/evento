const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH });
  try {
    const page = await browser.newPage();
    const cards = JSON.parse(fs.readFileSync("cards.json")).slice(0,32);
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.route("**/*", async route => {
      const path = new URL(route.request().url()).pathname;
      assert.equal(route.request().method(),"GET");
      if (path === "/") return route.fulfill({path:process.env.BINDER_HTML || "index.html",contentType:"text/html"});
      if (/^\/[\w-]+\.js$/.test(path)) return route.fulfill({path:path.slice(1),contentType:"text/javascript"});
      if (/\.(png|jpg|webp)$/.test(path)) {
        if (process.env.CARD_FIXTURE) return route.fulfill({path:process.env.CARD_FIXTURE,contentType:"image/png"});
        return route.fulfill({body:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==","base64"),contentType:"image/png"});
      }
      const fixtures = {"/api/access":{canEdit:false},"/api/catalog":{cards,binders:[{id:"main",name:"Main"}]},
        "/api/collection":{},"/card_images_jp/manifest.json":[]};
      return route.fulfill({json:fixtures[path] || {},headers:{ETag:'"test"'}});
    });
    for (const [width,height] of [[1920,1080],[1440,900],[1024,768],[390,844]]) {
      await page.setViewportSize({width,height});
      await page.goto("http://binder.test/");
      await page.locator("body.workflow-ready").waitFor();
      const art = page.locator("#grid .art");
      assert.equal(await art.count(),16);
      const card = art.nth(1);
      if (width >= 1280 || process.env.BASELINE) {
        await card.focus();
        await page.waitForFunction(()=>document.querySelector("#pv img")?.naturalWidth>0);
      }
      if (!process.env.BASELINE) {
        const geometry = await page.evaluate(()=>{
          const rect = selector=>document.querySelector(selector).getBoundingClientRect().toJSON();
          return {grid:rect("#grid"),side:rect(".side"),rail:rect(".pv"),preview:rect("#pv img"),pageWidth:document.documentElement.scrollWidth};
        });
        assert.ok(geometry.pageWidth<=width,JSON.stringify(geometry));
        if(width>=1280) {
          assert.ok(geometry.side.right <= geometry.grid.left && geometry.grid.right <= geometry.rail.left,JSON.stringify(geometry));
          assert.ok(geometry.preview.width>250 && geometry.preview.bottom<=height && geometry.grid.bottom<=height+1,JSON.stringify(geometry));
          assert.ok(Math.abs(geometry.preview.width/geometry.preview.height-600/838)<.005);
          assert.equal(await page.locator("#pv h3").innerText(),cards[1].name);
          console.log(width,"grid",Math.round(geometry.grid.width),"preview",Math.round(geometry.preview.width));
        } else {
          assert.equal(geometry.rail.width,0);
          await card.click();
          await page.getByRole("dialog",{name:"Card viewer"}).waitFor();
          await page.waitForFunction(()=>document.querySelector("#dlg .vi img")?.naturalWidth>0);
        }
      }
      if (process.env.SCREENSHOT_DIR) await page.screenshot({path:process.env.SCREENSHOT_DIR+"/evento-binder-"+(process.env.BASELINE?"before":"after")+"-"+width+".png"});
      if(!process.env.BASELINE) {
        if(width<1280) await page.keyboard.press("Escape");
        await page.getByRole("button",{name:"Collection",exact:true}).click();
        assert.equal(await page.locator(".pv").isVisible(),false);
      }
    }
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
}
run().catch(error=>{console.error(error);process.exitCode=1;});
