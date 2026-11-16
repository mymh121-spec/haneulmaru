// Standalone fault-injection process for the Python integration test, no descendants.
use std::{fs::OpenOptions, io::{self, BufRead, Write}, time::Duration};
fn main() {
    writeln!(OpenOptions::new().create(true).append(true).open(std::env::var("NATIVE_STALL_MARKER").unwrap()).unwrap(), "start").unwrap();
    if std::env::var("NATIVE_STALL_MODE").unwrap() == "frame" {
        let mut line = String::new();
        io::stdin().lock().read_line(&mut line).unwrap();
        println!("{{\"ready\":true,\"pipeline_protocol\":1}}");
        io::stdout().flush().unwrap();
    }
    std::thread::sleep(Duration::from_secs(60));
}
