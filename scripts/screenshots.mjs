import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, dirname, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const mime = {".html":"text/html; charset=utf-8",".js":"text/javascript; charset=utf-8",".json":"application/json",".css":"text/css"};
const server = createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  const path = resolve(root, "." + pathname);
  if (!path.startsWith(root + sep)) { response.writeHead(403).end(); return; }
  try {
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
  page.on("pageerror", error=>errors.push(error.message));
  const output=resolve(root,"docs","images");
  await mkdir(output,{recursive:true});
  for(const screen of ["material","method","code","password","consent","verification","options","dashboard","confirmation"]){
    await page.goto(`http://127.0.0.1:${port}/docs/demo/index.html?screen=${screen}`);
    await page.waitForSelector('body[data-ready="true"]');
    if(["dashboard","confirmation"].includes(screen)) await page.locator("gwm-thailand-card .gauge").waitFor({state:"attached"});
    if(screen==="confirmation") await page.locator("gwm-thailand-card [role=dialog]").waitFor();
    await page.screenshot({path:resolve(output,screen+".png"),fullPage:true});
    console.log("Generated synthetic documentation screen:",screen);
  }
  await page.goto(`http://127.0.0.1:${port}/docs/demo/index.html?screen=icon`);
  await page.waitForSelector('body[data-ready="true"]');
  const brand=resolve(root,"custom_components","gwm_thailand_connect","brand");
  await mkdir(brand,{recursive:true});
  await page.locator("#icon").screenshot({path:resolve(brand,"icon.png")});
  if(errors.length)throw new Error("Documentation renderer errors: "+errors.join("; "));
} finally {
  if(browser)await browser.close();
  await new Promise(resolve=>server.close(resolve));
}
