// floe-host-desktop-service is installed as a root-owned executable only after
// the host product has obtained administrator authorization. It does not
// install itself, elevate a Runtime, or accept network connections.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	nativeapps "github.com/floegence/floe-native-apps/hostdesktop"
)

func main() {
	if len(os.Args) == 4 && os.Args[1] == "media" {
		if nativeapps.RunLoginMediaPython(os.Args[2], os.Args[3]) != nil {
			os.Exit(77)
		}
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "display-power" {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		if nativeapps.RunLoginDisplayPower(ctx) != nil {
			os.Exit(1)
		}
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "manage" {
		os.Exit(manage())
	}
	var config nativeapps.LoginServiceConfig
	flags := flag.NewFlagSet("floe-host-desktop-service", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	flags.StringVar(&config.SocketPath, "socket", nativeapps.LoginServiceSocket, "Administrator-owned private Unix socket.")
	flags.StringVar(&config.WorkerPath, "worker", "", "Administrator-owned DRM worker executable.")
	flags.StringVar(&config.MediaRoot, "media-root", "", "Verified root-owned media installation.")
	flags.StringVar(&config.RuntimeSHA256, "runtime-sha256", "", "Authorized Runtime executable digest.")
	flags.StringVar(&config.Seat, "seat", "seat0", "Physical logind seat.")
	uid := flags.Uint("runtime-uid", 0, "Authorized unprivileged Runtime UID.")
	gid := flags.Uint("runtime-gid", 0, "Runtime primary GID for socket access.")
	if flags.Parse(os.Args[1:]) != nil || flags.NArg() != 0 || *uid == 0 || *uid > 1<<32-1 || *gid > 1<<32-1 {
		os.Exit(64)
	}
	config.RuntimeUID, config.RuntimeGID = uint32(*uid), uint32(*gid)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	if err := nativeapps.RunLoginScreenService(ctx, config); err != nil {
		// Do not print request bytes, OS credentials, environment or process paths.
		fmt.Fprintln(os.Stderr, "LOGIN_SERVICE_START_FAILED")
		os.Exit(1)
	}
}
