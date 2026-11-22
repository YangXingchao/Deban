//! Spawn / supervise the embedded (or dev) Python FastAPI backend.
//! Mirrors packaging/launcher/main.swift Backend.

use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub struct BackendPaths {
    pub python: PathBuf,
    pub app_root: PathBuf,
    pub log_path: PathBuf,
}

/// Resolve python + approot for production (bundled resources) or development (repo .venv).
pub fn resolve_paths(resource_dir: &Path, repo_hint: Option<&Path>) -> Result<BackendPaths, String> {
    let log_path = log_file_path()?;

    // Explicit overrides for debugging / CI.
    if let (Ok(py), Ok(root)) = (std::env::var("DEBAN_PYTHON"), std::env::var("DEBAN_APPROOT")) {
        return Ok(BackendPaths {
            python: PathBuf::from(py),
            app_root: PathBuf::from(root),
            log_path,
        });
    }

    // Bundled layout (prepared by packaging/build_windows.ps1):
    //   <resource_dir>/backend/python/deban.exe          (preferred; Privacy UX)
    //   <resource_dir>/backend/python/deban-backend.exe  (legacy rename)
    //   <resource_dir>/backend/python/python.exe         (mid-build stock CPython)
    //   <resource_dir>/backend/approot/{main.py,app/,ui/}
    let bundled_root = resource_dir.join("backend/approot");
    // Release payloads compile Deban's Python entry point to `main.pyc` and
    // remove the source file. Development payloads retain `main.py`.
    let has_entry = bundled_root.join("main.py").is_file() || bundled_root.join("main.pyc").is_file();
    let bundled_py = [
        resource_dir.join("backend/python/deban.exe"),
        resource_dir.join("backend/python/deban-backend.exe"),
        resource_dir.join("backend/python/python.exe"),
    ]
    .into_iter()
    .find(|p| p.is_file());
    if let Some(bundled_py) = bundled_py {
        if has_entry {
            return Ok(BackendPaths {
                python: bundled_py,
                app_root: bundled_root,
                log_path,
            });
        }
    }

    // Dev layout: repo root with .venv + main.py
    let candidates: Vec<PathBuf> = {
        let mut v = Vec::new();
        if let Some(h) = repo_hint {
            v.push(h.to_path_buf());
        }
        if let Ok(cwd) = std::env::current_dir() {
            v.push(cwd.clone());
            v.push(cwd.join(".."));
        }
        // cargo run from src-tauri/
        v.push(PathBuf::from(".."));
        v.push(PathBuf::from("../.."));
        v
    };

    for root in candidates {
        let root = root.canonicalize().unwrap_or(root);
        let py = root.join(".venv/Scripts/python.exe");
        let main = root.join("main.py");
        if py.is_file() && main.is_file() {
            return Ok(BackendPaths {
                python: py,
                app_root: root,
                log_path,
            });
        }
    }

    Err(format!(
        "backend not found.\n\
         Expected bundled resources under:\n  {}\n\
         or a repo checkout with .venv\\Scripts\\python.exe and main.py.\n\
         Or set DEBAN_PYTHON and DEBAN_APPROOT.",
        resource_dir.join("backend").display()
    ))
}

fn log_file_path() -> Result<PathBuf, String> {
    let base = std::env::var("LOCALAPPDATA")
        .map(PathBuf::from)
        .or_else(|_| std::env::var("APPDATA").map(PathBuf::from))
        .map_err(|_| "LOCALAPPDATA / APPDATA not set".to_string())?;
    let dir = base.join("Deban").join("logs");
    fs::create_dir_all(&dir).map_err(|e| format!("create log dir: {e}"))?;
    Ok(dir.join("backend.log"))
}

pub fn pick_port() -> Result<u16, String> {
    // Reserve an ephemeral loopback port through the OS rather than assuming a
    // popular fixed port such as 8765 is free. The Windows named mutex in
    // lib.rs remains the single-instance authority; this only isolates Deban
    // from unrelated local applications.
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("reserve loopback port: {e}"))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("read reserved loopback port: {e}"))?
        .port();
    drop(listener);
    Ok(port)
}

pub fn status_ok(port: u16) -> bool {
    let addr = match format!("127.0.0.1:{port}").to_socket_addrs() {
        Ok(mut a) => match a.next() {
            Some(x) => x,
            None => return false,
        },
        Err(_) => return false,
    };
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(400)) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));
    let request = format!(
        "GET /api/status HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 256];
    let n = match stream.read(&mut buf) {
        Ok(n) => n,
        Err(_) => return false,
    };
    let text = String::from_utf8_lossy(&buf[..n]);
    text.starts_with("HTTP/1.") && text.contains(" 200 ")
}

pub struct Backend {
    child: Child,
    pub port: u16,
    pub log_path: PathBuf,
}

impl Backend {
    pub fn start(paths: &BackendPaths) -> Result<Self, String> {
        let port = pick_port()?;
        if let Some(parent) = paths.log_path.parent() {
            fs::create_dir_all(parent).ok();
        }
        let mut log = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&paths.log_path)
            .map_err(|e| format!("open log {}: {e}", paths.log_path.display()))?;
        let stamp = chrono_like_stamp();
        writeln!(log, "\n===== launch {stamp} port {port} =====").ok();

        let log_out = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&paths.log_path)
            .map_err(|e| format!("open log for stdout: {e}"))?;
        let log_err = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&paths.log_path)
            .map_err(|e| format!("open log for stderr: {e}"))?;

        // Windows release builds ship Deban's backend as bytecode rather than
        // readable .py files.  Keep the source entrypoint for `-Dev` runs.
        let entrypoint = if paths.app_root.join("main.pyc").is_file() { "main.pyc" } else { "main.py" };
        let mut cmd = Command::new(&paths.python);
        cmd.arg("-u")
            .arg(entrypoint)
            .current_dir(&paths.app_root)
            .env("MC_PORT", port.to_string())
            .env("MC_DESKTOP_NATIVE_AUDIO", "1")
            .env("MC_LAUNCHER", "windows-tauri")
            .env("PYTHONNOUSERSITE", "1")
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::from(log_out))
            .stderr(Stdio::from(log_err));

        // Python is a console-subsystem executable. Keep the desktop shell
        // window-only while its output continues to be captured in backend.log.
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        // Prefer the embedded python's own DLLs / scripts on PATH.
        if let Some(bin) = paths.python.parent() {
            let path = std::env::var_os("PATH").unwrap_or_default();
            let mut new_path = bin.as_os_str().to_owned();
            new_path.push(";");
            new_path.push(&path);
            cmd.env("PATH", new_path);
        }

        let child = cmd
            .spawn()
            .map_err(|e| format!("spawn {}: {e}", paths.python.display()))?;

        Ok(Backend {
            child,
            port,
            log_path: paths.log_path.clone(),
        })
    }

    pub fn wait_ready(&mut self, timeout: Duration) -> Result<(), String> {
        wait_until_ready(self.port, timeout, &mut self.child)
    }

    pub fn stop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.stop();
    }
}

pub type SharedBackend = Arc<Mutex<Option<Backend>>>;

pub fn wait_until_ready(port: u16, timeout: Duration, child: &mut Child) -> Result<(), String> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!(
                "backend exited before ready (code {:?})",
                status.code()
            ));
        }
        if status_ok(port) {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(400));
    }
    Err(format!(
        "backend did not become ready within {}s",
        timeout.as_secs()
    ))
}

fn chrono_like_stamp() -> String {
    // Avoid pulling chrono just for a log banner; local time via Windows-friendly ISO-ish stamp.
    use std::time::SystemTime;
    match SystemTime::now().duration_since(SystemTime::UNIX_EPOCH) {
        Ok(d) => format!("unix={}", d.as_secs()),
        Err(_) => "unix=?".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::pick_port;
    use std::net::TcpListener;

    #[test]
    fn picks_a_bindable_ephemeral_loopback_port() {
        let port = pick_port().expect("reserve loopback port");
        assert_ne!(port, 0);
        TcpListener::bind(("127.0.0.1", port)).expect("selected port remains bindable");
    }
}
