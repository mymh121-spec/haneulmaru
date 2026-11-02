use crate::Result;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    env, fs,
    net::UdpSocket,
    time::{Duration, Instant},
};

#[derive(Deserialize, Serialize)]
struct Sample {
    v: u8,
    t_ms: u32,
    ax: f64,
    ay: f64,
    az: f64,
    gx: f64,
    gy: f64,
    gz: f64,
}

fn sample(raw: Value) -> Option<Sample> {
    let data = if raw["type"] == "imu" && raw["schema"] == 1 {
        let axes = &raw["imu"];
        json!({"v":1,"t_ms":raw["ts_ms"],"ax":axes["ax_g"],"ay":axes["ay_g"],"az":axes["az_g"],
               "gx":axes["gx_dps"],"gy":axes["gy_dps"],"gz":axes["gz_dps"]})
    } else {
        raw
    };
    let s: Sample = serde_json::from_value(data).ok()?;
    (s.v == 1
        && [s.ax, s.ay, s.az, s.gx, s.gy, s.gz]
            .iter()
            .all(|x| x.is_finite()))
    .then_some(s)
}

#[derive(Deserialize)]
struct Model {
    format: String,
    schema_version: u8,
    units: String,
    directions: HashMap<String, [f64; 3]>,
    rate_threshold_dps: f64,
    similarity_threshold: f64,
}
impl Model {
    fn load(s: &str) -> Result<Self> {
        let mut m: Self = serde_json::from_str(s)?;
        if m.format != "nu40-imu-direction-model"
            || m.units != "degrees_per_second"
            || m.schema_version != 1
            || m.rate_threshold_dps <= 0.
            || !(0.0..=1.0).contains(&m.similarity_threshold)
            || m.directions.len() != 4
        {
            return Err("Invalid direction model".into());
        }
        for k in ["up", "down", "left", "right"] {
            let v = m.directions.get_mut(k).ok_or("missing direction")?;
            let n = v[0].hypot(v[1]).hypot(v[2]);
            if !n.is_finite() || n == 0. {
                return Err("Invalid direction vector".into());
            }
            for x in v {
                *x /= n;
            }
        }
        Ok(m)
    }
    fn classify(&self, s: &Sample) -> (f64, Option<&str>, Option<f64>) {
        let rate = s.gx.hypot(s.gy).hypot(s.gz);
        if !rate.is_finite() || rate < self.rate_threshold_dps {
            return (rate, None, None);
        }
        let mut scores: Vec<_> = self
            .directions
            .iter()
            .map(|(k, v)| {
                (
                    (s.gx / rate) * v[0] + (s.gy / rate) * v[1] + (s.gz / rate) * v[2],
                    k.as_str(),
                )
            })
            .collect();
        scores.sort_by(|a, b| b.0.total_cmp(&a.0));
        let (score, k) = scores[0];
        if score >= self.similarity_threshold && score - scores[1].0 >= 0.08 {
            (rate, Some(k), Some(score))
        } else {
            (rate, None, None)
        }
    }
}

#[derive(Deserialize)]
struct Envelope {
    key: String,
    session: String,
    seq: u64,
    packet: Value,
}
#[derive(Default)]
struct Peer {
    current: Option<(String, u64, Instant)>,
    retired: Vec<String>,
}
impl Peer {
    fn accept(&mut self, session: &str, seq: u64, now: Instant) -> bool {
        if self.retired.iter().any(|s| s == session) {
            return false;
        }
        if let Some((id, last, last_seen)) = &self.current {
            if id == session && seq <= *last {
                return false;
            }
            if id != session {
                // Wait for the active USB stream to expire before switching sessions.
                if now.duration_since(*last_seen) <= Duration::from_millis(350) {
                    return false;
                }
                if self.retired.len() == 32 {
                    self.retired.remove(0);
                }
                self.retired.push(id.clone());
            }
        }
        self.current = Some((session.to_owned(), seq, now));
        true
    }
}

#[cfg(windows)]
fn key_bytes() -> Result<[u8; 8]> {
    #[link(name = "bcrypt")]
    extern "system" {
        fn BCryptGenRandom(
            algorithm: *mut std::ffi::c_void,
            dst: *mut u8,
            len: u32,
            flags: u32,
        ) -> i32;
    }
    let mut bytes = [0; 8];
    if unsafe { BCryptGenRandom(std::ptr::null_mut(), bytes.as_mut_ptr(), 8, 2) } != 0 {
        return Err("OS random generator failed".into());
    }
    Ok(bytes)
}
#[cfg(not(windows))]
fn key_bytes() -> Result<[u8; 8]> {
    use std::io::Read;
    let mut bytes = [0; 8];
    fs::File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes)
}

fn event(
    data: &[u8],
    key: &str,
    model: &Model,
    peer: &mut Peer,
    arrived: Instant,
) -> Option<Value> {
    if data.len() > 4096 {
        return None;
    }
    let e: Envelope = serde_json::from_slice(data).ok()?;
    if e.key != key || e.session.is_empty() || e.session.len() > 64 {
        return None;
    }
    let s = sample(e.packet)?;
    let (rate, direction, similarity) = model.classify(&s);
    if !rate.is_finite() || !peer.accept(&e.session, e.seq, arrived) {
        return None;
    }
    Some(
        json!({"event":"sample","source":"USB/Wi-Fi","session":e.session,"seq":e.seq,
        "sample":s,"direction":direction,"rate_dps":rate,"similarity":similarity}),
    )
}

pub fn listen(bind: &str, model: Option<&String>) -> Result<()> {
    let model = Model::load(&match model {
        Some(p) => fs::read_to_string(p)?,
        None => include_str!("../../assets/nu40-imu-direction-training.json").to_owned(),
    })?;
    let key = match env::var("HANEULMARU_PAIR_KEY") {
        Ok(k) if (8..=64).contains(&k.len()) => k,
        Ok(_) => return Err("Pair key must be 8 to 64 characters".into()),
        Err(_) => format!("{:016x}", u64::from_be_bytes(key_bytes()?)),
    };
    let socket = UdpSocket::bind(bind)?;
    eprintln!(
        "NU40 USB → phone → PC | UDP {} | Pair key: {}",
        socket.local_addr()?,
        key
    );
    println!(
        "{}",
        json!({"event":"ready","address":socket.local_addr()?.to_string()})
    );
    let mut data = vec![0u8; 65536];
    let mut peer = Peer::default();
    loop {
        let (n, _) = socket.recv_from(&mut data)?;
        if let Some(event) = event(&data[..n], &key, &model, &mut peer, Instant::now()) {
            println!("{event}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_packets_do_not_replace_new_samples() {
        let t = Instant::now();
        let mut p = Peer::default();
        assert!(p.accept("one", 4, t));
        assert!(!p.accept("one", 3, t));
        assert!(!p.accept("two", 0, t));
        let later = t + Duration::from_millis(400);
        assert!(p.accept("two", 1, later));
        assert!(!p.accept("one", 6, later + Duration::from_secs(1)));
    }
    #[test]
    fn phone_datagrams_require_a_key_and_sequence() {
        let m = Model::load(include_str!(
            "../../assets/nu40-imu-direction-training.json"
        ))
        .unwrap();
        let mut peer = Peer::default();
        let t = Instant::now();
        let packet=json!({"key":"paired-key","session":"phone","seq":2,"packet":{"v":1,"t_ms":15,"ax":0,"ay":0,"az":1,"gx":-180,"gy":-12,"gz":-11}}).to_string();
        assert!(event(packet.as_bytes(), "wrong", &m, &mut peer, t).is_none());
        let e = event(packet.as_bytes(), "paired-key", &m, &mut peer, t).unwrap();
        assert_eq!(e["direction"], "up");
        assert_eq!(e["sample"]["t_ms"], 15);
        assert!(event(packet.as_bytes(), "paired-key", &m, &mut peer, t).is_none());
        assert!(event(&[0; 4097], "paired-key", &m, &mut peer, t).is_none());
    }
    #[test]
    fn both_usb_formats_and_all_directions() {
        let text = include_str!("../../assets/nu40-imu-direction-training.json");
        let m = Model::load(text).unwrap();
        let raw: Value = serde_json::from_str(text).unwrap();
        for key in ["up", "down", "left", "right"] {
            let v = &raw["directions"][key];
            let data = json!({"v":1,"t_ms":4294967295u32,"ax":0,"ay":0,"az":1,"gx":v[0],"gy":v[1],"gz":v[2]});
            let s = sample(data).unwrap();
            assert_eq!(m.classify(&s).1, Some(key));
        }
        let raw = json!({"type":"imu","schema":1,"ts_ms":50,"imu":{"ax_g":0,"ay_g":0,"az_g":1,"gx_dps":0,"gy_dps":0,"gz_dps":0}});
        assert!(m.classify(&sample(raw).unwrap()).1.is_none());
        assert!(sample(json!({"v":1,"t_ms":false})).is_none());
    }
}
