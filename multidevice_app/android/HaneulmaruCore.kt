package io.haneulmaru.core

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.sqrt

// Integration library source, not an APK. CameraX/BLE lifecycle and ClikaRT
// native binding are supplied by the Android app that embeds these contracts.
data class CameraFrame(
    val sequence: Long,
    val timestampMs: Double,
    val width: Int,
    val height: Int,
    val rgb: ByteArray,
    val timestampBasis: String = "host_decode_time"
) {
    init {
        require(sequence >= 0 && timestampMs.isFinite() && timestampMs >= 0)
        require(width > 0 && height > 0 && width.toLong() * height * 3 == rgb.size.toLong())
    }
    // Caller rotates to upright, unmirrored RGB before constructing this DTO.
}

data class Detection(val code: String, val confidence: Double, val xyxy: List<Double>, val id: Long? = null) {
    fun validate(frame: CameraFrame) {
        require(code in listOf("R", "B", "G", "UNKNOWN"))
        require(confidence.isFinite() && confidence in 0.0..1.0)
        require(xyxy.size == 4 && xyxy.all { it.isFinite() })
        require(xyxy[0] >= 0 && xyxy[0] < xyxy[2] && xyxy[2] <= frame.width)
        require(xyxy[1] >= 0 && xyxy[1] < xyxy[3] && xyxy[3] <= frame.height)
    }
    fun packet() = mapOf("code" to code, "confidence" to confidence, "xyxy" to xyxy, "id" to id)
}

data class EngineStatus(val name: String, val ready: Boolean, val reason: String?)

interface InferenceEngine : AutoCloseable {
    val status: EngineStatus
    fun detect(frame: CameraFrame): List<Detection>
}

class DisabledEngine : InferenceEngine {
    override val status = EngineStatus("disabled", false, "인식 엔진 미연결")
    override fun detect(frame: CameraFrame) = emptyList<Detection>()
    override fun close() {}
}

// A real ClikaRT SDK adapter must implement this. No fake SDK types or
// default detections are supplied, and failure does not switch engines.
interface ClikaRtBinding : AutoCloseable {
    fun detectRgb(frame: CameraFrame): List<Detection>
}

class ClikaRtEngine(private val binding: ClikaRtBinding) : InferenceEngine {
    override val status = EngineStatus("clikart", true, null)
    override fun detect(frame: CameraFrame): List<Detection> = binding.detectRgb(frame)
    override fun close() = binding.close()
}

data class ImuSample(val boardTMs: Long, val gyroDps: List<Double>, val accelerationG: List<Double>) {
    init {
        require(boardTMs in 0..0xffffffffL)
        require(gyroDps.size == 3 && gyroDps.all { it.isFinite() })
        require(accelerationG.size == 3 && accelerationG.all { it.isFinite() })
    }
}

object Nu40Decoder {
    const val NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
    // Current firmware binary packet: <BI6h, 17 bytes.
    fun binary(data: ByteArray): ImuSample {
        require(data.size == 17 && data[0].toInt() == 1)
        val b = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        b.get()
        val stamp = b.int.toLong() and 0xffffffffL
        val accel = List(3) { b.short.toDouble() / 10000.0 }
        val gyro = List(3) { b.short.toDouble() / 10.0 }
        return ImuSample(stamp, gyro, accel)
    }
}

data class ImuSnapshot(val sample: ImuSample?, val source: String?, val ageMs: Double?)

class ImuSelector {
    private val latest = mutableMapOf<String, Pair<ImuSample, Double>>()
    @Synchronized fun receive(source: String, sample: ImuSample, hostMonotonicMs: Double) {
        require(source == "USB" || source == "BLE")
        require(hostMonotonicMs.isFinite() && hostMonotonicMs >= 0)
        latest[source] = sample to hostMonotonicMs
    }
    @Synchronized fun disconnected(source: String) { latest.remove(source) }
    @Synchronized fun snapshot(nowMs: Double): ImuSnapshot {
        for (source in listOf("USB", "BLE")) {
            val entry = latest[source] ?: continue
            val age = nowMs - entry.second
            if (age in 0.0..350.0) return ImuSnapshot(entry.first, source, age)
        }
        return ImuSnapshot(null, null, null)
    }
    // The selector does not disconnect BLE when a USB sample arrives.
}

class DirectionClassifier(vectors: Map<String, List<Double>>, private val threshold: Double,
                          private val similarity: Double) {
    private val trained = listOf("up", "down", "left", "right").associateWith { key ->
        val v = vectors.getValue(key)
        require(v.size == 3 && v.all { it.isFinite() })
        val rate = sqrt(v.sumOf { it * it })
        require(rate.isFinite() && rate > 0)
        v.map { it / rate }
    }
    init { require(threshold.isFinite() && threshold > 0 && similarity in 0.0..1.0) }
    fun classify(state: ImuSnapshot): Map<String, Any?> {
        val sample = state.sample
        val age = state.ageMs
        if (sample == null || age == null || age !in 0.0..350.0)
            return mapOf("valid" to false, "direction" to null, "reason" to "imu_unavailable_or_stale")
        val gyro = sample.gyroDps
        require(gyro.size == 3 && gyro.all { it.isFinite() })
        val rate = sqrt(gyro.sumOf { it * it })
        var direction: String? = null
        var score: Double? = null
        if (rate >= threshold) {
            val ranked = trained.map { (key, v) -> key to v.indices.sumOf { v[it] * gyro[it] / rate } }
                .sortedByDescending { it.second }
            if (ranked[0].second >= similarity && ranked[0].second - ranked[1].second >= 0.08) {
                direction = ranked[0].first
                score = ranked[0].second
            }
        }
        return mapOf("valid" to true, "direction" to direction, "similarity" to score,
            "rate_dps" to rate, "source" to state.source, "age_ms" to age, "board_t_ms" to sample.boardTMs)
    }
}

interface TrackingEngine {
    fun update(frame: CameraFrame, detections: List<Detection>): List<Detection>
    fun reset()
}

class NoTracking : TrackingEngine {
    override fun update(frame: CameraFrame, detections: List<Detection>) = detections
    override fun reset() {}
}

class Pipeline(private val engine: InferenceEngine, private val tracker: TrackingEngine,
               private val direction: DirectionClassifier, private val colorConfidence: Double = 0.4) {
    private val busy = AtomicBoolean(false)
    private var previousSequence: Long? = null
    private var previousTimestamp: Double? = null
    private var session = java.util.UUID.randomUUID().toString()
    init { require(colorConfidence in 0.0..1.0) }
    // Invoke on a worker thread. null means busy: drop the frame, don't queue it.
    fun process(frame: CameraFrame, imu: ImuSnapshot): Map<String, Any?>? {
        if (!busy.compareAndSet(false, true)) return null
        try {
            previousSequence?.let {
                require(frame.sequence > it)
                val previousTime = requireNotNull(previousTimestamp)
                if (frame.timestampMs < previousTime || frame.timestampMs - previousTime > 1000) {
                    tracker.reset()
                    session = java.util.UUID.randomUUID().toString()
                }
            }
            // Retain just identity/time, not a full previous camera frame.
            val imuResult = direction.classify(imu)
            val start = System.nanoTime()
            val detections = engine.detect(frame)
            val inferenceMs = (System.nanoTime() - start) / 1e6
            detections.forEach { it.validate(frame) }
            val tracks = tracker.update(frame, detections).map {
                it.validate(frame)
                it.packet() + mapOf("raw_code" to it.code,
                    "code" to if (it.confidence >= colorConfidence) it.code else "UNKNOWN")
            }
            previousSequence = frame.sequence
            previousTimestamp = frame.timestampMs
            return mapOf("schema_version" to 1, "app" to "haneulmaru", "session_id" to session,
                "frame_sequence" to frame.sequence, "frame_timestamp_ms" to frame.timestampMs,
                "timestamp_basis" to frame.timestampBasis, "image_size_px" to listOf(frame.width, frame.height),
                "orientation" to "upright_unmirrored", "imu" to imuResult, "tracks" to tracks,
                "engine" to mapOf("name" to engine.status.name, "ready" to engine.status.ready,
                    "reason" to engine.status.reason, "executed" to engine.status.ready, "inference_ms" to inferenceMs),
                "processing_ms" to (System.nanoTime() - start) / 1e6, "execution_device" to "android",
                "imu_alignment" to "host_arrival_approximate", "range_m" to null)
        } finally { busy.set(false) }
    }
}
