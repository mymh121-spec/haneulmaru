use crate::ipc::{self, Header};
use anyhow::{Context, Result};
use opencv::{core, prelude::*, videoio};
use serde::{Deserialize, Serialize};
use std::{
    io::{self, Write},
    time::Instant,
};

#[derive(Deserialize, Serialize)]
pub struct Config {
    pub source: String,
    pub rotation: i32,
    pub recording: bool,
}
#[derive(Default)]
pub struct MediaClock {
    previous: Option<(f64, f64)>,
}
impl MediaClock {
    pub fn stamp(&mut self, pts: f64, fps: f64, decoded: f64) -> (f64, &'static str) {
        let (stamp, basis) =
            if pts.is_finite() && pts >= 0. && self.previous.is_none_or(|(last, _)| pts > last) {
                (pts, "video_pts")
            } else if fps.is_finite() && fps > 0. {
                (
                    self.previous.map_or(0., |(last, _)| last + 1000. / fps),
                    "video_fps_estimate",
                )
            } else {
                (
                    self.previous.map_or(0., |(last, t)| last + decoded - t),
                    "host_decode_elapsed",
                )
            };
        self.previous = Some((stamp, decoded));
        (stamp, basis)
    }
}
pub fn worker() -> Result<()> {
    let mut input = io::stdin().lock();
    let cfg: Config = serde_json::from_value(ipc::read_json(&mut input, 65536)?)?;
    anyhow::ensure!(
        !cfg.source.is_empty() && [0, 90, 180, 270].contains(&cfg.rotation),
        "invalid source config"
    );
    let mut capture = if cfg.source.bytes().all(|b| b.is_ascii_digit()) {
        videoio::VideoCapture::new(cfg.source.parse::<i32>()?, videoio::CAP_ANY)?
    } else {
        videoio::VideoCapture::from_file(&cfg.source, videoio::CAP_ANY)?
    };
    anyhow::ensure!(capture.is_opened()?, "input could not be opened");
    let _ = capture.set(videoio::CAP_PROP_BUFFERSIZE, 1.);
    let mut out = io::stdout().lock();
    out.write_all(b"{\"ready\":true,\"native_video_protocol\":1}\n")?;
    out.flush()?;
    let start = Instant::now();
    let mut sequence = 0;
    let mut clock = MediaClock::default();
    loop {
        let request = ipc::read_json(&mut input, 1024)?;
        anyhow::ensure!(request["next"] == true, "unknown decoder request");
        let mut image = core::Mat::default();
        if !capture.read(&mut image)? || image.empty() {
            out.write_all(b"{\"eof\":true}\n")?;
            out.flush()?;
            return Ok(());
        }
        sequence += 1;
        let decoded = start.elapsed().as_secs_f64() * 1000.;
        let (timestamp_ms, basis) = if cfg.recording {
            clock.stamp(
                capture.get(videoio::CAP_PROP_POS_MSEC)?,
                capture.get(videoio::CAP_PROP_FPS)?,
                decoded,
            )
        } else {
            (decoded, "host_decode_time")
        };
        if cfg.rotation != 0 {
            let mut rotated = core::Mat::default();
            core::rotate(
                &image,
                &mut rotated,
                match cfg.rotation {
                    90 => core::ROTATE_90_CLOCKWISE,
                    180 => core::ROTATE_180,
                    _ => core::ROTATE_90_COUNTERCLOCKWISE,
                },
            )?;
            image = rotated;
        }
        anyhow::ensure!(
            image.typ() == core::CV_8UC3,
            "decoder must produce BGR uint8"
        );
        if !image.is_continuous() {
            image = image.try_clone()?;
        }
        let header = Header {
            sequence,
            timestamp_ms,
            timestamp_basis: basis.into(),
            width: image.cols().try_into()?,
            height: image.rows().try_into()?,
        };
        let size = header.validate()?;
        let bytes = image.data_bytes().context("read frame pixels")?;
        anyhow::ensure!(bytes.len() == size, "pixel buffer size mismatch");
        out.write_all(&ipc::json_line(&header)?)?;
        out.write_all(bytes)?;
        out.flush()?;
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pts_fps_and_decode_fallback_match_existing_source() {
        let mut clock = MediaClock::default();
        assert_eq!(clock.stamp(0., 60., 100.), (0., "video_pts"));
        assert_eq!(
            clock.stamp(0., 60., 120.),
            (1000. / 60., "video_fps_estimate")
        );
        let (n, b) = clock.stamp(f64::NAN, 0., 130.);
        assert_eq!(b, "host_decode_elapsed");
        assert!((n - (1000. / 60. + 10.)).abs() < 1e-10);
        assert_eq!(clock.stamp(50., 60., 150.), (50., "video_pts"));
    }
}
