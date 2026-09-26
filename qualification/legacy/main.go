// Prepare an immutable published bootstrap-v1 fixture, never the user's instance.
package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"

	nativeapps "github.com/floegence/floe-native-apps"
)

func main() {
	root, source := os.Args[1], os.Args[2]
	if err := os.Mkdir(root, 0700); err != nil {
		panic(err)
	}
	input, err := nativeapps.PrepareClientInput(filepath.Join(root, "input"), runtime.GOARCH)
	if err != nil {
		panic(err)
	}
	html := filepath.Join(root, "www")
	if err := nativeapps.PrepareInputClient(source, html); err != nil {
		panic(err)
	}
	data, err := json.Marshal(struct {
		Launcher string
		Args     []string
		HTML     string
	}{input.Launcher, input.XpraArgs(os.Environ()), html})
	if err != nil {
		panic(err)
	}
	if err := os.WriteFile(filepath.Join(root, "fixture.json"), data, 0600); err != nil {
		panic(err)
	}
}
