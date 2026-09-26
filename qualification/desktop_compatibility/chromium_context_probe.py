"""Actual isolated Chromium page receipts, never a substituted toolkit fixture."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
from threading import Thread


class ChromiumPage:
    def __init__(self, evidence, receipt, protocol, *, slow_pointer=False):
        binary = os.environ['FLOE_TEST_CHROMIUM_BIN']
        self.version = subprocess.check_output([binary, '--version'], text=True).strip()
        self.receipt, self.ready, self.clicked = receipt, False, False
        self.protocol = protocol
        self.input_events, self.rejected_receipts = [], []
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = b'''<!doctype html><meta charset="utf-8"><title>Floe native Chromium input</title>
<style>html,body{margin:0;background:#3b759f;min-height:100vh}
textarea{box-sizing:border-box;width:50%;height:420px;font:18px monospace;resize:none}</style>
<textarea autofocus></textarea><textarea></textarea><div id="checkpoint" style="position:fixed;bottom:0;left:0;right:0;height:16px"></div><script>
const fields=[...document.querySelectorAll('textarea')];let pending=Promise.resolve();
const report=(path,body='')=>{pending=pending.then(()=>fetch(path,{method:'POST',body}));};
let sending=false,dirty=false;const events=[];
const snapshot=async()=>{
 dirty=true;if(sending)return;sending=true;
 try{while(dirty){dirty=false;await fetch('/received',{method:'POST',body:JSON.stringify({
  values:fields.map(e=>e.value),events
 })});}}finally{sending=false;}
};
const trace=(event)=>{
 if(new URLSearchParams(location.search).has('slow-pointer')&&events.length<10000)events.push({
  type:event.type,key:event.key,ctrl:event.ctrlKey,inputType:event.inputType,
  dataBytes:new TextEncoder().encode(event.data||'').length,
  field:fields.indexOf(event.target),lengths:fields.map(e=>new TextEncoder().encode(e.value).length)
 });
};
// Chromium suppresses clicks during initial window positioning. This readiness
// delay belongs only to fixture startup; text delivery has no sleeps or retries.
window.addEventListener('load',()=>setTimeout(()=>report('/ready'),1000));
for(const field of fields){
 for(const name of ['pointerdown','keydown','keyup','beforeinput','input'])field.addEventListener(name,trace);
 field.addEventListener('pointerdown',()=>{
  if(new URLSearchParams(location.search).has('slow-pointer')){
   // Deliberately stall the application, never the compositor or driver.
   // Native ping responses must not masquerade as renderer completion.
   const until=performance.now()+200;while(performance.now()<until){}
  }
 });
 field.addEventListener('input',()=>{
  const body=JSON.stringify(fields.map(e=>e.value));let hash=2166136261;
  for(const byte of new TextEncoder().encode(body))hash=Math.imul(hash^byte,16777619)>>>0;
  document.querySelector('#checkpoint').style.backgroundColor=`rgb(${hash>>>16&255},${hash>>>8&255},${hash&255})`;
  // Retain the latest actual document with one request in flight. A slow
  // renderer must not build a second network queue of obsolete snapshots.
  snapshot();
 });
 field.addEventListener('pointerup',()=>requestAnimationFrame(()=>{
  if(document.hasFocus()&&document.activeElement===field)report('/clicked');
 }));
}
</script>'''
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                count = int(self.headers.get('Content-Length', '0'))
                if not 0 <= count <= 200000:
                    page.rejected_receipts.append({'path': self.path, 'bytes': count})
                    self.send_error(413)
                    return
                data = self.rfile.read(count)
                if self.path == '/ready':
                    page.ready = True
                elif self.path == '/clicked':
                    page.clicked = True
                elif self.path == '/received':
                    snapshot = json.loads(data)
                    values = snapshot['values']
                    assert len(values) == 2 and all(isinstance(value, str) for value in values)
                    assert isinstance(snapshot['events'], list) and len(snapshot['events']) <= 10000
                    page.input_events = snapshot['events']
                    pending = receipt.with_suffix('.pending')
                    pending.write_text(json.dumps(values, ensure_ascii=False))
                    pending.replace(receipt)
                else:
                    self.send_error(404)
                    return
                self.send_response(204)
                self.end_headers()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.command = [binary, '--user-data-dir=' + str(evidence / 'chromium-profile'),
            '--gtk-version=3', '--no-first-run', '--no-default-browser-check',
            '--ozone-platform=' + protocol,
            '--app=http://127.0.0.1:' + str(self.server.server_port) +
                ('/?slow-pointer' if slow_pointer else '/')]

    @staticmethod
    def commit_marker(values):
        # A page-generated visual receipt rejects queued captures from before
        # the final input event. It never changes or substitutes document text.
        value = 2166136261
        for byte in json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode():
            value = ((value ^ byte) * 16777619) & 0xffffffff
        return (value >> 16 & 255, value >> 8 & 255, value & 255)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.input_events:
            self.receipt.with_suffix('.events.json').write_text(json.dumps(self.input_events,
                ensure_ascii=False, indent=2) + '\n')
        self.receipt.with_suffix('.browser.json').write_text(json.dumps({
            'version': self.version, 'protocol': self.protocol,
            'page_ready': self.ready, 'actual_click': self.clicked,
            'rejected_receipts': self.rejected_receipts,
            'actual_fields': json.loads(self.receipt.read_text()) if self.receipt.exists() else None,
        }, ensure_ascii=False, indent=2) + '\n')
