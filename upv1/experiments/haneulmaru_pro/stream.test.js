import test from 'node:test';
import assert from 'node:assert/strict';
import {configureSender,streamProfiles,streamDelta} from './rtc.js';
test('quality settings preserve negotiated encoding and enforce only upper limits',async()=>{
 let parameters={transactionId:'negotiated',encodings:[{rid:'main',active:true}]};
 const sender={getParameters:()=>structuredClone(parameters),setParameters:async p=>{parameters=p;}};
 await configureSender(sender,streamProfiles.ultra);
 assert.equal(parameters.transactionId,'negotiated');assert.equal(parameters.encodings[0].rid,'main');
 assert.equal(parameters.encodings[0].maxBitrate,28000000);assert.equal(parameters.encodings[0].maxFramerate,20);
 assert.equal(parameters.degradationPreference,'maintain-resolution');assert.equal(parameters.encodings[0].scaleResolutionDownBy,1);
 await assert.rejects(configureSender({getParameters:()=>({})},streamProfiles.sharp),/협상/);
});
test('measured bitrate and encode cost use counter deltas rather than targets',()=>{
 const before={timestamp:1000,bytesSent:1000000,framesEncoded:30,totalEncodeTime:.3};
 const after={timestamp:2000,bytesSent:2000000,framesEncoded:50,totalEncodeTime:.5,frameWidth:1920,frameHeight:748,qualityLimitationReason:'cpu'};
 const s=streamDelta(after,before);assert.equal(s.mbps,8);assert.equal(s.fps,20);assert.equal(s.encodeMs,10);assert.equal(s.reason,'cpu');
 assert.equal(streamDelta(after,null).mbps,0);
});
