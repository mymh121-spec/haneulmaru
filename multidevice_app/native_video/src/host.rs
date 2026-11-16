use crate::{
    config::{self, Config},
    decoder,
    imu::LiveImu,
    ipc::{self, Frame, Kind, Response, Worker},
    Args,
};
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::{
    fs::OpenOptions,
    io::{self, BufWriter, Write},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub struct Budget {
    maximum: u32,
    pub used: u32,
}
impl Budget {
    pub fn new(maximum: u32) -> Self {
        Self { maximum, used: 0 }
    }
    pub fn retry(&mut self, cancel: &Arc<AtomicBool>) -> bool {
        if self.used >= self.maximum || cancel.load(Ordering::Relaxed) {
            return false;
        }
        self.used += 1;
        sleep_until(
            Instant::now() + Duration::from_millis((100u64 << (self.used - 1)).min(5000)),
            cancel,
        );
        !cancel.load(Ordering::Relaxed)
    }
}
fn sleep_until(target: Instant, cancel: &Arc<AtomicBool>) {
    while !cancel.load(Ordering::Relaxed) {
        let remaining = target.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            break;
        }
        thread::sleep(remaining.min(Duration::from_millis(20)));
    }
}
fn source(cfg: &Config, timeout: Duration, cancel: &Arc<AtomicBool>) -> Result<Worker> {
    let mut command = Command::new(std::env::current_exe()?);
    // OpenCV errors can echo URL credentials. Keep the decoder's stderr private.
    command.arg("--decode-worker").stderr(Stdio::null());
    let mut worker = Worker::spawn(&mut command)?;
    let options = decoder::Config {
        source: cfg.source.clone(),
        rotation: cfg.rotation,
        recording: !cfg.live,
    };
    let ready = worker.json(vec![ipc::json_line(&options)?], timeout, cancel)?;
    anyhow::ensure!(
        ready == json!({"ready":true,"native_video_protocol":1}),
        "decoder protocol mismatch"
    );
    Ok(worker)
}
fn pipeline(
    cfg: &Config,
    prefix: &str,
    generation: u64,
    cancel: &Arc<AtomicBool>,
) -> Result<Worker> {
    let mut command = Command::new(&cfg.worker);
    command.args(&cfg.pipeline_args).stderr(Stdio::inherit());
    let mut worker = Worker::spawn(&mut command)?;
    let mut settings = cfg.settings.clone();
    settings["session_prefix"] = json!(format!("{prefix}-{generation}"));
    let ready = worker.json(vec![ipc::json_line(&settings)?], cfg.startup, cancel)?;
    anyhow::ensure!(
        ready == json!({"ready":true,"pipeline_protocol":1}),
        "pipeline protocol mismatch"
    );
    Ok(worker)
}
fn infer(
    worker: &mut Worker,
    frame: &Frame,
    imu: &Value,
    cfg: &Config,
    cancel: &Arc<AtomicBool>,
) -> Result<Value> {
    let h = &frame.header;
    let header = json!({"sequence":h.sequence,"timestamp_ms":h.timestamp_ms,"timestamp_basis":h.timestamp_basis,"width":h.width,"height":h.height,"imu":imu});
    let start = Instant::now();
    let mut packet = worker.json(
        vec![ipc::json_line(&header)?, frame.pixels.clone()],
        cfg.request,
        cancel,
    )?;
    anyhow::ensure!(
        packet["frame_sequence"] == h.sequence
            && packet["frame_timestamp_ms"] == h.timestamp_ms
            && packet["image_size_px"] == json!([h.width, h.height])
            && packet["pipeline_backend"] == "rust"
            && packet["session_id"].as_str().is_some_and(|s| !s.is_empty())
            && packet["tracks"].is_array(),
        "pipeline response mismatch"
    );
    let mut identities = std::collections::HashSet::new();
    for track in packet["tracks"].as_array().unwrap() {
        anyhow::ensure!(
            track["code"].as_str().is_some_and(|s| !s.trim().is_empty())
                && track["confidence"]
                    .as_f64()
                    .is_some_and(|c| c.is_finite() && (0. ..=1.).contains(&c)),
            "invalid detection class/confidence"
        );
        let xy = track["xyxy"]
            .as_array()
            .filter(|v| v.len() == 4)
            .context("invalid detection box")?;
        let xy = xy
            .iter()
            .map(|v| {
                v.as_f64()
                    .filter(|n| n.is_finite())
                    .context("invalid box coordinate")
            })
            .collect::<Result<Vec<_>>>()?;
        anyhow::ensure!(
            xy[0] >= 0.
                && xy[1] >= 0.
                && xy[0] < xy[2]
                && xy[1] < xy[3]
                && xy[2] <= h.width as f64
                && xy[3] <= h.height as f64,
            "box outside frame"
        );
        if !track["id"].is_null() {
            let id = track["id"]
                .as_u64()
                .filter(|id| *id > 0)
                .context("invalid track id")?;
            anyhow::ensure!(identities.insert(id), "duplicate track id");
        }
    }
    packet["worker_processing_ms"] = packet["processing_ms"].clone();
    packet["processing_ms"] = json!(start.elapsed().as_secs_f64() * 1000.);
    Ok(packet)
}
pub fn run(args: Args, cancel: Arc<AtomicBool>) -> Result<()> {
    let cfg = config::load(&args)?;
    anyhow::ensure!(
        args.start_ms.is_finite()
            && args.start_ms >= 0.
            && args
                .end_ms
                .is_none_or(|t| t.is_finite() && t > args.start_ms),
        "invalid recording range"
    );
    anyhow::ensure!(
        args.max_fps == 0. || (args.max_fps.is_finite() && (0.001..=240.).contains(&args.max_fps)),
        "max-fps must be 0 (uncapped) or in [0.001,240]"
    );
    let timeout = config::duration(args.io_timeout)?;
    let mut budget = Budget::new(args.max_restarts);
    let mut capture = None::<Worker>;
    let mut generation = 0u64;
    let imu = cfg
        .imu
        .as_ref()
        .map(|(bind, model)| LiveImu::start(&cfg.worker, bind, model, &cancel))
        .transpose()?;
    let mut output: Box<dyn Write> = if let Some(path) = &args.jsonl {
        Box::new(BufWriter::new(
            OpenOptions::new().write(true).create_new(true).open(path)?,
        ))
    } else {
        Box::new(io::stdout())
    };
    let prefix = format!(
        "native-{}-{}",
        std::process::id(),
        SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos()
    );
    let origin = Instant::now();
    let mut sequence = 0u64;
    let mut processed = 0u64;
    let mut replay_pass = 0u32;
    let mut pass_frames = 0u64;
    let mut next = Instant::now();
    // Pipeline initialization is outside the steady-state per-frame measurements.
    let mut processor = Some(loop {
        match pipeline(&cfg, &prefix, generation, &cancel) {
            Ok(p) => break p,
            Err(e) => {
                generation += 1;
                if !budget.retry(&cancel) {
                    return Err(e);
                }
            }
        }
    });
    'frames: while !cancel.load(Ordering::Relaxed) {
        if args.max_fps > 0. {
            sleep_until(next, &cancel);
        }
        let service = Instant::now();
        if cancel.load(Ordering::Relaxed) {
            break;
        }
        if args.max_fps > 0. {
            next = service + Duration::from_secs_f64(1. / args.max_fps);
        }
        let read_start = Instant::now();
        let frame = loop {
            let result = (|| -> Result<Option<Frame>> {
                if capture.is_none() {
                    capture = Some(source(&cfg, timeout, &cancel)?);
                }
                match capture.as_mut().unwrap().exchange(
                    vec![b"{\"next\":true}\n".to_vec()],
                    Kind::Frame,
                    timeout,
                    &cancel,
                )? {
                    Response::Frame(None) if cfg.live => bail!("live input disconnected"),
                    Response::Frame(frame) => Ok(frame),
                    _ => bail!("decoder response mismatch"),
                }
            })();
            match result {
                Ok(frame) => break frame,
                Err(e) => {
                    capture.take();
                    processor.take();
                    generation += 1;
                    if cancel.load(Ordering::Relaxed) {
                        break 'frames;
                    }
                    if !cfg.live || !budget.retry(&cancel) {
                        return Err(e).context("native input stopped");
                    }
                }
            }
        };
        let read_ms = read_start.elapsed().as_secs_f64() * 1000.;
        let mut frame = match frame {
            Some(f) if cfg.live || args.end_ms.is_none_or(|end| f.header.timestamp_ms < end) => f,
            _ => {
                anyhow::ensure!(
                    pass_frames > 0,
                    "recording contains no frames in the selected range"
                );
                if replay_pass + 1 < args.repeat {
                    capture.take();
                    processor.take();
                    generation += 1;
                    replay_pass += 1;
                    pass_frames = 0;
                    sequence = 0;
                    next = Instant::now();
                    continue;
                }
                break;
            }
        };
        sequence += 1;
        frame.header.sequence = sequence;
        if cfg.live {
            frame.header.timestamp_ms = origin.elapsed().as_secs_f64() * 1000.;
        }
        if frame.header.timestamp_ms < args.start_ms {
            continue;
        }
        let mut packet = loop {
            let result = (|| -> Result<Value> {
                if processor.is_none() {
                    processor = Some(pipeline(&cfg, &prefix, generation, &cancel)?);
                }
                let classification = if let Some(imu) = &imu {
                    imu.snapshot()?
                } else {
                    json!({"valid":false,"direction":null,"reason":if cfg.live {"imu_unavailable_or_stale"} else {"recording_has_no_synchronized_imu"}})
                };
                infer(
                    processor.as_mut().unwrap(),
                    &frame,
                    &classification,
                    &cfg,
                    &cancel,
                )
            })();
            match result {
                Ok(packet) => break packet,
                Err(e) => {
                    processor.take();
                    generation += 1;
                    if cancel.load(Ordering::Relaxed) {
                        break 'frames;
                    }
                    if !budget.retry(&cancel) {
                        return Err(e).context("native pipeline stopped");
                    }
                }
            }
        };
        processed += 1;
        pass_frames += 1;
        packet["recovery_count"] = json!(budget.used);
        packet["replay_pass"] = json!(replay_pass);
        packet["input_read_ms"] = json!(read_ms);
        packet["host_backend"] = json!("rust");
        packet["input_backend"] = json!("rust_opencv");
        packet["imu_alignment"] = json!(if cfg.live {
            "host_arrival_approximate"
        } else {
            "unavailable"
        });
        // Service time excludes pacing and this packet's serialization/write; external benchmark includes output I/O.
        packet["host_service_ms"] = json!(service.elapsed().as_secs_f64() * 1000.);
        output.write_all(&ipc::json_line(&packet)?)?;
        if processed.is_multiple_of(args.flush_every) {
            output.flush()?;
        }
        if args.max_frames.is_some_and(|max| processed >= max) {
            break;
        }
    }
    output.flush()?;
    eprintln!(
        "Rust video finished: {processed} frames, {} restarts",
        budget.used
    );
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn budget_is_shared_finite_and_cancel_aware() {
        let stop = Arc::new(AtomicBool::new(false));
        let mut b = Budget::new(1);
        assert!(b.retry(&stop));
        assert!(!b.retry(&stop));
        assert_eq!(b.used, 1);
        stop.store(true, Ordering::Relaxed);
        assert!(!Budget::new(20).retry(&stop));
    }
}
