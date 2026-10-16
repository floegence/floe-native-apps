package hostdesktop

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"sync"
	"time"
)

func loginManagementDecode(line []byte, value any) error {
	if len(line) > 8192 || !hostDesktopUniqueJSON(line) {
		return ErrHostDesktopProtocol
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(value) != nil || decoder.Decode(new(any)) != io.EOF {
		return ErrHostDesktopProtocol
	}
	return nil
}

// RunLoginServiceManagement owns one bounded transaction. Typed stdin never
// contains credentials. A heartbeat loss cancels even through a PTY.
func RunLoginServiceManagement(parent context.Context, input io.Reader, output io.Writer) int {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	var mu sync.Mutex
	encoder := json.NewEncoder(output)
	report := func(event LoginServiceDeploymentEvent) {
		mu.Lock()
		defer mu.Unlock()
		if encoder.Encode(event) != nil {
			cancel()
		}
	}
	lines := make(chan []byte, 1)
	go func() {
		reader := bufio.NewReaderSize(input, 8193)
		for {
			line, err := reader.ReadSlice('\n')
			if err != nil || len(line) > 8192 {
				cancel()
				return
			}
			select {
			case lines <- append([]byte(nil), line...):
			case <-ctx.Done():
				return
			}
		}
	}()
	var request LoginServiceDeploymentRequest
	select {
	case line := <-lines:
		if loginManagementDecode(line, &request) != nil || request.Version != LoginServiceManagementVersion || request.OperationID == "" {
			report(LoginServiceDeploymentEvent{Stage: "failed", Code: "management_protocol_invalid"})
			return 64
		}
	case <-time.After(15 * time.Second):
		return 64
	case <-ctx.Done():
		return 1
	}
	commands := make(chan LoginServiceDeploymentCommand, 1)
	go func() {
		timer := time.NewTimer(15 * time.Second)
		defer timer.Stop()
		for {
			select {
			case line := <-lines:
				var command LoginServiceDeploymentCommand
				if loginManagementDecode(line, &command) != nil || command.Version != LoginServiceManagementVersion || command.OperationID != request.OperationID {
					cancel()
					return
				}
				if !timer.Stop() {
					select {
					case <-timer.C:
					default:
					}
				}
				timer.Reset(15 * time.Second)
				switch command.Action {
				case "heartbeat":
				case "cancel":
					cancel()
					return
				case "confirm", "commit":
					select {
					case commands <- command:
					default:
						cancel()
						return
					}
				default:
					cancel()
					return
				}
			case <-timer.C:
				cancel()
				return
			case <-ctx.Done():
				return
			}
		}
	}()
	control := func(wait context.Context, event LoginServiceDeploymentEvent) error {
		report(event)
		select {
		case command := <-commands:
			action := "confirm"
			if event.Stage == "verification_required" {
				action = "commit"
			}
			if command.Action != action || command.ChallengeID != event.ChallengeID || command.PolicyDigest != event.PolicyDigest {
				return ErrHostDesktopProtocol
			}
			return nil
		case <-wait.Done():
			return wait.Err()
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	status, err := ManageLoginScreenServiceControlled(ctx, request, report, control)
	code := ""
	if err != nil {
		code = status.Reason
		if code == "" {
			code = "deployment_failed"
		}
	}
	mu.Lock()
	writeErr := encoder.Encode(struct {
		Stage       string        `json:"stage"`
		OperationID string        `json:"operation_id"`
		Status      ServiceStatus `json:"status"`
		Code        string        `json:"code,omitempty"`
	}{"result", request.OperationID, status, code})
	mu.Unlock()
	if err != nil || writeErr != nil {
		return 1
	}
	return 0
}
