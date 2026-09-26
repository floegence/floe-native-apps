// Construct the reviewed Xpra window with real jQuery UI. Prototype-only windows
// skip constructor listeners and cannot prove the content focus contract.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';
const config = JSON.parse(process.argv[2]);
const sources = new Map();
for (const [url, name] of Object.entries({
  '/jquery.js':'jquery', '/jquery-ui.js':'jquery-ui-dist/jquery-ui.js',
  '/input.js':'@floegence/floe-webapp-core/remote-input',
  '/pointer.js':'@floegence/floe-webapp-core/remote-pointer',
})) sources.set(url, await readFile(new URL(import.meta.resolve(name)), 'utf8'));
const css = (await Promise.all(['remote-input', 'remote-pointer'].map(name =>
  readFile(new URL(import.meta.resolve(`@floegence/floe-webapp-core/${name}.css`)), 'utf8')))).join('\n');
const html = `<!doctype html><style>${css}
html,body{margin:0}#screen{position:relative;width:780px;height:550px}
.window{position:absolute;border:1px solid #333;box-sizing:content-box}
.windowhead{height:28px;background:#bbb}.windowbuttons{float:right;display:flex;gap:4px}.windowbuttons img{width:16px;height:16px}.windowicon{display:none}.window canvas{display:block;background:#eee}
.spinneroverlay{display:none}#local{position:absolute;top:570px;left:20px}
</style><div id="screen"><div id="1"></div></div><button id="local">Local control</button>
<script src="/jquery.js"></script><script src="/jquery-ui.js"></script>
<script src="/js/FloeCanvas.js"></script><script src="/js/Client.js"></script><script src="/js/Window.js"></script><script src="/js/FloePointer.js"></script>
<script type="module">
import {createRemoteInput} from '/input.js';
import {createRemotePointer} from '/pointer.js';
window.PACKET_TYPES={pointer_position:'pointer-position',button_action:'button-action',wheel_motion:'wheel-motion'};
const c=Object.create(XpraClient.prototype);
Object.assign(c,{connected:true,scale:1,id_to_window:{},buttons_pressed:new Set(),focused_wid:1,
  debug_categories:[],desktop_width:780,desktop_height:550,debug(){},log(){},warn(){},error(){},
  _get_desktop_size:()=>[780,550],_keyb_get_modifiers:()=>[],send(){},request_refresh(){}});
c.floePointer=new FloeXpraPointer(c);
const win=new XpraWindow(c,1,50,60,480,350,{},false,false,{},()=>{},w=>{c.focused_wid=w.wid;},()=>{},1);
c.id_to_window[1]=win;c.floePointer.painted(win,win.canvas);
const surface=document.querySelector('#screen'),adapter=c.floePointer;
window.keys=[];window.commits=[];window.activations=0;window.invalidations=0;
const input=createRemoteInput({surface,label:'Fixture input',sendKey:k=>keys.push(k),commitText:t=>commits.push(t),release(){}});
const token={};input.bindTarget(token);
const pointer=createRemotePointer({surface,resolveTarget:e=>adapter.resolveTarget(e),isTargetValid:t=>adapter.isTargetValid(t),
 sendPointer:(p,t)=>adapter.sendPointer(p,t),release:t=>adapter.release(t),
 onActivate:()=>{activations++;input.focus();}});
adapter.onInvalidate=()=>{invalidations++;pointer.reset();};
window.fixture={win,adapter,pointer,input,c};
</script>`;
const server=createServer(async(req,res)=>{
  try {
    if(req.url==='/outer') {res.setHeader('Content-Type','text/html');res.end('<iframe style="width:800px;height:650px" src="/"></iframe>');return;}
    if(req.url==='/') {res.setHeader('Content-Type','text/html');res.end(html);return;}
    res.setHeader('Content-Type','text/javascript');
    if(sources.has(req.url)){res.end(sources.get(req.url));return;}
    if(['/js/Client.js','/js/Window.js','/js/FloePointer.js','/js/FloeCanvas.js'].includes(req.url)){
      res.end(await readFile(path.join(config.directory,req.url.slice(1))));return;
    }
    res.writeHead(404);res.end();
  } catch(error){res.writeHead(500);res.end(String(error));}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const results=[];
try {
 for(const name of process.env.FLOE_TEST_POINTER_BROWSERS.split(',')) {
  const browser=await {chromium,firefox,webkit}[name].launch({headless:true,...(name==='chromium'?{chromiumSandbox:true}:{})});
  try {for(const iframe of [false,true]) {
   const page=await browser.newPage({viewport:{width:900,height:750}}),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await page.goto(`http://127.0.0.1:${server.address().port}/${iframe?'outer':''}`);
   const frame=iframe?page.frames().find(f=>f.parentFrame()):page.mainFrame();
   await frame.waitForFunction(()=>window.fixture,null,{timeout:5000}).catch(e=>{throw Error(`${e.message}: ${errors}`);});
   const canvas=frame.locator('.window canvas');
   await canvas.click({position:{x:120,y:120}});
   await page.keyboard.type('abc');
   const receipt=await frame.evaluate(()=>({active:document.activeElement.tagName,keys:keys.map(k=>[k.key,k.pressed]),activations}));
   assert.equal(receipt.active,'TEXTAREA',`${name}, iframe=${iframe}: content click lost keyboard focus`);
   assert.deepEqual(receipt.keys,[['a',true],['a',false],['b',true],['b',false],['c',true],['c',false]]);
   await frame.locator('#local').click();await page.keyboard.press('Shift');
   assert.equal(await frame.evaluate(()=>keys.length),6,'local controls must own keyboard focus');
   // Decorations must still start and finish a real jQuery UI drag.
   const header=await frame.locator('.windowhead').boundingBox();
   const before=await frame.evaluate(()=>({x:fixture.win.x,y:fixture.win.y}));
   await page.mouse.move(header.x+100,header.y+10);await page.mouse.down();
   await page.mouse.move(header.x+130,header.y+40,{steps:5});await page.mouse.up();
   const moved=await frame.evaluate(()=>({x:fixture.win.x,y:fixture.win.y,grabbed:fixture.c.mouse_grabbed,invalidations}));
   assert(moved.x>before.x&&moved.y>before.y,'decoration drag must move the window');
   assert.equal(moved.grabbed,false);assert(moved.invalidations>0);
   await canvas.click({position:{x:120,y:120}});await page.keyboard.press('Shift');
   assert.equal(await frame.evaluate(()=>keys.length),8,`${name}, iframe=${iframe}: click after decoration must reactivate input`);
   assert.deepEqual(errors,[]);
   results.push({browser:name,version:browser.version(),iframe,receipt,moved});await page.close();
  }} finally {await browser.close();}
 }
 if(process.env.FLOE_TEST_POINTER_EVIDENCE){await mkdir(process.env.FLOE_TEST_POINTER_EVIDENCE,{recursive:true});await writeFile(path.join(process.env.FLOE_TEST_POINTER_EVIDENCE,`input-focus-${config.version}.json`),JSON.stringify({results},null,2));}
 console.log(`PASS ${config.version}: ${results.length} real window constructor and click-to-keyboard cases`);
} finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
