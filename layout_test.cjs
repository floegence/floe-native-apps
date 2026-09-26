const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
function fixture(){
 const ctx=vm.createContext({console});vm.runInContext(readFileSync('layout.js','utf8'),ctx);
 if(process.env.FLOE_LAYOUT_FIXTURE) vm.runInContext(readFileSync(process.env.FLOE_LAYOUT_FIXTURE+'/js/Window.js','utf8'),ctx);
 const requests=[];const client={scale:1,id_to_window:{},_get_desktop_size:()=>[1440*client.scale,920*client.scale]};
 ctx.client=client;client.floeLayout=vm.runInContext('new FloeXpraLayout(client)',ctx);
 const win={wid:1,x:100,y:100,w:600,h:400,leftoffset:0,topoffset:0,rightoffset:0,bottomoffset:0,metadata:{'size-constraints':{'minimum-size':[1240,960],increment:[2,2]}},updateCSSGeometry(){this.renders=(this.renders||0)+1},geometry_cb(w){requests.push([w.x,w.y,w.w,w.h])}};if(process.env.FLOE_LAYOUT_FIXTURE) Object.setPrototypeOf(win,vm.runInContext('XpraWindow.prototype',ctx));win.client=client;client.id_to_window[1]=win;
 return {client,win,layout:client.floeLayout,requests};
}
test('native minimum and remote receipts converge without resize feedback',()=>{
 const {client,win,layout,requests}=fixture();layout.set(win,'viewport');
 assert.deepEqual(requests,[[0,0,1440,960]]);assert.equal(win.maximized,undefined);
 for(let i=0;i<100;i++){layout.accept(win,0,0,1400,960);layout.update(win)}
 assert.equal(requests.length,1);assert.equal(win.w,1400);
 client.scale=2;layout.update(win);assert.deepEqual(requests.at(-1),[0,0,2880,1840]);
 layout.accept(win,0,0,2880,1840);const renders=win.renders;layout.set(win,'viewport');
 assert.equal(requests.length,2);assert.equal(win.renders,renders);
 win.metadata['size-constraints']['maximum-size']=[2000,1600];layout.update(win);
 assert.deepEqual(requests.at(-1),[0,0,2000,1600]);
});
test('base and increments honor bounded sizes and policy transitions',()=>{
 const {win,layout,requests}=fixture();win.metadata['size-constraints']={'minimum-size':[90,90],'base-size':[10,10],increment:[16,16]};
 layout.set(win,'viewport');assert.deepEqual(requests.at(-1),[0,0,1434,906]);
 layout.set(win,'dialog');assert.deepEqual(requests.at(-1),[12,12,1402,890]);
 layout.set(win,'native');assert.equal(layout.accept(win,1,2,3,4),false);
});
test('destroy, disposal and stale window identity revoke layout',()=>{
 const {win,client,layout,requests}=fixture();layout.set(win,'viewport');layout.remove(win);assert.equal(layout.update(win),false);
 client.id_to_window[1]={};assert.equal(layout.set(win,'viewport'),false);
 client.id_to_window[1]=win;layout.set(win,'dialog');layout.dispose();const count=requests.length;
 assert.equal(layout.update(win),false);assert.equal(layout.set(win,'viewport'),false);assert.equal(requests.length,count);
});

test('prepared windows keep remote receipts and native states separate from layout',()=>{
 if(!process.env.FLOE_LAYOUT_FIXTURE)return;
 const {client,win,layout,requests}=fixture();layout.set(win,'viewport');
 win.set_maximized(false);win.set_fullscreen(false);assert.equal(win.maximized,false);
 for(let i=0;i<100;i++){win.move_resize(0,0,1400,960);win.screen_resized()}
 assert.equal(requests.length,1);assert.equal(win.w,1400);
 client.scale=2;win.screen_resized();assert.deepEqual(requests.at(-1),[0,0,2880,1840]);
});
