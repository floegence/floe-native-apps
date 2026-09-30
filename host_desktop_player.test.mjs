import test from 'node:test';
import assert from 'node:assert/strict';
import { unpackDesktopMedia, DesktopPaintOrder } from './host_desktop_player.mjs';
import { DesktopAudioRing } from './host_desktop_audio.mjs';

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
  const callbacks = new Map(); let next = 0;
  globalThis.requestAnimationFrame = callback => { callbacks.set(++next, callback); return next; };
  globalThis.cancelAnimationFrame = id => callbacks.delete(id);
  const draws = [], acknowledgements = [];
  const canvas = { width:2, height:2, getContext:()=>({drawImage:image=>draws.push(image.id)}) };
  const player = new HostDesktopPlayer(canvas, { acknowledge:(generation,id)=>acknowledgements.push([generation,id]), recover:code=>{throw Error(code);} });
  player.reset(1);
  const frame = id => ({ id, close(){} });
  const tick = () => { const scheduled=[...callbacks.values()]; callbacks.clear(); for(const callback of scheduled)callback(); };
  return { player, frame, tick, draws, acknowledgements };
}

test('paint receipts continue under consecutive animation frames and drain the last frame', async () => {
  const f=await playerFixture();
  f.player.schedule(f.frame(1),{generation:1,frame_id:1,width:2,height:2});
  f.tick();
  assert.deepEqual(f.acknowledgements,[]);
  f.player.schedule(f.frame(2),{generation:1,frame_id:2,width:2,height:2});
  f.tick();f.tick();
  assert.deepEqual(f.draws,[1,2]);
  assert.deepEqual(f.acknowledgements,[[1,1],[1,2]]);
  f.player.close();
});

test('reset retires an already drawn but not yet confirmed frame', async () => {
  const f=await playerFixture();
  f.player.schedule(f.frame(1),{generation:1,frame_id:1,width:2,height:2});
  f.tick(); f.player.reset(2); f.tick();
  assert.deepEqual(f.draws,[1]);
  assert.deepEqual(f.acknowledgements,[]);
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
  f.player.fail('DECODE_FAILED');
  f.tick(); f.tick();
  assert.deepEqual(f.draws, []);
  assert.deepEqual(f.acknowledgements, []);
  assert.deepEqual(failures, ['DECODE_FAILED']);
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
