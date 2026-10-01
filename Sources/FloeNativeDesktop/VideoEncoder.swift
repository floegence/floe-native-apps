import Foundation
import VideoToolbox

// Probe an actual frame at the requested size. Session creation and preparation
// alone can succeed even when the hardware rejects that resolution at encode.
struct NativeVideoEncoder {
    let session: VTCompressionSession
    let hardware: Bool
    var name: String { hardware ? "videotoolbox-hardware" : "videotoolbox-software" }

    static func prepare(width: Int, height: Int, frameRate: Int, bitrate: Int) -> NativeVideoEncoder? {
        guard width >= 2, height >= 2, width <= 8192, height <= 8192,
              width * height <= 16 * 1024 * 1024 else { return nil }
        var pixelBuffer: CVPixelBuffer?
        guard CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA,
                                  [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &pixelBuffer) == noErr,
              let pixelBuffer else { return nil }
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        if let base = CVPixelBufferGetBaseAddress(pixelBuffer) {
            memset(base, 0, CVPixelBufferGetBytesPerRow(pixelBuffer) * height)
        }
        CVPixelBufferUnlockBaseAddress(pixelBuffer, [])
        for hardware in [true, false] {
            var specification: [CFString: Any] = hardware
                ? [kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: true,
                   kVTVideoEncoderSpecification_EnableLowLatencyRateControl: true]
                : [kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder: false]
            specification[kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder] = hardware
            var candidate: VTCompressionSession?
            guard VTCompressionSessionCreate(allocator: kCFAllocatorDefault, width: Int32(width), height: Int32(height),
                codecType: kCMVideoCodecType_H264, encoderSpecification: specification as CFDictionary,
                imageBufferAttributes: nil, compressedDataAllocator: nil, outputCallback: nil, refcon: nil,
                compressionSessionOut: &candidate) == noErr, let candidate else { continue }
            let properties: [CFString: Any] = [
                kVTCompressionPropertyKey_RealTime: true,
                kVTCompressionPropertyKey_AllowFrameReordering: false,
                kVTCompressionPropertyKey_MaxFrameDelayCount: 0,
                kVTCompressionPropertyKey_ProfileLevel: kVTProfileLevel_H264_Main_AutoLevel,
                kVTCompressionPropertyKey_ExpectedFrameRate: frameRate,
                kVTCompressionPropertyKey_MaxKeyFrameInterval: frameRate * 2,
                kVTCompressionPropertyKey_AverageBitRate: bitrate,
            ]
            guard VTSessionSetProperties(candidate, propertyDictionary: properties as CFDictionary) == noErr,
                  VTCompressionSessionPrepareToEncodeFrames(candidate) == noErr else {
                VTCompressionSessionInvalidate(candidate); continue
            }
            let probe = Probe()
            let submitted = VTCompressionSessionEncodeFrame(candidate, imageBuffer: pixelBuffer,
                presentationTimeStamp: .zero, duration: .invalid, frameProperties: nil, infoFlagsOut: nil) { status, _, sample in
                probe.finish(status == noErr && sample?.dataBuffer != nil)
            }
            // The software implementation retains even its first sample until
            // completion is requested. It must never wait for a later desktop change.
            let flushed = hardware || VTCompressionSessionCompleteFrames(candidate, untilPresentationTimeStamp: .invalid) == noErr
            if submitted == noErr, flushed, probe.wait() {
                return NativeVideoEncoder(session: candidate, hardware: hardware)
            }
            VTCompressionSessionInvalidate(candidate)
        }
        return nil
    }

    private final class Probe {
        private let signal = DispatchSemaphore(value: 0)
        private let lock = NSLock()
        private var valid = false
        func finish(_ success: Bool) {
            lock.lock(); valid = success; lock.unlock()
            signal.signal()
        }
        func wait() -> Bool {
            guard signal.wait(timeout: .now() + 2) == .success else { return false }
            lock.lock(); defer { lock.unlock() }
            return valid
        }
    }
}
