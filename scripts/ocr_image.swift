import AppKit
import Foundation
import Vision

if CommandLine.arguments.count < 2 {
    fputs("usage: ocr_image.swift <image-path>\n", stderr)
    exit(2)
}

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)

guard let image = NSImage(contentsOf: url) else {
    fputs("failed to read image\n", stderr)
    exit(1)
}

guard
    let data = image.tiffRepresentation,
    let bitmap = NSBitmapImageRep(data: data),
    let cgImage = bitmap.cgImage
else {
    fputs("failed to decode image\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "en-US"]

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

do {
    try handler.perform([request])
    let observations = request.results ?? []
    let lines = observations.compactMap { observation in
        observation.topCandidates(1).first?.string
    }
    print(lines.joined(separator: "\n"))
} catch {
    fputs("ocr failed: \(error)\n", stderr)
    exit(1)
}
