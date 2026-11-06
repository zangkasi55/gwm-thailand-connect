import {writeFile} from "node:fs/promises";
import {resolve} from "node:path";
import {pathToFileURL} from "node:url";

function varint(value){
  if(!Number.isSafeInteger(value) || value<0)throw new Error("Unsigned safe integer required");
  const bytes=[];
  do{const digit=value%128;value=Math.floor(value/128);bytes.push(digit | (value?128:0));}while(value);
  return Buffer.from(bytes);
}
const message=(field,data)=>Buffer.concat([varint(field*8+2),varint(data.length),data]);
const integer=(field,value)=>Buffer.concat([varint(field*8),varint(value)]);
const text=(field,value)=>message(field,Buffer.from(value,"utf8"));
const packed=(field,values)=>message(field,Buffer.concat(values.map(varint)));
const zigzag=value=>value<0?-value*2-1:value*2;

function geometry(points,closed=false){
  let x=0,y=0;
  const commands=[9,zigzag(points[0][0]),zigzag(points[0][1])];
  [x,y]=points[0];
  commands.push((points.length-1)*8+2);
  for(const [nextX,nextY] of points.slice(1)){
    commands.push(zigzag(nextX-x),zigzag(nextY-y));x=nextX;y=nextY;
  }
  if(closed)commands.push(15);
  return commands;
}

function layer(name,features){
  const keys=[],values=[];
  const records=features.map((feature,index)=>{
    const tags=[];
    for(const [key,value] of Object.entries(feature.properties)){
      if(!keys.includes(key))keys.push(key);
      if(!values.includes(value))values.push(value);
      tags.push(keys.indexOf(key),values.indexOf(value));
    }
    return message(2,Buffer.concat([
      integer(1,index+1),packed(2,tags),integer(3,feature.type),
      packed(4,geometry(feature.points,feature.type===3)),
    ]));
  });
  return message(3,Buffer.concat([
    text(1,name),...records,...keys.map(value=>text(3,value)),
    ...values.map(value=>message(4,text(1,value))),integer(5,4096),integer(15,2),
  ]));
}

export function syntheticMapArchive(){
  const tile=Buffer.concat([
    layer("landcover",[{
      type:3,properties:{kind:"grass","pmap:kind":"grass",name:"Synthetic land"},
      points:[[0,0],[4096,0],[4096,4096],[0,4096]],
    }]),
    layer("roads",[
      {type:2,properties:{kind:"major_road","pmap:kind":"major_road",name:"Synthetic road"},
        points:[[0,2048],[4096,2048]]},
      {type:2,properties:{kind:"minor_road","pmap:kind":"minor_road",name:"Synthetic lane"},
        points:[[2048,0],[2048,4096]]},
    ]),
  ]);
  const maxZoom=13,addressed=(4**(maxZoom+1)-1)/3;
  const directory=Buffer.concat([varint(1),varint(0),varint(addressed),varint(tile.length),varint(1)]);
  const metadata=Buffer.from(JSON.stringify({
    name:"Synthetic map fixture",description:"Generated fictional land and roads, not OpenStreetMap or owner data",
    synthetic:true,attribution:"Synthetic fixture — no real geographic data",
    vector_layers:[{id:"landcover",fields:{kind:"String",name:"String"}},{id:"roads",fields:{kind:"String",name:"String"}}],
  }),"utf8");
  const header=Buffer.alloc(127);
  header.write("PMTiles",0,"ascii");header[7]=3;
  const tileOffset=127+directory.length+metadata.length;
  for(const [offset,value] of [
    [8,127],[16,directory.length],[24,127+directory.length],[32,metadata.length],
    [40,tileOffset],[48,0],[56,tileOffset],[64,tile.length],[72,addressed],[80,1],[88,1],
  ])header.writeBigUInt64LE(BigInt(value),offset);
  header[96]=1;header[97]=1;header[98]=1;header[99]=1;header[100]=0;header[101]=maxZoom;
  for(const [offset,value] of [[102,-1800000000],[106,-850000000],[110,1800000000],[114,850000000]]){
    header.writeInt32LE(value,offset);
  }
  return Buffer.concat([header,directory,metadata,tile]);
}

if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  if(!process.argv[2])throw new Error("Specify a new output file for the synthetic PMTiles archive");
  const archive=syntheticMapArchive();
  await writeFile(resolve(process.argv[2]),archive,{flag:"wx"});
  console.log(`Generated ${archive.length}-byte synthetic PMTiles fixture; no real map or owner data`);
}
