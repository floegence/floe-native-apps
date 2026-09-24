// Execute the prepared original Xpra client, including its real keymap tables.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const fixture = process.env.FLOE_INPUT_CLIENT_FIXTURE;

function setup(mac = false) {
  const sent = [];
  const context = vm.createContext({window:{}, TextEncoder, console,
    Utilities:{isMacOS:()=>mac, isWindows:()=>false, getFirstBrowserLanguage:()=> 'en',
      getKeyboardLayout:()=> 'us', StringToUint8:s=>new TextEncoder().encode(s)},
    performance:{now:()=>1}, PACKET_TYPES:{key_action:'key-action',keymap_changed:'keymap-changed'},
  });
  vm.runInContext(readFileSync(join(fixture,'Keycodes.js'),'utf8'),context);
  vm.runInContext(readFileSync('input_client.js','utf8'),context);
  vm.runInContext(readFileSync(join(fixture,'Client.js'),'utf8'),context);
  context.sent = sent;
  vm.runInContext(`
    const c = Object.create(XpraClient.prototype);
    Object.assign(c, {packet_handlers:{}, connected:true, server_readonly:false,
      id_to_window:{1:{},2:{}}, focused_wid:1, key_packets:[], key_layout:'us',
      debug_categories:[], browser_language_change_embargo_time:Infinity, swap_keys:${mac}});
    c.send = p => sent.push(p);
    c.init_keyboard();
    c.floeInput.connected(1);
    globalThis.adapter = c.floeInput;
    globalThis.client = c;
  `,context);
  return {context, adapter:context.adapter, client:context.client, sent};
}
const key = (name,code,pressed=true,extra={}) => ({key:name,code,pressed,repeat:false,shiftKey:false,ctrlKey:false,altKey:false,metaKey:false,location:0,...extra});

test('prepared bootstrap revision is independent of negotiated input protocol',()=>{
  const {context,adapter}=setup();
  assert.equal(context.window.floeXpraInput.version,2);
  assert.equal(adapter.version,1);
  adapter.disconnect();
  assert.equal(context.window.floeXpraInput.version,2);
  assert.equal(adapter.version,0);
});

test('text and keys retain event order without registering DOM owners',()=>{
  const {adapter,sent} = setup();
  const target=adapter.bindTarget(1);
  adapter.sendKey(key('a','KeyA'),target);
  adapter.sendKey(key('a','KeyA',false),target);
  adapter.commitText('你好🙂',target);
  adapter.sendKey(key('Enter','Enter'),target);
  adapter.sendKey(key('Enter','Enter',false),target);
  assert.deepEqual(sent.map(p=>p[0]),['key-action','key-action','floe-input','key-action','key-action']);
  assert.equal(sent[3][2],'Return');
  adapter.commitText('你好🙂',target);
  assert.notEqual(sent[2][1],sent[5][1]);
});

test('arrows, platform shortcuts, repeats and held releases preserve key semantics',()=>{
  const {adapter,sent} = setup(true);
  const target=adapter.bindTarget(1);
  adapter.sendKey(key('ArrowDown','ArrowDown'),target);
  assert.equal(sent.at(-1)[2],'Down');
  adapter.sendKey(key('ArrowDown','ArrowDown',true,{repeat:true}),target);
  assert.deepEqual(sent.slice(-2).map(p=>p[3]),[false,true]);
  adapter.sendKey(key('Meta','MetaLeft',true,{metaKey:true}),target);
  assert.equal(sent.at(-1)[2],'Control_L');
  adapter.release(target);
  assert.equal(sent.at(-1)[3],false);
  assert.equal(adapter.held.size,0);
});

test('disconnection and target changes reject old callbacks without replay',()=>{
  const {adapter,sent} = setup();
  const first=adapter.bindTarget(1);
  adapter.sendKey(key('Shift','ShiftLeft'),first);
  adapter.bindTarget(2);
  assert.equal(sent.at(-1)[1],1);
  assert.equal(sent.at(-1)[3],false);
  assert.equal(adapter.commitText('late',first),false);
  adapter.disconnect();
  adapter.connected(1);
  const next=adapter.bindTarget(1);
  assert.notEqual(next,first);
  assert.equal(adapter.commitText('late',first),false);
});

test('paste owns clipboard token and shortcut, never Unicode commit',()=>{
  const {adapter,client,sent} = setup();
  client.clipboard_enabled=true;
  client.send_clipboard_token=data=>sent.push(['clipboard-token',data]);
  const target=adapter.bindTarget(1);
  assert.equal(adapter.clipboard({...key('v','KeyV',true,{ctrlKey:true}),preventDefault(){throw Error('native paste suppressed');}},target),true);
  assert.equal(sent.length,0);
  adapter.paste({preventDefault(){},stopPropagation(){},clipboardData:{getData:()=> '你好'}},target);
  assert.deepEqual(sent.map(p=>p[0]),['clipboard-token','key-action','key-action']);
});

test('a delivery error releases held keys and revokes input',()=>{
  const {adapter,sent} = setup();
  const target=adapter.bindTarget(1);
  adapter.sendKey(key('Shift','ShiftLeft'),target);
  adapter.commitText('你好',target);
  adapter.result(['floe-input-result',1,'INPUT_CONTEXT_UNAVAILABLE']);
  assert.equal(sent.at(-1)[3],false);
  assert.equal(adapter.commitText('late',target),false);
  assert.equal(adapter.target,null);
});
