mod config;
mod decoder;
mod host;
mod imu;
mod ipc;
use clap::Parser;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

#[derive(Parser)]
#[command(
    version,
    about = "Native Rust video input and headless detect/track host"
)]
pub struct Args {
    #[arg(long, default_value = "multidevice_app/config.json")]
    config: PathBuf,
    #[arg(long)]
    profile: Option<PathBuf>,
    #[arg(long)]
    source: Option<String>,
    #[arg(long)]
    model: Option<String>,
    #[arg(long)]
    recording: bool,
    #[arg(long)]
    no_imu: bool,
    #[arg(long)]
    imu_bind: Option<String>,
    #[cfg_attr(
        windows,
        arg(long, default_value = "multidevice_app/bin/haneulmaru.exe")
    )]
    #[cfg_attr(
        not(windows),
        arg(long, default_value = "multidevice_app/rust/target/release/haneulmaru")
    )]
    worker: PathBuf,
    #[arg(long)]
    jsonl: Option<PathBuf>,
    #[arg(long, default_value_t = 0.)]
    start_ms: f64,
    #[arg(long)]
    end_ms: Option<f64>,
    #[arg(long,default_value_t=1,value_parser=clap::value_parser!(u32).range(1..=100))]
    repeat: u32,
    #[arg(long,default_value_t=0,value_parser=clap::value_parser!(u32).range(0..=20))]
    max_restarts: u32,
    #[arg(long, default_value_t = 5.)]
    io_timeout: f64,
    #[arg(long,default_value_t=1,value_parser=clap::value_parser!(u64).range(1..))]
    flush_every: u64,
    /// Maximum release rate; 0 runs as fast as possible. No frame dropping or catch-up bursts.
    #[arg(long, default_value_t = 0.)]
    max_fps: f64,
    #[arg(long,value_parser=clap::value_parser!(u64).range(1..))]
    max_frames: Option<u64>,
    #[arg(long, hide = true)]
    decode_worker: bool,
}
fn main() -> std::process::ExitCode {
    let args = Args::parse();
    if args.decode_worker {
        return match decoder::worker() {
            Ok(()) => std::process::ExitCode::SUCCESS,
            Err(_) => {
                eprintln!("Native decoder failed");
                std::process::ExitCode::FAILURE
            }
        };
    }
    let cancel = Arc::new(AtomicBool::new(false));
    let stop = cancel.clone();
    if let Err(e) = ctrlc::set_handler(move || stop.store(true, Ordering::Relaxed)) {
        eprintln!("{e}");
        return std::process::ExitCode::FAILURE;
    }
    match host::run(args, cancel) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{e:#}");
            std::process::ExitCode::FAILURE
        }
    }
}
