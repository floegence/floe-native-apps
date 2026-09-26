/* One cursor normalization owner for a remote connection. Sizes are CSS pixels. */
"use strict";

class FloeRemoteCursor {
  constructor(apply) {
    this.applyResult = apply;
    this.generation = 0;
    this.current = null;
    this.source = null;
    this.pending = null;
    this.disposed = false;
    this.density = this.pixelDensity();
    this.densityChanged = () => {
      if (this.disposed) return;
      const density = this.pixelDensity();
      if (density !== this.density) {
        this.density = density;
        if (this.source) this.render();
      }
      this.watchDensity();
    };
    window.addEventListener("resize", this.densityChanged);
    this.watchDensity();
  }

  pixelDensity() {
    // Integral backing density preserves exact logical dimensions at fractional DPR.
    return Math.min(4, Math.max(1, Math.ceil(window.devicePixelRatio || 1)));
  }

  watchDensity() {
    this.media?.removeEventListener("change", this.densityChanged);
    this.media = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    this.media.addEventListener("change", this.densityChanged);
  }

  cancelPending() {
    if (!this.pending) return;
    this.pending.image.onload = this.pending.image.onerror = null;
    this.pending.image.src = "";
    URL.revokeObjectURL(this.pending.url);
    this.pending = null;
  }

  receive(cursor) {
    if (this.disposed) return;
    const generation = ++this.generation;
    this.cancelPending();
    if (!cursor) { this.reset(); return; }
    const {width, height, logicalWidth=width, logicalHeight=height, xhot, yhot, png:bytes} = cursor;
    if (![width, height, logicalWidth, logicalHeight].every(Number.isInteger) ||
        ![xhot, yhot].every(Number.isFinite) ||
        logicalWidth < 1 || logicalHeight < 1 || logicalWidth > 1024 || logicalHeight > 1024 ||
        width < 1 || height < 1 || width > 1024 || height > 1024 ||
        xhot < 0 || yhot < 0 || xhot >= logicalWidth || yhot >= logicalHeight ||
        !(bytes instanceof Uint8Array) || !bytes.length || bytes.length > 5 * 1024 * 1024) {
      this.reset(); return;
    }
    const url = URL.createObjectURL(new Blob([bytes], {type:"image/png"}));
    const image = new Image();
    this.pending = {image, url};
    image.onload = () => {
      if (this.disposed || generation !== this.generation) return;
      URL.revokeObjectURL(url);
      this.pending = null;
      image.onload = image.onerror = null;
      if (image.naturalWidth !== width || image.naturalHeight !== height) { this.reset(); return; }
      this.source = {image, width:logicalWidth, height:logicalHeight, xhot, yhot};
      this.render();
    };
    image.onerror = () => { if (generation === this.generation) this.reset(); };
    image.src = url;
  }

  render() {
    if (this.disposed || !this.source) return;
    const source = this.source;
    const scale = Math.min(1, 24 / Math.max(source.width, source.height));
    const width = Math.max(1, Math.round(source.width * scale));
    const height = Math.max(1, Math.round(source.height * scale));
    const xhot = Math.min(width - 1, Math.round(source.xhot * scale));
    const yhot = Math.min(height - 1, Math.round(source.yhot * scale));
    try {
      const canvas = document.createElement("canvas");
      canvas.width = width * this.density;
      canvas.height = height * this.density;
      const context = canvas.getContext("2d");
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = "high";
      context.drawImage(source.image, 0, 0, canvas.width, canvas.height);
      const url = canvas.toDataURL("image/png");
      this.current = Object.freeze({url, width, height, xhot, yhot,
        css:`image-set(url("${url}") ${this.density}x) ${xhot} ${yhot}, default`});
      this.apply();
    } catch {
      this.reset();
    }
  }

  apply() {
    this.applyResult(this.current);
  }

  hide() {
    if (this.disposed) return;
    this.generation++;
    this.cancelPending();
    this.source = null;
    this.current = Object.freeze({css:"none", url:null, width:0, height:0, xhot:0, yhot:0});
    this.apply();
  }

  reset() {
    this.generation++;
    this.cancelPending();
    this.source = null;
    this.current = null;
    this.apply();
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    window.removeEventListener("resize", this.densityChanged);
    this.media?.removeEventListener("change", this.densityChanged);
    this.reset();
  }
}

// Xpra owns packet decoding and its window list; normalization has no backend.
class FloeXpraCursor extends FloeRemoteCursor {
  constructor(client) {
    super(cursor => {
      for (const win of Object.values(client.id_to_window)) win.set_cursor(cursor);
      // A shadow pointer is repainted by its next position packet.
      const shadow = document.querySelector("#shadow_pointer");
      if (shadow) shadow.style.display = "none";
    });
  }

  receive(packet) {
    if (this.disposed) return;
    if (!packet || packet.length < 10 || packet[1] !== "png") { this.reset(); return; }
    const [width, height, xhot, yhot] = packet.slice(4, 8);
    if (![xhot, yhot].every(Number.isInteger)) { this.reset(); return; }
    super.receive({width, height, xhot, yhot, png:packet[9]});
  }
}
