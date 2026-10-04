/** Bounded WebCodecs rendering for a consumer-authorized host desktop session. */
export function unpackDesktopMedia(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 4) throw new Error('MEDIA_INVALID');
  const length = new DataView(buffer).getUint32(0);
  if (!length || length > 8 * 1024 * 1024 || 4 + length > buffer.byteLength) throw new Error('MEDIA_INVALID');
  const header = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(new Uint8Array(buffer, 4, length)));
  if (header.version !== 1 || !Number.isSafeInteger(header.generation) || header.generation < 1 ||
      !Number.isSafeInteger(header.bytes) || header.bytes < 1 || header.bytes > 64 * 1024 * 1024 ||
      header.bytes !== buffer.byteLength - 4 - length) throw new Error('MEDIA_INVALID');
  if (header.type === 'frame') {
    if (!Number.isSafeInteger(header.frame_id) || header.frame_id < 1 ||
        !['h264', 'png'].includes(header.codec) ||
        !Number.isInteger(header.width) || header.width < 2 || header.width > 8192 ||
        !Number.isInteger(header.height) || header.height < 2 || header.height > 8192 ||
        header.width * header.height > 16 * 1024 * 1024) throw new Error('MEDIA_INVALID');
  } else if (header.type === 'cursor') {
    if (header.codec !== 'png' || header.bytes > 2 * 1024 * 1024 ||
        !Number.isInteger(header.width) || header.width < 1 || header.width > 512 ||
        !Number.isInteger(header.height) || header.height < 1 || header.height > 512 ||
        !Number.isInteger(header.hot_x ?? 0) || (header.hot_x ?? 0) < 0 || (header.hot_x ?? 0) >= header.width ||
        !Number.isInteger(header.hot_y ?? 0) || (header.hot_y ?? 0) < 0 || (header.hot_y ?? 0) >= header.height) throw new Error('MEDIA_INVALID');
  } else if (header.type !== 'audio' || header.codec !== 'opus' || header.sample_rate !== 48000 || header.channels !== 2 ||
             header.bytes > 64 * 1024 || !Number.isSafeInteger(header.timestamp) || header.timestamp < 0) {
    throw new Error('MEDIA_INVALID');
  }
  return { header, data: new Uint8Array(buffer, 4 + length) };
}

export class DesktopPaintOrder {
  generation = 0;
  received = 0;
  painted = 0;
  reset(generation) { this.generation = generation; this.received = this.painted = 0; }
  accept(header) {
    if (header.generation !== this.generation || header.frame_id <= this.received) return false;
    this.received = header.frame_id;
    return true;
  }
  current(header) { return header.generation === this.generation && header.frame_id > this.painted; }
  paint(header) {
    if (!this.current(header)) return false;
    this.painted = header.frame_id;
    return true;
  }
}

export class HostDesktopPlayer {
  static supported() { return globalThis.isSecureContext === true && typeof VideoDecoder !== 'undefined' && typeof createImageBitmap !== 'undefined'; }
  constructor(canvas, { acknowledge, recover, painted = () => {}, statistics = () => {}, audioState = () => {}, workletURL }) {
    this.canvas = canvas;
    this.context = canvas.getContext('2d', { alpha: false, desynchronized: true });
    if (!this.context) throw new Error('CANVAS_UNAVAILABLE');
    this.acknowledge = acknowledge; this.recover = recover; this.painted = painted;
    this.statistics = statistics; this.audioState = audioState; this.workletURL = workletURL;
    this.order = new DesktopPaintOrder();
    this.frames = new Map(); this.incoming = []; this.refinements = new Set();
    this.epoch = 0;
    this.processing = false; this.closed = false; this.recovering = false;
    this.decoder = null; this.audioDecoder = null; this.audio = null; this.speaker = null;
    this.gain = null; this.volume = 1; this.muted = false;
    this.audioStarting = null;
    this.audioPending = 0;
    this.lastPaintHeader = null; this.confirmation = 0; this.confirmationTask = 0;
    this.idle = 0; this.flushing = false; this.needsKey = true;
    this.bytes = 0; this.draws = 0; this.lastStatistic = performance.now(); this.lastDraw = 0;
    this.intervals = []; this.decoderPath = 'unconfigured';
    this.cursorPending = null; this.cursorProcessing = false;
  }
  reset(generation) {
    this.epoch++;
    this.order.reset(generation); this.recovering = false; this.needsKey = true; this.flushing = false;
    this.lastDraw = 0; this.draws = this.bytes = 0; this.intervals = [];
    this.frames.clear(); this.incoming = []; this.refinements.clear();
    clearTimeout(this.idle);
    cancelAnimationFrame(this.confirmation);
    clearTimeout(this.confirmationTask);
    this.confirmation = this.confirmationTask = 0;
    this.lastPaintHeader = null;
    this.cursorPending = null;
    this.canvas.style?.removeProperty('--floe-desktop-cursor');
    if (this.decoder && this.decoder.state !== 'closed') this.decoder.close();
    this.decoder = null;
    if (this.audioDecoder && this.audioDecoder.state !== 'closed') this.audioDecoder.close();
    this.audioDecoder = null;
    this.audioPending = 0;
    this.speaker?.port.postMessage({ type: 'reset', generation });
  }
  receive(buffer) {
    if (this.closed) return;
    let packet;
    try { packet = unpackDesktopMedia(buffer); } catch { this.fail('MEDIA_INVALID'); return; }
    if (packet.header.generation !== this.order.generation || this.recovering) return;
    if (packet.header.type === 'cursor') { this.cursorPending = packet; void this.updateCursor(); return; }
    this.bytes += buffer.byteLength;
    if (packet.header.type === 'audio') { this.decodeAudio(packet); return; }
    if (!this.order.accept(packet.header)) return;
    if (this.incoming.length >= 4) { this.fail('VIDEO_QUEUE_LIMIT'); return; }
    this.incoming.push(packet);
    void this.process();
  }
  async updateCursor() {
    if (this.cursorProcessing) return;
    this.cursorProcessing = true;
    try {
      while (this.cursorPending && !this.closed && !this.recovering) {
        const packet = this.cursorPending; this.cursorPending = null;
        const epoch = this.epoch, { header, data } = packet;
        let image;
        try {
          image = await createImageBitmap(new Blob([data], { type: 'image/png' }));
          if (epoch !== this.epoch || this.closed || this.recovering || this.cursorPending) continue;
          if (image.width !== header.width || image.height !== header.height) throw new Error('CURSOR_INVALID');
          const scale = Math.min(1, 128 / Math.max(header.width, header.height));
          const cursor = document.createElement('canvas');
          cursor.width = Math.max(1, Math.round(header.width * scale));
          cursor.height = Math.max(1, Math.round(header.height * scale));
          cursor.getContext('2d').drawImage(image, 0, 0, cursor.width, cursor.height);
          const x = Math.min(cursor.width - 1, Math.round((header.hot_x ?? 0) * scale));
          const y = Math.min(cursor.height - 1, Math.round((header.hot_y ?? 0) * scale));
          this.canvas.style.setProperty('--floe-desktop-cursor', `url("${cursor.toDataURL('image/png')}") ${x} ${y}, default`);
        } catch { if (epoch === this.epoch) this.fail('CURSOR_INVALID'); }
        finally { image?.close(); }
      }
    } finally { this.cursorProcessing = false; }
  }
  async process() {
    if (this.processing) return;
    this.processing = true;
    try {
      while (this.incoming.length && !this.closed && !this.recovering) {
        const packet = this.incoming.shift();
        const epoch = this.epoch;
        const { header, data } = packet;
        if (header.generation !== this.order.generation) continue;
        try {
          if (header.codec === 'png') {
            this.decodeRefinement(header, data);
            continue;
          }
          if (this.needsKey && !header.key) { this.fail('KEYFRAME_REQUIRED'); break; }
          if (!this.decoder) await this.configure(header);
          if (epoch !== this.epoch || header.generation !== this.order.generation || this.closed || this.recovering) continue;
          if (!this.decoder || this.decoder.state !== 'configured' || this.frames.size >= 4 || this.decoder.decodeQueueSize >= 4) {
            this.fail('VIDEO_QUEUE_LIMIT'); break;
          }
          // Frame identity is a collision-free decoder timestamp. Native capture
          // timestamps remain telemetry; frame order cannot depend on clock skew.
          this.frames.set(header.frame_id, header);
          this.needsKey = false;
          this.decoder.decode(new EncodedVideoChunk({ type: header.key ? 'key' : 'delta', timestamp: header.frame_id, data }));
          this.scheduleDrain();
        } catch {
          // A retired asynchronous decode/configuration cannot fail a newly
          // connected desktop while this serial worker drains its old work.
          if (epoch === this.epoch) this.fail('DECODE_FAILED');
        }
      }
    } catch { this.fail('DECODE_FAILED'); }
    finally { this.processing = false; }
  }
  decodeRefinement(header, data) {
    // Refinements are independently decodable. Never hold subsequent H.264
    // dependencies behind asynchronous PNG work, and bound retained decodes.
    if (this.refinements.size >= 4) { this.fail('VIDEO_QUEUE_LIMIT'); return; }
    const epoch = this.epoch;
    this.refinements.add(header);
    void createImageBitmap(new Blob([data], { type: 'image/png' })).then(image => {
      if (epoch !== this.epoch) image.close();
      else this.schedule(image, header);
    }).catch(() => { if (epoch === this.epoch) this.fail('DECODE_FAILED'); })
      .finally(() => this.refinements.delete(header));
  }
  scheduleDrain() {
    clearTimeout(this.idle);
    if (!this.frames.size || this.flushing) return;
    const decoder = this.decoder;
    this.idle = setTimeout(() => {
      if (this.closed || this.decoder !== decoder || !this.frames.size || this.flushing) return;
      // Some decoders retain a static first frame. Drain only after neither
      // submission nor output has made progress. A late event-loop turn must
      // not flush a decoder that has just delivered another buffered picture.
      this.flushing = true; this.needsKey = true;
      decoder.flush().then(() => {
        if (this.decoder === decoder) this.flushing = false;
      }).catch(() => { if (this.decoder === decoder) this.fail('DECODE_FAILED'); });
    }, 80);
  }
  async configure(header) {
    const epoch = this.epoch;
    if (!header.key || !header.description || !/^avc1\.[0-9a-f]{6}$/i.test(header.profile)) throw new Error('KEYFRAME_REQUIRED');
    const description = Uint8Array.from(atob(header.description), char => char.charCodeAt(0));
    let configuration, decoderPath;
    for (const preference of ['prefer-hardware', 'no-preference', 'prefer-software']) {
      const candidate = { codec: header.profile, codedWidth: header.width, codedHeight: header.height,
        description, hardwareAcceleration: preference, optimizeForLatency: true };
      try {
        const supported = await VideoDecoder.isConfigSupported(candidate);
        if (supported.supported) { configuration = supported.config; decoderPath = preference; break; }
      } catch { /* A rejected configuration is never used for incoming media. */ }
    }
    if (epoch !== this.epoch || this.closed || this.recovering || header.generation !== this.order.generation) return;
    if (!configuration) throw new Error('VIDEO_CODEC_UNSUPPORTED');
    this.decoderPath = decoderPath;
    const decoder = new VideoDecoder({
      output: image => {
        if (this.decoder !== decoder) { image.close(); return; }
        const metadata = this.frames.get(image.timestamp);
        this.frames.delete(image.timestamp);
        if (!metadata) { image.close(); this.fail('FRAME_IDENTITY_INVALID'); return; }
        this.scheduleDrain();
        this.schedule(image, metadata);
      },
      error: () => { if (this.decoder === decoder) this.fail('DECODE_FAILED'); },
    });
    this.decoder = decoder;
    decoder.configure(configuration);
  }
  schedule(image, header) {
    if (this.closed || this.recovering || !this.order.current(header)) { image.close(); return; }
    this.present(image, header);
  }
  present(image, header) {
    // Decoded pixels may replace an unconfirmed canvas immediately. Only the
    // picture that survives a rendering opportunity receives a cumulative ACK.
    try {
      if (this.canvas.width !== header.width || this.canvas.height !== header.height) {
        this.canvas.width = header.width; this.canvas.height = header.height;
      }
      this.context.drawImage(image, 0, 0, header.width, header.height);
    } catch {
      this.fail('RENDER_FAILED'); return;
    } finally { image.close(); }
    if (!this.order.paint(header)) return;
    const now = performance.now();
    if (this.lastDraw) { this.intervals.push(now - this.lastDraw); this.draws++; }
    else { this.lastStatistic = now; this.draws = 0; }
    this.lastDraw = now;
    if (now - this.lastStatistic >= 1000) {
      const elapsed = (now - this.lastStatistic) / 1000;
      const intervals = this.intervals.sort((a,b) => a-b);
      this.statistics({ width: header.width, height: header.height, fps: this.draws / elapsed,
        bitsPerSecond: 8 * this.bytes / elapsed, frameIntervalP95: intervals[Math.floor(intervals.length * .95)] ?? 0,
        decoderPreference: this.decoderPath, encoder: header.encoder ?? '', codec: header.codec });
      this.lastStatistic = now; this.draws = this.bytes = 0; this.intervals = [];
    }
    this.lastPaintHeader = header;
    this.confirmPaint();
  }
  confirmPaint() {
    if (this.confirmation || this.confirmationTask) return;
    const epoch = this.epoch;
    this.confirmation = requestAnimationFrame(() => {
      this.confirmation = 0;
      if (this.closed || epoch !== this.epoch) return;
      const header = this.lastPaintHeader;
      // RAF precedes rendering. A later RAF callback may still replace these
      // pixels; conservatively confirm only a picture unchanged through the
      // following task. Neither decoding nor drawing alone grants authority.
      this.confirmationTask = setTimeout(() => {
        this.confirmationTask = 0;
        if (this.closed || epoch !== this.epoch || !header || header.generation !== this.order.generation) return;
        if (this.lastPaintHeader !== header) { this.confirmPaint(); return; }
        this.acknowledge(header.generation, header.frame_id);
        this.painted(header.generation, header.frame_id);
      }, 0);
    });
  }
  fail(code) {
    if (this.closed || this.recovering) return;
    this.reset(this.order.generation);
    this.recovering = true;
    this.incoming = [];
    this.recover(code);
  }
  async enableAudio() {
    if (this.closed) return false;
    if (this.audioStarting) return this.audioStarting;
    this.audioStarting = this.startAudio();
    try { return await this.audioStarting; }
    finally { this.audioStarting = null; }
  }
  async startAudio() {
    if (typeof AudioDecoder === 'undefined' || !this.workletURL) { this.audioState('unsupported'); return false; }
    try {
      if (!this.audio) {
        this.audio = new AudioContext({ sampleRate: 48000, latencyHint: 'interactive' });
        await this.audio.audioWorklet.addModule(this.workletURL);
        if (this.closed) { await this.audio.close(); return false; }
        this.speaker = new AudioWorkletNode(this.audio, 'floe-desktop-audio', { numberOfInputs: 0,
          numberOfOutputs: 1, outputChannelCount: [2] });
        this.speaker.port.postMessage({ type: 'reset', generation: this.order.generation });
        this.speaker.port.onmessage = ({ data }) => {
          if (data.type === 'accepted' && data.generation === this.order.generation) this.audioPending = Math.max(0, this.audioPending - 1);
        };
        this.gain = this.audio.createGain();
        this.speaker.connect(this.gain).connect(this.audio.destination);
      }
      if (this.closed) return false;
      await this.audio.resume();
      this.setVolume(this.volume, this.muted);
      this.audioState(this.audio.state === 'running' ? 'enabled' : 'gesture_required');
      return this.audio.state === 'running';
    } catch {
      this.speaker?.disconnect(); this.gain?.disconnect();
      if (this.audio && this.audio.state !== 'closed') void this.audio.close();
      this.audio = this.speaker = this.gain = null;
      if (!this.closed) this.audioState('unavailable');
      return false;
    }
  }
  setVolume(volume, muted = false) {
    this.volume = Math.min(1, Math.max(0, Number.isFinite(volume) ? volume : 0)); this.muted = muted;
    if (this.gain) this.gain.gain.value = muted ? 0 : this.volume;
  }
  decodeAudio({ header, data }) {
    if (!this.audio || this.audio.state !== 'running' || !this.speaker) return;
    try {
      if (!this.audioDecoder) {
        const generation = this.order.generation;
        const decoder = new AudioDecoder({
          output: frame => {
            try {
              if (this.audioDecoder !== decoder || generation !== this.order.generation || this.closed || this.recovering || this.audioPending >= 6 || this.audio.state !== 'running') return;
              const planes = [0,1].map(planeIndex => {
                const plane = new Float32Array(frame.numberOfFrames);
                frame.copyTo(plane, { planeIndex, format: 'f32-planar' });
                return plane;
              });
              this.audioPending++;
              this.speaker.port.postMessage({ type: 'samples', generation, planes }, planes.map(plane => plane.buffer));
            } finally { frame.close(); }
          },
          error: () => { if (this.audioDecoder === decoder) this.audioState('unavailable'); },
        });
        decoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 2 });
        this.audioDecoder = decoder;
      }
      if (this.audioDecoder.state !== 'configured' || this.audioDecoder.decodeQueueSize >= 4) return;
      this.audioDecoder.decode(new EncodedAudioChunk({ type: 'key', timestamp: header.timestamp, data }));
    } catch { this.audioState('unavailable'); }
  }
  close() {
    if (this.closed) return;
    this.reset(0); this.closed = true;
    this.speaker?.disconnect(); this.gain?.disconnect();
    void this.audio?.close();
  }
}
