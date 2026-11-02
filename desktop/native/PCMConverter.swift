import AVFAudio
import Foundation

final class PCMConverter {
    private var converter: AVAudioConverter?
    private var inputFormat: AVAudioFormat?
    private let outputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!

    func convert(_ input: UnsafePointer<AudioBufferList>, format: AudioStreamBasicDescription) throws -> Data {
        if inputFormat == nil {
            var description = format
            guard let source = AVAudioFormat(streamDescription: &description),
                  let created = AVAudioConverter(from: source, to: outputFormat) else {
                throw TapFailure(stage: "create_converter", status: -1)
            }
            inputFormat = source
            converter = created
        }
        guard let inputFormat, let converter,
              inputFormat.sampleRate == format.mSampleRate,
              inputFormat.channelCount == format.mChannelsPerFrame,
              let source = AVAudioPCMBuffer(pcmFormat: inputFormat, bufferListNoCopy: input, deallocator: nil) else {
            throw TapFailure(stage: "changed_audio_format", status: -1)
        }
        guard source.frameLength > 0 else { return Data() }
        let capacity = AVAudioFrameCount(ceil(Double(source.frameLength) * 16000 / inputFormat.sampleRate)) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { throw TapFailure(stage: "allocate_pcm", status: -1) }
        var supplied = false
        var failure: NSError?
        let status = converter.convert(to: output, error: &failure) { _, state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true
            state.pointee = .haveData
            return source
        }
        if status == .error || failure != nil { throw TapFailure(stage: "convert_pcm", status: -1) }
        guard let samples = output.floatChannelData?[0] else { return Data() }
        return Data(bytes: samples, count: Int(output.frameLength) * MemoryLayout<Float>.size)
    }
}
