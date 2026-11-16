use crate::Args;
use anyhow::{Context, Result};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    time::Duration,
};

pub struct Config {
    pub source: String,
    pub live: bool,
    pub rotation: i32,
    pub worker: PathBuf,
    pub pipeline_args: Vec<String>,
    pub settings: Value,
    pub startup: Duration,
    pub request: Duration,
    pub imu: Option<(String, PathBuf)>,
}
fn read(path: &Path) -> Result<Value> {
    let mut s = String::new();
    File::open(path)?
        .take(1024 * 1024 + 1)
        .read_to_string(&mut s)?;
    anyhow::ensure!(s.len() <= 1024 * 1024, "configuration too large");
    Ok(serde_json::from_str(s.trim_start_matches('\u{feff}'))?)
}
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v[key]
        .as_str()
        .filter(|s| !s.trim().is_empty() && !s.contains('\0'))
        .context(format!("invalid {key}"))
}
fn number(v: &Value, key: &str) -> Result<f64> {
    v[key]
        .as_f64()
        .filter(|x| x.is_finite())
        .context(format!("invalid {key}"))
}
pub fn duration(seconds: f64) -> Result<Duration> {
    anyhow::ensure!(
        seconds.is_finite() && seconds > 0. && seconds <= 3600.,
        "timeout must be in (0,3600]"
    );
    Ok(Duration::from_secs_f64(seconds))
}
pub fn load(args: &Args) -> Result<Config> {
    let path = args.config.canonicalize()?;
    let root = path.parent().context("config directory")?;
    let cfg = read(&path)?;
    anyhow::ensure!(
        cfg["schema_version"] == 1 && cfg["tracking"] == "rust",
        "native host requires schema_version=1 and tracking=rust"
    );
    let mut engine = cfg["engine"].clone();
    anyhow::ensure!(
        engine["name"] == "rust" && engine["device"].as_str().unwrap_or("cpu") == "cpu",
        "native host requires Rust ONNX CPU engine"
    );
    let mut model_root = root.to_path_buf();
    if let Some(profile) = &args.profile {
        let path = profile.canonicalize()?;
        model_root = path.parent().context("profile directory")?.to_path_buf();
        let p = read(&path)?;
        anyhow::ensure!(p["schema_version"] == 1, "profile schema_version must be 1");
        for key in ["model_path", "input_size", "classes", "output_layout"] {
            anyhow::ensure!(p.get(key).is_some(), "profile missing {key}");
        }
        let e = engine.as_object_mut().context("engine object required")?;
        e.remove("deployment");
        // Match Python load_profile: profile supplies model layout, not runtime thresholds.
        for (key, default) in [
            ("input_name", json!("images")),
            ("output_name", json!("output0")),
            ("display_names", json!({})),
            ("class_change_policy", json!("allow")),
        ] {
            e.insert(key.into(), p.get(key).cloned().unwrap_or(default));
        }
        e.remove("agnostic_nms");
        for key in [
            "model_path",
            "input_size",
            "classes",
            "output_layout",
            "agnostic_nms",
            "deployment",
        ] {
            if let Some(value) = p.get(key) {
                e.insert(key.into(), value.clone());
            }
        }
    }
    let model_path = model_root
        .join(
            args.model
                .as_deref()
                .map(Path::new)
                .unwrap_or(Path::new(string(&engine, "model_path")?)),
        )
        .canonicalize()?;
    anyhow::ensure!(model_path.is_file(), "ONNX model is not a file");
    let classes = engine["classes"]
        .as_array()
        .filter(|v| !v.is_empty())
        .context("classes required")?;
    let mut names = HashSet::new();
    for name in classes {
        let n = name.as_str().context("class name must be a string")?;
        anyhow::ensure!(
            !n.is_empty()
                && n.trim() == n
                && n != "UNKNOWN"
                && !n.chars().any(char::is_control)
                && names.insert(n),
            "invalid or duplicate class"
        );
    }
    let layout = engine["output_layout"]
        .as_str()
        .unwrap_or("yolo11_raw_1_7_n");
    let legacy = layout == "yolo11_raw_1_7_n";
    anyhow::ensure!(
        !legacy || classes == &vec![json!("R"), json!("B"), json!("G")],
        "legacy layout requires R/B/G"
    );
    let layout = if legacy { "yolo_raw" } else { layout };
    anyhow::ensure!(
        [
            "yolo_raw",
            "yolo_raw_rows",
            "yolo_objectness_rows",
            "xyxy_score_class"
        ]
        .contains(&layout),
        "unsupported output layout"
    );
    let agnostic = match engine.get("agnostic_nms") {
        None => legacy,
        Some(v) => v.as_bool().context("agnostic_nms must be bool")?,
    };
    let policy = engine["class_change_policy"].as_str().unwrap_or("allow");
    anyhow::ensure!(
        ["allow", "strict"].contains(&policy),
        "invalid class policy"
    );
    let aliases = engine.get("display_names").cloned().unwrap_or(json!({}));
    for (key, value) in aliases
        .as_object()
        .context("display_names must be an object")?
    {
        let s = value.as_str().context("alias must be string")?;
        anyhow::ensure!(
            names.contains(key.as_str())
                && !s.trim().is_empty()
                && !s.chars().any(char::is_control),
            "invalid display alias"
        );
    }
    let verification = if let Some(deployment) = engine.get("deployment") {
        let expected = string(deployment, "model_sha256")?;
        anyhow::ensure!(
            expected.len() == 64
                && expected
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                && deployment["classes"] == engine["classes"],
            "invalid deployment hash or class order"
        );
        let mut hash = Sha256::new();
        let mut file = File::open(&model_path)?;
        let mut buffer = [0; 65536];
        loop {
            let n = file.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            hash.update(&buffer[..n]);
        }
        let actual = format!("{:x}", hash.finalize());
        anyhow::ensure!(actual == expected, "ONNX deployment SHA-256 mismatch");
        json!({"status":"verified","model_sha256":actual})
    } else {
        json!({"status":"unverified","reason":"missing_deployment_record"})
    };
    let size = engine["input_size"]
        .as_u64()
        .context("input_size must be integer")?;
    let confidence = number(&engine, "confidence")?;
    let iou = number(&engine, "nms_iou")?;
    let color = number(&cfg, "color_confidence")?;
    let gap = number(&cfg, "reset_tracking_after_gap_ms")?;
    anyhow::ensure!(
        (32..=2048).contains(&size)
            && size % 32 == 0
            && confidence > 0.
            && confidence <= 1.
            && iou > 0.
            && iou <= 1.
            && (0. ..=1.).contains(&color)
            && gap > 0.,
        "invalid detector or tracker settings"
    );
    let source = args
        .source
        .clone()
        .unwrap_or_else(|| cfg["camera"]["source"].as_str().unwrap_or("").into());
    anyhow::ensure!(!source.trim().is_empty(), "--source is required");
    let configured_live = cfg["camera"]["live"]
        .as_bool()
        .context("camera.live must be bool")?;
    let live = configured_live && !args.recording && !Path::new(&source).is_file();
    let rotation = cfg["camera"]["rotation_clockwise_deg"]
        .as_i64()
        .context("rotation must be integer")?;
    anyhow::ensure!([0, 90, 180, 270].contains(&rotation), "invalid rotation");
    anyhow::ensure!(
        !live || (args.start_ms == 0. && args.end_ms.is_none() && args.repeat == 1),
        "range/repeat only supported for recordings"
    );
    let imu_enabled = cfg["imu"]["enabled"]
        .as_bool()
        .context("imu.enabled must be bool")?;
    let imu = if live && imu_enabled && !args.no_imu {
        Some((
            args.imu_bind
                .clone()
                .unwrap_or_else(|| cfg["imu"]["bind"].as_str().unwrap_or("0.0.0.0:8793").into()),
            root.join(string(&cfg["imu"], "training_path")?)
                .canonicalize()?,
        ))
    } else {
        None
    };
    let worker = args.worker.canonicalize().context(
        "native pipeline worker missing; build multidevice_app/rust first or pass --worker",
    )?;
    let pipeline_args = vec![
        "pipeline".into(),
        model_path.to_string_lossy().into(),
        size.to_string(),
        confidence.to_string(),
        iou.to_string(),
        string(&engine, "input_name")?.into(),
        string(&engine, "output_name")?.into(),
    ];
    Ok(Config {
        source,
        live,
        rotation: rotation as i32,
        worker,
        pipeline_args,
        settings: json!({"classes":classes,"output_layout":layout,"agnostic_nms":agnostic,
            "color_confidence":color,"max_gap_ms":gap,"class_change_policy":policy,"display_names":aliases,"model_verification":verification,"include_detections":false}),
        startup: duration(
            engine
                .get("startup_timeout_s")
                .map_or(Ok(30.), |_| number(&engine, "startup_timeout_s"))?,
        )?,
        request: duration(
            engine
                .get("request_timeout_s")
                .map_or(Ok(5.), |_| number(&engine, "request_timeout_s"))?,
        )?,
        imu,
    })
}
