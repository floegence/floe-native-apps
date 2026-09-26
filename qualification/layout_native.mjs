/* Actual prepared viewer + native Xpra workarea and toolkit minimum constraints. */
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {firefox} from 'playwright';
const [receipt,address,kind,viewerAddress]=process.argv.slice(2);
const legacy=Boolean(process.env.FLOE_TEST_LEGACY_VIEWER);
const password=(await readFile(receipt.replace(/\.json$/,'.password'),'utf8')).trim();
const nativePath=receipt.replace(/\.json$/,'.layout-native.json');
const readNative=async()=>JSON.parse(await readFile(nativePath,'utf8'));
const browser=process.env.FLOE_TEST_BROWSER_WS
 ? await firefox.connect(process.env.FLOE_TEST_BROWSER_WS)
 : await firefox.launch({headless:true});
const sources=await Promise.all(['remote-input','remote-pointer'].map(name=>readFile(fileURLToPath(import.meta.resolve('@floegence/floe-webapp-core/'+name)),'utf8')));
const css=(await Promise.all(['remote-input','remote-pointer'].map(name=>readFile(fileURLToPath(import.meta.resolve('@floegence/floe-webapp-core/'+name+'.css')),'utf8')))).join('\n');
const results=[],errors=[];
let expected='';
async function expectDocument(){
 const deadline=Date.now()+8000;
 while(true){const value=JSON.parse(await readFile(receipt,'utf8'));if(value[0]===expected)return;
  assert(Date.now()<deadline,`native document differs: ${JSON.stringify(value)}`);await new Promise(resolve=>setTimeout(resolve,30));}
}
let applicationPID;
try {
 if(legacy){
  const page=await browser.newPage({viewport:{width:1440,height:920}});
  await page.goto(address+'/?password='+encodeURIComponent(password)+'&floating_menu=false&encoding=png');
  await page.waitForFunction(()=>window.floeXpraClient?.connected&&Object.values(floeXpraClient.id_to_window).length>0);
  assert.equal(await page.evaluate(()=>floeXpraInput.version),1,'legacy fixture must expose preparation v1');
  await expectDocument();
  await page.evaluate(()=>{const c=floeXpraClient,w=Object.values(c.id_to_window)[0];c.set_focus(w);c.floeInput.commitText('legacy retained ',c.floeInput.bindTarget(w.wid))});
  expected='legacy retained ';await expectDocument();applicationPID=(await readNative()).pid;
  await page.close();
 }
 for(const offscreen of [false,true]) {
  const context=await browser.newContext({viewport:{width:1440,height:920},deviceScaleFactor:2});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(viewerAddress+'/?port='+new URL(address).port+'&path=/&password='+encodeURIComponent(password)+'&floating_menu=false&encoding=png&clipboard=false&offscreen='+offscreen);
  await page.waitForFunction(()=>window.floeXpraClient?.connected&&Object.values(floeXpraClient.id_to_window).some(win=>floeXpraClient.floePointer.targetForWindow(win)),null,{timeout:25000});
  await page.evaluate(()=>{
   const c=floeXpraClient;window.layoutEvents=[];window.remoteSizes=[];
   const send=c.send;c.send=function(packet){if(['configure-display','configure-window'].includes(packet[0]))layoutEvents.push(JSON.parse(JSON.stringify(packet)));return send.call(this,packet)};
   const fit=win=>{
    if(win.override_redirect||win.tray)return;
    const dialog=win.metadata['transient-for']||win.metadata.modal||win.has_windowtype(['DIALOG']);
    if(!dialog)win.update_metadata({decorations:false});
    c.set_window_layout(win.wid,dialog?'dialog':'viewport');
    const move=win.move_resize;win.move_resize=function(...args){remoteSizes.push(args);return move.apply(this,args)};
   };
   const create=c._new_window;c._new_window=function(...args){create.apply(this,args);fit(this.id_to_window[args[0]])};
   Object.values(c.id_to_window).forEach(fit);
   window.primary=Object.values(c.id_to_window).find(win=>!win.override_redirect&&!win.tray);

  });
  await expectDocument();
  assert.deepEqual(await page.evaluate(()=>({...floeXpraViewer.capabilities(floeXpraClient)})),{display:legacy?'logical':'native',input:'ready',pointer:'ready'});
  if(legacy)assert.equal(await page.evaluate(()=>floeXpraClient.set_display_density('native')),false,'legacy display must not claim the repaired native density contract');
  await page.addStyleTag({content:css});
  await page.evaluate(async([inputSource,pointerSource])=>{
   const {createRemoteInput}=await import('data:text/javascript;base64,'+btoa(inputSource));
   const {createRemotePointer}=await import('data:text/javascript;base64,'+btoa(pointerSource));
   const client=floeXpraClient,keys=client.floeInput,adapter=client.floePointer,surface=document.querySelector('#screen');
   window.inputErrors=[];keys.onError=code=>inputErrors.push(code);
   const input=createRemoteInput({surface,label:'Layout input qualification',commitText:(text,target)=>{pointer.flush();keys.commitText(text,target)},sendKey:(key,target)=>{pointer.flush();keys.sendKey(key,target)},release:target=>keys.release(target)});
   const pointer=createRemotePointer({surface,resolveTarget:event=>adapter.resolveTarget(event),isTargetValid:target=>adapter.isTargetValid(target),sendPointer:(command,target)=>adapter.sendPointer(command,target),release:target=>adapter.release(target),onActivate:(position,target)=>{client.set_focus(target.window);input.bindTarget(keys.bindTarget(target.wid));input.setAnchor(position.clientX,position.clientY);input.focus();}});
   adapter.onInvalidate=()=>pointer.reset();
  },sources);
  const type=async text=>{
   await page.mouse.click(160,180);await page.keyboard.press('End');
   await page.locator('.floe-remote-input').evaluate((element,value)=>{
    element.dispatchEvent(new CompositionEvent('compositionstart'));element.value=value;
    element.dispatchEvent(new InputEvent('input',{inputType:'insertCompositionText',data:value,isComposing:true}));
    element.dispatchEvent(new CompositionEvent('compositionend',{data:value}));
    element.dispatchEvent(new InputEvent('input',{inputType:'insertText',data:value}));
   },text);
   expected+=text;await expectDocument();
  };
  await type('Retained 中文🙂 ');
  async function stable(policy) {
   await page.evaluate(policy=>floeXpraClient.set_display_density(policy),policy);
   let previous='',unchanged=0,record;
   for(let i=0;i<90;i++){
    await page.waitForTimeout(100);
    record=await page.evaluate(()=>({geometry:[primary.x,primary.y,primary.w,primary.h],events:layoutEvents.length,remote:remoteSizes.length,scale:floeXpraClient.scale,display:[floeXpraClient.desktop_width,floeXpraClient.desktop_height]}));
    const key=JSON.stringify(record);unchanged=key===previous?unchanged+1:0;previous=key;
    if(unchanged>=8)break;
   }
   assert(unchanged>=8,`persistent geometry feedback: ${JSON.stringify(record)}`);
   const native=await readNative();applicationPID??=native.pid;assert.equal(native.pid,applicationPID,'viewer reattach changed application PID');
   const workarea=/_NET_WORKAREA[^=]*=\s*0,\s*0,\s*(\d+),\s*(\d+)/.exec(native.root);
   assert(workarea,`missing native workarea: ${native.root}`);
   assert.deepEqual(workarea.slice(1).map(Number),record.display,'native workarea retained stale logical dimensions');
   const pixels=await page.evaluate(()=>{
    const copy=document.createElement('canvas');copy.width=4;copy.height=4;
    copy.getContext('2d').drawImage(primary.canvas,0,0,4,4);
    return [...copy.getContext('2d').getImageData(0,0,4,4).data];
   });
   if(!pixels.some((v,i)=>i%4!==3&&v>16)) {
    const samples=[];
    for(let i=0;i<30;i++) {
     await page.waitForTimeout(100);
     samples.push(await page.evaluate(()=>{
      const c=document.createElement('canvas');c.width=c.height=4;c.getContext('2d').drawImage(primary.canvas,0,0,4,4);
      return {geometry:[primary.x,primary.y,primary.w,primary.h],canvas:[primary.canvas.width,primary.canvas.height],pixels:[...c.getContext('2d').getImageData(0,0,4,4).data]};
     }));
    }
    await page.screenshot({path:receipt+'.png'});
    await writeFile(receipt.replace(/\.json$/,'.layout.json'),JSON.stringify({offscreen,policy,record,native,results,samples},null,2));
    assert.fail(`blank native image after ${policy}; subsequent pixel samples recorded`);
   }
   assert(record.geometry[2]<=Math.max(record.display[0],1240*record.scale)&&record.geometry[3]<=Math.max(record.display[1],960*record.scale), 'transient scale inflated native minimum constraints');
   results.push({offscreen,policy,...record,native,pixels});return record;
  }
  for(const policy of legacy?['logical']:['logical','native','logical','native','logical']) {
   const record=await stable(policy);
   await page.evaluate(policy=>{for(let i=0;i<30;i++)floeXpraClient.set_display_density(policy)},policy);
   await page.waitForTimeout(300);
   assert.equal(await page.evaluate(()=>layoutEvents.length),record.events,'identical settings sent layout commands');
  }
  if(!legacy){
   await page.evaluate(()=>{for(const mode of ['native','logical','native','logical','native'])floeXpraClient.set_display_density(mode)});
   await stable('native');
  }
  await page.setViewportSize({width:1280,height:800});await stable(legacy?'logical':'native');
  // Xpra cannot override a GTK minimum; the accepted geometry must still settle.
  await stable('logical');
  await type('after resize ');
  assert.deepEqual(await page.evaluate(()=>inputErrors),[]);
  if(kind==='gtk4') {
   const statePath=receipt.replace(/\.json$/,'.windows.json');
   const waitState=async check=>{
    const deadline=Date.now()+8000;
    while(true){const state=JSON.parse(await readFile(statePath,'utf8'));if(check(state))return state;
     assert(Date.now()<deadline,`native window action did not settle: ${JSON.stringify(state)}`);await page.waitForTimeout(50);}
   };
   for(const [key,field,value] of [['F6','maximized',true],['F7','maximized',false]]){
    await page.keyboard.press(key);await waitState(state=>state[field]===value);
    await page.waitForFunction(value=>primary.maximized===value,value);
   }
   for(const [key,field] of [['F2','popup_clicks'],['F3','dialog_clicks']]){
    const initial=JSON.parse(await readFile(statePath,'utf8'))[field];
    await page.keyboard.press(key);
    await page.waitForFunction(()=>Object.values(floeXpraClient.id_to_window).some(win=>win!==primary&&win.canvas));
    const target=await page.evaluate(()=>{
     const win=Object.values(floeXpraClient.id_to_window).find(win=>win!==primary&&win.canvas);
     const rect=win.canvas.getBoundingClientRect();
     return {wid:win.wid,x:rect.x+rect.width/2,y:rect.y+rect.height/2};
    });
    assert(target.x>=0&&target.y>=0&&target.x<1280&&target.y<800,'native popup action is unreachable');
    await page.mouse.click(target.x,target.y);await waitState(state=>state[field]===initial+1);
    if(key==='F2')await page.keyboard.press('Escape');
    else await page.evaluate(wid=>floeXpraClient.send_close_window(floeXpraClient.id_to_window[wid]),target.wid);
    await page.waitForFunction(wid=>!floeXpraClient.id_to_window[wid],target.wid);
    await page.mouse.click(160,180);
   }
  }
  const before=await page.evaluate(()=>primary.maximized);
  await page.evaluate(()=>primary.set_maximized(!primary.maximized));
  assert.equal(await page.evaluate(()=>primary.maximized),!before,'layout substituted a maximize state');
  assert.equal(await page.evaluate(()=>floeXpraClient.offscreen_api),offscreen,'requested canvas path was not exercised');
  await page.screenshot({path:receipt+'.png'});
  assert.deepEqual(errors,[]);
  await context.close();
 }
 await writeFile(receipt.replace(/\.json$/,'.layout.json'),JSON.stringify({browser:browser.version(),applicationPID,results},null,2));
 console.log('PASS '+kind+' stable native workarea, density transitions, repeated policy, minimum constraints and same-PID reattachment');
} finally {await browser.close();}
