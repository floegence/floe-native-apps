package hostdesktop

// LoginServiceDeploymentRequest is resolved by the trusted SSH host adapter,
// after its UI has confirmed scope. Credentials are intentionally absent. The
// management process already has administrator authority; it never invokes sudo.
type LoginServiceDeploymentRequest struct {
	Operation       string `json:"operation"`
	SourceDirectory string `json:"source_directory,omitempty"`
	RuntimeUID      uint32 `json:"runtime_uid,omitempty"`
	RuntimeGID      uint32 `json:"runtime_gid,omitempty"`
	RuntimeSHA256   string `json:"runtime_sha256,omitempty"`
	ServiceSHA256   string `json:"service_sha256,omitempty"`
	WorkerSHA256    string `json:"worker_sha256,omitempty"`
}
type LoginServiceDeploymentEvent struct {
	Stage    string `json:"stage"`
	Code     string `json:"code,omitempty"`
	Rollback string `json:"rollback,omitempty"`
}
