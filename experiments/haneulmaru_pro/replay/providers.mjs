import {LIMITS, validateScenario} from './core.mjs';

function validateCatalog(data) {
  if (!data || !Array.isArray(data.scenarios) || data.scenarios.length < 1 || data.scenarios.length > 20) throw new Error('시나리오 목록 형식 오류');
  const ids = new Set();
  for (const item of data.scenarios) {
    if (!item || !/^[a-z0-9-]{1,48}$/.test(item.id || '') || ids.has(item.id) || typeof item.title !== 'string' || !item.title.length || item.title.length > 100 || item.source !== 'synthetic') throw new Error('시나리오 목록 항목 오류');
    ids.add(item.id);
  }
  return data.scenarios;
}

async function readJSON(url, {token = '', fetchImpl = fetch, timeoutMs = 8000} = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {
      method: 'GET', signal: controller.signal, mode: 'cors', credentials: 'omit',
      redirect: 'error', cache: 'no-store',
      headers: token ? {Accept: 'application/json', Authorization: `Bearer ${token}`} : {Accept: 'application/json'},
    });
    if (!response.ok) throw new Error(`API 응답 ${response.status}`);
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('JSON 응답이 아닙니다.');
    if (Number(response.headers.get('content-length')) > LIMITS.bytes) throw new Error('응답이 너무 큽니다.');
    const reader = response.body?.getReader();
    if (!reader) throw new Error('응답 본문이 없습니다.');
    const chunks = []; let size = 0;
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > LIMITS.bytes) { await reader.cancel(); throw new Error('응답이 너무 큽니다.'); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let at = 0;
    for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.byteLength; }
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch (error) {
    if (controller.signal.aborted) throw new Error('API 연결 시간 초과');
    throw error;
  } finally { clearTimeout(timer); }
}

export function createLocalProvider({baseURL = new URL('./data/', import.meta.url), fetchImpl = fetch} = {}) {
  return {
    label: '기본 가상 데이터',
    async list() { return validateCatalog(await readJSON(new URL('catalog.json', baseURL), {fetchImpl})); },
    async load(id) {
      if (!/^[a-z0-9-]{1,48}$/.test(id)) throw new Error('잘못된 시나리오 ID');
      return validateScenario(await readJSON(new URL(`${id}.json`, baseURL), {fetchImpl}));
    },
  };
}

// Swap only the scenario source. The API has no camera, tracking or control route.
export function createApiProvider({baseURL, token, fetchImpl = fetch, timeoutMs = 8000}) {
  const base = new URL(baseURL);
  if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash) throw new Error('인증정보·쿼리 없는 HTTPS API 주소를 입력하세요.');
  if (typeof token !== 'string' || !token.trim() || /\s/.test(token) || token.length > 12000) throw new Error('유효한 액세스 토큰이 필요합니다.');
  base.pathname = base.pathname.replace(/\/$/, '') + '/';
  const options = {token, fetchImpl, timeoutMs};
  return {
    label: '클라우드 가상 데이터',
    async list() { return validateCatalog(await readJSON(new URL('scenarios', base), options)); },
    async load(id) {
      if (!/^[a-z0-9-]{1,48}$/.test(id)) throw new Error('잘못된 시나리오 ID');
      return validateScenario(await readJSON(new URL(`scenarios/${id}`, base), options));
    },
  };
}
