import AVFoundation
import CoreMedia

// ScreenCaptureKit supplies only the authorized system-output samples. Native
// AudioConverter provides Opus; no microphone or additional capture process exists.
final class NativeOpusEncoder {
    static var isSupported: Bool {
        guard let input = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 2),
              let output = AVAudioFormat(settings: [AVFormatIDKey: kAudioFormatOpus,
                  AVSampleRateKey: 48000, AVNumberOfChannelsKey: 2]) else { return false }
        return AVAudioConverter(from: input, to: output) != nil
    }
    private var converter: AVAudioConverter?
    private var inputFormat: AVAudioFormat?
    private var timestamp: Int64 = 0
    private let output: (Data, Int64) -> Void

    init(output: @escaping (Data, Int64) -> Void) { self.output = output }

    func reset() {
        converter = nil; inputFormat = nil; timestamp = 0
    }

    func append(_ sample: CMSampleBuffer) {
        guard sample.isValid, let description = sample.formatDescription,
              let source = AVAudioFormat(cmAudioFormatDescription: description) as AVAudioFormat?,
              let destination = AVAudioFormat(settings: [AVFormatIDKey: kAudioFormatOpus,
                  AVSampleRateKey: 48000, AVNumberOfChannelsKey: 2]) else { return }
        if converter == nil || inputFormat != source {
            converter = AVAudioConverter(from: source, to: destination)
            converter?.bitRate = 128000
            inputFormat = source
            timestamp = Int64(CMTimeGetSeconds(sample.presentationTimeStamp) * 1_000_000)
        }
        guard let converter,
              let input = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: AVAudioFrameCount(sample.numSamples)) else { return }
        input.frameLength = AVAudioFrameCount(sample.numSamples)
        guard CMSampleBufferCopyPCMDataIntoAudioBufferList(sample, at: 0, frameCount: Int32(sample.numSamples), into: input.mutableAudioBufferList) == noErr else { return }
        let encoded = AVAudioCompressedBuffer(format: destination, packetCapacity: 8,
            maximumPacketSize: converter.maximumOutputPacketSize)
        var supplied = false
        var failure: NSError?
        let status = converter.convert(to: encoded, error: &failure) { _, state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true; state.pointee = .haveData; return input
        }
        guard status != .error, failure == nil, let descriptions = encoded.packetDescriptions else { return }
        for index in 0..<Int(encoded.packetCount) {
            let packet = descriptions[index]
            let data = Data(bytes: encoded.data.advanced(by: Int(packet.mStartOffset)), count: Int(packet.mDataByteSize))
            output(data, timestamp)
            let frames = packet.mVariableFramesInPacket == 0 ? 960 : packet.mVariableFramesInPacket
            timestamp += Int64(frames) * 1_000_000 / 48000
        }
    }
}
