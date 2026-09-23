package nativeapps

import (
	_ "embed"
	"fmt"
	"strings"
)

//go:embed display.js
var displaySource []byte

func prepareDisplayHTML(index, client, window []byte) ([]byte, []byte, []byte, error) {
	var failure error
	replace := func(source, old, next string) string {
		if strings.Count(source, old) != 1 {
			failure = fmt.Errorf("unsupported Xpra HTML display contract near %.64q", old)
			return source
		}
		return strings.Replace(source, old, next, 1)
	}
	h, c, w := string(index), string(client), string(window)
	h = replace(h, `    <script type="text/javascript" src="js/Client.js"></script>`, `    <script type="text/javascript" src="js/FloeDisplay.js"></script>
    <script type="text/javascript" src="js/Client.js"></script>`)
	c = replace(c, `    if (this.scale !== 1) {
      this.container.style.width = `+"`${100 * this.scale}%`"+`;
      this.container.style.height = `+"`${100 * this.scale}%`"+`;
      this.container.style.transform = `+"`scale(${1 / this.scale})`"+`;
      this.container.style.transformOrigin = "top left";
    }`, `    this.floeDisplay?.dispose();
    this.floeDisplay = new FloeXpraDisplay(this);`)
	c = replace(c, "  _screen_resized(event) {", "  _screen_resized(event) {\n    const densityChanged = this.floeDisplay?.sync();")
	c = replace(c, "    if (this.container.clientWidth === this.desktop_width && this.container.clientHeight === this.desktop_height) {", "    if (!densityChanged && this.container.clientWidth === this.desktop_width && this.container.clientHeight === this.desktop_height) {")
	c = replace(c, `      "desktop-size": [this.desktop_width, this.desktop_height],`, `      "desktop-size": [this.desktop_width, this.desktop_height],
      "floe-display-density": this.scale,`)
	start, end := strings.Index(c, "  _get_DPI() {"), strings.Index(c, "  _get_screen_sizes() {")
	if start < 0 || end < start {
		return nil, nil, nil, fmt.Errorf("unsupported Xpra HTML DPI contract")
	}
	c = c[:start] + `  set_display_density(policy) {
    return this.floeDisplay.setPolicy(policy);
  }

  _get_DPI() {
    return 96 * this.scale;
  }

` + c[end:]
	c = replace(c, "      auto_refresh_delay: 500,", "      auto_refresh_delay: 500,\n      \"floe-display\": 1,")
	c = replace(c, "  _process_hello(packet) {", "  _process_hello(packet) {\n    this.floeDisplay.accept(packet[1]);")
	c = replace(c, "  close_protocol() {", "  close_protocol() {\n    this.floeDisplay?.dispose();")
	// The shadow pointer lives inside the scaled surface, in remote pixels.
	c = replace(c, "    style.width = `${cursor.width}px`;", "    style.width = `${cursor.width * this.scale}px`;")
	c = replace(c, "    style.height = `${cursor.height}px`;", "    style.height = `${cursor.height * this.scale}px`;")
	c = replace(c, "    style.left = `${x - cursor.xhot}px`;", "    style.left = `${x - cursor.xhot * this.scale}px`;")
	c = replace(c, "    style.top = `${y - cursor.yhot}px`;", "    style.top = `${y - cursor.yhot * this.scale}px`;")
	// Drag/resize must continue to use remote coordinates after a live density change.
	w = replace(w, "    if (this.scale !== 1) {\n      jQuery(this.div).draggable({\n        transform: true\n      });\n    }", "    jQuery(this.div).draggable({transform: true});")
	w = replace(w, "    if (this.scale !== 1) {\n      jQuery(this.div).resizable({\n        transform: true\n      });\n    }", "    jQuery(this.div).resizable({transform: true});")
	w = replace(w, "  update_offsets() {", "  update_offsets() {\n    if (this.d_header) jQuery(this.d_header).css(\"zoom\", this.scale);")
	w = replace(w, `Number.parseInt(jQuery(this.d_header).css("height"), 10);`, `Number.parseInt(jQuery(this.d_header).css("height"), 10) * this.scale;`)
	if failure != nil {
		return nil, nil, nil, failure
	}
	return []byte(h), []byte(c), []byte(w), nil
}
