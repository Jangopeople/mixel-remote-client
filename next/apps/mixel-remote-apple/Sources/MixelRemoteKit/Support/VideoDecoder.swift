import CoreGraphics
import CoreMedia
import CoreVideo
import Foundation
import VideoToolbox

/// H.264 pictures decoded on this device (VideoToolbox, hardware where available), so the engine
/// passes the compressed stream through instead of decoding every picture itself.
///
/// Packet "MRH1" (from the engine, little endian): magic, flags (1 = complete picture), 8 unused
/// bytes, sequence, timestamp (microseconds), then one H.264 access unit in Annex B (start codes). A complete
/// picture carries the sequence and picture parameter sets (SPS/PPS); later pictures build on it.
public final class H264Decoder: @unchecked Sendable {
    public enum Problem: Error, Equatable {
        /// Not an "MRH1" packet, or out of bounds.
        case invalid
        /// The stream needs a complete picture first (none seen yet, or the decoder was reset).
        case waitingForKeyframe
        /// VideoToolbox could not decode the picture (status code).
        case decoder(Int32)
    }

    public struct Packet: Equatable {
        public var keyframe: Bool
        public var sequence: UInt64
        public var timestamp: UInt64
        public var units: [Data]
    }

    private var format: CMVideoFormatDescription?
    private var session: VTDecompressionSession?
    private var parameterSets: [Data] = []
    private var lastSequence: UInt64 = 0

    public init() {}

    deinit { invalidate() }

    /// Forgets the stream (a new session, or after a decoding error): the next picture must be complete.
    public func reset() {
        invalidate()
        format = nil
        parameterSets = []
        lastSequence = 0
    }

    private func invalidate() {
        if let session {
            VTDecompressionSessionWaitForAsynchronousFrames(session)
            VTDecompressionSessionInvalidate(session)
        }
        session = nil
    }

    /// Reads the header and splits the access unit into its NAL units (without start codes).
    public static func parse(_ data: Data) throws -> Packet {
        guard data.count > 32, data.count <= 32 + 4 * 1024 * 1024 else { throw Problem.invalid }
        let bytes = [UInt8](data)
        guard bytes[0] == 77, bytes[1] == 82, bytes[2] == 72, bytes[3] == 49 else { throw Problem.invalid }
        func u32(_ at: Int) -> UInt32 { (0..<4).reduce(UInt32(0)) { $0 | UInt32(bytes[at + $1]) << (8 * UInt32($1)) } }
        func u64(_ at: Int) -> UInt64 { (0..<8).reduce(UInt64(0)) { $0 | UInt64(bytes[at + $1]) << (8 * UInt64($1)) } }
        let flags = u32(4)
        guard flags & ~1 == 0 else { throw Problem.invalid }
        let sequence = u64(16)
        guard sequence > 0 else { throw Problem.invalid }
        let units = splitAnnexB(bytes, from: 32)
        guard !units.isEmpty else { throw Problem.invalid }
        return Packet(keyframe: flags & 1 == 1, sequence: sequence, timestamp: u64(24), units: units)
    }

    /// NAL units of an Annex B stream (00 00 01 or 00 00 00 01 start codes).
    static func splitAnnexB(_ bytes: [UInt8], from start: Int) -> [Data] {
        var units: [Data] = []
        var index = start
        var unitStart: Int?
        let end = bytes.count
        while index + 2 < end {
            if bytes[index] == 0, bytes[index + 1] == 0, bytes[index + 2] == 1 {
                if let begin = unitStart {
                    var stop = index
                    while stop > begin, bytes[stop - 1] == 0 { stop -= 1 }
                    if stop > begin { units.append(Data(bytes[begin..<stop])) }
                }
                index += 3
                unitStart = index
            } else {
                index += 1
            }
        }
        if let begin = unitStart, begin < end { units.append(Data(bytes[begin..<end])) }
        return units
    }

    /// Decodes one packet into a picture. Pictures must arrive in order (sequence numbers grow).
    public func decode(_ data: Data) throws -> VideoFrame {
        let packet = try Self.parse(data)
        guard packet.sequence > lastSequence else { throw Problem.invalid }
        let sets = packet.units.filter { nalType($0) == 7 || nalType($0) == 8 }
        let slices = packet.units.filter { [1, 5].contains(nalType($0)) }
        if !sets.isEmpty, sets != parameterSets {
            guard sets.contains(where: { nalType($0) == 7 }), sets.contains(where: { nalType($0) == 8 }) else { throw Problem.invalid }
            try configure(sets)
        }
        guard let format, let session else { throw Problem.waitingForKeyframe }
        guard !slices.isEmpty else { throw Problem.invalid }
        lastSequence = packet.sequence

        // AVCC: every NAL unit with a 4-byte big-endian length instead of a start code.
        var avcc = Data(capacity: slices.reduce(0) { $0 + $1.count + 4 })
        for unit in slices {
            var length = UInt32(unit.count).bigEndian
            withUnsafeBytes(of: &length) { avcc.append(contentsOf: $0) }
            avcc.append(unit)
        }
        var block: CMBlockBuffer?
        var status = CMBlockBufferCreateWithMemoryBlock(allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: avcc.count,
                                                        blockAllocator: kCFAllocatorDefault, customBlockSource: nil, offsetToData: 0,
                                                        dataLength: avcc.count, flags: 0, blockBufferOut: &block)
        guard status == noErr, let block else { throw Problem.decoder(status) }
        status = avcc.withUnsafeBytes { raw in
            CMBlockBufferReplaceDataBytes(with: raw.baseAddress!, blockBuffer: block, offsetIntoDestination: 0, dataLength: avcc.count)
        }
        guard status == noErr else { throw Problem.decoder(status) }
        var sample: CMSampleBuffer?
        var size = avcc.count
        var timing = CMSampleTimingInfo(duration: .invalid, presentationTimeStamp: CMTime(value: CMTimeValue(clamping: packet.timestamp), timescale: 1_000_000),
                                        decodeTimeStamp: .invalid)
        status = CMSampleBufferCreateReady(allocator: kCFAllocatorDefault, dataBuffer: block, formatDescription: format, sampleCount: 1,
                                           sampleTimingEntryCount: 1, sampleTimingArray: &timing, sampleSizeEntryCount: 1,
                                           sampleSizeArray: &size, sampleBufferOut: &sample)
        guard status == noErr, let sample else { throw Problem.decoder(status) }

        var decoded: CVImageBuffer?
        var decodeStatus: OSStatus = noErr
        status = VTDecompressionSessionDecodeFrame(session, sampleBuffer: sample, flags: [], infoFlagsOut: nil) { result, _, image, _, _ in
            decodeStatus = result
            decoded = image
        }
        if status == noErr { status = decodeStatus }
        guard status == noErr, let pixels = decoded else {
            reset()
            throw Problem.decoder(status)
        }
        var image: CGImage?
        status = VTCreateCGImageFromCVPixelBuffer(pixels, options: nil, imageOut: &image)
        guard status == noErr, let image else { throw Problem.decoder(status) }
        let width = image.width, height = image.height
        // The same bounds as decoded pictures (at most 1920 × 1088, or upright).
        guard width >= 2, height >= 2, max(width, height) <= 1920, min(width, height) <= 1088 else { throw Problem.invalid }
        return VideoFrame(image: image, width: width, height: height, sequence: packet.sequence)
    }

    private func nalType(_ unit: Data) -> UInt8 { unit.first.map { $0 & 0x1F } ?? 0 }

    private func configure(_ sets: [Data]) throws {
        let sps = sets.filter { nalType($0) == 7 }.prefix(1), pps = sets.filter { nalType($0) == 8 }
        let ordered = Array(sps) + pps
        var description: CMFormatDescription?
        let status: OSStatus = ordered.withPointers { pointers, sizes in
            CMVideoFormatDescriptionCreateFromH264ParameterSets(allocator: kCFAllocatorDefault, parameterSetCount: ordered.count,
                                                                parameterSetPointers: pointers, parameterSetSizes: sizes,
                                                                nalUnitHeaderLength: 4, formatDescriptionOut: &description)
        }
        guard status == noErr, let description else { throw Problem.decoder(status) }
        if let session, VTDecompressionSessionCanAcceptFormatDescription(session, formatDescription: description) {
            format = description
            parameterSets = sets
            return
        }
        invalidate()
        let attributes: [CFString: Any] = [kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA]
        var created: VTDecompressionSession?
        let made = VTDecompressionSessionCreate(allocator: kCFAllocatorDefault, formatDescription: description, decoderSpecification: nil,
                                                imageBufferAttributes: attributes as CFDictionary, outputCallback: nil,
                                                decompressionSessionOut: &created)
        guard made == noErr, let created else { throw Problem.decoder(made) }
        session = created
        format = description
        parameterSets = sets
    }
}

private extension Array where Element == Data {
    /// Calls `body` with C pointers to every element's bytes (valid only inside `body`).
    func withPointers<R>(_ body: (UnsafePointer<UnsafePointer<UInt8>>, UnsafePointer<Int>) -> R) -> R {
        let buffers = map { data -> UnsafeMutablePointer<UInt8> in
            let pointer = UnsafeMutablePointer<UInt8>.allocate(capacity: Swift.max(data.count, 1))
            data.copyBytes(to: pointer, count: data.count)
            return pointer
        }
        defer { buffers.forEach { $0.deallocate() } }
        let pointers = buffers.map { UnsafePointer($0) }
        let sizes = map(\.count)
        return pointers.withUnsafeBufferPointer { p in sizes.withUnsafeBufferPointer { s in body(p.baseAddress!, s.baseAddress!) } }
    }
}
