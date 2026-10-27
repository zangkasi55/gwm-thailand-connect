import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";

const registered=new Map();
globalThis.HTMLElement=class {
  attachShadow(){
    const nodes=new Map();
    this.shadowRoot={addEventListener(){},innerHTML:"",querySelector:selector=>{
      if(!nodes.has(selector))nodes.set(selector,{innerHTML:"",textContent:"",classList:{toggle(){}},checked:false});
      return nodes.get(selector);
    }};
  }
};
globalThis.customElements={get:key=>registered.get(key),define:(key,value)=>registered.set(key,value)};
const {validPosition,freshCurrent,localTileTemplate,localMapArchive,elapsed}=
  await import("../../custom_components/gwm_thailand_connect/frontend/gwm-journey-card.js");
const now=Date.now();
const current=()=>({
  availability:"available",position:{latitude:13.748,longitude:100.512},
  reported_at:new Date(now-30000).toISOString(),expires_at:new Date(now+270000).toISOString(),
});

test("map sources must be local and never send a vehicle viewport to an external provider",()=>{
  assert.equal(localTileTemplate(undefined),null);
  assert.equal(localTileTemplate("/local/offline/{z}/{x}/{y}.png"),"/local/offline/{z}/{x}/{y}.png");
  assert.equal(localMapArchive(),"/local/gwm-maps/thailand.pmtiles");
  for(const value of ["https://tiles.example.invalid/{z}/{x}/{y}.png","//host/{z}/{x}/{y}","/local/../proxy/{z}/{x}/{y}","/local/tiles.png"]){
    assert.throws(()=>localTileTemplate(value));
  }
  for(const value of ["https://example.invalid/map.pmtiles","//host/map.pmtiles","/local/../map.pmtiles","/local/map.pmtiles?key=x"]){
    assert.throws(()=>localMapArchive(value));
  }
});

test("only a recent valid manufacturer fix becomes a car marker",()=>{
  assert.equal(freshCurrent(current(),now).availability,"available");
  for(const patch of [
    {availability:"stale_sample"},{position:null},{position:{latitude:0,longitude:0}},
    {reported_at:new Date(now-3600000).toISOString()},{reported_at:new Date(now+60000).toISOString()},
    {expires_at:new Date(now-1).toISOString()},{expires_at:new Date(now+86400000).toISOString()},
    {reported_at:"broken"},{position:{latitude:100,longitude:100}},
  ])assert.equal(freshCurrent({...current(),...patch},now),null);
  assert.equal(validPosition({latitude:NaN,longitude:100}),false);
  assert.equal(freshCurrent(undefined),null);
});

test("missing and zero durations are distinct",()=>{
  assert.equal(elapsed(null),"Not reported");assert.equal(elapsed(0),"0s");assert.equal(elapsed(300),"5m 0s");
});

test("opening journey card reads only private history; no deletion or command is sent",async()=>{
  const Card=registered.get("gwm-journey-card"),card=new Card(),calls=[];
  card.setConfig({type:"custom:gwm-journey-card"});
  card.hass={connected:true,callWS:async data=>{
    calls.push(data);
    return data.type==="config/entity_registry/list"
      ? [{platform:"gwm_thailand_connect",device_id:"synthetic-device"}]
      : {status:"disabled",current:{availability:"stale_sample",position:null},stops:[],segments:[],doors:[]};
  }};
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls.map(call=>call.type),["config/entity_registry/list","gwm_thailand_connect/journeys"]);
  assert.match(card.shadowRoot.querySelector(".status").innerHTML,/No recent GPS/);
  assert.match(card.shadowRoot.querySelector(".status").innerHTML,/History recording off/);
  assert.equal(card._map,undefined);
  await card._clear();assert.equal(calls.length,2);
});

test("a late response for vehicle A cannot overwrite vehicle B history",async()=>{
  const Card=registered.get("gwm-journey-card"),card=new Card();
  card.setConfig({type:"custom:gwm-journey-card"});
  const responses=new Map();
  card._registry=[{platform:"gwm_thailand_connect",device_id:"a"},{platform:"gwm_thailand_connect",device_id:"b"}];
  card.hass={connected:true,callWS:data=>new Promise(resolve=>responses.set(data.device_id,resolve))};
  card._device="b";const second=card._load();
  responses.get("b")({status:"disabled",owner:"b"});await second;
  responses.get("a")({status:"disabled",owner:"a"});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(card._history.owner,"b");
});

test("manual Refresh requests one cached cloud read while automatic loads remain local-only",async()=>{
  const Card=registered.get("gwm-journey-card"),card=new Card(),calls=[];
  card.setConfig({type:"custom:gwm-journey-card"});
  card.hass={connected:true,callWS:async request=>{
    calls.push(request);
    return request.type==="config/entity_registry/list"
      ? [{platform:"gwm_thailand_connect",device_id:"synthetic-device"}]
      : {status:"disabled",segments:[],stops:[],doors:[]};
  }};
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.find(call=>call.type.endsWith("/journeys")).refresh_cloud,undefined);
  const target={disabled:false,dataset:{},hasAttribute:name=>name==="data-refresh"};
  card._click({target:{closest:()=>target}});
  await new Promise(resolve=>setImmediate(resolve));
  const reads=calls.filter(call=>call.type.endsWith("/journeys"));
  assert.equal(reads.length,2);
  assert.equal(reads[1].refresh_cloud,true);
});

test("compact status escapes messages and never contains fabricated defaults or CDN requests",async()=>{
  const Card=registered.get("gwm-journey-card"),card=new Card();
  card.setConfig({type:"custom:gwm-journey-card"});
  card._hass={connected:false};card._error='<img src=x onerror="bad">';card._renderStatus();
  assert.doesNotMatch(card.shadowRoot.querySelector(".status").innerHTML,/<img src=x/);
  const source=await readFile(new URL("../../custom_components/gwm_thailand_connect/frontend/gwm-journey-card.js",import.meta.url),"utf8");
  assert.doesNotMatch(source,/callService|localStorage|tile\.openstreetmap\.org|unpkg\.com|setView\(\[13\.7563/);
  assert.match(source,/confirmed:true/);
  assert.match(source,/OpenStreetMap contributors/);
});
