/* Optional host-owned network carrier; never changes graphics worker capability. */
"use strict";
const FloeXpraTransport = Object.freeze({
  required() {
    return document.documentElement.hasAttribute("data-floe-host-transport");
  },
  socket() {
    if (!this.required()) return WebSocket;
    const constructor = globalThis.floeHostTransport?.WebSocket;
    if (typeof constructor !== "function") throw Error("Host viewer transport is unavailable");
    return constructor;
  },
});
