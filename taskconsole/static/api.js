let csrf = null;
export const setCsrf = value => { csrf = value || null; };
export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const body = options.body;
  if (body && !(body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (csrf && (options.method || 'GET') !== 'GET') headers.set('X-CSRF-Token', csrf);
  let response;
  try { response = await fetch(path, {...options, headers, credentials: 'same-origin'}); }
  catch { throw {status: 0, code: 'network', message: ''}; }
  const type = response.headers.get('content-type') || '';
  const data = options.responseType === 'blob' && response.ok ? await response.blob() : type.includes('json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = data?.detail || {};
    throw {status: response.status, code: detail.code || 'generic', message: detail.message || '', ...(typeof detail.node_id==='string'?{node_id:detail.node_id}:{}), ...(typeof detail.field==='string'?{field:detail.field}:{})};
  }
  return data;
}
export const jsonBody = value => JSON.stringify(value);
