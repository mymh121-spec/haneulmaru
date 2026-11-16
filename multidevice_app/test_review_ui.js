// Source-level UI behavior tests with a mock DOM; not a browser/rendering test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor() { this.value = ''; this.textContent = ''; this.children = []; this.checked = false; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  click() { this.onclick?.(); }
}
const elements = Object.fromEntries(['frames','canvas','video','seek','from','to','speed','classFilter',
  'position','session','objects','latency','imu','details','tracks','status','play','prev','next',
  'loop','clear','videoFile'].map(id => [id, new Element()]));
const hit = (id,code) => ({id,code,xyxy:[0,0,10,10],confidence:.9});
elements.frames.textContent = JSON.stringify([0,1,2].map(i => ({
  frame_sequence:i+1, frame_timestamp_ms:i*50, image_size_px:[20,20],
  session_id:i===2?'new':'s', replay_pass:0, tracks:[hit(1,'R'),hit(2,'B')],
  imu:{valid:true}, engine:{inference_ms:2}
})));
elements.from.value='1'; elements.speed.value='1';
elements.canvas.getContext = () => new Proxy({}, {get: (_,key) => key==='measureText'
  ? text=>({width:text.length*8}) : ()=>{}});
const scheduled = new Map(); let nextTimer=0;
const context = vm.createContext({document:{getElementById:id=>elements[id],
  createElement:()=>new Element(),addEventListener:()=>{}},
  setTimeout:(fn,ms)=>{scheduled.set(++nextTimer,{fn,ms});return nextTimer;},
  clearTimeout:id=>scheduled.delete(id)});
const html = fs.readFileSync(path.join(__dirname,'review_template.html'),'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
vm.runInContext(script,context);
assert.match(elements.position.textContent,/1 \/ 3/);
assert.equal(elements.tracks.children.length,2);
elements.next.click();
assert.match(elements.position.textContent,/2 \/ 3/);
elements.classFilter.value='R'; elements.classFilter.onchange();
assert.equal(elements.tracks.children.length,1);
elements.tracks.children[0].click();
assert.equal(elements.tracks.children[0].className,'selected');
elements.next.click();
assert.notEqual(elements.tracks.children[0].className,'selected'); // session reset
elements.seek.value='0'; elements.seek.oninput();
elements.speed.value='2'; elements.play.click();
assert.equal([...scheduled.values()][0].ms,25);
let tick=[...scheduled.entries()][0]; scheduled.delete(tick[0]); tick[1].fn();
assert.match(elements.position.textContent,/2 \/ 3/);
elements.play.click(); assert.equal(scheduled.size,0);
elements.seek.value='2'; elements.seek.oninput();
elements.loop.checked=true; elements.from.value='2'; elements.to.value='3';
elements.play.click(); tick=[...scheduled.entries()][0]; scheduled.delete(tick[0]); tick[1].fn();
assert.match(elements.position.textContent,/2 \/ 3/);
elements.play.click();
console.log('Review source behavior: navigation, filter, ID/session, speed, pause, loop passed');
