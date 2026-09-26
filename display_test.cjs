const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function setup(dpr=2) {
 const scheduled=[];const flush=()=>{for(const fn of scheduled.splice(0))fn()};
 const listeners=new Set(),packets=[],shadow={style:{}};
 const browser={devicePixelRatio:dpr,matchMedia:()=>({addEventListener:(_t,f)=>listeners.add(f),removeEventListener:(_t,f)=>listeners.delete(f)})};
 const surface={style:{},get clientWidth(){return Math.round(800*parseFloat(this.style.width||'100')/100)},get clientHeight(){return Math.round(600*parseFloat(this.style.height||'100')/100)}};
 const context=vm.createContext({console,queueMicrotask:fn=>scheduled.push(fn),PACKET_TYPES:{configure_display:"configure-display"},window:browser,document:{querySelector:()=>shadow},jQuery:()=>({scrollLeft:()=>0,scrollTop:()=>0})});
 vm.runInContext(readFileSync(path.join(process.env.FLOE_DISPLAY_FIXTURE,'Client.js'),'utf8'),context);
 vm.runInContext(readFileSync('display.js','utf8'),context);
 const client=vm.runInContext('Object.create(XpraClient.prototype)',context);
 Object.assign(client,{scale:1,container:surface,connected:true,desktop_width:800,desktop_height:600,id_to_window:{1:{scale:1,update_offsets(){},screen_resized(){this.resizes=(this.resizes||0)+1}}},send:p=>packets.push(JSON.parse(JSON.stringify(p))),_get_monitors:()=>({}),_get_screen_sizes:()=>[],position_float_menu(){}});
 context.client=client;
 client.floeDisplay=vm.runInContext('new FloeXpraDisplay(client)',context);client.floeDisplay.version=2;
 return {client,surface,browser,listeners,packets,shadow,flush};
}

test('native density negotiates real pixels and DPI while pointer hits the same logical target',()=>{
 const {client:c,surface,packets,flush}=setup();
 assert(c.set_display_density('native'));flush();
 assert.deepEqual(Array.from(c._get_desktop_size()),[1600,1200]);assert.equal(c._get_DPI(),192);
 assert.equal(surface.style.transform,'scale(0.5)');assert.equal(c.id_to_window[1].scale,2);
 assert.equal(c.getMouse({clientX:123,clientY:234,button:0}).x,246);
 assert.deepEqual(packets[0][1]['desktop-size'],[1600,1200]);assert.equal(packets[0][1]['floe-display-density'],2);
 c.set_display_density('logical');flush();assert.equal(c.getMouse({clientX:123,clientY:234,button:0}).x,123);
 assert.equal(c._get_DPI(),96);assert.equal(c.id_to_window[1].scale,1);
});

test('density rounds up to preserve toolkit geometry and is bounded',()=>{
 for(const [dpr,want] of [[0.8,1],[1,1],[1.25,2],[1.5,2],[2,2],[3,3],[8,4],[NaN,1]]){
  const {client:c,flush}=setup(dpr);c.set_display_density('native');flush();assert.equal(c.scale,want);
 }
});

test('monitor changes reconfigure density once and disconnect revokes listeners',()=>{
 const s=setup();s.client.set_display_density('native');s.flush();const n=s.packets.length;
 for(const f of [...s.listeners])f();assert.equal(s.packets.length,n);
 s.browser.devicePixelRatio=1;const late=[...s.listeners][0];late();assert.equal(s.client.scale,1);assert.equal(s.packets.length,n+1);
 s.client.floeDisplay.dispose();assert.equal(s.listeners.size,0);s.browser.devicePixelRatio=2;late();assert.equal(s.client.scale,1);
});

test('unsupported server cannot silently claim native density',()=>{
 const {client:c,packets}=setup();c.floeDisplay.version=0;
 assert.equal(c.set_display_density('native'),false);assert.equal(c.scale,1);assert.equal(packets.length,0);
 assert.throws(()=>c.set_display_density('unexpected'),/Invalid display density policy/);
});

test('attachment resets retained remote density even when logical dimensions are unchanged',()=>{
 const {client:c,packets}=setup();
 c.log=()=>{};c.emit_connection_established=()=>assert.equal(packets.length,1);
 c._process_startup_complete([]);
 assert.equal(packets.length,1);
 assert.equal(packets[0][1]['floe-display-density'],1);
 assert.equal(packets[0][1].dpi.x,96);
 c._screen_resized();assert.equal(packets.length,1);
});


test('server display bounds cap backing density and are reconsidered on resize',()=>{
 const {client:c,flush}=setup(4);
 c.floeDisplay.accept({'floe-display':2,max_desktop_size:[1920,1440]});
 c.set_display_density('native');flush();assert.equal(c.scale,2);
 c.floeDisplay.accept({'floe-display':2,max_desktop_size:[3840,2160]});
 c._screen_resized();assert.equal(c.scale,3);
 c.floeDisplay.accept({'floe-display':2,max_desktop_size:[0,NaN]});
 assert.equal(c.floeDisplay.maximum,null);
});

test('shadow pointer keeps logical size and hotspot inside the scaled surface',()=>{
 const {client:c,shadow,flush}=setup();c.set_display_density('native');flush();
 c.floeCursor={current:{width:24,height:16,xhot:5,yhot:3,url:'fixture'}};
 c._process_pointer_position(['pointer-position',1,246,468]);
 assert.deepEqual(shadow.style,{width:'48px',height:'32px',left:'236px',top:'462px',display:'inline'});
 c.set_display_density('logical');flush();c._process_pointer_position(['pointer-position',1,123,234]);
 assert.equal(shadow.style.left,'118px');assert.equal(shadow.style.width,'24px');
});

test('display subscribers see resolved limits and recover on resize without polling',()=>{
 const {client:c,surface,browser,listeners,packets,flush}=setup(2),states=[];
 c.floeDisplay.accept({'floe-display':2,max_desktop_size:[1200,900]});
 const stop=c.subscribe_display(state=>states.push(state));
 c.set_display_density('native');flush();
 assert.deepEqual(JSON.parse(JSON.stringify(states.at(-1))),{available:true,policy:'native',density:1,width:800,height:600,limit:'display'});
 assert(Object.isFrozen(states.at(-1)));
 Object.defineProperties(surface,{clientWidth:{get(){return Math.round(500*parseFloat(this.style.width)/100)}},clientHeight:{get(){return Math.round(400*parseFloat(this.style.height)/100)}}});
 c._screen_resized();
 assert.deepEqual(JSON.parse(JSON.stringify(states.at(-1))),{available:true,policy:'native',density:2,width:1000,height:800,limit:null});
 const count=states.length, sent=packets.length;
 for(let i=0;i<200;i++)c._screen_resized();
 assert.equal(states.length,count);assert.equal(packets.length,sent);
 browser.devicePixelRatio=1;[...listeners][0]();assert.equal(states.at(-1).density,1);
 stop();c.set_display_density('logical');flush();assert.equal(states.length,count+1);
});

test('density limit is distinct from display bounds and disconnect revokes subscribers',()=>{
 const {client:c,flush}=setup(8),states=[];
 c.subscribe_display(state=>states.push(state));c.set_display_density('native');flush();
 assert.equal(states.at(-1).limit,'density');assert.equal(states.at(-1).density,4);
 c.floeDisplay.dispose();const count=states.length;
 c.subscribe_display(()=>assert.fail('disposed subscription'));c.floeDisplay.sync();
 assert.equal(states.length,count);assert.equal(c.set_display_density('native'),false);
});

test('a burst of policy selections applies only its final display configuration',()=>{
 const {client:c,packets,flush}=setup();
 for(const policy of ['native','logical','native','logical','native'])c.set_display_density(policy);
 assert.equal(packets.length,0);
 flush();assert.equal(packets.length,1);assert.equal(c.scale,2);
 c.set_display_density('logical');c.floeDisplay.dispose();flush();assert.equal(packets.length,1);
});
