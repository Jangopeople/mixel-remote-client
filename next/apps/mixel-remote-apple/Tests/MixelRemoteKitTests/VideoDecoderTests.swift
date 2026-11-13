import CoreGraphics
import CoreMedia
import CoreVideo
import VideoToolbox
import XCTest
@testable import MixelRemoteKit

/// The device's own H.264 decoder: a real stream (encoded here with VideoToolbox) in the engine's
/// "MRH1" packets is decoded back to the pictures that went in.
final class VideoDecoderTests: XCTestCase {
    /// Encodes solid-colour pictures and returns them as MRH1 packets (Annex B, SPS/PPS on keyframes).
    static func encode(colors: [(UInt8, UInt8, UInt8)], width: Int = 320, height: Int = 240) throws -> [Data] {
        var session: VTCompressionSession?
        let status = VTCompressionSessionCreate(allocator: nil, width: Int32(width), height: Int32(height), codecType: kCMVideoCodecType_H264,
                                                encoderSpecification: nil, imageBufferAttributes: nil, compressedDataAllocator: nil,
                                                outputCallback: nil, refcon: nil, compressionSessionOut: &session)
        guard status == noErr, let session else { throw XCTSkip("No H.264 encoder here (\(status))") }
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AllowFrameReordering, value: kCFBooleanFalse)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_ProfileLevel, value: kVTProfileLevel_H264_Baseline_AutoLevel)
        var packets: [Data] = []
        for (index, color) in colors.enumerated() {
            var pixels: CVPixelBuffer?
            CVPixelBufferCreate(nil, width, height, kCVPixelFormatType_32BGRA, nil, &pixels)
            guard let pixels else { throw XCTSkip("No pixel buffer") }
            CVPixelBufferLockBaseAddress(pixels, [])
            let base = CVPixelBufferGetBaseAddress(pixels)!.assumingMemoryBound(to: UInt8.self)
            let row = CVPixelBufferGetBytesPerRow(pixels)
            for y in 0..<height { for x in 0..<width {
                let at = base + y * row + x * 4
                at[0] = color.2; at[1] = color.1; at[2] = color.0; at[3] = 255
            } }
            CVPixelBufferUnlockBaseAddress(pixels, [])
            var packet: Data?
            let options = index == 0 ? [kVTEncodeFrameOptionKey_ForceKeyFrame: true] as CFDictionary : nil
            let encoded = VTCompressionSessionEncodeFrame(session, imageBuffer: pixels, presentationTimeStamp: CMTime(value: CMTimeValue(index), timescale: 30),
                                                          duration: .invalid, frameProperties: options, infoFlagsOut: nil) { status, _, sample in
                guard status == noErr, let sample else { return }
                packet = Self.packet(sample, sequence: UInt64(index + 1))
            }
            XCTAssertEqual(encoded, noErr)
            VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid)
            if let packet { packets.append(packet) }
        }
        VTCompressionSessionInvalidate(session)
        return packets
    }

    static func packet(_ sample: CMSampleBuffer, sequence: UInt64) -> Data? {
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[CFString: Any]]
        let keyframe = !(attachments?.first?[kCMSampleAttachmentKey_NotSync] as? Bool ?? false)
        var annexB = Data()
        let start: [UInt8] = [0, 0, 0, 1]
        if keyframe, let format = CMSampleBufferGetFormatDescription(sample) {
            var count = 0
            CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, parameterSetIndex: 0, parameterSetPointerOut: nil, parameterSetSizeOut: nil,
                                                               parameterSetCountOut: &count, nalUnitHeaderLengthOut: nil)
            for index in 0..<count {
                var pointer: UnsafePointer<UInt8>?
                var size = 0
                CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, parameterSetIndex: index, parameterSetPointerOut: &pointer,
                                                                   parameterSetSizeOut: &size, parameterSetCountOut: nil, nalUnitHeaderLengthOut: nil)
                if let pointer { annexB.append(contentsOf: start); annexB.append(pointer, count: size) }
            }
        }
        guard let block = CMSampleBufferGetDataBuffer(sample) else { return nil }
        var length = 0
        var pointer: UnsafeMutablePointer<CChar>?
        CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &pointer)
        guard let pointer else { return nil }
        let bytes = UnsafeRawPointer(pointer).assumingMemoryBound(to: UInt8.self)
        var offset = 0
        while offset + 4 <= length {
            let size = Int(bytes[offset]) << 24 | Int(bytes[offset + 1]) << 16 | Int(bytes[offset + 2]) << 8 | Int(bytes[offset + 3])
            offset += 4
            guard offset + size <= length else { return nil }
            annexB.append(contentsOf: start)
            annexB.append(bytes + offset, count: size)
            offset += size
        }
        var header = Data("MRH1".utf8)
        func append<T: FixedWidthInteger>(_ value: T) { withUnsafeBytes(of: value.littleEndian) { header.append(contentsOf: $0) } }
        append(UInt32(keyframe ? 1 : 0)); append(UInt64(0)); append(sequence); append(sequence * 33_333)
        return header + annexB
    }

    /// The colour in the middle of a decoded picture.
    static func center(_ image: CGImage) -> (Int, Int, Int) {
        var pixel = [UInt8](repeating: 0, count: 4)
        let context = CGContext(data: &pixel, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        context.draw(image, in: CGRect(x: -CGFloat(image.width) / 2, y: -CGFloat(image.height) / 2, width: CGFloat(image.width), height: CGFloat(image.height)))
        return (Int(pixel[0]), Int(pixel[1]), Int(pixel[2]))
    }

    func testARealStreamDecodesToItsPictures() throws {
        let colors: [(UInt8, UInt8, UInt8)] = [(200, 30, 40), (30, 180, 60), (40, 60, 200), (220, 220, 220)]
        let packets = try Self.encode(colors: colors)
        XCTAssertEqual(packets.count, colors.count)
        XCTAssertTrue(packets.allSatisfy(VideoFrame.isCompressed))
        XCTAssertTrue(try H264Decoder.parse(packets[0]).keyframe)
        let decoder = H264Decoder()
        for (packet, color) in zip(packets, colors) {
            let frame = try decoder.decode(packet)
            XCTAssertEqual(frame.width, 320)
            XCTAssertEqual(frame.height, 240)
            // Colour-managed (video matrix and transfer of the stream): close, and the same dominant colour.
            let (r, g, b) = Self.center(frame.image)
            let got = [r, g, b], wanted = [Int(color.0), Int(color.1), Int(color.2)]
            XCTAssertTrue(zip(got, wanted).allSatisfy { abs($0 - $1) <= 60 }, "got \(got) for \(wanted)")
            XCTAssertEqual(got.firstIndex(of: got.max()!), wanted.firstIndex(of: wanted.max()!), "got \(got) for \(wanted)")
        }
    }

    func testPicturesBeforeTheFirstKeyframeAndBrokenPacketsAreRefused() throws {
        let packets = try Self.encode(colors: [(10, 10, 10), (90, 90, 90)])
        let decoder = H264Decoder()
        XCTAssertThrowsError(try decoder.decode(packets[1])) { XCTAssertEqual($0 as? H264Decoder.Problem, .waitingForKeyframe) }
        XCTAssertNoThrow(try decoder.decode(packets[0]))
        // Out of order (an older sequence number) is refused.
        XCTAssertThrowsError(try decoder.decode(packets[0]))
        XCTAssertThrowsError(try H264Decoder.parse(Data("MRF2".utf8) + Data(count: 40)))
        XCTAssertThrowsError(try H264Decoder.parse(Data("MRH1".utf8) + Data(count: 10)))
        var flags = packets[0]
        flags[4] = 6
        XCTAssertThrowsError(try H264Decoder.parse(flags))
        decoder.reset()
        XCTAssertThrowsError(try decoder.decode(packets[1])) { XCTAssertEqual($0 as? H264Decoder.Problem, .waitingForKeyframe) }
    }

    func testAnnexBSplitsOnThreeAndFourByteStartCodes() {
        let units = H264Decoder.splitAnnexB([0, 0, 0, 1, 0x67, 1, 2, 0, 0, 1, 0x68, 3, 0, 0, 0, 1, 0x65, 4, 5], from: 0)
        XCTAssertEqual(units, [Data([0x67, 1, 2]), Data([0x68, 3]), Data([0x65, 4, 5])])
    }
}
