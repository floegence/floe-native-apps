const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

function setup(dpr=2) {
  const images=[], draws=[], urls=new Set(), listeners=new Map();
  const shadow={style:{},src:''};
  let serial=0;
  const window={devicePixelRatio:dpr,
    addEventListener:(type,fn)=>listeners.set(type,fn),
    removeEventListener:type=>listeners.delete(type),
    matchMedia:()=>({addEventListener:(_type,fn)=>listeners.set('density',fn),removeEventListener:()=>listeners.delete('density')})};
  const context=vm.createContext({console,window,Uint8Array,Blob,
    Image:class {constructor(){images.push(this);}},
    URL:{createObjectURL:()=>{const url=`blob:${++serial}`;urls.add(url);return url;},revokeObjectURL:url=>urls.delete(url)},
    document:{querySelector:()=>shadow,createElement:()=>({width:0,height:0,getContext(){return {drawImage:(...args)=>draws.push(args)};},toDataURL(){return `data:image/png;base64,${this.width}x${this.height}`;}})},
  });
  for(const file of ['Window','Client'])vm.runInContext(readFileSync(join(process.env.FLOE_CURSOR_FIXTURE,file+'.js'),'utf8'),context);
  vm.runInContext(readFileSync('cursor.js','utf8'),context);
  vm.runInContext(`
    const client = Object.create(XpraClient.prototype);
    client.id_to_window = {};
    client.protocol = null;
    globalThis.addWindow = wid => {
      const win = Object.create(XpraWindow.prototype);
      win.wid=wid;win.div={style:{},remove(){}};
      win.get_internal_geometry=()=>({x:10,y:20});
      client.id_to_window[wid]=win;
      return win;
    };
    addWindow(1);addWindow(2);
    client.floeCursor = new FloeXpraCursor(client);
    globalThis.client=client;
  `,context);
  const client=context.client, cursor=client.floeCursor;
  const packet=(w=48,h=48,x=22,y=24)=>['cursor','png',0,0,w,h,x,y,1,new Uint8Array([137,80,78,71])];
  const load=(width=48,height=48,image=images.at(-1))=>{image.naturalWidth=width;image.naturalHeight=height;image.onload?.();};
  return {client,cursor,images,draws,urls,listeners,window,shadow,packet,load,context};
}

test('48px remote cursor becomes 24 CSS pixels and hotspot scales once',()=>{
  const s=setup();s.client._process_cursor(s.packet());s.load();
  const c=s.cursor.current;
  assert.deepEqual([c.xhot,c.yhot,c.width,c.height],[11,12,24,24]);
  assert.match(c.css,/48x48.*2x\) 11 12, default/);
  assert.equal(s.client.id_to_window[1].div.style.cursor,c.css);
  assert.equal(s.client.id_to_window[2].div.style.cursor,c.css);
  assert.equal(s.urls.size,0);
});

test('size matrix preserves small, rectangular and transparent bitmap geometry at every DPR',()=>{
  for(const dpr of [1,1.25,1.5,2,3]) for(const width of [16,24,32,48,64,128]) for(const height of [width,Math.max(1,width/2)]) {
    const s=setup(dpr);s.client._process_cursor(s.packet(width,height,width-1,height-1));s.load(width,height);
    const c=s.cursor.current,scale=Math.min(1,24/Math.max(width,height));
    assert.deepEqual([c.width,c.height],[Math.round(width*scale),Math.round(height*scale)]);
    assert(c.xhot<c.width && c.yhot<c.height);
    assert.equal(s.draws.at(-1)[3],c.width*Math.ceil(dpr));
    s.cursor.dispose();assert.equal(s.listeners.size,0);
  }
});

test('DPR changes always redraw the original image without cumulative scaling',()=>{
  const s=setup(1);s.client._process_cursor(s.packet());s.load();const original=s.images[0];
  for(const dpr of [1.25,2,3,1.5,1,2]) {
    s.window.devicePixelRatio=dpr;s.listeners.get('density')();
    assert.equal(s.cursor.current.width,24);assert.equal(s.cursor.current.xhot,11);
    assert.equal(s.draws.at(-1)[0],original);
    assert.equal(s.draws.at(-1)[3],24*Math.ceil(dpr));
  }
});

test('late A decode cannot overwrite B, reset or disconnect',()=>{
  const s=setup();s.client._process_cursor(s.packet());const a=s.images[0],late=a.onload;
  s.client._process_cursor(s.packet(16,16,1,1));s.load(16,16);const b=s.cursor.current;
  a.naturalWidth=a.naturalHeight=48;late();assert.equal(s.cursor.current,b);
  s.client._process_cursor(s.packet());const afterReset=s.images.at(-1).onload;
  s.client.reset_cursor();afterReset();assert.equal(s.cursor.current,null);
  s.client._process_cursor(s.packet());const afterClose=s.images.at(-1).onload;
  const afterDispose=s.listeners.get('density');
  s.client.close_protocol();afterClose();assert.equal(s.cursor.current,null);
  afterDispose();
  assert.equal(s.client.id_to_window[1].div.style.cursor,'default');
  assert.equal(s.urls.size,0);assert.equal(s.listeners.size,0);
});

test('destroyed windows and separate connections cannot receive another cursor',()=>{
  const s=setup(),other=setup();s.client._process_cursor(s.packet());
  const removed=s.client.id_to_window[2];removed.destroy();delete s.client.id_to_window[2];s.load();
  assert.equal(removed.div.style.cursor,undefined);assert.equal(other.cursor.current,null);
});

test('a new window adopts the current cursor before another cursor packet arrives',()=>{
  const s=setup();s.client._process_cursor(s.packet());s.load();
  vm.runInContext(`
    const setCursor=XpraWindow.prototype.set_cursor;
    XpraWindow=class {
      constructor(client,wid){this.wid=wid;this.div={style:{}};this.set_cursor=setCursor;}
    };
    document.querySelector=()=>({append(){}});
    client.auto_fullscreen_desktop_window=()=>{};
    client._new_window(3,0,0,100,100,{},true,{});
  `,s.context);
  assert.equal(s.client.id_to_window[3].div.style.cursor,s.cursor.current.css);
});

test('invalid metadata, corrupt image and mismatched decoded dimensions clear the old cursor',()=>{
  for(const invalid of [s=>s.packet(0,48),s=>s.packet(48,48,48,0),s=>s.packet(48,48,-1,0),s=>['cursor','jpeg'],s=>{const p=s.packet();p[9]=new Uint8Array();return p;}]) {
    const s=setup();s.client._process_cursor(s.packet());s.load();
    s.client._process_cursor(invalid(s));assert.equal(s.cursor.current,null);
    assert.equal(s.client.id_to_window[1].div.style.cursor,'default');
  }
  const s=setup();s.client._process_cursor(s.packet());s.images.at(-1).onerror();assert.equal(s.cursor.current,null);assert.equal(s.urls.size,0);
  s.client._process_cursor(s.packet());s.load(64,64);assert.equal(s.cursor.current,null);
});

test('shadow pointer reuses normalized dimensions and hotspot, then resets',()=>{
  const s=setup();s.client._process_cursor(s.packet());s.load();
  s.client._process_pointer_position(['pointer-position',1,200,300]);
  assert.deepEqual(s.shadow.style,{display:'inline',width:'24px',height:'24px',left:'189px',top:'288px'});
  assert.equal(s.shadow.src,s.cursor.current.url);
  s.client.reset_cursor();assert.equal(s.shadow.style.display,'none');
  s.client._process_pointer_position(['pointer-position',999,200,300]);assert.equal(s.shadow.style.display,'none');
});
