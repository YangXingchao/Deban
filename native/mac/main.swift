import AppKit
import AVFoundation
import CoreGraphics
import Darwin
import Foundation
import WebKit

// Keep a launcher-level lock for the lifetime of the app. This is separate
// from the backend's data lock, so the child can acquire its own lock.
final class SingleInstanceLock {
    private let fd: Int32
    private init(_ fd: Int32) { self.fd = fd }

    static func acquire() throws -> SingleInstanceLock? {
        let directory = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                    appropriateFor: nil, create: true)
            .appendingPathComponent("Deban-Desktop", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let fd = open(directory.appendingPathComponent("launcher.lock").path, O_CREAT | O_RDWR | O_CLOEXEC, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            let error = errno
            close(fd)
            if error == EWOULDBLOCK || error == EAGAIN { return nil }
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(error))
        }
        return SingleInstanceLock(fd)
    }

    deinit { flock(fd, LOCK_UN); close(fd) }
}

func activateExistingDeban() {
    guard let bundleID = Bundle.main.bundleIdentifier else { return }
    for running in NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
        where running.processIdentifier != getpid() {
        running.activate(options: [.activateAllWindows])
        return
    }
}

func availablePort() throws -> UInt16 {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    guard fd >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    defer { close(fd) }
    var address = sockaddr_in()
    address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    address.sin_family = sa_family_t(AF_INET)
    address.sin_addr = in_addr(s_addr: in_addr_t(INADDR_LOOPBACK).bigEndian)
    address.sin_port = 0
    let bound = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    guard bound == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    var size = socklen_t(MemoryLayout<sockaddr_in>.size)
    let found = withUnsafeMutablePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &size) }
    }
    guard found == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    return UInt16(bigEndian: address.sin_port)
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandlerWithReply {
    private var window: NSWindow!
    private var webview: WKWebView!
    private var backend: Process?
    private var capture: NativeAudioCapture?
    private var fifo: URL?
    private var port: UInt16 = 0
    private var launchedWithScreenCapture = true
    private var watchingScreenCapture = false
    private var onboardingActive = false
    private var relaunchedForScreenGrant = false
    private static let screenGrantRelaunchKey = "screenGrantRelaunchAt"

    func applicationDidFinishLaunching(_ notification: Notification) {
        launchedWithScreenCapture = CGPreflightScreenCaptureAccess()
        if let at = UserDefaults.standard.object(forKey: Self.screenGrantRelaunchKey) as? Date {
            UserDefaults.standard.removeObject(forKey: Self.screenGrantRelaunchKey)
            relaunchedForScreenGrant = Date().timeIntervalSince(at) < 60
        }
        do {
            port = try availablePort()
            let pipe = FileManager.default.temporaryDirectory.appendingPathComponent("deban-\(UUID().uuidString).fifo")
            guard mkfifo(pipe.path, S_IRUSR | S_IWUSR) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
            fifo = pipe
            capture = NativeAudioCapture(fifo: pipe)
            capture?.start()
            let config = WKWebViewConfiguration()
            config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "debanPermissions")
            webview = WKWebView(frame: .zero, configuration: config)
            webview.navigationDelegate = self
            window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 780),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            window.title = "Deban"
            window.contentView = webview
            window.delegate = self
            window.center()
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            try launchBackend(pipe: pipe)
            waitForBackend(attempt: 0)
        } catch {
            fail("Unable to start Deban: \(error.localizedDescription)")
        }
    }

    private func launchBackend(pipe: URL) throws {
        let resources = Bundle.main.resourceURL!
        let root = resources.appendingPathComponent("backend/approot")
        let python = resources.appendingPathComponent("backend/python/bin/python3")
        let process = Process()
        process.executableURL = python
        process.arguments = ["-u", root.appendingPathComponent("main.pyc").path]
        process.currentDirectoryURL = root
        var env = ProcessInfo.processInfo.environment
        env["MC_PORT"] = String(port)
        env["MC_DESKTOP_NATIVE_AUDIO"] = "1"
        env["MC_AUDIO_FIFO"] = pipe.path
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = env
        try process.run()
        backend = process
    }

    private func waitForBackend(attempt: Int) {
        guard backend?.isRunning == true else { fail("The local service stopped unexpectedly."); return }
        guard attempt < 150 else { fail("The local service did not start in time."); return }
        let url = URL(string: "http://127.0.0.1:\(port)/api/status")!
        var request = URLRequest(url: url)
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    self.webview.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.port)/")!))
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { self.waitForBackend(attempt: attempt + 1) }
                }
            }
        }.resume()
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.name == "debanPermissions", message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == Int(port),
              let action = message.body as? String, ["status", "screen", "screenSettings", "mic", "micSettings", "onboardingDone"].contains(action) else {
            replyHandler(nil, "Invalid request"); return
        }
        if action == "onboardingDone" {
            onboardingActive = false
            replyHandler(permissionState(), nil)
            return
        }
        if action == "status" || action == "screen" || action == "screenSettings" {
            onboardingActive = true
            watchForScreenCaptureGrant()
        }
        if action == "screenSettings" || action == "micSettings" {
            let pane = action == "screenSettings" ? "Privacy_ScreenCapture" : "Privacy_Microphone"
            guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)"),
                  NSWorkspace.shared.open(url) else {
                replyHandler(nil, "Unable to open System Settings"); return
            }
            replyHandler(permissionState(), nil)
            return
        }
        if action == "screen" {
            DispatchQueue.main.async {
                if !CGPreflightScreenCaptureAccess() { _ = CGRequestScreenCaptureAccess() }
                replyHandler(self.permissionState(), nil)
            }
            return
        }
        if action == "mic" {
            AVCaptureDevice.requestAccess(for: .audio) { _ in DispatchQueue.main.async { replyHandler(self.permissionState(), nil) } }
            return
        }
        replyHandler(permissionState(), nil)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" || (url.scheme == "http" && url.host == "127.0.0.1" && url.port == Int(port)) {
            decisionHandler(.allow)
        } else {
            if navigationAction.navigationType == .linkActivated, ["https", "http", "mailto"].contains(url.scheme ?? "") {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }

    private func permissionState() -> [String: Any] {
        let mic = AVCaptureDevice.authorizationStatus(for: .audio)
        return ["screen": CGPreflightScreenCaptureAccess(), "mic": mic == .authorized,
                "micDenied": mic == .denied || mic == .restricted]
    }

    // ScreenCaptureKit can retain the pre-grant TCC state until the process
    // restarts. Mirror the validated desktop launcher, but only relaunch while
    // onboarding is open so an active meeting is never interrupted.
    private func watchForScreenCaptureGrant() {
        guard !launchedWithScreenCapture, !watchingScreenCapture, !relaunchedForScreenGrant else { return }
        watchingScreenCapture = true
        DispatchQueue.global(qos: .utility).async { [weak self] in
            while !CGPreflightScreenCaptureAccess() { Thread.sleep(forTimeInterval: 0.5) }
            DispatchQueue.main.async {
                guard let self, self.onboardingActive else { return }
                self.scheduleReopenAfterExit()
                NSApp.terminate(nil)
            }
        }
    }

    private func scheduleReopenAfterExit() {
        UserDefaults.standard.set(Date(), forKey: Self.screenGrantRelaunchKey)
        let reopen = Process()
        reopen.executableURL = URL(fileURLWithPath: "/bin/sh")
        reopen.arguments = ["-c", "i=0; while kill -0 \"$1\" 2>/dev/null && [ $i -lt 300 ]; do sleep 0.2; i=$((i+1)); done; exec /usr/bin/open \"$2\"", "deban-reopen", String(getpid()), Bundle.main.bundlePath]
        do { try reopen.run() } catch { NSLog("Deban reopen helper failed: %@", error.localizedDescription) }
    }

    private func fail(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Deban"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }

    func windowWillClose(_ notification: Notification) { NSApp.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) {
        capture?.stop()
        if backend?.isRunning == true { backend?.terminate() }
        if let fifo { try? FileManager.default.removeItem(at: fifo) }
    }
}

let instanceLock: SingleInstanceLock
do {
    guard let acquired = try SingleInstanceLock.acquire() else {
        activateExistingDeban()
        exit(0)
    }
    instanceLock = acquired
} catch {
    NSLog("Deban cannot acquire its launcher lock: %@", error.localizedDescription)
    exit(1)
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
withExtendedLifetime(instanceLock) {}
