import {build} from "esbuild";
import {copyFile,mkdir,readFile,readdir,writeFile} from "node:fs/promises";
import {resolve,dirname,sep} from "node:path";
import {fileURLToPath} from "node:url";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const output=resolve(root,"custom_components","gwm_thailand_connect","frontend","vendor");
await mkdir(output,{recursive:true});
const result=await build({
  absWorkingDir:root,
  entryPoints:["scripts/map-runtime-entry.mjs"],
  inject:["scripts/map-runtime-leaflet.mjs"],
  outfile:resolve(output,"map-runtime.js"),
  bundle:true,format:"esm",platform:"browser",target:"es2022",
  minify:true,metafile:true,legalComments:"inline",
});
await copyFile(resolve(root,"node_modules","leaflet","dist","leaflet.css"),resolve(output,"leaflet.css"));
const packages=new Set();
for(const name of Object.keys(result.metafile.inputs)){
  let directory=dirname(resolve(root,name));
  while(directory.startsWith(resolve(root,"node_modules")+sep)){
    try{
      const metadata=JSON.parse(await readFile(resolve(directory,"package.json"),"utf8"));
      if(metadata.name && metadata.version){packages.add(directory);break;}
    }catch(error){if(error.code!=="ENOENT")throw error;}
    directory=dirname(directory);
  }
}
const notices=[];
for(const directory of [...packages].sort()){
  const metadata=JSON.parse(await readFile(resolve(directory,"package.json"),"utf8"));
  const files=(await readdir(directory)).filter(name=>/^licen[cs]e(?:[.].+)?$/i.test(name));
  const supplemental={"@protomaps/basemaps":"protomaps-basemaps-LICENSE.txt",pmtiles:"pmtiles-LICENSE.txt"};
  if(!files.length && !supplemental[metadata.name])throw new Error("Missing bundled dependency license: "+metadata.name);
  const licenses=files.length ? await Promise.all(files.map(name=>readFile(resolve(directory,name),"utf8")))
    : [await readFile(resolve(root,"scripts","licenses",supplemental[metadata.name]),"utf8")];
  notices.push(`${metadata.name} ${metadata.version}\n${licenses.join("\n")}`);
}
await writeFile(resolve(output,"LICENSES.txt"),notices.join("\n\n"+"=".repeat(72)+"\n\n"),"utf8");
console.log(`Built local Leaflet/OpenStreetMap vector runtime; preserved ${packages.size} dependency license notices`);
