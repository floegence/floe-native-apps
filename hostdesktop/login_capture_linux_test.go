//go:build linux

package hostdesktop

import (
	"context"
	"image/png"
	"os"
	"testing"
	"time"
)

func TestLoginDRMRealCapture(t *testing.T) {
	worker := os.Getenv("FLOE_LOGIN_CAPTURE_WORKER")
	if worker == "" {
		t.Skip("real DRM capture qualification not requested")
	}
	if os.Geteuid() != 0 {
		t.Fatal("qualification requires administrator authorization")
	}
	// Wake the display through a task-owned input device, without Portal or any
	// local graphical consent. Shift injects no character or credential.
	input, err := openLoginUInput()
	if err != nil {
		t.Fatal(err)
	}
	defer input.close()
	time.Sleep(1500 * time.Millisecond)
	if err = input.input(&HostDesktopInput{Kind: "key", Code: "ShiftLeft", Pressed: true}); err != nil {
		t.Fatal(err)
	}
	time.Sleep(100 * time.Millisecond)
	if err = input.release(); err != nil {
		t.Fatal(err)
	}
	time.Sleep(1500 * time.Millisecond)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	capture, err := openLoginDRMCapture(ctx, worker, 1000, 1000, loginRenderGroups(1000))
	if err != nil {
		t.Fatal(err)
	}
	defer capture.close()
	frame, err := capture.frame()
	if err != nil {
		t.Fatal(err)
	}
	size := frame.Bounds()
	nonblack := 0
	for y := size.Min.Y; y < size.Max.Y; y++ {
		for x := size.Min.X; x < size.Max.X; x++ {
			r, g, b, _ := frame.At(x, y).RGBA()
			if r|g|b != 0 {
				nonblack++
			}
		}
	}
	if nonblack == 0 {
		t.Fatal("captured frame is entirely black")
	}
	t.Logf("captured %dx%d scanout; %d nonblack pixels", size.Dx(), size.Dy(), nonblack)
	if path := os.Getenv("FLOE_LOGIN_CAPTURE_OUTPUT"); path != "" {
		file, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
		if err != nil {
			t.Fatal(err)
		}
		err = png.Encode(file, frame)
		closeErr := file.Close()
		if err != nil {
			t.Fatal(err)
		}
		if closeErr != nil {
			t.Fatal(closeErr)
		}
	}
}
