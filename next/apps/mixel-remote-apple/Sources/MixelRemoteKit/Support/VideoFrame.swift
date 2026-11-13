import CoreGraphics
import Foundation

/// A session picture from the engine. "MRF2": a 32-byte header (magic, width, height, stride,
/// sequence, timestamp; little endian) followed by RGBA rows. "MRF1": the same with BGRA rows.
public struct VideoFrame: @unchecked Sendable {
    public let image: CGImage
    public let width: Int
    public let height: Int
    public let sequence: UInt64

    public enum Problem: Error, Equatable { case invalid, compressed }

    /// Checks the header and bounds exactly as the desktop app does (at most 1920 × 1088, or upright).
    public static func decode(_ data: Data, after previous: UInt64 = 0) throws -> VideoFrame {
        guard data.count > 32 else { throw Problem.invalid }
        let bytes = [UInt8](data.prefix(32))
        guard bytes[0] == 77, bytes[1] == 82 else { throw Problem.invalid }
        if bytes[2] == 72, bytes[3] == 49 { throw Problem.compressed }
        guard bytes[2] == 70, bytes[3] == 50 || bytes[3] == 49 else { throw Problem.invalid }
        func u32(_ at: Int) -> Int { Int(bytes[at]) | Int(bytes[at + 1]) << 8 | Int(bytes[at + 2]) << 16 | Int(bytes[at + 3]) << 24 }
        func u64(_ at: Int) -> UInt64 { (0..<8).reduce(UInt64(0)) { $0 | UInt64(bytes[at + $1]) << (8 * UInt64($1)) } }
        let width = u32(4), height = u32(8), stride = u32(12)
        let sequence = u64(16)
        guard width >= 2, height >= 2, max(width, height) <= 1920, min(width, height) <= 1088,
              stride >= width * 4, data.count == 32 + stride * height, sequence > previous
        else { throw Problem.invalid }
        let packed = bytes[3] == 50
        let pixels = data.subdata(in: 32..<data.count)
        guard let provider = CGDataProvider(data: pixels as CFData) else { throw Problem.invalid }
        let info: CGBitmapInfo = packed
            ? CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue)
            : CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        guard let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: stride,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: info, provider: provider,
                                  decode: nil, shouldInterpolate: true, intent: .defaultIntent)
        else { throw Problem.invalid }
        return VideoFrame(image: image, width: width, height: height, sequence: sequence)
    }

    /// A test picture in the engine's format (for previews, screenshots and tests).
    public static func sample(width: Int = 640, height: Int = 400, sequence: UInt64 = 1, draw: ((CGContext) -> Void)? = nil) -> Data {
        var header = Data("MRF2".utf8)
        func append(_ value: UInt32) { withUnsafeBytes(of: value.littleEndian) { header.append(contentsOf: $0) } }
        func append64(_ value: UInt64) { withUnsafeBytes(of: value.littleEndian) { header.append(contentsOf: $0) } }
        append(UInt32(width)); append(UInt32(height)); append(UInt32(width * 4)); append64(sequence); append64(sequence * 3000)
        var pixels = Data(count: width * height * 4)
        pixels.withUnsafeMutableBytes { raw in
            guard let context = CGContext(data: raw.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                          space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
            else { return }
            if let draw { draw(context) } else { Self.desktop(context, width: width, height: height) }
        }
        return header + pixels
    }

    /// A calm stand-in desktop: wallpaper, two windows and a dock.
    static func desktop(_ context: CGContext, width: Int, height: Int) {
        let w = CGFloat(width), h = CGFloat(height)
        let colors = [CGColor(red: 0.16, green: 0.27, blue: 0.45, alpha: 1), CGColor(red: 0.45, green: 0.22, blue: 0.40, alpha: 1)] as CFArray
        if let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: colors, locations: [0, 1]) {
            context.drawLinearGradient(gradient, start: .zero, end: CGPoint(x: w, y: h), options: [])
        }
        context.setFillColor(CGColor(red: 0.97, green: 0.97, blue: 0.98, alpha: 1))
        context.fill(CGRect(x: w * 0.08, y: h * 0.30, width: w * 0.50, height: h * 0.55))
        context.setFillColor(CGColor(red: 0.90, green: 0.91, blue: 0.93, alpha: 1))
        context.fill(CGRect(x: w * 0.08, y: h * 0.80, width: w * 0.50, height: h * 0.05))
        context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        context.fill(CGRect(x: w * 0.48, y: h * 0.16, width: w * 0.42, height: h * 0.46))
        context.setFillColor(CGColor(red: 0.73, green: 0.13, blue: 0.20, alpha: 1))
        context.fill(CGRect(x: w * 0.52, y: h * 0.22, width: w * 0.16, height: h * 0.05))
        context.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 0.25))
        context.fill(CGRect(x: w * 0.30, y: h * 0.02, width: w * 0.40, height: h * 0.07))
    }
}
