package nativeapps

import (
	_ "embed"
	"fmt"
	"strings"
)

//go:embed canvas.js
var canvasSource []byte

//go:embed layout.js
var layoutSource []byte

func prepareLayoutHTML(index, client, window []byte) ([]byte, []byte, []byte, error) {
	var failure error
	replace := func(source, old, next string) string {
		if strings.Count(source, old) != 1 {
			failure = fmt.Errorf("unsupported Xpra HTML layout contract near %.64q", old)
			return source
		}
		return strings.Replace(source, old, next, 1)
	}
	h, c, w := string(index), string(client), string(window)
	h = replace(h, `    <script type="text/javascript" src="js/Client.js"></script>`, `    <script type="text/javascript" src="js/FloeCanvas.js"></script>
    <script type="text/javascript" src="js/FloeLayout.js"></script>
    <script type="text/javascript" src="js/Client.js"></script>`)
	c = replace(c, "    this.floeDisplay = new FloeXpraDisplay(this);", "    this.floeDisplay = new FloeXpraDisplay(this);\n    this.floeLayout?.dispose();\n    this.floeLayout = new FloeXpraLayout(this);")
	c = replace(c, "  set_display_density(policy) {", "  set_window_layout(wid, policy) {\n    return this.floeLayout.set(this.id_to_window[wid], policy);\n  }\n\n  set_display_density(policy) {")
	c = replace(c, "  close_protocol() {", "  close_protocol() {\n    this.floeLayout?.dispose();")
	w = replace(w, "  screen_resized() {", "  screen_resized() {\n    if (this.client.floeLayout?.update(this)) return;")
	w = replace(w, "  move_resize(x, y, w, h) {", "  move_resize(x, y, w, h) {\n    if (this.client.floeLayout?.accept(this, x, y, w, h)) return;")
	w = replace(w, "  apply_size_constraints() {", "  apply_size_constraints() {\n    this.client.floeLayout?.update(this);")
	w = replace(w, "  destroy() {", "  destroy() {\n    this.client.floeLayout?.remove(this);")
	for _, state := range []string{"maximized", "fullscreen"} {
		old := "  set_" + state + "(" + state + ") {"
		w = replace(w, old, old+"\n    if (this.client.floeLayout?.managed(this)) {\n      this."+state+" = "+state+";\n      return;\n    }")
	}
	old := `    // set size of both canvas if needed
    if (this.canvas.width !== this.w) {
      this.canvas.width = this.w;
    }
    if (this.canvas.height !== this.h) {
      this.canvas.height = this.h;
    }
    if (this.offscreen_canvas.width !== this.w) {
      this.offscreen_canvas.width = this.w;
    }
    if (this.offscreen_canvas.height !== this.h) {
      this.offscreen_canvas.height = this.h;
    }`
	w = replace(w, old, `    for (const canvas of new Set([this.canvas, this.offscreen_canvas, this.draw_canvas])) {
      floeResizeCanvas(canvas, this.w, this.h);
    }`)
	if failure != nil {
		return nil, nil, nil, failure
	}
	return []byte(h), []byte(c), []byte(w), nil
}

func prepareCanvasWorker(source []byte) ([]byte, error) {
	prepared := string(source)
	replace := func(old, next string) error {
		if strings.Count(prepared, old) != 1 {
			return fmt.Errorf("unsupported Xpra offscreen canvas contract near %.64q", old)
		}
		prepared = strings.Replace(prepared, old, next, 1)
		return nil
	}
	for _, change := range [][2]string{
		{"    this.canvas.width = w;\n    this.canvas.height = h;", "    floeResizeCanvas(this.canvas, w, h);"},
		{`    // Tell the server we are done with this packet
    self.postMessage({
      draw: clonepacket,
      start
    });`, `    // Decoding alone must not acknowledge painted pixels or authorize input.`},
		{`    if (packet[6] === "throttle") {
      return;
    }`, `    if (this.closed || !this.canvas) { packet[7]?.close?.(); return; }
    if (packet[6] === "throttle") {
      self.postMessage({draw: clonepacket, start: 0});
      return;
    }`},
		{`    this.paint_packet(wid, coding, image, x, y, w, h);`, `    // The existing decode queue owns paint order. A second rAF queue allowed
    // damage acknowledgements and later geometry to overtake these pixels.
    this.do_paint_packet(wid, coding, image, x, y, w, h);
    if (coding.startsWith("bitmap")) image?.close?.();
    self.postMessage({draw: clonepacket, start: coding === "void" ? 0 : start});`},
		{"  close() {\n    this.eos();", "  close() {\n    this.closed = true;\n    this.eos();"},
	} {
		if err := replace(change[0], change[1]); err != nil {
			return nil, err
		}
	}
	start := strings.Index(prepared, "  paint_packet(wid, coding, image, x, y, width, height) {")
	end := strings.Index(prepared, "  do_paint_packet(wid, coding, image, x, y, width, height) {")
	if start < 0 || end <= start || strings.Count(prepared[start:end], "requestAnimationFrame(") != 1 {
		return nil, fmt.Errorf("unsupported Xpra offscreen paint scheduling contract")
	}
	prepared = prepared[:start] + prepared[end:]
	return []byte(prepared + "\n" + string(canvasSource)), nil
}
