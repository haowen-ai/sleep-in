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

export const RUN_STATUSES = ['queued','running','cancelling','succeeded','failed','timed_out','cancelled','interrupted','skipped'];
export const executionIsLive = status => ['queued','running','cancelling'].includes(status);
export const canCancelExecution = executionIsLive;
export const endOfDay = date => date ? `${date}T23:59:59.999Z` : '';
export function paramsFromRows(rows) {
  const result={};
  for (const {key:rawKey,value} of rows) {
    const key=rawKey.trim();
    if (!key && !value) continue;
    if (!key || value === '') throw new Error('Complete both the parameter key and value.');
    if (Object.hasOwn(result,key)) throw new Error(`Duplicate parameter key: ${key}`);
    result[key]=value;
  }
  return result;
}
