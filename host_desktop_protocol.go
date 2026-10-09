package nativeapps

import (
	"github.com/floegence/floe-native-apps/hostdesktop"
	"io"
)

// hostdesktop owns the physical-desktop wire and privileged service boundary.
// Root aliases preserve the published SDK API; the standalone service imports
// only hostdesktop and cannot recursively embed distribution artifacts.
const HostDesktopProtocolVersion = hostdesktop.HostDesktopProtocolVersion

var ErrHostDesktopProtocol = hostdesktop.ErrHostDesktopProtocol

type HostDesktopDisplay = hostdesktop.HostDesktopDisplay
type HostDesktopCapabilities = hostdesktop.HostDesktopCapabilities
type HostDesktopPicture = hostdesktop.HostDesktopPicture
type HostDesktopServiceStatus = hostdesktop.HostDesktopServiceStatus
type HostDesktopInput = hostdesktop.HostDesktopInput
type HostDesktopCommand = hostdesktop.HostDesktopCommand
type HostDesktopMessage = hostdesktop.HostDesktopMessage
type HostDesktopCursorPosition = hostdesktop.HostDesktopCursorPosition

func ParseHostDesktopCommand(data []byte) (HostDesktopCommand, error) {
	return hostdesktop.ParseHostDesktopCommand(data)
}
func ReadHostDesktopMessage(reader io.Reader) (HostDesktopMessage, error) {
	return hostdesktop.ReadHostDesktopMessage(reader)
}
func WriteHostDesktopMessage(writer io.Writer, message HostDesktopMessage) error {
	return hostdesktop.WriteHostDesktopMessage(writer, message)
}
func WriteHostDesktopCommand(writer io.Writer, command HostDesktopCommand) error {
	return hostdesktop.WriteHostDesktopCommand(writer, command)
}
