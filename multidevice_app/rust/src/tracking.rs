use crate::{vision::overlap, Result};
use serde::Deserialize;
use std::io::{self, BufRead, Write};

const HOLD_MS: f64 = 750.;
const START_CONFIDENCE: f32 = 0.4;
const UNMATCHED: f64 = 1.05;
const FORBIDDEN: f64 = 1e6;

#[derive(Clone, Deserialize)]
struct Observation {
    xyxy: [f32; 4],
    code: String,
    confidence: f32,
}

#[derive(Deserialize)]
struct Frame {
    timestamp_ms: f64,
    detections: Vec<Observation>,
}

fn center(b: [f32; 4]) -> [f32; 2] {
    [(b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5]
}

struct Track {
    id: u64,
    last: Observation,
    velocity: Option<[f32; 2]>,
    seen_ms: f64,
}

impl Track {
    fn cost(&self, obs: &Observation, now: f64) -> f64 {
        let mut predicted = self.last.xyxy;
        let dt = (now - self.seen_ms) as f32;
        if let Some([vx, vy]) = self.velocity {
            predicted[0] += vx * dt;
            predicted[2] += vx * dt;
            predicted[1] += vy * dt;
            predicted[3] += vy * dt;
        }
        let a = center(predicted);
        let b = center(obs.xyxy);
        let mut distance = [0.; 2];
        for axis in 0..2 {
            let before = predicted[axis + 2] - predicted[axis];
            let after = obs.xyxy[axis + 2] - obs.xyxy[axis];
            if before.min(after) * 2.5 < before.max(after) {
                return FORBIDDEN;
            }
            distance[axis] = (a[axis] - b[axis]) / ((before + after) * 0.5).max(1.);
        }
        let d = distance[0].hypot(distance[1]);
        if d > 1.6 {
            return FORBIDDEN;
        }
        let changed_class =
            self.last.code != obs.code && obs.code != "UNKNOWN" && self.last.code != "UNKNOWN";
        f64::from(
            0.55 * d / 1.6
                + 0.35 * (1. - overlap(predicted, obs.xyxy))
                + if changed_class { 0.10 } else { 0. },
        )
    }

    fn observe(&mut self, obs: Observation, now: f64) {
        let dt = (now - self.seen_ms) as f32;
        if dt >= 1. {
            let a = center(self.last.xyxy);
            let b = center(obs.xyxy);
            let measured = [(b[0] - a[0]) / dt, (b[1] - a[1]) / dt];
            self.velocity = Some(match self.velocity {
                None => measured,
                Some(old) => [
                    0.65 * measured[0] + 0.35 * old[0],
                    0.65 * measured[1] + 0.35 * old[1],
                ],
            });
        }
        let code = if obs.confidence >= START_CONFIDENCE && obs.code != "UNKNOWN" {
            obs.code
        } else {
            self.last.code.clone()
        };
        self.last = Observation { code, ..obs };
        self.seen_ms = now;
    }
}

// Rectangular minimum-cost assignment. Each row can use a dummy column to
// stay unmatched, so a gated pair never displaces a valid association.
fn assignment(costs: &[Vec<f64>], real_cols: usize) -> Vec<(usize, usize)> {
    let rows = costs.len();
    let cols = real_cols + rows;
    let mut row_price = vec![0.; rows + 1];
    let mut col_price = vec![0.; cols + 1];
    let mut owner = vec![0; cols + 1];
    let mut parent = vec![0; cols + 1];
    for row in 1..=rows {
        owner[0] = row;
        let mut j = 0;
        let mut used = vec![false; cols + 1];
        let mut slack = vec![f64::INFINITY; cols + 1];
        loop {
            used[j] = true;
            let r = owner[j];
            let mut best = f64::INFINITY;
            let mut next = 0;
            for k in 1..=cols {
                if used[k] {
                    continue;
                }
                let cost = costs[r - 1].get(k - 1).copied().unwrap_or(UNMATCHED);
                let reduced = cost - row_price[r] - col_price[k];
                if reduced < slack[k] {
                    slack[k] = reduced;
                    parent[k] = j
                }
                if slack[k] < best {
                    best = slack[k];
                    next = k
                }
            }
            for k in 0..=cols {
                if used[k] {
                    row_price[owner[k]] += best;
                    col_price[k] -= best
                } else {
                    slack[k] -= best
                }
            }
            j = next;
            if owner[j] == 0 {
                break;
            }
        }
        while j != 0 {
            let previous = parent[j];
            owner[j] = owner[previous];
            j = previous;
        }
    }
    (1..=real_cols)
        .filter(|&c| owner[c] != 0)
        .map(|c| (owner[c] - 1, c - 1))
        .collect()
}

#[derive(Default)]
struct Tracker {
    tracks: Vec<Track>,
    next_id: u64,
    last_ms: Option<f64>,
}

impl Tracker {
    fn associate(
        &self,
        indices: &[usize],
        candidates: &[usize],
        frame: &Frame,
    ) -> Vec<(usize, usize)> {
        if indices.is_empty() || candidates.is_empty() {
            return Vec::new();
        }
        // Assign the smaller side as rows when many tracks are temporarily missing.
        let transpose = indices.len() > candidates.len();
        let (rows, cols) = if transpose {
            (candidates, indices)
        } else {
            (indices, candidates)
        };
        let costs: Vec<_> = rows
            .iter()
            .map(|&r| {
                cols.iter()
                    .map(|&c| {
                        let (t, d) = if transpose { (c, r) } else { (r, c) };
                        self.tracks[t].cost(&frame.detections[d], frame.timestamp_ms)
                    })
                    .collect()
            })
            .collect();
        assignment(&costs, cols.len())
            .into_iter()
            .map(|(r, c)| {
                if transpose {
                    (cols[c], rows[r])
                } else {
                    (rows[r], cols[c])
                }
            })
            .collect()
    }

    fn update(&mut self, frame: &Frame) -> Result<Vec<Option<u64>>> {
        let now = frame.timestamp_ms;
        if !now.is_finite() || now < 0. || self.last_ms.is_some_and(|t| now < t) {
            return Err("Invalid or out-of-order frame time".into());
        }
        for d in &frame.detections {
            if d.code.trim().is_empty()
                || !(0.0..=1.).contains(&d.confidence)
                || !d.xyxy.iter().all(|x| x.is_finite())
                || d.xyxy[2] <= d.xyxy[0]
                || d.xyxy[3] <= d.xyxy[1]
            {
                return Err("Invalid tracker detection".into());
            }
        }
        self.last_ms = Some(now);
        self.tracks.retain(|t| now - t.seen_ms <= HOLD_MS);
        let (strong, weak): (Vec<_>, Vec<_>) = (0..frame.detections.len())
            .partition(|&i| frame.detections[i].confidence >= START_CONFIDENCE);
        let mut used = vec![false; self.tracks.len()];
        let mut ids = vec![None; frame.detections.len()];
        for group in [&strong, &weak] {
            let available: Vec<_> = (0..used.len()).filter(|&i| !used[i]).collect();
            for (t, d) in self.associate(&available, group, frame) {
                self.tracks[t].observe(frame.detections[d].clone(), now);
                ids[d] = Some(self.tracks[t].id);
                used[t] = true;
            }
        }
        for d in strong {
            if ids[d].is_none() {
                self.next_id += 1;
                self.tracks.push(Track {
                    id: self.next_id,
                    last: frame.detections[d].clone(),
                    velocity: None,
                    seen_ms: now,
                });
                ids[d] = Some(self.next_id);
            }
        }
        Ok(ids)
    }
}

pub fn worker() -> Result<()> {
    let mut tracker = Tracker::default();
    let mut out = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let frame: Frame = serde_json::from_str(&line?)?;
        serde_json::to_writer(&mut out, &tracker.update(&frame)?)?;
        writeln!(out)?;
        out.flush()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn assign_globally_and_allow_unmatched() {
        let costs = vec![
            vec![0.18, 0.30],
            vec![0.20, FORBIDDEN],
            vec![FORBIDDEN, FORBIDDEN],
        ];
        assert_eq!(assignment(&costs, 2), vec![(1, 0), (0, 1)]);
    }
    #[test]
    fn weak_hits_can_continue_but_do_not_start_tracks() {
        let mut tracker = Tracker::default();
        let update = |tracker: &mut Tracker, ms, confidence| {
            tracker
                .update(&Frame {
                    timestamp_ms: ms,
                    detections: vec![Observation {
                        xyxy: [0., 0., 10., 10.],
                        code: "R".into(),
                        confidence,
                    }],
                })
                .unwrap()[0]
        };
        assert_eq!(update(&mut tracker, 0., 0.2), None);
        assert_eq!(update(&mut tracker, 50., 0.9), Some(1));
        assert_eq!(update(&mut tracker, 100., 0.2), Some(1));
        assert_eq!(update(&mut tracker, 851., 0.2), None);
        assert_eq!(update(&mut tracker, 900., 0.9), Some(2));
    }
}
