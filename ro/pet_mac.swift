import AppKit
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

// MARK: - CPU 걸음 (07). 순수 계산만 두어 --selftest로 GUI 없이 확인한다

// host_statistics(HOST_CPU_LOAD_INFO)의 누적 틱. 배열 순서는 USER, SYSTEM, IDLE, NICE (idle은 인덱스 2)
struct CPUTicks: Equatable {
    var user: UInt32
    var system: UInt32
    var idle: UInt32
    var nice: UInt32

    // 카운터는 UInt32라 몇 달 켜 두면 한 바퀴 돈다. 합과 차를 모두 &+ / &- 로 해야 wrap에도 맞는다
    var busy: UInt32 { user &+ system &+ nice }
    var total: UInt32 { busy &+ idle }

    init(user: UInt32, system: UInt32, idle: UInt32, nice: UInt32) {
        self.user = user
        self.system = system
        self.idle = idle
        self.nice = nice
    }

    // 커널이 주는 cpu_ticks 튜플을 이름 붙은 필드로. 순서를 틀리면 사용률이 100%로 나온다
    init(machTicks t: (UInt32, UInt32, UInt32, UInt32)) {
        self.init(user: t.0, system: t.1, idle: t.2, nice: t.3)
    }

    static func read() -> CPUTicks? {
        var info = host_cpu_load_info()
        var count = mach_msg_type_number_t(MemoryLayout<host_cpu_load_info>.size / MemoryLayout<integer_t>.size)
        let rc = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                host_statistics(mach_host_self(), HOST_CPU_LOAD_INFO, $0, &count)
            }
        }
        guard rc == KERN_SUCCESS else { return nil }
        return CPUTicks(machTicks: info.cpu_ticks)
    }
}

// 두 표본 사이 전체 CPU 사용률(0~100). 틱이 안 늘었으면(잠자기 직후 등) nil
func cpuPercent(from prev: CPUTicks, to cur: CPUTicks) -> Double? {
    let total = cur.total &- prev.total
    guard total > 0 else { return nil }
    let busy = cur.busy &- prev.busy
    return min(100, Double(busy) / Double(total) * 100)
}

// 걸음 4단계. 경계는 20/50/80이고 메뉴 막대는 0.8초보다 빨라지지 않는다
enum Gait: Int, CaseIterable {
    case calm = 0, walk, busy, hot

    static let edges: [Double] = [20, 50, 80]
    var period: Double { [3.2, 1.6, 0.8, 0.4][rawValue] }
    var rate: Double { 3.2 / period }
    var menubarPeriod: Double { max(period, 0.8) }
    var label: String { ["느긋", "보통 걸음", "바쁨", "과열"][rawValue] }
}

// 최근 3개 중앙값(순간 튐 제거) → 지수 평균 α=0.3 → 모든 경계에 ±5% 히스테리시스.
// 시뮬레이션(docs/PET-MENUBAR-MODE.md): 50% 근처 10분 140회 → 13회 전환
struct CPUSmoother {
    static let alpha = 0.3
    static let gap = 5.0
    private(set) var recent: [Double] = []
    private(set) var smoothed: Double? = nil
    private(set) var gait: Gait = .calm

    mutating func push(_ raw: Double) -> Gait {
        recent.append(raw)
        if recent.count > 3 { recent.removeFirst() }
        let median = recent.sorted()[recent.count / 2]
        let value = smoothed.map { Self.alpha * median + (1 - Self.alpha) * $0 } ?? median
        smoothed = value
        // 한 번에 여러 단계를 건너뛸 수 있다 (5% → 90% 계단)
        while gait != .hot && value >= Gait.edges[gait.rawValue] + Self.gap {
            gait = Gait(rawValue: gait.rawValue + 1)!
        }
        while gait != .calm && value < Gait.edges[gait.rawValue - 1] - Self.gap {
            gait = Gait(rawValue: gait.rawValue - 1)!
        }
        return gait
    }

    // 잠자기·화면 꺼짐 뒤에는 이전 값이 의미 없으니 처음부터
    mutating func reset() {
        recent = []
        smoothed = nil
        gait = .calm
    }
}

// GUI 없이 계산만 확인한다. 실패 항목을 출력하고 실패 수를 돌려준다
func runCPUSelfTest() -> Int {
    var failures = 0
    func check(_ ok: Bool, _ name: String) {
        print("\(ok ? "ok  " : "FAIL") \(name)")
        if !ok { failures += 1 }
    }
    func feed(_ values: [Double]) -> [Gait] {
        var s = CPUSmoother()
        return values.map { s.push($0) }
    }
    func switches(_ gaits: [Gait]) -> Int {
        zip(gaits, gaits.dropFirst()).filter { $0 != $1 }.count
    }

    // 커널 순서 (USER, SYSTEM, IDLE, NICE) 그대로 넣는다
    let a = CPUTicks(machTicks: (100, 100, 700, 0))
    let b = CPUTicks(machTicks: (110, 110, 780, 0))
    check(cpuPercent(from: a, to: b) == 20, "idle은 인덱스 2: 바쁨 20틱 / 전체 100틱 = 20%")

    // 카운터 셋이 함께 한 바퀴 돌아도 (합에서도 넘친다) 차이는 맞아야 한다
    let nearMax = CPUTicks(user: UInt32.max - 5, system: UInt32.max - 3, idle: UInt32.max - 50, nice: 0)
    let wrapped = CPUTicks(user: 4, system: 6, idle: 29, nice: 0)  // user +10, system +10, idle +80
    let wrapPct = cpuPercent(from: nearMax, to: wrapped) ?? -1
    check(abs(wrapPct - 20) < 0.01, "UInt32 wrap에도 사용률이 맞다 (\(String(format: "%.2f", wrapPct)))")
    check(cpuPercent(from: a, to: a) == nil, "틱이 안 늘면 nil")

    var spikes = Array(repeating: 6.0, count: 60)
    spikes[20] = 76
    spikes[41] = 76
    check(feed(spikes).allSatisfy { $0 == .calm }, "한가할 때 순간 튐 한 번은 무시한다")

    let hover = (0..<240).map { $0 % 2 == 0 ? 44.0 : 56.0 }
    check(switches(feed(hover)) <= 1, "50% 근처에서 단계가 흔들리지 않는다")

    let step = Array(repeating: 5.0, count: 30) + Array(repeating: 90.0, count: 30)
    let stepGaits = feed(step)
    let hotAt = stepGaits.firstIndex(of: .hot).map { $0 - 30 } ?? -1
    check(hotAt >= 0 && hotAt <= 10, "5% → 90% 계단은 10초 안에 과열 (\(hotAt)초)")

    var s = CPUSmoother()
    for _ in 0..<30 { _ = s.push(95) }
    for _ in 0..<30 { _ = s.push(78) }
    check(s.gait == .hot, "과열은 75% 아래로 내려가야 풀린다 (78%는 유지)")
    for _ in 0..<30 { _ = s.push(70) }
    check(s.gait == .busy, "70%면 바쁨으로 내려온다")

    check(Gait.allCases.map(\.period) == [3.2, 1.6, 0.8, 0.4], "사이클 3.2 / 1.6 / 0.8 / 0.4초")
    check(Gait.hot.menubarPeriod == 0.8 && Gait.calm.menubarPeriod == 3.2, "메뉴 막대는 0.8초 상한")
    print(failures == 0 ? "selftest passed" : "selftest failed: \(failures)")
    return failures
}

// 데스크톱 펫과 메뉴 막대 아이콘은 같은 Ro다. 한 번에 한 곳에만 보인다
enum PetMode: String {
    case desktop
    case menubar
}

final class PetAppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    private var panel: FloatingPetPanel!
    private var webView: WKWebView!
    private var statusItem: NSStatusItem!
    private var wanderItem: NSMenuItem!
    private var modeItem: NSMenuItem!
    private var mainMenu: NSMenu!
    // 번들 없는 swiftc 실행 파일이라 .standard 도메인이 실행 파일 이름에 묶이므로 suite를 고정한다
    private let defaults = UserDefaults(suiteName: "ro.pet") ?? .standard
    private var mode: PetMode = .desktop
    private var urlString: String = "http://127.0.0.1:8765/"
    private var wanderEnabled: Bool = false
    private var bubbleOpen: Bool = false
    private var placeTop: Bool = false
    private var placeLeft: Bool = false
    private var wanderDirection: CGFloat = -1.0
    private var wanderTimer: Timer?
    private var backdropTimer: Timer?
    private var dragTimer: Timer?
    private var isDragging: Bool = false
    private var backdropIsDark: Bool? = nil
    private var lastBackdropLum: Double = 0.5
    private var lastBackdropSampleTime: TimeInterval = 0
    private var cgWinImgFn: CGWinImgFn? = nil

    // 말풍선이 닫혀 있을 때는 캐릭터(23u×22u)+캡션+그림자+점프/흔들기 여유 공간(176x168)을 확보해 잘림 방지
    private let compactSize = NSSize(width: 176, height: 168)
    private let expandedSize = NSSize(width: 360, height: 520)
    private let edgeMargin: CGFloat = 6.0
    // 화면 위 이 거리 안으로 끌고 오면 메뉴 막대에 Ro 자리가 열린다
    private let dockZone: CGFloat = 80.0
    private var toastPanel: NSPanel?
    // 아이콘을 이만큼 아래로 끌면 펫으로 꺼낸다
    private let popOutDistance: CGFloat = 24.0
    private var iconTimer: Timer?
    private var headerTitle: NSTextField!
    private var headerDetail: NSTextField!
    // 메뉴 막대 모드에서 패널을 아이콘 아래로 옮기기 전 데스크톱 자리
    private var savedDesktopOrigin: NSPoint?
    private var cpuTimer: Timer?
    private var lastTicks: CPUTicks?
    private var smoother = CPUSmoother()
    private(set) var gait: Gait = .calm

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
        mode = PetMode(rawValue: defaults.string(forKey: "ro.mode") ?? "") ?? .desktop
        setupStatusItem()
        setupFloatingPanel()
        setMode(mode)
        ensureIconVisible(announceSuccess: false)  // 지난번 메뉴 막대 모드로 시작했는데 아이콘이 가려진 경우
        setupWanderTimer()
        setupBackdropTimer()
        setupCPUSampler()
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
            revealPet()
        }
        updateBackdropTheme(force: true)
    }

    // 네이티브 120fps 마우스 드래그 추적 (이동 중에도 배경 밝기를 실시간 감지해 분필/먹 자동 전환)
    private func startNativeDrag(fromPet: Bool) {
        dragTimer?.invalidate()
        let startMouse = NSEvent.mouseLocation
        let startOrigin = panel.frame.origin
        var moved = false
        var prevMouse = startMouse
        var lastDxSign = 0
        var shakeCount = 0
        var lastReversalTime = Date().timeIntervalSince1970
        // 메뉴 막대로 보내기는 데스크톱 펫(말풍선 닫힘)을 끌 때만
        let canDock = fromPet && mode == .desktop && !bubbleOpen
        var inZone = false
        var lastProgress: CGFloat = 0
        isDragging = true

        if fromPet {
            webView.evaluateJavaScript(
                "window.onHostDragStart && window.onHostDragStart(true);",
                completionHandler: nil
            )
        }

        let timer = Timer(timeInterval: 0.008, repeats: true) { [weak self] timer in
            guard let self = self else {
                timer.invalidate()
                return
            }
            let curMouse = NSEvent.mouseLocation
            let dx = curMouse.x - startMouse.x
            let dy = curMouse.y - startMouse.y
            let stepDx = curMouse.x - prevMouse.x
            let now = Date().timeIntervalSince1970

            // 흔들기(shake) 감지: 0.35초 내에 좌우 방향 전환이 3회 이상 발생하면 shake 트리거
            if abs(stepDx) > 6.0 {
                let sign = stepDx > 0 ? 1 : -1
                if sign != lastDxSign && lastDxSign != 0 {
                    if (now - lastReversalTime) < 0.35 {
                        shakeCount += 1
                        if shakeCount >= 3 {
                            self.webView.evaluateJavaScript(
                                "window.onHostDragShake && window.onHostDragShake();",
                                completionHandler: nil
                            )
                            shakeCount = 0
                        }
                    } else {
                        shakeCount = 1
                    }
                    lastReversalTime = now
                }
                lastDxSign = sign
            }
            prevMouse = curMouse

            if hypot(dx, dy) > 8.0 {
                moved = true
            }
            if moved {
                let target = NSPoint(x: startOrigin.x + dx, y: startOrigin.y + dy)
                let clamped = self.clampOrigin(target, size: self.panel.frame.size)
                self.panel.setFrameOrigin(clamped)
                self.updateBackdropTheme(force: false)
            }
            if canDock && moved {
                let top = (self.panel.screen ?? NSScreen.main)?.visibleFrame.maxY ?? curMouse.y
                let d = max(0, top - curMouse.y)
                let nowIn = d < self.dockZone
                if nowIn != inZone {
                    inZone = nowIn
                    self.statusItem.isVisible = nowIn  // 막대에 자리를 열고 닫는다
                }
                let progress = nowIn ? min(1, 1 - d / self.dockZone) : 0
                if abs(progress - lastProgress) > 0.02 || (progress == 0) != (lastProgress == 0) {
                    lastProgress = progress
                    self.webView.evaluateJavaScript(
                        "window.onHostDockProgress && window.onHostDockProgress(\(String(format: "%.3f", progress)));",
                        completionHandler: nil
                    )
                }
            }
            if (NSEvent.pressedMouseButtons & 1) == 0 {
                timer.invalidate()
                self.dragTimer = nil
                self.isDragging = false
                if inZone {
                    self.dockToMenubar(returnOrigin: startOrigin)
                    return
                }
                self.updateBackdropTheme(force: true)
                if fromPet {
                    self.webView.evaluateJavaScript(
                        "window.onHostDragEnd && window.onHostDragEnd(\(moved));",
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
        RunLoop.main.add(timer, forMode: .common)
        dragTimer = timer
    }

    // 놓는 순간 펫 창을 아이콘 자리로 빨아들이고 숨긴다. 다시 꺼내면 끌기 전 자리로 돌아온다
    private func dockToMenubar(returnOrigin: NSPoint) {
        guard let iconFrame = statusItem.button?.window?.frame else {
            setMode(.menubar)
            return
        }
        let size = panel.frame.size
        let target = NSPoint(x: iconFrame.midX - size.width / 2, y: iconFrame.midY - size.height / 2)
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.28
            ctx.timingFunction = CAMediaTimingFunction(name: .easeOut)
            self.panel.animator().setFrameOrigin(target)
        }, completionHandler: { [weak self] in
            guard let self = self else { return }
            self.setMode(.menubar)
            self.panel.setFrameOrigin(self.clampOrigin(returnOrigin, size: size))
            self.webView.evaluateJavaScript("window.onHostDocked && window.onHostDocked();", completionHandler: nil)
            self.bounceIcon()
            // 아이템이 많으면 노치 뒤로 숨는다. 가려졌으면 알리고 데스크톱으로 되돌린다
            self.ensureIconVisible(announceSuccess: true)
        })
    }

    // 아이템이 많으면 아이콘이 노치 뒤로 숨는다. 가려졌으면 알리고 데스크톱으로 되돌린다.
    // 전체 화면 앱 때문에 메뉴 막대가 숨은 경우는 가려진 것으로 보지 않는다
    private func ensureIconVisible(announceSuccess: Bool) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
            guard let self = self, self.mode == .menubar,
                  let win = self.statusItem.button?.window else { return }
            if win.occlusionState.contains(.visible) {
                if announceSuccess {
                    self.showToast("메뉴 막대로 옮겼어요", "아이콘을 끌어 내리면 다시 꺼낼 수 있어요")
                }
            } else if NSMenu.menuBarVisible() {
                self.setMode(.desktop)
                self.showToast("메뉴 막대가 가득 찼어요", "아이콘이 노치 뒤로 가려져 데스크톱에 남겨 두었어요")
            }
        }
    }

    // 착지 바운스 1회: 살짝 올라간 아이콘을 잠깐 보여 준 뒤 제자리로
    private func bounceIcon() {
        guard let btn = statusItem.button else { return }
        btn.image = make16pxIcon(lift: 1.5)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) { [weak self] in
            self?.statusItem.button?.image = self?.make16pxIcon()
        }
    }

    // 펫 창이 숨은 상태에서도 보이도록 메뉴 막대 아래에 잠깐 띄우는 알림
    private func showToast(_ title: String, _ detail: String) {
        toastPanel?.orderOut(nil)
        let label = NSTextField(labelWithAttributedString: {
            let s = NSMutableAttributedString(string: title + "\n", attributes: [.font: NSFont.boldSystemFont(ofSize: 13)])
            s.append(NSAttributedString(string: detail, attributes: [
                .font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor
            ]))
            return s
        }())
        label.sizeToFit()
        let size = NSSize(width: label.frame.width + 32, height: label.frame.height + 22)
        let box = NSVisualEffectView(frame: NSRect(origin: .zero, size: size))
        box.material = .popover
        box.state = .active
        box.wantsLayer = true
        box.layer?.cornerRadius = 12
        label.frame.origin = NSPoint(x: 16, y: 11)
        box.addSubview(label)

        let vf = (statusItem.button?.window?.screen ?? NSScreen.main)?.visibleFrame ?? .zero
        let anchorX = statusItem.isVisible ? (statusItem.button?.window?.frame.midX ?? vf.midX) : vf.midX
        let x = min(max(anchorX - size.width / 2, vf.minX + 8), vf.maxX - size.width - 8)
        let toast = NSPanel(contentRect: NSRect(x: x, y: vf.maxY - size.height - 8, width: size.width, height: size.height),
                            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        toast.isOpaque = false
        toast.backgroundColor = .clear
        toast.hasShadow = true
        toast.level = .statusBar
        toast.ignoresMouseEvents = true
        toast.contentView = box
        toast.orderFrontRegardless()
        toastPanel = toast
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.6) { [weak self, weak toast] in
            toast?.orderOut(nil)
            if self?.toastPanel === toast { self?.toastPanel = nil }
        }
    }

    private func make16pxIcon(lift: CGFloat = 0) -> NSImage {
        let size = NSSize(width: 16, height: 16)
        let img = NSImage(size: size, flipped: true) { _ in
            let shift = NSAffineTransform()
            shift.translateX(by: 0, yBy: -lift)  // flipped 좌표라 음수가 위쪽
            shift.concat()
            let ink = NSColor(calibratedRed: 0x1A/255.0, green: 0x19/255.0, blue: 0x17/255.0, alpha: 1.0)

            let head = NSBezierPath(roundedRect: NSRect(x: 1, y: 0.8, width: 11.5, height: 5.0), xRadius: 2.5, yRadius: 2.5)
            ink.setFill()
            head.fill()

            // 템플릿 이미지는 알파만 쓰므로 눈은 색을 칠하지 않고 투명하게 뚫는다
            NSRect(x: 6.5, y: 2.0, width: 1.3, height: 2.5).fill(using: .clear)
            NSRect(x: 9.0, y: 2.2, width: 1.2, height: 2.1).fill(using: .clear)

            let body = NSBezierPath(roundedRect: NSRect(x: 1, y: 6.6, width: 9.1, height: 3.8), xRadius: 1.9, yRadius: 1.9)
            ink.setFill()
            body.fill()

            let base = NSBezierPath(roundedRect: NSRect(x: 1, y: 11.2, width: 14.0, height: 3.8), xRadius: 1.9, yRadius: 1.9)
            ink.setFill()
            base.fill()
            return true
        }
        img.isTemplate = true  // 다크 메뉴바에서는 시스템이 흰색으로 바꿔 그린다
        return img
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.autosaveName = "ro.pet"  // ⌘-드래그로 바꾼 막대 안 위치를 기억한다
        if let btn = statusItem.button {
            // 메뉴를 직접 띄워야 '끌어 내리기'와 '눌러서 메뉴'를 구분할 수 있다
            btn.target = self
            btn.action = #selector(statusButtonPressed)
            btn.sendAction(on: [.leftMouseDown, .rightMouseDown])
            btn.image = make16pxIcon()
            btn.toolTip = "ro — 층 셋, 눈 둘. 기록하고, 풀어낸다."
        }
        let menu = NSMenu()
        menu.addItem(makeHeaderItem())
        menu.addItem(NSMenuItem.separator())
        let talkItem = NSMenuItem(title: "패널 열기", action: #selector(toggleBubble), keyEquivalent: "k")
        talkItem.target = self
        menu.addItem(talkItem)

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

        modeItem = NSMenuItem(title: "", action: #selector(toggleMode), keyEquivalent: "")
        modeItem.target = self
        menu.addItem(modeItem)

        let resetItem = NSMenuItem(title: "우측 하단으로 부르기", action: #selector(resetPosition), keyEquivalent: "0")
        resetItem.target = self
        menu.addItem(resetItem)

        // 화면 기록 권한이 없으면 배경 밝기 자동 전환이 조용히 꺼지므로 메뉴에서 알린다
        if !CGPreflightScreenCaptureAccess() {
            let permItem = NSMenuItem(title: "배경 밝기 자동 전환 꺼짐 — 화면 기록 권한 설정 열기…",
                                      action: #selector(openScreenCaptureSettings), keyEquivalent: "")
            permItem.target = self
            menu.addItem(permItem)
        }

        menu.addItem(NSMenuItem.separator())
        let quitItem = NSMenuItem(title: "ro 펫 종료", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        mainMenu = menu
    }

    // 메뉴 첫 줄: 오늘 기록 수와 지금 표정. 숫자는 웹이 폴링할 때 넘겨준다
    private func makeHeaderItem() -> NSMenuItem {
        headerTitle = NSTextField(labelWithString: "오늘 기록 불러오는 중")
        headerTitle.font = NSFont.boldSystemFont(ofSize: 13)
        headerDetail = NSTextField(labelWithString: "CPU 재는 중")
        headerDetail.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        headerDetail.textColor = .secondaryLabelColor
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 240, height: 44))
        headerTitle.frame = NSRect(x: 14, y: 22, width: 220, height: 17)
        headerDetail.frame = NSRect(x: 14, y: 5, width: 220, height: 15)
        view.addSubview(headerTitle)
        view.addSubview(headerDetail)
        let item = NSMenuItem()
        item.view = view
        return item
    }

    @objc private func statusButtonPressed() {
        guard let event = NSApp.currentEvent, let btn = statusItem.button else { return }
        if event.type == .rightMouseDown {
            showStatusMenu()
            return
        }
        // 누른 채 아래로 끌면 펫으로 꺼내고, 그대로 떼면 메뉴를 연다
        let start = NSEvent.mouseLocation
        iconTimer?.invalidate()
        let timer = Timer(timeInterval: 0.008, repeats: true) { [weak self] timer in
            guard let self = self else {
                timer.invalidate()
                return
            }
            let cur = NSEvent.mouseLocation
            if start.y - cur.y > self.popOutDistance {
                timer.invalidate()
                self.iconTimer = nil
                btn.highlight(false)
                self.popOut(at: cur)
            } else if (NSEvent.pressedMouseButtons & 1) == 0 {
                timer.invalidate()
                self.iconTimer = nil
                self.showStatusMenu()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        iconTimer = timer
    }

    private func showStatusMenu() {
        guard let btn = statusItem.button, let win = btn.window else { return }
        btn.highlight(true)
        mainMenu.popUp(positioning: nil, at: NSPoint(x: win.frame.minX, y: win.frame.minY - 4), in: nil)
        btn.highlight(false)
    }

    // 끌어 내린 커서 아래에 펫을 띄우고 그대로 드래그를 이어 받는다
    private func popOut(at mouse: NSPoint) {
        savedDesktopOrigin = nil
        panel.setFrameOrigin(NSPoint(x: mouse.x - compactSize.width / 2, y: mouse.y - compactSize.height + 40))
        setMode(.desktop)
        startNativeDrag(fromPet: true)
    }

    // 메뉴 막대 모드에서 펫(과 패널)이 내려올 자리: 아이콘 바로 아래
    private func menubarAnchorOrigin() -> NSPoint {
        let vf = currentVisibleFrame()
        let midX = statusItem.button?.window?.frame.midX ?? (vf.maxX - compactSize.width / 2)
        return NSPoint(x: midX - compactSize.width / 2, y: vf.maxY - compactSize.height)
    }

    // 모드 전환은 여기 한 곳에서만 한다: 펫 창, 상태 아이콘, 저장
    private func setMode(_ next: PetMode) {
        if next != mode && bubbleOpen {
            webView.evaluateJavaScript("window.toggleBubbleFromHost && window.toggleBubbleFromHost(false);", completionHandler: nil)
            setBubbleState(false)
        }
        if next == .desktop, let origin = savedDesktopOrigin {
            panel.setFrameOrigin(origin)
            savedDesktopOrigin = nil
        }
        mode = next
        defaults.set(next.rawValue, forKey: "ro.mode")
        statusItem.isVisible = (next == .menubar)
        modeItem.title = (next == .menubar) ? "데스크톱에 꺼내기" : "메뉴 막대로 보내기"
        if next == .menubar {
            panel.orderOut(nil)
        } else {
            panel.orderFrontRegardless()
            updateBackdropTheme(force: true)
        }
    }

    // 메뉴 막대 모드에서는 말풍선이 열려 있을 때만 창을 보인다
    private func revealPet() {
        if mode == .desktop || bubbleOpen {
            panel.orderFrontRegardless()
        } else {
            panel.orderOut(nil)
        }
    }

    @objc private func toggleMode() {
        setMode(mode == .desktop ? .menubar : .desktop)
    }

    @objc private func openScreenCaptureSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
            NSWorkspace.shared.open(url)
        }
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
        panel.collectionBehavior = [.canJoinAllSpaces]  // 전체 화면 앱 위에는 띄우지 않는다
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
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        pushGait()
        updateBackdropTheme(force: true)
    }

    private func setupWanderTimer() {
        wanderTimer = Timer.scheduledTimer(withTimeInterval: 3.2, repeats: true) { [weak self] _ in
            guard let self = self, self.mode == .desktop, self.wanderEnabled, !self.bubbleOpen, !self.isDragging else { return }
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

    // 1초마다 전체 CPU 사용률을 읽어 걸음 단계를 정한다. 화면이 꺼지거나 잠기면 쉬고, 돌아오면 처음부터
    private func setupCPUSampler() {
        let nc = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.screensDidSleepNotification, NSWorkspace.sessionDidResignActiveNotification] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in self?.stopCPUSampler() }
        }
        for name in [NSWorkspace.screensDidWakeNotification, NSWorkspace.sessionDidBecomeActiveNotification] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in self?.startCPUSampler() }
        }
        startCPUSampler()
    }

    private func startCPUSampler() {
        stopCPUSampler()
        let timer = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in self?.sampleCPU() }
        timer.tolerance = 0.3  // 다른 타이머와 묶여 깨어나는 횟수를 줄인다
        RunLoop.main.add(timer, forMode: .common)
        cpuTimer = timer
    }

    private func stopCPUSampler() {
        cpuTimer?.invalidate()
        cpuTimer = nil
        lastTicks = nil
        smoother.reset()
    }

    private func sampleCPU() {
        guard let cur = CPUTicks.read() else { return }
        defer { lastTicks = cur }
        guard let prev = lastTicks, let pct = cpuPercent(from: prev, to: cur) else { return }
        let next = smoother.push(pct)
        if next != gait {
            gait = next
            pushGait()
        }
        let shown = Int((smoother.smoothed ?? pct).rounded())
        headerDetail.stringValue = "CPU \(shown)% · \(gait.label)"
    }

    // 웹 펫은 사이클이 끝날 때 바꾼다 (window.setGait)
    private func pushGait() {
        let name = ["calm", "walk", "busy", "hot"][gait.rawValue]
        webView?.evaluateJavaScript("window.setGait && window.setGait('\(name)');", completionHandler: nil)
    }

    // 배경 창이 바뀌거나 다크/라이트 화면이 전환될 때도 0.6초마다 배경 밝기를 감지
    private func setupBackdropTimer() {
        backdropTimer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in
            guard let self = self, self.panel.isVisible, !self.isDragging else { return }
            self.updateBackdropTheme(force: false)
        }
    }

    @objc private func toggleBubble() {
        if mode == .menubar && !bubbleOpen {
            savedDesktopOrigin = savedDesktopOrigin ?? panel.frame.origin
            panel.setFrameOrigin(menubarAnchorOrigin())
        }
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        webView.evaluateJavaScript("window.toggleBubbleFromHost && window.toggleBubbleFromHost();", completionHandler: nil)
    }

    @objc private func previewMood(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        revealPet()
        webView.evaluateJavaScript("window.setMoodPreview && window.setMoodPreview('\(code)', 10000);", completionHandler: nil)
    }

    @objc private func triggerActionFromMenu(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        revealPet()
        webView.evaluateJavaScript("window.triggerAction && window.triggerAction('\(code)', 4500);", completionHandler: nil)
    }

    @objc private func selectMaterial(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        revealPet()
        webView.evaluateJavaScript("window.setMaterialFromHost && window.setMaterialFromHost('\(code)');", completionHandler: nil)
    }

    @objc private func toggleWander() {
        wanderEnabled.toggle()
        wanderItem.title = wanderEnabled ? "산책 모드: 켜짐" : "산책 모드: 꺼짐"
    }

    @objc private func resetPosition() {
        if mode == .menubar {
            setMode(.desktop)
        }
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
        } else if type == "menu" {
            // 데스크톱 모드에선 상태 아이콘이 숨으므로 펫 우클릭이 같은 메뉴로 가는 길이다
            mainMenu.popUp(positioning: nil, at: NSEvent.mouseLocation, in: nil)
        } else if type == "status" {
            let today = (dict["today"] as? NSNumber)?.intValue ?? 0
            headerTitle.stringValue = "오늘 \(today)건 기록"
        } else if type == "bubble" {
            let open = (dict["open"] as? Bool) ?? false
            setBubbleState(open)
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
    } else if args[i] == "--selftest" {
        // 앱을 띄우기 전에 끝낸다 (killOtherInstances가 떠 있는 펫을 죽이지 않게)
        exit(runCPUSelfTest() == 0 ? 0 : 1)
    } else {
        i += 1
    }
}

let app = NSApplication.shared
let delegate = PetAppDelegate(url: urlArg, wander: wanderArg)
app.delegate = delegate
app.run()
