package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"os/signal"
	"syscall"

	"github.com/floegence/floe-native-apps/hostdesktop"
)

// Management reads one bounded request followed by a liveness stream. The SSH
// adapter keeps that stream open until completion; closing it cancels and rolls
// back. sudo/root credentials are consumed by SSH's authorization command before
// this process starts, and never belong in this request or a daemon's stdin.
func manage() int {
	signal.Ignore(syscall.SIGPIPE)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP)
	defer cancel()
	encoder := json.NewEncoder(os.Stdout)
	report := func(event hostdesktop.LoginServiceDeploymentEvent) {
		if encoder.Encode(event) != nil {
			cancel()
		}
	}
	reader := bufio.NewReaderSize(os.Stdin, 8192)
	line, err := reader.ReadSlice('\n')
	if err != nil || len(line) > 8192 {
		report(hostdesktop.LoginServiceDeploymentEvent{Stage: "failed", Code: "DEPLOYMENT_REQUEST_INVALID"})
		return 64
	}
	var request hostdesktop.LoginServiceDeploymentRequest
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF {
		report(hostdesktop.LoginServiceDeploymentEvent{Stage: "failed", Code: "DEPLOYMENT_REQUEST_INVALID"})
		return 64
	}
	go func() { _, _ = io.Copy(io.Discard, reader); cancel() }()
	status, err := hostdesktop.ManageLoginScreenService(ctx, request, report)
	code := ""
	if err != nil {
		code = "DEPLOYMENT_FAILED"
	}
	if encoder.Encode(struct {
		Stage  string                    `json:"stage"`
		Status hostdesktop.ServiceStatus `json:"status"`
		Code   string                    `json:"code,omitempty"`
	}{Stage: "result", Status: status, Code: code}) != nil {
		return 1
	}
	if err != nil {
		return 1
	}
	return 0
}
