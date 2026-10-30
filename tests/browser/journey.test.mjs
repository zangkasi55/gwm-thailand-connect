import assert from "node:assert/strict";
import {after,before,test} from "node:test";
import {createServer} from "node:http";
import {readFile} from "node:fs/promises";
import {resolve,dirname,extname,sep} from "node:path";
import {fileURLToPath} from "node:url";
import {chromium} from "playwright";
import {syntheticMapArchive} from "../../scripts/synthetic_map_fixture.mjs";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..","..");
const frontend=resolve(root,"custom_components","gwm_thailand_connect","frontend");
const archive=syntheticMapArchive();
let server,browser,origin;
const pageHtml=`<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic map tests</title>
<style>body{margin:0;font:14px system-ui}main{max-width:1000px}gwm-journey-card{display:block}</style><main></main>
<script type="module">
import "/custom_components/gwm_thailand_connect/frontend/gwm-journey-card.js";
const now=Date.now(),mode=window.__scenario || "ready";
const timestamp=offset=>new Date(now+offset).toISOString();
const doors={door_front_driver_open:false,door_front_passenger_open:false,door_rear_driver_side_open:false,door_rear_passenger_side_open:false,trunk_open:false};
const fixture=(id,battery,longitude)=>({
  status:"recording",sample_count:3,retention_days:7,segments:[],stops:[],doors:[],
  current:{availability:"available",position:{latitude:13.748,longitude},reported_at:timestamp(-1000),expires_at:timestamp(299000),battery,locked:id==="a",doors,ac_active:false},
});
window.__histories={a:fixture("a",55,100.512),b:fixture("b",18,100.532)};
for(const history of Object.values(window.__histories)){
  if(mode==="stale")history.current.reported_at=timestamp(-3600000);
  if(mode==="future")history.current.reported_at=timestamp(60000);
  if(mode==="expired")history.current.expires_at=timestamp(-1000);
  if(mode==="missing")history.current.position=null;
  if(mode==="zero")history.current.position={latitude:0,longitude:0};
  if(mode==="unknown"){history.current.battery=null;history.current.locked=null;history.current.doors={};history.current.ac_active=null;}
}
window.__calls=[];
window.__adapter={connected:true,callWS:async request=>{
  window.__calls.push(request);
  if(request.type==="config/entity_registry/list")return ["a","b"].map(id=>({platform:"gwm_thailand_connect",device_id:id,entity_id:"sensor.synthetic_"+id}));
  if(request.type==="gwm_thailand_connect/journeys")return structuredClone(window.__histories[request.device_id]);
  throw new Error("Synthetic browser fixture refuses any write or unrecognized operation");
}};
const card=document.createElement("gwm-journey-card");
card.setConfig({type:"custom:gwm-journey-card",map_archive:"/local/test.pmtiles"});
window.__card=card;document.querySelector("main").append(card);card.hass=window.__adapter;
document.body.dataset.ready="true";
</script></html>`;

before(async()=>{
  server=createServer(async(request,response)=>{
    const path=new URL(request.url,"http://127.0.0.1").pathname;
    if(path==="/test"){
      response.writeHead(200,{"content-type":"text/html; charset=utf-8"}).end(pageHtml);return;
    }
    if(path==="/local/test.pmtiles"){
      const range=/^bytes=(\d+)-(\d*)$/.exec(request.headers.range || "");
      if(!range){
        response.writeHead(200,{"content-type":"application/octet-stream","content-length":archive.length}).end(archive);return;
      }
      const start=Number(range[1]),end=Math.min(archive.length-1,range[2]?Number(range[2]):archive.length-1);
      if(start>end || start>=archive.length){
        response.writeHead(416,{"content-range":`bytes */${archive.length}`}).end();return;
      }
      response.writeHead(206,{"content-type":"application/octet-stream","content-range":`bytes ${start}-${end}/${archive.length}`,"content-length":end-start+1,"accept-ranges":"bytes"})
        .end(archive.subarray(start,end+1));return;
    }
    if(!path.startsWith("/custom_components/gwm_thailand_connect/frontend/")){
      response.writeHead(404).end();return;
    }
    const file=resolve(root,"."+decodeURIComponent(path));
    if(!file.startsWith(frontend+sep)){response.writeHead(403).end();return;}
    try{
      const data=await readFile(file);
      response.writeHead(200,{"content-type":extname(file)===".css"?"text/css":"text/javascript"}).end(data);
    }catch(error){
      if(error.code!=="ENOENT")console.error(error);
      response.writeHead(404).end();
    }
  });
  await new Promise(done=>server.listen(0,"127.0.0.1",done));
  origin="http://127.0.0.1:"+server.address().port;
  browser=await chromium.launch({headless:true,channel:process.platform==="win32"?"msedge":undefined});
});
after(async()=>{
  if(browser)await browser.close();
  if(server)await new Promise(done=>server.close(done));
});

async function withPage(callback,scenario="ready"){
  const context=await browser.newContext({viewport:{width:1100,height:950},locale:"en-US"});
  const blocked=[],errors=[],ranges=[];
  await context.route("**/*",route=>{
    if(new URL(route.request().url()).origin===origin)return route.continue();
    blocked.push(route.request().url());return route.abort();
  });
  const page=await context.newPage();
  page.on("pageerror",error=>errors.push(error.message));
  page.on("response",response=>{if(response.url().endsWith("/local/test.pmtiles"))ranges.push(response.status());});
  await page.addInitScript(value=>{window.__scenario=value;},scenario);
  try{
    await callback(page,{blocked,errors,ranges});
    assert.deepEqual(blocked,[],"Map must not request third-party data or a CDN");
    assert.deepEqual(errors,[],"Map must not leave uncaught browser errors");
  }finally{await context.close();}
}
async function ready(page){
  await page.goto(origin+"/test");
  await page.waitForSelector('body[data-ready="true"]');
}
async function marker(page){
  await page.locator("gwm-journey-card .car-marker").waitFor();
}

test("real Leaflet renders a tiny local synthetic vector map with compact current status and attribution",async()=>{
  await withPage(async(page,{ranges})=>{
    await ready(page);await marker(page);
    await page.waitForFunction(()=>Object.values(window.__card._baseLayer?._tiles || {}).some(tile=>tile.loaded));
    assert.ok(ranges.includes(206));
    assert.ok(await page.locator("gwm-journey-card canvas").count()>0);
    await page.getByRole("button",{name:"Car",exact:true}).click();
    assert.match(await page.locator("gwm-journey-card .popup").innerText(),/Battery 55%/);
    assert.match(await page.locator("gwm-journey-card .popup").innerText(),/Locked/);
    assert.match(await page.locator("gwm-journey-card .popup").innerText(),/A\/C off/);
    assert.match(await page.locator("gwm-journey-card .credit").innerText(),/OpenStreetMap contributors/);
    assert.equal(await page.locator("gwm-journey-card .history").getAttribute("open"),null);
    assert.equal(await page.evaluate(()=>window.__calls.some(call=>call.type.includes("clear"))),false);
  });
});

test("missing, stale, future, expired and zero fixes stay blank with no default city or tile downloads",async()=>{
  for(const scenario of ["missing","stale","future","expired","zero"]){
    await withPage(async(page,{ranges})=>{
      await ready(page);
      await page.waitForFunction(()=>window.__card._history && !window.__card._loading);
      assert.equal(await page.locator("gwm-journey-card .car-marker").count(),0,scenario);
      assert.match(await page.locator("gwm-journey-card .status").innerText(),/No recent GPS/);
      assert.equal(await page.evaluate(()=>window.__card._map===undefined),true);
      assert.deepEqual(ranges,[]);
    },scenario);
  }
});

test("unknown status stays unknown rather than becoming zero, locked, closed or off",async()=>{
  await withPage(async page=>{
    await ready(page);await marker(page);
    await page.getByRole("button",{name:"Car",exact:true}).click();
    const popup=await page.locator("gwm-journey-card .popup").innerText();
    assert.match(popup,/Battery —/);
    assert.doesNotMatch(popup,/Battery 0|Locked|Doors closed|A\/C off/);
  },"unknown");
});

test("manual pan and zoom survive a cloud status refresh",async()=>{
  await withPage(async page=>{
    await ready(page);await marker(page);
    const before=await page.evaluate(()=>{
      window.__card._map.setView([13.7492,100.5135],17,{animate:false});
      const map=window.__card._map;
      return {center:map.getCenter(),zoom:map.getZoom(),pixel:map.project(map.getCenter(),map.getZoom())};
    });
    await page.evaluate(async()=>{
      window.__histories.a.current.battery=56;
      await window.__card._load();
    });
    const after=await page.evaluate(()=>{
      const map=window.__card._map;
      return {center:map.getCenter(),zoom:map.getZoom(),pixel:map.project(map.getCenter(),map.getZoom())};
    });
    // Leaflet may round a refreshed center to the nearest screen pixel.
    assert.ok(Math.hypot(before.pixel.x-after.pixel.x,before.pixel.y-after.pixel.y)<=1,JSON.stringify({before,after}));
    assert.equal(after.zoom,17);
    assert.match(await page.locator("gwm-journey-card .status").innerText(),/Battery 56%/);
  });
});

test("Refresh explicitly asks for cached cloud data without changing automatic read behavior",async()=>{
  await withPage(async page=>{
    await ready(page);await marker(page);
    assert.equal(await page.evaluate(()=>window.__calls.find(call=>call.type.endsWith("/journeys")).refresh_cloud),undefined);
    await page.getByRole("button",{name:"Refresh",exact:true}).click();
    await page.waitForFunction(()=>window.__calls.some(call=>call.refresh_cloud===true));
    assert.equal(await page.evaluate(()=>window.__calls.filter(call=>call.refresh_cloud===true).length),1);
    await marker(page);
  });
});
test("disconnect immediately removes a current car marker instead of leaving a false live location",async()=>{
  await withPage(async page=>{
    await ready(page);await marker(page);
    await page.evaluate(()=>{window.__card.hass={...window.__adapter,connected:false};});
    assert.equal(await page.locator("gwm-journey-card .car-marker").count(),0);
    assert.match(await page.locator("gwm-journey-card .status").innerText(),/Disconnected/);
  });
});

test("the freshness timer removes an expired marker without another cloud response",async()=>{
  await withPage(async page=>{
    await ready(page);await marker(page);
    await page.evaluate(()=>{window.__card._history.current.expires_at=new Date(Date.now()+100).toISOString();});
    await page.waitForFunction(()=>!window.__card.shadowRoot.querySelector(".car-marker"),{},{timeout:20000});
    assert.match(await page.locator("gwm-journey-card .status").innerText(),/No recent GPS/);
    assert.equal(await page.evaluate(()=>window.__calls.filter(call=>call.type.endsWith("/journeys")).length),1);
  });
});

test("a delayed old-vehicle response cannot redraw its marker after switching vehicles",async()=>{
  await withPage(async page=>{
    await ready(page);await marker(page);
    await page.evaluate(()=>{
      window.__card._hass={...window.__adapter,callWS:request=>{
        if(request.type.endsWith("/journeys") && request.device_id==="a"){
          return new Promise(done=>{window.__releaseOld=()=>done(structuredClone(window.__histories.a));});
        }
        return window.__adapter.callWS(request);
      }};
      window.__pendingOld=window.__card._load();
    });
    await page.waitForFunction(()=>typeof window.__releaseOld==="function");
    await page.locator("gwm-journey-card [data-device]").selectOption("b");
    await page.waitForFunction(()=>window.__card._history?.current?.battery===18 && !window.__card._loading);
    await page.evaluate(async()=>{window.__releaseOld();await window.__pendingOld;});
    assert.match(await page.locator("gwm-journey-card .status").innerText(),/Battery 18%/);
    assert.equal(await page.evaluate(()=>window.__card._device),"b");
    assert.equal(await page.evaluate(()=>window.__card._car.getLatLng().lng),100.532);
  });
});

test("an invalid archive response is an explicit error, not a successful blank or current marker",async()=>{
  await withPage(async page=>{
    await page.route("**/local/test.pmtiles",route=>route.fulfill({
      status:200,contentType:"text/html",body:"Synthetic invalid archive response",
    }));
    await ready(page);
    await page.locator("gwm-journey-card [role=alert]").waitFor();
    assert.match(await page.locator("gwm-journey-card [role=alert]").innerText(),/archive unavailable or byte ranges unsupported/);
    assert.equal(await page.locator("gwm-journey-card .car-marker").count(),0);
    assert.equal(await page.evaluate(()=>window.__card._map),null);
  });
});

test("removing a card while its archive probe is pending cannot create an orphan map",async()=>{
  await withPage(async page=>{
    let release,arrived;
    const gate=new Promise(done=>{release=done;});
    const probing=new Promise(done=>{arrived=done;});
    let first=true;
    await page.route("**/local/test.pmtiles",async route=>{
      if(first && route.request().headers().range==="bytes=0-7"){
        first=false;arrived();await gate;
      }
      await route.continue();
    });
    await ready(page);await probing;
    await page.evaluate(()=>window.__card.remove());
    release();
    await page.waitForFunction(()=>!window.__card._mapCreating);
    assert.equal(await page.evaluate(()=>window.__card._map===null),true);
    assert.equal(await page.locator("gwm-journey-card").count(),0);
    await page.evaluate(()=>document.querySelector("main").append(window.__card));
    await marker(page);
  });
});
