package nativeapps

import _ "embed"

//go:embed desktop_frames.js
var desktopFramesSource []byte

// DesktopFramesClientSource supplies the browser decoder for the released native
// frame contract. It owns ordered damage composition and bounded decode work;
// hosts own presentation, transport, target validity, and paint acknowledgements.
func DesktopFramesClientSource() []byte { return append([]byte(nil), desktopFramesSource...) }
