package ch.mixel.quicksupport

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import java.nio.ByteBuffer

/**
 * Captures the screen while a support session runs and hands the encoded pictures to the host core.
 * It runs as a visible foreground service, as Android requires for screen capture.
 */
class ShareService : Service() {
    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var encoder: MediaCodec? = null
    private var thread: HandlerThread? = null
    /** Parameter sets of the encoder; the viewer needs them in front of every complete picture. */
    private var header: ByteArray? = null
    private var combined: ByteBuffer = ByteBuffer.allocateDirect(1 shl 20)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val permission: Intent? = if (Build.VERSION.SDK_INT >= 33) intent?.getParcelableExtra(EXTRA_PERMISSION, Intent::class.java) else @Suppress("DEPRECATION") intent?.getParcelableExtra(EXTRA_PERMISSION)
        if (permission == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        notifyForeground()
        try {
            capture(permission)
            Support.keyframe = { requestKeyframe() }
            Support.sessionEnded = { stopSelf() }
            // Only now does the technician get the answer: the picture is ready to flow.
            Support.decide(true)
        } catch (error: Exception) {
            Support.decide(false)
            stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun notifyForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.notification_channel), NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val notification: Notification = Notification.Builder(this, CHANNEL)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notification_text))
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setOngoing(true)
            .setContentIntent(open)
            .build()
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION) else startForeground(1, notification)
    }

    private fun capture(permission: Intent) {
        val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val projection = manager.getMediaProjection(Activity.RESULT_OK, permission) ?: throw IllegalStateException("no projection")
        this.projection = projection
        val thread = HandlerThread("mixel-share").also { it.start() }
        this.thread = thread
        val handler = Handler(thread.looper)
        projection.registerCallback(object : MediaProjection.Callback() {
            // The user stopped the capture from the system (status bar chip): end the session too.
            override fun onStop() { Support.endSession(); stopSelf() }
        }, handler)

        val (width, height) = Support.width to Support.height
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, 2_500_000)
            setInteger(MediaFormat.KEY_FRAME_RATE, 30)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 10)
            // The viewer expects the simplest H.264 form (baseline): no reordering of pictures.
            setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline)
            setInteger(MediaFormat.KEY_LEVEL, MediaCodecInfo.CodecProfileLevel.AVCLevel4)
            // A still screen produces no pictures; repeat the last one once so the first picture always arrives.
            setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 500_000L)
            if (Build.VERSION.SDK_INT >= 29) setFloat(MediaFormat.KEY_MAX_FPS_TO_ENCODER, 20f)
        }
        val encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        encoder.setCallback(object : MediaCodec.Callback() {
            override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {}
            override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) {}
            override fun onError(codec: MediaCodec, error: MediaCodec.CodecException) { Support.endSession(); stopSelf() }
            override fun onOutputBufferAvailable(codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
                try {
                    val buffer = codec.getOutputBuffer(index)
                    if (buffer != null && info.size > 0) deliver(buffer, info)
                    codec.releaseOutputBuffer(index, false)
                } catch (_: IllegalStateException) {
                    // The encoder was released while a picture was on its way.
                }
            }
        }, handler)
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        val surface = encoder.createInputSurface()
        encoder.start()
        this.encoder = encoder
        display = projection.createVirtualDisplay("mixel-quick-support", width, height, resources.displayMetrics.densityDpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, surface, null, handler)
    }

    private fun deliver(buffer: ByteBuffer, info: MediaCodec.BufferInfo) {
        if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
            header = ByteArray(info.size).also { buffer.position(info.offset); buffer.get(it) }
            return
        }
        val key = info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0
        val head = header
        if (key && head != null) {
            val total = head.size + info.size
            if (combined.capacity() < total) combined = ByteBuffer.allocateDirect(total * 2)
            combined.clear()
            combined.put(head)
            buffer.position(info.offset).limit(info.offset + info.size)
            combined.put(buffer)
            Support.push(combined, 0, total)
        } else {
            Support.push(buffer, info.offset, info.size)
        }
    }

    private fun requestKeyframe() {
        try {
            encoder?.setParameters(Bundle().apply { putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0) })
        } catch (_: IllegalStateException) {
        }
    }

    override fun onDestroy() {
        Support.keyframe = null
        Support.sessionEnded = null
        try { display?.release() } catch (_: Exception) {}
        try { encoder?.stop() } catch (_: Exception) {}
        try { encoder?.release() } catch (_: Exception) {}
        try { projection?.stop() } catch (_: Exception) {}
        thread?.quitSafely()
        display = null; encoder = null; projection = null; thread = null
        super.onDestroy()
    }

    companion object {
        const val EXTRA_PERMISSION = "permission"
        private const val CHANNEL = "session"
    }
}
