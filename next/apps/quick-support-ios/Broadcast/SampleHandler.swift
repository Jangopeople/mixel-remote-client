import CoreMedia
import QuickHost
import ReplayKit
import UIKit
import VideoToolbox

/// Turns the pictures iOS hands to the broadcast into H.264 in the form the host core sends
/// (baseline, start codes, parameter sets in front of every complete picture).
final class ScreenEncoder {
    private var session: VTCompressionSession?
    private var last = CMTime.invalid
    /// The viewer needs a complete picture next.
    var wantsKeyframe = true
    var output: ((Data) -> Void)?

    private func start(width: Int32, height: Int32) -> Bool {
        var created: VTCompressionSession?
        guard VTCompressionSessionCreate(allocator: nil, width: width, height: height, codecType: kCMVideoCodecType_H264, encoderSpecification: nil, imageBufferAttributes: nil, compressedDataAllocator: nil, outputCallback: nil, refcon: nil, compressionSessionOut: &created) == noErr, let created else { return false }
        VTSessionSetProperty(created, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        VTSessionSetProperty(created, key: kVTCompressionPropertyKey_ProfileLevel, value: kVTProfileLevel_H264_Baseline_AutoLevel)
        VTSessionSetProperty(created, key: kVTCompressionPropertyKey_AllowFrameReordering, value: kCFBooleanFalse)
        VTSessionSetProperty(created, key: kVTCompressionPropertyKey_AverageBitRate, value: 2_500_000 as CFNumber)
        VTSessionSetProperty(created, key: kVTCompressionPropertyKey_ExpectedFrameRate, value: 15 as CFNumber)
        VTSessionSetProperty(created, key: kVTCompressionPropertyKey_MaxKeyFrameInterval, value: 300 as CFNumber)
        VTCompressionSessionPrepareToEncodeFrames(created)
        session = created
        return true
    }

    func encode(_ sample: CMSampleBuffer, width: Int, height: Int) {
        guard let image = CMSampleBufferGetImageBuffer(sample) else { return }
        // At most 15 pictures a second: enough for support, and it keeps this small process light.
        let time = CMSampleBufferGetPresentationTimeStamp(sample)
        if last.isValid, CMTimeGetSeconds(CMTimeSubtract(time, last)) < 1.0 / 15.0, !wantsKeyframe { return }
        last = time
        if session == nil, !start(width: Int32(width), height: Int32(height)) { return }
        guard let session else { return }
        var options: CFDictionary?
        if wantsKeyframe {
            wantsKeyframe = false
            options = [kVTEncodeFrameOptionKey_ForceKeyFrame: kCFBooleanTrue] as CFDictionary
        }
        VTCompressionSessionEncodeFrame(session, imageBuffer: image, presentationTimeStamp: time, duration: .invalid, frameProperties: options, infoFlagsOut: nil) { [weak self] status, _, encoded in
            guard status == noErr, let encoded, let self, let data = Self.annexB(encoded) else { return }
            self.output?(data)
        }
    }

    func stop() {
        if let session { VTCompressionSessionInvalidate(session) }
        session = nil
    }

    /// The encoder's output has length-prefixed units; the host core wants start codes.
    private static func annexB(_ sample: CMSampleBuffer) -> Data? {
        guard CMSampleBufferDataIsReady(sample), let block = CMSampleBufferGetDataBuffer(sample) else { return nil }
        let start: [UInt8] = [0, 0, 0, 1]
        var out = Data()
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[CFString: Any]]
        let complete = !((attachments?.first?[kCMSampleAttachmentKey_NotSync] as? Bool) ?? false)
        if complete, let format = CMSampleBufferGetFormatDescription(sample) {
            var count = 0
            CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, parameterSetIndex: 0, parameterSetPointerOut: nil, parameterSetSizeOut: nil, parameterSetCountOut: &count, nalUnitHeaderLengthOut: nil)
            for index in 0..<count {
                var pointer: UnsafePointer<UInt8>?
                var size = 0
                if CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, parameterSetIndex: index, parameterSetPointerOut: &pointer, parameterSetSizeOut: &size, parameterSetCountOut: nil, nalUnitHeaderLengthOut: nil) == noErr, let pointer {
                    out.append(contentsOf: start)
                    out.append(pointer, count: size)
                }
            }
        }
        let total = CMBlockBufferGetDataLength(block)
        var bytes = [UInt8](repeating: 0, count: total)
        guard CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: total, destination: &bytes) == noErr else { return nil }
        var offset = 0
        while offset + 4 <= total {
            let length = Int(bytes[offset]) << 24 | Int(bytes[offset + 1]) << 16 | Int(bytes[offset + 2]) << 8 | Int(bytes[offset + 3])
            offset += 4
            guard length > 0, offset + length <= total else { return nil }
            out.append(contentsOf: start)
            out.append(contentsOf: bytes[offset..<(offset + length)])
            offset += length
        }
        return out.isEmpty ? nil : out
    }
}

/// The screen broadcast: iOS runs this while the customer shares the screen, also when they
/// switch to other apps. It holds the whole support session.
final class SampleHandler: RPBroadcastSampleHandler {
    private var host: OpaquePointer?
    private let encoder = ScreenEncoder()
    private var size = (0, 0)
    private var finished = false

    private func fail(_ text: String) {
        guard !finished else { return }
        finished = true
        finishBroadcastWithError(NSError(domain: "ch.mixel.quicksupport", code: 1, userInfo: [NSLocalizedDescriptionKey: text, NSLocalizedFailureReasonErrorKey: text]))
    }

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        guard let seed = Shared.data("seed"), seed.count == 32 else {
            fail(Texts.of("no_code"))
            return
        }
        let context = Unmanaged.passUnretained(self).toOpaque()
        host = seed.withUnsafeBytes { bytes in
            mqs_create(bytes.bindMemory(to: UInt8.self).baseAddress, { event, context in
                guard let event, let context else { return }
                Unmanaged<SampleHandler>.fromOpaque(context).takeUnretainedValue().event(String(cString: event))
            }, context)
        }
        guard let host else { fail(Texts.of("failed")); return }
        let screen = UIScreen.main.nativeBounds
        size = Shared.pictureSize(width: Int(screen.width), height: Int(screen.height))
        // Starting the broadcast is the person's consent; only the technician who typed this phone's ID gets in.
        mqs_accept_issuer(host, true)
        encoder.output = { [weak self] data in
            guard let host = self?.host else { return }
            data.withUnsafeBytes { mqs_push_frame(host, $0.bindMemory(to: UInt8.self).baseAddress, data.count) }
        }
        // Wait (up to 10 minutes) until the technician has typed this phone's ID, then go online.
        let name = Shared.deviceName
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            for _ in 0..<300 {
                guard let self, !self.finished, let host = self.host else { return }
                guard let answer = mqs_register(host, name) else { Thread.sleep(forTimeInterval: 2); continue }
                let json = String(cString: answer)
                mqs_free(answer)
                if let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any], object["error"] == nil, object["pending"] == nil, object["device_id"] != nil {
                    if let problem = mqs_start(host, json, UInt32(self.size.0), UInt32(self.size.1), false) {
                        let text = String(cString: problem)
                        mqs_free(problem)
                        self.fail(text)
                    }
                    return
                }
                Thread.sleep(forTimeInterval: 2)
            }
            self?.fail(Texts.of("not_claimed"))
        }
    }

    private func event(_ json: String) {
        guard let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any], let type = object["type"] as? String else { return }
        switch type {
        case "keyframe": encoder.wantsKeyframe = true
        case "started": encoder.wantsKeyframe = true
        case "ended": fail(Texts.of("ended"))
        case "status": if object["phase"] as? String == "offline" { fail(Texts.of("offline")) }
        default: break
        }
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video, host != nil, !finished else { return }
        encoder.encode(sampleBuffer, width: size.0, height: size.1)
    }

    override func broadcastFinished() {
        finished = true
        encoder.stop()
        if let host {
            mqs_stop(host)
            mqs_destroy(host)
        }
        host = nil
    }
}
