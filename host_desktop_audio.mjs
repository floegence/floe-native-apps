// The ring never retains more than 120 ms, including while a tab is throttled.
export class DesktopAudioRing {
  constructor(capacity = 5760) {
    this.capacity = capacity; this.planes = [new Float32Array(capacity), new Float32Array(capacity)];
    this.read = 0; this.length = 0;
  }
  reset() { this.read = this.length = 0; }
  append(planes) {
    if (!Array.isArray(planes) || planes.length !== 2 || planes.some(plane => !(plane instanceof Float32Array)) || planes[0].length !== planes[1].length) return;
    const start = Math.max(0, planes[0].length - this.capacity);
    for (let index = start; index < planes[0].length; index++) {
      if (this.length === this.capacity) { this.read = (this.read + 1) % this.capacity; this.length--; }
      const position = (this.read + this.length) % this.capacity;
      for (let channel = 0; channel < 2; channel++) this.planes[channel][position] = planes[channel][index];
      this.length++;
    }
  }
  take(output) {
    for (const plane of output) plane.fill(0);
    const count = Math.min(this.length, output[0]?.length ?? 0);
    for (let index = 0; index < count; index++) {
      for (let channel = 0; channel < output.length; channel++) output[channel][index] = this.planes[Math.min(channel, 1)][this.read];
      this.read = (this.read + 1) % this.capacity;
    }
    this.length -= count;
  }
}

if (typeof registerProcessor === 'function') {
  registerProcessor('floe-desktop-audio', class extends AudioWorkletProcessor {
    constructor() {
      super(); this.ring = new DesktopAudioRing(); this.generation = 0;
      this.port.onmessage = ({ data }) => {
        if (data.type === 'reset') { this.generation = data.generation; this.ring.reset(); }
        else if (data.type === 'samples' && data.generation === this.generation) {
          this.ring.append(data.planes);
          this.port.postMessage({ type: 'accepted', generation: data.generation });
        }
      };
    }
    process(_inputs, outputs) { this.ring.take(outputs[0]); return true; }
  });
}
