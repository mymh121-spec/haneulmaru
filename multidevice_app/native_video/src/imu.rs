use crate::ipc::Worker;
use anyhow::Result;
use serde_json::{json, Value};
use std::{
    path::Path,
    process::Command,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

pub struct LiveImu {
    latest: Arc<Mutex<Option<Value>>>,
    origin: Instant,
    stop: Arc<AtomicBool>,
    reader: Option<JoinHandle<()>>,
}
impl LiveImu {
    pub fn start(
        worker: &Path,
        bind: &str,
        model: &Path,
        cancel: &Arc<AtomicBool>,
    ) -> Result<Self> {
        let mut command = Command::new(worker);
        command
            .arg("imu")
            .arg(bind)
            .arg(model)
            .env("HANEULMARU_IMU_SYNC", "1");
        let mut child = Worker::spawn(&mut command)?;
        let ready = child.json(vec![], Duration::from_secs(5), cancel)?;
        anyhow::ensure!(
            ready["event"] == "ready" && ready["protocol_version"] == 2,
            "IMU protocol mismatch"
        );
        let origin = Instant::now();
        let synced = child.json(vec![b"sync\n".to_vec()], Duration::from_secs(5), cancel)?;
        anyhow::ensure!(synced["event"] == "clock_synced", "IMU sync failed");
        let latest = Arc::new(Mutex::new(None::<Value>));
        let target = latest.clone();
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = stop.clone();
        let reader = thread::spawn(move || {
            while !stopping.load(Ordering::Relaxed) {
                match child.json(vec![], Duration::from_secs(3600), &stopping) {
                    Ok(event) if event["event"] == "sample" => {
                        let now = origin.elapsed().as_secs_f64() * 1000.;
                        if let Some(t) = event["received_elapsed_ms"]
                            .as_f64()
                            .filter(|t| t.is_finite() && *t >= 0. && *t <= now)
                        {
                            let mut guard = target.lock().unwrap();
                            if guard
                                .as_ref()
                                .and_then(|v| v["received_elapsed_ms"].as_f64())
                                .is_none_or(|old| t >= old)
                            {
                                *guard = Some(event);
                            }
                        }
                    }
                    Ok(_) => {}
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            latest,
            origin,
            stop,
            reader: Some(reader),
        })
    }
    pub fn snapshot(&self) -> Result<Value> {
        let state = self
            .latest
            .lock()
            .map_err(|_| anyhow::anyhow!("IMU state unavailable"))?;
        Ok(classification(
            state.as_ref(),
            self.origin.elapsed().as_secs_f64() * 1000.,
        ))
    }
}
pub fn classification(event: Option<&Value>, now: f64) -> Value {
    if let Some(e) = event {
        if let Some(age) = e["received_elapsed_ms"]
            .as_f64()
            .map(|t| now - t)
            .filter(|a| a.is_finite() && (0. ..=350.).contains(a))
        {
            return json!({"valid":true,"direction":e["direction"],"similarity":e["similarity"],
                "rate_dps":e["rate_dps"],"source":e["source"],"age_ms":age,"board_t_ms":e["sample"]["t_ms"],"session":e["session"]});
        }
    }
    json!({"valid":false,"direction":null,"reason":"imu_unavailable_or_stale"})
}
impl Drop for LiveImu {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn transport_age_does_not_rejuvenate_delayed_samples() {
        let e = json!({"received_elapsed_ms":100.,"direction":"up","sample":{"t_ms":12}});
        assert_eq!(classification(Some(&e), 450.)["valid"], true);
        assert_eq!(classification(Some(&e), 451.)["valid"], false);
        assert_eq!(classification(Some(&e), 99.)["valid"], false);
    }
}
