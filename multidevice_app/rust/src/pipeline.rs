//! A single binary frame request performs detection, tracking and packet construction.
use crate::{
    tracking::{Frame, Observation, Tracker},
    vision::{frame_bytes, Detector, DetectorSpec},
    Result,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{self, BufRead, Read, Write},
    time::Instant,
};

#[derive(Deserialize)]
struct Settings {
    #[serde(flatten)]
    detector: DetectorSpec,
    color_confidence: f64,
    max_gap_ms: f64,
    session_prefix: String,
    class_change_policy: String,
    display_names: HashMap<String, String>,
    model_verification: Value,
    include_detections: bool,
}
#[derive(Deserialize)]
struct Header {
    sequence: u64,
    timestamp_ms: f64,
    width: usize,
    height: usize,
    timestamp_basis: String,
    imu: Value,
}
#[derive(Serialize)]
struct TrackPacket<'a> {
    id: Option<u64>,
    code: &'a str,
    raw_code: &'a str,
    display_name: &'a str,
    xyxy: [f32; 4],
    confidence: f32,
}
#[derive(Serialize)]
struct Packet<'a> {
    #[serde(flatten)]
    metadata: Value,
    tracks: Vec<TrackPacket<'a>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    _detections: Option<&'a Vec<crate::vision::Detection<'a>>>,
}
fn displayed_confidence(value: f32) -> f64 {
    // Preserve the previous detect JSON -> Python threshold boundary exactly.
    value
        .to_string()
        .parse()
        .expect("finite detector confidence")
}
struct State {
    tracker: Tracker,
    strict: bool,
    previous: Option<(u64, f64, usize, usize)>,
    prefix: String,
    epoch: u64,
    max_gap_ms: f64,
}
impl State {
    fn validate(&self, h: &Header) -> Result<()> {
        frame_bytes(h.width, h.height)?;
        if !h.timestamp_ms.is_finite()
            || h.timestamp_ms < 0.
            || self.previous.is_some_and(|p| h.sequence <= p.0)
        {
            return Err("Invalid or unordered pipeline frame".into());
        }
        Ok(())
    }
    fn update(&mut self, h: &Header, detections: Vec<Observation>) -> Result<Vec<Option<u64>>> {
        self.validate(h)?;
        if self.previous.is_some_and(|p| {
            h.timestamp_ms < p.1
                || h.timestamp_ms - p.1 > self.max_gap_ms
                || h.width != p.2
                || h.height != p.3
        }) {
            self.tracker = Tracker::new(self.strict);
            self.epoch += 1;
        }
        let ids = self.tracker.update(&Frame {
            timestamp_ms: h.timestamp_ms,
            detections,
        })?;
        self.previous = Some((h.sequence, h.timestamp_ms, h.width, h.height));
        Ok(ids)
    }
    fn session(&self) -> String {
        format!("{}:{}", self.prefix, self.epoch)
    }
}
fn line(input: &mut impl BufRead) -> Result<Option<String>> {
    let mut bytes = Vec::new();
    input.take(65537).read_until(b'\n', &mut bytes)?;
    if bytes.is_empty() {
        return Ok(None);
    }
    if bytes.len() > 65536 || !bytes.ends_with(b"\n") {
        return Err("Invalid pipeline header".into());
    }
    Ok(Some(String::from_utf8(bytes)?))
}
pub fn worker(args: &[String]) -> Result<()> {
    let mut input = io::stdin().lock();
    let cfg: Settings =
        serde_json::from_str(&line(&mut input)?.ok_or("Missing pipeline settings")?)?;
    if !(0.0..=1.).contains(&cfg.color_confidence)
        || !cfg.max_gap_ms.is_finite()
        || cfg.max_gap_ms <= 0.
        || cfg.session_prefix.is_empty()
        || !["allow", "strict"].contains(&cfg.class_change_policy.as_str())
    {
        return Err("Invalid pipeline settings".into());
    }
    let detector = Detector::load(args, cfg.detector)?;
    let strict = cfg.class_change_policy == "strict";
    let mut state = State {
        tracker: Tracker::new(strict),
        strict,
        previous: None,
        prefix: cfg.session_prefix,
        epoch: 0,
        max_gap_ms: cfg.max_gap_ms,
    };
    let mut out = io::stdout().lock();
    writeln!(out, "{{\"ready\":true,\"pipeline_protocol\":1}}")?;
    out.flush()?;
    let mut bgr = Vec::new();
    while let Some(request) = line(&mut input)? {
        let h: Header = serde_json::from_str(&request)?;
        state.validate(&h)?;
        bgr.resize(frame_bytes(h.width, h.height)?, 0);
        input.read_exact(&mut bgr)?;
        let start = Instant::now();
        let detections = detector.detect(h.width, h.height, &bgr)?;
        let inference_ms = start.elapsed().as_secs_f64() * 1000.;
        let ids = state.update(
            &h,
            detections
                .iter()
                .map(|d| Observation {
                    code: d.code.into(),
                    confidence: d.confidence,
                    xyxy: d.xyxy,
                })
                .collect(),
        )?;
        let tracks: Vec<_> = detections
            .iter()
            .zip(ids)
            .map(|(d, id)| {
                let code = if displayed_confidence(d.confidence) < cfg.color_confidence {
                    "UNKNOWN"
                } else {
                    d.code
                };
                TrackPacket {
                    id,
                    code,
                    raw_code: d.code,
                    xyxy: d.xyxy,
                    confidence: d.confidence,
                    display_name: cfg
                        .display_names
                        .get(code)
                        .map(String::as_str)
                        .unwrap_or(code),
                }
            })
            .collect();
        let metadata = json!({"schema_version":1,"app":"haneulmaru","session_id":state.session(),
            "frame_sequence":h.sequence,"frame_timestamp_ms":h.timestamp_ms,"timestamp_basis":h.timestamp_basis,
            "image_size_px":[h.width,h.height],"orientation":"upright_unmirrored","imu":h.imu,
            "engine":{"name":"rust","ready":true,"reason":null,"executed":true,
                "model_verification":cfg.model_verification,"inference_ms":inference_ms},
            "processing_ms":start.elapsed().as_secs_f64()*1000.,"execution_device":"host","range_m":null,
            "pipeline_backend":"rust"});
        let packet = Packet {
            metadata,
            tracks,
            _detections: cfg.include_detections.then_some(&detections),
        };
        serde_json::to_writer(&mut out, &packet)?;
        writeln!(out)?;
        out.flush()?;
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_python_decimal_confidence_boundary() {
        assert_eq!(displayed_confidence(0.9f32), 0.9f64);
        assert!(f64::from(0.9f32) < 0.9f64);
    }
    #[test]
    fn resets_sessions_on_gap_time_reversal_or_size_change() {
        let mut state = State {
            tracker: Tracker::new(false),
            strict: false,
            previous: None,
            prefix: "test".into(),
            epoch: 0,
            max_gap_ms: 1000.,
        };
        for (i, t, w, epoch) in [
            (1, 0., 20, 0),
            (2, 10., 20, 0),
            (3, 1011., 20, 1),
            (4, 0., 20, 2),
            (5, 1., 21, 3),
        ] {
            let h = Header {
                sequence: i,
                timestamp_ms: t,
                width: w,
                height: 20,
                timestamp_basis: "test".into(),
                imu: Value::Null,
            };
            state.update(&h, vec![]).unwrap();
            assert_eq!(state.epoch, epoch);
            assert!(state.validate(&h).is_err());
        }
    }
}
