import test from 'node:test';
import assert from 'node:assert/strict';
import { unpackDesktopMedia, DesktopPaintOrder, desktopPointerPosition } from './host_desktop_player.mjs';
import { DesktopAudioRing } from './host_desktop_audio.mjs';
const nativeSetTimeout = globalThis.setTimeout, nativeClearTimeout = globalThis.clearTimeout;

test('cursor hiding is explicit and coordinate mapping matches fitted and scrolled pixels', () => {
  const hidden = unpackDesktopMedia(packet({ type: 'cursor', cursor_visible: false, codec: undefined, width: undefined, height: undefined }, new Uint8Array()));
  assert.equal(hidden.header.cursor_visible, false);
  assert.throws(() => unpackDesktopMedia(packet({ type: 'cursor', cursor_visible: false })));
  assert.throws(() => unpackDesktopMedia(packet({ type: 'frame' }, new Uint8Array())));
  const canvas = { width: 1920, height: 1280, getBoundingClientRect: () => ({ left: 100, top: -200, width: 960, height: 960 }) };
  assert.deepEqual(desktopPointerPosition(canvas, 580, 280), { x: .5, y: .5 });
  assert.deepEqual(desktopPointerPosition(canvas, -100, 2000), { x: 0, y: 1 });
});

function packet(header, payload = Uint8Array.of(1, 2, 3)) {
  const metadata = new TextEncoder().encode(JSON.stringify({ version: 1, type: 'frame', generation: 1, frame_id: 1, codec: 'png', width: 2, height: 2, bytes: payload.length, ...header }));
  const buffer = new ArrayBuffer(4 + metadata.length + payload.length);
  new DataView(buffer).setUint32(0, metadata.length);
  new Uint8Array(buffer, 4, metadata.length).set(metadata);
  new Uint8Array(buffer, 4 + metadata.length).set(payload);
  return buffer;
}

test('reject truncated, oversized, and unknown media without allocating a decoder', () => {
  assert.deepEqual([...unpackDesktopMedia(packet({})).data], [1,2,3]);
  for (const header of [{ bytes: 20 }, { width: 100000 }, { frame_id: 0 }, { generation: 0 }, { type: 'input' }, { codec: 'jpeg' }]) {
    assert.throws(() => unpackDesktopMedia(packet(header)));
  }
  assert.throws(() => unpackDesktopMedia(new ArrayBuffer(2)));
});

test('video negotiation offers AV1 and VP9 only after independent positive decoder probes', async () => {
  const { HostDesktopPlayer } = await import('./host_desktop_player.mjs');
  const original = globalThis.VideoDecoder;
  try {
    for (const supported of [true, false]) {
      globalThis.VideoDecoder = class { static async isConfigSupported() { return { supported }; } };
      assert.deepEqual(await HostDesktopPlayer.supportedCodecs(), supported ? ['av1', 'vp9', 'h264'] : ['h264']);
    }
    globalThis.VideoDecoder = class { static async isConfigSupported() { throw Error('unavailable'); } };
    assert.deepEqual(await HostDesktopPlayer.supportedCodecs(), ['h264']);
    for (const supported of ['av01', 'vp09']) {
      globalThis.VideoDecoder = class { static async isConfigSupported(config) { return { supported: config.codec.startsWith(supported) }; } };
      assert.deepEqual(await HostDesktopPlayer.supportedCodecs(), [supported === 'av01' ? 'av1' : 'vp9', 'h264']);
    }
  } finally { globalThis.VideoDecoder = original; }
});

for (const [codec, profile] of [['vp9', 'vp09.00.51.08'], ['av1', 'av01.0.12M.08']]) test(`${codec} key and delta frames use the ordered decoder without AVC description`, async () => {
  const f = await playerFixture();
  const originals = ['VideoDecoder', 'EncodedVideoChunk'].map(key => [key, globalThis[key]]);
  const chunks = []; let configuration, decoder;
  globalThis.EncodedVideoChunk = class { constructor(value) { Object.assign(this, value); } };
  globalThis.VideoDecoder = class {
    static async isConfigSupported(config) { return { supported: true, config }; }
    constructor(callbacks) { this.callbacks = callbacks; this.decodeQueueSize = 0; decoder = this; }
    configure(config) { configuration = config; this.state = 'configured'; }
    decode(chunk) { chunks.push(chunk); this.callbacks.output({ timestamp: chunk.timestamp, id: chunk.timestamp, close() {} }); }
    close() { this.state = 'closed'; }
  };
  try {
    f.player.receive(packet({ codec, key: true, profile }));
    await new Promise(resolve => setImmediate(resolve));
    f.player.receive(packet({ codec, key: false, frame_id: 2 }));
    f.tick();
    assert.equal(configuration.codec, profile);
    assert.equal(configuration.description, undefined);
    assert.deepEqual(chunks.map(x => [x.type, x.timestamp]), [['key', 1], ['delta', 2]]);
    assert.deepEqual(f.acknowledgements, [[1, 2]]);
  } finally {
    f.player.close();
    for (const [key, value] of originals) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }
  }
});

test('a delayed refinement cannot overwrite newer video or another display', () => {
  const order = new DesktopPaintOrder(); order.reset(1);
  const old = { generation: 1, frame_id: 2 }, current = { generation: 1, frame_id: 3 };
  assert.equal(order.accept(old), true); assert.equal(order.accept(current), true);
  assert.equal(order.paint(current), true); assert.equal(order.paint(old), false);
  order.reset(2);
  assert.equal(order.paint(current), false);
  assert.equal(order.accept({ generation: 2, frame_id: 1 }), true);
  assert.equal(order.painted, 0);
});

test('audio backlog stays bounded and reset cannot replay old sound', () => {
  const ring = new DesktopAudioRing(4);
  ring.append([Float32Array.of(1,2,3), Float32Array.of(11,12,13)]);
  ring.append([Float32Array.of(4,5,6), Float32Array.of(14,15,16)]);
  assert.equal(ring.length, 4);
  const output = [new Float32Array(6), new Float32Array(6)];
  ring.take(output);
  assert.deepEqual([...output[0]], [3,4,5,6,0,0]);
  assert.deepEqual([...output[1]], [13,14,15,16,0,0]);
  ring.append([Float32Array.of(1), Float32Array.of(1)]); ring.reset(); ring.take(output);
  assert.deepEqual([...output[0]], [0,0,0,0,0,0]);
});

async function playerFixture(clock = null) {
  const { HostDesktopPlayer } = await import('./host_desktop_player.mjs');
  const callbacks = new Map(), tasks = new Map(); let next = 0;
  globalThis.requestAnimationFrame = callback => { callbacks.set(++next, callback); return next; };
  globalThis.cancelAnimationFrame = id => callbacks.delete(id);
  globalThis.setTimeout = (callback, delay, ...args) => delay === 0 ? (tasks.set(++next, callback), next) :
    clock ? (clock.timers.set(++next, { callback, due: clock.now + delay }), next) : nativeSetTimeout(callback, delay, ...args);
  globalThis.clearTimeout = id => { tasks.delete(id); clock?.timers.delete(id); nativeClearTimeout(id); };
  const draws = [], acknowledgements = [], paints = [];
  const attributes = new Map();
  const canvas = { width:2, height:2, getContext:()=>({drawImage:image=>draws.push(image.id)}),
    setAttribute: (name, value) => attributes.set(name, value), removeAttribute: name => attributes.delete(name),
    getAttribute: name => attributes.get(name) };
  const player = new HostDesktopPlayer(canvas, { acknowledge:(generation,id)=>acknowledgements.push([generation,id]),
    painted:(generation,id)=>paints.push([generation,id]), recover:code=>{throw Error(code);} });
  player.reset(1);
  const frame = id => ({ id, close(){} });
  const refresh = () => { const scheduled=[...callbacks.values()]; callbacks.clear(); for(const callback of scheduled)callback(); };
  const afterRender = () => { const scheduled=[...tasks.values()]; tasks.clear(); for(const callback of scheduled)callback(); };
  const tick = () => { refresh(); afterRender(); };
  return { player, frame, tick, refresh, afterRender, draws, acknowledgements, paints };
}

async function timedPlayerFixture(t) {
  const clock = { now: 0, timers: new Map() };
  t.mock.method(performance, 'now', () => clock.now);
  const f = await playerFixture(clock);
  t.after(() => {
    f.player.close();
    globalThis.setTimeout = nativeSetTimeout;
    globalThis.clearTimeout = nativeClearTimeout;
  });
  f.advance = milliseconds => {
    clock.now += milliseconds;
    for (const [id, timer] of [...clock.timers]) {
      if (timer.due <= clock.now && clock.timers.delete(id)) timer.callback();
    }
  };
  f.schedule = (id, generation = 1) => f.player.schedule(f.frame(id), { generation, frame_id: id, width: 2, height: 2 });
  f.timers = clock.timers;
  return f;
}

test('continuous paint sends bounded cumulative receipts without authorizing unconfirmed pixels', async t => {
  const f = await timedPlayerFixture(t);
  f.schedule(1); f.tick();
  assert.deepEqual(f.acknowledgements, [[1, 1]], 'the first static picture returns credit immediately');
  f.advance(16); f.schedule(2); f.tick();
  f.advance(16); f.schedule(3); f.tick();
  f.advance(16); f.schedule(4); // Drawn, but it has not survived rendering yet.
  assert.deepEqual(f.acknowledgements, [[1, 1]]);
  assert.deepEqual(f.paints, [[1, 1], [1, 2], [1, 3]], 'local paint remains responsive');
  assert.equal(f.timers.size, 1, 'all confirmed pictures share one bounded deadline');
  f.advance(2);
  assert.deepEqual(f.acknowledgements, [[1, 1], [1, 3]], 'a newer draw cannot enter the receipt');
  f.tick(); f.advance(50);
  assert.deepEqual(f.acknowledgements, [[1, 1], [1, 3], [1, 4]]);
});

test('a settled followup picture returns credit without another arriving frame', async t => {
  const f = await timedPlayerFixture(t);
  f.schedule(1); f.tick();
  f.advance(10); f.schedule(2); f.tick();
  f.advance(39);
  assert.deepEqual(f.acknowledgements, [[1, 1]]);
  f.advance(1);
  assert.deepEqual(f.acknowledgements, [[1, 1], [1, 2]]);
  assert.equal(f.timers.size, 0);
  f.advance(500); f.schedule(3); f.tick();
  assert.deepEqual(f.acknowledgements, [[1, 1], [1, 2], [1, 3]], 'a later idle update has no batching delay');
});

test('reset cancels confirmed pending receipts and a retired timer cannot touch its successor', async t => {
  const f = await timedPlayerFixture(t);
  f.schedule(1); f.tick();
  f.advance(10); f.schedule(2); f.tick();
  const retired = [...f.timers.values()].map(timer => timer.callback);
  assert.equal(retired.length, 1);
  f.player.reset(2);
  assert.equal(f.timers.size, 0);
  f.schedule(1, 2); f.tick();
  f.advance(10); f.schedule(2, 2); f.tick();
  for (const callback of retired) callback();
  assert.deepEqual(f.acknowledgements, [[1, 1], [2, 1]]);
  assert.equal(f.timers.size, 1, 'the successor retains its own receipt deadline');
  f.advance(40);
  assert.deepEqual(f.acknowledgements, [[1, 1], [2, 1], [2, 2]]);
});

for (const end of ['close', 'failure']) test(`${end} retires already confirmed pending receipts`, async t => {
  const f = await timedPlayerFixture(t);
  f.schedule(1); f.tick();
  f.advance(10); f.schedule(2); f.tick();
  const retired = [...f.timers.values()].map(timer => timer.callback);
  if (end === 'close') f.player.close();
  else { f.player.recover = () => {}; f.player.fail('DECODE_FAILED'); }
  assert.equal(f.timers.size, 0);
  f.advance(100);
  for (const callback of retired) callback();
  assert.deepEqual(f.acknowledgements, [[1, 1]]);
});

test('only current painted frames select separate cursor presentation', async () => {
  const f = await playerFixture();
  const cursor = () => f.player.canvas.getAttribute('data-floe-desktop-cursor');
  assert.equal(cursor(), undefined);
  f.player.schedule(f.frame(1), { generation: 1, frame_id: 1, width: 2, height: 2, cursor: 'separate' });
  assert.equal(cursor(), 'separate');
  f.player.schedule(f.frame(2), { generation: 1, frame_id: 2, width: 2, height: 2, cursor: 'embedded' });
  assert.equal(cursor(), 'embedded');
  f.player.schedule(f.frame(1), { generation: 1, frame_id: 1, width: 2, height: 2, cursor: 'separate' });
  assert.equal(cursor(), 'embedded');
  f.player.reset(2);
  assert.equal(cursor(), undefined);
  f.player.schedule(f.frame(3), { generation: 1, frame_id: 3, width: 2, height: 2, cursor: 'separate' });
  assert.equal(cursor(), undefined);
  f.player.schedule(f.frame(1), { generation: 2, frame_id: 1, width: 2, height: 2 });
  assert.equal(cursor(), 'embedded', 'legacy frames never promise cursor-free pixels');
  assert.throws(() => unpackDesktopMedia(packet({ cursor: 'guess' })), /MEDIA_INVALID/);
  f.player.close();
});

test('frames replaced before rendering grant only the newest cumulative receipt', async () => {
  const f=await playerFixture();
  f.player.schedule(f.frame(1),{generation:1,frame_id:1,width:2,height:2});
  assert.deepEqual(f.acknowledgements,[]);
  f.player.schedule(f.frame(2),{generation:1,frame_id:2,width:2,height:2});
  f.tick();f.tick();
  assert.deepEqual(f.draws,[1,2]);
  assert.deepEqual(f.acknowledgements,[[1,2]]);
  f.player.close();
});

test('an idle decoder output draws immediately but authorizes input only after a refresh', async () => {
  const f = await playerFixture();
  f.player.schedule(f.frame(1), { generation: 1, frame_id: 1, width: 2, height: 2 });
  assert.deepEqual(f.draws, [1]);
  assert.deepEqual(f.acknowledgements, []);
  f.refresh();
  assert.deepEqual(f.acknowledgements, []);
  f.afterRender();
  assert.deepEqual(f.acknowledgements, [[1, 1]]);
  f.player.close();
});

test('reset retires an already drawn but not yet confirmed frame', async () => {
  const f=await playerFixture();
  f.player.schedule(f.frame(1),{generation:1,frame_id:1,width:2,height:2});
  f.refresh(); f.player.reset(2); f.afterRender(); f.tick();
  assert.deepEqual(f.draws,[1]);
  assert.deepEqual(f.acknowledgements,[]);
  f.player.close();
});

test('decoded arrival bursts present the freshest picture without queuing old interaction state', async () => {
  const f = await playerFixture();
  const closed = [];
  const schedule = id => f.player.schedule({ id, close: () => closed.push(id) },
    { generation: 1, frame_id: id, width: 2, height: 2 });
  schedule(1); schedule(2); schedule(3);
  assert.deepEqual(closed, [1, 2, 3]);
  f.tick(); f.tick(); f.tick();
  assert.deepEqual(f.draws, [1, 2, 3]);
  assert.deepEqual(f.acknowledgements, [[1, 3]]);
  schedule(4); schedule(5);
  f.player.reset(2); f.tick();
  assert.deepEqual(closed, [1, 2, 3, 4, 5]);
  assert.deepEqual(f.draws, [1, 2, 3, 4, 5]);
  f.player.close();
});

test('rejected PNG decode from a retired connection cannot fail its successor', async () => {
  const f = await playerFixture();
  let rejectImage;
  globalThis.createImageBitmap = () => new Promise((_, reject) => { rejectImage = reject; });
  f.player.receive(packet({}));
  f.player.reset(2);
  rejectImage(new Error('old decode failed'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.player.recovering, false);
  f.player.schedule(f.frame(1), { generation: 2, frame_id: 1, width: 2, height: 2 });
  f.tick(); f.tick();
  assert.deepEqual(f.acknowledgements, [[2, 1]]);
  f.player.close();
  delete globalThis.createImageBitmap;
});

test('decoder failure cancels pending pixels and their control-authorizing receipt', async () => {
  const f = await playerFixture();
  const failures = [];
  f.player.recover = code => failures.push(code);
  f.player.schedule(f.frame(1), { generation: 1, frame_id: 1, width: 2, height: 2 });
  f.player.schedule(f.frame(2), { generation: 1, frame_id: 2, width: 2, height: 2 });
  f.player.fail('DECODE_FAILED');
  f.tick(); f.tick();
  assert.deepEqual(f.draws, [1, 2]);
  assert.deepEqual(f.acknowledgements, []);
  assert.deepEqual(failures, ['DECODE_FAILED']);
  f.player.close();
});

test('a PNG resolving during recovery cannot restore retired paint authority', async () => {
  const f = await playerFixture();
  let resolveImage, closed = 0;
  globalThis.createImageBitmap = () => new Promise(resolve => { resolveImage = resolve; });
  f.player.recover = () => {};
  try {
    f.player.receive(packet({}));
    f.player.fail('MEDIA_INVALID');
    resolveImage({ id: 1, close: () => closed++ });
    await new Promise(resolve => setImmediate(resolve));
    f.tick(); f.tick();
    assert.deepEqual(f.draws, []);
    assert.deepEqual(f.acknowledgements, []);
    assert.equal(closed, 1);
  } finally { f.player.close(); delete globalThis.createImageBitmap; }
});

test('codec negotiation completing after reset cannot install a retired decoder', async () => {
  const f = await playerFixture();
  const original = globalThis.VideoDecoder;
  let supported, created = 0;
  globalThis.VideoDecoder = class {
    static isConfigSupported(config) { return new Promise(resolve => { supported = () => resolve({ supported: true, config }); }); }
    constructor() { created++; }
    configure() {}
    close() {}
  };
  try {
    const negotiation = f.player.configure({ generation: 1, key: true, description: 'AQ==', profile: 'avc1.42e01e', width: 2, height: 2 });
    f.player.reset(1);
    supported();
    await negotiation;
    assert.equal(created, 0);
    assert.equal(f.player.decoder, null);
  } finally {
    f.player.close();
    if (original === undefined) delete globalThis.VideoDecoder; else globalThis.VideoDecoder = original;
  }
});

test('recent decoder output postpones draining another buffered frame', async () => {
  const f = await playerFixture();
  const originals = ['VideoDecoder', 'EncodedVideoChunk', 'setTimeout', 'clearTimeout'].map(key => [key, globalThis[key]]);
  const timers = new Map(); let next = 10000, decoder, flushes = 0;
  globalThis.setTimeout = (callback, delay) => { timers.set(++next, { callback, delay }); return next; };
  globalThis.clearTimeout = id => timers.delete(id);
  globalThis.EncodedVideoChunk = class { constructor(value) { Object.assign(this, value); } };
  globalThis.VideoDecoder = class {
    static async isConfigSupported(config) { return { supported: true, config }; }
    constructor(callbacks) { this.callbacks = callbacks; this.decodeQueueSize = 0; decoder = this; }
    configure() { this.state = 'configured'; }
    decode() {}
    async flush() { flushes++; }
    close() { this.state = 'closed'; }
  };
  try {
    f.player.receive(packet({ codec: 'h264', key: true, description: 'AQ==', profile: 'avc1.42e01e' }));
    await new Promise(resolve => setImmediate(resolve));
    f.player.receive(packet({ codec: 'h264', key: false, frame_id: 2 }));
    const old = [...timers.keys()];
    decoder.callbacks.output({ timestamp: 1, id: 1, close() {} });
    assert(old.every(id => !timers.has(id)), 'output progress must cancel the stale drain timer');
    assert.equal(flushes, 0);
    const drain = [...timers.values()].find(timer => timer.delay === 80);
    assert(drain, 'the remaining buffered frame still needs bounded drain');
    drain.callback();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(flushes, 1);
  } finally {
    f.player.close();
    for (const [key, value] of originals) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }
  }
});

test('a failed canvas draw cannot acknowledge pixels or retain decoded resources', async () => {
  const f = await playerFixture();
  const failures = [], closed = [];
  f.player.recover = code => failures.push(code);
  f.player.context.drawImage = () => { throw Error('canvas lost'); };
  for (const id of [1, 2]) f.player.schedule({ id, close: () => closed.push(id) },
    { generation: 1, frame_id: id, width: 2, height: 2 });
  f.tick(); f.tick();
  assert.deepEqual(f.acknowledgements, []);
  assert.deepEqual(failures, ['RENDER_FAILED']);
  assert.deepEqual(closed.sort(), [1, 2]);
  assert.equal(f.player.recovering, true);
  f.player.close();
});

test('repeated audio gestures share setup and bind the worklet to the active generation', async () => {
  const f = await playerFixture();
  f.player.workletURL = '/fixture-worklet';
  const originals = ['AudioContext', 'AudioWorkletNode', 'AudioDecoder'].map(key => [key, globalThis[key]]);
  const messages = []; let created = 0, ready;
  globalThis.AudioDecoder = class {};
  globalThis.AudioContext = class {
    constructor() { created++; this.state = 'suspended'; this.audioWorklet = { addModule: () => new Promise(resolve => { ready = resolve; }) }; }
    createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
    async resume() { this.state = 'running'; }
    async close() { this.state = 'closed'; }
  };
  globalThis.AudioWorkletNode = class {
    constructor() { this.port = { postMessage: message => messages.push(message) }; }
    connect(gain) { return gain; }
    disconnect() {}
  };
  try {
    const first = f.player.enableAudio(), second = f.player.enableAudio();
    f.player.reset(2);
    ready();
    assert.deepEqual(await Promise.all([first, second]), [true, true]);
    assert.equal(created, 1);
    assert.deepEqual(messages, [{ type: 'reset', generation: 2 }]);
  } finally {
    f.player.close();
    for (const [key, value] of originals) {
      if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
    }
  }
});


test('new decoded pixels draw before the outstanding refresh without granting early authority', async () => {
  const f = await playerFixture();
  const schedule = id => f.player.schedule(f.frame(id), { generation: 1, frame_id: id, width: 2, height: 2 });
  schedule(1); schedule(2);
  assert.deepEqual(f.draws, [1, 2]);
  assert.deepEqual(f.acknowledgements, []);
  f.refresh();
  schedule(3); // A later RAF callback could overwrite pixels before rendering.
  f.afterRender();
  assert.deepEqual(f.acknowledgements, []);
  f.tick();
  assert.deepEqual(f.acknowledgements, [[1, 3]]);
  f.player.close();
});


test('a pending lossless refinement cannot block newer H264 decoding', async () => {
  const f = await playerFixture();
  const originals = ['VideoDecoder', 'EncodedVideoChunk', 'createImageBitmap'].map(key => [key, globalThis[key]]);
  let resolveImage; const decoded = [];
  globalThis.createImageBitmap = () => new Promise(resolve => { resolveImage = resolve; });
  globalThis.EncodedVideoChunk = class { constructor(value) { Object.assign(this, value); } };
  globalThis.VideoDecoder = class {
    static async isConfigSupported(config) { return { supported: true, config }; }
    constructor(callbacks) { this.callbacks = callbacks; this.decodeQueueSize = 0; }
    configure() { this.state = 'configured'; }
    decode(chunk) { decoded.push(chunk.timestamp); this.callbacks.output({ timestamp: chunk.timestamp, id: chunk.timestamp, close() {} }); }
    close() { this.state = 'closed'; }
  };
  try {
    f.player.receive(packet({ frame_id: 1 }));
    f.player.receive(packet({ frame_id: 2, codec: 'h264', key: true, description: 'AQ==', profile: 'avc1.42e01e' }));
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(decoded, [2]);
    f.tick();
    assert.deepEqual(f.acknowledgements, [[1, 2]]);
    let closed = false;
    resolveImage({ id: 1, close() { closed = true; } });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(closed, true);
    assert.deepEqual(f.draws, [2]);
  } finally {
    f.player.close();
    for (const [key, value] of originals) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }
  }
});

test('cursor packets validate bounds and do not become painted frame receipts', async () => {
  for (const header of [{ width: 513 }, { hot_x: -1 }, { hot_y: 2 }, { codec: 'h264' }]) {
    assert.throws(() => unpackDesktopMedia(packet({ type: 'cursor', ...header })));
  }
  const f = await playerFixture();
  const originals = ['createImageBitmap', 'document'].map(key => [key, globalThis[key]]);
  const styles = new Map(); let resolveImage, closed = 0;
  f.player.canvas.style = { setProperty: (k,v) => styles.set(k,v), removeProperty: k => styles.delete(k) };
  globalThis.createImageBitmap = () => new Promise(resolve => { resolveImage = resolve; });
  globalThis.document = { createElement: () => ({ getContext: () => ({ drawImage() {} }), toDataURL: () => 'data:image/png;base64,fixture' }) };
  try {
    f.player.receive(packet({ type: 'cursor', hot_x: 1, hot_y: 1 }));
    resolveImage({ width: 2, height: 2, close() { closed++; } });
    await new Promise(resolve => setImmediate(resolve));
    assert.match(styles.get('--floe-desktop-cursor'), /1 1, default$/);
    assert.deepEqual(f.acknowledgements, []);
    f.player.receive(packet({ type: 'cursor' }));
    f.player.reset(2);
    resolveImage({ width: 2, height: 2, close() { closed++; } });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(styles.size, 0);
    assert.equal(closed, 2);
  } finally {
    f.player.close();
    for (const [key, value] of originals) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }
  }
});

test('untrusted hotspots use an immediate arrow and position updates cannot grant paint', async () => {
  const f = await playerFixture();
  const styles = new Map();
  const originals = ['createImageBitmap', 'document'].map(key => [key, globalThis[key]]);
  f.player.canvas.style = { setProperty: (key, value) => styles.set(key, value), removeProperty: key => styles.delete(key) };
  globalThis.document = { createElement: () => ({ getContext: () => ({ drawImage() {} }), toDataURL: () => 'data:image/png;base64,fixture' }) };
  let resolveImage;
  globalThis.createImageBitmap = () => new Promise(resolve => { resolveImage = resolve; });
  try {
    f.player.receive(packet({ type: 'cursor', hotspot_valid: false, cursor_visible: true,
      cursor_position: { x: 20, y: 30, width: 1920, height: 1280 } }));
    f.player.receive(packet({ type: 'cursor', codec: undefined, width: undefined, height: undefined,
      cursor_visible: true, cursor_position: { x: 60, y: 70, width: 1920, height: 1280 } }, new Uint8Array()));
    resolveImage({ width: 2, height: 2, close() {} });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(styles.get('--floe-desktop-cursor'), 'default');
    assert.equal(f.player.cursor.header.cursor_position.x, 60);
    assert.deepEqual(f.acknowledgements, []);
    f.player.setInteraction(false);
    assert.equal(styles.get('--floe-desktop-cursor'), 'none');
    f.player.receive(packet({ type: 'cursor', cursor_visible: false, codec: undefined, width: undefined, height: undefined }, new Uint8Array()));
    f.player.setInteraction(true);
    assert.equal(styles.get('--floe-desktop-cursor'), 'none');
    f.player.reset(2);
    f.player.receive(packet({ type: 'cursor', cursor_visible: false, codec: undefined, width: undefined, height: undefined }, new Uint8Array()));
    assert.equal(f.player.cursor, null);
  } finally {
    f.player.close();
    for (const [key, value] of originals) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }
  }
});


test('video windows require an explicit bounded generation setting', async () => {
  const f = await playerFixture();
  assert.equal(f.player.frameWindow, 4);
  for (const invalid of [0, 5, 7, '6', null]) assert.throws(() => f.player.reset(2, invalid), /FRAME_WINDOW_INVALID/);
  f.player.reset(2, 6);
  f.player.processing = true;
  const wire = id => {
    const header = new TextEncoder().encode(JSON.stringify({ version: 1, type: 'frame', generation: 2, frame_id: id,
      codec: 'h264', width: 2, height: 2, bytes: 1 }));
    const data = new Uint8Array(5 + header.length); new DataView(data.buffer).setUint32(0, header.length);
    data.set(header, 4); data[data.length - 1] = 1; return data.buffer;
  };
  for (let i = 1; i <= 6; i++) f.player.receive(wire(i));
  assert.equal(f.player.incoming.length, 6);
  assert.deepEqual(f.acknowledgements, [], 'buffer admission never grants paint authority');
  assert.throws(() => f.player.receive(wire(7)), /VIDEO_QUEUE_LIMIT/);
  f.player.reset(3);
  assert.equal(f.player.frameWindow, 4, 'an unspecified successor restores the legacy window');
  assert.equal(f.player.incoming.length, 0);
  f.player.close();
});
