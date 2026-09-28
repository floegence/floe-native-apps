// Ordered, bounded browser composition for the native desktop frame contract.
// Pixel receipt never changes target authority; the host acknowledges only the
// onPaint callback for its still-current attachment/window/generation.
class FloeDesktopFrames {
  constructor({canvas, isValid, onResize, onPaint, onError, decode}) {
    this.canvas = canvas;
    this.context = canvas.getContext('2d', {alpha:false});
    this.isValid = isValid;
    this.onResize = onResize;
    this.onPaint = onPaint;
    this.onError = onError;
    this.decode = decode || (packet => createImageBitmap(new Blob([packet.bytes], {type:'image/' + packet.meta.encoding})));
    this.queue = [];
    this.job = null;
    this.epoch = 0;
    this.sequence = 0;
  }
  receive(packet) {
    if (!this.isValid(packet)) return;
    if (this.queue.length + (this.job && this.jobEpoch === this.epoch ? 1 : 0) >= 2) {
      this.onError(packet, new Error('Native frame decode capacity exceeded'));
      return;
    }
    this.queue.push(packet);
    void this.drain();
  }
  invalidate() {
    this.epoch++;
    this.queue.length = 0;
    this.sequence = 0;
    // A pending decode retains its single owner until its bitmap is closed.
    // New target packets wait in the same bounded queue; no parallel decoders.
  }
  async drain() {
    if (this.job) return;
    while (this.queue.length) {
      const packet = this.queue.shift(), epoch = this.epoch;
      this.job = packet;
      this.jobEpoch = epoch;
      let image;
      try {
        if (!this.isValid(packet)) continue;
        const f = packet.meta;
        const width = f.region_width || f.width, height = f.region_height || f.height;
        const x = f.x || 0, y = f.y || 0, base = f.base || 0;
        const complete = x === 0 && y === 0 && width === f.width && height === f.height;
        if (!['png','jpeg','webp'].includes(f.encoding) ||
            ![width,height,f.width,f.height].every(v=>Number.isInteger(v)&&v>0&&v<=4096) ||
            ![x,y,base].every(v=>Number.isSafeInteger(v)&&v>=0) ||
            !Number.isSafeInteger(f.sequence) || f.sequence <= this.sequence || (complete && base !== 0) ||
            x+width>f.width || y+height>f.height ||
            (!complete && (!base || base!==this.sequence || this.canvas.width!==f.width || this.canvas.height!==f.height))) {
          throw new Error('Invalid native frame region or reference');
        }
        image = await this.decode(packet);
        if (epoch !== this.epoch || !this.isValid(packet)) continue;
        if (image.width !== width || image.height !== height) throw new Error('Native frame dimensions differ');
        if (this.canvas.width !== f.width || this.canvas.height !== f.height) {
          this.onResize(); this.canvas.width = f.width; this.canvas.height = f.height;
        }
        this.context.drawImage(image,x,y);
        this.sequence = f.sequence;
        this.onPaint(packet,f.width,f.height);
      } catch (error) {
        if (epoch === this.epoch && this.isValid(packet)) {
          this.queue.length = 0;
          this.onError(packet,error);
        }
      } finally {
        image?.close();
        this.job = null;
      }
    }
  }
}
