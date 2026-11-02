import AppKit
import Foundation

final class ProbeApp: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var status: NSTextField!
    private var button: NSButton!
    private let control = DispatchQueue(label: "dev.grounded.tap.control")
    private var tap: SystemAudioTap?
    private var active = false
    private var report: [String: Any] = [:]
    private let destination = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Roomshift Research/coreaudio-probe.json")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 620, height: 320), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Grounded — Core Audio check"
        window.delegate = self
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 22
        stack.translatesAutoresizingMaskIntoConstraints = false
        let heading = NSTextField(labelWithString: "Check real system audio")
        heading.font = .systemFont(ofSize: 24, weight: .semibold)
        let explanation = NSTextField(wrappingLabelWithString: "Use headphones and have the other Meet participant speak during the 15-second test. This reads system playback only. No microphone, screen, audio file or cloud upload.")
        button = NSButton(title: "Start 15-second check", target: self, action: #selector(begin))
        button.bezelStyle = .rounded
        status = NSTextField(wrappingLabelWithString: "Ready. Approve System Audio Recording normally if macOS asks.")
        for view in [heading, explanation, button!, status!] { stack.addArrangedSubview(view) }
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 28), stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -28), stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 28)])
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func save() {
        do {
            try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: destination, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
        } catch { status.stringValue = "Could not save diagnostic metadata: \(error.localizedDescription)" }
    }

    @objc private func begin() {
        guard !active else { return }
        active = true
        button.isEnabled = false
        status.stringValue = "Starting Core Audio…"
        report = ["route": "native_core_audio_tap", "status": "starting", "passed": false,
                  "started_at": ISO8601DateFormatter().string(from: Date()),
                  "os": ProcessInfo.processInfo.operatingSystemVersionString,
                  "audio_saved": false, "audio_uploaded": false]
        save()
        let candidate = SystemAudioTap { stage in
            DispatchQueue.main.async {
                self.report["stage"] = stage
                self.save()
            }
        }
        tap = candidate
        control.async {
            do {
                try candidate.start()
                DispatchQueue.main.async {
                    self.status.stringValue = "Listening to system playback for 15 seconds. Ask the remote participant to speak now."
                    self.report["status"] = "capturing"
                    self.save()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 15) { self.finish() }
                }
            } catch {
                DispatchQueue.main.async {
                    self.report["error"] = String(describing: error)
                    self.finish()
                }
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
            if self.active && self.report["status"] as? String == "starting" {
                self.report["status"] = "startup_timeout"
                self.status.stringValue = "NOT VERIFIED — startup stalled at \(self.report["stage"] ?? "unknown"). Close the app and check System Audio Recording permission."
                self.save()
            }
        }
    }

    private func finish() {
        guard active, let tap else { return }
        active = false
        control.async {
            tap.stop()
            let metrics = tap.metrics()
            DispatchQueue.main.async {
                self.report.merge(metrics) { _, next in next }
                self.report["status"] = "finished"
                self.report["completed_at"] = ISO8601DateFormatter().string(from: Date())
                self.status.stringValue = metrics["passed"] as? Bool == true ? "PASS — nonzero system audio received. Report: \(self.destination.path)" : "NOT VERIFIED — \(self.report["error"] ?? "No audible samples received"). Report: \(self.destination.path)"
                self.save()
                self.button.isEnabled = true
            }
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        if active {
            report["status"] = "cancelled"
            save()
        }
        let stopped = DispatchSemaphore(value: 0)
        control.async { self.tap?.stop(); stopped.signal() }
        _ = stopped.wait(timeout: .now() + 1)
    }
}

@main
struct ProbeApplication {
    static func main() {
        let application = NSApplication.shared
        let delegate = ProbeApp()
        application.delegate = delegate
        withExtendedLifetime(delegate) { application.run() }
    }
}
