//! Offline metrics use f64 and the shared assignment core, without ONNX loading.
use crate::{assignment, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    io::{self, BufRead, Read, Write},
};

#[derive(Clone, Deserialize)]
struct Track {
    code: String,
    xyxy: [f64; 4],
    #[serde(default)]
    id: Value,
}
#[derive(Deserialize)]
struct Frame {
    frame_sequence: u64,
    #[serde(default)]
    replay_pass: u64,
    tracks: Vec<Track>,
    session_id: Value,
    frame_timestamp_ms: Option<f64>,
    image_size_px: Option<[u64; 2]>,
    inference_ms: Option<f64>,
    processing_ms: Option<f64>,
    model_hash: Option<String>,
}
#[derive(Deserialize)]
struct Request {
    operation: String,
    threshold: f64,
    #[serde(default)]
    warmup: usize,
    #[serde(default)]
    reference: Vec<Frame>,
    #[serde(default)]
    predictions: Vec<Frame>,
    #[serde(default)]
    actual: Vec<Track>,
    #[serde(default)]
    predicted: Vec<Track>,
}
fn validate_tracks(tracks: &[Track]) -> Result<()> {
    let mut ids = HashSet::new();
    for t in tracks {
        let [x1, y1, x2, y2] = t.xyxy;
        if t.code.is_empty()
            || !t.xyxy.iter().all(|v| v.is_finite())
            || x1 < 0.
            || y1 < 0.
            || x2 <= x1
            || y2 <= y1
        {
            return Err("Invalid evaluation box/class".into());
        }
        if !t.id.is_null() {
            if !(t.id.is_i64() || t.id.is_u64() || t.id.as_str().is_some_and(|s| !s.is_empty()))
                || !ids.insert(t.id.to_string())
            {
                return Err("Invalid evaluation ID".into());
            }
        }
    }
    Ok(())
}
fn overlap(a: [f64; 4], b: [f64; 4]) -> f64 {
    let intersection =
        (a[2].min(b[2]) - a[0].max(b[0])).max(0.) * (a[3].min(b[3]) - a[1].max(b[1])).max(0.);
    let union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection;
    if union == 0. {
        0.
    } else {
        intersection / union
    }
}
fn matching(a: &[Track], b: &[Track], threshold: f64) -> Result<Vec<(usize, usize, f64)>> {
    validate_tracks(a)?;
    validate_tracks(b)?;
    // Bound quadratic allocation for malformed or impractically dense frames.
    if a.len()
        .checked_mul(b.len() + a.len())
        .is_none_or(|cells| cells > 16_000_000)
    {
        return Err("Evaluation frame exceeds assignment limit".into());
    }
    let reward = a.len().min(b.len()) as f64 + 1.;
    let costs: Vec<Vec<f64>> = a
        .iter()
        .map(|r| {
            b.iter()
                .map(|p| {
                    let iou = overlap(r.xyxy, p.xyxy);
                    if r.code == p.code && iou >= threshold {
                        -reward - iou
                    } else {
                        1e6
                    }
                })
                .collect()
        })
        .collect();
    Ok(assignment::solve(&costs, b.len(), 0.)
        .into_iter()
        .filter(|&(r, p)| costs[r][p] < 0.)
        .map(|(r, p)| (r, p, overlap(a[r].xyxy, b[p].xyxy)))
        .collect())
}
#[derive(Default)]
struct Counts {
    tp: usize,
    fp: usize,
    misses: usize,
    switches: usize,
    pending: usize,
}
impl Counts {
    fn rates(&self, identities: bool) -> Value {
        let mut row = json!({"tp":self.tp,"fp":self.fp,"fn":self.misses,
            "precision":if self.tp+self.fp>0 {Some(self.tp as f64/(self.tp+self.fp) as f64)}else{None},
            "recall":if self.tp+self.misses>0 {Some(self.tp as f64/(self.tp+self.misses) as f64)}else{None}});
        if identities {
            row["id_switches"] = json!(self.switches);
            row["matched_without_id"] = json!(self.pending);
        }
        row
    }
}
type Index = BTreeMap<(u64, u64), Frame>;
fn index(frames: Vec<Frame>) -> Result<Index> {
    let mut indexed = BTreeMap::new();
    for f in frames {
        validate_tracks(&f.tracks)?;
        if f.frame_timestamp_ms
            .is_some_and(|t| !t.is_finite() || t < 0.)
        {
            return Err("Invalid frame time".into());
        }
        if indexed
            .insert((f.replay_pass, f.frame_sequence), f)
            .is_some()
        {
            return Err("Duplicate frame identity".into());
        }
    }
    Ok(indexed)
}
fn timing(mut values: Vec<f64>) -> Result<Value> {
    if values.iter().any(|v| !v.is_finite() || *v < 0.) {
        return Err("Invalid timing".into());
    }
    if values.is_empty() {
        return Ok(json!({"samples":0,"mean_ms":null,"p50_ms":null,"p95_ms":null}));
    }
    values.sort_by(f64::total_cmp);
    let n = values.len();
    Ok(
        json!({"samples":n,"mean_ms":values.iter().sum::<f64>()/n as f64,
        "p50_ms":values[((n as f64*0.5).ceil() as usize).max(1)-1],
        "p95_ms":values[((n as f64*0.95).ceil() as usize).max(1)-1]}),
    )
}
fn evaluate(req: Request) -> Result<Value> {
    let reference = index(req.reference)?;
    let predictions = index(req.predictions)?;
    if reference.is_empty() {
        return Err("Reference must contain frames".into());
    }
    let mut totals = Counts::default();
    let mut classes: BTreeMap<String, Counts> = BTreeMap::new();
    let mut last: HashMap<String, String> = HashMap::new();
    let mut details = Vec::new();
    let (mut overlap_sum, mut overlap_count) = (0., 0usize);
    let keys: BTreeSet<_> = reference
        .keys()
        .chain(predictions.keys())
        .copied()
        .collect();
    for key in keys {
        let actual = reference.get(&key);
        let predicted = predictions.get(&key);
        if let (Some(a), Some(b)) = (actual, predicted) {
            if let (Some(x), Some(y)) = (a.frame_timestamp_ms, b.frame_timestamp_ms) {
                if (x - y).abs() > 0.01 {
                    return Err("Frame timestamp mismatch".into());
                }
            }
            if a.image_size_px.is_some()
                && b.image_size_px.is_some()
                && a.image_size_px != b.image_size_px
            {
                return Err("Frame dimension mismatch".into());
            }
        }
        let a = actual.map(|f| f.tracks.as_slice()).unwrap_or(&[]);
        let b = predicted.map(|f| f.tracks.as_slice()).unwrap_or(&[]);
        let pairs = matching(a, b, req.threshold)?;
        let mut ma = HashSet::new();
        let mut mb = HashSet::new();
        let (mut switches, mut pending) = (0, 0);
        for &(r, p, iou) in &pairs {
            ma.insert(r);
            mb.insert(p);
            overlap_sum += iou;
            overlap_count += 1;
            if b[p].id.is_null() {
                pending += 1;
            } else if !a[r].id.is_null() {
                let identity = json!([key.0, actual.unwrap().session_id, a[r].id]).to_string();
                let next = json!([predicted.unwrap().session_id, b[p].id]).to_string();
                if last.get(&identity).is_some_and(|old| old != &next) {
                    switches += 1;
                }
                last.insert(identity, next);
            }
        }
        let (tp, fp, misses) = (pairs.len(), b.len() - pairs.len(), a.len() - pairs.len());
        totals.tp += tp;
        totals.fp += fp;
        totals.misses += misses;
        totals.switches += switches;
        totals.pending += pending;
        details.push(
            json!({"replay_pass":key.0,"frame":key.1,"tp":tp,"fp":fp,"fn":misses,
            "id_switches":switches,"matched_without_id":pending}),
        );
        for (r, t) in a.iter().enumerate() {
            let c = classes.entry(t.code.clone()).or_default();
            if ma.contains(&r) {
                c.tp += 1;
            } else {
                c.misses += 1;
            }
        }
        for (p, t) in b.iter().enumerate() {
            if !mb.contains(&p) {
                classes.entry(t.code.clone()).or_default().fp += 1;
            }
        }
    }
    let model_hashes: BTreeSet<_> = predictions
        .values()
        .filter_map(|f| f.model_hash.as_ref())
        .filter(|s| !s.is_empty())
        .collect();
    let inference = timing(
        predictions
            .values()
            .skip(req.warmup)
            .filter_map(|f| f.inference_ms)
            .collect(),
    )?;
    let processing = timing(
        predictions
            .values()
            .skip(req.warmup)
            .filter_map(|f| f.processing_ms)
            .collect(),
    )?;
    let per_class: BTreeMap<_, _> = classes.iter().map(|(k, v)| (k, v.rates(false))).collect();
    Ok(
        json!({"schema_version":1,"metric":"class_aware_iou_assignment","iou_threshold":req.threshold,
        "model_hashes":model_hashes,"reference_frames":reference.len(),"predicted_frames":predictions.len(),
        "missing_frames":reference.keys().filter(|k|!predictions.contains_key(k)).count(),
        "extra_frames":predictions.keys().filter(|k|!reference.contains_key(k)).count(),
        "totals":totals.rates(true),"mean_matched_iou":if overlap_count>0 {Some(overlap_sum/overlap_count as f64)}else{None},
        "per_class":per_class,"timing":{"inference":inference,"processing":processing},
        "timing_warmup_frames":req.warmup,"frames":details}),
    )
}
pub fn worker() -> Result<()> {
    let mut input = io::stdin().lock();
    let mut out = io::stdout().lock();
    loop {
        let mut line = Vec::new();
        (&mut input)
            .take(64 * 1024 * 1024 + 1)
            .read_until(b'\n', &mut line)?;
        if line.is_empty() {
            break;
        }
        if line.len() > 64 * 1024 * 1024 || !line.ends_with(b"\n") {
            return Err("Evaluation request limit exceeded".into());
        }
        let result = (|| -> Result<Value> {
            let req: Request = serde_json::from_slice(&line)?;
            if !req.threshold.is_finite() || req.threshold <= 0. || req.threshold > 1. {
                return Err("Invalid IoU threshold".into());
            }
            match req.operation.as_str() {
                "evaluate" => evaluate(req),
                "match" => Ok(serde_json::to_value(matching(
                    &req.actual,
                    &req.predicted,
                    req.threshold,
                )?)?),
                _ => Err("Unknown evaluation operation".into()),
            }
        })();
        let response = match result {
            Ok(data) => json!({"ok":true,"data":data}),
            Err(e) => json!({"ok":false,"error":e.to_string()}),
        };
        serde_json::to_writer(&mut out, &response)?;
        writeln!(out)?;
        out.flush()?;
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    fn t(b: [f64; 4]) -> Track {
        Track {
            xyxy: b,
            code: "R".into(),
            id: Value::Null,
        }
    }
    #[test]
    fn global_matching_and_zero_cost_dummies() {
        let pairs = matching(
            &[t([0., 0., 10., 10.]), t([4., 0., 14., 10.])],
            &[t([2., 0., 12., 10.]), t([0., 0., 6., 10.])],
            0.5,
        )
        .unwrap();
        assert_eq!(
            pairs.iter().map(|&(r, p, _)| (r, p)).collect::<Vec<_>>(),
            vec![(1, 0), (0, 1)]
        );
        assert!(
            matching(&[t([0., 0., 1., 1.])], &[t([2., 2., 3., 3.])], 0.5)
                .unwrap()
                .is_empty()
        );
        assert!(matching(&[], &[t([0., 0., 1., 1.])], 0.5)
            .unwrap()
            .is_empty());
    }
}
