const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
test('canvas resize preserves pixels and equal geometry never resets either dimension',()=>{
 class Canvas{
  constructor(w=0,h=0){this._w=w;this._h=h;this.pixels='painted';this.resets=0}
  get width(){return this._w} set width(n){this._w=n;this.pixels='';this.resets++}
  get height(){return this._h} set height(n){this._h=n;this.pixels='';this.resets++}
  getContext(){return {drawImage:other=>{this.pixels=other.pixels}}}
 }
 for(const worker of [false,true]){
  const ctx=vm.createContext(worker?{OffscreenCanvas:Canvas}:{document:{createElement:()=>new Canvas}});
  vm.runInContext(readFileSync('canvas.js','utf8'),ctx);
  const canvas=new Canvas(640,480);ctx.canvas=canvas;
  vm.runInContext('floeResizeCanvas(canvas, 1280, 960)',ctx);assert.equal(canvas.pixels,'painted');assert.equal(canvas.resets,2);
  vm.runInContext('floeResizeCanvas(canvas, 1280, 960)',ctx);assert.equal(canvas.pixels,'painted');assert.equal(canvas.resets,2);
  vm.runInContext('floeResizeCanvas(canvas, 320, 240)',ctx);assert.equal(canvas.pixels,'painted');assert.equal(canvas.resets,4);
 }
});
