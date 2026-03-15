import AVFAudio
import Foundation
import Speech

struct AppleASRError: Error, CustomStringConvertible {
    let message: String

    var description: String {
        message
    }
}

func usage() -> Never {
    fputs("usage: video_audio_asr_apple.swift --input-path <audio-path> --locale <locale>\n", stderr)
    exit(2)
}

func argumentValue(_ name: String) -> String? {
    let args = CommandLine.arguments
    guard let index = args.firstIndex(of: name), index + 1 < args.count else {
        return nil
    }
    return args[index + 1]
}

func normalizedLocaleIdentifier(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines)
        .replacingOccurrences(of: "-", with: "_")
        .lowercased()
}

func resolveSupportedLocale(_ requested: Locale, supported: [Locale]) -> Locale? {
    let requestedID = normalizedLocaleIdentifier(requested.identifier)
    if let exact = supported.first(where: { normalizedLocaleIdentifier($0.identifier) == requestedID }) {
        return exact
    }

    let requestedLanguage = requested.language.languageCode?.identifier.lowercased() ?? ""
    if requestedLanguage.isEmpty {
        return nil
    }

    return supported.first {
        ($0.language.languageCode?.identifier.lowercased() ?? "") == requestedLanguage
    }
}

func audioDurationSeconds(_ file: AVAudioFile) -> Double {
    let sampleRate = file.processingFormat.sampleRate
    guard sampleRate > 0 else {
        return 0
    }
    return Double(file.length) / sampleRate
}

guard let inputPath = argumentValue("--input-path"), !inputPath.isEmpty else {
    usage()
}

let requestedLocale = Locale(identifier: argumentValue("--locale") ?? "zh_CN")
let inputURL = URL(fileURLWithPath: inputPath)
let semaphore = DispatchSemaphore(value: 0)

Task {
    defer { semaphore.signal() }

    do {
        guard SpeechTranscriber.isAvailable else {
            throw AppleASRError(message: "SpeechTranscriber is not available on this machine")
        }

        let supportedLocales = await SpeechTranscriber.supportedLocales
        guard let selectedLocale = resolveSupportedLocale(requestedLocale, supported: supportedLocales) else {
            throw AppleASRError(message: "Locale not supported by SpeechTranscriber: \(requestedLocale.identifier)")
        }

        let audioFile = try AVAudioFile(forReading: inputURL)
        let transcriber = SpeechTranscriber(locale: selectedLocale, preset: .timeIndexedProgressiveTranscription)
        _ = try await SpeechAnalyzer(
            inputAudioFile: audioFile,
            modules: [transcriber],
            finishAfterFile: true
        )

        var segments: [[String: Any]] = []
        for try await result in transcriber.results {
            guard result.isFinal else {
                continue
            }
            let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else {
                continue
            }
            let start = round(result.range.start.seconds * 1000) / 1000
            let end = round((result.range.start.seconds + result.range.duration.seconds) * 1000) / 1000
            segments.append([
                "start": start,
                "end": end,
                "text": text,
            ])
        }

        guard !segments.isEmpty else {
            throw AppleASRError(message: "SpeechTranscriber returned no final segments")
        }

        let joinedText = segments.compactMap { $0["text"] as? String }.joined(separator: " ")
        let payload: [String: Any] = [
            "text": joinedText,
            "language": normalizedLocaleIdentifier(selectedLocale.identifier),
            "duration_seconds": round(audioDurationSeconds(audioFile) * 1000) / 1000,
            "segments": segments,
            "model": "apple_speechtranscriber",
        ]

        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        FileHandle.standardOutput.write(data)
    } catch {
        let message = "apple speech transcription failed: \(error)\n"
        FileHandle.standardError.write(Data(message.utf8))
        exit(1)
    }
}

_ = semaphore.wait(timeout: .now() + 900)
