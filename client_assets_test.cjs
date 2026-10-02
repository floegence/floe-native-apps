const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const fixture = process.env.FLOE_ASSETS_FIXTURE;

test('protocol and both decoders follow the immutable script origin after document load', () => {
  const created = [];
  class Worker {
    constructor(url) { this.url = String(url); created.push(this); }
    addEventListener() {}
    postMessage(message) { this.message = message; }
  }
  const document = {currentScript:null};
  const context = vm.createContext({document, window:{document,Worker,createImageBitmap(){}}, Worker, URL,
    console, XpraOffscreenWorker:{isAvailable:()=>true}});
  const base = 'https://host.test/_assets/version/js/';
  for (const name of ['Protocol.js','Client.js']) {
    document.currentScript = {src:base+name};
    vm.runInContext(readFileSync(join(fixture,name),'utf8'),context);
  }
  document.currentScript = null;
  vm.runInContext(`
    const client = Object.create(XpraClient.prototype);
    client.clog = () => {};
    client.open_protocol = () => {};
    client.ssl = true;
    client.offscreen_api = true;
    client.initialize_workers();
    client.protocol.open('wss://host.test/session-one/socket');
    client.offscreen_api = false;
    client.initialize_workers();
    client.protocol.open('wss://host.test/session-two/socket');
  `,context);
  assert.deepEqual(created.map(w=>w.url),[
    base+'OffscreenDecodeWorker.js',base+'Protocol.js',
    base+'DecodeWorker.js',base+'Protocol.js',
  ]);
});

test('the protocol worker keeps its relative imports and does not require a document', () => {
  const imports = [];
  const context = vm.createContext({console,URL, importScripts:(...paths)=>imports.push(...paths),
    postMessage(){},addEventListener(){},self:{addEventListener(){}},setTimeout,clearTimeout});
  vm.runInContext(readFileSync(join(fixture,'Protocol.js'),'utf8'),context);
  assert.deepEqual(imports,['lib/lz4.js','lib/brotli_decode.js','lib/rencode.js','Utilities.js']);
});

test('required host transport preserves decoding workers and rejects native network fallback', () => {
  const workers = [];
  const sockets = [];
  class Worker {
    constructor(url) { workers.push(String(url)); }
    addEventListener() {}
    postMessage() {}
  }
  class HostSocket {
    constructor(url, protocol) { sockets.push({url, protocol}); }
    addEventListener() {}
  }
  const document = {currentScript:null, documentElement:{hasAttribute:()=>true}};
  const context = vm.createContext({document, window:{document,Worker,createImageBitmap(){}}, Worker, URL,
    floeHostTransport:{WebSocket:HostSocket}, WebSocket:class { constructor() { throw Error('raw network bypass'); } },
    console, setTimeout:()=>0, clearTimeout(){}, XpraOffscreenWorker:{isAvailable:()=>true}});
  for (const name of ['FloeTransport.js','Protocol.js','Client.js']) {
    document.currentScript = {src:'https://host.test/assets/js/'+name};
    vm.runInContext(readFileSync(join(fixture,name),'utf8'),context);
  }
  vm.runInContext(`
    const client = Object.create(XpraClient.prototype);
    client.clog = () => {};
    client.open_protocol = () => {};
    client.ssl = true; client.offscreen_api = true; client.webtransport = true;
    client.initialize_workers();
    if (!(client.protocol instanceof XpraProtocol) || client.webtransport) throw Error('carrier bypass');
    client.protocol.open('wss://host.test/socket');
  `,context);
  assert.deepEqual(sockets, [{url:'wss://host.test/socket',protocol:'binary'}]);
  assert.deepEqual(workers, ['https://host.test/assets/js/OffscreenDecodeWorker.js']);
  vm.runInContext('delete globalThis.floeHostTransport',context);
  assert.throws(()=>vm.runInContext('client.initialize_workers()',context), /Host viewer transport is unavailable/);
});
