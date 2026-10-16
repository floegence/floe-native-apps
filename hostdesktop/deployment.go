package hostdesktop

import "context"

const LoginServiceManagementVersion = 2

// LoginServicePolicy is the persistent administrator-owned allowlist.
type LoginServicePolicy struct {
	RuntimeUID    uint32 `json:"runtime_uid,omitempty"`
	RuntimeGID    uint32 `json:"runtime_gid,omitempty"`
	RuntimeSHA256 string `json:"runtime_sha256,omitempty"`
	ServiceSHA256 string `json:"service_sha256,omitempty"`
	WorkerSHA256  string `json:"worker_sha256,omitempty"`
	MediaSHA256   string `json:"media_sha256,omitempty"`
}

// LoginServiceDeploymentRequest is resolved by the trusted SSH adapter after
// explicit authorization. The manager is already privileged and never elevates.
type LoginServiceDeploymentRequest struct {
	Operation       string `json:"operation"`
	SourceDirectory string `json:"source_directory,omitempty"`
	RuntimeUID      uint32 `json:"runtime_uid,omitempty"`
	RuntimeGID      uint32 `json:"runtime_gid,omitempty"`
	RuntimeSHA256   string `json:"runtime_sha256,omitempty"`
	ServiceSHA256   string `json:"service_sha256,omitempty"`
	WorkerSHA256    string `json:"worker_sha256,omitempty"`
	MediaSHA256     string `json:"media_sha256,omitempty"`
	Version         int    `json:"version,omitempty"`
	OperationID     string `json:"operation_id,omitempty"`
	RuntimePID      uint32 `json:"runtime_pid,omitempty"`
	RuntimeStart    string `json:"runtime_start,omitempty"`
}

func (r LoginServiceDeploymentRequest) policy() LoginServicePolicy {
	return LoginServicePolicy{r.RuntimeUID, r.RuntimeGID, r.RuntimeSHA256, r.ServiceSHA256, r.WorkerSHA256, r.MediaSHA256}
}

type LoginServiceDeploymentEvent struct {
	Stage             string `json:"stage"`
	Code              string `json:"code,omitempty"`
	Rollback          string `json:"rollback,omitempty"`
	OperationID       string `json:"operation_id,omitempty"`
	ChallengeID       string `json:"challenge_id,omitempty"`
	ServiceGeneration string `json:"service_generation,omitempty"`
	PolicyDigest      string `json:"policy_digest,omitempty"`
	ActiveConnections *int   `json:"active_connections,omitempty"`
}
type LoginServiceDeploymentCommand struct {
	Version      int    `json:"version"`
	OperationID  string `json:"operation_id"`
	Action       string `json:"action"`
	ChallengeID  string `json:"challenge_id,omitempty"`
	PolicyDigest string `json:"policy_digest,omitempty"`
}

// Control waits for confirmation or authenticated Runtime proof. An error
// cancels the single manager-owned transaction; credentials never belong here.
type LoginServiceDeploymentControl func(context.Context, LoginServiceDeploymentEvent) error
