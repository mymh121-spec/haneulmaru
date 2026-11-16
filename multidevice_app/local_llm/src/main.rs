mod runtime;

use anyhow::{bail, Context, Result};
use clap::Parser;
use haneulmaru::evidence::{self, Evidence};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, File},
    io::{BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant},
};

const MAX_REQUEST_BYTES: u64 = 128 * 1024;
const MAX_RESPONSE_BYTES: u64 = 128 * 1024;

#[derive(Parser, Debug)]
#[command(
    version,
    about = "Local GGUF analysis of Haneulmaru result JSONL, powered by mistral.rs"
)]
struct Args {
    #[arg(long, required_unless_present = "worker_request")]
    results: Option<PathBuf>,
    /// Existing GGUF file. No Hub IDs or automatic weight downloads.
    #[arg(long, required_unless_present_any = ["prepare_only", "worker_request"])]
    model: Option<PathBuf>,
    /// Optional local .jinja/.json template matching the model (otherwise GGUF metadata).
    #[arg(long)]
    chat_template: Option<PathBuf>,
    #[arg(
        long,
        default_value = "실행 결과를 요약하고, IMU 누락과 프레임 누락 및 처리 지연을 설명해줘."
    )]
    question: String,
    /// Print deterministic evidence and prompt without loading any model.
    #[arg(long)]
    prepare_only: bool,
    /// New JSON output file. Existing files are never overwritten.
    #[arg(long)]
    output: Option<PathBuf>,
    #[arg(long, default_value_t = 512, value_parser = clap::value_parser!(u32).range(1..=4096))]
    max_tokens: u32,
    /// Includes model loading and generation; kills the child on expiration.
    #[arg(long, default_value_t = 120, value_parser = clap::value_parser!(u64).range(1..=3600))]
    timeout_secs: u64,
    /// Use the compiled CUDA/Metal backend instead of the default CPU.
    #[arg(long)]
    accelerator: bool,
    #[arg(long, hide = true)]
    worker_request: Option<PathBuf>,
    #[arg(long, hide = true, requires = "worker_request")]
    worker_response: Option<PathBuf>,
}

#[derive(Serialize, Deserialize)]
struct Request {
    model: PathBuf,
    chat_template: Option<PathBuf>,
    prompt: String,
    max_tokens: u32,
    accelerator: bool,
}
#[derive(Serialize, Deserialize)]
struct Answer {
    text: String,
    prompt_tokens: usize,
    completion_tokens: usize,
}

fn read_bounded(path: &Path, limit: u64) -> Result<Vec<u8>> {
    let file = File::open(path).with_context(|| format!("open {}", path.display()))?;
    anyhow::ensure!(file.metadata()?.is_file(), "expected a regular file");
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes)?;
    anyhow::ensure!(bytes.len() as u64 <= limit, "file exceeds {limit} bytes");
    Ok(bytes)
}

fn local_model(path: &Path) -> Result<PathBuf> {
    let path = path
        .canonicalize()
        .context("--model must be an existing local GGUF file")?;
    let mut file = File::open(&path)?;
    anyhow::ensure!(file.metadata()?.is_file(), "model is not a regular file");
    let mut magic = [0; 4];
    file.read_exact(&mut magic).context("truncated GGUF file")?;
    anyhow::ensure!(
        &magic == b"GGUF",
        "model does not have the GGUF file signature"
    );
    Ok(path)
}
fn local_template(path: &Path) -> Result<PathBuf> {
    let path = path
        .canonicalize()
        .context("chat template must be a local file")?;
    anyhow::ensure!(
        matches!(
            path.extension().and_then(|s| s.to_str()),
            Some("jinja" | "json")
        ),
        "chat template must end in .jinja or .json"
    );
    let bytes = read_bounded(&path, 1024 * 1024)?;
    anyhow::ensure!(!bytes.is_empty(), "chat template is empty");
    std::str::from_utf8(&bytes).context("chat template must be UTF-8")?;
    Ok(path)
}

/// Hard wall-clock bound even when a native model load hangs.
fn wait_bounded(mut child: std::process::Child, timeout: Duration) -> Result<()> {
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                anyhow::ensure!(
                    status.success(),
                    "local inference worker exited with {status}"
                );
                return Ok(());
            }
            Ok(None) if start.elapsed() < timeout => std::thread::sleep(Duration::from_millis(10)),
            result => {
                let _ = child.kill();
                let _ = child.wait();
                if let Err(e) = result {
                    return Err(e.into());
                }
                bail!("local inference exceeded the model-load + generation deadline; worker terminated");
            }
        }
    }
}

fn infer(request: &Request, timeout: Duration) -> Result<Answer> {
    let temporary = tempfile::tempdir()?;
    let input = temporary.path().join("request.json");
    let output = temporary.path().join("answer.json");
    fs::write(&input, serde_json::to_vec(request)?)?;
    let mut command = Command::new(std::env::current_exe()?);
    // Model-library stdout must not corrupt our JSON output.
    command
        .arg("--worker-request")
        .arg(&input)
        .arg("--worker-response")
        .arg(&output)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .env("HF_HUB_OFFLINE", "1")
        .env("HF_HUB_DISABLE_TELEMETRY", "1");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    wait_bounded(command.spawn()?, timeout)?;
    let answer: Answer = serde_json::from_slice(&read_bounded(&output, MAX_RESPONSE_BYTES)?)?;
    anyhow::ensure!(
        !answer.text.trim().is_empty(),
        "model produced no answer text"
    );
    Ok(answer)
}

fn write_output(output: Option<&Path>, value: &impl Serialize) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    if let Some(path) = output {
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let mut temp = tempfile::NamedTempFile::new_in(parent)?;
        temp.write_all(&bytes)?;
        temp.as_file().sync_all()?;
        temp.persist_noclobber(path)
            .context("output exists or cannot be created")?;
    } else {
        std::io::stdout().lock().write_all(&bytes)?;
    }
    Ok(())
}

fn run(args: Args) -> Result<()> {
    if let Some(path) = args.worker_request {
        let output = args
            .worker_response
            .context("worker response path required")?;
        let request: Request = serde_json::from_slice(&read_bounded(&path, MAX_REQUEST_BYTES)?)?;
        anyhow::ensure!(
            (1..=4096).contains(&request.max_tokens) && request.prompt.len() <= 64 * 1024,
            "invalid worker request limits"
        );
        local_model(&request.model)?;
        if let Some(template) = &request.chat_template {
            local_template(template)?;
        }
        let answer = runtime::generate(request)?;
        return write_output(Some(&output), &answer);
    }
    if args.output.as_ref().is_some_and(|path| path.exists()) {
        bail!("output already exists");
    }
    let source = args.results.context("--results required")?.canonicalize()?;
    let input = File::open(&source)?;
    anyhow::ensure!(
        input.metadata()?.is_file(),
        "results must be a regular JSONL file"
    );
    let evidence: Evidence = evidence::summarize(BufReader::new(input))?;
    let prompt = evidence::prompt(&evidence, &args.question)?;
    anyhow::ensure!(
        prompt.len() <= 64 * 1024,
        "evidence too large; split the recording"
    );
    if args.prepare_only {
        return write_output(
            args.output.as_deref(),
            &serde_json::json!({
                "schema_version":1,"status":"prepared_only","source":source,
                "system_prompt":evidence::SYSTEM_PROMPT,"prompt":prompt,"evidence":evidence,
                "answer":null,"model_executed":false,
            }),
        );
    }
    if args.accelerator && !cfg!(any(feature = "cuda", feature = "metal")) {
        bail!("this binary has no accelerator backend; build with --features cuda or metal");
    }
    let request = Request {
        model: local_model(&args.model.context("--model required")?)?,
        chat_template: args
            .chat_template
            .as_deref()
            .map(local_template)
            .transpose()?,
        prompt,
        max_tokens: args.max_tokens,
        accelerator: args.accelerator,
    };
    let started = Instant::now();
    let answer = infer(&request, Duration::from_secs(args.timeout_secs))?;
    write_output(
        args.output.as_deref(),
        &serde_json::json!({
            "schema_version":1,"status":"generated","source":source,"evidence":evidence,
            "question":args.question,"model":request.model,"chat_template":request.chat_template,
            "backend":"mistralrs","backend_version":"0.8.1","model_executed":true,
            "device_policy":if args.accelerator {"compiled_accelerator"} else {"cpu"},
            "elapsed_ms":started.elapsed().as_secs_f64()*1000.,"answer":answer,
            "answer_status":"model_generated_not_fact_checked",
        }),
    )
}
fn main() -> std::process::ExitCode {
    match run(Args::parse()) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error:#}");
            std::process::ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn local_files_required_and_magic_checked() {
        assert!(local_model(Path::new("org/remote-model")).is_err());
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("bad.gguf");
        fs::write(&file, b"not a GGUF").unwrap();
        assert!(local_model(&file).is_err());
        assert!(local_model(dir.path()).is_err());
    }
    #[test]
    fn output_never_overwrites_existing_data() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("result.json");
        fs::write(&file, b"keep").unwrap();
        assert!(write_output(Some(&file), &serde_json::json!({"new":true})).is_err());
        assert_eq!(fs::read(&file).unwrap(), b"keep");
    }
    #[test]
    fn bounded_reader_rejects_oversize_response() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("response.json");
        fs::write(&file, vec![b'x'; 17]).unwrap();
        assert!(read_bounded(&file, 16).is_err());
    }
    #[test]
    fn process_deadline_kills_and_reaps_a_stalled_worker() {
        let child = Command::new(std::env::current_exe().unwrap())
            .args(["--ignored", "--exact", "tests::sleeping_child"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let now = Instant::now();
        assert!(wait_bounded(child, Duration::from_millis(50)).is_err());
        assert!(now.elapsed() < Duration::from_secs(5));
    }
    #[test]
    #[ignore = "subprocess fixture invoked by process_deadline test"]
    fn sleeping_child() {
        std::thread::sleep(Duration::from_secs(30));
    }
}
