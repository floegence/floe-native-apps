package nativeapps

import (
	_ "embed"
	"fmt"
	"strings"
)

//go:embed pointer.js
var pointerSource []byte

// Retire content event owners while preserving Xpra's window decorations and
// pointer transport. Only these reviewed v20/v21 source structures are accepted.
func preparePointerHTML(index, client, window []byte) ([]byte, []byte, []byte, error) {
	var failure error
	replace := func(source, old, next string) string {
		if strings.Count(source, old) != 1 {
			failure = fmt.Errorf("unsupported Xpra HTML pointer contract near %.64q", old)
			return source
		}
		return strings.Replace(source, old, next, 1)
	}
	section := func(source, start, end, next string) string {
		i, j := strings.Index(source, start), strings.Index(source, end)
		if i < 0 || j < i || strings.Count(source, start) != 1 || strings.Count(source, end) != 1 {
			failure = fmt.Errorf("unsupported Xpra HTML pointer section near %.64q", start)
			return source
		}
		return source[:i] + next + source[j:]
	}
	h, c, w := string(index), string(client), string(window)
	h = replace(h, `    <script type="text/javascript" src="js/Client.js"></script>`, `    <script type="text/javascript" src="js/FloePointer.js"></script>
    <script type="text/javascript" src="js/Client.js"></script>`)
	h = replace(h, "        window.oncontextmenu = event => { event.preventDefault(); };\n", "")
	h = section(h, "      let touchaction_scroll = true;", "      function init_client() {", "")
	h = replace(h, "        const touchaction = getstrparam(\"touchaction\") || \"scroll\";\n        touchaction_scroll = touchaction == \"scroll\";\n        set_touchaction();\n", "")
	h = section(h, "        $(\".windowinfocus\").children(\"canvas\").on(\"click\", function(e) {", "        $(\"#cursor_lock_button\").on(\"click\", function(e) {", "")
	c = section(c, "    const me = this;\n    const screen_element = jQuery(\"#screen\");", "  send() {", "    this.floePointer = new FloeXpraPointer(this);\n  }\n\n")
	c = section(c, "  on_mousedown(e, win) {", "  send_button_action(wid, button, pressed, coords, modifiers) {", "")
	c = section(c, "  // Source: https://deepmikoto.com/coding/1--javascript-detect-mouse-wheel-direction", "  /**\n   * Focus", "")
	for _, line := range []string{"    this.wheel_delta_x = 0;\n", "    this.wheel_delta_y = 0;\n", "    this.last_button_event = [-1, false, -1, -1];\n"} {
		c = replace(c, line, "")
	}
	for _, name := range []string{"mousemove", "mousedown", "mouseup", "mousescroll"} {
		line := "      (event, window) => this.on_" + name + "(event, window),\n"
		if strings.Count(c, line) != 2 {
			failure = fmt.Errorf("unsupported Xpra pointer callbacks: %s", name)
		}
		c = strings.ReplaceAll(c, line, "")
	}
	c = replace(c, "  _process_hello(packet) {", "  _process_hello(packet) {\n    this.floePointer.connected();")
	c = replace(c, "  close_protocol() {", "  close_protocol() {\n    this.floePointer?.disconnect();")
	// Bind completion to the actual canvas captured before asynchronous decoding.
	c = replace(c, "    const ptype = packet[0];\n    const wid = packet[1];\n    const win = this.id_to_window[wid];", "    const ptype = packet[0];\n    const wid = packet[1];\n    const win = this.id_to_window[wid];\n    const floeCanvas = win?.canvas;")
	c = replace(c, "    function decode_result(error) {", "    function decode_result(error) {\n      if (!error && start !== 0) client.floePointer?.painted(win, floeCanvas);")
	c = replace(c, "    if (coding === \"offscreen-painted\") {", "    if (coding === \"offscreen-painted\") {\n      this.floePointer?.painted(win, floeCanvas);")
	for _, name := range []string{"move", "down", "up", "scroll"} {
		w = replace(w, "    mouse_"+name+"_callback,\n", "")
		w = replace(w, "    this.mouse_"+name+"_cb = mouse_"+name+"_callback || dummy;\n", "")
	}
	w = section(w, "  register_canvas_mouse_events(canvas) {", "  set_spinner(state) {", "")
	for _, line := range []string{"    this.pointer_down = -1;\n", "    this.pointer_last_x = 0;\n", "    this.pointer_last_y = 0;\n"} {
		w = replace(w, line, "")
	}
	w = replace(w, "    this.register_canvas_mouse_events(this.canvas);\n    this.register_canvas_pointer_events(this.canvas);", "    this.client.floePointer?.register(this);")
	w = replace(w, "  init_canvas() {", "  init_canvas() {\n    this.client.floePointer?.remove(this);")
	w = replace(w, "  updateCSSGeometry() {", "  updateCSSGeometry() {\n    this.client.floePointer?.geometryChanged(this);")
	w = replace(w, "  destroy() {", "  destroy() {\n    this.client.floePointer?.remove(this);")
	w = replace(w, "      this.client.release_buttons(event_, this);", "      this.client.floePointer?.cancel();")
	w = replace(w, "      this.client.do_window_mouse_click(evt, this, false);", "      this.client.floePointer?.cancel();")
	if failure != nil {
		return nil, nil, nil, failure
	}
	return []byte(h), []byte(c), []byte(w), nil
}
