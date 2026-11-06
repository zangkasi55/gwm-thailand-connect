import {CenteredTextSymbolizer,OffsetTextSymbolizer,leafletLayer} from "protomaps-leaflet";

export * as L from "leaflet";
export {leafletLayer} from "protomaps-leaflet";

export function placeMapLayer(options){
  const language=options.lang || "th";
  const names=[...new Set([`name:${language}`,"name","name:en"])];
  const labelProps=(zoom,feature)=>names.filter(key=>
    typeof feature.props[key]==="string" && feature.props[key].trim().length>0);
  const visible=(zoom,feature)=>{
    const minimum=feature.props.min_zoom ?? feature.props["pmap:min_zoom"];
    return labelProps(zoom,feature).length>0 && (
      typeof minimum!=="number" || Number.isFinite(minimum) && zoom>=minimum);
  };
  const layer=leafletLayer({...options,lang:language});
  layer.labelRules.push(
    {
      dataLayer:"places",minzoom:11,
      symbolizer:new CenteredTextSymbolizer({
        labelProps,font:"500 12px sans-serif",fill:"#455768",stroke:"#fff",width:2,
      }),
      filter:(zoom,feature)=>["neighbourhood","suburb","quarter","borough","macrohood"].includes(
        feature.props.kind ?? feature.props["pmap:kind"]) && visible(zoom,feature),
    },
    {
      dataLayer:"pois",minzoom:13,
      symbolizer:new OffsetTextSymbolizer({
        labelProps,font:"500 12px sans-serif",fill:"#53634c",stroke:"#fff",width:2,offsetX:4,offsetY:3,
      }),
      filter:visible,
    },
  );
  return layer;
}
