using System.Buffers.Binary;

namespace MixelRemote.Support;

/// <summary>
/// A session picture from the engine. "MRF2": a 32-byte header (magic, width, height, stride, sequence,
/// timestamp; little endian) followed by RGBA rows. "MRF1": the same with BGRA rows. "MRH1" is H.264,
/// which this app does not ask for (it reads decoded pictures).
/// </summary>
public sealed class VideoFrame
{
    public const int HeaderSize = 32;

    private VideoFrame(byte[] data, int width, int height, int stride, ulong sequence, bool rgba)
    {
        Data = data;
        Width = width;
        Height = height;
        Stride = stride;
        Sequence = sequence;
        Rgba = rgba;
    }

    /// <summary>The whole packet; pixels start at <see cref="HeaderSize"/>.</summary>
    public byte[] Data { get; }
    public int Width { get; }
    public int Height { get; }
    public int Stride { get; }
    public ulong Sequence { get; }
    /// <summary>true: RGBA rows (MRF2); false: BGRA rows (MRF1).</summary>
    public bool Rgba { get; }

    public sealed class InvalidFrame : Exception
    {
        public InvalidFrame() : base("Invalid native video frame") { }
        public InvalidFrame(string message) : base(message) { }
        public InvalidFrame(string message, Exception inner) : base(message, inner) { }
    }

    public static bool IsCompressed(byte[] data) => data.Length >= 4 && data[0] == 77 && data[1] == 82 && data[2] == 72 && data[3] == 49;

    /// <summary>Checks the header and bounds exactly as the desktop app does (at most 1920 × 1088, or upright).</summary>
    public static VideoFrame Decode(byte[] data, ulong after = 0)
    {
        if (data.Length <= HeaderSize || data[0] != 77 || data[1] != 82 || data[2] != 70 || (data[3] != 50 && data[3] != 49)) throw new InvalidFrame();
        var span = data.AsSpan();
        long width = BinaryPrimitives.ReadUInt32LittleEndian(span[4..]);
        long height = BinaryPrimitives.ReadUInt32LittleEndian(span[8..]);
        long stride = BinaryPrimitives.ReadUInt32LittleEndian(span[12..]);
        var sequence = BinaryPrimitives.ReadUInt64LittleEndian(span[16..]);
        if (width < 2 || height < 2 || Math.Max(width, height) > 1920 || Math.Min(width, height) > 1088
            || stride < width * 4 || data.Length != HeaderSize + stride * height || sequence <= after)
        {
            throw new InvalidFrame();
        }
        return new VideoFrame(data, (int)width, (int)height, (int)stride, sequence, data[3] == 50);
    }

    /// <summary>The pixels as tightly packed BGRA rows (premultiplied, opaque), for Windows bitmaps.</summary>
    public byte[] ToBgra()
    {
        var output = new byte[Width * Height * 4];
        for (var row = 0; row < Height; row++)
        {
            var source = HeaderSize + row * Stride;
            var target = row * Width * 4;
            if (!Rgba)
            {
                Buffer.BlockCopy(Data, source, output, target, Width * 4);
                for (var x = 0; x < Width; x++) output[target + x * 4 + 3] = 255;
                continue;
            }
            for (var x = 0; x < Width; x++)
            {
                var s = source + x * 4;
                var t = target + x * 4;
                output[t] = Data[s + 2];
                output[t + 1] = Data[s + 1];
                output[t + 2] = Data[s];
                output[t + 3] = 255;
            }
        }
        return output;
    }

    /// <summary>The pixels as tightly packed RGBA rows (for Direct3D R8G8B8A8 bitmaps).</summary>
    public byte[] ToRgba()
    {
        if (Rgba && Stride == Width * 4)
        {
            var copy = new byte[Width * Height * 4];
            Buffer.BlockCopy(Data, HeaderSize, copy, 0, copy.Length);
            for (var index = 3; index < copy.Length; index += 4) copy[index] = 255;
            return copy;
        }
        var output = new byte[Width * Height * 4];
        for (var row = 0; row < Height; row++)
        {
            for (var x = 0; x < Width; x++)
            {
                var s = HeaderSize + row * Stride + x * 4;
                var t = (row * Width + x) * 4;
                output[t] = Data[s + (Rgba ? 0 : 2)];
                output[t + 1] = Data[s + 1];
                output[t + 2] = Data[s + (Rgba ? 2 : 0)];
                output[t + 3] = 255;
            }
        }
        return output;
    }

    /// <summary>A test picture in the engine's format: a calm stand-in desktop (wallpaper, two windows, a taskbar).</summary>
    public static byte[] Sample(int width = 640, int height = 400, ulong sequence = 1)
    {
        var data = new byte[HeaderSize + width * height * 4];
        "MRF2"u8.CopyTo(data);
        BinaryPrimitives.WriteUInt32LittleEndian(data.AsSpan(4), (uint)width);
        BinaryPrimitives.WriteUInt32LittleEndian(data.AsSpan(8), (uint)height);
        BinaryPrimitives.WriteUInt32LittleEndian(data.AsSpan(12), (uint)(width * 4));
        BinaryPrimitives.WriteUInt64LittleEndian(data.AsSpan(16), sequence);
        BinaryPrimitives.WriteUInt64LittleEndian(data.AsSpan(24), sequence * 3000);
        for (var y = 0; y < height; y++)
        {
            for (var x = 0; x < width; x++)
            {
                var t = (double)(x + y) / (width + height);
                var (r, g, b) = ((byte)(41 + t * 74), (byte)(69 - t * 13), (byte)(115 - t * 13));
                double fx = (double)x / width, fy = (double)y / height;
                if (fx is >= 0.08 and < 0.58 && fy is >= 0.15 and < 0.70) (r, g, b) = fy < 0.20 ? ((byte)230, (byte)232, (byte)237) : ((byte)247, (byte)247, (byte)250);
                if (fx is >= 0.48 and < 0.90 && fy is >= 0.38 and < 0.84) (r, g, b) = fy < 0.43 ? ((byte)226, (byte)228, (byte)233) : ((byte)255, (byte)255, (byte)255);
                if (fx is >= 0.52 and < 0.68 && fy is >= 0.73 and < 0.78) (r, g, b) = (186, 32, 52);
                if (fy >= 0.93) (r, g, b) = (32, 32, 36);
                var index = HeaderSize + (y * width + x) * 4;
                data[index] = r;
                data[index + 1] = g;
                data[index + 2] = b;
                data[index + 3] = 255;
            }
        }
        return data;
    }
}
