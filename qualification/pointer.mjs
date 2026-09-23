import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {chromium,firefox,webkit} from 'playwright';
const config=JSON.parse(process.argv[2]);
const script=await readFile(fileURLToPath(import.meta.resolve('@floegence/floe-webapp-core/remote-pointer')),'utf8');
const css=await readFile(fileURLToPath(import.meta.resolve('@floegence/floe-webapp-core/remote-pointer.css')),'utf8');
const html=`<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}body{margin:0}#screen{height:500px}canvas{width:400px;height:500px}#sink{height:100px;width:300px;overflow:auto}#inside{height:3000px;width:3000px}</style>
<div id="screen"><canvas></canvas></div><div id="sink"><div id="inside"></div></div><button id="local">Local control</button>
<script src="/js/Client.js"></script><script src="/js/Window.js"></script><script src="/js/FloePointer.js"></script>
<script type="module">
import {createRemotePointer} from '/pointer.js';
window.jQuery=()=>({scrollLeft:()=>0,scrollTop:()=>0});window.PACKET_TYPES={pointer_position:'pointer-position',button_action:'button-action',wheel_motion:'wheel-motion'};
const c=Object.create(XpraClient.prototype),win=Object.create(XpraWindow.prototype),canvas=document.querySelector('canvas');
Object.assign(win,{client:c,wid:1,canvas,x:0,y:0,w:400,h:500,div:document.querySelector('#screen')});
Object.assign(c,{connected:true,scale:1,id_to_window:{1:win},buttons_pressed:new Set(),focused_wid:1});
window.packets=[];c.send=p=>{packets.push(p);if(p[0]==='button-action'&&p[3]){const sink=document.querySelector('#sink');if(p[2]===5)sink.scrollTop+=120;if(p[2]===4)sink.scrollTop-=120;if(p[2]===7)sink.scrollLeft+=120;if(p[2]===6)sink.scrollLeft-=120;}};
c._keyb_get_modifiers=e=>e.ctrlKey?['control']:[];c.set_focus=w=>c.focused_wid=w.wid;c.floePointer=new FloeXpraPointer(c);const adapter=c.floePointer;adapter.register(win);adapter.painted(win,canvas);
window.activations=0;window.holds=[];
window.pointer=createRemotePointer({surface:document.querySelector('#screen'),resolveTarget:e=>adapter.resolveTarget(e),isTargetValid:t=>adapter.isTargetValid(t),sendPointer:(p,t)=>adapter.sendPointer(p,t),release:t=>adapter.release(t),onActivate:()=>activations++,onHoldChange:p=>holds.push(Boolean(p))});
adapter.onInvalidate=()=>pointer.reset();window.fixture={c,win,adapter};
</script>`;
const server=createServer(async(req,res)=>{
 res.setHeader('Content-Type',req.url==='/'?'text/html':'text/javascript');
 if(req.url==='/')return res.end(html);
 if(req.url==='/pointer.js')return res.end(script);
 if(!['/js/Client.js','/js/Window.js','/js/FloePointer.js'].includes(req.url)){res.writeHead(404);res.end();return;}
 res.end(await readFile(path.join(config.directory,req.url.slice(1))));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const results=[];
try {
 for(const name of process.env.FLOE_TEST_POINTER_BROWSERS.split(',')) {
  const browser=await {chromium,firefox,webkit}[name].launch({headless:true,...(name==='chromium'?{chromiumSandbox:true}:{})});
  try {for(const dpr of [1,1.25,1.5,2,3]) {
   const context=await browser.newContext({viewport:{width:420,height:740},deviceScaleFactor:dpr,hasTouch:true});
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(`http://127.0.0.1:${server.address().port}/`);await page.waitForFunction(()=>window.pointer);
   const receipt=await page.evaluate(async()=>{
    const canvas=document.querySelector('canvas'),surface=document.querySelector('#screen'),sink=document.querySelector('#sink');
    const capture=surface.setPointerCapture;surface.setPointerCapture=()=>{};
    const event=(type,x,y,id=1)=>canvas.dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,pointerType:'touch',pointerId:id,clientX:x,clientY:y}));
    event('pointerdown',300,400);for(let n=1;n<=12;n++)event('pointermove',300-n*10,400-n*20);event('pointerup',180,160);
    await new Promise(r=>requestAnimationFrame(r));
    const scroll={top:sink.scrollTop,left:sink.scrollLeft,activations,packets:packets.splice(0)};
    event('pointerdown',60,60);event('pointerup',60,60);event('pointerdown',60,60);event('pointerup',60,60);const taps=packets.splice(0);
    event('pointerdown',60,60);await new Promise(r=>setTimeout(r,470));event('pointerup',60,60);const right=packets.splice(0);
    event('pointerdown',60,60);await new Promise(r=>setTimeout(r,470));event('pointermove',80,90);event('pointerup',90,100);const drag=packets.splice(0);
    event('pointerdown',100,200);event('pointermove',100,150);event('pointerdown',120,150,2);event('pointerup',100,150);event('pointerup',120,150,2);const pinch=packets.splice(0);
    event('pointerdown',100,200);event('pointermove',100,50);fixture.win.x++;fixture.adapter.geometryChanged(fixture.win);event('pointerup',100,50);const stale=packets.splice(0);
    surface.setPointerCapture=capture;return {scroll,taps,right,drag,pinch,stale};
   });
   assert.equal(receipt.scroll.top,240);assert.equal(receipt.scroll.left,120);assert.equal(receipt.scroll.activations,0);
   assert.deepEqual(receipt.taps.map(p=>[p[2],p[3]]),[[1,true],[1,false],[1,true],[1,false]]);
   assert.deepEqual(receipt.right.map(p=>[p[2],p[3]]),[[3,true],[3,false]]);
   assert.deepEqual(receipt.drag.map(p=>p[0]),['button-action','pointer-position','button-action']);
   assert.deepEqual(receipt.drag[0][4],[60,60,60,60]);assert.deepEqual(receipt.drag.at(-1)[4],[90,100,90,100]);
   assert.deepEqual(receipt.pinch,[]);assert.deepEqual(receipt.stale,[]);
   await page.mouse.move(80,90);await page.mouse.down();await page.mouse.move(100,120);await page.mouse.up();
   await page.mouse.wheel(0,480);
   try {await page.waitForFunction(()=>packets.some(p=>p[2]===5),null,{timeout:3000});}
   catch(e){console.log(name,dpr,await page.evaluate(()=>({packets,scroll:fixture.adapter.scroll,valid:!!fixture.adapter.targetForWindow(fixture.win),hit:document.elementFromPoint(100,120)?.tagName})));throw e;}
   assert.deepEqual(errors,[]);results.push({browser:name,version:browser.version(),dpr,receipt});await context.close();
  }} finally {await browser.close();}
 }
 if(process.env.FLOE_TEST_POINTER_EVIDENCE){await mkdir(process.env.FLOE_TEST_POINTER_EVIDENCE,{recursive:true});await writeFile(path.join(process.env.FLOE_TEST_POINTER_EVIDENCE,`browser-${config.version}.json`),JSON.stringify({physicalDevice:false,results},null,2));}
 console.log(`PASS ${config.version}: ${results.length} controller/adapter browser cases with scroll sink receipts`);
} finally {server.closeAllConnections();await new Promise(r=>server.close(r));}
