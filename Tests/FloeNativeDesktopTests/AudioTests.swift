import AVFoundation
import CoreMedia
import XCTest
@testable import FloeNativeDesktop

final class AudioTests: XCTestCase {
    func testSyntheticSystemAudioProducesBoundedOpusPackets() throws {
        let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 2))
        var packets: [(Data, Int64)] = []
        let encoder = NativeOpusEncoder { packets.append(($0, $1)) }
        for index in 0..<50 {
            let pcm = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 960))
            pcm.frameLength = 960
            for channel in 0..<2 {
                for frame in 0..<960 {
                    pcm.floatChannelData![channel][frame] = Float(sin(Double(index * 960 + frame) * 2 * .pi * 440 / 48000) * 0.2)
                }
            }
            var timing = CMSampleTimingInfo(duration: CMTime(value: 1, timescale: 48000),
                presentationTimeStamp: CMTime(value: Int64(index * 960), timescale: 48000), decodeTimeStamp: .invalid)
            var sample: CMSampleBuffer?
            XCTAssertEqual(CMSampleBufferCreate(allocator: kCFAllocatorDefault, dataBuffer: nil, dataReady: false,
                makeDataReadyCallback: nil, refcon: nil, formatDescription: format.formatDescription, sampleCount: 960,
                sampleTimingEntryCount: 1, sampleTimingArray: &timing, sampleSizeEntryCount: 0,
                sampleSizeArray: nil, sampleBufferOut: &sample), noErr)
            let ready = try XCTUnwrap(sample)
            XCTAssertEqual(CMSampleBufferSetDataBufferFromAudioBufferList(ready, blockBufferAllocator: kCFAllocatorDefault,
                blockBufferMemoryAllocator: kCFAllocatorDefault, flags: 0, bufferList: pcm.audioBufferList), noErr)
            XCTAssertEqual(CMSampleBufferSetDataReady(ready), noErr)
            encoder.append(ready)
        }
        XCTAssertGreaterThanOrEqual(packets.count, 40)
        XCTAssertLessThanOrEqual(packets.count, 55)
        XCTAssertTrue(packets.allSatisfy { !$0.0.isEmpty && $0.0.count <= 4096 })
        for index in 1..<packets.count {
            XCTAssertGreaterThan(packets[index].1, packets[index - 1].1)
            XCTAssertEqual(packets[index].1 - packets[index - 1].1, 20000)
        }
    }
}
