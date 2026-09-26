/* One compatibility decision for the current viewer and retained backend. */
"use strict";
window.floeXpraViewer = Object.freeze({
  version: 1,
  getClient: () => window.floeXpraClient || null,
  capabilities(client) {
    const error = client.floeInput?.error;
    const input = error === "INPUT_MODULE_VERSION_UNSUPPORTED" ? "restart-required"
      : error === "INPUT_VERSION_UNSUPPORTED" ? "unsupported"
      : error ? "unavailable"
      : client.floeInput?.version === 1 ? "ready" : "unsupported";
    return Object.freeze({
      display: client.floeDisplay?.version === 2 ? "native" : "logical",
      input,
      // Ordered pointer delivery shares the backend input admission boundary.
      pointer: input === "ready" && client.floePointer?.version === 1 ? "ready" : "unavailable",
    });
  },
});
