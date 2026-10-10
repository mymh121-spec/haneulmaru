// Pure, deterministic replay of authored examples. No sensor or detection input.
export const LIMITS = Object.freeze({objects: 24, frames: 601, duration: 300, bytes: 1000000});
const finite = (n, lo, hi) => typeof n === 'number' && Number.isFinite(n) && n >= lo && n <= hi;
const vector = (v, lo, hi) => Array.isArray(v) && v.length === 3 && v.every(n => finite(n, lo, hi));
const fail = message => { throw new Error(`시나리오 형식 오류: ${message}`); };
export function validateScenario(s) {
  if (!s || s.schemaVersion !== 1 || s.source !== 'synthetic' || s.coordinates !== 'local-xyz-y-up-m') fail('가상 XYZ v1 데이터만 지원합니다.');
  if (!/^[a-z0-9-]{1,48}$/.test(s.id || '') || typeof s.title !== 'string' || s.title.length < 1 || s.title.length > 100) fail('이름');
  if (!finite(s.duration, 1, LIMITS.duration) || !finite(s.originAltitude, 0, 150)) fail('시간 또는 원점');
  if (!s.bounds || !vector(s.bounds.center, -500, 500) || !finite(s.bounds.size, 1, 500)) fail('공간 경계');
  if (!Array.isArray(s.objects) || s.objects.length < 1 || s.objects.length > LIMITS.objects) fail('객체 수');
  const ids = new Set();
  for (const o of s.objects) {
    if (!/^[a-z0-9-]{1,48}$/.test(o.id || '') || ids.has(o.id)) fail('중복 또는 잘못된 객체 ID');
    ids.add(o.id);
    if (typeof o.name !== 'string' || !o.name.length || o.name.length > 60) fail('객체 이름');
    if (!['sphere', 'box', 'octahedron'].includes(o.shape) || !['demo-a', 'demo-b', 'unreviewed'].includes(o.group)) fail('가상 객체 유형');
    if (!finite(o.radius, 0.2, 8) || !Array.isArray(o.frames) || o.frames.length < 2 || o.frames.length > LIMITS.frames) fail('경로');
    let previous = -1;
    for (const f of o.frames) {
      if (!finite(f.t, 0, s.duration) || f.t <= previous || !vector(f.p, -1000, 1000)) fail('프레임 순서 또는 좌표');
      if (f.p.some((n, i) => Math.abs(n - s.bounds.center[i]) > s.bounds.size / 2)) fail('공간 경계 밖의 좌표');
      previous = f.t;
    }
    if (o.frames[0].t !== 0 || o.frames.at(-1).t !== s.duration) fail('처음과 마지막 프레임');
  }
  return s;
}

export function sampleObject(object, time) {
  if (!Number.isFinite(time)) throw new Error('시간은 유한한 수여야 합니다.');
  const frames = object.frames, t = Math.max(0, Math.min(frames.at(-1).t, time));
  let lo = 0, hi = frames.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t <= t) lo = mid; else hi = mid;
  }
  const a = frames[lo], b = frames[hi], dt = b.t - a.t, u = (t - a.t) / dt;
  return {
    id: object.id,
    position: a.p.map((n, i) => n + (b.p[i] - n) * u),
    velocity: a.p.map((n, i) => (b.p[i] - n) / dt),
  };
}

export function replayFrame(scenario, time, originAltitude = scenario.originAltitude) {
  if (!finite(originAltitude, 0, 150)) throw new Error('원점 높이는 0~150m입니다.');
  return scenario.objects.map(o => {
    const sample = sampleObject(o, time);
    return {...sample, absolutePosition: sample.position.map((n, i) => n + (i === 1 ? originAltitude : 0))};
  });
}

export function historySamples(scenario, time, windowSeconds = 8, step = 0.5) {
  if (!finite(time, 0, scenario.duration) || !finite(windowSeconds, 0, 30) || !finite(step, 0.1, 5)) throw new Error('샘플 범위 오류');
  const result = [], start = Math.max(0, time - windowSeconds);
  for (const o of scenario.objects) {
    for (let t = start; t < time; t += step) result.push(sampleObject(o, t).position);
    result.push(sampleObject(o, time).position);
  }
  return result;
}

// Counts authored samples in occupied leaves; this is NOT Bayesian occupancy,
// a confidence estimate, a tracker, or a future-position predictor.
export function buildOctree(points, bounds, {maxDepth = 2, capacity = 5} = {}) {
  if (!Number.isInteger(maxDepth) || maxDepth < 0 || maxDepth > 4 || !Number.isInteger(capacity) || capacity < 1) throw new Error('분할 설정 오류');
  if (!bounds || !vector(bounds.center, -500, 500) || !finite(bounds.size, 1, 500)) throw new Error('공간 경계 오류');
  if (!Array.isArray(points) || points.length > 10000 || points.some(p => !vector(p, -1000, 1000) || p.some((n, i) => Math.abs(n - bounds.center[i]) > bounds.size / 2))) throw new Error('샘플 좌표 오류');
  const leaves = [], total = points.length;
  function split(items, center, size, depth) {
    if (!items.length) return;
    if (depth === maxDepth || items.length <= capacity) {
      leaves.push({center, size, depth, count: items.length, fraction: items.length / total});
      return;
    }
    const buckets = Array.from({length: 8}, () => []);
    for (const p of items) {
      const key = (p[0] >= center[0] ? 1 : 0) | (p[1] >= center[1] ? 2 : 0) | (p[2] >= center[2] ? 4 : 0);
      buckets[key].push(p);
    }
    buckets.forEach((bucket, key) => split(bucket, center.map((n, i) => n + ((key & (1 << i)) ? 1 : -1) * size / 4), size / 2, depth + 1));
  }
  split(points, [...bounds.center], bounds.size, 0);
  return leaves;
}
