package nativeapps

// ProcessIdentity is an observation of one Linux process generation. Persisting
// it allows a host to distinguish a surviving native backend from a reused PID
// after restart. It grants no authorization and must never be used alone to
// select an application or issue a signal.
type ProcessIdentity struct {
	PID     int    `json:"pid"`
	Boot    string `json:"boot"`
	Started string `json:"started"`
}

func (p ProcessIdentity) Alive() bool {
	current, err := ObserveProcess(p.PID)
	return err == nil && p == current
}
