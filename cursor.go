package nativeapps

import (
	_ "embed"
	"fmt"
	"strings"
)

//go:embed cursor.js
var cursorSource []byte

// All reviewed HTML versions share this cursor owner. The original client still
// owns pointer packets; normalization changes neither coordinates nor buttons.
func prepareCursorHTML(index, client, window []byte) ([]byte, []byte, []byte, error) {
	var failure error
	replace := func(source, old, next string) string {
		if strings.Count(source, old) != 1 {
			failure = fmt.Errorf("unsupported Xpra HTML cursor contract near %.64q", old)
			return source
		}
		return strings.Replace(source, old, next, 1)
	}
	section := func(source, start, end, next string) string {
		i, j := strings.Index(source, start), strings.Index(source, end)
		if i < 0 || j < i || strings.Count(source, start) != 1 || strings.Count(source, end) != 1 {
			failure = fmt.Errorf("unsupported Xpra HTML cursor section near %.64q", start)
			return source
		}
		return source[:i] + next + source[j:]
	}
	h, c, w := string(index), string(client), string(window)
	h = replace(h, "    <script type=\"text/javascript\" src=\"js/Client.js\"></script>", "    <script type=\"text/javascript\" src=\"js/FloeCursor.js\"></script>\n    <script type=\"text/javascript\" src=\"js/Client.js\"></script>")
	c = replace(c, "  _process_hello(packet) {", "  _process_hello(packet) {\n    this.floeCursor?.dispose();\n    this.floeCursor = new FloeXpraCursor(this);")
	c = replace(c, "  close_protocol() {", "  close_protocol() {\n    this.floeCursor?.dispose();\n    this.floeCursor = null;")
	c = replace(c, "    this.id_to_window[wid] = win;", "    this.id_to_window[wid] = win;\n    win.set_cursor(this.floeCursor?.current);")
	c = section(c, "  reset_cursor() {", "  _process_window_icon(packet) {", `  reset_cursor() {
    this.floeCursor?.reset();
  }

  _process_cursor(packet) {
    this.floeCursor?.receive(packet);
  }

`)
	c = section(c, "    let cursor_url;", "    style.display = \"inline\";", `    const cursor = this.floeCursor?.current;
    if (!win || !cursor) { style.display = "none"; return; }
    style.width = `+"`${cursor.width}px`"+`;
    style.height = `+"`${cursor.height}px`"+`;
    shadow_pointer.src = cursor.url;
    style.left = `+"`${x - cursor.xhot}px`"+`;
    style.top = `+"`${y - cursor.yhot}px`"+`;
`)
	w = section(w, "  reset_cursor() {", "  eos() {", `  set_cursor(cursor) {
    this.div.style.cursor = cursor?.css || "default";
  }

`)
	w = replace(w, "    this.cursor_data = null;\n", "")
	if failure != nil {
		return nil, nil, nil, failure
	}
	return []byte(h), []byte(c), []byte(w), nil
}
