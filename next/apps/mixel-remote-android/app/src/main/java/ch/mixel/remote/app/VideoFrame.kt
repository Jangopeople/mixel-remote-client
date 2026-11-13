package ch.mixel.remote.app

/**
 * A session picture from the engine. "MRF2": a 32-byte header (magic, width, height, stride,
 * sequence, timestamp; little endian) followed by RGBA rows. "MRH1": the same header size (magic,
 * flags with 1 = complete picture, two unused fields, sequence, timestamp) followed by one H.264
 * access unit (Annex B), for the phone's own hardware decoder.
 */
sealed class VideoPacket {
    abstract val sequence: Long

    /** Tightly packed RGBA rows (`width * 4` bytes each), ready to draw. */
    class Picture(val width: Int, val height: Int, val pixels: ByteArray, override val sequence: Long) : VideoPacket()

    /** One H.264 access unit; `keyframe` = a complete picture the decoder can start with. */
    class Compressed(val keyframe: Boolean, val timestampMicros: Long, val annexB: ByteArray, override val sequence: Long) : VideoPacket()

    class Invalid(message: String) : Exception(message)

    companion object {
        private fun u32(bytes: ByteArray, at: Int): Long =
            (bytes[at].toLong() and 0xFF) or ((bytes[at + 1].toLong() and 0xFF) shl 8) or
                ((bytes[at + 2].toLong() and 0xFF) shl 16) or ((bytes[at + 3].toLong() and 0xFF) shl 24)

        private fun u64(bytes: ByteArray, at: Int): Long = (0 until 8).fold(0L) { value, index -> value or ((bytes[at + index].toLong() and 0xFF) shl (8 * index)) }

        /** Checks the header and bounds exactly as the desktop app does (at most 1920 × 1088, or upright). */
        fun decode(data: ByteArray, after: Long = 0): VideoPacket {
            if (data.size <= 32 || data[0] != 'M'.code.toByte() || data[1] != 'R'.code.toByte()) throw Invalid("Invalid native video frame")
            val sequence = u64(data, 16)
            if (sequence <= after) throw Invalid("Invalid native video frame")
            if (data[2] == 'H'.code.toByte() && data[3] == '1'.code.toByte()) {
                if (data.size > 32 + 4 * 1024 * 1024) throw Invalid("Invalid native video frame")
                return Compressed((u32(data, 4) and 1L) == 1L, u64(data, 24), data.copyOfRange(32, data.size), sequence)
            }
            if (data[2] != 'F'.code.toByte() || data[3] != '2'.code.toByte()) throw Invalid("Invalid native video frame")
            val width = u32(data, 4).toInt()
            val height = u32(data, 8).toInt()
            val stride = u32(data, 12).toInt()
            if (width < 2 || height < 2 || maxOf(width, height) > 1920 || minOf(width, height) > 1088 ||
                stride != width * 4 || data.size.toLong() != 32L + stride.toLong() * height
            ) throw Invalid("Invalid native video frame")
            return Picture(width, height, data.copyOfRange(32, data.size), sequence)
        }

        /** A test picture in the engine's format (screenshots, tests): a calm stand-in desktop. */
        fun sample(width: Int = 640, height: Int = 400, sequence: Long = 1): ByteArray {
            val out = ByteArray(32 + width * height * 4)
            fun put32(at: Int, value: Long) { for (index in 0 until 4) out[at + index] = (value shr (8 * index)).toByte() }
            fun put64(at: Int, value: Long) { for (index in 0 until 8) out[at + index] = (value shr (8 * index)).toByte() }
            "MRF2".forEachIndexed { index, char -> out[index] = char.code.toByte() }
            put32(4, width.toLong()); put32(8, height.toLong()); put32(12, width * 4L); put64(16, sequence); put64(24, sequence * 3000)
            for (y in 0 until height) for (x in 0 until width) {
                val fx = x.toFloat() / width; val fy = y.toFloat() / height
                var r = (0.16f + 0.29f * fx) * 255; var g = (0.27f - 0.05f * fy) * 255; var b = (0.45f - 0.05f * fx) * 255
                val window1 = fx in 0.08f..0.58f && fy in 0.15f..0.70f
                val window2 = fx in 0.48f..0.90f && fy in 0.38f..0.84f
                val title = fx in 0.08f..0.58f && fy in 0.15f..0.20f
                val button = fx in 0.52f..0.68f && fy in 0.73f..0.78f
                val dock = fx in 0.30f..0.70f && fy in 0.91f..0.98f
                when {
                    button -> { r = 186f; g = 32f; b = 52f }
                    window2 -> { r = 255f; g = 255f; b = 255f }
                    title -> { r = 230f; g = 232f; b = 237f }
                    window1 -> { r = 247f; g = 247f; b = 250f }
                    dock -> { r *= 0.75f; g *= 0.75f; b *= 0.75f }
                }
                val at = 32 + (y * width + x) * 4
                out[at] = r.toInt().toByte(); out[at + 1] = g.toInt().toByte(); out[at + 2] = b.toInt().toByte(); out[at + 3] = 0xFF.toByte()
            }
            return out
        }
    }
}
