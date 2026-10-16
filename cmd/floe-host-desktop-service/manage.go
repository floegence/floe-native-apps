package main

import (
	"context"
	"github.com/floegence/floe-native-apps/hostdesktop"
	"os"
	"os/signal"
	"syscall"
)

func manage() int {
	signal.Ignore(syscall.SIGPIPE)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP)
	defer cancel()
	return hostdesktop.RunLoginServiceManagement(ctx, os.Stdin, os.Stdout)
}
