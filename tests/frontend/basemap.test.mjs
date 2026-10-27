import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";
import {BASEMAP_URL,loadRegionalBasemap,renderRegionalBasemap} from "../../custom_components/gwm_thailand_connect/frontend/basemap.js";

test("bundled map is public-domain regional geography, not owner history",async()=>{
  const data=JSON.parse(await readFile(BASEMAP_URL,"utf8"));
  assert.equal(data.license,"Public Domain");
  assert.equal(data.type,"RegionalBasemap");
  assert.ok(data.countries.length>0 && data.roads.length>0 && data.places.length>0);
  assert.deepEqual(data.bounds,[96,4,108,23]);
  assert.equal(data.stops,undefined);
  assert.equal(data.vin,undefined);
  assert.ok(data.sources.every(source=>/^[a-f0-9]{64}$/.test(source.sha256)));
});

test("map fetch is a fixed module-relative asset without vehicle coordinates or query values",async()=>{
  const original=globalThis.fetch;
  let calls=0;
  globalThis.fetch=async(url,options)=>{
    calls++;
    assert.equal(url.href,BASEMAP_URL.href);
    assert.equal(url.search,"");
    assert.equal(url.hash,"");
    assert.equal(options.credentials,"same-origin");
    return {ok:true,json:async()=>({type:"RegionalBasemap",license:"Public Domain",bounds:[96,4,108,23],countries:[],roads:[],places:[]})};
  };
  try{await loadRegionalBasemap();assert.equal(calls,1);}finally{globalThis.fetch=original;}
});

test("invalid map data is rejected and map labels cannot inject markup",async()=>{
  const projection={project:point=>({x:point.longitude,y:point.latitude})};
  const markup=renderRegionalBasemap({
    bounds:[-1,-1,1,1],
    countries:[{name:"<script>bad</script>",geometry:{type:"Polygon",coordinates:[[[0,0],[1,0],[1,1],[0,0]]]}}],
    roads:[],places:[],
  },projection);
  assert.doesNotMatch(markup,/<script>/);
  assert.match(markup,/&lt;script&gt;/);
  const original=globalThis.fetch;
  globalThis.fetch=async()=>({ok:true,json:async()=>({
    type:"RegionalBasemap",license:"Public Domain",
    bounds:[96,4,108,23],
    countries:[{name:"invalid",geometry:{type:"Polygon",coordinates:[[[1000,1000]]]}}],roads:[],places:[],
  })});
  try{await assert.rejects(loadRegionalBasemap(),/invalid format/);}finally{globalThis.fetch=original;}
});
