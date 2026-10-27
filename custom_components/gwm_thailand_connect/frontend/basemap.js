import {escapeHtml as esc} from "./shared.js";

export const BASEMAP_URL=new URL("./thailand-basemap.json",import.meta.url);

function validGeometry(geometry){
  const depths={LineString:1,MultiLineString:2,Polygon:2,MultiPolygon:3};
  const depth=depths[geometry?.type];
  const coordinates=(value,level)=>Array.isArray(value) && (level===0
    ? value.length===2 && value.every(Number.isFinite) && Math.abs(value[0])<=180 && Math.abs(value[1])<=90
    : value.length>0 && value.every(child=>coordinates(child,level-1)));
  return depth!==undefined && coordinates(geometry.coordinates,depth);
}

export async function loadRegionalBasemap(){
  const response=await fetch(BASEMAP_URL,{credentials:"same-origin"});
  if(!response.ok)throw new Error("Bundled regional map could not be loaded");
  const data=await response.json();
  if(data.type!=="RegionalBasemap" || data.license!=="Public Domain"
    || !Array.isArray(data.bounds) || data.bounds.length!==4 || !data.bounds.every(Number.isFinite)
    || !["countries","roads","places"].every(key=>Array.isArray(data[key]))
    || ![...data.countries,...data.roads].every(feature=>typeof feature.name==="string" && validGeometry(feature.geometry))
    || !data.places.every(place=>typeof place.name==="string" && Number.isFinite(place.latitude) && Number.isFinite(place.longitude))){
    throw new Error("Bundled regional map has an invalid format");
  }
  return data;
}

function geometryPath(geometry,project){
  const line=(coordinates,closed=false)=>coordinates.map((point,index)=>{
    const p=project({longitude:point[0],latitude:point[1]});
    return `${index?"L":"M"}${p.x.toFixed(2)},${p.y.toFixed(2)}`;
  }).join("")+(closed?"Z":"");
  if(geometry.type==="LineString")return line(geometry.coordinates);
  if(geometry.type==="MultiLineString")return geometry.coordinates.map(points=>line(points)).join("");
  if(geometry.type==="Polygon")return geometry.coordinates.map(points=>line(points,true)).join("");
  if(geometry.type==="MultiPolygon")return geometry.coordinates.flatMap(polygon=>polygon.map(points=>line(points,true))).join("");
  throw new Error("Unsupported bundled map geometry");
}

export function renderRegionalBasemap(data,projection){
  const {project}=projection;
  const first=project({longitude:data.bounds[0],latitude:data.bounds[1]});
  const second=project({longitude:data.bounds[2],latitude:data.bounds[3]});
  const countries=data.countries.map(feature=>`<path class="basemap-land" d="${geometryPath(feature.geometry,project)}"
    fill="#214958" stroke="#6895a0" stroke-width="1.2" fill-rule="evenodd"><title>${esc(feature.name)}</title></path>`).join("");
  const roads=data.roads.map(feature=>`<path class="basemap-road" d="${geometryPath(feature.geometry,project)}"
    fill="none" stroke="#c1ac7a" stroke-width="1.3" opacity=".8"><title>${esc(feature.name || "Major road (regional context)")}</title></path>`).join("");
  const used=[];
  const labels=data.places.flatMap(place=>{
    const p=project(place);
    if(p.x<20 || p.x>980 || p.y<30 || p.y>430 || used.some(old=>Math.abs(old.x-p.x)<110 && Math.abs(old.y-p.y)<28))return [];
    used.push(p);
    return [`<g class="basemap-place"><circle cx="${p.x}" cy="${p.y}" r="3" fill="#d7e9e8"/><text x="${p.x+7}" y="${p.y-6}" fill="#e6f2eb" stroke="#153442" stroke-width="3" paint-order="stroke" font-size="13">${esc(place.name)}</text></g>`];
  }).join("");
  return `<defs><clipPath id="regional-coverage"><rect x="${Math.min(first.x,second.x)}" y="${Math.min(first.y,second.y)}" width="${Math.abs(second.x-first.x)}" height="${Math.abs(second.y-first.y)}"/></clipPath></defs><g class="regional-basemap" clip-path="url(#regional-coverage)"><rect width="1000" height="460" fill="#10344b"/>${countries}${roads}${labels}</g>`;
}
