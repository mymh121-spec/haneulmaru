import {readFile} from 'node:fs/promises';
import {validateScenario} from './core.mjs';

const json = (statusCode, body) => ({statusCode, headers: {'content-type': 'application/json', 'cache-control': 'no-store'}, body: JSON.stringify(body)});
const read = async name => JSON.parse(await readFile(new URL(`./data/${name}.json`, import.meta.url), 'utf8'));

// JWT validation is performed by API Gateway, not by trusting a browser claim.
export async function handler(event) {
  if (!event.requestContext?.authorizer?.jwt?.claims?.sub) return json(401, {error: 'unauthorized'});
  if (event.requestContext?.http?.method !== 'GET') return json(405, {error: 'method_not_allowed'});
  try {
    const catalog = await read('catalog');
    if (event.routeKey === 'GET /scenarios') return json(200, catalog);
    if (event.routeKey === 'GET /scenarios/{id}') {
      const id = event.pathParameters?.id;
      if (!catalog.scenarios.some(s => s.id === id)) return json(404, {error: 'not_found'});
      return json(200, validateScenario(await read(id)));
    }
    return json(404, {error: 'not_found'});
  } catch {
    return json(500, {error: 'scenario_unavailable'});
  }
}
