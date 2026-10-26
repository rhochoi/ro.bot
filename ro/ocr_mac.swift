import Foundation
import Vision
import PDFKit
import AppKit

func recognize(_ handler: VNImageRequestHandler) throws -> [String] {
    let request = VNRecognizeTextRequest()
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ko-KR", "en-US"]
    request.usesLanguageCorrection = true
    try handler.perform([request])
    return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
}

for path in CommandLine.arguments.dropFirst() {
    autoreleasepool {
        var text = "", lines = 0, pages = 0, error = ""
        let url = URL(fileURLWithPath: path)
        do {
            if url.pathExtension.lowercased() == "pdf" {
                guard let document = PDFDocument(url: url), !document.isLocked else {
                    throw NSError(domain: "OCR", code: 1)
                }
                pages = document.pageCount
                for number in 0..<min(pages, 20) {
                    try autoreleasepool {
                        guard let page = document.page(at: number) else {
                            throw NSError(domain: "OCR", code: 2)
                        }
                        let bounds = page.bounds(for: .mediaBox)
                        guard bounds.width > 0, bounds.height > 0 else {
                            throw NSError(domain: "OCR", code: 3)
                        }
                        // shortcut: 긴 변 2400px 렌더링, 작은 글자 누락 실측 시 해상도를 조정한다.
                        let scale = min(2, 2400 / max(bounds.width, bounds.height))
                        let size = NSSize(width: bounds.width * scale, height: bounds.height * scale)
                        let thumbnail = page.thumbnail(of: size, for: .mediaBox)
                        guard let image = thumbnail.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
                            throw NSError(domain: "OCR", code: 4)
                        }
                        let recognized = try recognize(VNImageRequestHandler(cgImage: image))
                        text += "[p.\(number + 1)]\n" + recognized.joined(separator: "\n") + "\n"
                        lines += recognized.count
                    }
                }
            } else {
                let recognized = try recognize(VNImageRequestHandler(url: url))
                text = recognized.joined(separator: "\n")
                lines = recognized.count
                pages = 1
            }
        } catch _ {
            // 오류 설명에는 원본 경로나 내용이 포함될 수 있다.
            text = ""
            error = "OCR 미지원 또는 인식 실패"
        }
        let result: [String: Any] = ["path": path, "text": text, "lines": lines,
                                     "pages": pages, "error": error]
        if let data = try? JSONSerialization.data(withJSONObject: result) {
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data([10]))
        }
    }
}
