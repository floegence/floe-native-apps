import AppKit
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers
import VideoToolbox

public struct NativeCaptureSettings: Equatable {
    public let mode: String
    public let pixelRatio: Double
    public let maxDimension: Int
    public let frameRate: Int
    public let video: Bool
    public let requireVideo: Bool
    public let nativePixels: Bool
    public let audio: Bool
    public let frameCapacity: Int
    var imageQuality: Double { mode == "data" ? 0.72 : mode == "clarity" ? 0.95 : 0.88 }
    public init(request: [String: Any] = [:]) throws {
        guard request["mode"] == nil || request["mode"] is String,
              ["video", "audio", "require_video", "native_pixels"].allSatisfy({ name in request[name] == nil || (request[name] as? NSNumber).map({ CFGetTypeID($0) == CFBooleanGetTypeID() }) == true }) else {
            throw NativeDesktopInput.invalid("Invalid picture setting.")
        }
        mode = request["mode"] as? String ?? "auto"
        guard ["auto", "clarity", "smooth", "data"].contains(mode) else { throw NativeDesktopInput.invalid("Unknown picture mode.") }
        func number(_ name: String, fallback: Double, allowed: ClosedRange<Double>) throws -> Double {
            guard let raw = request[name] else { return fallback }
            guard let value = raw as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite,
                  allowed.contains(value.doubleValue) else { throw NativeDesktopInput.invalid("Invalid picture setting.") }
            return value.doubleValue
        }
        pixelRatio = try number("pixel_ratio", fallback: 2, allowed: 0.5...4)
        let dimension = try number("max_dimension", fallback: 0, allowed: 0...4096)
        let fps = try number("frame_rate", fallback: 0, allowed: 0...60)
        guard [0, 1600, 1920, 2560, 3840, 4096].contains(dimension), [0, 15, 24, 30, 60].contains(fps) else { throw NativeDesktopInput.invalid("Unsupported picture limit.") }
        maxDimension = Int(dimension == 0 ? (mode == "data" ? 1600 : mode == "smooth" ? 2560 : 4096) : dimension)
        frameRate = Int(fps == 0 ? (mode == "smooth" ? 60 : mode == "data" ? 15 : 30) : fps)
        video = request["video"] as? Bool ?? false
        requireVideo = request["require_video"] as? Bool ?? false
        nativePixels = request["native_pixels"] as? Bool ?? false
        audio = request["audio"] as? Bool ?? false
        let capacity = try number("frame_capacity", fallback: 1, allowed: 1...4)
        guard capacity.rounded() == capacity, !requireVideo || video else { throw NativeDesktopInput.invalid("Invalid capture pipeline setting.") }
        frameCapacity = Int(capacity)
    }
    public func dimensions(points: CGSize, sourceScale: Double) -> CGSize {
        if nativePixels { return CGSize(width: Int(points.width * sourceScale), height: Int(points.height * sourceScale)) }
        let scale = min(pixelRatio, sourceScale, Double(maxDimension) / max(points.width, points.height))
        // Even dimensions are required by the H.264 4:2:0 encoder.
        return CGSize(width: max(2, Int(points.width * scale) / 2 * 2), height: max(2, Int(points.height * scale) / 2 * 2))
    }
}

// ScreenCaptureKit can deliver complete samples even when the pixels have not
// changed. Compare active BGRA rows (excluding padding) before encoding them.
public func nativeCapturePixelsEqual(_ lhs: CVPixelBuffer, _ rhs: CVPixelBuffer) -> Bool {
    guard CVPixelBufferGetWidth(lhs) == CVPixelBufferGetWidth(rhs),
          CVPixelBufferGetHeight(lhs) == CVPixelBufferGetHeight(rhs),
          CVPixelBufferGetPixelFormatType(lhs) == kCVPixelFormatType_32BGRA,
          CVPixelBufferGetPixelFormatType(rhs) == kCVPixelFormatType_32BGRA else { return false }
    CVPixelBufferLockBaseAddress(lhs, .readOnly)
    CVPixelBufferLockBaseAddress(rhs, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(rhs, .readOnly); CVPixelBufferUnlockBaseAddress(lhs, .readOnly) }
    guard let a = CVPixelBufferGetBaseAddress(lhs), let b = CVPixelBufferGetBaseAddress(rhs) else { return false }
    let bytes = CVPixelBufferGetWidth(lhs) * 4
    for row in 0..<CVPixelBufferGetHeight(lhs) {
        if memcmp(a.advanced(by: row * CVPixelBufferGetBytesPerRow(lhs)), b.advanced(by: row * CVPixelBufferGetBytesPerRow(rhs)), bytes) != 0 { return false }
    }
    return true
}

// One pending screen sample crosses from acquisition to encoder state. A slow
// consumer replaces only unencoded pixels; encoded reference frames never enter
// this mailbox. Closing it releases pending storage and rejects late callbacks.
final class NativeCaptureMailbox {
    private let lock = NSLock()
    private var pending: (CVPixelBuffer, TimeInterval)?
    private var scheduled = false
    private var closed = false

    var accepting: Bool {
        lock.lock(); defer { lock.unlock() }
        return !closed
    }
    func replace(_ buffer: CVPixelBuffer, at timestamp: TimeInterval) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !closed else { return false }
        pending = (buffer, timestamp)
        guard !scheduled else { return false }
        scheduled = true
        return true
    }
    func take() -> (CVPixelBuffer, TimeInterval)? {
        lock.lock(); defer { lock.unlock() }
        let value = pending
        pending = nil; scheduled = false
        return value
    }
    func close() {
        lock.lock(); defer { lock.unlock() }
        closed = true; pending = nil
    }
}

// All encoder state is confined to the capture queue. Bounded frame credit
// permits pipelining without dropping dependent H.264 frames. New samples replace
// only the pending unencoded buffer. Existing application clients can retain one credit.
public final class NativeCaptureStream: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private var captureFilter: SCContentFilter?
    private var captureConfiguration: SCStreamConfiguration?
    // Main-thread lifecycle: ScreenCaptureKit stop must wait for start to finish.
    private var starting = false
    private var retiring = false
    private var stopping = false
    private var stopCallbacks: [() -> Void] = []
    private let queue = DispatchQueue(label: "floe.native.capture", qos: .userInteractive)
    // Locking ScreenCaptureKit's pixel buffers can wait on the producer GPU.
    // Keep those waits off the queue that forwards completed encoded frames.
    private let screenQueue = DispatchQueue(label: "floe.native.screen", qos: .userInteractive)
    private let screenMailbox = NativeCaptureMailbox()
    private var comparedScreen: CVPixelBuffer? // Confined to screenQueue.
    private var stopped = false
    private var captureReady = false
    private var encoder: VTCompressionSession?
    private var encoderHardware = true
    private var encoderName = ""
    private let softwareQueue = DispatchQueue(label: "floe.native.software-video", qos: .userInitiated)
    private var latest: CVPixelBuffer?
    private var sequence = 0
    private var sentSequence = 0
    private var inFlight: [Int: TimeInterval] = [:]
    private var nextEncoded: TimeInterval = 0
    private var changedAt: TimeInterval = 0
    private var refinedSequence = 0
    private var timer: DispatchSourceTimer?
    private var pendingProduce: DispatchWorkItem?
    private var dimensions = CGSize.zero
    private var bitrate = 0
    private var adaptiveFactor = 1.0
    private var lastAdapted: TimeInterval = 0
    private var frameID = 0
    private var videoActive = false
    private var forceKeyFrame = true
    private var refinementPending = false
    private let refinementQueue = DispatchQueue(label: "floe.native.refinement", qos: .userInitiated)
    private var audioEncoder: NativeOpusEncoder?
    public private(set) var generation: Int
    public let settings: NativeCaptureSettings
    private let emitFrame: ([String: Any]) -> Void
    let failed: (Error) -> Void
    public init(generation: Int, settings: NativeCaptureSettings, output: @escaping ([String: Any]) -> Void, failed: @escaping (Error) -> Void) {
        self.generation = generation; self.settings = settings; self.emitFrame = output; self.failed = failed
    }
    private func failure(_ error: Error) {
        guard !stopped else { return }
        DispatchQueue.main.async { if !self.retiring { self.failed(error) } }
    }
    public func start(_ window: SCWindow, completion: @escaping () -> Void) {
        starting = true
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let sourceScale: Double
        if #available(macOS 14.0, *) { sourceScale = Double(filter.pointPixelScale) }
        else {
            // ScreenCaptureKit reports points on macOS 13. Convert AppKit's
            // bottom-left screen coordinates to WindowServer's top-left space.
            let top = NSScreen.screens.first?.frame.maxY ?? 0
            sourceScale = Double(NSScreen.screens.max { a, b in
                func area(_ screen: NSScreen) -> Double {
                    let rect = CGRect(x: screen.frame.minX, y: top - screen.frame.maxY, width: screen.frame.width, height: screen.frame.height).intersection(window.frame)
                    return rect.isNull ? 0 : rect.width * rect.height
                }
                return area(a) < area(b)
            }?.backingScaleFactor ?? 1)
        }
        dimensions = settings.dimensions(points: window.frame.size, sourceScale: max(1, sourceScale))
        start(filter: filter, cursor: false, completion: completion)
    }
    public func start(_ display: SCDisplay, completion: @escaping () -> Void) {
        starting = true
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let scale: Double
        if #available(macOS 14.0, *) { scale = Double(filter.pointPixelScale) }
        else { scale = Double(CGDisplayCopyDisplayMode(display.displayID)?.pixelWidth ?? CGDisplayPixelsWide(display.displayID)) / max(1, display.frame.width) }
        dimensions = settings.dimensions(points: display.frame.size, sourceScale: max(1, scale))
        start(filter: filter, cursor: true, completion: completion)
    }
    private func start(filter: SCContentFilter, cursor: Bool, completion: @escaping () -> Void) {
        if dimensions.width < 2 || dimensions.height < 2 || dimensions.width > 8192 || dimensions.height > 8192 ||
            dimensions.width * dimensions.height > 16 * 1024 * 1024 || Int(dimensions.width) % 2 != 0 || Int(dimensions.height) % 2 != 0 {
            starting = false
            failed(NativeDesktopError(code: "NATIVE_RESOLUTION_UNSUPPORTED", message: "The exact source resolution is unsupported."))
            completion()
            return
        }
        if settings.audio && !NativeOpusEncoder.isSupported {
            starting = false
            failed(NativeDesktopError(code: "AUDIO_ENCODER_UNAVAILABLE", message: "The system Opus encoder is unavailable."))
            completion()
            return
        }
        let configuration = SCStreamConfiguration()
        configuration.width = Int(dimensions.width); configuration.height = Int(dimensions.height)
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: Int32(settings.frameRate))
        configuration.queueDepth = 3
        configuration.showsCursor = cursor
        configuration.pixelFormat = kCVPixelFormatType_32BGRA
        configuration.colorSpaceName = CGColorSpace.sRGB
        configuration.capturesAudio = settings.audio
        configuration.sampleRate = 48000
        configuration.channelCount = 2
        configuration.excludesCurrentProcessAudio = true
        if #available(macOS 14.2, *) { configuration.includeChildWindows = true }
        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        self.stream = stream
        captureFilter = filter; captureConfiguration = configuration
        queue.async {
            self.prepareEncoder()
            if self.settings.requireVideo && !self.videoActive {
                self.failure(NativeDesktopError(code: "VIDEO_ENCODER_UNAVAILABLE", message: "The required H.264 encoder is unavailable."))
                return
            }
            let timer = DispatchSource.makeTimerSource(queue: self.queue)
            timer.schedule(deadline: .now(), repeating: 1.0 / Double(self.settings.frameRate))
            timer.setEventHandler { [weak self] in self?.produce() }
            self.timer = timer; timer.resume()
        }
        do {
            try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: screenQueue)
            if settings.audio {
                audioEncoder = NativeOpusEncoder { [weak self] packet, timestamp in
                    guard let self, !self.stopped else { return }
                    self.emitFrame(["type": "audio", "generation": self.generation, "codec": "opus",
                        "sample_rate": 48000, "channels": 2, "timestamp": timestamp, "data": packet.base64EncodedString()])
                }
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
            }
            stream.startCapture { error in
                DispatchQueue.main.async {
                    self.starting = false
                    if let error, !self.retiring { self.failed(error) }
                    self.finishStop()
                    completion()
                    // The consumer publishes its active generation in completion.
                    // A first frame must not race ahead of that state: its receipt
                    // would be rejected, leaving a static desktop without input.
                    if error == nil, !self.retiring {
                        self.queue.async { self.captureReady = true; self.produce() }
                    }
                }
            }
        } catch {
            starting = false
            if !retiring { failed(error) }
            finishStop()
            completion()
        }
    }
    private func prepareEncoder() {
        guard settings.video else { return }
        let pixels = dimensions.width * dimensions.height
        bitrate = Int(min(32_000_000, max(1_000_000, pixels * Double(settings.frameRate) * (settings.mode == "data" ? 0.06 : 0.12))))
        guard let prepared = NativeVideoEncoder.prepare(width: Int(dimensions.width), height: Int(dimensions.height),
            frameRate: settings.frameRate, bitrate: bitrate) else { return }
        encoder = prepared.session; encoderHardware = prepared.hardware; encoderName = prepared.name; videoActive = true
    }
    public func stop(completion: @escaping () -> Void = {}) {
        retiring = true
        stopCallbacks.append(completion)
        screenMailbox.close()
        screenQueue.async { self.comparedScreen = nil }
        queue.async {
            self.stopped = true; self.captureReady = false; self.timer?.cancel(); self.timer = nil
            self.pendingProduce?.cancel(); self.pendingProduce = nil
            self.retireEncoder(); self.latest = nil
        }
        finishStop()
    }
    private func finishStop() {
        guard retiring, !starting, !stopping else { return }
        guard let stream else {
            let callbacks = stopCallbacks; stopCallbacks.removeAll()
            for callback in callbacks { callback() }
            return
        }
        stopping = true
        stream.stopCapture { _ in
            DispatchQueue.main.async {
                self.stream = nil; self.stopping = false
                self.finishStop()
            }
        }
    }
    public func acknowledge(_ id: Int) {
        queue.async {
            guard let sentAt = self.inFlight[id], sentAt >= 0, !self.stopped else { return }
            let now = ProcessInfo.processInfo.systemUptime
            let elapsed = now - sentAt
            self.inFlight = self.inFlight.filter { $0.key > id || $0.value < 0 }
            // Auto adjusts compression to sustained delivery pressure, retaining
            // Retina resolution and restoring lossless text after motion stops.
            if self.settings.mode == "auto", let encoder = self.encoder, now - self.lastAdapted > 2 {
                let factor = elapsed > 0.12 ? max(0.3, self.adaptiveFactor * 0.8) : elapsed < 0.05 ? min(1, self.adaptiveFactor + 0.1) : self.adaptiveFactor
                self.adaptiveFactor = factor; self.lastAdapted = now
                VTSessionSetProperty(encoder, key: kVTCompressionPropertyKey_AverageBitRate, value: NSNumber(value: Int(Double(self.bitrate) * factor)))
            }
            self.produce()
        }
    }
    public func requestKeyFrame() { queue.async { self.forceKeyFrame = true } }
    public func recover(generation: Int) {
        queue.async {
            guard !self.stopped, generation > self.generation else { return }
            self.generation = generation
            self.audioEncoder?.reset()
            // Retire transport credit at an explicit keyframe boundary while
            // keeping the authorized capture and latest unencoded pixels alive.
            // Frame IDs stay monotonic so an old codec callback cannot claim a
            // newly reserved credit even if it completes after this reset.
            self.inFlight.removeAll()
            self.forceKeyFrame = true
            self.sentSequence = 0; self.refinedSequence = 0; self.nextEncoded = 0
            self.pendingProduce?.cancel(); self.pendingProduce = nil
            self.produce()
        }
    }
    public func stream(_ stream: SCStream, didStopWithError error: Error) { queue.async { self.failure(error) } }
    public func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        if type == .audio {
            if !stopped, captureReady { audioEncoder?.append(sample) }
            return
        }
        guard type == .screen, screenMailbox.accepting, sample.isValid,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let status = attachments.first?[.status] as? Int, status == SCFrameStatus.complete.rawValue,
              let buffer = sample.imageBuffer else { return }
        if let comparedScreen, nativeCapturePixelsEqual(comparedScreen, buffer) { return }
        comparedScreen = buffer
        if screenMailbox.replace(buffer, at: ProcessInfo.processInfo.systemUptime) {
            queue.async {
                guard let (buffer, timestamp) = self.screenMailbox.take(), !self.stopped else { return }
                self.latest = buffer; self.sequence += 1; self.changedAt = timestamp
                self.produce()
            }
        }
    }
    private func produce() {
        guard !stopped, captureReady, !settings.requireVideo || videoActive,
              inFlight.count < (encoderHardware ? settings.frameCapacity : 1), let buffer = latest else { return }
        let now = ProcessInfo.processInfo.systemUptime
        let refine = !forceKeyFrame && now - changedAt >= 0.2 && refinedSequence != sequence
        if refine {
            refineImage(buffer)
            return
        }
        guard sequence != sentSequence || forceKeyFrame else { return }
        if now + 0.0005 < nextEncoded {
            if pendingProduce == nil {
                let work = DispatchWorkItem { [weak self] in
                    self?.pendingProduce = nil; self?.produce()
                }
                pendingProduce = work
                queue.asyncAfter(deadline: .now() + (nextEncoded - now), execute: work)
            }
            return
        }
        pendingProduce?.cancel(); pendingProduce = nil
        // Preserve the intended cadence across early/late capture callbacks;
        // restarting the interval at a late callback compounds scheduler jitter.
        nextEncoded = max(now, nextEncoded + 1.0 / Double(settings.frameRate))
        sentSequence = sequence; frameID += 1; inFlight[frameID] = -1
        let id = frameID
        let capturedAt = changedAt
        if let encoder {
            let properties = forceKeyFrame ? [kVTEncodeFrameOptionKey_ForceKeyFrame: true] as CFDictionary : nil
            forceKeyFrame = false
            let encode = { [weak self] in
                self?.encode(buffer, using: encoder, id: id, timestamp: now, capturedAt: capturedAt, properties: properties)
            }
            if encoderHardware { encode() }
            else {
                softwareQueue.async {
                    encode()
                    if VTCompressionSessionCompleteFrames(encoder, untilPresentationTimeStamp: .invalid) != noErr {
                        self.queue.async {
                            if !self.stopped, self.inFlight[id] != nil { self.fallbackToImages(buffer, id: id) }
                        }
                    }
                }
            }
        } else { forceKeyFrame = false; outputImage(buffer, id: id, lossless: false) }
    }
    private func encode(_ buffer: CVPixelBuffer, using encoder: VTCompressionSession, id: Int,
                        timestamp: TimeInterval, capturedAt: TimeInterval, properties: CFDictionary?) {
        let result = VTCompressionSessionEncodeFrame(encoder, imageBuffer: buffer,
            presentationTimeStamp: CMTime(seconds: timestamp, preferredTimescale: 1_000_000), duration: .invalid,
            frameProperties: properties, infoFlagsOut: nil) { [weak self] status, _, sample in
            guard let self else { return }
            self.queue.async {
                guard !self.stopped, self.inFlight[id] != nil else { return }
                guard status == noErr, let sample, let data = sample.dataBuffer,
                      let format = sample.formatDescription else { self.fallbackToImages(buffer, id: id); return }
                let length = CMBlockBufferGetDataLength(data)
                var bytes = Data(count: length)
                let copied = bytes.withUnsafeMutableBytes { CMBlockBufferCopyDataBytes(data, atOffset: 0, dataLength: length, destination: $0.baseAddress!) }
                guard copied == noErr else { self.fallbackToImages(buffer, id: id); return }
                let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[CFString: Any]]
                let key = attachments?.first?[kCMSampleAttachmentKey_NotSync] as? Bool != true
                var metadata: [String: Any] = ["codec": "h264", "key": key, "timestamp": Int(capturedAt * 1_000_000)]
                if key {
                    let extensions = CMFormatDescriptionGetExtensions(format) as NSDictionary?
                    guard let atoms = extensions?[kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms] as? NSDictionary,
                          let avcc = atoms["avcC"] as? Data, avcc.count >= 4 else { self.fallbackToImages(buffer, id: id); return }
                    metadata["description"] = avcc.base64EncodedString()
                    metadata["profile"] = "avc1." + avcc[1...3].map { String(format: "%02X", $0) }.joined()
                }
                self.output(bytes, id: id, metadata: metadata)
            }
        }
        if result != noErr {
            queue.async { if !self.stopped, self.inFlight[id] != nil { self.fallbackToImages(buffer, id: id) } }
        }
    }

    private func retireEncoder() {
        guard let encoder else { return }
        self.encoder = nil
        if encoderHardware { VTCompressionSessionInvalidate(encoder) }
        else { softwareQueue.async { VTCompressionSessionInvalidate(encoder) } }
    }
    private func refineImage(_ buffer: CVPixelBuffer) {
        guard !refinementPending, inFlight.isEmpty else { return }
        refinementPending = true
        let sourceSequence = sequence
        let sourceGeneration = generation
        let capturedAt = changedAt
        let encodeStill: (CGImage?) -> Void = { [weak self] image in
            guard let self else { return }
            self.refinementQueue.async {
                let data = NSMutableData()
                var completed = false
                autoreleasepool {
                    if let cg = image,
                       let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) {
                        CGImageDestinationAddImage(destination, cg, nil)
                        completed = CGImageDestinationFinalize(destination)
                    }
                }
                let bytes = data as Data
                let succeeded = completed
                self.queue.async {
                    self.refinementPending = false
                    guard !self.stopped, self.generation == sourceGeneration,
                          self.sequence == sourceSequence, self.inFlight.isEmpty else { return }
                    guard succeeded else {
                        self.failure(NativeDesktopError(code: "REFINEMENT_FAILED", message: "The lossless desktop capture failed.")); return
                    }
                    self.refinedSequence = sourceSequence
                    self.frameID += 1; self.inFlight[self.frameID] = -1
                    self.output(bytes, id: self.frameID, metadata: ["codec": "png", "key": true, "timestamp": Int(capturedAt * 1_000_000)])
                }
            }
        }
        if #available(macOS 14.0, *), let captureFilter, let captureConfiguration {
            // ScreenCaptureKit's video samples can differ from its still-image
            // output after display color conversion. Refine with the authorized
            // still capture at the exact same filter and pixel dimensions.
            SCScreenshotManager.captureImage(contentFilter: captureFilter, configuration: captureConfiguration) { image, error in
                encodeStill(error == nil ? image : nil)
            }
        } else { encodeStill(nativeCaptureImage(buffer)) }
    }
    private func fallbackToImages(_ buffer: CVPixelBuffer, id: Int) {
        if settings.requireVideo {
            failure(NativeDesktopError(code: "VIDEO_ENCODER_FAILED", message: "The desktop H.264 encoder failed."))
            return
        }
        retireEncoder(); videoActive = false
        outputImage(buffer, id: id, lossless: false)
    }
    private func outputImage(_ buffer: CVPixelBuffer, id: Int, lossless: Bool) {
        autoreleasepool {
            guard let cg = nativeCaptureImage(buffer) else { failure(NativeDesktopInput.unavailable()); return }
            let data = NSMutableData()
            guard let destination = CGImageDestinationCreateWithData(data, (lossless ? UTType.png : UTType.jpeg).identifier as CFString, 1, nil) else { failure(NativeDesktopInput.unavailable()); return }
            let options = lossless ? nil : [kCGImageDestinationLossyCompressionQuality: settings.imageQuality * (settings.mode == "auto" ? max(0.8, adaptiveFactor) : 1)] as CFDictionary
            CGImageDestinationAddImage(destination, cg, options)
            guard CGImageDestinationFinalize(destination) else { failure(NativeDesktopInput.unavailable()); return }
            output(data as Data, id: id, metadata: ["codec": lossless ? "png" : "jpeg", "key": true])
        }
    }
    private func output(_ data: Data, id: Int, metadata: [String: Any]) {
        inFlight[id] = ProcessInfo.processInfo.systemUptime
        var message = metadata
        message.merge(["type": "frame", "generation": generation, "frame_id": id, "data": data.base64EncodedString(),
                       "width": Int(dimensions.width), "height": Int(dimensions.height), "frame_rate": settings.frameRate,
                       "encoder": videoActive ? encoderName : "",
                       "transport": videoActive ? "video" : "images", "mode": settings.mode]) { _, new in new }
        emitFrame(message)
    }
}

func nativeCaptureImage(_ buffer: CVPixelBuffer) -> CGImage? {
    // Use the source buffer's pixel format and color profile directly.
    var image: CGImage?
    guard VTCreateCGImageFromCVPixelBuffer(buffer, options: nil, imageOut: &image) == noErr else { return nil }
    return image
}
