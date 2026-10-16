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

func TestLoginManagementBindsEveryChallengeField(t *testing.T) {
	event := LoginServiceDeploymentEvent{OperationID: "operation", ChallengeID: "challenge", PolicyDigest: "digest"}
	command := LoginServiceDeploymentCommand{Version: LoginServiceManagementVersion, OperationID: event.OperationID, ChallengeID: event.ChallengeID, PolicyDigest: event.PolicyDigest, Action: "commit"}
	if !loginManagementMatches(command, event, "commit") {
		t.Fatal("current proof rejected")
	}
	for _, mutate := range []func(*LoginServiceDeploymentCommand){
		func(c *LoginServiceDeploymentCommand) { c.Version-- },
		func(c *LoginServiceDeploymentCommand) { c.OperationID = "previous-operation" },
		func(c *LoginServiceDeploymentCommand) { c.ChallengeID = "expired-challenge" },
		func(c *LoginServiceDeploymentCommand) { c.PolicyDigest = "other-policy" },
		func(c *LoginServiceDeploymentCommand) { c.Action = "confirm" },
	} {
		stale := command
		mutate(&stale)
		if loginManagementMatches(stale, event, "commit") {
			t.Fatal("stale command accepted", stale)
		}
	}
}
