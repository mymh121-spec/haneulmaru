use crate::{ort::Session, Result};
use serde::{Deserialize, Serialize};
use std::io::{self, BufRead, Read, Write};

struct Fit {
    w: usize,
    h: usize,
    nw: usize,
    nh: usize,
    left: usize,
    top: usize,
    scale: f32,
}
impl Fit {
    fn new(w: usize, h: usize, size: usize) -> Self {
        let scale = size as f32 / w.max(h) as f32;
        let nw = (w as f32 * scale).round().max(1.) as usize;
        let nh = (h as f32 * scale).round().max(1.) as usize;
        Self {
            w,
            h,
            nw,
            nh,
            left: (size - nw) / 2,
            top: (size - nh) / 2,
            scale,
        }
    }
    // Upright BGR bytes -> RGB NCHW, with centered bilinear letterbox.
    fn input(&self, bgr: &[u8], size: usize) -> Vec<f32> {
        let mut tensor = vec![114. / 255.; 3 * size * size];
        let lookup = |i: usize, src: usize, dst: usize| {
            let coord =
                (((i as f32 + 0.5) * src as f32 / dst as f32) - 0.5).clamp(0., (src - 1) as f32);
            (
                coord as usize,
                (coord as usize + 1).min(src - 1),
                coord.fract(),
            )
        };
        let xs: Vec<_> = (0..self.nw).map(|x| lookup(x, self.w, self.nw)).collect();
        for y in 0..self.nh {
            let (y0, y1, dy) = lookup(y, self.h, self.nh);
            for (x, &(x0, x1, dx)) in xs.iter().enumerate() {
                let idx = (y + self.top) * size + x + self.left;
                for c in 0..3 {
                    let pixel = |yy, xx| bgr[(yy * self.w + xx) * 3 + 2 - c] as f32;
                    let top = pixel(y0, x0) * (1. - dx) + pixel(y0, x1) * dx;
                    let bot = pixel(y1, x0) * (1. - dx) + pixel(y1, x1) * dx;
                    tensor[c * size * size + idx] = (top * (1. - dy) + bot * dy).round() / 255.;
                }
            }
        }
        tensor
    }
}
#[derive(Serialize, Debug)]
struct Detection<'a> {
    code: &'a str,
    confidence: f32,
    xyxy: [f32; 4],
}
pub(crate) fn overlap(a: [f32; 4], b: [f32; 4]) -> f32 {
    let intersection =
        (a[2].min(b[2]) - a[0].max(b[0])).max(0.) * (a[3].min(b[3]) - a[1].max(b[1])).max(0.);
    intersection / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection)
}
#[derive(Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Layout {
    YoloRaw,
    YoloRawRows,
    YoloObjectnessRows,
    XyxyScoreClass,
}

#[derive(Deserialize)]
struct DetectorSpec {
    classes: Vec<String>,
    output_layout: Layout,
    agnostic_nms: bool,
}
impl DetectorSpec {
    fn legacy() -> Self {
        Self {
            classes: ["R", "B", "G"].map(str::to_owned).to_vec(),
            output_layout: Layout::YoloRaw,
            agnostic_nms: true,
        }
    }

    fn decode(
        &self,
        fit: &Fit,
        shape: [usize; 2],
        data: &[f32],
        threshold: f32,
        iou: f32,
    ) -> Result<Vec<Detection<'_>>> {
        let class_count = self.classes.len();
        let (fields, count) = match self.output_layout {
            Layout::YoloRaw => (4 + class_count, shape[1]),
            Layout::YoloRawRows => (4 + class_count, shape[0]),
            Layout::YoloObjectnessRows => (5 + class_count, shape[0]),
            Layout::XyxyScoreClass => (6, shape[0]),
        };
        let channels_first = self.output_layout == Layout::YoloRaw;
        let expected = if channels_first {
            [fields, count]
        } else {
            [count, fields]
        };
        if shape != expected || count > 1_000_000 {
            return Err(format!("Output [1,{},{}] incompatible with profile ({} classes, expected {} fields per candidate)",shape[0],shape[1],class_count,fields).into());
        }
        if !data.iter().all(|x| x.is_finite()) {
            return Err("Nonfinite model output".into());
        }
        let mut candidates = Vec::new();
        for i in 0..count {
            let col = |k: usize| {
                if channels_first {
                    data[k * count + i]
                } else {
                    data[i * fields + k]
                }
            };
            let (class, score, coords) = if self.output_layout == Layout::XyxyScoreClass {
                let score = probability(col(4))?;
                if score == 0. {
                    continue;
                } // padding in fixed-size exported NMS results
                let id = col(5);
                if id < 0. || id.fract() != 0. || id as usize >= class_count {
                    return Err("Output class ID is outside the model profile".into());
                }
                (id as usize, score, [col(0), col(1), col(2), col(3)])
            } else {
                let has_objectness = self.output_layout == Layout::YoloObjectnessRows;
                let offset = if has_objectness { 5 } else { 4 };
                let objectness = if has_objectness {
                    probability(col(4))?
                } else {
                    1.
                };
                let mut best = 0;
                let mut score = probability(col(offset))?;
                for k in 1..class_count {
                    let value = probability(col(offset + k))?;
                    if value > score {
                        best = k;
                        score = value;
                    }
                }
                (
                    best,
                    score * objectness,
                    [
                        col(0) - col(2) / 2.,
                        col(1) - col(3) / 2.,
                        col(0) + col(2) / 2.,
                        col(1) + col(3) / 2.,
                    ],
                )
            };
            if score < threshold {
                continue;
            }
            let x = |v: f32| ((v - fit.left as f32) / fit.scale).clamp(0., fit.w as f32);
            let y = |v: f32| ((v - fit.top as f32) / fit.scale).clamp(0., fit.h as f32);
            let b = [x(coords[0]), y(coords[1]), x(coords[2]), y(coords[3])];
            if b[2] > b[0] && b[3] > b[1] {
                candidates.push(Detection {
                    code: &self.classes[class],
                    confidence: score,
                    xyxy: b,
                });
            }
        }
        candidates.sort_by(|a, b| b.confidence.total_cmp(&a.confidence));
        if self.output_layout == Layout::XyxyScoreClass {
            return Ok(candidates);
        }
        let mut keep: Vec<Detection> = Vec::new();
        for d in candidates {
            if keep.iter().all(|old| {
                (!self.agnostic_nms && old.code != d.code) || overlap(d.xyxy, old.xyxy) <= iou
            }) {
                keep.push(d)
            }
        }
        Ok(keep)
    }
}

fn probability(value: f32) -> Result<f32> {
    if (0.0..=1.0).contains(&value) {
        Ok(value)
    } else {
        Err("Expected probabilities between 0 and 1".into())
    }
}

pub fn worker(args: &[String], read_profile: bool) -> Result<()> {
    let size = args[1].parse::<usize>()?;
    let conf = args[2].parse::<f32>()?;
    let iou = args[3].parse::<f32>()?;
    if !(32..=2048).contains(&size)
        || size % 32 != 0
        || !(0.0..=1.0).contains(&conf)
        || !(0.0..=1.0).contains(&iou)
    {
        return Err("Invalid detector configuration".into());
    }
    let mut input = io::stdin().lock();
    let spec = if read_profile {
        let mut line = String::new();
        input.read_line(&mut line)?;
        serde_json::from_str::<DetectorSpec>(&line)?
    } else {
        DetectorSpec::legacy()
    };
    if spec.classes.is_empty()
        || spec
            .classes
            .iter()
            .any(|s| s.trim().is_empty() || s == "UNKNOWN")
    {
        return Err("Invalid profile classes".into());
    }
    let session = Session::load(&args[0], &args[4], &args[5])?;
    let mut out = io::stdout().lock();
    writeln!(out, "ready")?;
    out.flush()?;
    loop {
        let mut header = [0u8; 8];
        if input.read(&mut header[..1])? == 0 {
            break;
        }
        input.read_exact(&mut header[1..])?;
        let w = u32::from_le_bytes(header[..4].try_into()?) as usize;
        let h = u32::from_le_bytes(header[4..].try_into()?) as usize;
        let bytes = w
            .checked_mul(h)
            .and_then(|n| n.checked_mul(3))
            .filter(|n| *n > 0 && *n <= 64 * 1024 * 1024)
            .ok_or("Invalid frame dimensions")?;
        let mut bgr = vec![0; bytes];
        input.read_exact(&mut bgr)?;
        let fit = Fit::new(w, h, size);
        let (shape, data) = session.run(&mut fit.input(&bgr, size), size)?;
        serde_json::to_writer(&mut out, &spec.decode(&fit, shape, &data, conf, iou)?)?;
        writeln!(out)?;
        out.flush()?;
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reject_mismatched_class_count_and_out_of_range_id() {
        let fit = Fit::new(32, 32, 32);
        let mut spec = DetectorSpec::legacy();
        assert!(spec.decode(&fit, [6, 1], &[0.; 6], 0.1, 0.7).is_err());
        spec.output_layout = Layout::XyxyScoreClass;
        assert!(spec
            .decode(&fit, [1, 6], &[0., 0., 5., 5., 0.9, 3.], 0.1, 0.7)
            .is_err());
        assert!(spec.decode(&fit, [0, 6], &[], 0.1, 0.7).unwrap().is_empty());
    }
    #[test]
    fn preprocess_and_suppress_across_classes() {
        let fit = Fit::new(2, 1, 4);
        let tensor = fit.input(&[0, 0, 255, 0, 0, 255], 4);
        assert_eq!(tensor[4], 1.);
        assert_eq!(tensor[16 + 4], 0.);
        assert_eq!(tensor[0], 114. / 255.);
        let spec = DetectorSpec::legacy();
        let detections = spec
            .decode(
                &fit,
                [7, 2],
                &[2., 2., 2., 2., 4., 4., 2., 2., 0.9, 0.1, 0.0, 0.8, 0.0, 0.0],
                0.1,
                0.7,
            )
            .unwrap();
        assert_eq!(detections.len(), 1);
        assert_eq!(detections[0].xyxy, [0., 0., 2., 1.]);
        assert_eq!(detections[0].code, "R");
    }
}
