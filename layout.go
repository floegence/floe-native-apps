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
	w = replace(w, old, `    floeResizeCanvas(this.canvas, this.w, this.h);
    floeResizeCanvas(this.offscreen_canvas, this.w, this.h);`)
	if failure != nil {
		return nil, nil, nil, failure
	}
	return []byte(h), []byte(c), []byte(w), nil
}

func prepareCanvasWorker(source []byte) ([]byte, error) {
	old := "    this.canvas.width = w;\n    this.canvas.height = h;"
	if strings.Count(string(source), old) != 1 {
		return nil, fmt.Errorf("unsupported Xpra offscreen canvas geometry contract")
	}
	prepared := strings.Replace(string(source), old, "    floeResizeCanvas(this.canvas, w, h);", 1)
	return []byte(prepared + "\n" + string(canvasSource)), nil
}
