import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation
import Darwin
import os

private let launcherLog = Logger(subsystem: "ai.deban.desktop", category: "launcher")
func debanLog(_ message: String) {
    launcherLog.notice("\(message, privacy: .public)")
}

final class NativeAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    private enum EndReason: String { case readerLeft = "backend closed the channel", streamStopped = "stream stopped", microphoneChanged = "microphone permission changed" }

    private static let readyTag = UInt8(ascii: "R")
    private static let heartbeatTag = UInt8(ascii: "H")
    private static let maxBacklog = 512 * 1024

    private let fifo: URL
    private let writes = DispatchQueue(label: "ai.deban.audio-fifo")
    private let wake = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var stopping = false
    private var fd: Int32 = -1
    private var session = 0
    private var sessionEnded = true
    private var endReason: EndReason?
    private var activeStream: SCStream?
    private var ready = false
    private var pendingBytes = 0
    private var droppingFrames = false
    private var announcedSources = Set<UInt8>()

    init(fifo: URL) { self.fifo = fifo }

    func start() {
        let thread = Thread { self.supervise() }
        thread.name = "ai.deban.audio-supervisor"
        thread.qualityOfService = .userInitiated
        thread.start()
    }

    func stop() {
        lock.lock()
        stopping = true
        sessionEnded = true
        lock.unlock()
        wake.signal()
    }


    private var isStopping: Bool {
        lock.lock(); defer { lock.unlock() }
        return stopping
    }

    private func supervise() {
        var failures = 0
        while waitForPermission(), let fd = waitForReader() {
            let id = beginSession(fd)
            let mic = AVCaptureDevice.authorizationStatus(for: .audio)
            var micOn = mic == .authorized
            var stream = startStream(microphone: micOn)
            if stream == nil, micOn, !isStopping {
                micOn = false
                stream = startStream(microphone: false)
            }
            var healthy = false
            if let stream {
                let began = Date()
                markReady(stream)
                debanLog("Deban native audio capture started (session \(id), microphone \(micOn ? "on" : "off"))")
                logIfSilent(id)
                waitForSessionEnd(id, mic: mic)
                stopStream(stream)
                lock.lock(); let reason = endReason; lock.unlock()
                healthy = reason != .streamStopped || Date().timeIntervalSince(began) > 10
                if let reason { debanLog("Deban native audio session \(id) ended: \(reason.rawValue)") }
            }
            lock.lock(); let readerLeft = endReason == .readerLeft; lock.unlock()
            closeSession()
            failures = healthy ? 0 : failures + 1
            if failures > 0 {
                pause(Double(min(30, 1 << min(failures, 5))))
            } else if !readerLeft {
                pause(1)
            }
        }
        closeSession()
    }

    private func waitForPermission() -> Bool {
        var logged = false
        while !isStopping {
            if CGPreflightScreenCaptureAccess() { return true }
            if !logged {
                debanLog("Deban native audio waiting for Screen & System Audio Recording permission")
                logged = true
            }
            pause(1)
        }
        return false
    }

    private func waitForReader() -> Int32? {
        while !isStopping {
            let fd = Darwin.open(fifo.path, O_WRONLY | O_NONBLOCK | O_CLOEXEC)
            if fd >= 0 {
                _ = fcntl(fd, F_SETNOSIGPIPE, 1)
                return fd
            }
            usleep(50_000)
        }
        return nil
    }

    private func beginSession(_ fd: Int32) -> Int {
        lock.lock(); defer { lock.unlock() }
        while wake.wait(timeout: .now()) == .success {}
        session += 1
        self.fd = fd
        sessionEnded = stopping
        endReason = nil
        ready = false
        pendingBytes = 0
        droppingFrames = false
        announcedSources.removeAll()
        return session
    }

    private func markReady(_ stream: SCStream) {
        lock.lock(); activeStream = stream; lock.unlock()
        send(tag: Self.readyTag, rate: 0, payload: Data())
        lock.lock(); ready = true; lock.unlock()
    }

    private func waitForSessionEnd(_ id: Int, mic: AVAuthorizationStatus) {
        while true {
            _ = wake.wait(timeout: .now() + 1)
            lock.lock(); let over = stopping || sessionEnded; lock.unlock()
            if over { return }
            if AVCaptureDevice.authorizationStatus(for: .audio) != mic {
                finish(id, .microphoneChanged)
                return
            }
            send(tag: Self.heartbeatTag, rate: 0, payload: Data())
        }
    }

    private func finish(_ id: Int, _ reason: EndReason) {
        lock.lock()
        let ended = id == session && !sessionEnded
        if ended {
            sessionEnded = true
            endReason = reason
        }
        lock.unlock()
        if ended { wake.signal() }
    }

    private func closeSession() {
        lock.lock()
        sessionEnded = true
        activeStream = nil
        ready = false
        let fd = self.fd
        self.fd = -1
        lock.unlock()
        writes.sync { if fd >= 0 { Darwin.close(fd) } }
    }

    private func pause(_ seconds: Double) {
        _ = wake.wait(timeout: .now() + seconds)
    }

    private func logIfSilent(_ id: Int) {
        DispatchQueue.global().asyncAfter(deadline: .now() + 10) { [weak self] in
            guard let self else { return }
            self.lock.lock()
            let silent = self.session == id && !self.sessionEnded && self.announcedSources.isEmpty
            self.lock.unlock()
            if silent { debanLog("Deban native audio session \(id): no system audio or microphone frames 10 s after start") }
        }
    }


    private func startStream(microphone: Bool) -> SCStream? {
        let done = DispatchSemaphore(value: 0)
        let result = StartResult()
        Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                guard let display = content.displays.first else { throw CaptureError.noDisplay }
                let config = SCStreamConfiguration()
                config.capturesAudio = true
                config.excludesCurrentProcessAudio = true
                config.captureMicrophone = microphone
                config.sampleRate = 16_000
                config.channelCount = 1
                config.width = max(2, display.width)
                config.height = max(2, display.height)
                config.queueDepth = 3
                config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
                if microphone, let mic = AVCaptureDevice.default(for: .audio) {
                    config.microphoneCaptureDeviceID = mic.uniqueID
                }
                let filter = SCContentFilter(display: display, excludingWindows: [])
                let stream = SCStream(filter: filter, configuration: config, delegate: self)
                try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: DispatchQueue(label: "ai.deban.screen"))
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: DispatchQueue(label: "ai.deban.system-audio"))
                if microphone {
                    try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: DispatchQueue(label: "ai.deban.microphone"))
                }
                try await stream.startCapture()
                if !result.deliver(stream) { try? await stream.stopCapture() }
            } catch {
                debanLog("Deban native audio capture failed (microphone \(microphone ? "on" : "off")): \(error.localizedDescription)")
            }
            done.signal()
        }
        if done.wait(timeout: .now() + 20) == .timedOut {
            debanLog("Deban native audio capture did not start within 20 s")
        }
        return result.close()
    }

    private func stopStream(_ stream: SCStream) {
        lock.lock()
        if activeStream === stream { activeStream = nil }
        lock.unlock()
        let done = DispatchSemaphore(value: 0)
        Task {
            try? await stream.stopCapture()
            done.signal()
        }
        _ = done.wait(timeout: .now() + 5)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        let tag: UInt8
        switch type {
        case .audio: tag = UInt8(ascii: "S")
        case .microphone: tag = UInt8(ascii: "M")
        default: return
        }
        guard sampleBuffer.isValid else { return }
        var rate: UInt32 = 16_000
        if let description = CMSampleBufferGetFormatDescription(sampleBuffer),
           let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description) {
            rate = UInt32(asbd.pointee.mSampleRate)
        }
        try? sampleBuffer.withAudioBufferList { buffers, _ in
            for buffer in buffers {
                guard let data = buffer.mData, buffer.mDataByteSize > 0 else { continue }
                send(tag: tag, rate: rate, payload: Data(bytes: data, count: Int(buffer.mDataByteSize)), from: stream)
                break
            }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        debanLog("Deban native audio stream stopped: \(error.localizedDescription)")
        lock.lock()
        let id: Int? = activeStream === stream ? session : nil
        lock.unlock()
        if let id { finish(id, .streamStopped) }
    }


    private func send(tag: UInt8, rate: UInt32, payload: Data, from stream: SCStream? = nil) {
        var frame = Data(capacity: 9 + payload.count)
        frame.append(tag)
        withUnsafeBytes(of: rate.littleEndian) { frame.append(contentsOf: $0) }
        withUnsafeBytes(of: UInt32(payload.count).littleEndian) { frame.append(contentsOf: $0) }
        frame.append(payload)

        lock.lock(); defer { lock.unlock() }
        guard !sessionEnded, fd >= 0 else { return }
        if let stream {
            guard ready, stream === activeStream else { return }
            if announcedSources.insert(tag).inserted {
                let source = tag == UInt8(ascii: "S") ? "system audio" : "microphone"
                debanLog("Deban native audio received first \(source) frame (\(rate) Hz, \(payload.count) bytes)")
            }
        }
        guard pendingBytes + frame.count <= Self.maxBacklog else {
            if !droppingFrames {
                droppingFrames = true
                debanLog("Deban native audio: backend is not reading; dropping frames")
            }
            return
        }
        droppingFrames = false
        pendingBytes += frame.count
        let id = session
        let fd = self.fd
        writes.async { [self] in
            let ok = writeFrame(frame, to: fd, session: id)
            lock.lock()
            if session == id { pendingBytes -= frame.count }
            lock.unlock()
            if !ok { finish(id, .readerLeft) }
        }
    }

    private func isCurrent(_ id: Int) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return session == id && !sessionEnded
    }

    private func writeFrame(_ frame: Data, to fd: Int32, session id: Int) -> Bool {
        guard isCurrent(id) else { return false }
        return frame.withUnsafeBytes { raw -> Bool in
            guard var cursor = raw.baseAddress else { return true }
            var left = raw.count
            var giveUp: Date?
            while left > 0 {
                let n = Darwin.write(fd, cursor, left)
                if n > 0 {
                    cursor = cursor.advanced(by: n)
                    left -= n
                } else if n < 0 && errno == EAGAIN {
                    if left == raw.count && !isCurrent(id) { return false }
                    if !isCurrent(id) {
                        let limit = giveUp ?? Date().addingTimeInterval(2)
                        giveUp = limit
                        if Date() > limit { return false }
                    }
                    var pending = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
                    _ = poll(&pending, 1, 100)
                } else if !(n < 0 && errno == EINTR) {
                    return false
                }
            }
            return true
        }
    }

    private enum CaptureError: LocalizedError { case noDisplay }
}

private final class StartResult {
    private let lock = NSLock()
    private var stream: SCStream?
    private var closed = false

    func deliver(_ stream: SCStream) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !closed else { return false }
        self.stream = stream
        return true
    }

    func close() -> SCStream? {
        lock.lock(); defer { lock.unlock() }
        closed = true
        return stream
    }
}
