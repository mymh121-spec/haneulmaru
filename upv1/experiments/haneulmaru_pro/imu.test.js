import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DirectionInput} from './imu-direction.js';
const model=JSON.parse(readFileSync(new URL('../../multidevice_app/assets/nu40-imu-direction-training.json',import.meta.url)));
const frame=(t,source='USB',session='a',key='up')=>({source,session,age_ms:10,sample:{t_ms:t,...Object.fromEntries(['gx','gy','gz'].map((k,i)=>[k,model.directions[key][i]]))}});
test('all four trained directions work independently of training speed',()=>{
 const d=new DirectionInput(model);
 for(const [key,v] of Object.entries(model.directions))for(const speed of [25,80,300]){
  assert.equal(d.classify(v.map(x=>x/Math.hypot(...v)*speed)).key,key);
 }
 assert.equal(d.classify([0,0,0]),null);assert.equal(d.classify([NaN,1,2]),null);
});
test('every recorded repetition representative matches its trained label',()=>{
 const d=new DirectionInput(model);
 for(const [key,group] of Object.entries(model.training_metadata.directions))for(const r of group.repetitions)assert.equal(d.classify(r.gyro_dps).key,key);
});
test('idle, duplicate, delayed and reconnect samples cannot rotate the camera',()=>{
 const d=new DirectionInput(model);
 assert.equal(d.consume(frame(100)),null);
 const movement=d.consume(frame(150));assert(movement.pitch>0);assert.equal(movement.yaw,0);
 assert.equal(d.consume(frame(150)),null);
 assert.equal(d.consume(frame(1000)),null);
 assert.equal(d.consume(frame(1050,'BLE')),null);
 assert(d.consume(frame(1100,'BLE')).pitch>0);
 assert.equal(d.consume(frame(1150,'BLE','restart')),null);
 assert.equal(d.consume({...frame(1200,'BLE','restart'),age_ms:400}),null);
 assert.equal(d.consume(frame(1250,'BLE','restart')),null);
 assert.equal(d.consume({source:null,sample:null}),null);
});
test('rotation follows four requested signs and accounts for uint32 timestamp rollover',()=>{
 for(const [key,yaw,pitch] of [['up',0,1],['down',0,-1],['left',1,0],['right',-1,0]]){
  const d=new DirectionInput(model);d.consume(frame(0xfffffff0,'USB','a',key));
  const m=d.consume(frame(34,'USB','a',key));assert.equal(Math.sign(m.yaw),yaw);assert.equal(Math.sign(m.pitch),pitch);
 }
});
test('incomplete or non-finite models are rejected',()=>{
 assert.throws(()=>new DirectionInput({...model,directions:{up:[1,0,0]}}));
 assert.throws(()=>new DirectionInput({...model,rate_threshold_dps:NaN}));
});
