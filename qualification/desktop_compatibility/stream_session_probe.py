"""Native browser display benchmark through the installed, authenticated helper.

All browser data, HTML, sockets, and processes belong to the provided directory.
This measures host input-to-decoded-pixels and payload bytes, not WAN or browser
presentation latency. The product viewer benchmark owns those additional stages.
"""
import argparse
import io
import json
import os
from pathlib import Path
import signal
import select
import socket
import struct
import subprocess
import time

from PIL import Image

HTML = '''<!doctype html><meta charset="utf-8"><title>Native stream acceptance</title>
<style>html,body{margin:0;overflow:hidden;background:#f5f5f5}canvas{display:block}</style><canvas></canvas>
<script>
const c=document.querySelector('canvas'),ctx=c.getContext('2d');let count=0,moving=false,offset=0;
function draw(){c.width=innerWidth;c.height=innerHeight;ctx.fillStyle='#f5f5f5';ctx.fillRect(0,0,c.width,c.height);
ctx.font='16px sans-serif';for(let y=-36-offset%36;y<c.height+50;y+=36){const n=Math.round((y+offset)/36);ctx.fillStyle=n%2?'#e5edf7':'#fff';ctx.fillRect(0,y,innerWidth,34);ctx.fillStyle='#24324a';ctx.fillText('Remote application document '+n+' — keyboard, scrolling and window capture',100,y+23);}
ctx.fillStyle=`rgb(${30+count%4*50},30,210)`;ctx.fillRect(12,12,64,64);}
function tick(){if(moving){offset+=12;draw();}requestAnimationFrame(tick)}
onresize=draw;onkeydown=e=>{e.preventDefault();if(e.code==='Space'){count++;draw();}if(e.code==='KeyS')moving=true;if(e.code==='KeyI'){moving=false;draw();}};draw();tick();
</script>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--state', required=True)
    parser.add_argument('--preparer', required=True)
    parser.add_argument('--browser', choices=['chrome', 'firefox'], default='chrome')
    parser.add_argument('--baseline', action='store_true', help='Released v0.20 launcher with a fixture-owned explicit profile')
    parser.add_argument('--mode', choices=['legacy', 'auto', 'clarity', 'smooth', 'data'], default='auto')
    args = parser.parse_args()
    root = args.directory.resolve(); root.mkdir(mode=0o700)
    runtime = root / 'runtime'; runtime.mkdir(mode=0o700)
    (root / 'page.html').write_text(HTML)
    executable = '/usr/bin/google-chrome-stable' if args.browser == 'chrome' else '/usr/bin/firefox'
    argv = ['--app=' + (root / 'page.html').as_uri(), '--window-size=1280,720'] if args.browser == 'chrome' else [(root / 'page.html').as_uri()]
    if args.baseline:
        (root / 'profile').mkdir(mode=0o700)
        argv = (['--user-data-dir=' + str(root / 'profile'), '--no-first-run', '--no-default-browser-check'] if args.browser == 'chrome' else ['--no-remote', '--profile', str(root / 'profile')]) + argv
    if args.browser == 'firefox':
        (root / 'profile').mkdir(mode=0o700, exist_ok=True)
        (root / 'profile' / 'user.js').write_text('user_pref("termsofuse.bypassNotification", true);\nuser_pref("browser.shell.checkDefaultBrowser", false);\nuser_pref("browser.aboutwelcome.enabled", false);\nuser_pref("browser.startup.homepage_override.mstone", "ignore");\n')
    def quote(value):
        for char in ('\\', '"', '`', '$'): value = value.replace(char, '\\' + char)
        return '"' + value.replace('%', '%%') + '"'
    desktop = root / 'browser.desktop'
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Stream acceptance\nExec=' + ' '.join(map(quote, [executable, *argv])) + '\n')
    env = dict(os.environ)
    for name in ['DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'XAUTHORITY']:
        env.pop(name, None)
    request = {'state': args.state, 'desktop': str(desktop), 'directory': str(root / 'session'),
               'runtime': str(runtime), 'instance': root.name, 'environment': env, 'browser_profile': str(root / 'profile')}
    if args.baseline:
        request.pop('browser_profile')
    prepared = json.loads(subprocess.check_output([args.preparer], input=json.dumps(request).encode()))
    process = subprocess.Popen([prepared['Executable'], prepared['Configuration']], start_new_session=True,
                               stdout=(root / 'helper.log').open('wb'), stderr=subprocess.STDOUT)
    endpoint = prepared['Endpoint']; connection = socket.socket(socket.AF_UNIX)
    serial, epoch, state, canvas, sequence, marker = 0, 0, {}, None, 0, None
    frames, byte_count, errors = 0, 0, []
    def send(value):
        nonlocal serial
        if 'method' in value: serial += 1; value = {'id': serial, **value}
        data = json.dumps(value).encode(); connection.sendall(struct.pack('!BI', 1, len(data)) + data)
    def receive(length):
        result = bytearray()
        while len(result) < length:
            block = connection.recv(length-len(result))
            if not block: raise EOFError('Helper closed')
            result.extend(block)
        return bytes(result)
    def packet():
        kind, length = struct.unpack('!BI', receive(5)); assert length <= 64*1024*1024
        return kind, receive(length)
    def event():
        nonlocal epoch,state,canvas,sequence,frames,byte_count,marker
        if not select.select([connection], [], [], .1)[0]:
            return
        kind,data=packet(); assert kind==1
        value=json.loads(data)
        if value.get('error'): errors.append(value['error'])
        if value.get('event')=='attached':
            epoch=value['connection'];state=value['state']
            if args.mode!='legacy': send({'method':'configure_stream','mode':args.mode})
        elif value.get('event')=='state': state=value['state']
        elif value.get('event') in ('frame','cursor') and value.get('bytes'):
            kind,pixels=packet();assert len(pixels)==value['bytes']
            if value['event']=='frame':
                f=value['frame']; image=Image.open(io.BytesIO(pixels)).convert('RGB')
                if (f['window'],f['generation']) != (state['window'],state['generation']):return
                size=(f['width'],f['height'])
                if f.get('base'): assert canvas is not None and f['base']==sequence and canvas.size==size
                else: canvas=Image.new('RGB',size)
                canvas.paste(image,(f.get('x',0),f.get('y',0)));sequence=f['sequence']
                send({'method':'frame_ack','frame':sequence});frames+=1;byte_count+=len(pixels)
                if marker is None:
                    for y in range(0,canvas.height,4):
                        for x in range(0,canvas.width,4):
                            r,g,b=canvas.getpixel((x,y))
                            if abs(r-30)<8 and abs(g-30)<8 and abs(b-210)<8: marker=(x+8,y+8);return
    def until(check, timeout=15):
        deadline=time.monotonic()+timeout
        while not check():
            if time.monotonic()>deadline:raise TimeoutError('Native observation deadline')
            try:event()
            except socket.timeout:pass
    def collect(duration):
        before_f,before_b,start=frames,byte_count,time.monotonic()
        while time.monotonic()-start<duration:
            try:event()
            except socket.timeout:pass
        elapsed=time.monotonic()-start
        return {'seconds':elapsed,'frames':frames-before_f,'fps':(frames-before_f)/elapsed,
                'payload_bytes':byte_count-before_b,'mbps':(byte_count-before_b)*8/elapsed/1e6}
    def key(code):
        for down in [True,False]:send({'method':'input','connection':epoch,'window':state['window'],
            'generation':state['generation'],'operation':{'kind':'key','code':code,'pressed':down}})
    try:
        start=time.monotonic()
        while not Path(endpoint['SocketPath']).exists():
            if process.poll() is not None or time.monotonic()-start>30:raise RuntimeError('Native helper failed to start')
            time.sleep(.05)
        connection.connect(endpoint['SocketPath']);connection.settimeout(10)
        send({'version':1,'instance':endpoint['Instance'],'token':endpoint['Token']})
        until(lambda:marker is not None,30)
        first=time.monotonic()-start
        collect(1)
        idle=collect(3);latencies=[]
        before=byte_count
        for n in range(1,21):
            started=time.monotonic();key(57)
            until(lambda:canvas is not None and abs(canvas.getpixel(marker)[0]-(30+n%4*50))<10,3)
            latencies.append((time.monotonic()-started)*1000)
        typing_bytes=byte_count-before
        key(31);motion=collect(5);key(23);refine=collect(1)
        canvas.save(root/'frame.png')
        result={'browser':args.browser,'baseline':args.baseline,'mode':args.mode,'first_frame_ms':first*1000,'dimensions':canvas.size,
            'idle':idle,'motion':motion,'refinement':refine,'typing_payload_bytes':typing_bytes,
            'input_to_decoded_pixels_ms':{'median':sorted(latencies)[len(latencies)//2], 'p95':sorted(latencies)[18],'samples':latencies},'errors':errors}
        (root/'metrics.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
        assert not errors,errors
    except Exception:
        if canvas is not None:canvas.save(root / 'failure.png')
        (root / 'failure.json').write_text(json.dumps({'state':state,'errors':errors,'frames':frames,'marker':marker}, indent=2))
        raise
    finally:
        # Explicit lifecycle control terminates only this fixture's application
        # tree; stopping a viewer/helper alone intentionally leaves apps alive.
        if process.poll() is None:
            try:
                send({'method':'terminate_application'})
                limit = time.monotonic() + 10
                while process.poll() is None and time.monotonic() < limit:event()
            except (OSError, EOFError, ValueError):pass
        connection.close()
        # This process is the exact task-owned native supervisor, never a user's
        # browser or a discovered process-name match.
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait();raise


if __name__=='__main__':main()
