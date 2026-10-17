import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

// Exercise the real synchronization prefix; native CI owns pixels and toolkits.
const source=await readFile(new URL('./layout_native.mjs',import.meta.url),'utf8');
const start=source.indexOf('  async function stable(policy) {');
const end=source.indexOf('   // Sample the presented image.',start);
assert(start>=0&&end>start);
const body=source.slice(source.indexOf('{',start)+1,end);
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
const stable=new AsyncFunction('page','readNative','assert','applicationPID','policy',body+'; return {record,native};');

function fixture(readyAt){
 let elapsed=0;
 const page={waitForTimeout:async ms=>{elapsed+=ms},evaluate:async()=>({geometry:[0,0,2880,1840],events:2,remote:1,scale:2,display:[2880,1840]})};
 const readNative=async()=>({pid:42,root:'_NET_WORKAREA(CARDINAL) = 0, 0, '+(elapsed>=readyAt?'2880, 1840':'1440, 920')+'\n'});
 return {run:()=>stable(page,readNative,assert,42,'native'),elapsed:()=>elapsed};
}

test('stable browser geometry waits for the independently observed native workarea',async()=>{
 const state=fixture(1500);
 const result=await state.run();
 assert.deepEqual(result.record.display,[2880,1840]);
 assert(state.elapsed()>=1500);
});

test('an unchanged stale native workarea never qualifies',async()=>{
 await assert.rejects(fixture(Infinity).run());
});
