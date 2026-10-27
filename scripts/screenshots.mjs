import { createServer } from "node:http";
import { readFile, mkdir, stat } from "node:fs/promises";
import {createReadStream} from "node:fs";
import { resolve, dirname, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import assert from "node:assert/strict";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const mime = {".html":"text/html; charset=utf-8",".js":"text/javascript; charset=utf-8",".json":"application/json",".css":"text/css"};
const server = createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  const fixture=pathname==="/local/gwm-maps/test.pmtiles" && process.env.GWM_OSM_TEST_ARCHIVE;
  const path = fixture ? resolve(process.env.GWM_OSM_TEST_ARCHIVE) : resolve(root, "." + pathname);
  if (!fixture && !path.startsWith(root + sep)) { response.writeHead(403).end(); return; }
  try {
    if(fixture){
      const {size}=await stat(path);
      const range=/^bytes=(\d+)-(\d*)$/.exec(request.headers.range || "");
      if(!range){response.writeHead(200,{"content-type":"application/octet-stream","content-length":size,"accept-ranges":"bytes"});createReadStream(path).pipe(response);return;}
      const start=Number(range[1]),end=Math.min(size-1,range[2]?Number(range[2]):size-1);
      if(start> end || start>=size){response.writeHead(416,{"content-range":`bytes */${size}`}).end();return;}
      response.writeHead(206,{"content-type":"application/octet-stream","content-length":end-start+1,"content-range":`bytes ${start}-${end}/${size}`,"accept-ranges":"bytes"});
      createReadStream(path,{start,end}).pipe(response);return;
    }
    response.writeHead(200, {"content-type":mime[extname(path)] || "application/octet-stream"});
    response.end(await readFile(path));
  } catch (error) {
    if (error.code !== "ENOENT") console.error(error);
    response.writeHead(404).end();
  }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
let browser;
try {
  browser = await chromium.launch({
    headless:true,
    channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
  });
  const context = await browser.newContext({viewport:{width:1280,height:1000},locale:"en-US",timezoneId:"Asia/Bangkok"});
  await context.route("**/*", route => {
    const url = new URL(route.request().url());
    return url.hostname === "127.0.0.1" && url.port === String(port) ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  const errors=[];
  const blocked=[];
  const tiles=[];
  page.on("requestfailed",request=>{if(request.url().includes("test.pmtiles"))tiles.push("failed");});
  page.on("response",response=>{if(response.url().includes("test.pmtiles"))tiles.push(response.status());});
  page.on("pageerror", error=>errors.push(error.message));
  const output=resolve(root,"docs","images");
  await mkdir(output,{recursive:true});
  const journeysOnly=process.argv.includes("--journeys-only");
  for(const screen of journeysOnly ? [] : ["material","method","code","password","consent","verification","options","dashboard","confirmation"]){
    await page.goto(`http://127.0.0.1:${port}/docs/demo/index.html?screen=${screen}`);
    await page.waitForSelector('body[data-ready="true"]');
    if(["dashboard","confirmation"].includes(screen)) await page.locator("gwm-thailand-card .gauge").waitFor({state:"attached"});
    if(screen==="confirmation") await page.locator("gwm-thailand-card [role=dialog]").waitFor();
    await page.screenshot({path:resolve(output,screen+".png"),fullPage:true});
    console.log("Generated synthetic documentation screen:",screen);
  }
  if(!journeysOnly){
    await page.goto(`http://127.0.0.1:${port}/docs/demo/index.html?screen=icon`);
    await page.waitForSelector('body[data-ready="true"]');
    const brand=resolve(root,"custom_components","gwm_thailand_connect","brand");
    await mkdir(brand,{recursive:true});
    await page.locator("#icon").screenshot({path:resolve(brand,"icon.png")});
  }else{
    if(!process.env.GWM_OSM_TEST_ARCHIVE)throw new Error("Set GWM_OSM_TEST_ARCHIVE to a generic local test archive; no owner data is needed");
    await page.goto(`http://127.0.0.1:${port}/docs/demo/journey.html`);
    await page.waitForSelector('body[data-ready="true"]');
    await page.locator("gwm-journey-card .car-marker").waitFor();
    await page.waitForFunction(()=>document.querySelector("gwm-journey-card")._baseLayer?._tiles && Object.values(document.querySelector("gwm-journey-card")._baseLayer._tiles).some(tile=>tile.loaded));
    assert.ok(tiles.includes(206),"Local PMTiles must use successful byte-range requests");
    assert.equal(await page.locator("gwm-journey-card .stop-marker").count(),3);
    await page.getByRole("button",{name:"Car",exact:true}).click();
    assert.match(await page.locator("gwm-journey-card .popup").innerText(),/Battery 62.5%/);
    assert.match(await page.locator("gwm-journey-card .popup").innerText(),/A\/C off/);
    await page.screenshot({path:resolve(output,"journeys.png"),fullPage:true});
    await page.locator("gwm-journey-card .history summary").click();
    assert.equal(await page.locator("gwm-journey-card [data-stop]").count(),3);
    await page.locator("gwm-journey-card [data-stop]").last().click();
    assert.match(await page.locator("gwm-journey-card .popup").innerText(),/Driver/);
    assert.match(await page.locator("gwm-journey-card .popup").innerText(),/~2m/);
    assert.equal(await page.locator("gwm-journey-card [data-clear]").isDisabled(),true);
    assert.equal(await page.evaluate(()=>window.__journeyCalls.some(call=>call.type.endsWith("/clear_journeys"))),false);
    await page.getByRole("button",{name:"Journey",exact:true}).click();
    await page.locator("gwm-journey-card [data-clear-check]").check();
    await page.locator("gwm-journey-card [data-clear]").click();
    await page.waitForFunction(()=>window.__journeyCalls.some(call=>call.type.endsWith("/clear_journeys")));
    for(const [mode,text] of [["disabled","History recording off"],["nogps","No recent GPS"],["stale","No recent GPS"],["error","Synthetic permission error"]]){
      await page.goto(`http://127.0.0.1:${port}/docs/demo/journey.html?mode=${mode}`);
      await page.waitForSelector('body[data-ready="true"]');
      assert.match(await page.locator("gwm-journey-card .shell").innerText(),new RegExp(text));
      assert.equal(await page.locator("gwm-journey-card .car-marker").count(),0);
      assert.equal(await page.locator("gwm-journey-card .leaflet-tile").count(),0);
    }
    console.log("OSM browser checks passed: real local vector tiles/ranges, car/status popup, three stop markers, door duration, blank stale/no-GPS states, confirmed deletion");
  }
  if(errors.length)throw new Error("Documentation renderer errors: "+errors.join("; "));
} finally {
  if(browser)await browser.close();
  await new Promise(resolve=>server.close(resolve));
}
