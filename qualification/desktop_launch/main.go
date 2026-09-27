// desktop_launch is a private native fixture for the public installed launch API.
// Its stdout contains authentication: callers capture it privately, never log it.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"runtime"
	"time"

	nativeapps "github.com/floegence/floe-native-apps"
)

func main() {
	var input struct {
		State       string            `json:"state"`
		Desktop     string            `json:"desktop"`
		Directory   string            `json:"directory"`
		Runtime     string            `json:"runtime"`
		Instance    string            `json:"instance"`
		Environment map[string]string `json:"environment"`
		HostBus     string            `json:"host_bus"`
		Documents   []string          `json:"documents"`
	}
	decoder := json.NewDecoder(io.LimitReader(os.Stdin, 1<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		fail(err)
	}
	if decoder.Decode(new(any)) != io.EOF {
		fail(fmt.Errorf("trailing fixture input"))
	}
	pkg, err := nativeapps.DesktopForPlatform("linux", runtime.GOARCH)
	if err != nil {
		fail(err)
	}
	manager, err := nativeapps.New(input.State, pkg, nil)
	if err != nil {
		fail(err)
	}
	defer manager.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	environment := make([]string, 0, len(input.Environment))
	for key, value := range input.Environment {
		environment = append(environment, key+"="+value)
	}
	plan, err := manager.PlanDesktop(ctx, input.Desktop, environment)
	if err != nil {
		fail(err)
	}
	prepared, err := manager.PrepareDesktopSession(ctx, nativeapps.DesktopSessionOptions{
		Directory: input.Directory, Runtime: input.Runtime, Instance: input.Instance, Plan: plan,
		Environment: environment, HostBus: input.HostBus, InitialDocuments: input.Documents})
	if err != nil {
		fail(err)
	}
	if err := json.NewEncoder(os.Stdout).Encode(prepared); err != nil {
		fail(err)
	}
}

func fail(err error) { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
