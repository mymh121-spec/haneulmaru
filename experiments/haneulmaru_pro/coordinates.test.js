import test from 'node:test';
import assert from 'node:assert/strict';
import {NorthCoordinateSession,GyroPose} from './drone-coordinates.js';
const close=(a,b)=>assert(Math.abs(a-b)<1e-9,`${a} != ${b}`);
const qHeading=deg=>[0,-Math.sin(deg*Math.PI/360),0,Math.cos(deg*Math.PI/360)];
const pose=heading=>({quaternion:qHeading(heading),source:'fixture'});
const packet=(box=[310,230,330,250],width=640,height=480)=>({image_size_px:[width,height],tracks:[{id:1,code:'R',xyxy:box}]});

test('starting arbitrary heading is north; later directions map to ENU without distances',()=>{
 const s=new NorthCoordinateSession();s.start(pose(123));
 for(const [h,expected] of [[123,[0,1,0]],[213,[1,0,0]],[303,[0,-1,0]],[393,[-1,0,0]]]){
  const o=s.record(packet(),pose(h)).observations[0];o.direction_enu_unit.forEach((v,i)=>close(v,expected[i]));
  assert.equal(o.position_enu_m,null);assert.equal(o.range_m,null);
 }
});
test('right/up pixels, resize invariance and explicit estimated projection',()=>{
 const s=new NorthCoordinateSession();s.start(pose(0));
 const a=s.record(packet([400,100,400,100]),pose(0)),b=s.record(packet([800,200,800,200],1280,960),pose(0));
 assert(a.observations[0].direction_enu_unit[0]>0);assert(a.observations[0].direction_enu_unit[2]>0);
 assert.deepEqual(a.observations[0].direction_enu_unit,b.observations[0].direction_enu_unit);
 close(Math.hypot(...a.observations[0].direction_enu_unit),1);assert.equal(a.projection.calibrated,false);
});
test('a stationary drone keeps north while camera turns and pixels move left',()=>{
 const s=new NorthCoordinateSession({vertical_fov_deg:90});s.start(pose(0));
 const o=s.record(packet([0,320,0,320],640,640),pose(45)).observations[0];
 o.direction_enu_unit.forEach((v,i)=>close(v,[0,1,0][i]));
 close(o.bearing_deg,0);
});
test('missing/stale poses and source changes cannot yield apparently valid positions',()=>{
 const s=new NorthCoordinateSession();assert.throws(()=>s.start({...pose(0),valid:false}));s.start(pose(0));
 const out=s.record({...packet(),captured_at_ms:1000},{...pose(0),timestamp_ms:0});
 assert.equal(out.observations[0].direction_enu_unit,null);
 assert.throws(()=>s.record(packet(),{...pose(0),source:'other'}));
 s.stop();assert.throws(()=>s.record(packet(),pose(0)));
});
const model={format:'nu40-imu-direction-model',units:'degrees_per_second',directions:{up:[-1,0,0],down:[1,0,0],left:[0,1,0],right:[0,-1,0]}};
const state=(t,gyro=[0,-90,0],source='USB')=>({session:'a',source,age_ms:0,sample:{t_ms:t,gx:gyro[0],gy:gyro[1],gz:gyro[2]}});
test('slow angular rates are integrated independently of gesture threshold',()=>{
 const p=new GyroPose(model);p.consume(state(0,[0,-1,0]));p.consume(state(100,[0,-1,0]));close(p.heading,.1);
});
test('USB/BLE duplication, rollover and gaps preserve session reference',()=>{
 const p=new GyroPose(model);p.consume(state(0xfffffff0));p.consume(state(34,undefined,'BLE'));close(p.heading,4.5);
 p.consume(state(30));close(p.heading,4.5);p.consume(state(134));close(p.heading,13.5);
 assert.equal(p.consume(state(800)).valid,false);assert.equal(p.consume(state(900)).valid,false);
 p.reset();assert.equal(p.consume(state(1000)).valid,true);p.consume(state(2000));assert.equal(p.consume(state(100)).valid,false);
 p.reset();p.consume(state(150));assert.equal(p.consume(state(10)).valid,false);
});
