package hostdesktop

import "testing"

func TestLoginManagementRejectsAmbiguousCommands(t *testing.T) {
	for _, wire := range []string{
		"{\"version\":2,\"version\":2,\"action\":\"cancel\"}",
		"{\"version\":2,\"action\":\"cancel\",\"password\":\"secret\"}",
		"{\"version\":2} {}",
	} {
		var value LoginServiceDeploymentCommand
		if loginManagementDecode([]byte(wire), &value) == nil {
			t.Fatal("ambiguous management command accepted")
		}
	}
}
