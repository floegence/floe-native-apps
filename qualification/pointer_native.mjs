/* Real native application receipts, delivered through the prepared Xpra client. */
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
const [receipt,address,kind]=process.argv.slice(2);
const password=(await readFile(receipt.replace(/\.json$/,'.password'),'utf8')).trim();
const source=await readFile(fileURLToPath(import.meta.resolve('@floegence/floe-webapp-core/remote-pointer')),'utf8');
const css=await readFile(fileURLToPath(import.meta.resolve('@floegence/floe-webapp-core/remote-pointer.css')),'utf8');
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.FLOE_TEST_POINTER_CHROMIUM_BIN});
const evidence=[];
try {
 const context=await browser.newContext({viewport:{width:1000,height:768},hasTouch:true});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(address+'/?password='+encodeURIComponent(password)+'&floating_menu=false&encoding=png&clipboard=false');
 await page.waitForFunction(()=>window.floeXpraClient?.connected&&Object.values(floeXpraClient.id_to_window).some(w=>floeXpraClient.floePointer.targetForWindow(w)),null,{timeout:25000});
 await page.addStyleTag({content:css});
 await page.evaluate(async source=>{
  const {createRemotePointer}=await import('data:text/javascript;base64,'+btoa(source));
  const c=floeXpraClient,a=c.floePointer;
  window.pointer=createRemotePointer({surface:document.querySelector('#screen'),resolveTarget:e=>a.resolveTarget(e),isTargetValid:t=>a.isTargetValid(t),sendPointer:(p,t)=>a.sendPointer(p,t),release:t=>a.release(t)});
  a.onInvalidate=()=>pointer.reset();
 },source);
 const cdp=await context.newCDPSession(page);
 const read=async()=>JSON.parse(await readFile(receipt,'utf8'));
 async function wait(check,label){
  const end=Date.now()+5000;let last;
  while(Date.now()<end){last=await read();if(check(last)){evidence.push({label,result:last});return last;}await new Promise(r=>setTimeout(r,30));}
  throw Error(label+' failed: '+JSON.stringify(last));
 }
 const initial=await wait(r=>Array.isArray(r.outer),'fixture ready');
 const geometry=await page.evaluate(()=>{
  const c=floeXpraClient,w=Object.values(c.id_to_window).find(w=>c.floePointer.targetForWindow(w)),r=w.canvas.getBoundingClientRect();
  return {x:r.x,y:r.y,scale:c.scale,wid:w.wid,precise:c.server_precise_wheel};
 });
 const point=(x,y)=>({x:geometry.x+x/geometry.scale,y:geometry.y+(y+initial.inset)/geometry.scale,id:1});
 async function touch(x,y,dx=0,dy=0,hold=0){
  const start=point(x,y);await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[start]});
  if(hold)await new Promise(r=>setTimeout(r,hold));
  if(dx||dy)for(let i=1;i<=12;i++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{...start,x:start.x+dx*i/12,y:start.y+dy*i/12}]});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 }
 await touch(80,310,0,-240);
 await wait(r=>r.outer[1]>0&&r.clicks===0,'swipe from button scrolls without clicking');
 await new Promise(r=>setTimeout(r,250));const stop=await read();await new Promise(r=>setTimeout(r,250));assert.deepEqual((await read()).outer,stop.outer,'Release stops scrolling');
 await page.mouse.move(point(80,220).x,point(80,220).y);await page.mouse.wheel(0,-2400);await wait(r=>r.outer[1]===0,'hardware wheel returns to top');
 await touch(460,260,-240,-160);
 await wait(r=>r.inner[0]>0&&r.inner[1]>0&&r.outer[1]===0,'diagonal nested scroll stays at initial hit point');
 await touch(80,310);await wait(r=>r.clicks===1,'single tap reaches actual button');
 await touch(80,310);await touch(80,310);await wait(r=>r.clicks>=3&&r.doubles>=1,'double tap reaches actual application');
 await touch(80,220,0,0,500);await wait(r=>r.rights>=1,'hold release delivers right click');
 await touch(80,125,120,0,500);await wait(r=>r.drag>30,'hold drag changes native slider');
 assert.deepEqual(errors,[]);
 await page.screenshot({path:receipt+'.png'});
 await writeFile(receipt.replace(/\.json$/,'.pointer.json'),JSON.stringify({browser:browser.version(),kind,geometry,physicalMobile:false,evidence},null,2));
 console.log(`PASS ${kind} native pointer: actual scroll, nested region, click, double click, right click and drag receipts`);
} finally {await browser.close();}
