package hostdesktop

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

type loginMaintenanceStatus struct {
	ActiveConnections int
	Generation        string
}

func loginConfigDigest(config LoginServiceConfig) string {
	if config.PolicyDigest != "" {
		return config.PolicyDigest
	}
	data, _ := json.Marshal(struct {
		UID, GID uint32
		SHA256   string
	}{config.RuntimeUID, config.RuntimeGID, config.RuntimeSHA256})
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// VerifyLoginServiceAdmission authenticates from the calling Runtime process.
// It does not open an attachment, inspect a display or request Portal consent.
func VerifyLoginServiceAdmission(ctx context.Context, path string) (string, error) {
	conn, reader, err := loginDial(ctx, path)
	if err != nil {
		return "", err
	}
	defer conn.Close()
	stop := context.AfterFunc(ctx, func() { _ = conn.Close() })
	defer stop()
	if err = loginWritePacket(conn, loginServiceHello{Operation: "status"}); err != nil {
		return "", err
	}
	var reply loginServiceReply
	if err = loginReadPacket(reader, &reply); err != nil {
		return "", err
	}
	if reply.Version != loginAttachmentVersion {
		return "", ErrServiceUpdateRequired
	}
	if reply.Status == nil || reply.Status.State != ServiceActive || len(reply.PolicyDigest) != 64 {
		return "", errLoginServiceUnavailable
	}
	return reply.PolicyDigest, nil
}

func loginPolicyDigest(policy LoginServicePolicy) string {
	data, _ := json.Marshal(policy)
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}
