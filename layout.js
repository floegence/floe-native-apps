/* Window layout is independent of native maximize/minimize state. */
"use strict";

class FloeXpraLayout {
  constructor(client) {
    this.client = client;
    this.windows = new Map();
    this.disposed = false;
  }

  managed(win) { return !this.disposed && this.windows.has(win); }

  set(win, policy) {
    if (!["viewport", "dialog", "native"].includes(policy)) throw new Error("Invalid window layout policy");
    if (this.disposed || !win || this.client.id_to_window[win.wid] !== win) return false;
    if (policy === "native") { this.windows.delete(win); return true; }
    if (win.override_redirect || win.tray) return false;
    if (this.windows.get(win)?.policy !== policy) this.windows.set(win, {policy, key:null});
    this.update(win);
    return true;
  }

  update(win) {
    const state = this.windows.get(win);
    if (this.disposed || !state) return false;
    const client = this.client;
    const [width, height] = client._get_desktop_size();
    const constraints = win.metadata["size-constraints"] || {};
    const pairs = ["minimum-size", "maximum-size", "base-size", "increment"].map(name =>
      [0, 1].map(i => Number.isSafeInteger(constraints[name]?.[i]) && constraints[name][i] > 0 ? constraints[name][i] : 0));
    const offsets = [win.leftoffset, win.topoffset, win.rightoffset, win.bottomoffset];
    const key = JSON.stringify([width, height, client.scale, state.policy, pairs, offsets]);
    if (state.key === key) return true;
    state.key = key;
    const margin = state.policy === "dialog" ? 12 * client.scale : 0;
    const size = (axis, available, current) => {
      const [minimum, maximum, base, increment] = pairs.map(pair => pair[axis]);
      const low = Math.max(1, minimum), high = Math.max(low, maximum || 32768);
      const target = Math.max(low, Math.min(high, available, state.policy === "dialog" ? current : Infinity));
      const step = increment || 1, origin = base || minimum;
      // Respect native hints even when a minimum exceeds the viewport. A server
      // acknowledgement must never restart negotiation for this same input.
      const first = origin + Math.ceil((low - origin) / step) * step;
      return Math.min(high, Math.max(first, origin + Math.floor((target - origin) / step) * step));
    };
    const w = size(0, width - offsets[0] - offsets[2] - 2 * margin, win.w);
    const h = size(1, height - offsets[1] - offsets[3] - 2 * margin, win.h);
    const x = offsets[0] + (state.policy === "dialog" ? Math.max(margin, Math.min(win.x - offsets[0], width - w - offsets[0] - offsets[2] - margin)) : 0);
    const y = offsets[1] + (state.policy === "dialog" ? Math.max(margin, Math.min(win.y - offsets[1], height - h - offsets[1] - offsets[3] - margin)) : 0);
    if (win.x !== x || win.y !== y || win.w !== w || win.h !== h) {
      Object.assign(win, {x, y, w, h});
      win.updateCSSGeometry();
      win.geometry_cb(win);
    }
    return true;
  }

  accept(win, x, y, w, h) {
    if (!this.managed(win)) return false;
    if (win.x !== x || win.y !== y || win.w !== w || win.h !== h) {
      Object.assign(win, {x, y, w, h});
      win.updateCSSGeometry();
    }
    return true;
  }

  remove(win) { this.windows.delete(win); }
  dispose() { this.disposed = true; this.windows.clear(); }
}
