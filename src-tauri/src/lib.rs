mod backend;

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
    webview::DownloadEvent,
    Manager, RunEvent, WebviewUrl, WindowEvent,
};
use crate::backend::{resolve_paths, Backend, SharedBackend};

// A named mutex is released by Windows when the owning process exits, including
// a crash. It covers installed, unpacked and development builds in the same
// user session, before any of them can spawn a second backend.
#[cfg(windows)]
struct InstanceGuard(*mut std::ffi::c_void);

#[cfg(windows)]
impl InstanceGuard {
    fn acquire() -> Option<Self> {
        use std::ffi::{c_void, OsStr};
        use std::iter::once;
        use std::os::windows::ffi::OsStrExt;

        unsafe extern "system" {
            fn CreateMutexW(attributes: *const c_void, initial_owner: i32, name: *const u16) -> *mut c_void;
            fn GetLastError() -> u32;
            fn CloseHandle(handle: *mut c_void) -> i32;
        }

        const ERROR_ALREADY_EXISTS: u32 = 183;
        let name: Vec<u16> = OsStr::new("Local\\ai.deban.desktop.instance")
            .encode_wide().chain(once(0)).collect();
        let handle = unsafe { CreateMutexW(std::ptr::null(), 1, name.as_ptr()) };
        if handle.is_null() {
            return None;
        }
        if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
            unsafe { CloseHandle(handle); }
            return None;
        }
        Some(Self(handle))
    }
}

#[cfg(windows)]
impl Drop for InstanceGuard {
    fn drop(&mut self) {
        unsafe extern "system" { fn CloseHandle(handle: *mut std::ffi::c_void) -> i32; }
        unsafe { CloseHandle(self.0); }
    }
}

#[cfg(not(windows))]
struct InstanceGuard;

#[cfg(not(windows))]
impl InstanceGuard {
    fn acquire() -> Option<Self> { Some(Self) }
}

fn download_filename(path: &str, requested: Option<String>, suggested: &std::path::Path) -> Option<String> {
    let requested = requested.filter(|name| {
        !name.is_empty()
            && name.chars().count() <= 120
            && !name.chars().any(|c| matches!(c, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*') || c.is_control())
    });
    match path {
        "/api/export" => requested.or_else(|| Some("deban-all.deban".to_owned())),
        // WebView2 sometimes offers `1.srt` before it has read the response
        // header. The UI supplies the already-sanitised meeting title in the
        // URL, so it is the reliable initial name for the native save dialog.
        path if path.starts_with("/api/export/") => requested.or_else(|| Some(path.rsplit('/').next()?.to_owned())),
        // This also catches the download button in WebView2's built-in PDF
        // reader, whose request is for the original inline `/view` URL.
        path if path.starts_with("/api/context/")
            && (path.ends_with("/download") || path.ends_with("/view")) => suggested
                .file_name()
                .and_then(|name| name.to_str())
                .map(str::to_owned)
                .or_else(|| Some("deban-document".to_owned())),
        path if path.starts_with("/api/audio/") || path.starts_with("/api/audio_seg/") => suggested
            .file_name()
            .and_then(|name| name.to_str())
            .map(str::to_owned)
            .or_else(|| Some("deban-audio".to_owned())),
        _ => return None,
    }
}

fn splash_language() -> String {
    let language = std::env::var("LANG").unwrap_or_default().to_lowercase();
    if language.starts_with("ja") { "ja".to_owned() }
    else if language.starts_with("zh") { "zh".to_owned() }
    else { "en".to_owned() }
}

fn splash_text(lang: &str) -> (&'static str, &'static str) {
    match lang {
        "zh" | "zh-Hant" => ("正在启动 Deban…", "正在加载本地服务（语音模型在后台加载）。"),
        "ja" => ("Deban を起動中…", "ローカルサービスを読み込み中です（音声モデルはバックグラウンドで読み込まれます）。"),
        _ => ("Starting Deban…", "Loading the local backend (speech models load in the background)."),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // The macOS launcher has an equivalent flock guard. On Windows this mutex
    // makes a second launch exit before Tauri creates a window or backend.
    let _instance_guard = match InstanceGuard::acquire() {
        Some(guard) => guard,
        None => return,
    };
    let shared: SharedBackend = Arc::new(Mutex::new(None));
    let shared_for_setup = Arc::clone(&shared);
    let shared_for_exit = Arc::clone(&shared);

    tauri::Builder::default()
        .setup(move |app| {
            // Feed the persisted UI language to the initial splash synchronously.
            // An eval from the backend thread can race the splash page load.
            let initial_splash_lang = splash_language();
            let show = MenuItem::with_id(app, "show", "Show Deban", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit Deban", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::with_id("deban-tray")
                .icon(app.default_window_icon().expect("missing app icon").clone())
                .tooltip("Deban")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => show_main_window(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if matches!(event, TrayIconEvent::Click { button: MouseButton::Left, .. } | TrayIconEvent::DoubleClick { .. }) {
                        show_main_window(tray.app_handle());
                    }
                })
                .build(app)?;

            let win = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::App(format!("index.html?ui_lang={initial_splash_lang}").into()),
            )
            .title("Deban")
            .inner_size(1320.0, 860.0)
            .min_inner_size(900.0, 600.0)
            .resizable(true)
                    .on_download(|_webview, event| {
                if let DownloadEvent::Requested { url, destination } = event {
                    // WebView2 does not consistently surface an HTML `download`
                    // attribute from a loopback response. Ask the user where to
                    // save Deban exports rather than silently writing Downloads.
                    let requested = url
                        .query_pairs()
                        .find(|(key, _)| key == "filename")
                        .map(|(_, value)| value.into_owned())
                        .filter(|name| {
                            url.path() == "/api/export"
                                || (url.path().starts_with("/api/export/")
                                    && ((url.path().ends_with(".md") && name.ends_with(".md"))
                                        || (url.path().ends_with(".srt") && name.ends_with(".srt"))))
                        });
                    if let Some(filename) = download_filename(url.path(), requested, destination) {
                        let is_archive = filename.ends_with(".deban");
                        let path = rfd::FileDialog::new()
                            .set_title(if is_archive { "Save Deban archive" } else { "Save Deban file" })
                            .add_filter(if is_archive { "Deban archive" } else { "All files" }, if is_archive { &["deban"] } else { &["*"] })
                            .set_file_name(&filename)
                            .save_file();
                        if let Some(path) = path {
                            *destination = path;
                        } else {
                            return false;
                        }
                    }
                }
                        true
                    })
                    // `target=_blank` and `window.open()` are separate from
                    // normal navigation in WebView2. Keep loopback previews in
                    // a webview, but send every external link to the user's
                    // default browser.
                    .on_new_window(|url, _features| {
                        let host = url.host_str().unwrap_or("");
                        let local = host.is_empty()
                            || host == "127.0.0.1"
                            || host == "localhost"
                            || host == "tauri.localhost"
                            || host.ends_with(".localhost");
                        if local {
                            tauri::webview::NewWindowResponse::Allow
                        } else {
                            let _ = open::that(url.as_str());
                            tauri::webview::NewWindowResponse::Deny
                        }
                    })
                    .on_navigation(|url| {
                let host = url.host_str().unwrap_or("");
                let scheme = url.scheme();
                if scheme == "tauri" || scheme == "http" || scheme == "https" || scheme == "data" {
                    if host.is_empty()
                        || host == "127.0.0.1"
                        || host == "localhost"
                        || host == "tauri.localhost"
                        || host.ends_with(".localhost")
                    {
                        return true;
                    }
                    // External http(s) opens in the system browser.
                    if scheme == "http" || scheme == "https" {
                        let _ = open::that(url.as_str());
                        return false;
                    }
                }
                if scheme == "mailto" {
                    let _ = open::that(url.as_str());
                    return false;
                }
                true
            })
            .build()?;

            let _ = win; // keep window alive via app manager

            let handle = app.handle().clone();
            let shared = shared_for_setup;

            let resource_dir = app
                .path()
                .resource_dir()
                .unwrap_or_else(|_| PathBuf::from("."));
            let repo_hint = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .map(|p| p.to_path_buf());

            std::thread::spawn(move || {
                let splash_lang = splash_language();
                let (startup_title, startup_detail) = splash_text(&splash_lang);
                let splash = |msg: &str, detail: &str| {
                    if let Some(w) = handle.get_webview_window("main") {
                        let _ = w.eval(&format!(
                            "window.__debanSet && window.__debanSet({msg}, {detail})",
                            msg = serde_json::to_string(msg).unwrap_or_else(|_| "\"\"".into()),
                            detail = serde_json::to_string(detail).unwrap_or_else(|_| "\"\"".into()),
                        ));
                    }
                };

                splash(startup_title, startup_detail);

                let paths = match resolve_paths(&resource_dir, repo_hint.as_deref()) {
                    Ok(p) => p,
                    Err(e) => {
                        splash("The backend could not be started.", &e);
                        return;
                    }
                };

                let mut be = match Backend::start(&paths) {
                    Ok(b) => b,
                    Err(e) => {
                        splash(
                            "The backend could not be started.",
                            &format!("{e}\nLog: {}", paths.log_path.display()),
                        );
                        return;
                    }
                };
                let port = be.port;
                let log = be.log_path.clone();

                if let Err(e) = be.wait_ready(Duration::from_secs(120)) {
                    splash(
                        "The backend did not become ready in time.",
                        &format!("{e}\nLog: {}", log.display()),
                    );
                    be.stop();
                    return;
                }

                {
                    let mut slot = shared.lock().unwrap();
                    *slot = Some(be);
                }

                if let Some(w) = handle.get_webview_window("main") {
                    let url = format!("http://127.0.0.1:{port}/");
                    // WebView2 reliably performs this top-level navigation from
                    // the launcher worker thread. `WebviewWindow::navigate`
                    // can be silently deferred while the initial Tauri page is
                    // still loading, leaving the splash screen in place.
                    let script = format!(
                        "window.location.replace({});",
                        serde_json::to_string(&url).unwrap_or_else(|_| "\"\"".into()),
                    );
                    if let Err(e) = w.eval(&script) {
                        splash(
                            "The page could not be loaded.",
                            &format!("{e}\nTried {url}"),
                        );
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Closing the window keeps Deban available in the tray. The
                // explicit tray Quit action runs the cleanup in RunEvent::Exit.
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Deban")
        .run(move |_app, event| {
            if let RunEvent::Exit = event {
                if let Ok(mut slot) = shared_for_exit.lock() {
                    if let Some(mut be) = slot.take() {
                        be.stop();
                    }
                }
            }
        });
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}
