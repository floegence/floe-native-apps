package nativeapps

import (
	"strings"
	"testing"
)

func TestReleasedCatalog(t *testing.T) {
	for _, arch := range []string{"amd64", "arm64"} {
		p, err := ForPlatform("linux", arch)
		if err != nil {
			t.Fatal(err)
		}
		if p.Architecture != arch || len(p.Artifacts) < 100 {
			t.Fatal("incomplete closure")
		}
		for _, a := range p.Artifacts {
			if a.License == "" || !strings.HasPrefix(a.Source, "https://") {
				t.Fatal("missing component provenance", a.Name)
			}
		}
	}
	if _, err := ForPlatform("darwin", "arm64"); err != ErrUnsupported {
		t.Fatal(err)
	}
	if _, err := ForPlatform("linux", "386"); err != ErrUnsupported {
		t.Fatal(err)
	}
}
func TestEnvironmentRestorationMetadata(t *testing.T) {
	tools := Tools{Root: "/private/graphics"}
	env := tools.Environment([]string{"PATH=/host/bin", "PYTHONPATH=/host/python", "DISPLAY=:42", "DBUS_SESSION_BUS_ADDRESS=private-bus", "XPRA_RESOURCES_DIR=/host/xpra"})
	joined := strings.Join(env, "\n")
	if strings.Contains(joined, "\nPYTHONPATH=") || !strings.Contains(joined, `"PYTHONPATH":"/host/python"`) || !strings.Contains(joined, `"XPRA_RESOURCES_DIR":"/host/xpra"`) || !strings.Contains(joined, "DISPLAY=:42") || !strings.Contains(joined, "DBUS_SESSION_BUS_ADDRESS=private-bus") {
		t.Fatal(env)
	}
}
