"""Known browser cursor pixels and actual pointer receipts in a task-only page."""
import base64
import hashlib
import io
import json
import time

from desktop_cursor import rgba_png


def cases():
    result = []
    for size in (16, 24, 32, 48, 64, 128):
        result.append(dict(name=str(size), width=size, height=size, logical_width=size, logical_height=size,
                           xhot=size-1, yhot=size-1))
    result += [dict(name='density', width=48, height=32, logical_width=24, logical_height=16, xhot=23, yhot=15),
               dict(name='transparent', width=16, height=16, logical_width=16, logical_height=16, xhot=0, yhot=0),
               dict(name='hidden')]
    return result


def pixels(case):
    data = bytearray()
    for y in range(case['height']):
        for x in range(case['width']):
            if case['name'] == 'transparent' or x == 0 or y == 0:
                color = (0, 0, 0, 0)
            elif x < case['width']//2:
                color = (255, 0, 255, 128)
            else:
                color = (0, 255, 0, 255)
            data.extend(color)
    return bytes(data)


def markup():
    tiles = []
    for index, case in enumerate(cases()):
        value = 'none'
        if case['name'] != 'hidden':
            source = 'data:image/png;base64,' + base64.b64encode(rgba_png(case['width'], case['height'], pixels(case))).decode()
            image = f'url({source})'
            if case['name'] == 'density':
                image = f'image-set(url({source}) 2x)'
            value = f"{image} {case['xhot']} {case['yhot']}, default"
        tiles.append(f'<div data-cursor="{case["name"]}" style="position:fixed;left:{index*100}px;top:450px;width:100px;height:100px;background:#3b759f;cursor:{value}">{case["name"]}</div>')
    return (''.join(tiles) + '''<script>
for(const tile of document.querySelectorAll('[data-cursor]')){
 for(const type of ['pointerenter','click'])tile.addEventListener(type,event=>fetch('/cursor-hit',{
  method:'POST',body:JSON.stringify({name:tile.dataset.cursor,type,x:event.clientX,y:event.clientY,screenX:event.screenX,screenY:event.screenY,viewportHeight:innerHeight})
 }));
}
</script>''').encode()


def exercise(client, target, page, result):
    from PIL import Image
    observations = []
    for index, case in enumerate(cases()):
        after = len(client.events)
        x, y = index * 100 + 50, 580
        move = client.request('input', **target, operation={'kind': 'move', 'x': x, 'y': y})
        assert 'error' not in client.response(move)
        deadline = time.monotonic() + 8
        matched = None
        while time.monotonic() < deadline:
            for message in client.events[after:]:
                if message.get('event') != 'cursor':
                    continue
                description = message['cursor']
                if case['name'] == 'hidden':
                    if description['mode'] == 'hidden':
                        matched = description
                elif description['mode'] == 'image' and all(description[key] == case[key] for key in
                        ('logical_width', 'logical_height', 'xhot', 'yhot')):
                    source = client.directory.parent / f"cursor-{client.generation}-{description['sequence']}.png"
                    image = Image.open(source).convert('RGBA')
                    if case['name'] == 'density':
                        # At output scale 1 Chromium resamples its 2x CSS source
                        # before wl_surface commit. Preserve that actual buffer;
                        # it is not evidence for a native scale-2 wl_buffer.
                        assert image.size == (24, 16)
                        assert image.getpixel((5, 5)) == (255, 0, 255, 128)
                        assert image.getpixel((20, 10)) == (0, 255, 0, 255)
                        assert image.getpixel((0, 0))[3] < 128
                    else:
                        assert image.size == (case['width'], case['height'])
                        assert image.tobytes() == pixels(case), f'Native cursor pixels differ: {case["name"]}'
                    matched = description
            if matched:
                break
            client.record()
        assert matched, f'No actual native cursor for {case["name"]}'
        for pressed in (True, False):
            request = client.request('input', **target, operation={'kind': 'button', 'button': 0,
                                       'pressed': pressed, 'x': x, 'y': y})
            assert 'error' not in client.response(request)
        deadline = time.monotonic() + 5
        while not any(item['name'] == case['name'] and item['type'] == 'click' for item in page.cursor_hits):
            assert time.monotonic() < deadline, 'No actual click receipt'
            time.sleep(.01)
        click = next(item for item in page.cursor_hits if item['name'] == case['name'] and item['type'] == 'click')
        height = result['frames'][-1]['height']
        assert (click['x'], click['y']) == (x, y - height + click['viewportHeight']), f'Native hotspot shifted click: {click}'
        assert click['x'] == x and 450 <= click['y'] < 550, 'Click reached the wrong page tile'
        observations.append({'source': case, 'native': matched, 'click': click,
            'rgba_sha256': hashlib.sha256(pixels(case)).hexdigest() if case['name'] != 'hidden' else None})
    result['native_cursor_cases'] = observations
    # Return to a normal text cursor and capture a fresh scene. No magenta/green
    # cursor pixels may be burned into the application frame.
    client.response(client.request('input', **target, operation={'kind': 'move', 'x': 350, 'y': 580}))
    client.response(client.request('refresh'))
    result['cursor_absent_from_frame'] = client.paint('cursor-excluded', absent=((255,0,255),(0,255,0)))
