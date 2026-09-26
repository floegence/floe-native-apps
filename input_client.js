/* Client input adapter for the prepared Xpra HTML client. */
"use strict";

class FloeXpraInput {
  constructor(client) {
    this.client = client;
    this.version = 0;
    this.generation = 0;
    this.sequence = 0;
    this.pending = new Map();
    this.held = new Map();
    this.target = null;
    this.onError = null;
    client.packet_handlers["floe-input-result"] = packet => this.result(packet);
  }

  connected(version) {
    this.disconnect();
    this.version = version === 1 ? 1 : 0;
  }

  disconnect() {
    this.generation++;
    this.version = 0;
    this.error = null;
    this.target = null;
    this.held.clear();
    this.pending.clear();
    this.pasteKey = null;
  }

  bindTarget(wid) {
    if (this.target?.wid === wid && this.target.generation === this.generation) return this.target;
    this.release(this.target);
    this.pasteKey = null;
    this.target = wid > 0 && this.version === 1
      ? Object.freeze({wid, generation: this.generation}) : null;
    return this.target;
  }

  valid(target) {
    return target !== null && target === this.target && this.version === 1 &&
      this.client.connected && !this.client.server_readonly &&
      Boolean(this.client.id_to_window[target.wid]);
  }

  fail(code) {
    this.error = code;
    this.release(this.target);
    this.version = 0;
    this.target = null;
    this.pending.clear();
    this.onError?.(code);
  }

  commitText(text, target) {
    if (!this.valid(target)) return false;
    // One protocol operation is one complete commit. Never split a selection
    // replacement or replay a rejected operation in another input context.
    if (typeof text !== "string" || !text || /[\uD800-\uDFFF]/u.test(text) ||
        new TextEncoder().encode(text).length > 16000 || text.includes("\0")) {
      this.fail("INPUT_TEXT_INVALID");
      return false;
    }
    if (this.pending.size >= 256 || !Number.isSafeInteger(this.sequence + 1)) {
      this.fail("INPUT_QUEUE_FULL");
      return false;
    }
    const sequence = ++this.sequence;
    this.pending.set(sequence, target);
    this.client.send(["floe-input", sequence, target.wid, text]);
    return true;
  }

  result(packet) {
    const [, sequence, error] = packet;
    if (!this.pending.delete(sequence)) return;
    if (error) this.fail(String(error));
  }

  sendKey(key, target) {
    if (!this.valid(target)) return false;
    const identity = key.code || key.key;
    let event;
    if (!key.pressed) {
      event = this.held.get(identity);
      if (!event) return false;
      this.held.delete(identity);
      event = {...event, shiftKey:key.shiftKey, ctrlKey:key.ctrlKey, altKey:key.altKey, metaKey:key.metaKey};
    } else {
      // Xpra's keymap contract uses browser virtual keycodes. Reuse its reviewed
      // naming tables, including non-US character names and platform modifiers.
      const name = KEY_TO_NAME[key.code] || CHAR_TO_NAME[key.key] || key.key;
      const translated = {ArrowUp:"Up", ArrowDown:"Down", ArrowLeft:"Left", ArrowRight:"Right"}[name] || name;
      const entry = Object.entries(CHARCODE_TO_NAME).find(([, value]) =>
        value === translated || value.toLowerCase() === translated.toLowerCase());
      event = {...key, which:entry ? Number(entry[0]) : 0, floeWid:target.wid};
      if (key.repeat && this.held.has(identity)) this.client._keyb_process(false, event);
      this.held.set(identity, event);
    }
    this.client._keyb_process(key.pressed, event);
    return true;
  }

  release(target) {
    if (!target || target !== this.target) return;
    for (const event of this.held.values()) {
      if (this.client.connected && this.version === 1)
        this.client._keyb_process(false, {...event, shiftKey:false, ctrlKey:false, altKey:false, metaKey:false});
    }
    this.held.clear();
  }

  clipboard(event, target) {
    if (!this.valid(target) || !this.client.clipboard_enabled) return false;
    const modifier = Utilities.isMacOS() ? event.metaKey : event.ctrlKey;
    const name = event.key.toLowerCase();
    if (event.shiftKey && event.key === "Insert" || modifier && name === "v") {
      // The native paste event owns both the clipboard token and the shortcut.
      // Do not also send this key or submit its text through the IME controller.
      this.pasteKey = {key:event.key, code:event.code, repeat:false, location:event.location,
        shiftKey:event.shiftKey, ctrlKey:event.ctrlKey, altKey:event.altKey, metaKey:event.metaKey};
      return true;
    }
    if (!modifier || !["c", "x"].includes(name)) return false;
    event.preventDefault();
    const packet = {key:event.key, code:event.code, repeat:event.repeat, location:event.location,
      shiftKey:event.shiftKey, ctrlKey:event.ctrlKey, altKey:event.altKey, metaKey:event.metaKey};
    this.sendKey({...packet, pressed:true}, target);
    this.sendKey({...packet, pressed:false}, target);
    return true;
  }

  paste(event, target) {
    event.preventDefault();
    event.stopPropagation();
    const key = this.pasteKey;
    this.pasteKey = null;
    if (!this.valid(target) || !this.client.clipboard_enabled || !event.clipboardData) return;
    const data = event.clipboardData;
    if (data.files?.length) {
      for (const file of data.files) this.client.send_file(file);
      return;
    }
    const text = data.getData("text/plain");
    this.client.clipboard_buffer = text;
    this.client.send_clipboard_token(Utilities.StringToUint8(text));
    const packet = key || {key:"v",code:"KeyV",repeat:false,location:0,shiftKey:false,
      ctrlKey:!Utilities.isMacOS(),metaKey:Utilities.isMacOS(),altKey:false};
    this.sendKey({...packet, pressed:true}, target);
    this.sendKey({...packet, pressed:false}, target);
  }
}

// The host supplies product permissions, first-frame gating, window binding and
// the published remote-input controller. This adapter registers no DOM input.
window.floeXpraInput = {version:2, getClient:() => window.floeXpraClient || null};
