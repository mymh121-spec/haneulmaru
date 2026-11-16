use serde_json::{json, Value};
mod support;

#[test]
fn untrained_local_gguf_generates_tokens_through_the_real_sdk() {
    let dir = tempfile::tempdir().unwrap();
    let source = recording(dir.path());
    let model = dir.path().join("untrained.gguf");
    support::tiny_gguf(&model);
    let output = Command::new(env!("CARGO_BIN_EXE_haneulmaru-llm"))
        .arg("--results")
        .arg(source)
        .arg("--model")
        .arg(model)
        .args(["--max-tokens", "2", "--timeout-secs", "60"])
        .env("HF_HOME", dir.path().join("hf-cache"))
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["model_executed"], true);
    assert_eq!(value["backend"], "mistralrs");
    assert_eq!(value["answer"]["completion_tokens"], 2);
    assert!(value["answer"]["text"].as_str().unwrap().contains('x'));
    assert!(value["answer"]["prompt_tokens"].as_u64().unwrap() > 0);
    assert_eq!(value["evidence"]["imu_unavailable_frames"], 2);
}
use std::{fs, process::Command};

fn recording(dir: &std::path::Path) -> std::path::PathBuf {
    let path = dir.join("results.jsonl");
    let text=(1..=3).map(|seq|format!("{}\n",json!({
        "schema_version":1,"app":"haneulmaru","session_id":"test-session",
        "frame_sequence":seq,"frame_timestamp_ms":seq as f64*1000./60.,
        "timestamp_basis":"video_pts","tracks":[],"imu":{"valid":seq==1},"processing_ms":8.
    }))).collect::<String>();
    fs::write(&path, text).unwrap();
    path
}
#[test]
fn prepare_only_uses_real_cli_and_does_not_claim_inference() {
    let dir = tempfile::tempdir().unwrap();
    let source = recording(dir.path());
    let output = Command::new(env!("CARGO_BIN_EXE_haneulmaru-llm"))
        .arg("--results")
        .arg(source)
        .args(["--prepare-only", "--question", "IMU 누락은?"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["status"], "prepared_only");
    assert_eq!(value["model_executed"], false);
    assert_eq!(value["answer"], Value::Null);
    assert_eq!(value["evidence"]["frames"], 3);
    assert_eq!(value["evidence"]["imu_unavailable_frames"], 2);
}
#[test]
fn truncated_gguf_reaches_native_loader_and_fails_without_an_answer_file() {
    let dir = tempfile::tempdir().unwrap();
    let source = recording(dir.path());
    let model = dir.path().join("truncated.gguf");
    let answer = dir.path().join("answer.json");
    fs::write(&model, b"GGUF").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_haneulmaru-llm"))
        .arg("--results")
        .arg(source)
        .arg("--model")
        .arg(model)
        .arg("--output")
        .arg(&answer)
        .args(["--timeout-secs", "15"])
        .env("HF_HOME", dir.path().join("hf-cache"))
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(!answer.exists());
    assert!(output.stdout.is_empty());
    let error = String::from_utf8_lossy(&output.stderr);
    assert!(error.contains("load local GGUF"), "{error}");
    assert!(
        !error.contains("deadline"),
        "bad local model should fail immediately: {error}"
    );
}
#[test]
fn invalid_cli_limits_and_malformed_results_are_errors() {
    let dir = tempfile::tempdir().unwrap();
    let source = recording(dir.path());
    for (flag, value) in [("--max-tokens", "0"), ("--timeout-secs", "0")] {
        let output = Command::new(env!("CARGO_BIN_EXE_haneulmaru-llm"))
            .arg("--results")
            .arg(&source)
            .args(["--prepare-only", flag, value])
            .output()
            .unwrap();
        assert!(!output.status.success());
    }
    fs::write(&source, b"{invalid").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_haneulmaru-llm"))
        .arg("--results")
        .arg(source)
        .arg("--prepare-only")
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
}
