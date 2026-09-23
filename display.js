/* Density is a connection setting. Xpra geometry remains in remote pixels. */
"use strict";

class FloeXpraDisplay {
  constructor(client) {
    this.client = client;
    this.policy = "logical";
    this.version = 0;
    this.maximum = null;
    this.disposed = false;
    this.changed = () => {
      if (this.disposed) return;
      this.client._screen_resized();
      this.watch();
    };
    this.watch();
    this.sync();
  }

  watch() {
    this.media?.removeEventListener("change", this.changed);
    this.media = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    this.media.addEventListener("change", this.changed);
  }

  setPolicy(policy) {
    if (!["logical", "native"].includes(policy)) throw new Error("Invalid display density policy");
    if (this.disposed || this.version !== 1) return false;
    this.policy = policy;
    this.client._screen_resized();
    return true;
  }

  accept(hello) {
    this.version = hello["floe-display"] === 1 ? 1 : 0;
    const maximum = hello.max_desktop_size;
    this.maximum = Array.isArray(maximum) && maximum.length === 2 &&
      maximum.every(n => Number.isInteger(n) && n > 0) ? maximum : null;
  }

  sync() {
    const dpr = Number(window.devicePixelRatio);
    let density = this.policy === "native" && Number.isFinite(dpr)
      ? Math.max(1, Math.min(4, Math.ceil(dpr))) : 1;
    const client = this.client;
    if (this.maximum) {
      const width = client.container.clientWidth / client.scale;
      const height = client.container.clientHeight / client.scale;
      density = Math.max(1, Math.min(density,
        Math.floor(this.maximum[0] / Math.max(1, width)),
        Math.floor(this.maximum[1] / Math.max(1, height))));
    }
    const changed = client.scale !== density;
    client.scale = density;
    const style = client.container.style;
    style.width = `${100 * density}%`;
    style.height = `${100 * density}%`;
    style.transform = `scale(${1 / density})`;
    style.transformOrigin = "top left";
    for (const win of Object.values(client.id_to_window)) {
      win.scale = density;
      if (changed) win.update_offsets();
    }
    return changed;
  }

  dispose() {
    this.disposed = true;
    this.media?.removeEventListener("change", this.changed);
  }
}
