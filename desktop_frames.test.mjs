import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const Frames=vm.runInNewContext(readFileSync(new URL('./desktop_frames.js',import.meta.url),'utf8')+';FloeDesktopFrames');
const drain=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function fixture(){
 const draws=[],painted=[],errors=[],jobs=[],closed=[];
 const canvas={width:20,height:10,getContext:()=>({drawImage:(image,x,y)=>draws.push([image.id,x,y])})};
 let current=1;
 const frames=new Frames({canvas,isValid:p=>p.target===current,onResize:()=>{},onPaint:p=>painted.push(p.meta.sequence),onError:(_,e)=>errors.push(e.message),
  decode:p=>new Promise(resolve=>jobs.push(()=>resolve({id:p.meta.sequence,width:p.meta.region_width||20,height:p.meta.region_height||10,close:()=>closed.push(p.meta.sequence)})))});
 const packet=(sequence,meta={})=>({target:current,meta:{encoding:'png',width:20,height:10,sequence,...meta},bytes:new Uint8Array()});
 return {frames,packet,draws,painted,errors,jobs,closed,retire:()=>{current++;frames.invalidate();}};
}
test('composes two in-flight regions in order without dropping their reference',async()=>{
 const f=fixture();f.frames.receive(f.packet(1));f.frames.receive(f.packet(2,{base:1,x:4,y:2,region_width:3,region_height:2}));
 assert.equal(f.jobs.length,1);f.jobs.shift()();await drain();assert.equal(f.jobs.length,1);f.jobs.shift()();await drain();
 assert.deepEqual(f.draws,[[1,0,0],[2,4,2]]);assert.deepEqual(f.painted,[1,2]);assert.deepEqual(f.closed,[1,2]);assert.deepEqual(f.errors,[]);
});
test('never paints or acknowledges a retired asynchronous decode',async()=>{
 const f=fixture();f.frames.receive(f.packet(1));f.retire();f.frames.receive(f.packet(2));f.jobs.shift()();await drain();f.jobs.shift()();await drain();
 assert.deepEqual(f.painted,[2]);assert.deepEqual(f.closed,[1,2]);
});
test('rejects orphaned damage and excessive decode work',async()=>{
 const f=fixture();f.frames.receive(f.packet(2,{base:1,x:1,region_width:2,region_height:2}));await drain();assert.equal(f.errors.length,1);assert.equal(f.jobs.length,0);
 f.frames.receive(f.packet(3));f.frames.receive(f.packet(4));f.frames.receive(f.packet(5));assert.equal(f.errors.length,2);
 f.jobs.shift()();await drain();f.jobs.shift()();await drain();
});

test('accepts two new target frames while a retired bitmap finishes', async()=>{
 const f=fixture();f.frames.receive(f.packet(1));f.retire();
 f.frames.receive(f.packet(2));f.frames.receive(f.packet(3,{base:2,x:2,y:2,region_width:2,region_height:2}));
 assert.deepEqual(f.errors,[]);f.jobs.shift()();await drain();f.jobs.shift()();await drain();f.jobs.shift()();await drain();
 assert.deepEqual(f.painted,[2,3]);assert.deepEqual(f.closed,[1,2,3]);
});

test('rejects a full image with a dependency and a replayed sequence',async()=>{
 const f=fixture();f.frames.receive(f.packet(1));f.jobs.shift()();await drain();
 f.frames.receive(f.packet(1));await drain();assert.equal(f.errors.length,1);
 f.frames.receive(f.packet(2,{base:1}));await drain();assert.equal(f.errors.length,2);
});
