/* One cursor owner for a prepared Xpra connection. Sizes are CSS pixels. */
"use strict";

class FloeXpraCursor {
  constructor(client) {
    this.client = client;
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

  receive(packet) {
    if (this.disposed) return;
    const generation = ++this.generation;
    this.cancelPending();
    if (packet.length < 9) { this.reset(); return; }
    const [width, height, xhot, yhot] = packet.slice(4, 8);
    const bytes = packet[9];
    if (packet[1] !== "png" || ![width, height, xhot, yhot].every(Number.isInteger) ||
        width < 1 || height < 1 || width > 1024 || height > 1024 ||
        xhot < 0 || yhot < 0 || xhot >= width || yhot >= height ||
        !(bytes instanceof Uint8Array) || !bytes.length || bytes.length > 4 * 1024 * 1024) {
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
      this.source = {image, width, height, xhot, yhot};
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
    for (const win of Object.values(this.client.id_to_window)) win.set_cursor(this.current);
    // A shadow pointer uses the same geometry as the CSS cursor. Its next
    // position packet makes it visible again; never leave a stale bitmap showing.
    const shadow = document.querySelector("#shadow_pointer");
    if (shadow) shadow.style.display = "none";
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
