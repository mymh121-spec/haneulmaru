import * as T from 'three';
import {validateScenario, sampleObject, replayFrame, historySamples, buildOctree} from './core.mjs';
import {createLocalProvider, createApiProvider} from './providers.mjs';

const $ = id => document.getElementById(id);
const colors = {'demo-a': 0x71dfcc, 'demo-b': 0x87b7ff, unreviewed: 0xe2bd7c};
const groups = {'demo-a': '가상 그룹 A', 'demo-b': '가상 그룹 B', unreviewed: '미확인 설정'};
let provider = createLocalProvider(), scenario, time = 0, playing = true, origin = 100, selected = '';
let previous = null, statsAt = -1, cellKey = '', busy = false, renderer;
const scene = new T.Scene(), visuals = new T.Group(), paths = new T.Group(), cells = new T.Group();
scene.add(visuals, paths, cells, new T.HemisphereLight(0xe0f2ff, 0x263344, 2.4));
const light = new T.DirectionalLight(0xffffff, 2); light.position.set(50, 120, 80); scene.add(light);
const camera = new T.PerspectiveCamera(43, 1, .1, 3000);
const grid = new T.GridHelper(240, 12, 0x376977, 0x234351); scene.add(grid);
const axes = new T.AxesHelper(22); scene.add(axes);
const models = new Map();
let boundary = null;

function clearGroup(group) {
  for (const child of [...group.children]) {
    child.traverse(o => {
      o.geometry?.dispose();
      if (Array.isArray(o.material)) o.material.forEach(m => m.dispose()); else o.material?.dispose();
    });
    group.remove(child);
  }
}

function resize() {
  if (!renderer) return;
  const rect = $('scene').getBoundingClientRect();
  renderer.setSize(rect.width, rect.height, false);
  camera.aspect = rect.width / Math.max(1, rect.height);
  camera.updateProjectionMatrix();
}

class AuthoredPath extends T.Curve {
  constructor(object, duration) { super(); this.object = object; this.duration = duration; }
  getPoint(t, target = new T.Vector3()) { return target.fromArray(sampleObject(this.object, t * this.duration).position); }
}

function fitCamera() {
  if (!scenario) return;
  const center = new T.Vector3(...scenario.bounds.center), size = scenario.bounds.size;
  const distance = size * (camera.aspect < .85 ? 2.3 : 1.55);
  camera.position.copy(center).add(new T.Vector3(.9, .65, 1).normalize().multiplyScalar(distance));
  camera.lookAt(center);
}

function mount(data) {
  scenario = validateScenario(data); time = 0; playing = true; previous = null;
  selected = scenario.objects[0].id; origin = scenario.originAltitude;
  $('origin').value = origin; $('originLabel').textContent = `${origin} m`;
  $('timeline').max = scenario.duration; $('durationLabel').textContent = `${scenario.duration}초`;
  $('play').textContent = '일시정지';
  clearGroup(visuals); clearGroup(paths); clearGroup(cells); models.clear();
  if (boundary) { scene.remove(boundary); boundary.geometry.dispose(); boundary.material.dispose(); }
  const box = new T.BoxGeometry(scenario.bounds.size, scenario.bounds.size, scenario.bounds.size);
  boundary = new T.LineSegments(new T.EdgesGeometry(box), new T.LineBasicMaterial({color: 0x38556a, transparent: true, opacity: .65}));
  box.dispose(); boundary.position.fromArray(scenario.bounds.center); scene.add(boundary);
  const buttons = [];
  for (const object of scenario.objects) {
    const geometry = object.shape === 'sphere' ? new T.SphereGeometry(3, 18, 12) : object.shape === 'box' ? new T.BoxGeometry(5, 5, 5) : new T.OctahedronGeometry(4);
    const material = new T.MeshStandardMaterial({color: colors[object.group], roughness: .38, metalness: .1, emissive: colors[object.group], emissiveIntensity: .12});
    const mesh = new T.Mesh(geometry, material); visuals.add(mesh); models.set(object.id, mesh);
    const curve = new AuthoredPath(object, scenario.duration);
    const tube = new T.Mesh(new T.TubeGeometry(curve, Math.min(300, object.frames.length * 4), object.radius, 6, false), new T.MeshBasicMaterial({color: colors[object.group], transparent: true, opacity: .12, depthWrite: false}));
    const line = new T.Line(new T.BufferGeometry().setFromPoints(object.frames.map(f => new T.Vector3(...f.p))), new T.LineBasicMaterial({color: colors[object.group], transparent: true, opacity: .5}));
    paths.add(tube, line);
    const button = document.createElement('button'); button.textContent = object.name;
    button.setAttribute('aria-pressed', String(object.id === selected));
    button.onclick = () => {
      selected = object.id;
      for (const b of $('objects').children) b.setAttribute('aria-pressed', String(b === button));
      statsAt = -1;
    };
    buttons.push(button);
  }
  $('objects').replaceChildren(...buttons);
  grid.position.y = -origin; cellKey = ''; statsAt = -1; fitCamera(); drawState();
}

function drawState() {
  if (!scenario) return;
  const frame = replayFrame(scenario, time, origin);
  for (const sample of frame) {
    const mesh = models.get(sample.id); mesh.position.fromArray(sample.position);
    mesh.material.emissiveIntensity = sample.id === selected ? .5 : .08;
  }
  $('timeline').value = time; $('timeLabel').textContent = `${time.toFixed(1).padStart(4, '0')} s`;
  paths.visible = $('paths').checked; cells.visible = $('cells').checked;
  if (statsAt < 0 || Math.abs(time - statsAt) >= .1) {
    statsAt = time;
    const item = frame.find(s => s.id === selected), object = scenario.objects.find(o => o.id === selected);
    const fields = [ ['객체', object.name], ['설정', groups[object.group]], ['XYZ (m)', item.position.map(n => n.toFixed(1)).join(' / ')], ['VXYZ (m/s)', item.velocity.map(n => n.toFixed(1)).join(' / ')], ['기준면 높이', `${item.absolutePosition[1].toFixed(1)} m`], ['경로 반경', `${object.radius} m · 작성값`] ];
    $('details').replaceChildren(...fields.flatMap(([key, value]) => {
      const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = key; dd.textContent = value; return [dt, dd];
    }));
  }
  const nextKey = `${Math.floor(time * 5)}:${$('depth').value}`;
  if (cells.visible && cellKey !== nextKey) {
    cellKey = nextKey; clearGroup(cells);
    const leaves = buildOctree(historySamples(scenario, time), scenario.bounds, {maxDepth: +$('depth').value});
    for (const leaf of leaves) {
      const geometry = new T.BoxGeometry(leaf.size, leaf.size, leaf.size);
      const edge = new T.LineSegments(new T.EdgesGeometry(geometry), new T.LineBasicMaterial({color: 0x77afc5, transparent: true, opacity: Math.min(.6, .12 + leaf.fraction)}));
      const mesh = new T.Mesh(geometry, new T.MeshBasicMaterial({color: 0x418aa6, transparent: true, opacity: Math.min(.14, .015 + leaf.fraction * .18), depthWrite: false}));
      edge.position.fromArray(leaf.center); mesh.position.copy(edge.position); cells.add(mesh, edge);
    }
    $('cellSummary').textContent = `표시 셀 ${leaves.length}개 · 최근 최대 8초의 가상 샘플 · 각 샘플은 한 셀에만 포함`;
  }
}

function setBusy(value) {
  busy = value;
  for (const id of ['connect', 'disconnect', 'scenario']) $(id).disabled = value;
  for (const id of ['play', 'restart', 'timeline']) $(id).disabled = !scenario;
}

async function switchProvider(candidate) {
  if (busy) return;
  setBusy(true); $('status').textContent = '시나리오를 확인하는 중입니다.';
  try {
    const catalog = await candidate.list(), data = await candidate.load(catalog[0].id);
    if (data.id !== catalog[0].id) throw new Error('목록과 시나리오 ID가 다릅니다.');
    mount(data); provider = candidate;
    $('scenario').replaceChildren(...catalog.map(item => {
      const option = document.createElement('option'); option.value = item.id; option.textContent = item.title; return option;
    }));
    $('sourceLabel').textContent = provider.label;
    $('status').textContent = `${provider.label} 연결 완료. 사전 작성 시나리오를 재생합니다.`;
  } catch (error) { $('status').textContent = `불러오기 실패: ${error.message}${scenario ? ' · 기존 장면 유지' : ' · 기본 데이터로 다시 시도하세요.'}`; }
  finally { setBusy(false); }
}

$('scenario').onchange = async () => {
  const id = $('scenario').value; setBusy(true);
  try {
    const data = await provider.load(id);
    if (data.id !== id) throw new Error('시나리오 ID가 다릅니다.');
    mount(data); $('status').textContent = `${data.title} 재생 중`;
  } catch (error) { $('scenario').value = scenario?.id || ''; $('status').textContent = `불러오기 실패: ${error.message} · 기존 장면 유지`; }
  finally { setBusy(false); }
};
$('play').onclick = () => { if (time >= scenario.duration) time = 0; playing = !playing; previous = null; $('play').textContent = playing ? '일시정지' : '재생'; };
$('restart').onclick = () => { time = 0; previous = null; statsAt = -1; cellKey = ''; drawState(); };
$('timeline').oninput = () => { time = +$('timeline').value; playing = false; $('play').textContent = '재생'; statsAt = -1; drawState(); };
$('origin').oninput = () => { origin = +$('origin').value; grid.position.y = -origin; $('originLabel').textContent = `${origin} m`; statsAt = -1; drawState(); };
for (const id of ['depth', 'cells', 'paths']) $(id).onchange = drawState;
$('apiForm').onsubmit = async event => {
  event.preventDefault();
  try {
    const candidate = createApiProvider({baseURL: $('endpoint').value.trim(), token: $('token').value.trim()});
    $('token').value = ''; await switchProvider(candidate);
  } catch (error) { $('status').textContent = error.message; }
  finally { $('token').value = ''; }
};
$('disconnect').onclick = () => { $('token').value = ''; switchProvider(createLocalProvider()); };
document.addEventListener('visibilitychange', () => { previous = null; });
window.addEventListener('pagehide', () => { $('token').value = ''; renderer?.setAnimationLoop(null); });
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
try {
  renderer = new T.WebGLRenderer({canvas: $('scene'), alpha: true, antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.outputColorSpace = T.SRGBColorSpace;
  new ResizeObserver(() => { resize(); fitCamera(); }).observe($('scene').parentElement);
  resize();
  $('scene').addEventListener('webglcontextlost', event => {
    event.preventDefault(); renderer.setAnimationLoop(null);
    $('sceneError').hidden = false; $('sceneError').textContent = '그래픽 연결이 끊겼습니다. 페이지를 새로고침하세요.';
  });
  renderer.setAnimationLoop(now => {
    const dt = previous === null ? 0 : Math.min((now - previous) / 1000, .1); previous = now;
    if (scenario && playing && !document.hidden) {
      time = Math.min(scenario.duration, time + dt * +$('speed').value);
      if (time >= scenario.duration) { playing = false; $('play').textContent = '재생'; }
    }
    drawState(); renderer.render(scene, camera);
  });
  switchProvider(provider);
} catch {
  $('sceneError').hidden = false; $('sceneError').textContent = '이 브라우저에서 3D 화면을 시작할 수 없습니다. WebGL을 지원하는 브라우저에서 다시 열어 주세요.';
  $('status').textContent = '그래픽 초기화 실패';
}
