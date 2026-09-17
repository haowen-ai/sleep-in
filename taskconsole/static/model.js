export const manifestRows = manifest => Array.isArray(manifest?.parameters) ? manifest.parameters : [];
export const manifestPayload = rows => ({parameters: rows});
export const timestampOf = value => value && typeof value === 'object' ? value.created_at : value;
export function parameterRows(manifest, params={}) {
  const definitions=manifestRows(manifest);
  const keys=new Set(definitions.map(row=>row.key));
  return [
    ...definitions.map(row=>({key:row.key,value:Object.hasOwn(params,row.key)?params[row.key]:(row.default??''),required:Boolean(row.required)})),
    ...Object.entries(params).filter(([key])=>!keys.has(key)).map(([key,value])=>({key,value,required:false})),
  ];
}
