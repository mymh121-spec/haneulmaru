//! One outstanding request, one buffered reader, and a deadline covering write + read.
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    io::{BufRead, BufReader, Read, Write},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, SyncSender},
        Arc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

pub const MAX_PIXELS: usize = 16_000_000;
pub fn bytes_for(width: usize, height: usize) -> Result<usize> {
    let pixels = width
        .checked_mul(height)
        .context("frame dimensions overflow")?;
    anyhow::ensure!(
        pixels > 0 && pixels <= MAX_PIXELS,
        "frame dimensions out of range"
    );
    Ok(pixels * 3)
}
pub fn read_json(reader: &mut impl BufRead, limit: usize) -> Result<Value> {
    let mut line = Vec::new();
    reader
        .take((limit + 1) as u64)
        .read_until(b'\n', &mut line)?;
    anyhow::ensure!(
        line.len() <= limit && line.ends_with(b"\n"),
        "worker closed, truncated or oversized response"
    );
    Ok(serde_json::from_slice(&line)?)
}
pub fn json_line(value: &impl Serialize) -> Result<Vec<u8>> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    Ok(bytes)
}
#[derive(Debug, Deserialize, Serialize)]
pub struct Header {
    pub sequence: u64,
    pub timestamp_ms: f64,
    pub timestamp_basis: String,
    pub width: usize,
    pub height: usize,
}
impl Header {
    pub fn validate(&self) -> Result<usize> {
        anyhow::ensure!(
            self.sequence > 0
                && self.timestamp_ms.is_finite()
                && self.timestamp_ms >= 0.
                && [
                    "video_pts",
                    "video_fps_estimate",
                    "host_decode_elapsed",
                    "host_decode_time"
                ]
                .contains(&self.timestamp_basis.as_str()),
            "invalid decoded identity or timestamp"
        );
        bytes_for(self.width, self.height)
    }
}
#[derive(Debug)]
pub struct Frame {
    pub header: Header,
    pub pixels: Vec<u8>,
}
pub enum Response {
    Json(Value),
    Frame(Option<Frame>),
}
#[derive(Clone, Copy)]
pub enum Kind {
    Json,
    Frame,
}
struct Job {
    parts: Vec<Vec<u8>>,
    kind: Kind,
}
pub struct Worker {
    child: Child,
    tx: Option<SyncSender<Job>>,
    rx: Option<Receiver<Result<Response>>>,
    io: Option<JoinHandle<()>>,
}
impl Worker {
    pub fn spawn(command: &mut Command) -> Result<Self> {
        command.stdin(Stdio::piped()).stdout(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn().context("start native worker")?;
        let mut writer = child.stdin.take().context("worker stdin")?;
        let mut reader =
            BufReader::with_capacity(65536, child.stdout.take().context("worker stdout")?);
        let (tx, jobs) = mpsc::sync_channel::<Job>(1);
        let (responses, rx) = mpsc::sync_channel(1);
        let io = thread::spawn(move || {
            while let Ok(job) = jobs.recv() {
                let result = (|| -> Result<Response> {
                    for part in job.parts {
                        writer.write_all(&part)?;
                    }
                    writer.flush()?;
                    let row = read_json(
                        &mut reader,
                        if matches!(job.kind, Kind::Frame) {
                            65536
                        } else {
                            8 * 1024 * 1024
                        },
                    )?;
                    match job.kind {
                        Kind::Json => Ok(Response::Json(row)),
                        Kind::Frame if row.get("eof") == Some(&Value::Bool(true)) => {
                            Ok(Response::Frame(None))
                        }
                        Kind::Frame => {
                            let header: Header = serde_json::from_value(row)?;
                            let mut pixels = vec![0; header.validate()?];
                            reader.read_exact(&mut pixels)?;
                            Ok(Response::Frame(Some(Frame { header, pixels })))
                        }
                    }
                })();
                let failed = result.is_err();
                if responses.send(result).is_err() || failed {
                    break;
                }
            }
        });
        Ok(Self {
            child,
            tx: Some(tx),
            rx: Some(rx),
            io: Some(io),
        })
    }
    pub fn exchange(
        &mut self,
        parts: Vec<Vec<u8>>,
        kind: Kind,
        timeout: Duration,
        cancel: &Arc<AtomicBool>,
    ) -> Result<Response> {
        self.tx
            .as_ref()
            .context("closed worker")?
            .try_send(Job { parts, kind })
            .map_err(|_| anyhow::anyhow!("worker unavailable"))?;
        let until = Instant::now() + timeout;
        loop {
            if cancel.load(Ordering::Relaxed) {
                bail!("cancelled");
            }
            let remaining = until.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                bail!("native worker I/O deadline exceeded");
            }
            match self
                .rx
                .as_ref()
                .context("closed worker")?
                .recv_timeout(remaining.min(Duration::from_millis(20)))
            {
                Ok(reply) => return reply,
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(_) => bail!("native worker disconnected"),
            }
        }
    }
    pub fn json(
        &mut self,
        parts: Vec<Vec<u8>>,
        timeout: Duration,
        cancel: &Arc<AtomicBool>,
    ) -> Result<Value> {
        match self.exchange(parts, Kind::Json, timeout, cancel)? {
            Response::Json(v) => Ok(v),
            _ => bail!("worker response kind mismatch"),
        }
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        self.tx.take();
        self.rx.take();
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(io) = self.io.take() {
            let _ = io.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bounds_dimensions_and_requires_complete_headers() {
        assert!(bytes_for(usize::MAX, 2).is_err());
        assert!(bytes_for(0, 1).is_err());
        assert!(bytes_for(4001, 4000).is_err());
        assert!(read_json(&mut std::io::Cursor::new(b"{}"), 64).is_err());
        assert!(read_json(&mut std::io::Cursor::new(b"12345\n"), 4).is_err());
    }
    #[test]
    fn stalled_child_is_killed_without_leaking_the_io_thread() {
        let mut cmd = Command::new(std::env::current_exe().unwrap());
        cmd.args(["--ignored", "--exact", "ipc::tests::stalled_child"])
            .stderr(Stdio::null());
        let mut worker = Worker::spawn(&mut cmd).unwrap();
        let start = Instant::now();
        // The child does not read stdin. A large write fills the OS pipe before any
        // test-harness stdout can be mistaken for the response under test.
        let error = worker
            .json(
                vec![vec![0; 8 * 1024 * 1024]],
                Duration::from_millis(80),
                &Arc::new(AtomicBool::new(false)),
            )
            .err()
            .unwrap();
        assert!(error.to_string().contains("deadline"));
        drop(worker);
        assert!(start.elapsed() < Duration::from_secs(3));
    }
    #[test]
    #[ignore = "child process fixture"]
    fn stalled_child() {
        thread::sleep(Duration::from_secs(30));
    }
}
