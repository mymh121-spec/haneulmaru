import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {validateScenario, sampleObject, replayFrame, historySamples, buildOctree, LIMITS} from './core.mjs';
import {createApiProvider, createLocalProvider} from './providers.mjs';
import {handler} from './aws-handler.mjs';

const data = JSON.parse(await readFile(new URL('./data/floating-shapes.json', import.meta.url), 'utf8'));
const catalog = JSON.parse(await readFile(new URL('./data/catalog.json', import.meta.url), 'utf8'));
const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'content-type': 'application/json'}});

test('both bundled scenarios satisfy the bounded synthetic contract', async () => {
  for (const item of catalog.scenarios) {
    const s = JSON.parse(await readFile(new URL(`./data/${item.id}.json`, import.meta.url), 'utf8'));
    assert.equal(validateScenario(s), s);
    assert.equal(s.id, item.id);
  }
});

test('linear replay uses nonuniform frame times and reports segment velocity', () => {
  const object = {id: 'example', frames: [{t: 0, p: [0, 0, 0]}, {t: 2, p: [4, 8, -2]}, {t: 5, p: [1, 8, 4]}]};
  assert.deepEqual(sampleObject(object, 1), {id: 'example', position: [2, 4, -1], velocity: [2, 4, -1]});
  assert.deepEqual(sampleObject(object, 3).position, [3, 8, 0]);
  assert.deepEqual(sampleObject(object, -5).position, [0, 0, 0]);
  assert.deepEqual(sampleObject(object, 50).position, [1, 8, 4]);
  assert.throws(() => sampleObject(object, NaN));
});

test('an airborne origin changes absolute height without changing relative motion', () => {
  const a = replayFrame(data, 13.5, 0), b = replayFrame(data, 13.5, 100);
  a.forEach((item, i) => {
    assert.deepEqual(item.position, b[i].position);
    assert.deepEqual(item.velocity, b[i].velocity);
    assert.ok(Math.abs(b[i].absolutePosition[1] - item.absolutePosition[1] - 100) < 1e-10);
  });
});

test('scenario validation rejects ambiguous or unbounded replay inputs', () => {
  const corruptions = [
    s => { s.source = 'live'; },
    s => { s.coordinates = 'gps'; },
    s => { s.originAltitude = Infinity; },
    s => { s.objects[1].id = s.objects[0].id; },
    s => { s.objects[0].frames[1].t = 0; },
    s => { s.objects[0].frames[0].p[0] = 1000; },
    s => { s.objects[0].frames[1].p[0] = NaN; },
    s => { s.objects[0].frames.pop(); },
    s => { s.objects[0].group = 'not-a-demo-group'; },
    s => { s.duration = 301; },
    s => { s.objects = []; },
  ];
  for (const corrupt of corruptions) {
    const s = structuredClone(data); corrupt(s); assert.throws(() => validateScenario(s));
  }
});

test('octree assigns center-plane and boundary samples exactly once', () => {
  const points = [[0, 0, 0], [-4, -4, -4], [4, 4, 4], [0, -4, 4], [4, 0, -4]];
  const leaves = buildOctree(points, {center: [0, 0, 0], size: 8}, {maxDepth: 3, capacity: 1});
  assert.equal(leaves.reduce((sum, leaf) => sum + leaf.count, 0), points.length);
  assert.ok(Math.abs(leaves.reduce((sum, leaf) => sum + leaf.fraction, 0) - 1) < 1e-12);
  assert.ok(leaves.every(leaf => leaf.depth <= 3 && leaf.count > 0));
  assert.deepEqual(buildOctree([], {center: [0, 0, 0], size: 8}), []);
  assert.throws(() => buildOctree([[5, 0, 0]], {center: [0, 0, 0], size: 8}));
});

test('sample history never includes future frames and remains bounded at time zero', () => {
  const scenario = {...data, duration: 10, objects: [{id: 'a', frames: [{t: 0, p: [0, 0, 0]}, {t: 10, p: [10, 0, 0]}]}]};
  assert.deepEqual(historySamples(scenario, 0), [[0, 0, 0]]);
  assert.deepEqual(historySamples(scenario, 10, 2, 1), [[8, 0, 0], [9, 0, 0], [10, 0, 0]]);
  assert.throws(() => historySamples(scenario, 11));
});

test('local and API providers implement the same contract; auth stays in headers', async () => {
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({url: String(url), options});
    return jsonResponse(String(url).endsWith('/scenarios') || String(url).endsWith('catalog.json') ? catalog : data);
  };
  const local = createLocalProvider({baseURL: 'https://app.test/replay/data/', fetchImpl});
  const cloud = createApiProvider({baseURL: 'https://api.test/demo/', token: 'test-access-token', fetchImpl});
  assert.deepEqual(await local.list(), await cloud.list());
  assert.deepEqual(await local.load(data.id), await cloud.load(data.id));
  const remote = requests.filter(r => r.url.includes('api.test'));
  assert.equal(remote[0].url, 'https://api.test/demo/scenarios');
  for (const request of remote) {
    assert.equal(request.options.headers.Authorization, 'Bearer test-access-token');
    assert.equal(request.options.credentials, 'omit');
    assert.equal(request.options.redirect, 'error');
    assert.ok(!request.url.includes('test-access-token'));
  }
  await assert.rejects(cloud.load('../secret'));
});

test('API source rejects insecure endpoints and blank credentials', () => {
  for (const baseURL of ['http://api.test', 'https://u:p@api.test', 'https://api.test?token=x', 'https://api.test#fragment']) {
    assert.throws(() => createApiProvider({baseURL, token: 'token'}));
  }
  assert.throws(() => createApiProvider({baseURL: 'https://api.test', token: ''}));
});

test('API errors, wrong content, invalid scenarios, duplicate IDs and oversize streams are rejected', async () => {
  const cases = [
    () => jsonResponse({}, 401),
    () => new Response('<html>error</html>', {headers: {'content-type': 'text/html'}}),
    () => jsonResponse({...data, source: 'live'}),
    () => new Response('x'.repeat(LIMITS.bytes + 1), {headers: {'content-type': 'application/json'}}),
  ];
  for (const response of cases) {
    const api = createApiProvider({baseURL: 'https://api.test', token: 'token', fetchImpl: async () => response()});
    await assert.rejects(api.load(data.id));
  }
  const api = createApiProvider({baseURL: 'https://api.test', token: 'token', fetchImpl: async () => jsonResponse({scenarios: [catalog.scenarios[0], catalog.scenarios[0]]})});
  await assert.rejects(api.list());
});

test('API timeout aborts requests rather than hanging replay controls', async () => {
  const api = createApiProvider({baseURL: 'https://api.test', token: 'token', timeoutMs: 5,
    fetchImpl: (_url, {signal}) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true}))});
  await assert.rejects(api.list(), /시간 초과/);
});

const event = (routeKey, id) => ({routeKey, pathParameters: {id}, requestContext: {authorizer: {jwt: {claims: {sub: 'demo-user'}}}, http: {method: 'GET'}}});
test('Lambda serves only allowlisted scenarios after the gateway authorizer context', async () => {
  assert.equal((await handler({})).statusCode, 401);
  const list = await handler(event('GET /scenarios'));
  assert.equal(list.statusCode, 200);
  assert.deepEqual(JSON.parse(list.body), catalog);
  const single = await handler(event('GET /scenarios/{id}', data.id));
  assert.deepEqual(JSON.parse(single.body), data);
  assert.equal((await handler(event('GET /scenarios/{id}', '../../secret'))).statusCode, 404);
  assert.equal((await handler(event('GET /control'))).statusCode, 404);
  const post = event('GET /scenarios'); post.requestContext.http.method = 'POST';
  assert.equal((await handler(post)).statusCode, 405);
});
