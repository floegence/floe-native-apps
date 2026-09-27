const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');

function fixture() {
 const callbacks=[],messages=[],paint=[],decodes=[];
 const context={imageSmoothingEnabled:true,clearRect(){},drawImage(image){paint.push(image)}};
 const canvas={width:10,height:10,getContext:()=>context};
 const scope=vm.createContext({console,performance,setTimeout:callback=>queueMicrotask(callback),importScripts(){},
  self:{postMessage:message=>messages.push({...message,painted:paint.map(image=>image.sequence)})},requestAnimationFrame:fn=>callbacks.push(fn),
  XpraImageDecoder:class {convertToBitmap(packet){return new Promise(resolve=>decodes.push(()=>{packet[6]='bitmap:png';packet[7]={sequence:packet[8]};resolve()}))}},
  XpraVideoDecoder:class {initialized=true;async queue_frame(packet){packet[6]='throttle';return packet}},
  XpraVideoDecoderLoader:{hasNativeDecoder:()=>true},canvas});
 vm.runInContext(readFileSync(process.env.FLOE_LAYOUT_FIXTURE+'/js/OffscreenDecodeWorker.js','utf8'),scope);
 const decoder=vm.runInContext('new WindowDecoder(1,canvas,false)',scope);
 const packet=sequence=>['draw',1,0,0,10,10,'png',new Uint8Array(),sequence,0,{}];
 const tick=()=>new Promise(resolve=>setImmediate(resolve));
 return {callbacks,messages,paint,decodes,decoder,packet,tick};
}

test('worker acknowledges a frame only after ordered paint completes',async()=>{
 const f=fixture();
 f.decoder.queue_draw_packet(f.packet(1));
 f.decoder.queue_draw_packet(f.packet(2));
 await f.tick();
 assert.equal(f.messages.length,0,'decoded but unpainted pixels must not unlock input');
 assert.equal(f.decodes.length,1,'the next image cannot overtake pending decode');
 f.decodes.shift()();await f.tick();
 assert.deepEqual(f.paint.map(image=>image.sequence),[1]);
 assert.deepEqual(f.messages.map(message=>message.draw[8]),[1]);
 assert.deepEqual(f.messages[0].painted,[1],'acknowledgement follows the actual paint');
 f.decodes.shift()();await f.tick();
 assert.deepEqual(f.paint.map(image=>image.sequence),[1,2]);
 assert.deepEqual(f.messages.map(message=>message.draw[8]),[1,2]);
});

test('removed window cannot report a late decode as painted',async()=>{
 const f=fixture();
 f.decoder.queue_draw_packet(f.packet(1));await f.tick();
 f.decoder.close();
 f.decodes.shift()();await f.tick();
 assert.equal(f.paint.length,0);
 assert.equal(f.messages.length,0,'retired decoder must not acknowledge a reused window number');
});

test('no-op packets retain flow control without reporting a first frame',async()=>{
 for(const coding of ['void','h264']){
  const f=fixture(),packet=f.packet(1);packet[6]=coding;
  f.decoder.queue_draw_packet(packet);await f.tick();
  assert.equal(f.messages.length,1);
  assert.equal(f.messages[0].start,0);
  assert.equal(f.paint.length,0);
 }
});
