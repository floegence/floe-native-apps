package nativeapps

import (
	"compress/gzip"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func inputFixture(t *testing.T, name string) []byte {
	t.Helper()
	f, err := os.Open(filepath.Join("testdata", "input", name+".gz"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	r, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	data, err := io.ReadAll(r)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestInputHTMLHasOneExternalOwner(t *testing.T) {
	for _, version := range []string{"v20", "v21"} {
		t.Run(version, func(t *testing.T) { testInputHTML(t, version) })
	}
}

func testInputHTML(t *testing.T, version string) {
	index, client, err := prepareInputHTML(inputFixture(t, "index-"+version+".html"), inputFixture(t, "Client-"+version+".js"))
	if err != nil {
		t.Fatal(err)
	}
	for _, retired := range []string{"init_tablet_input", "enable_clipboard_autofocus", "toggle_keyboard", "last_key_packet"} {
		if strings.Contains(string(index), retired) {
			t.Fatalf("retired HTML input path: %s", retired)
		}
	}
	for _, retired := range []string{"document.addEventListener(\"keydown\"", "document.addEventListener(\"keyup\"", "window.addEventListener(\"paste\"", "query_keyboard_map()", "_poll_clipboard", "pasteboard.select()", "setTimeout(send_button_action"} {
		if strings.Contains(string(client), retired) {
			t.Fatalf("retired client input path: %s", retired)
		}
	}
	dir := t.TempDir()
	for name, data := range map[string][]byte{"Client.js": client, "Keycodes.js": inputFixture(t, "Keycodes-v20.js"), "index.html": index} {
		if err := os.WriteFile(filepath.Join(dir, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	command := exec.Command("node", "--test", "input_client_test.cjs")
	command.Env = append(os.Environ(), "FLOE_INPUT_CLIENT_FIXTURE="+dir)
	if out, err := command.CombinedOutput(); err != nil {
		t.Fatalf("browser adapter: %v\n%s", err, out)
	}
}

func TestInputProtocolDoesNotLogFailedCommitBodies(t *testing.T) {
	data, err := prepareInputProtocol(inputFixture(t, "Protocol.js"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "failed to encode packet:\", packet") || strings.Contains(string(data), "${packet_data}") {
		t.Fatal("protocol diagnostics expose input bodies")
	}
}

func TestInputHTMLRejectsUnreviewedSource(t *testing.T) {
	if _, _, err := prepareInputHTML([]byte("<html></html>"), []byte("class XpraClient {}")); err == nil {
		t.Fatal("unreviewed source accepted")
	}
}
