/* External pointer transport for the reviewed Xpra HTML client. No DOM owners. */
"use strict";

class FloeXpraPointer {
  constructor(client) {
    this.client = client;
    this.version = 1;
    this.generation = 0;
    this.windows = new Map();
    this.canvases = new WeakMap();
    this.buttons = new Map();
    this.scroll = null;
    this.onInvalidate = null;
  }

  geometry(win) {
    const g = win.get_internal_geometry();
    return [g.x,g.y,g.w,g.h,this.client.scale].join(":");
  }

  token(win) {
    return Object.freeze({wid:win.wid, window:win, canvas:win.canvas, generation:this.generation});
  }

  register(win) {
    this.remove(win);
    const entry = {token:this.token(win), geometry:this.geometry(win), painted:false};
    this.windows.set(win, entry);
    this.canvases.set(win.canvas, entry);
    win.canvas.setAttribute("data-floe-remote-pointer", "");
  }

  painted(win, canvas) {
    const entry = this.windows.get(win);
    if (entry && canvas === entry.token.canvas && win.canvas === canvas && this.client.id_to_window[win.wid] === win)
      entry.painted = true;
  }

  geometryChanged(win) {
    const entry = this.windows.get(win);
    if (!entry || entry.geometry === this.geometry(win)) return;
    this.cancel();
    entry.geometry = this.geometry(win);
    entry.token = this.token(win);
  }

  remove(win) {
    const entry = this.windows.get(win);
    if (!entry) return;
    this.cancel();
    this.canvases.delete(entry.token.canvas);
    this.windows.delete(win);
  }

  connected() {
    this.disconnect();
    // Window objects are always recreated by the connection's window packets.
  }

  disconnect() {
    this.cancel();
    this.generation++;
    this.windows.clear();
    this.canvases = new WeakMap();
    this.client.mousedown_event = this.client.mouseup_event = null;
  }

  cancel() {
    this.onInvalidate?.();
    for (const item of [...this.buttons.values()]) this.release(item.target);
    this.scroll = null;
  }

  live(target) {
    return target && target.generation === this.generation && this.client.connected &&
      this.client.id_to_window[target.wid] === target.window && target.window.canvas === target.canvas;
  }

  isTargetValid(target) {
    const entry = target && this.windows.get(target.window);
    return Boolean(entry && entry.token === target && entry.painted && this.live(target) &&
      !this.client.server_readonly && !this.client.mouse_grabbed && !target.window.minimized &&
      target.canvas.isConnected && target.canvas.getClientRects().length &&
      entry.geometry === this.geometry(target.window));
  }

  targetForWindow(win) {
    this.geometryChanged(win);
    const target = this.windows.get(win)?.token;
    return this.isTargetValid(target) ? target : null;
  }

  resolveTarget(event) {
    const canvas = event.target?.closest?.("canvas[data-floe-remote-pointer]");
    const entry = canvas && this.canvases.get(canvas);
    if (!entry) return null;
    return this.targetForWindow(entry.token.window);
  }

  coordinates(command, target) {
    // Preserve the existing display-density mapping for position only. Wheel
    // distance is already in CSS pixels and must never be multiplied by DPR.
    const mouse = this.client.getMouse(command);
    const g = target.window.get_internal_geometry();
    return [Math.round(mouse.x),Math.round(mouse.y),Math.round(mouse.x-g.x),Math.round(mouse.y-g.y)];
  }

  release(target) {
    if (this.scroll?.target === target) this.scroll = null;
    for (const [button,item] of this.buttons) {
      if (item.target !== target) continue;
      this.buttons.delete(button);
      if (this.live(target)) this.client.send_button_action(target.wid,item.number,false,item.coords,[]);
      else this.client.buttons_pressed.delete(item.number);
    }
  }

  sendPointer(command, target) {
    if (![command.clientX,command.clientY].every(Number.isFinite)) return false;
    if (command.kind === "up") {
      const item = this.buttons.get(command.button);
      if (!item || item.target !== target) return false;
      this.buttons.delete(command.button);
      if (!this.live(target)) { this.client.buttons_pressed.delete(item.number); return false; }
      const coords = this.isTargetValid(target) ? this.coordinates(command,target) : item.coords;
      this.client.send_button_action(target.wid,item.number,false,coords,this.client._keyb_get_modifiers(command));
      this.client.mouseup_event = this.decorationEvent(command,target);
      return true;
    }
    if (!this.isTargetValid(target)) return false;
    const c = this.client, coords = this.coordinates(command,target);
    const modifiers = c._keyb_get_modifiers(command);
    if (command.kind === "move") {
      c.send([PACKET_TYPES.pointer_position,target.wid,coords,modifiers,[]]);
      for (const item of this.buttons.values()) if (item.target === target) item.coords = coords;
    } else if (command.kind === "down") {
      if (!Number.isInteger(command.button) || command.button < 0 || command.button > 4 || this.buttons.has(command.button)) return false;
      let number = command.button + 1;
      const modifier = (c.middle_emulation_modifier || "").toLowerCase();
      if (number === 1 && {control:command.ctrlKey,meta:command.metaKey,alt:command.altKey,shift:command.shiftKey}[modifier]) {
        number = c.middle_emulation_button || 2;
        const index = modifiers.indexOf(c.translate_modifiers([modifier])[0]);
        if (index >= 0) modifiers.splice(index,1);
      }
      if (number === 4) number = 8;
      else if (number === 5) number = 9;
      if (c.focused_wid !== target.wid) c.set_focus(target.window);
      this.buttons.set(command.button,{target,number,coords});
      c.mousedown_event = this.decorationEvent(command,target);
      c.mouseup_event = null;
      c.send_button_action(target.wid,number,true,coords,modifiers);
    } else if (command.kind === "scroll") {
      if (![command.dx,command.dy].every(Number.isFinite)) return false;
      if (this.scroll?.target !== target) this.scroll = {target,x:0,y:0};
      for (const [axis,delta,reverse,positive,negative] of [
        ["x",command.dx,c.scroll_reverse_x === true,7,6],
        ["y",command.dy,c.scroll_reverse_y === true,5,4],
      ]) {
        const value = this.scroll[axis] + delta * (reverse ? -1 : 1);
        const unit = c.server_precise_wheel ? 120 / 1000 : 120;
        const steps = Math.trunc(value / unit);
        this.scroll[axis] = value - steps * unit;
        if (!steps) continue;
        const button = steps > 0 ? positive : negative;
        if (c.server_precise_wheel) c.send([PACKET_TYPES.wheel_motion,target.wid,button,-steps,coords,modifiers,[]]);
        else for (let i=0;i<Math.abs(steps);i++) {
          c.send_button_action(target.wid,button,true,coords,modifiers);
          c.send_button_action(target.wid,button,false,coords,modifiers);
        }
      }
    } else return false;
    return true;
  }

  decorationEvent(command,target) {
    // Existing server-initiated decoration dragging consumes this event record.
    // No content event is dispatched or simulated.
    return {type:command.kind === "up" ? "mouseup" : "mousedown",which:command.button+1,
      target:target.canvas,clientX:command.clientX,clientY:command.clientY,
      pageX:command.clientX + window.scrollX,pageY:command.clientY + window.scrollY};
  }
}
