// floe-native-apps is an explicit qualification and offline acquisition tool.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"time"

	nativeapps "github.com/floegence/floe-native-apps"
)

func main() {
	state := flag.String("state", "", "absolute private preparation directory")
	architecture := flag.String("arch", runtime.GOARCH, "Linux target architecture")
	bundle := flag.String("bundle", "", "write a verified offline ZIP for the target architecture")
	check := flag.String("check", "", "self-check an installed native root")
	input := flag.String("prepare-input", "", "prepare client input support in a new private directory")
	html := flag.String("input-html", "", "installed Xpra HTML distribution for client input preparation")
	flag.Parse()
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	if *input != "" {
		support, err := nativeapps.PrepareClientInput(*input, *architecture)
		if err != nil {
			fail(err)
		}
		www := ""
		if *html != "" {
			www = filepath.Join(*input, "www")
			if err := nativeapps.PrepareInputClient(*html, www); err != nil {
				fail(err)
			}
		}
		if err := json.NewEncoder(os.Stdout).Encode(map[string]any{"launcher": support.Launcher, "args": support.XpraArgs(os.Environ()), "html": www}); err != nil {
			fail(err)
		}
		return
	}
	if *check != "" {
		if err := nativeapps.SelfTest(ctx, *check); err != nil {
			fail(err)
		}
		fmt.Println("Native picture and input passed")
		return
	}
	pkg, err := nativeapps.ForPlatform("linux", *architecture)
	if err != nil {
		fail(err)
	}
	if *bundle != "" {
		file, err := os.Create(*bundle + ".part")
		if err != nil {
			fail(err)
		}
		err = nativeapps.WriteBundle(ctx, pkg, *state, file, func(n int64) {
			_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"state": "downloading", "received_bytes": n, "expected_bytes": pkg.SizeBytes})
		})
		closed := file.Close()
		if err == nil {
			err = closed
		}
		if err == nil {
			err = os.Rename(file.Name(), *bundle)
		}
		if err != nil {
			os.Remove(file.Name())
			fail(err)
		}
		fmt.Println(*bundle)
		return
	}
	manager, err := nativeapps.New(*state, pkg, func(ctx context.Context, root string) error {
		err := nativeapps.SelfTest(ctx, root)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
		}
		return err
	})
	if err != nil {
		fail(err)
	}
	defer manager.Close()
	_, err = manager.Start("qualification", time.Now().Format(time.RFC3339Nano), "download", 0)
	if err != nil {
		fail(err)
	}
	changes, stop := manager.Watch()
	defer stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-changes:
			status := manager.Snapshot("qualification")
			_ = json.NewEncoder(os.Stdout).Encode(status)
			if status.State == "ready" {
				root, _ := manager.Directory()
				fmt.Println(root)
				return
			}
			if !status.Active() {
				fail(fmt.Errorf("preparation failed: %s", status.ErrorCode))
			}
		}
	}
}
func fail(err error) { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
