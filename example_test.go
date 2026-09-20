package nativeapps_test

import (
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"

	nativeapps "github.com/floegence/floe-native-apps"
)

// This example compiles in source checks but performs no network operations in
// tests. A service should retain its manager for the service's entire lifetime.
func ExampleNew() {
	prepare := func(ctx context.Context, stateRoot, owner, requestID string) (nativeapps.Tools, error) {
		pkg, err := nativeapps.NativePackage()
		if err != nil {
			return nativeapps.Tools{}, err
		}
		manager, err := nativeapps.New(stateRoot, pkg, nil)
		if err != nil {
			return nativeapps.Tools{}, err
		}
		defer manager.Close()
		changes, stop := manager.Watch()
		defer stop()
		if _, err := manager.Start(owner, requestID, "download", 0); err != nil {
			return nativeapps.Tools{}, err
		}
		for {
			status := manager.Snapshot(owner)
			if status.State == "ready" {
				root, err := manager.Directory()
				if err != nil {
					return nativeapps.Tools{}, err
				}
				return nativeapps.ResolveTools(root)
			}
			if !status.Active() {
				return nativeapps.Tools{}, fmt.Errorf("preparation %s: %s", status.State, status.ErrorCode)
			}
			select {
			case <-ctx.Done():
				return nativeapps.Tools{}, ctx.Err()
			case <-changes:
			}
		}
	}
	cache, err := os.UserCacheDir()
	if err != nil {
		log.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Minute)
	defer cancel()
	// A real product supplies an authenticated owner and a stable request ID.
	tools, err := prepare(ctx, filepath.Join(cache, "floe-native-apps"), "local-user", "prepare-1")
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(tools.Xpra)
}
