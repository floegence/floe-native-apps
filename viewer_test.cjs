const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
test('compatibility separates display, input and confirmed module replacement',()=>{
 const ctx=vm.createContext({window:{}});vm.runInContext(readFileSync('viewer.js','utf8'),ctx);
 const v=ctx.window.floeXpraViewer,c={floeInput:{version:1},floePointer:{version:1},floeDisplay:{version:1}};
 assert.deepEqual(JSON.parse(JSON.stringify(v.capabilities(c))),{display:'logical',input:'ready',pointer:'ready'});
 c.floeDisplay.version=2;assert.equal(v.capabilities(c).display,'native');
 c.floeInput.version=0;assert.equal(v.capabilities(c).input,'unsupported');assert.equal(v.capabilities(c).pointer,'unavailable');
 for(const error of ['INPUT_CONTEXT_UNAVAILABLE','INPUT_TARGET_UNAVAILABLE','INPUT_DELIVERY_FAILED','UNKNOWN']) {c.floeInput.error=error;assert.equal(v.capabilities(c).input,'unavailable')}
 c.floeInput.error='INPUT_MODULE_VERSION_UNSUPPORTED';assert.equal(v.capabilities(c).input,'restart-required');
 assert(Object.isFrozen(v.capabilities(c)));
});
test('input failure classification survives the adapter failure transition and resets on reattach',()=>{
 const ctx=vm.createContext({window:{}});
 vm.runInContext(readFileSync('viewer.js','utf8')+'\n'+readFileSync('input_client.js','utf8'),ctx);
 ctx.client={packet_handlers:{}};ctx.client.floeInput=vm.runInContext('new FloeXpraInput(client)',ctx);
 const {client}=ctx,v=ctx.window.floeXpraViewer;
 client.floeInput.connected(1);client.floeInput.fail('INPUT_MODULE_VERSION_UNSUPPORTED');
 assert.equal(v.capabilities(client).input,'restart-required');
 client.floeInput.connected(1);assert.equal(v.capabilities(client).input,'ready');
 client.floeInput.fail('INPUT_CONTEXT_UNAVAILABLE');assert.equal(v.capabilities(client).input,'unavailable');
});
