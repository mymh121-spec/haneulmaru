mod assignment;
mod evaluation;
mod imu;
mod ort;
mod pipeline;
mod tracking;
mod vision;

use std::{env, error::Error, process::ExitCode};
type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn main() -> ExitCode {
    let args: Vec<_> = env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        None | Some("imu") => imu::listen(args.get(1).map(String::as_str).unwrap_or("0.0.0.0:8793"), args.get(2)),
        Some("detect") if args.len() == 7 => vision::worker(&args[1..], false),
        Some("detect-profile") if args.len() == 7 => vision::worker(&args[1..], true),
        Some("pipeline") if args.len() == 7 => pipeline::worker(&args[1..]),
        Some("evaluate") if args.len() == 1 => evaluation::worker(),
        Some("track") if args.len() == 1 => tracking::worker(false),
        Some("track") if args.len() == 2 && args[1] == "strict" => tracking::worker(true),
        _ => Err("Usage: haneulmaru imu [bind:port] [direction-model.json] | detect|detect-profile|pipeline model.onnx size confidence iou input_name output_name | track [strict] | evaluate".into()),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{e}");
            ExitCode::FAILURE
        }
    }
}
