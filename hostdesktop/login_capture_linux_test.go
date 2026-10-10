//go:build linux

package hostdesktop

import (
	"bytes"
	"context"
	"image/png"
	"os"
	"testing"
	"time"
)

func TestLoginVirtualTerminalRequiresKernelAndLogindAgreement(t *testing.T) {
	for _, test := range []struct {
		vt, active, kernel string
		ready              bool
	}{
		{"1", "yes", "tty1\n", true}, {"2", "yes", "tty2", true},
		{"1", "yes", "tty2", false}, {"1", "no", "tty1", false},
		{"0", "yes", "tty0", false}, {"", "yes", "tty1", false},
		{"1x", "yes", "tty1", false}, {"1", "yes", "tty1x", false},
	} {
		if got := loginVirtualTerminalReady(test.vt, test.active, test.kernel); got != test.ready {
			t.Fatalf("VT=%q active=%q kernel=%q: ready=%v", test.vt, test.active, test.kernel, got)
		}
	}
}

// Qualification exercises the deployed, nonprivileged codec path rather than
// maintaining a second root pixel conversion and PNG implementation for tests.
func TestLoginDRMRealCapture(t *testing.T) {
	worker := os.Getenv("FLOE_LOGIN_CAPTURE_WORKER")
	if worker == "" {
		t.Skip("real DRM capture qualification not requested")
	}
	if os.Geteuid() != 0 {
		t.Fatal("qualification requires administrator authorization")
	}
	ctx, cancel := context.WithTimeout(t.Context(), 15*time.Second)
	defer cancel()
	media, err := openLoginMedia(ctx, LoginServiceConfig{WorkerPath: worker, MediaRoot: os.Getenv("FLOE_LOGIN_MEDIA_ROOT"), RuntimeUID: 1000, RuntimeGID: 1000})
	if err != nil {
		t.Fatal(err)
	}
	defer media.close()
	if !media.send(map[string]any{"method": "configure", "generation": 1, "picture": HostDesktopPicture{Mode: "clarity", MaxDimension: 1920, FrameRate: 30}, "reset": true}) {
		t.Fatal("media configuration rejected")
	}
	video := false
	for {
		select {
		case <-ctx.Done():
			t.Fatal("lossless refinement timed out")
		case <-media.done:
			t.Fatal("media worker stopped")
		case frame := <-media.messages:
			if frame.Type == "error" {
				t.Fatal(frame.Code)
			}
			if frame.Type != "frame" {
				continue
			}
			if frame.Codec == "h264" {
				video = true
			}
			if !media.send(map[string]any{"method": "ack", "generation": frame.Generation, "frame_id": frame.FrameID}) {
				t.Fatal("frame credit rejected")
			}
			if frame.Codec != "png" {
				continue
			}
			image, err := png.Decode(bytes.NewReader(frame.Data))
			if err != nil {
				t.Fatal(err)
			}
			bounds, nonblack := image.Bounds(), 0
			for y := 0; y < bounds.Dy(); y++ {
				for x := 0; x < bounds.Dx(); x++ {
					r, g, b, _ := image.At(x, y).RGBA()
					if r|g|b != 0 {
						nonblack++
					}
				}
			}
			if !video || nonblack == 0 {
				t.Fatal("capture did not produce video and nonblack refinement")
			}
			if os.Getenv("FLOE_LOGIN_CAPTURE_PATTERN") == "rgb-bars" {
				if bounds.Dx() != 1920 || bounds.Dy() != 1080 {
					t.Fatal("known-color target requires the original 1920x1080 scanout")
				}
				colors := [8][3]uint32{{65535, 0, 0}, {0, 65535, 0}, {0, 0, 65535}, {65535, 65535, 65535}, {}, {0, 65535, 65535}, {65535, 65535, 0}, {65535, 0, 65535}}
				for _, y := range []int{200, 720, 880} {
					for x := 0; x < 1920; x++ {
						r, g, b, _ := image.At(x, y).RGBA()
						if [3]uint32{r, g, b} != colors[x/240] {
							t.Fatalf("color/edge mismatch at (%d,%d): RGB=(%d,%d,%d)", x, y, r, g, b)
						}
					}
				}
				for y := 888; y < 1080; y++ {
					for x := 0; x < 1024; x++ {
						level := uint32(((x/16 + (y-888)/16) % 2) * 65535)
						r, g, b, _ := image.At(x, y).RGBA()
						if r != level || g != level || b != level {
							t.Fatalf("checker layout mismatch at (%d,%d)", x, y)
						}
					}
				}
				textPixels := 0
				for y := 500; y < 540; y++ {
					for x := 972; x < 1200; x++ {
						r, g, b, _ := image.At(x, y).RGBA()
						if r|g|b != 0 {
							textPixels++
						}
					}
				}
				if textPixels < 100 {
					t.Fatal("known-color target text is missing")
				}
				t.Log("known-color bars, one-pixel boundaries, checker orientation and text passed")
			}
			t.Logf("captured %dx%d scanout; %d nonblack pixels; shared H264 and lossless refinement", bounds.Dx(), bounds.Dy(), nonblack)
			if output := os.Getenv("FLOE_LOGIN_CAPTURE_OUTPUT"); output != "" {
				if err = os.WriteFile(output, frame.Data, 0600); err != nil {
					t.Fatal(err)
				}
			}
			return
		}
	}
}
