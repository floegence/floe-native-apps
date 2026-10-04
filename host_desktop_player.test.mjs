import test from 'node:test';
import assert from 'node:assert/strict';
import { unpackDesktopMedia, DesktopPaintOrder } from './host_desktop_player.mjs';
import { DesktopAudioRing } from './host_desktop_audio.mjs';
const nativeSetTimeout = globalThis.setTimeout, nativeClearTimeout = globalThis.clearTimeout;

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

async function playerFixture() {
  const { HostDesktopPlayer } = await import('./host_desktop_player.mjs');
  const callbacks = new Map(), tasks = new Map(); let next = 0;
  globalThis.requestAnimationFrame = callback => { callbacks.set(++next, callback); return next; };
  globalThis.cancelAnimationFrame = id => callbacks.delete(id);
  globalThis.setTimeout = (callback, delay, ...args) => delay === 0 ? (tasks.set(++next, callback), next) : nativeSetTimeout(callback, delay, ...args);
  globalThis.clearTimeout = id => { tasks.delete(id); nativeClearTimeout(id); };
  const draws = [], acknowledgements = [];
  const attributes = new Map();
  const canvas = { width:2, height:2, getContext:()=>({drawImage:image=>draws.push(image.id)}),
    setAttribute: (name, value) => attributes.set(name, value), removeAttribute: name => attributes.delete(name),
    getAttribute: name => attributes.get(name) };
  const player = new HostDesktopPlayer(canvas, { acknowledge:(generation,id)=>acknowledgements.push([generation,id]), recover:code=>{throw Error(code);} });
  player.reset(1);
  const frame = id => ({ id, close(){} });
  const refresh = () => { const scheduled=[...callbacks.values()]; callbacks.clear(); for(const callback of scheduled)callback(); };
  const afterRender = () => { const scheduled=[...tasks.values()]; tasks.clear(); for(const callback of scheduled)callback(); };
  const tick = () => { refresh(); afterRender(); };
  return { player, frame, tick, refresh, afterRender, draws, acknowledgements };
}

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
