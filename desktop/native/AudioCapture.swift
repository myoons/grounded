import CoreAudio
import Foundation
import Darwin

func emit(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value) {
        FileHandle.standardError.write(data + Data([10]))
    }
}

@main
struct AudioCapture {
    static func main() {
        if CommandLine.arguments.contains("--self-test") { selfTest(); return }
        signal(SIGPIPE, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        _ = fcntl(STDOUT_FILENO, F_SETFL, O_NONBLOCK)
        let control = DispatchQueue(label: "dev.grounded.audio.control")
        let converter = PCMConverter()
        let tap = SystemAudioTap(onStage: { emit(["state": "starting", "stage": $0]) }, onBuffer: { buffer, format in
            do {
                let data = try converter.convert(buffer, format: format)
                guard !data.isEmpty else { return }
                let count = data.withUnsafeBytes { bytes in Darwin.write(STDOUT_FILENO, bytes.baseAddress, bytes.count) }
                if count != data.count { emit(["state": "error", "code": "PCM_BACKPRESSURE"]); exit(3) }
            } catch { emit(["state": "error", "code": "PCM_CONVERSION_FAILED"]); exit(4) }
        })
        let stopping = DispatchSource.makeSignalSource(signal: SIGTERM, queue: control)
        stopping.setEventHandler { tap.stop(); exit(0) }
        stopping.resume()
        let parent = getppid()
        let lifetime = DispatchSource.makeTimerSource(queue: DispatchQueue.global())
        lifetime.schedule(deadline: .now() + 1, repeating: 1)
        lifetime.setEventHandler { if getppid() != parent { exit(0) } }
        lifetime.resume()
        control.async {
            do { try tap.start(); emit(["state": "ready", "sample_rate": 16000, "channels": 1, "encoding": "float32le"]) }
            catch { emit(["state": "error", "code": "NATIVE_START_FAILED", "detail": String(describing: error)]); exit(2) }
        }
        withExtendedLifetime((stopping, lifetime)) { dispatchMain() }
    }

    static func selfTest() {
        let converter = PCMConverter()
        let format = AudioStreamBasicDescription(mSampleRate: 48000, mFormatID: kAudioFormatLinearPCM, mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked, mBytesPerPacket: 8, mFramesPerPacket: 1, mBytesPerFrame: 8, mChannelsPerFrame: 2, mBitsPerChannel: 32, mReserved: 0)
        var total = 0
        var energy = 0.0
        do {
            for block in 0..<100 {
                var input = (0..<960).map { index -> Float in Float(sin(Double(block * 480 + index / 2) * 2 * .pi * 1000 / 48000) * 0.25) }
                try input.withUnsafeMutableBytes { bytes in
                    var buffer = AudioBufferList(mNumberBuffers: 1, mBuffers: AudioBuffer(mNumberChannels: 2, mDataByteSize: UInt32(bytes.count), mData: bytes.baseAddress))
                    let data = try converter.convert(&buffer, format: format)
                    data.withUnsafeBytes { raw in
                        for offset in stride(from: 0, to: raw.count, by: 4) {
                            let value = raw.loadUnaligned(fromByteOffset: offset, as: Float.self)
                            energy += Double(value * value)
                            total += 1
                        }
                    }
                }
            }
            let rms = sqrt(energy / Double(max(total, 1)))
            guard total > 15500 && total <= 16100 && rms > 0.12 && rms < 0.22 else { exit(5) }
            emit(["self_test": "passed", "samples": total, "sample_rate": 16000, "channels": 1, "rms": rms])
        } catch { emit(["self_test": "failed", "detail": String(describing: error)]); exit(6) }
    }
}
