const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');
const fixture=process.env.FLOE_POINTER_FIXTURE;
function setup(precise=false) {
 const sent=[];
 const query=()=>({scrollLeft:()=>0,scrollTop:()=>0});
 const context=vm.createContext({window:{scrollX:0,scrollY:0},document:{pointerLockElement:null},jQuery:query,
  PACKET_TYPES:{pointer_position:'pointer-position',button_action:'button-action',wheel_motion:'wheel-motion'},console});
 for(const file of ['Client.js','Window.js','FloePointer.js'])vm.runInContext(readFileSync(join(fixture,'js',file),'utf8'),context);
 context.sendPacket=p=>sent.push(JSON.parse(JSON.stringify(p)));
 vm.runInContext(`
  const c=Object.create(XpraClient.prototype);
  Object.assign(c,{connected:true,scale:1,id_to_window:{},buttons_pressed:new Set(),server_precise_wheel:${precise},focused_wid:1});
  c.send=sendPacket;c._keyb_get_modifiers=e=>e.ctrlKey?['control']:[];
  c.translate_modifiers=m=>m;c.set_focus=w=>{c.focused_wid=w.wid};
  c.floePointer=new FloeXpraPointer(c);
  function add(wid=1){
   const win=Object.create(XpraWindow.prototype);
   const canvas={setAttribute(){},isConnected:true,getClientRects:()=>[{}],closest:()=>canvas};
   Object.assign(win,{client:c,wid,canvas,x:10,y:20,w:640,h:480,div:{remove(){}}});
   c.id_to_window[wid]=win;c.floePointer.register(win);return win;
  }
  globalThis.add=add;globalThis.client=c;globalThis.adapter=c.floePointer;globalThis.win=add();
 `,context);
 const {client,adapter,win}=context;
 const ready=()=>{adapter.painted(win,win.canvas);return adapter.targetForWindow(win)};
 return {client,adapter,win,sent,context,ready,add:context.add};
}
const command=(kind,extra={})=>({kind,clientX:30,clientY:50,pointerType:'touch',ctrlKey:false,shiftKey:false,metaKey:false,altKey:false,button:0,clicks:1,dx:0,dy:0,...extra});
test('first frame, readonly, hidden and local controls cannot resolve targets',()=>{
 const s=setup();assert.equal(s.adapter.targetForWindow(s.win),null);assert.equal(s.adapter.resolveTarget({target:{}}),null);
 const t=s.ready();assert.equal(s.adapter.resolveTarget({target:s.win.canvas}),t);
 s.client.server_readonly=true;assert.equal(s.adapter.isTargetValid(t),false);
 s.client.server_readonly=false;s.win.minimized=true;assert.equal(s.adapter.isTargetValid(t),false);
 s.win.minimized=false;s.win.canvas.isConnected=false;assert.equal(s.adapter.isTargetValid(t),false);
});
test('positions preserve display density while wheel distance never scales',()=>{
 for(const scale of [1,1.25,1.5,2,3]) {
  const s=setup();s.client.scale=scale;const t=s.ready();
  s.adapter.sendPointer(command('move'),t);assert.deepEqual(s.sent[0][2],[Math.round(30*scale),Math.round(50*scale),Math.round(30*scale-10),Math.round(50*scale-20)]);
  s.adapter.sendPointer(command('scroll',{dy:120}),t);assert.deepEqual(s.sent.slice(1).map(p=>p[2]),[5,5]);
 }
});
test('discrete scrolling is invariant to event partition',()=>{
 for(const deltas of [[120],[60,60],Array(12).fill(10),Array(240).fill(.5)]) {
  const s=setup(),t=s.ready();for(const dy of deltas)s.adapter.sendPointer(command('scroll',{dy}),t);
  assert.deepEqual(s.sent.map(p=>[p[2],p[3]]),[[5,true],[5,false]]);
 }
});
test('diagonal direction, reverse and sub-step remainder stay in the same target',()=>{
 const s=setup(),t=s.ready();s.adapter.sendPointer(command('scroll',{dx:-60,dy:60}),t);assert.equal(s.sent.length,0);
 s.adapter.sendPointer(command('scroll',{dx:-60,dy:60}),t);assert.deepEqual(s.sent.map(p=>p[2]),[6,6,5,5]);
 s.client.scroll_reverse_y=true;s.adapter.sendPointer(command('scroll',{dy:120}),t);assert.equal(s.sent.at(-1)[2],4);
 s.adapter.sendPointer(command('scroll',{dy:60}),t);s.adapter.release(t);s.adapter.sendPointer(command('scroll',{dy:60}),t);assert.equal(s.sent.length,6);
 const other=s.add(2);s.adapter.painted(other,other.canvas);s.adapter.sendPointer(command('scroll',{dy:60}),s.adapter.targetForWindow(other));assert.equal(s.sent.length,6);
});
test('precise wheels retain fractional wire units and both directions',()=>{
 const s=setup(true),t=s.ready();for(let i=0;i<20;i++)s.adapter.sendPointer(command('scroll',{dx:.06,dy:-.06}),t);
 assert.ok(Math.abs(s.sent.filter(p=>p[2]===7).reduce((n,p)=>n+p[3],0)+10)<=1);
 assert.ok(Math.abs(s.sent.filter(p=>p[2]===4).reduce((n,p)=>n+p[3],0)-10)<=1);
 assert.ok(Math.abs(s.adapter.scroll.x)<.12);
});
test('buttons preserve middle emulation, side mapping and release despite changed modifiers',()=>{
 const s=setup(),t=s.ready();s.client.middle_emulation_modifier='control';
 s.adapter.sendPointer(command('down',{ctrlKey:true}),t);s.adapter.sendPointer(command('up'),t);
 s.adapter.sendPointer(command('down',{button:3}),t);s.adapter.sendPointer(command('up',{button:3}),t);
 assert.deepEqual(s.sent.map(p=>p[2]),[2,2,8,8]);assert.deepEqual(s.sent[0][5],[]);
 assert.equal(s.client.buttons_pressed.size,0);assert.equal(s.client.mouseup_event.type,'mouseup');
});
test('cancel releases only owned pointer buttons at last delivered position',()=>{
 const s=setup(),t=s.ready();s.client.buttons_pressed.add(9);s.client.floeInput={release(){throw Error('Keyboard release is forbidden')}};
 s.adapter.sendPointer(command('down'),t);s.adapter.sendPointer(command('move',{clientX:80}),t);s.adapter.cancel();
 assert.equal(s.sent.at(-1)[3],false);assert.equal(s.sent.at(-1)[4][0],80);assert.deepEqual([...s.client.buttons_pressed],[9]);
});
test('geometry changes revoke tokens and release held buttons before new input',()=>{
 const s=setup(),t=s.ready();s.adapter.sendPointer(command('down'),t);let cancelled=0;s.adapter.onInvalidate=()=>cancelled++;
 s.win.x=40;s.adapter.geometryChanged(s.win);const next=s.adapter.targetForWindow(s.win);
 assert.notEqual(next,t);assert.equal(s.adapter.isTargetValid(t),false);assert.equal(s.sent.at(-1)[3],false);assert.equal(cancelled,1);
 assert.equal(s.adapter.sendPointer(command('down'),t),false);assert.ok(next);
});
test('canvas replacement rejects old decode completion until new canvas paints',()=>{
 const s=setup(),t=s.ready(),old=s.win.canvas;s.win.canvas={...old};s.adapter.register(s.win);
 s.adapter.painted(s.win,old);assert.equal(s.adapter.targetForWindow(s.win),null);assert.equal(s.adapter.isTargetValid(t),false);
 s.adapter.painted(s.win,s.win.canvas);assert.ok(s.adapter.targetForWindow(s.win));
});
test('destroy, reused window numbers and reconnect reject stale callbacks',()=>{
 const s=setup(),t=s.ready();s.win.destroy();delete s.client.id_to_window[1];const next=s.add(1);s.adapter.painted(next,next.canvas);
 assert.equal(s.adapter.isTargetValid(t),false);s.adapter.painted(s.win,s.win.canvas);assert.ok(s.adapter.targetForWindow(next));
 const nt=s.adapter.targetForWindow(next);s.adapter.disconnect();s.adapter.connected();assert.equal(s.adapter.isTargetValid(nt),false);
 assert.equal(s.adapter.sendPointer(command('down'),nt),false);assert.equal(s.sent.length,0);
});
test('popup windows and independent sessions retain their own target',()=>{
 const a=setup(),b=setup(),t=a.ready(),bt=b.ready();const popup=a.add(4);popup.override_redirect=true;a.adapter.painted(popup,popup.canvas);
 assert.equal(a.adapter.resolveTarget({target:popup.canvas}).wid,4);a.adapter.sendPointer(command('scroll',{dy:60}),t);b.adapter.sendPointer(command('scroll',{dy:60}),bt);
 assert.equal(a.sent.length+b.sent.length,0);a.adapter.cancel();b.adapter.sendPointer(command('scroll',{dy:60}),bt);assert.equal(b.sent.length,2);
});
test('invalid commands cannot send malformed packets',()=>{
 const s=setup(),t=s.ready();for(const cmd of [command('move',{clientX:NaN}),command('scroll',{dy:Infinity}),command('down',{button:7}),command('bogus')])assert.equal(s.adapter.sendPointer(cmd,t),false);
 assert.equal(s.sent.length,0);
});
