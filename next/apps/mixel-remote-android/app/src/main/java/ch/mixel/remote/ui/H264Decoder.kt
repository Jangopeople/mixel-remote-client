package ch.mixel.remote.ui

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.view.Surface
import ch.mixel.remote.app.HardwareDecoder
import ch.mixel.remote.app.VideoPacket

/**
 * The phone's own H.264 decoder (MediaCodec in asynchronous mode), drawing straight onto the
 * session's surface. Pictures are shown as soon as the decoder has them (a still remote screen sends
 * few pictures, so nothing waits for the next one). A failure makes [decode] answer `false`, and the
 * session asks the engine to decode instead.
 */
class H264Decoder(private val surface: Surface) : HardwareDecoder {
    private val thread = HandlerThread("mixel-h264").apply { start() }
    private val handler = Handler(thread.looper)
    private var codec: MediaCodec? = null
    private val packets = ArrayDeque<VideoPacket.Compressed>()
    private val inputs = ArrayDeque<Int>()
    @Volatile private var failed = false
    @Volatile private var closed = false
    @Volatile override var pictureWidth = 0
        private set
    @Volatile override var pictureHeight = 0
        private set

    override fun decode(packet: VideoPacket.Compressed): Boolean {
        if (failed || closed) return false
        handler.post {
            try {
                if (codec == null) {
                    if (!packet.keyframe) return@post
                    start()
                }
                // A decoder that falls far behind would only add delay: let the engine decode instead.
                if (packets.size > 90) throw IllegalStateException("decoder behind")
                packets.addLast(packet)
                feed()
            } catch (_: Exception) {
                fail()
            }
        }
        return true
    }

    private fun start() {
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, 1920, 1088)
        if (Build.VERSION.SDK_INT >= 30) format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
        format.setInteger(MediaFormat.KEY_PRIORITY, 0)
        val decoder = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        decoder.setCallback(object : MediaCodec.Callback() {
            override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {
                inputs.addLast(index)
                runCatching { feed() }.onFailure { fail() }
            }

            override fun onOutputBufferAvailable(codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
                runCatching { codec.releaseOutputBuffer(index, true) }
            }

            override fun onError(codec: MediaCodec, error: MediaCodec.CodecException) = fail()

            override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) = size(format)
        }, handler)
        decoder.configure(format, surface, null, 0)
        decoder.start()
        codec = decoder
    }

    private fun feed() {
        val decoder = codec ?: return
        while (packets.isNotEmpty() && inputs.isNotEmpty()) {
            val packet = packets.removeFirst()
            val index = inputs.removeFirst()
            val buffer = decoder.getInputBuffer(index) ?: continue
            buffer.clear()
            if (packet.annexB.size > buffer.capacity()) throw IllegalStateException("picture too large")
            buffer.put(packet.annexB)
            decoder.queueInputBuffer(index, 0, packet.annexB.size, packet.timestampMicros, if (packet.keyframe) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
        }
    }

    private fun size(format: MediaFormat) {
        val hasCrop = format.containsKey("crop-right") && format.containsKey("crop-left") && format.containsKey("crop-bottom") && format.containsKey("crop-top")
        pictureWidth = if (hasCrop) format.getInteger("crop-right") - format.getInteger("crop-left") + 1 else format.getInteger(MediaFormat.KEY_WIDTH)
        pictureHeight = if (hasCrop) format.getInteger("crop-bottom") - format.getInteger("crop-top") + 1 else format.getInteger(MediaFormat.KEY_HEIGHT)
    }

    private fun fail() {
        failed = true
        release()
    }

    override fun reset() {
        handler.post {
            release()
            failed = false
            pictureWidth = 0
            pictureHeight = 0
        }
    }

    private fun release() {
        runCatching { codec?.stop() }
        runCatching { codec?.release() }
        codec = null
        packets.clear()
        inputs.clear()
    }

    /** The surface goes away: stop for good. */
    fun close() {
        closed = true
        handler.post { release(); thread.quitSafely() }
    }
}
