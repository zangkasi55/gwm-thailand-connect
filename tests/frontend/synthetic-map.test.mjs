import assert from "node:assert/strict";
import test from "node:test";
import {PMTiles} from "pmtiles";
import {syntheticMapArchive} from "../../scripts/synthetic_map_fixture.mjs";

test("tiny generated map is deterministic, synthetic and readable by the production PMTiles decoder",async()=>{
  const archive=syntheticMapArchive();
  assert.ok(archive.length<4096);
  assert.deepEqual(archive,syntheticMapArchive());
  const source={
    getKey:()=>"synthetic-in-memory-map",
    getBytes:async(offset,length)=>({
      data:archive.buffer.slice(archive.byteOffset+offset,archive.byteOffset+Math.min(archive.length,offset+length)),
      etag:"synthetic-fixed",
    }),
  };
  const map=new PMTiles(source);
  const header=await map.getHeader(),metadata=await map.getMetadata();
  assert.equal(header.specVersion,3);
  assert.equal(header.maxZoom,13);
  assert.equal(metadata.synthetic,true);
  assert.match(metadata.description,/not OpenStreetMap or owner data/);
  const low=await map.getZxy(0,0,0);
  const high=await map.getZxy(13,6383,3779);
  assert.ok(low?.data.byteLength>100);
  assert.deepEqual(new Uint8Array(low.data),new Uint8Array(high.data));
  assert.equal(await map.getZxy(14,0,0),undefined);
});
