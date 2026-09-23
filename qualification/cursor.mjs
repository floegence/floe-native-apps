import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';

const config = JSON.parse(process.argv[2]);
const html = `<!doctype html><style>body{margin:0}#surface{width:500px;height:400px;background:#ddeeff}</style>
<div id="surface"></div><img id="shadow_pointer" hidden>
<script src="Window.js"></script><script src="Client.js"></script><script src="FloeCursor.js"></script>
<script>
const client = Object.create(XpraClient.prototype);
const win = Object.create(XpraWindow.prototype);
win.wid=1;win.div=document.querySelector('#surface');win.get_internal_geometry=()=>({x:0,y:0});
client.id_to_window={1:win};client.floeCursor=new FloeXpraCursor(client);
window.fixture={client,win};
</script>`;
const server = createServer(async (req,res) => {
  if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(html);return;}
  if(!['/Window.js','/Client.js','/FloeCursor.js'].includes(req.url)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type','text/javascript');res.end(await readFile(path.join(config.directory,req.url.slice(1))));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const results=[];
try {
  for(const name of process.env.FLOE_TEST_CURSOR_BROWSERS.split(',')) {
    const engine={chromium,firefox,webkit}[name];assert(engine,'Unknown qualification browser');
    const browser=await engine.launch({headless:true,...(name==='chromium'?{chromiumSandbox:true}:{})});
    try {
      for(const dpr of [1,1.25,1.5,2,3]) {
        const context=await browser.newContext({deviceScaleFactor:dpr});
        const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
        await page.goto(`http://127.0.0.1:${server.address().port}`);
        for(const size of [16,24,32,48,64,128]) for(const transparent of [false,true]) {
          const result=await page.evaluate(async ({size,transparent})=>{
            const canvas=document.createElement('canvas');canvas.width=size;canvas.height=size;
            const ctx=canvas.getContext('2d');
            if(!transparent){ctx.fillStyle='#ff0000';ctx.fillRect(0,0,size/2,size);ctx.fillStyle='#0000ff';ctx.fillRect(size/2,0,size/2,size);}
            const bytes=new Uint8Array(await(await fetch(canvas.toDataURL())).arrayBuffer());
            const owner=fixture.client.floeCursor;const previous=owner.current;
            fixture.client._process_cursor(['cursor','png',0,0,size,size,size-1,size-1,1,bytes]);
            await new Promise((resolve,reject)=>{
              const until=performance.now()+3000;
              function check(){if(owner.current!==previous&&owner.current)return resolve();if(performance.now()>until)return reject(Error('Cursor did not decode'));requestAnimationFrame(check);}check();
            });
            const cursor=owner.current;
            const image=new Image();image.src=cursor.url;await image.decode();
            const pixels=document.createElement('canvas');pixels.width=image.naturalWidth;pixels.height=image.naturalHeight;
            const pctx=pixels.getContext('2d');pctx.drawImage(image,0,0);
            return {width:cursor.width,height:cursor.height,xhot:cursor.xhot,yhot:cursor.yhot,
              backingWidth:image.naturalWidth,backingHeight:image.naturalHeight,dpr:devicePixelRatio,
              css:getComputedStyle(fixture.win.div).cursor,supported:CSS.supports('cursor',cursor.css),
              first:Array.from(pctx.getImageData(0,0,1,1).data),last:Array.from(pctx.getImageData(pixels.width-1,0,1,1).data)};
          },{size,transparent});
          const logical=Math.min(24,size),density=Math.min(4,Math.ceil(result.dpr));
          assert.deepEqual([result.width,result.height,result.backingWidth,result.backingHeight],[logical,logical,logical*density,logical*density]);
          assert(result.xhot<logical&&result.yhot<logical);assert(result.supported);assert.match(result.css,/image-set/);
          assert.deepEqual(result.first,transparent?[0,0,0,0]:[255,0,0,255]);
          assert.deepEqual(result.last,transparent?[0,0,0,0]:[0,0,255,255]);
          results.push({browser:name,version:browser.version(),sourceSize:size,transparent,...result});
        }
        assert.deepEqual(errors,[]);await context.close();
      }
    } finally {await browser.close();}
  }
  if(process.env.FLOE_TEST_CURSOR_EVIDENCE){
    await mkdir(process.env.FLOE_TEST_CURSOR_EVIDENCE,{recursive:true});
    await writeFile(path.join(process.env.FLOE_TEST_CURSOR_EVIDENCE,`browser-${config.version}.json`),JSON.stringify({passed:true,actualOSCursor:false,results},null,2));
  }
  console.log(`PASS ${config.version}: ${results.length} decoded-image and CSS cursor cases`);
} finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
