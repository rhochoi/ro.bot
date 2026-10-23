import AppKit
import Carbon
import CoreGraphics
import Darwin
import WebKit

private typealias CGWinImgFn = @convention(c) (CGRect, UInt32, UInt32, UInt32) -> Unmanaged<CGImage>?

final class FloatingPetPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    // macOS 기본 창 높이 제한(메뉴바 아래로 큰 창 상단이 막히는 현상)을 해제하고 직접 화면 경계로 클램핑한다
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect {
        return frameRect
    }
}

final class PetAppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    private var panel: FloatingPetPanel!
    private var webView: WKWebView!
    private var statusItem: NSStatusItem!
    private var wanderItem: NSMenuItem!
    private var urlString: String = "http://127.0.0.1:8765/"
    private var wanderEnabled: Bool = false
    private var bubbleOpen: Bool = false
    private var placeTop: Bool = false
    private var placeLeft: Bool = false
    private var wanderDirection: CGFloat = -1.0
    private var wanderTimer: Timer?
    private var backdropTimer: Timer?
    private var dragTimer: Timer?
    private var fallTimer: Timer?
    private var isDragging: Bool = false
    private var backdropIsDark: Bool? = nil
    private var lastBackdropLum: Double = 0.5
    private var lastBackdropSampleTime: TimeInterval = 0
    private var cgWinImgFn: CGWinImgFn? = nil
    private var hotKeyRef: EventHotKeyRef? = nil

    // 말풍선이 닫혀 있을 때는 캐릭터(23u×22u)+캡션+그림자+점프/흔들기 여유 공간(176x168)을 확보해 잘림 방지
    private let compactSize = NSSize(width: 176, height: 168)
    private let expandedSize = NSSize(width: 360, height: 530)
    private let edgeMargin: CGFloat = 6.0

    init(url: String, wander: Bool) {
        self.urlString = url
        self.wanderEnabled = wander
        super.init()
        if let sym = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "CGWindowListCreateImage") {
            self.cgWinImgFn = unsafeBitCast(sym, to: CGWinImgFn.self)
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        killOtherInstances()
        NSApp.setActivationPolicy(.accessory)
        setupStatusItem()
        setupFloatingPanel()
        setupWanderTimer()
        setupBackdropTimer()
        setupGlobalHotKey()
    }

    /// 어느 앱에서든 ⌥R로 명령 팔레트를 연다(Carbon 핫키: 접근성 권한 불필요).
    private func setupGlobalHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let me = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            guard let userData = userData else { return noErr }
            let delegate = Unmanaged<PetAppDelegate>.fromOpaque(userData).takeUnretainedValue()
            DispatchQueue.main.async { delegate.openPalette() }
            return noErr
        }, 1, &spec, me, nil)
        let id = EventHotKeyID(signature: OSType(0x726f6274), id: 1)
        RegisterEventHotKey(UInt32(kVK_ANSI_R), UInt32(optionKey), id, GetApplicationEventTarget(), 0, &hotKeyRef)
    }

    @objc private func openPalette() {
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        webView.evaluateJavaScript("window.openPaletteFromHost && window.openPaletteFromHost();", completionHandler: nil)
    }

    // 중복 실행 방지: 현재 프로세스 외에 다른 ro-pet-host가 떠 있으면 즉시 종료시킨다
    private func killOtherInstances() {
        let myPid = ProcessInfo.processInfo.processIdentifier
        for app in NSWorkspace.shared.runningApplications {
            if app.processIdentifier != myPid,
               let name = app.localizedName ?? app.executableURL?.lastPathComponent,
               name.contains("ro-pet-host") {
                app.forceTerminate()
            }
        }
    }

    private func currentVisibleFrame() -> NSRect {
        if let s = panel?.screen ?? NSScreen.main ?? NSScreen.screens.first {
            return s.visibleFrame
        }
        return NSRect(x: 0, y: 0, width: 1440, height: 900)
    }

    // 화면 상하좌우 경계 클램핑: 밑으로 내릴 때 화면/Dock 아래로 사라지지 않고, 위로 올릴 때 메뉴바 바로 아래까지 이동
    private func clampOrigin(_ origin: NSPoint, size: NSSize) -> NSPoint {
        let vf = currentVisibleFrame()
        let minX = vf.minX + edgeMargin
        let maxX = max(minX, vf.maxX - size.width - edgeMargin)
        let minY = vf.minY + edgeMargin
        let maxY = max(minY, vf.maxY - size.height - edgeMargin)
        return NSPoint(
            x: min(max(origin.x, minX), maxX),
            y: min(max(origin.y, minY), maxY)
        )
    }

    // 현재 패널 프레임에서 펫 캐릭터(144x132)의 화면 좌표를 계산
    private func currentPetOrigin() -> NSPoint {
        let f = panel.frame
        if !bubbleOpen {
            return f.origin
        }
        let x = placeLeft ? f.minX : (f.maxX - compactSize.width)
        let y = placeTop ? (f.maxY - compactSize.height) : f.minY
        return NSPoint(x: x, y: y)
    }

    // 펫 바로 뒤쪽(kCGWindowListOptionOnScreenBelowWindow = 2) 배경의 평균 밝기를 측정해 분필/먹 자동 전환
    private func sampleBackdropLuminance() -> Double? {
        guard let fn = cgWinImgFn, panel != nil, panel.windowNumber > 0 else { return nil }
        guard CGPreflightScreenCaptureAccess() else { return nil }
        let primaryHeight = NSScreen.screens.first?.frame.height ?? 900.0
        let petPos = currentPetOrigin()
        // AppKit(하단 원점) -> Quartz(상단 원점) 좌표 변환: 캐릭터 몸체 중심부(88x64) 영역 샘플링
        let sampleX = petPos.x + 28.0
        let sampleY = primaryHeight - (petPos.y + 104.0)
        let rect = CGRect(x: sampleX, y: sampleY, width: 88.0, height: 64.0)

        // listOption 2 = kCGWindowListOptionOnScreenBelowWindow (펫 자신의 픽셀은 제외하고 바로 뒤 창/배경만 캡처)
        // imageOption 16 = kCGWindowImageNominalResolution
        guard let cgImg = fn(rect, 2, UInt32(panel.windowNumber), 16)?.takeRetainedValue() else {
            return nil
        }
        let rep = NSBitmapImageRep(cgImage: cgImg)
        let w = rep.pixelsWide
        let h = rep.pixelsHigh
        guard w > 0, h > 0 else { return nil }

        var total: Double = 0.0
        var count: Double = 0.0
        let stepX = max(1, w / 8)
        let stepY = max(1, h / 8)
        for y in stride(from: 0, to: h, by: stepY) {
            for x in stride(from: 0, to: w, by: stepX) {
                if let c = rep.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) {
                    let lum = 0.2126 * Double(c.redComponent)
                            + 0.7152 * Double(c.greenComponent)
                            + 0.0722 * Double(c.blueComponent)
                    total += lum
                    count += 1.0
                }
            }
        }
        return count > 0 ? (total / count) : nil
    }

    private func updateBackdropTheme(force: Bool = false) {
        let now = Date().timeIntervalSince1970
        if !force && (now - lastBackdropSampleTime) < 0.11 {
            return
        }
        lastBackdropSampleTime = now
        guard let lum = sampleBackdropLuminance() else { return }

        // 히스테리시스: 어두운 배경(<0.43)이면 분필(true), 밝은 배경(>0.53)이면 먹(false)
        let nextDark: Bool
        if let cur = backdropIsDark {
            if lum < 0.43 {
                nextDark = true
            } else if lum > 0.53 {
                nextDark = false
            } else {
                nextDark = cur
            }
        } else {
            nextDark = (lum < 0.48)
        }

        if force || nextDark != backdropIsDark || abs(lum - lastBackdropLum) > 0.08 {
            backdropIsDark = nextDark
            lastBackdropLum = lum
            let lumStr = String(format: "%.3f", lum)
            webView?.evaluateJavaScript(
                "window.setAutoBackdropDark && window.setAutoBackdropDark(\(nextDark), \(lumStr));",
                completionHandler: nil
            )
        }
    }

    // 말풍선 열림/닫힘 시 펫의 화면 위치를 유지한 채 패널 크기를 조절
    private func setBubbleState(_ open: Bool) {
        if bubbleOpen == open { return }
        let petPos = clampOrigin(currentPetOrigin(), size: compactSize)
        bubbleOpen = open

        if open {
            let vf = currentVisibleFrame()
            placeTop = (petPos.y + expandedSize.height > vf.maxY - edgeMargin)
            placeLeft = (petPos.x + compactSize.width - expandedSize.width < vf.minX + edgeMargin)
            webView.evaluateJavaScript(
                "window.setBubblePlacement && window.setBubblePlacement(\(placeTop), \(placeLeft));",
                completionHandler: nil
            )
            let rawX = placeLeft ? petPos.x : (petPos.x + compactSize.width - expandedSize.width)
            let rawY = placeTop ? (petPos.y + compactSize.height - expandedSize.height) : petPos.y
            let newOrigin = clampOrigin(NSPoint(x: rawX, y: rawY), size: expandedSize)
            let newFrame = NSRect(origin: newOrigin, size: expandedSize)
            panel.setFrame(newFrame, display: true)
            webView.frame = NSRect(origin: .zero, size: expandedSize)
            NSApp.activate(ignoringOtherApps: true)
            panel.makeKeyAndOrderFront(nil)
        } else {
            webView.evaluateJavaScript(
                "window.setBubblePlacement && window.setBubblePlacement(false, false);",
                completionHandler: nil
            )
            let newOrigin = clampOrigin(petPos, size: compactSize)
            let newFrame = NSRect(origin: newOrigin, size: compactSize)
            panel.setFrame(newFrame, display: true)
            webView.frame = NSRect(origin: .zero, size: compactSize)
            panel.orderFrontRegardless()
        }
        updateBackdropTheme(force: true)
    }

    // 네이티브 120fps 마우스 드래그 추적 (이동 중에도 배경 밝기를 실시간 감지해 분필/먹 자동 전환)
    private func startNativeDrag(fromPet: Bool) {
        dragTimer?.invalidate()
        let startMouse = NSEvent.mouseLocation
        let startOrigin = panel.frame.origin
        var moved = false
        var tick = 0
        isDragging = true

        if fromPet {
            // 누르는 순간 light 햅틱 1회 (트랙패드가 있는 맥에서만 느껴진다)
            NSHapticFeedbackManager.defaultPerformer.perform(.generic, performanceTime: .now)
            webView.evaluateJavaScript(
                "window.onHostDragStart && window.onHostDragStart(true);",
                completionHandler: nil
            )
        }

        dragTimer = Timer.scheduledTimer(withTimeInterval: 0.008, repeats: true) { [weak self] timer in
            guard let self = self else {
                timer.invalidate()
                return
            }
            let curMouse = NSEvent.mouseLocation
            let dx = curMouse.x - startMouse.x
            let dy = curMouse.y - startMouse.y
            tick += 1

            if hypot(dx, dy) > 8.0 {
                moved = true
            }
            if moved {
                let target = NSPoint(x: startOrigin.x + dx, y: startOrigin.y + dy)
                let clamped = self.clampOrigin(target, size: self.panel.frame.size)
                self.panel.setFrameOrigin(clamped)
                self.updateBackdropTheme(force: false)
                // 모션 엔진에 머리(창)의 위치를 스트리밍한다(약 60Hz). 속도·체인·기울기·흔들기 감지는 JS가 계산한다.
                if fromPet && tick % 2 == 0 {
                    self.webView.evaluateJavaScript(
                        "window.onHostDragMove && window.onHostDragMove(\(clamped.x), \(-clamped.y));",
                        completionHandler: nil
                    )
                }
            }
            if (NSEvent.pressedMouseButtons & 1) == 0 {
                timer.invalidate()
                self.dragTimer = nil
                self.isDragging = false
                self.updateBackdropTheme(force: true)
                if fromPet {
                    // 놓으면 중력(2400px/s²)으로 떨어진다. 낙하 거리는 최대 280ms 분량으로 제한한다.
                    let fall = moved ? self.startFall() : 0
                    self.webView.evaluateJavaScript(
                        "window.onHostDragEnd && window.onHostDragEnd(\(moved), \(fall));",
                        completionHandler: nil
                    )
                }
                if !moved && fromPet {
                    self.webView.evaluateJavaScript(
                        "window.onPetClickFromHost && window.onPetClickFromHost();",
                        completionHandler: nil
                    )
                }
            }
        }
    }

    /// 바닥(작업 영역 아래 여백)까지 떨어진다. 반환: 실제로 떨어진 거리(px). 최대 280ms 분량(≈94px).
    private func startFall() -> CGFloat {
        let g: CGFloat = 2400
        let maxT: CGFloat = 0.28
        let floorY = currentVisibleFrame().minY + edgeMargin
        let startY = panel.frame.origin.y
        let fall = min(max(0, startY - floorY), 0.5 * g * maxT * maxT)
        if fall < 0.5 { return 0 }
        let duration = sqrt(2 * fall / g)
        let startX = panel.frame.origin.x
        let begin = Date()
        fallTimer?.invalidate()
        fallTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 120.0, repeats: true) { [weak self] timer in
            guard let self = self else { timer.invalidate(); return }
            let t = CGFloat(Date().timeIntervalSince(begin))
            if t >= duration {
                self.panel.setFrameOrigin(NSPoint(x: startX, y: startY - fall))
                self.updateBackdropTheme(force: true)
                timer.invalidate()
                self.fallTimer = nil
                return
            }
            self.panel.setFrameOrigin(NSPoint(x: startX, y: startY - 0.5 * g * t * t))
        }
        return fall
    }

    private func make16pxIcon() -> NSImage {
        let size = NSSize(width: 16, height: 16)
        let img = NSImage(size: size, flipped: true) { _ in
            let ink = NSColor(calibratedRed: 0x1A/255.0, green: 0x19/255.0, blue: 0x17/255.0, alpha: 1.0)
            let chalk = NSColor(calibratedRed: 0xF3/255.0, green: 0xEF/255.0, blue: 0xE6/255.0, alpha: 1.0)

            let head = NSBezierPath(roundedRect: NSRect(x: 1, y: 0.8, width: 11.5, height: 5.0), xRadius: 2.5, yRadius: 2.5)
            ink.setFill()
            head.fill()

            chalk.setFill()
            NSRect(x: 6.5, y: 2.0, width: 1.3, height: 2.5).fill()
            NSRect(x: 9.0, y: 2.2, width: 1.2, height: 2.1).fill()

            let body = NSBezierPath(roundedRect: NSRect(x: 1, y: 6.6, width: 9.1, height: 3.8), xRadius: 1.9, yRadius: 1.9)
            ink.setFill()
            body.fill()

            let base = NSBezierPath(roundedRect: NSRect(x: 1, y: 11.2, width: 14.0, height: 3.8), xRadius: 1.9, yRadius: 1.9)
            ink.setFill()
            base.fill()
            return true
        }
        img.isTemplate = false
        return img
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let btn = statusItem.button {
            btn.image = make16pxIcon()
            btn.toolTip = "ro — 층 셋, 눈 둘. 기록하고, 풀어낸다."
        }
        let menu = NSMenu()
        let talkItem = NSMenuItem(title: "말 걸기 (대화창 열기/닫기)", action: #selector(toggleBubble), keyEquivalent: "r")
        talkItem.target = self
        menu.addItem(talkItem)
        let paletteItem = NSMenuItem(title: "명령 팔레트 (⌥R)", action: #selector(openPalette), keyEquivalent: "")
        paletteItem.target = self
        menu.addItem(paletteItem)

        let moodMenu = NSMenu()
        let moods: [(String, String)] = [
            ("기본 — 평소의 ro", "idle"),
            ("기록 중 — 아래를 보며 바닥이 길어짐", "recording"),
            ("집중 — 하나만 본다", "focus"),
            ("불안 — 눈이 작아지고 층이 어긋남", "anxious"),
            ("발견 — 눈이 커지고 머리가 들림", "discovery"),
            ("풀어냄 — 바닥이 길게 뻗는다", "resolved")
        ]
        for (title, code) in moods {
            let item = NSMenuItem(title: title, action: #selector(previewMood(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = code
            moodMenu.addItem(item)
        }
        let moodRoot = NSMenuItem(title: "표정 미리보기 (6종)", action: nil, keyEquivalent: "")
        moodRoot.submenu = moodMenu
        menu.addItem(moodRoot)

        let actMenu = NSMenu()
        let actions: [(String, String)] = [
            ("대기 · 숨쉬기 (Breathing)", "breathe"),
            ("두리번 (Look around)", "look_around"),
            ("걷기 · 이동 (Walk)", "walk"),
            ("생각 · 계산 (Thinking)", "thinking"),
            ("깜짝 · 깨어나기 (Alert)", "alert"),
            ("졸음 · 수면 (Drowsy)", "drowsy"),
            ("시선 추적 (Gaze)", "gaze"),
            ("쓰다듬기 (Pet)", "pet"),
            ("잡기 · 끌기 (Drag)", "drag"),
            ("흔들기 (Shake)", "shake"),
            ("놓기 · 착지 바운스 (Drop)", "drop"),
            ("성공 · 해결 (Success)", "success"),
            ("실패 · 불안 (Failure)", "failure")
        ]
        for (title, code) in actions {
            let item = NSMenuItem(title: title, action: #selector(triggerActionFromMenu(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = code
            actMenu.addItem(item)
        }
        let actRoot = NSMenuItem(title: "상호작용 바로 실행 (모션 12종)", action: nil, keyEquivalent: "")
        actRoot.submenu = actMenu
        menu.addItem(actRoot)

        let matMenu = NSMenu()
        let materials: [(String, String)] = [
            ("자동 — 감정·배경에 따라 전환", "auto"),
            ("점토 · Clay", "clay"),
            ("리퀴드 글라스 · Liquid Glass", "glass"),
            ("젤리 · Jelly", "jelly")
        ]
        for (title, code) in materials {
            let item = NSMenuItem(title: title, action: #selector(selectMaterial(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = code
            matMenu.addItem(item)
        }
        let matRoot = NSMenuItem(title: "재질 테마 (점토 / 리퀴드 글라스 / 젤리)", action: nil, keyEquivalent: "")
        matRoot.submenu = matMenu
        menu.addItem(matRoot)

        wanderItem = NSMenuItem(title: wanderEnabled ? "산책 모드: 켜짐" : "산책 모드: 꺼짐",
                                action: #selector(toggleWander), keyEquivalent: "w")
        wanderItem.target = self
        menu.addItem(wanderItem)

        let resetItem = NSMenuItem(title: "우측 하단으로 부르기", action: #selector(resetPosition), keyEquivalent: "0")
        resetItem.target = self
        menu.addItem(resetItem)

        menu.addItem(NSMenuItem.separator())
        let quitItem = NSMenuItem(title: "ro 펫 종료", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu
    }

    private func setupFloatingPanel() {
        let vf = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let origin = NSPoint(x: vf.maxX - compactSize.width - 20, y: vf.minY + 18)
        let rect = NSRect(origin: origin, size: compactSize)

        panel = FloatingPetPanel(
            contentRect: rect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isMovableByWindowBackground = false

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "roHost")
        webView = WKWebView(frame: NSRect(origin: .zero, size: compactSize), configuration: config)
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        panel.contentView = webView
        if let url = URL(string: urlString) {
            webView.load(URLRequest(url: url))
        }
        panel.orderFrontRegardless()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        updateBackdropTheme(force: true)
        webView.evaluateJavaScript("window.setWanderState && window.setWanderState(\(wanderEnabled));", completionHandler: nil)
    }

    private func setupWanderTimer() {
        wanderTimer = Timer.scheduledTimer(withTimeInterval: 3.2, repeats: true) { [weak self] _ in
            guard let self = self, self.wanderEnabled, !self.bubbleOpen, !self.isDragging else { return }
            let vf = self.currentVisibleFrame()
            var frame = self.panel.frame
            let step: CGFloat = 14.0 * self.wanderDirection
            var nextX = frame.origin.x + step
            let minX = vf.minX + 16
            let maxX = max(minX, vf.maxX - frame.width - 16)
            if nextX <= minX {
                nextX = minX
                self.wanderDirection = 1.0
            } else if nextX >= maxX {
                nextX = maxX
                self.wanderDirection = -1.0
            }
            frame.origin = self.clampOrigin(NSPoint(x: nextX, y: frame.origin.y), size: frame.size)
            self.panel.setFrame(frame, display: true, animate: true)
            self.updateBackdropTheme(force: false)
        }
    }

    // 배경 창이 바뀌거나 다크/라이트 화면이 전환될 때도 0.6초마다 배경 밝기를 감지
    private func setupBackdropTimer() {
        backdropTimer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in
            guard let self = self, !self.isDragging else { return }
            self.updateBackdropTheme(force: false)
        }
    }

    @objc private func toggleBubble() {
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        webView.evaluateJavaScript("window.toggleBubbleFromHost && window.toggleBubbleFromHost();", completionHandler: nil)
    }

    @objc private func previewMood(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        panel.orderFrontRegardless()
        webView.evaluateJavaScript("window.setMoodPreview && window.setMoodPreview('\(code)', 10000);", completionHandler: nil)
    }

    @objc private func triggerActionFromMenu(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        panel.orderFrontRegardless()
        webView.evaluateJavaScript("window.triggerAction && window.triggerAction('\(code)', 4500);", completionHandler: nil)
    }

    @objc private func selectMaterial(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        panel.orderFrontRegardless()
        webView.evaluateJavaScript("window.setMaterialFromHost && window.setMaterialFromHost('\(code)');", completionHandler: nil)
    }

    @objc private func toggleWander() {
        wanderEnabled.toggle()
        wanderItem.title = wanderEnabled ? "산책 모드: 켜짐" : "산책 모드: 꺼짐"
        webView?.evaluateJavaScript("window.setWanderState && window.setWanderState(\(wanderEnabled));", completionHandler: nil)
    }

    @objc private func resetPosition() {
        if bubbleOpen {
            webView.evaluateJavaScript("window.toggleBubbleFromHost && window.toggleBubbleFromHost(false);", completionHandler: nil)
            setBubbleState(false)
        }
        let vf = currentVisibleFrame()
        let origin = clampOrigin(NSPoint(x: vf.maxX - compactSize.width - 20, y: vf.minY + 18), size: compactSize)
        panel.setFrame(NSRect(origin: origin, size: compactSize), display: true, animate: true)
        updateBackdropTheme(force: true)
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let dict = message.body as? [String: Any],
              let type = dict["type"] as? String else { return }
        if type == "dragStart" {
            let fromPet = (dict["fromPet"] as? Bool) ?? true
            startNativeDrag(fromPet: fromPet)
        } else if type == "moveBy" {
            let dx = CGFloat((dict["dx"] as? NSNumber)?.doubleValue ?? 0)
            let dy = CGFloat((dict["dy"] as? NSNumber)?.doubleValue ?? 0)
            let target = NSPoint(x: panel.frame.origin.x + dx, y: panel.frame.origin.y - dy)
            panel.setFrameOrigin(clampOrigin(target, size: panel.frame.size))
            updateBackdropTheme(force: false)
        } else if type == "bubble" {
            let open = (dict["open"] as? Bool) ?? false
            setBubbleState(open)
        } else if type == "toggleWander" {
            let enabled = (dict["enabled"] as? Bool) ?? !wanderEnabled
            wanderEnabled = enabled
            wanderItem?.title = wanderEnabled ? "산책 모드: 켜짐" : "산책 모드: 꺼짐"
        }
    }
}

var urlArg = "http://127.0.0.1:8765/"
var wanderArg = false
let args = CommandLine.arguments
var i = 1
while i < args.count {
    if args[i] == "--url", i + 1 < args.count {
        urlArg = args[i + 1]
        i += 2
    } else if args[i] == "--wander" {
        wanderArg = true
        i += 1
    } else {
        i += 1
    }
}

let app = NSApplication.shared
let delegate = PetAppDelegate(url: urlArg, wander: wanderArg)
app.delegate = delegate
app.run()
