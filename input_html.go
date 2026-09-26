package nativeapps

import (
	"embed"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

//go:embed input_client.js
var inputClientSource embed.FS

//go:embed viewer.js
var viewerSource []byte

// PrepareInputClient prepares an Xpra HTML5 v20/v21 distribution with exactly
// one external keyboard owner and one external pointer owner. It retains the
// graphics, clipboard and pointer transport. The destination must not exist. Neither
// a failed preparation nor an unsupported source is usable as a client.
// Consumers serve this directory through the same authenticated Xpra endpoint.
func PrepareInputClient(source, destination string) error {
	if !filepath.IsAbs(source) || !filepath.IsAbs(destination) || source == destination {
		return ErrInvalid
	}
	index, err := os.ReadFile(filepath.Join(source, "index.html"))
	if err != nil {
		return err
	}
	client, err := os.ReadFile(filepath.Join(source, "js", "Client.js"))
	if err != nil {
		return err
	}
	index, client, err = prepareInputHTML(index, client)
	if err != nil {
		return err
	}
	protocol, err := os.ReadFile(filepath.Join(source, "js", "Protocol.js"))
	if err != nil {
		return err
	}
	protocol, err = prepareInputProtocol(protocol)
	if err != nil {
		return err
	}
	window, err := os.ReadFile(filepath.Join(source, "js", "Window.js"))
	if err != nil {
		return err
	}
	index, client, window, err = prepareCursorHTML(index, client, window)
	if err != nil {
		return err
	}
	index, client, window, err = prepareDisplayHTML(index, client, window)
	if err != nil {
		return err
	}
	index, client, window, err = prepareLayoutHTML(index, client, window)
	if err != nil {
		return err
	}
	index, client, window, err = preparePointerHTML(index, client, window)
	if err != nil {
		return err
	}
	worker, err := os.ReadFile(filepath.Join(source, "js", "OffscreenDecodeWorker.js"))
	if err != nil {
		return err
	}
	worker, err = prepareCanvasWorker(worker)
	if err != nil {
		return err
	}
	if err = os.Mkdir(destination, 0700); err != nil {
		return err
	}
	complete := false
	defer func() {
		if !complete {
			_ = os.RemoveAll(destination)
		}
	}()
	// Some system packages symlink assets into shared JavaScript directories.
	// Copy their contents without publishing host paths into the served tree.
	var total int64
	var copyDirectory func(string, string, int) error
	copyDirectory = func(from, to string, depth int) error {
		if depth > 16 {
			return errors.New("native HTML asset nesting exceeds limit")
		}
		entries, err := os.ReadDir(from)
		if err != nil {
			return err
		}
		for _, entry := range entries {
			in, out := filepath.Join(from, entry.Name()), filepath.Join(to, entry.Name())
			info, err := os.Stat(in)
			if err != nil {
				return err
			}
			if info.IsDir() {
				if err = os.Mkdir(out, 0700); err != nil {
					return err
				}
				if err = copyDirectory(in, out, depth+1); err != nil {
					return err
				}
			} else {
				total += info.Size()
				if !info.Mode().IsRegular() || info.Size() > 32<<20 || total > 128<<20 {
					return errors.New("native HTML assets exceed limits")
				}
				data, err := os.ReadFile(in)
				if err != nil {
					return err
				}
				if err = os.WriteFile(out, data, 0600); err != nil {
					return err
				}
			}
		}
		return nil
	}
	if err = copyDirectory(source, destination, 0); err != nil {
		return err
	}
	adapter, _ := inputClientSource.ReadFile("input_client.js")
	for name, data := range map[string][]byte{"index.html": index, "js/Client.js": client, "js/FloeInput.js": adapter, "js/Protocol.js": protocol, "js/Window.js": window, "js/FloeCursor.js": cursorSource, "js/FloeDisplay.js": displaySource, "js/FloeLayout.js": layoutSource, "js/FloeCanvas.js": canvasSource, "js/OffscreenDecodeWorker.js": worker, "js/FloeViewer.js": viewerSource, "js/FloePointer.js": pointerSource} {
		if err = os.WriteFile(filepath.Join(destination, name), data, 0600); err != nil {
			return err
		}
	}
	complete = true
	return nil
}

func prepareInputProtocol(data []byte) ([]byte, error) {
	source := string(data)
	for old, next := range map[string]string{
		"this.error(\"Error: failed to encode packet:\", packet);": "this.error(\"Error: failed to encode packet type:\", packet[0]);",
		"this.error(`packet=${packet_data}`);":                     "this.error(\"Packet content withheld\");",
		"this.error(` packet data: ${packet_data}`)":               "this.error(\"Packet content withheld\")",
	} {
		if strings.Count(source, old) != 1 {
			return nil, errors.New("unsupported Xpra HTML protocol diagnostics")
		}
		source = strings.Replace(source, old, next, 1)
	}
	return []byte(source), nil
}

func prepareInputHTML(index, client []byte) ([]byte, []byte, error) {
	var failure error
	replace := func(source, old, next string) string {
		if failure != nil {
			return source
		}
		if strings.Count(source, old) != 1 {
			failure = fmt.Errorf("unsupported Xpra HTML input contract near %.64q", old)
			return source
		}
		return strings.Replace(source, old, next, 1)
	}
	section := func(source, start, end, next string) string {
		i, j := strings.Index(source, start), strings.Index(source, end)
		if i < 0 || j < i || strings.Count(source, start) != 1 || strings.Count(source, end) != 1 {
			failure = fmt.Errorf("unsupported Xpra HTML input section near %.64q", start)
			return source
		}
		return source[:i] + next + source[j:]
	}
	c, h := string(client), string(index)
	h = replace(h, `    <script type="text/javascript" src="js/Client.js"></script>`, `    <script type="text/javascript" src="js/FloeViewer.js"></script>
    <script type="text/javascript" src="js/Client.js"></script>`)
	for _, retired := range []string{"const PASTEBOARD_SELECTOR = \"#pasteboard\";\n", "    this.key_packets = [];\n", "    this.clipboard_delayed_event_time = 0;\n", "    this.last_keycode_pressed = 0;\n", "    this.last_key_packet = [];\n"} {
		c = replace(c, retired, "")
	}
	c = section(c, "  init_keyboard() {", "  send_keymap(", `  init_keyboard() {
    this.keyboard_map = {};
    this.altgr_state = false;
    this.capture_keyboard = true;
    this.floeInput = new FloeXpraInput(this);
  }

`)
	c = section(c, "    let allow_default = false;", "    const wid = this.focused_wid;\n    this.debug(\"keyboard\"", "    const allow_default = false;\n")
	c = replace(c, "    if (!this.capture_keyboard) {\n      return true;\n    }\n", "")
	c = replace(c, "const wid = this.focused_wid;\n    this.debug(\"keyboard\"", "const wid = event.floeWid;\n    this.debug(\"keyboard\"")
	c = replace(c, "\n    this.key_packets.push(packet);", "\n    this.send(packet);")
	c = replace(c, "\n      this.key_packets.push(packet);", "\n      this.send(packet);")
	c = section(c, "    //if there is a chance that we're in the process of handling", "  _get_keyboard_layout() {", `
    return allow_default;
  }

`)
	c = section(c, "    this.debug(\"keyboard\", \"last keycode pressed=\"", "    //sync numlock", "")
	c = section(c, "    const map_string = this.keyboard_map[keyname];", "    //special case for numpad,", `    if (keyname in KEY_TO_NAME) {
      keyname = KEY_TO_NAME[keyname];
    } else if (keyname === "" && keystring in KEY_TO_NAME) {
      keyname = KEY_TO_NAME[keystring];
    }
`)
	c = section(c, "  init_clipboard() {", "  /**\n   * Focus", "")
	c = replace(c, "    let send_delay = 0;\n    const client = this;\n    if (client.clipboard_direction !== \"to-server\" && this._poll_clipboard(e)) {\n      send_delay = CLIPBOARD_EVENT_DELAY;\n    }", "    const client = this;")
	c = section(c, "    function send_button_action() {", "  send_button_action(wid, button, pressed, coords, modifiers) {", "    client.send_button_action(wid, button, pressed, coords, modifiers);\n  }\n\n")
	c = replace(c, "  _process_hello(packet) {", "  _process_hello(packet) {\n    this.floeInput.connected(packet[1][\"floe-input\"]);")
	c = replace(c, "  close_protocol() {", "  close_protocol() {\n    this.floeInput?.disconnect();")
	c = replace(c, "  debug() {", "  debug() {\n    if ([\"keyboard\", \"clipboard\", \"network\"].includes(arguments[0])) return;")
	c = replace(c, "  cdebug() {", "  cdebug() {\n    if ([\"keyboard\", \"clipboard\", \"network\"].includes(arguments[0])) return;")
	c = replace(c, "\"log\", \"redraw\", \"stop-audio\", \"toggle-keyboard\",", "\"log\", \"redraw\", \"stop-audio\",")
	c = replace(c, "    else if (action === \"toggle-keyboard\") {\n        toggle_keyboard();\n    }\n", "")
	c = replace(c, "    this.send([PACKET_TYPES.hello, this.capabilities]);", "    this.capabilities.keyboard_sync = true;\n    this.capabilities.key_repeat = [0, 0];\n    this.capabilities[\"floe-input\"] = 1;\n    this.capabilities.wants = [...new Set([...(this.capabilities.wants || []), \"features\"])];\n    this.send([PACKET_TYPES.hello, this.capabilities]);")
	h = replace(h, "    <script type=\"text/javascript\" src=\"js/Client.js\"></script>", "    <script type=\"text/javascript\" src=\"js/FloeInput.js\"></script>\n    <script type=\"text/javascript\" src=\"js/Client.js\"></script>")
	h = replace(h, "    <!-- simple-keyboard -->\n    <script type=\"application/javascript\" src=\"js/lib/simple-keyboard.js\"></script>\n    <link rel=\"stylesheet\" href=\"css/simple-keyboard.css\" />\n", "")
	h = replace(h, "      <textarea id=\"pasteboard\" readonly style=\"display: block; position: absolute; left: -99em\"></textarea>\n", "")
	h = replace(h, "    <div class=\"simple-keyboard\" style=\"display: block; position: fixed; bottom: 0; width: 100%\"></div>\n", "")
	h = section(h, "      function enable_clipboard_autofocus() {", "      function show_about(event) {", "")
	h = strings.ReplaceAll(h, "enable_clipboard_autofocus();", "")
	h = strings.ReplaceAll(h, "cancel_clipboard_autofocus();", "")
	h = section(h, "      function init_tablet_input(client) {", "      function init_file_transfer(client) {", "")
	for _, line := range []string{"        init_tablet_input(client);\n", "        init_clipboard(client);\n", "        init_keyboard(client);\n"} {
		h = replace(h, line, "")
	}
	h = replace(h, "        client = init_client();", "        client = init_client();\n        window.floeXpraClient = client;\n        $(\"#keyboard_button, #clipboard_button\").parent().remove();")
	h = section(h, "        $(\"#keyboard_button\").on(\"click\", function(e) {", "        $(\".windowinfocus\").children(\"canvas\").on(\"click\", function(e) {", "")
	h = section(h, "        // disable right click menu:", "      function load_default_settings() {", "        window.oncontextmenu = event => { event.preventDefault(); };\n      }\n\n")
	if failure != nil {
		return nil, nil, failure
	}
	return []byte(h), []byte(c), nil
}
