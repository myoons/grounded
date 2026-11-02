import CoreAudio
import Foundation

struct TapFailure: Error, CustomStringConvertible {
    let stage: String
    let status: OSStatus
    var description: String { "\(stage): OSStatus \(status)" }
}

final class SystemAudioTap {
    private var tap = AudioObjectID(kAudioObjectUnknown)
    private var device = AudioObjectID(kAudioObjectUnknown)
    private var procedure: AudioDeviceIOProcID?
    private let audioQueue = DispatchQueue(label: "dev.grounded.tap.samples")
    private var blocks = 0
    private var audibleBlocks = 0
    private var sampleCount = 0
    private var peakRMS = 0.0
    private var format = AudioStreamBasicDescription()
    private let onStage: (String) -> Void
    private let onBuffer: ((UnsafePointer<AudioBufferList>, AudioStreamBasicDescription) -> Void)?

    init(onStage: @escaping (String) -> Void = { _ in }, onBuffer: ((UnsafePointer<AudioBufferList>, AudioStreamBasicDescription) -> Void)? = nil) { self.onStage = onStage; self.onBuffer = onBuffer }

    private func check(_ operation: @autoclosure () -> OSStatus, _ stage: String) throws {
        onStage(stage)
        let status = operation()
        if status != noErr { throw TapFailure(stage: stage, status: status) }
    }

    func start() throws {
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.name = "Grounded system audio probe"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        try check(AudioHardwareCreateProcessTap(description, &tap), "create_process_tap")
        do {
            var address = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
            try check(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &format), "read_tap_format")
            guard format.mFormatID == kAudioFormatLinearPCM,
                  format.mFormatFlags & kAudioFormatFlagIsFloat != 0,
                  format.mBitsPerChannel == 32 else {
                throw TapFailure(stage: "unsupported_pcm_format", status: -1)
            }
            var output = AudioObjectID(kAudioObjectUnknown)
            address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            size = UInt32(MemoryLayout<AudioObjectID>.size)
            try check(AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &output), "read_default_output")
            var inputAddress = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration, mScope: kAudioDevicePropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
            var inputSize: UInt32 = 0
            try check(AudioObjectGetPropertyDataSize(output, &inputAddress, 0, nil, &inputSize), "read_output_input_size")
            guard inputSize >= MemoryLayout<UInt32>.size && inputSize <= 65536 else {
                if inputSize == 0 { throw TapFailure(stage: "unknown_output_input_layout", status: -1) }
                throw TapFailure(stage: "unsupported_output_input_layout", status: -1)
            }
            let layout = UnsafeMutableRawPointer.allocate(byteCount: max(Int(inputSize), MemoryLayout<AudioBufferList>.size), alignment: MemoryLayout<AudioBufferList>.alignment)
            defer { layout.deallocate() }
            try check(AudioObjectGetPropertyData(output, &inputAddress, 0, nil, &inputSize, layout), "read_output_input_layout")
            let physicalInputs = UnsafeMutableAudioBufferListPointer(layout.assumingMemoryBound(to: AudioBufferList.self)).reduce(0) { $0 + Int($1.mNumberChannels) }
            guard physicalInputs == 0 else { throw TapFailure(stage: "output_device_has_physical_inputs", status: -1) }
            var outputUID: CFString = "" as CFString
            address.mSelector = kAudioDevicePropertyDeviceUID
            size = UInt32(MemoryLayout<CFString>.size)
            try check(withUnsafeMutablePointer(to: &outputUID) { pointer in
                AudioObjectGetPropertyData(output, &address, 0, nil, &size, pointer)
            }, "read_output_uid")
            let specification: [String: Any] = [
                kAudioAggregateDeviceNameKey: "Grounded private audio probe",
                kAudioAggregateDeviceUIDKey: UUID().uuidString,
                kAudioAggregateDeviceIsPrivateKey: true,
                kAudioAggregateDeviceIsStackedKey: false,
                kAudioAggregateDeviceMainSubDeviceKey: outputUID,
                kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
                kAudioAggregateDeviceTapAutoStartKey: false,
                kAudioAggregateDeviceTapListKey: [[
                    kAudioSubTapUIDKey: description.uuid.uuidString,
                    kAudioSubTapDriftCompensationKey: true
                ]]
            ]
            try check(AudioHardwareCreateAggregateDevice(specification as CFDictionary, &device), "create_private_aggregate")
            try check(AudioDeviceCreateIOProcIDWithBlock(&procedure, device, audioQueue) { [weak self] _, input, _, _, _ in
                self?.consume(input)
            }, "create_audio_callback")
            try check(AudioDeviceStart(device, procedure), "start_audio_device")
        } catch {
            stop()
            throw error
        }
    }

    private func consume(_ input: UnsafePointer<AudioBufferList>) {
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        var sum = 0.0
        var count = 0
        for buffer in buffers {
            guard let data = buffer.mData else { continue }
            let samples = data.assumingMemoryBound(to: Float.self)
            let length = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
            for index in 0..<length {
                let value = Double(samples[index])
                if value.isFinite { sum += value * value; count += 1 }
            }
        }
        guard count > 0 else { return }
        let rms = sqrt(sum / Double(count))
        blocks += 1
        sampleCount += count
        if rms > 0.0001 { audibleBlocks += 1 }
        peakRMS = max(peakRMS, rms)
        onBuffer?(input, format)
    }

    func stop() {
        if device != kAudioObjectUnknown {
            if let procedure {
                AudioDeviceStop(device, procedure)
                AudioDeviceDestroyIOProcID(device, procedure)
            }
            AudioHardwareDestroyAggregateDevice(device)
            device = AudioObjectID(kAudioObjectUnknown)
            procedure = nil
        }
        if tap != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tap)
            tap = AudioObjectID(kAudioObjectUnknown)
        }
        audioQueue.sync {}
    }

    func metrics() -> [String: Any] {
        audioQueue.sync {
            ["sample_blocks": blocks, "audible_blocks": audibleBlocks,
             "sample_count": sampleCount, "peak_rms": peakRMS,
             "sample_rate": format.mSampleRate, "channels": format.mChannelsPerFrame,
             "passed": audibleBlocks >= 5]
        }
    }
}
