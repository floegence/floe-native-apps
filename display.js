/* Density is a connection setting. Xpra geometry remains in remote pixels. */
"use strict";

class FloeXpraDisplay {
  constructor(client) {
    this.client = client;
    this.policy = "logical";
    this.version = 0;
    this.maximum = null;
    this.disposed = false;
    this.listeners = new Set();
    this.state = null;
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
    if (this.disposed) return false;
    const dpr = Number(window.devicePixelRatio);
    const requested = this.policy === "native" && Number.isFinite(dpr)
      ? Math.max(1, Math.ceil(dpr)) : 1;
    let density = Math.min(4, requested);
    let limit = density < requested ? "density" : null;
    const client = this.client;
    if (this.maximum) {
      const width = client.container.clientWidth / client.scale;
      const height = client.container.clientHeight / client.scale;
      const bounded = Math.max(1, Math.min(density,
        Math.floor(this.maximum[0] / Math.max(1, width)),
        Math.floor(this.maximum[1] / Math.max(1, height))));
      if (bounded < density) limit = "display";
      density = bounded;
    }
    const changed = client.scale !== density;
    client.scale = density;
    if (changed || !this.state) {
      const style = client.container.style;
      style.width = `${100 * density}%`;
      style.height = `${100 * density}%`;
      style.transform = `scale(${1 / density})`;
      style.transformOrigin = "top left";
    }
    for (const win of Object.values(client.id_to_window)) {
      win.scale = density;
      if (changed) win.update_offsets();
    }
    const state = {available:this.version === 1, policy:this.policy, density,
      width:client.container.clientWidth, height:client.container.clientHeight, limit};
    if (!this.state || Object.keys(state).some(key => state[key] !== this.state[key])) {
      this.state = Object.freeze(state);
      for (const listener of this.listeners) listener(this.state);
    }
    return changed;
  }

  subscribe(listener) {
    if (this.disposed) return () => {};
    this.listeners.add(listener);
    listener(this.state);
    return () => this.listeners.delete(listener);
  }

  dispose() {
    this.disposed = true;
    this.media?.removeEventListener("change", this.changed);
    this.listeners.clear();
  }
}
