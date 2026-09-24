/* GNOME's save path rebinds the focused GtkSourceView input context. */
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
const [receipt,address]=process.argv.slice(2);
const document=receipt.replace(/\.json$/,'.txt');
const password=(await readFile(receipt.replace(/\.json$/,'.password'),'utf8')).trim();
const sources=await Promise.all(['remote-input','remote-pointer'].map(name=>readFile(fileURLToPath(import.meta.resolve('@floegence/floe-webapp-core/'+name)),'utf8')));
const css=(await Promise.all(['remote-input','remote-pointer'].map(name=>readFile(fileURLToPath(import.meta.resolve('@floegence/floe-webapp-core/'+name+'.css')),'utf8')))).join('\n');
const browser=await chromium.launch({headless:true,chromiumSandbox:true});
try {
 const page=await browser.newPage({viewport:{width:1000,height:760}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(address+'/?password='+encodeURIComponent(password)+'&floating_menu=false&encoding=png&clipboard=false');
 await page.waitForFunction(()=>window.floeXpraClient?.connected&&Object.values(floeXpraClient.id_to_window).some(win=>floeXpraClient.floePointer.targetForWindow(win)),null,{timeout:25000});
 await page.addStyleTag({content:css});
 await page.evaluate(async([inputSource,pointerSource])=>{
  const {createRemoteInput}=await import('data:text/javascript;base64,'+btoa(inputSource));
  const {createRemotePointer}=await import('data:text/javascript;base64,'+btoa(pointerSource));
  const client=floeXpraClient,keys=client.floeInput,adapter=client.floePointer,surface=document.querySelector('#screen');
  window.inputErrors=[];keys.onError=code=>inputErrors.push(code);
  const input=createRemoteInput({surface,label:'Input qualification',commitText:(text,target)=>{pointer.flush();keys.commitText(text,target)},sendKey:(key,target)=>{pointer.flush();keys.sendKey(key,target)},release:target=>keys.release(target)});
  const pointer=createRemotePointer({surface,resolveTarget:event=>adapter.resolveTarget(event),isTargetValid:target=>adapter.isTargetValid(target),sendPointer:(command,target)=>adapter.sendPointer(command,target),release:target=>adapter.release(target),onActivate:(position,target)=>{client.set_focus(target.window);input.bindTarget(keys.bindTarget(target.wid));input.setAnchor(position.clientX,position.clientY);input.focus();}});
  adapter.onInvalidate=()=>pointer.reset();
 },sources);
 const canvas=page.locator('canvas').filter({visible:true}).first();
 const box=await canvas.boundingBox();
 await page.mouse.click(box.x+180,box.y+180);
 const modifier=await page.evaluate(()=>/Mac/.test(navigator.platform)?'Meta':'Control');
 const save=async expected=>{
  await page.keyboard.press(modifier+'+s');
  const deadline=Date.now()+8000;
  while(await readFile(document,'utf8')!==expected){
   assert(Date.now()<deadline,'GNOME did not save the exact expected UTF-8 bytes');
   await new Promise(resolve=>setTimeout(resolve,30));
  }
 };
 await page.keyboard.type('abc');await save('abc\n');
 const text='中文日本語한글🙂👩🏽‍💻e\u0301𠮷';
 const compose=value=>page.locator('.floe-remote-input').evaluate((element,value)=>{
  element.dispatchEvent(new CompositionEvent('compositionstart'));element.value=value;
  element.dispatchEvent(new InputEvent('input',{inputType:'insertCompositionText',data:value,isComposing:true}));
  element.dispatchEvent(new CompositionEvent('compositionend',{data:value}));
  element.dispatchEvent(new InputEvent('input',{inputType:'insertText',data:value}));
 },value);
 await compose(text);await compose(text);await save('abc'+text+text+'\n');
 await page.keyboard.press(modifier+'+a');await compose(text+'\nsecond');
 await page.keyboard.press('Enter');await page.keyboard.type('last');
 const expected=text+'\nsecond\nlast\n';await save(expected);
 assert.deepEqual(await page.evaluate(()=>window.inputErrors),[]);assert.deepEqual(errors,[]);
 assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('floe-remote-input')),true);
 await page.screenshot({path:receipt+'.png'});
 await writeFile(receipt,JSON.stringify({browser:browser.version(),expected,composition:'simulated',checks:['click then ASCII','save then repeated Unicode','selection replacement','multiline then Enter','saved bytes']},null,2));
 console.log('PASS gnome click, save, repeated Unicode, multiline and exact saved bytes');
} finally {await browser.close();}
