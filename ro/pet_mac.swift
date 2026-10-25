import AppKit
import Carbon
import CoreGraphics
import Darwin
import WebKit

final class FloatingPetPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    // macOS 기본 창 높이 제한(메뉴바 아래로 큰 창 상단이 막히는 현상)을 해제하고 직접 화면 경계로 클램핑한다
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect {
        return frameRect
    }
}

// MARK: - 몸통 아래 시스템 유리
// 웹 화면은 투명 창 뒤를 볼 수 없어 CSS로는 굴절·흐림을 못 낸다. 그래서 몸통 세 층 자리에 시스템 유리를 깐다.
// macOS 26 이상: 뒤 화면을 굴절시키는 유리(NSGlassEffectView). 그보다 낮으면: 뒤 화면을 실제로 흐리는 NSVisualEffectView.
// NSGlassEffectView는 macOS 26 SDK(Swift 6.2)에만 있으므로, 예전 Xcode에서도 빌드되게 컴파일러 버전으로 감싼다.

/// 웹 화면 좌표(왼쪽 위 기준, CSS 픽셀 = 포인트)를 그대로 쓰기 위한 뒤집힌 뷰
final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

enum NativeGlass {
    /// `defaults write ro.pet ro.glass blur` 로 굴절 유리 대신 흐림 유리를 강제할 수 있다(되돌리기: defaults delete ro.pet ro.glass)
    static var forceBlur = false

    /// 창을 늘 활성 모습으로 그리게 했는지(forceActiveAppearance 성공 여부)
    static var activeForced = false

    /// 웹 화면에 알려 줄 유리 종류: "liquid"(굴절) 또는 "blur"(흐림).
    /// 굴절 유리는 창이 비활성이면 뿌옇게 그려진다(펫 창은 다른 앱을 쓰는 동안 늘 비활성이다).
    /// 창을 늘 활성 모습으로 그리게 했으면 항상 굴절 유리, 못 했으면 비활성일 때는 늘 켜 둘 수 있는 흐림 유리(state = .active)로 바꾼다.
    static func kind(active: Bool) -> String {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *), !forceBlur, active || activeForced { return "liquid" }
        #endif
        return "blur"
    }

    /// 비활성 창의 굴절 유리를 활성 모습으로 그리게 하는 공개 API가 없다(NSGlassEffectView엔 state가 없음).
    /// 그래서 AppKit의 비공개 `_setForceMainAppearance:`를 쓴다. 모양만 바꾸고 키 입력·활성 상태는 바꾸지 않는다.
    /// 이 호출이 없어진 macOS에서는 응답하지 않으므로 건너뛰고, 위의 흐림 유리 대체가 그대로 동작한다.
    static func forceActiveAppearance(_ window: NSWindow) {
        let sel = NSSelectorFromString("_setForceMainAppearance:")
        guard window.responds(to: sel), let m = class_getInstanceMethod(type(of: window), sel) else { return }
        typealias SetBool = @convention(c) (AnyObject, Selector, Bool) -> Void
        unsafeBitCast(method_getImplementation(m), to: SetBool.self)(window, sel, true)
        activeForced = true
    }

    static func make(_ kind: String) -> NSView {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *), kind == "liquid" { return NSGlassEffectView() }
        #endif
        let v = NSVisualEffectView()
        v.blendingMode = .behindWindow
        v.state = .active
        return v
    }

    /// 알약 모양(높이의 절반 반지름)으로 frame에 맞추고, 중심을 축으로 rotation도(웹 화면의 시계 방향)만큼 돌린다.
    /// 부모가 뒤집힌 좌표(아래로 +y)라 양수 회전이 화면에서 시계 방향이 되어 CSS rotate와 같은 방향이다. dark면 연기 낀 유리
    static func shape(_ v: NSView, frame: NSRect, rotation: CGFloat, alpha: CGFloat, dark: Bool) {
        v.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        v.frameCenterRotation = 0  // 돌린 채로 frame을 바꾸면 테두리 상자 기준으로 바뀐다
        let sizeChanged = v.frame.size != frame.size
        v.frame = frame
        v.frameCenterRotation = rotation
        v.alphaValue = alpha
        #if compiler(>=6.2)
        if #available(macOS 26.0, *), let g = v as? NSGlassEffectView {
            g.style = .clear  // 뒤가 또렷이 비치고 가장자리에서 휘는 투명 유리(기본 .regular는 뿌옇다)
            g.cornerRadius = frame.height / 2
            g.tintColor = dark ? NSColor.black.withAlphaComponent(0.28) : nil
            return
        }
        #endif
        guard let e = v as? NSVisualEffectView else { return }
        e.material = dark ? .hudWindow : .popover
        if sizeChanged || e.maskImage == nil { e.maskImage = pillMask(frame.size) }
    }

    static func pillMask(_ size: NSSize) -> NSImage {
        NSImage(size: size, flipped: false) { rect in
            NSColor.black.setFill()
            NSBezierPath(roundedRect: rect, xRadius: rect.height / 2, yRadius: rect.height / 2).fill()
            return true
        }
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
    // 아이콘 점프 높이(pt)와 웅크림·늘어남 정도. 바쁨은 더 높이·더 크게 찌그러지고, 과열은 낮고 잦게
    var jumpHeight: Double { [1.0, 1.6, 2.2, 1.0][rawValue] }
    var squash: Double { [0.08, 0.12, 0.18, 0.10][rawValue] }

    // 저전력 모드에서는 '보통 걸음'까지만
    func capped(lowPower: Bool) -> Gait {
        lowPower ? Gait(rawValue: min(rawValue, Gait.walk.rawValue))! : self
    }
}

// MARK: 메뉴 막대 아이콘 점프 (07 시안: 제자리에서 콩). 프레임 3장을 f1·f2·f3·f2·f1 순서로 바꾼다

enum JumpFrame: Int {
    case crouch = 0, rise, apex  // f1 웅크림, f2 솟음, f3 정점
}

// 한 사이클 안에서 프레임을 바꾸는 시점(비율). 이륙 15%·정점 45%·착지 75%, 남은 25%는 바닥에서 숨 고르기.
// 메뉴 막대는 보간이 없으니 시안의 타임라인(0 / 480 / 720 / 960 / 1200ms @1.6초) 그대로 바꾼다
let jumpSchedule: [(at: Double, frame: JumpFrame)] = [
    (0.00, .crouch), (0.30, .rise), (0.45, .apex), (0.60, .rise), (0.75, .crouch),
]

// 아이콘 캔버스 18×18pt. 몸은 16pt 디자인 그대로 두고 위쪽 여유로 뛴다
let iconCanvas: CGFloat = 18

struct IconPose {
    var head: CGRect
    var body: CGRect
    var base: CGRect
    var narrowEyes: Bool
}

// frame이 nil이면 서 있는 기본 자세. 층은 바닥부터 쌓고, 솟을 때는 머리가 먼저 가고 받침이 늦게 따라온다
func iconPose(_ frame: JumpFrame?, gait: Gait) -> IconPose {
    let s = CGFloat(gait.squash), h = CGFloat(gait.jumpHeight)
    var k: CGFloat = 1, wk: CGFloat = 1
    var lift: (head: CGFloat, body: CGFloat, base: CGFloat) = (0, 0, 0)
    switch frame {
    case .crouch?: k = 1 - s; wk = 1 + s / 2
    case .rise?: k = 1 + s / 3; wk = 1 - s / 4; lift = (0.6 * h, 0.5 * h, 0.4 * h)
    case .apex?: lift = (h, h, h)
    case nil: break
    }
    let x: CGFloat = 2, ground: CGFloat = iconCanvas - 1.3, gap = 0.8 * k
    let baseH = 3.8 * k, bodyH = 3.8 * k, headH = 5.0 * k
    let baseY = ground - baseH, bodyY = baseY - gap - bodyH, headY = bodyY - gap - headH
    return IconPose(
        head: CGRect(x: x, y: headY - lift.head, width: 11.5 * wk, height: headH),
        body: CGRect(x: x, y: bodyY - lift.body, width: 9.1 * wk, height: bodyH),
        base: CGRect(x: x, y: baseY - lift.base, width: 14.0 * wk, height: baseH),
        narrowEyes: gait == .hot
    )
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

// MARK: - 과열 알림 (07). "Mac이 힘들어해요"는 1분 넘게 바쁠 때 한 번만

struct RollingMean {
    let capacity: Int
    private(set) var values: [Double] = []

    init(capacity: Int) {
        self.capacity = capacity
    }

    mutating func push(_ v: Double) {
        values.append(v)
        if values.count > capacity { values.removeFirst() }
    }

    var isFull: Bool { values.count == capacity }
    var mean: Double { values.isEmpty ? 0 : values.reduce(0, +) / Double(values.count) }

    mutating func reset() {
        values = []
    }
}

// 원시 CPU의 60초 평균이 85% 이상이면 한 번 울린다. 평균이 75% 아래로 내려가야 다시 울릴 수 있다.
// (시뮬레이션: 진입/해제 규칙은 빌드처럼 잠깐씩 꺼지는 부하에서 안 울리고 평균 83%에서 울렸다)
struct OverheatGate {
    static let enter = 85.0
    static let rearm = 75.0
    private(set) var window = RollingMean(capacity: 60)
    private(set) var armed = true

    mutating func push(_ raw: Double) -> Bool {
        window.push(raw)
        if !armed {
            if window.mean < Self.rearm { armed = true }
            return false
        }
        if window.isFull && window.mean >= Self.enter {
            armed = false
            return true
        }
        return false
    }

    mutating func reset() {
        window.reset()
        armed = true
    }
}

// proc_pid_rusage의 시간은 나노초가 아니라 mach 틱이다 (Apple Silicon timebase 125/3)
func machToSeconds(_ ticks: UInt64, numer: UInt32, denom: UInt32) -> Double {
    Double(ticks) * Double(numer) / Double(denom) / 1e9
}

// 실행 파일 경로에서 가장 바깥 .app 이름. Chrome Helper 수십 개가 'Google Chrome' 하나로 묶인다
func appName(forExecutablePath path: String) -> String {
    let parts = path.split(separator: "/")
    if let app = parts.first(where: { $0.hasSuffix(".app") }) {
        return String(app.dropLast(4))
    }
    return String(parts.last ?? "")
}

// 1위 앱의 몫(%)이 읽지 못한 몫(시스템·root 프로세스)보다 클 때만 이름을 댄다. 엉뚱한 앱을 지목하지 않게
func overheatCulprit(_ usage: [String: Double], unreadable: Double) -> (name: String, share: Double)? {
    guard let top = usage.max(by: { $0.value < $1.value }), top.value > 0, top.value > unreadable else { return nil }
    return (top.key, top.value)
}

// 같은 앱은 하루 한 번, '오늘은 그만'이면 그날은 더 없다. 앱 이름이 없으면 ""로 센다
func shouldAlertOverheat(app: String?, today: String, memoDay: String?, muted: Bool, alerted: [String]) -> Bool {
    guard memoDay == today else { return true }
    if muted { return false }
    return !alerted.contains(app ?? "")
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
    check(Gait.hot.capped(lowPower: true) == .walk && Gait.busy.capped(lowPower: false) == .busy
          && Gait.calm.capped(lowPower: true) == .calm, "저전력은 '보통 걸음'까지")
    check(jumpSchedule.map(\.frame) == [.crouch, .rise, .apex, .rise, .crouch]
          && jumpSchedule.first?.at == 0 && jumpSchedule.last?.at == 0.75, "f1 → f2 → f3 → f2 → f1, 착지 75%")
    check(zip(jumpSchedule, jumpSchedule.dropFirst()).allSatisfy { $0.at < $1.at }, "프레임 시점이 앞으로만 간다")
    let canvas = CGRect(x: 0, y: 0, width: iconCanvas, height: iconCanvas)
    let inside = Gait.allCases.allSatisfy { g in
        ([nil] + [JumpFrame.crouch, .rise, .apex]).allSatisfy { f in
            let p = iconPose(f, gait: g)
            return [p.head, p.body, p.base].allSatisfy { canvas.contains($0) }
        }
    }
    check(inside, "모든 단계·프레임이 18pt 캔버스 안에 있다")
    let apexTop = { (g: Gait) in iconPose(.apex, gait: g).head.minY }
    check(apexTop(.busy) < apexTop(.walk) && apexTop(.walk) < apexTop(.calm), "바쁠수록 더 높이 뛴다")
    check(apexTop(.hot) > apexTop(.busy), "과열은 낮게 뛴다")
    let crouch = iconPose(.crouch, gait: .walk), stand = iconPose(nil, gait: .walk)
    check(crouch.base.width > stand.base.width && crouch.head.minY > stand.head.minY, "웅크리면 낮고 넓어진다")
    let rise = iconPose(.rise, gait: .walk)
    check(stand.base.minY - rise.base.minY < stand.head.minY - rise.head.minY, "솟을 때 받침이 머리보다 늦게 따라온다")
    check(iconPose(.apex, gait: .hot).narrowEyes && !iconPose(.apex, gait: .busy).narrowEyes, "과열만 눈을 가늘게")

    func fires(_ values: [Double]) -> [Int] {
        var g = OverheatGate()
        return values.enumerated().compactMap { g.push($0.element) ? $0.offset : nil }
    }
    check(fires(Array(repeating: 95, count: 120)) == [59], "95%가 60초 이어지면 60초째에 한 번만")
    check(fires(Array(repeating: 95, count: 70) + Array(repeating: 60, count: 70) + Array(repeating: 95, count: 70)).count == 2,
          "평균이 75% 아래로 내려간 뒤에만 다시 울린다")
    check(fires((0..<300).map { _ in 83.0 }).isEmpty, "평균 83%로 오래 바빠도 울리지 않는다")
    let build = (0..<200).map { $0 % 40 < 34 ? 92.0 : 60.0 }  // 링크 때마다 6초씩 60%로 꺼짐 → 평균 87%
    check(!fires(build).isEmpty, "빌드처럼 잠깐씩 꺼지는 부하도 평균이 85% 넘으면 울린다")
    check(machToSeconds(24_000_000, numer: 125, denom: 3) == 1.0, "mach 틱을 timebase로 초로 바꾼다")
    let helper = "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)"
    check(appName(forExecutablePath: helper) == "Google Chrome", "Chrome Helper는 Google Chrome으로 묶는다")
    check(appName(forExecutablePath: "/usr/sbin/mds_stores") == "mds_stores", ".app 밖이면 실행 파일 이름")
    check(overheatCulprit(["Chrome": 40, "Xcode": 10], unreadable: 30)?.name == "Chrome", "1위가 읽지 못한 몫보다 크면 이름을 댄다")
    check(overheatCulprit(["Chrome": 40, "Xcode": 10], unreadable: 50) == nil, "읽지 못한 몫이 더 크면 이름 없이")
    check(shouldAlertOverheat(app: "Chrome", today: "d2", memoDay: "d1", muted: true, alerted: ["Chrome"]), "날이 바뀌면 다시 알린다")
    check(!shouldAlertOverheat(app: "Xcode", today: "d1", memoDay: "d1", muted: true, alerted: []), "'오늘은 그만'이면 그날은 없다")
    check(!shouldAlertOverheat(app: "Chrome", today: "d1", memoDay: "d1", muted: false, alerted: ["Chrome"]), "같은 앱은 하루 한 번")
    check(shouldAlertOverheat(app: "Xcode", today: "d1", memoDay: "d1", muted: false, alerted: ["Chrome"]), "다른 앱은 알린다")
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
    private var glassLayer: FlippedView!   // 웹 화면 아래, 몸통 자리에 시스템 유리를 까는 층
    private var glassViews: [NSView] = []
    private var glassViewsKind = ""        // glassViews가 어느 유리로 만들어졌는지("liquid"/"blur")
    private var glassKind: String { NativeGlass.kind(active: panel.isKeyWindow) }
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
    private var dragTimer: Timer?
    private var fallTimer: Timer?
    private var isDragging: Bool = false
    private var hotKeyRef: EventHotKeyRef? = nil

    // 말풍선이 닫혀 있을 때는 캐릭터(23u×22u)+캡션+그림자+점프/흔들기 여유 공간(176x168)을 확보해 잘림 방지
    private let compactSize = NSSize(width: 176, height: 168)
    private let expandedSize = NSSize(width: 360, height: 530)
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
    private var iconJumpTimer: Timer?
    private var jumpStep = 0
    private var iconGait: Gait = .calm  // 지금 아이콘이 뛰고 있는 단계. 착지(f1)할 때만 gait를 따라간다
    private var iconImages: [Int: NSImage] = [:]
    private var overheatGate = OverheatGate()
    private var overheatScanning = false
    private var alertPanel: NSPanel?

    init(url: String, wander: Bool) {
        self.urlString = url
        self.wanderEnabled = wander
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        killOtherInstances()
        NSApp.setActivationPolicy(.accessory)
        mode = PetMode(rawValue: defaults.string(forKey: "ro.mode") ?? "") ?? .desktop
        if wanderEnabled, defaults.object(forKey: "ro.wander") != nil {
            wanderEnabled = defaults.bool(forKey: "ro.wander")
        }
        setupStatusItem()
        setupFloatingPanel()
        setMode(mode)
        ensureIconVisible(announceSuccess: false)  // 지난번 메뉴 막대 모드로 시작했는데 아이콘이 가려진 경우
        setupWanderTimer()
        setupCPUSampler()
        observeIconConditions()
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
        if mode == .menubar && !bubbleOpen {
            savedDesktopOrigin = savedDesktopOrigin ?? panel.frame.origin
            panel.setFrameOrigin(menubarAnchorOrigin())
        }
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
            refreshGlass()
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
            refreshGlass()
            revealPet()
        }
    }

    // 네이티브 120fps 마우스 드래그 추적 (이동 중에도 배경 밝기를 실시간 감지해 분필/먹 자동 전환)
    private func startNativeDrag(fromPet: Bool) {
        dragTimer?.invalidate()
        let startMouse = NSEvent.mouseLocation
        let startOrigin = panel.frame.origin
        var moved = false
        var tick = 0
        // 메뉴 막대로 보내기는 데스크톱 펫(말풍선 닫힘)을 끌 때만
        let canDock = fromPet && mode == .desktop && !bubbleOpen
        var inZone = false
        var lastProgress: CGFloat = 0
        isDragging = true

        if fromPet {
            // 누르는 순간 light 햅틱 1회 (트랙패드가 있는 맥에서만 느껴진다)
            NSHapticFeedbackManager.defaultPerformer.perform(.generic, performanceTime: .now)
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
            tick += 1

            if hypot(dx, dy) > 8.0 {
                moved = true
            }
            if moved {
                let target = NSPoint(x: startOrigin.x + dx, y: startOrigin.y + dy)
                let clamped = self.clampOrigin(target, size: self.panel.frame.size)
                self.panel.setFrameOrigin(clamped)
                // 모션 엔진에 머리(창)의 위치를 스트리밍한다(약 60Hz). 속도·체인·기울기·흔들기 감지는 JS가 계산한다.
                if fromPet && tick % 2 == 0 {
                    self.webView.evaluateJavaScript(
                        "window.onHostDragMove && window.onHostDragMove(\(clamped.x), \(-clamped.y));",
                        completionHandler: nil
                    )
                }
            }
            if canDock && moved {
                let top = (self.panel.screen ?? NSScreen.main)?.visibleFrame.maxY ?? curMouse.y
                let d = max(0, top - curMouse.y)
                let nowIn = d < self.dockZone
                if nowIn != inZone {
                    inZone = nowIn
                    if nowIn { self.pinStatusItemFirst() }
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
                if moved && self.mode == .desktop { self.savePosition(delay: 0.5) }  // 낙하가 끝난 자리
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
        RunLoop.main.add(timer, forMode: .common)
        dragTimer = timer
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
                timer.invalidate()
                self.fallTimer = nil
                return
            }
            self.panel.setFrameOrigin(NSPoint(x: startX, y: startY - 0.5 * g * t * t))
        }
        return fall
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
        btn.image = makeIcon(iconPose(.apex, gait: .walk))
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) { [weak self] in
            self?.statusItem.button?.image = self?.makeIcon(iconPose(nil, gait: .calm))
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

    private func makeIcon(_ pose: IconPose) -> NSImage {
        let img = NSImage(size: NSSize(width: iconCanvas, height: iconCanvas), flipped: true) { _ in
            let ink = NSColor(calibratedRed: 0x1A/255.0, green: 0x19/255.0, blue: 0x17/255.0, alpha: 1.0)
            ink.setFill()
            // 기기 픽셀에 맞춘다. 안 맞추면 1x 화면에서 0.8pt 층 사이와 1.3pt 눈이 회색으로 번진다 (9단계 확인).
            // 위치와 크기를 따로 반올림해 두 눈 크기가 같게, 층은 받침부터 쌓아 층 사이가 1px 아래로 붙지 않게 한다
            let px = max(1, NSGraphicsContext.current?.cgContext.ctm.a ?? 2)
            func round(_ v: CGFloat) -> CGFloat { (v * px).rounded() / px }
            func snap(_ r: NSRect) -> NSRect {
                NSRect(x: round(r.minX), y: round(r.minY), width: max(1 / px, round(r.width)), height: max(1 / px, round(r.height)))
            }
            let base = snap(pose.base)
            var body = snap(pose.body)
            body.origin.y = base.minY - max(1 / px, round(pose.base.minY - pose.body.maxY)) - body.height
            var head = snap(pose.head)
            head.origin.y = body.minY - max(1 / px, round(pose.body.minY - pose.head.maxY)) - head.height
            for r in [head, body, base] {
                let radius = min(r.height / 2, 2.5)
                NSBezierPath(roundedRect: r, xRadius: radius, yRadius: radius).fill()
            }
            // 템플릿 이미지는 알파만 쓰므로 눈은 색을 칠하지 않고 투명하게 뚫는다. 과열이면 가늘게 뜬다
            let hd = head
            let eyes = pose.narrowEyes
                ? [NSRect(x: hd.minX + 5.3, y: hd.minY + hd.height * 0.42, width: 1.7, height: 1.0),
                   NSRect(x: hd.minX + 7.9, y: hd.minY + hd.height * 0.42, width: 1.5, height: 1.0)]
                : [NSRect(x: hd.minX + 5.5, y: hd.minY + hd.height * 0.24, width: 1.3, height: hd.height * 0.5),
                   NSRect(x: hd.minX + 8.0, y: hd.minY + hd.height * 0.28, width: 1.2, height: hd.height * 0.42)]
            NSGraphicsContext.current?.compositingOperation = .clear
            // 눈은 크기를 먼저 반올림하고 원래 중심에 다시 놓는다 (두 눈 높이가 어긋나 보이지 않게)
            func snapEye(_ e: NSRect) -> NSRect {
                // 높이는 내림: 1x에서 2.5pt·2.1pt 눈이 3px·2px로 갈려 한쪽이 들려 보이던 것을 막는다 (2x는 5px·4px 유지)
                let w = max(1 / px, round(e.width)), h = max(1 / px, (e.height * px).rounded(.down) / px)
                return NSRect(x: round(e.midX - w / 2), y: round(e.midY - h / 2), width: w, height: h)
            }
            for e in eyes.map(snapEye) {
                NSBezierPath(roundedRect: e, xRadius: 0.5, yRadius: 0.5).fill()
            }
            NSGraphicsContext.current?.compositingOperation = .sourceOver
            return true
        }
        img.isTemplate = true  // 다크 메뉴바에서는 시스템이 흰색으로 바꿔 그린다
        return img
    }

    private let statusAutosaveName = "ro.pet"

    /// 메뉴 막대로 보낸 ro 아이콘이 막대 아이콘 줄의 첫 칸(시계 쪽 끝)에 놓이게 한다.
    /// AppKit은 자리를 'NSStatusItem Preferred Position <이름>'(오른쪽 끝에서의 거리)으로 기억하므로,
    /// 아이콘을 보이기 직전마다 가장 작은 값으로 되돌린다.
    private func pinStatusItemFirst() {
        UserDefaults.standard.set(1, forKey: "NSStatusItem Preferred Position \(statusAutosaveName)")
    }

    private func setupStatusItem() {
        pinStatusItemFirst()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.autosaveName = statusAutosaveName
        if let btn = statusItem.button {
            // 메뉴를 직접 띄워야 '끌어 내리기'와 '눌러서 메뉴'를 구분할 수 있다
            btn.target = self
            btn.action = #selector(statusButtonPressed)
            btn.sendAction(on: [.leftMouseDown, .rightMouseDown])
            btn.image = makeIcon(iconPose(nil, gait: .calm))
            btn.toolTip = "ro — 층 셋, 눈 둘. 기록하고, 풀어낸다."
        }
        let menu = NSMenu()
        menu.addItem(makeHeaderItem())
        menu.addItem(NSMenuItem.separator())
        let talkItem = NSMenuItem(title: "패널 열기", action: #selector(toggleBubble), keyEquivalent: "k")
        talkItem.target = self
        menu.addItem(talkItem)
        let paletteItem = NSMenuItem(title: "명령 팔레트 (⌥R)", action: #selector(openPalette), keyEquivalent: "")
        paletteItem.target = self
        menu.addItem(paletteItem)

        // 표정·모션은 설정이 아니라 구경용이라 '미리보기' 하위 메뉴 하나로 묶는다
        let previewMenu = NSMenu()
        let moods: [(String, String)] = [
            ("기본", "idle"), ("기록 중", "recording"), ("집중", "focus"),
            ("확인 필요", "anxious"), ("발견", "discovery"), ("해결", "resolved")
        ]
        previewMenu.addItem(menuSection("표정"))
        for (title, code) in moods {
            let item = NSMenuItem(title: title, action: #selector(previewMood(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = code
            previewMenu.addItem(item)
        }
        let actions: [(String, String)] = [
            ("숨쉬기", "breathe"), ("두리번", "look_around"), ("걷기", "walk"), ("생각", "thinking"),
            ("깜짝", "alert"), ("졸음", "drowsy"), ("시선", "gaze"), ("누름", "press"), ("쓰다듬기", "pet"),
            ("끌기", "drag"), ("흔들기", "shake"), ("착지", "drop"), ("성공", "success"), ("실패", "failure")
        ]
        previewMenu.addItem(NSMenuItem.separator())
        previewMenu.addItem(menuSection("모션"))
        for (title, code) in actions {
            let item = NSMenuItem(title: title, action: #selector(triggerActionFromMenu(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = code
            previewMenu.addItem(item)
        }
        let previewRoot = NSMenuItem(title: "표정·모션 미리보기", action: nil, keyEquivalent: "")
        previewRoot.submenu = previewMenu

        let matMenu = NSMenu()
        let materials: [(String, String)] = [
            ("점토", "clay"), ("유리", "glass")
        ]
        for (title, code) in materials {
            let item = NSMenuItem(title: title, action: #selector(selectMaterial(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = code
            matMenu.addItem(item)
        }
        let matRoot = NSMenuItem(title: "재질", action: nil, keyEquivalent: "")
        matRoot.submenu = matMenu
        menu.addItem(NSMenuItem.separator())
        menu.addItem(matRoot)

        wanderItem = NSMenuItem(title: "돌아다니기", action: #selector(toggleWander), keyEquivalent: "w")
        wanderItem.target = self
        wanderItem.state = wanderEnabled ? .on : .off
        menu.addItem(wanderItem)
        menu.addItem(previewRoot)

        modeItem = NSMenuItem(title: "", action: #selector(toggleMode), keyEquivalent: "")
        modeItem.target = self
        menu.addItem(modeItem)

        let resetItem = NSMenuItem(title: "우측 하단으로 부르기", action: #selector(resetPosition), keyEquivalent: "0")
        resetItem.target = self
        menu.addItem(resetItem)

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
        if next == .menubar { pinStatusItemFirst() }
        statusItem.isVisible = (next == .menubar)
        DispatchQueue.main.async { [weak self] in self?.updateIconAnimation() }  // 아이콘 창 배치 뒤에
        modeItem.title = (next == .menubar) ? "데스크톱에 꺼내기" : "메뉴 막대로 보내기"
        if next == .menubar {
            panel.orderOut(nil)
        } else {
            panel.orderFrontRegardless()
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

    private func setupFloatingPanel() {
        let vf = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        var origin = NSPoint(x: vf.maxX - compactSize.width - 20, y: vf.minY + 18)
        // 지난번에 놓아 둔 자리(그 화면이 아직 있을 때만)
        if defaults.object(forKey: "ro.pos.x") != nil {
            let saved = NSPoint(x: defaults.double(forKey: "ro.pos.x"), y: defaults.double(forKey: "ro.pos.y"))
            let r = NSRect(origin: saved, size: compactSize)
            if NSScreen.screens.contains(where: { $0.visibleFrame.intersects(r.insetBy(dx: 40, dy: 40)) }) {
                origin = saved
            }
        }
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
        webView.autoresizingMask = [.width, .height]

        // 시스템 유리는 웹 화면 아래에 깔리고, 마우스는 위의 웹 화면이 받는다
        NativeGlass.forceBlur = defaults.string(forKey: "ro.glass") == "blur"
        if !NativeGlass.forceBlur { NativeGlass.forceActiveAppearance(panel) }
        let container = FlippedView(frame: NSRect(origin: .zero, size: compactSize))
        glassLayer = FlippedView(frame: container.bounds)
        glassLayer.autoresizingMask = [.width, .height]
        glassLayer.isHidden = true
        container.addSubview(glassLayer)
        container.addSubview(webView)
        panel.contentView = container
        // 펫 창이 활성/비활성으로 바뀌면 굴절 유리 ↔ 흐림 유리를 바꾼다
        for name in [NSWindow.didBecomeKeyNotification, NSWindow.didResignKeyNotification] {
            NotificationCenter.default.addObserver(forName: name, object: panel, queue: .main) { [weak self] _ in
                self?.refreshGlass()
            }
        }
        if let url = URL(string: urlString) {
            webView.load(URLRequest(url: url))
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.evaluateJavaScript("window.setWanderState && window.setWanderState(\(wanderEnabled));", completionHandler: nil)
        webView.evaluateJavaScript("window.setNativeGlass && window.setNativeGlass('\(glassKind)');", completionHandler: nil)
    }

    /// 창 크기나 활성 상태가 바뀌면, 예전 유리를 걷고 웹 화면에 유리 종류를 다시 알려 자리를 다시 받는다
    private func refreshGlass() {
        glassLayer.isHidden = true
        webView.evaluateJavaScript("window.setNativeGlass && window.setNativeGlass('\(glassKind)');", completionHandler: nil)
    }

    /// 웹 화면이 알려 준 몸통 세 층의 자리·기울기·불투명도에 유리를 맞춘다. on=false(재질이 다르거나 펫이 숨음)면 걷는다
    private func updateGlass(on: Bool, rects: [[Double]], dark: Bool) {
        guard on, rects.count == 3, rects.allSatisfy({ $0.count == 6 }) else {
            glassLayer.isHidden = true
            return
        }
        let kind = glassKind
        if glassViewsKind != kind {
            glassViews.forEach { $0.removeFromSuperview() }
            glassViews = []
            glassViewsKind = kind
        }
        while glassViews.count < rects.count {
            let v = NativeGlass.make(kind)
            glassLayer.addSubview(v)
            glassViews.append(v)
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)  // 숨쉬기·흔들기를 따라갈 때 미끄러지는 애니메이션 없이 바로 옮긴다
        for (v, r) in zip(glassViews, rects) {
            NativeGlass.shape(v, frame: NSRect(x: r[0], y: r[1], width: r[2], height: r[3]),
                              rotation: CGFloat(r[4]), alpha: CGFloat(r[5]), dark: dark)
        }
        CATransaction.commit()
        glassLayer.isHidden = false
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
        overheatGate.reset()
    }

    private func sampleCPU() {
        guard let cur = CPUTicks.read() else { return }
        defer { lastTicks = cur }
        guard let prev = lastTicks, let pct = cpuPercent(from: prev, to: cur) else { return }
        let next = smoother.push(pct)
        if next != gait {
            gait = next
            updateIconAnimation()
        }
        let shown = Int((smoother.smoothed ?? pct).rounded())
        headerDetail.stringValue = "CPU \(shown)% · \(gait.label)"
        if overheatGate.push(pct) {
            startOverheatScan(busy: overheatGate.window.mean)
        }
    }

    // MARK: 과열 알림. 앱별 사용량은 그때만 2초 재고, 이름은 이 Mac 밖으로도 로그로도 나가지 않는다

    private func todayString() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date())
    }

    private func startOverheatScan(busy: Double) {
        let today = todayString()
        let memoDay = defaults.string(forKey: "ro.overheat.day")
        if memoDay == today && defaults.bool(forKey: "ro.overheat.muted") { return }  // 오늘은 그만
        guard !overheatScanning else { return }
        overheatScanning = true
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let culprit = Self.scanTopApp()
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.overheatScanning = false
                self.presentOverheat(culprit: culprit, busy: busy)
            }
        }
    }

    // 프로세스별 CPU 시간(초)과 앱 이름. rusage를 못 읽는 프로세스(시스템·root)는 빠진다
    private static func processCPU() -> [pid_t: (app: String, seconds: Double)] {
        var tb = mach_timebase_info()
        mach_timebase_info(&tb)
        let n = proc_listallpids(nil, 0)
        guard n > 0 else { return [:] }
        var pids = [pid_t](repeating: 0, count: Int(n) * 2)
        let got = proc_listallpids(&pids, Int32(pids.count * MemoryLayout<pid_t>.size))
        var out: [pid_t: (String, Double)] = [:]
        var pathBuf = [CChar](repeating: 0, count: 4096)
        for pid in pids.prefix(Int(max(0, got))) where pid > 0 {
            var info = rusage_info_v4()
            let rc = withUnsafeMutablePointer(to: &info) {
                $0.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) { proc_pid_rusage(pid, RUSAGE_INFO_V4, $0) }
            }
            guard rc == 0 else { continue }
            let len = proc_pidpath(pid, &pathBuf, UInt32(pathBuf.count))
            let path = len > 0 ? String(cString: pathBuf) : ""
            let secs = machToSeconds(info.ri_user_time + info.ri_system_time, numer: tb.numer, denom: tb.denom)
            out[pid] = (appName(forExecutablePath: path), secs)
        }
        return out
    }

    // 2초 동안 앱별로 CPU의 몇 %를 썼는지 재고, 읽지 못한 몫과 비교해 이름을 댈지 정한다
    private static func scanTopApp() -> (name: String, share: Double)? {
        guard let t0 = CPUTicks.read() else { return nil }
        let a = processCPU()
        Thread.sleep(forTimeInterval: 2.0)
        guard let t1 = CPUTicks.read(), let busy = cpuPercent(from: t0, to: t1) else { return nil }
        let b = processCPU()
        let capacity = Double(t1.total &- t0.total) / Double(sysconf(Int32(_SC_CLK_TCK)))  // 코어 수 × 경과 초
        guard capacity > 0 else { return nil }
        var usage: [String: Double] = [:]
        for (pid, now) in b {
            guard let before = a[pid], now.seconds >= before.seconds else { continue }
            usage[now.app, default: 0] += (now.seconds - before.seconds) / capacity * 100
        }
        let readable = usage.values.reduce(0, +)
        return overheatCulprit(usage, unreadable: max(0, busy - readable))
    }

    private func presentOverheat(culprit: (name: String, share: Double)?, busy: Double) {
        let today = todayString()
        let memoDay = defaults.string(forKey: "ro.overheat.day")
        var alerted = (memoDay == today) ? (defaults.stringArray(forKey: "ro.overheat.apps") ?? []) : []
        guard shouldAlertOverheat(app: culprit?.name, today: today, memoDay: memoDay,
                                  muted: defaults.bool(forKey: "ro.overheat.muted"), alerted: alerted) else { return }
        if memoDay != today { defaults.set(false, forKey: "ro.overheat.muted") }
        alerted.append(culprit?.name ?? "")
        defaults.set(today, forKey: "ro.overheat.day")
        defaults.set(alerted, forKey: "ro.overheat.apps")

        // CPU %는 메뉴의 숫자와 같은 'Mac 전체 대비'다 (활동 모니터의 코어당 %와 다르다)
        let detail = culprit.map { "가장 많이 쓰는 앱: \($0.name) (CPU \(Int($0.share.rounded()))%) · 1분째 바빠요" }
            ?? "1분째 CPU \(Int(busy.rounded()))%로 바빠요 · 어떤 앱인지는 확인할 수 없어요"
        showOverheatCard(detail: detail)
    }

    private func showOverheatCard(detail: String) {
        alertPanel?.orderOut(nil)
        let title = NSTextField(labelWithString: "Mac이 힘들어해요")
        title.font = NSFont.boldSystemFont(ofSize: 13)
        let body = NSTextField(wrappingLabelWithString: detail)
        body.font = NSFont.systemFont(ofSize: 12)
        body.textColor = .secondaryLabelColor
        body.preferredMaxLayoutWidth = 260
        let open = NSButton(title: "활동 모니터 열기", target: self, action: #selector(openActivityMonitor))
        open.bezelStyle = .rounded
        open.keyEquivalent = "\r"
        let mute = NSButton(title: "오늘은 그만", target: self, action: #selector(muteOverheatToday))
        mute.bezelStyle = .inline
        let buttons = NSStackView(views: [open, mute])
        buttons.spacing = 10
        let stack = NSStackView(views: [title, body, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.edgeInsets = NSEdgeInsets(top: 14, left: 16, bottom: 14, right: 16)
        let size = stack.fittingSize

        let box = NSVisualEffectView(frame: NSRect(origin: .zero, size: size))
        box.material = .popover
        box.state = .active
        box.wantsLayer = true
        box.layer?.cornerRadius = 12
        stack.frame = box.bounds
        box.addSubview(stack)

        // 메뉴 막대 모드는 아이콘 아래, 데스크톱 모드는 펫 옆
        let vf = currentVisibleFrame()
        var origin: NSPoint
        if mode == .menubar, let icon = statusItem.button?.window?.frame {
            origin = NSPoint(x: icon.midX - size.width / 2, y: vf.maxY - size.height - 8)
        } else {
            let pet = panel.frame
            origin = NSPoint(x: pet.minX - size.width - 8, y: pet.maxY - size.height)
            if origin.x < vf.minX + 8 { origin.x = pet.maxX + 8 }
        }
        origin.x = min(max(origin.x, vf.minX + 8), vf.maxX - size.width - 8)
        origin.y = min(max(origin.y, vf.minY + 8), vf.maxY - size.height - 8)
        let card = NSPanel(contentRect: NSRect(origin: origin, size: size),
                           styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        card.isOpaque = false
        card.backgroundColor = .clear
        card.hasShadow = true
        card.level = .statusBar
        card.becomesKeyOnlyIfNeeded = true
        card.contentView = box
        card.orderFrontRegardless()
        alertPanel = card
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self, weak card] in
            card?.orderOut(nil)
            if self?.alertPanel === card { self?.alertPanel = nil }
        }
    }

    @objc private func openActivityMonitor() {
        alertPanel?.orderOut(nil)
        if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.ActivityMonitor") {
            NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration())
        }
    }

    @objc private func muteOverheatToday() {
        alertPanel?.orderOut(nil)
        defaults.set(todayString(), forKey: "ro.overheat.day")
        defaults.set(true, forKey: "ro.overheat.muted")
    }

    // MARK: 메뉴 막대 아이콘 걸음 (07). 데스크톱 펫은 CPU에 맞춰 걷지 않는다

    // 아이콘이 보이고(메뉴 막대 모드, 노치에 안 가림) '동작 줄이기'가 꺼져 있을 때만 뛴다
    private func updateIconAnimation() {
        let visible = statusItem.button?.window?.occlusionState.contains(.visible) ?? false
        let animate = mode == .menubar && visible && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        guard animate else {
            iconJumpTimer?.invalidate()
            iconJumpTimer = nil
            jumpStep = 0
            statusItem.button?.image = makeIcon(iconPose(nil, gait: .calm))
            return
        }
        if iconJumpTimer == nil {
            jumpStep = 0
            iconGait = targetIconGait()
            showJumpStep()
        }
    }

    private func targetIconGait() -> Gait {
        gait.capped(lowPower: ProcessInfo.processInfo.isLowPowerModeEnabled)
    }

    // 지금 단계의 프레임을 보여 주고 다음 시점까지 기다린다. 사이클 길이는 0.8초 상한(menubarPeriod)
    private func showJumpStep() {
        let step = jumpSchedule[jumpStep]
        statusItem.button?.image = jumpImage(step.frame, gait: iconGait)
        let nextAt = jumpStep + 1 < jumpSchedule.count ? jumpSchedule[jumpStep + 1].at : 1.0
        let delay = (nextAt - step.at) * iconGait.menubarPeriod
        let timer = Timer(timeInterval: delay, repeats: false) { [weak self] _ in
            guard let self = self else { return }
            self.jumpStep = (self.jumpStep + 1) % jumpSchedule.count
            // 착지해 다시 웅크릴 때만 빠르기를 바꾼다 (공중에서 끊기지 않게)
            if self.jumpStep == 0 {
                self.iconGait = self.targetIconGait()
            }
            self.showJumpStep()
        }
        timer.tolerance = delay * 0.1
        RunLoop.main.add(timer, forMode: .common)
        iconJumpTimer = timer
    }

    private func jumpImage(_ frame: JumpFrame, gait g: Gait) -> NSImage {
        let key = g.rawValue * 3 + frame.rawValue
        if let img = iconImages[key] { return img }
        let img = makeIcon(iconPose(frame, gait: g))
        iconImages[key] = img
        return img
    }

    private func observeIconConditions() {
        let refresh: (Notification) -> Void = { [weak self] _ in self?.updateIconAnimation() }
        NotificationCenter.default.addObserver(forName: Notification.Name.NSProcessInfoPowerStateDidChange,
                                               object: nil, queue: .main, using: refresh)
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification, object: nil, queue: .main, using: refresh)
        // 노치 뒤로 밀리거나 전체 화면으로 메뉴 막대가 숨으면 그리기를 멈춘다
        NotificationCenter.default.addObserver(forName: NSWindow.didChangeOcclusionStateNotification,
                                               object: nil, queue: .main) { [weak self] note in
            guard let self = self, (note.object as? NSWindow) === self.statusItem.button?.window else { return }
            self.updateIconAnimation()
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
        wanderItem.state = wanderEnabled ? .on : .off
        defaults.set(wanderEnabled, forKey: "ro.wander")
        webView?.evaluateJavaScript("window.setWanderState && window.setWanderState(\(wanderEnabled));", completionHandler: nil)
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
        savePosition(delay: 0.4)
    }

    private func menuSection(_ title: String) -> NSMenuItem {
        if #available(macOS 14.0, *) { return NSMenuItem.sectionHeader(title: title) }
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    /// 데스크톱 펫 자리(말풍선을 접은 기준)를 저장해 다음 실행 때 같은 곳에 띄운다.
    private func savePosition(delay: TimeInterval) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self, self.mode == .desktop else { return }
            let o = self.clampOrigin(self.currentPetOrigin(), size: self.compactSize)
            self.defaults.set(Double(o.x), forKey: "ro.pos.x")
            self.defaults.set(Double(o.y), forKey: "ro.pos.y")
        }
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
        } else if type == "menu" {
            // 데스크톱 모드에선 상태 아이콘이 숨으므로 펫 우클릭이 같은 메뉴로 가는 길이다
            mainMenu.popUp(positioning: nil, at: NSEvent.mouseLocation, in: nil)
        } else if type == "status" {
            let today = (dict["today"] as? NSNumber)?.intValue ?? 0
            headerTitle.stringValue = "오늘 \(today)건 기록"
        } else if type == "bubble" {
            let open = (dict["open"] as? Bool) ?? false
            setBubbleState(open)
        } else if type == "glass" {
            let rects = (dict["rects"] as? [[NSNumber]])?.map { $0.map { $0.doubleValue } } ?? []
            updateGlass(on: (dict["on"] as? Bool) ?? false, rects: rects, dark: (dict["dark"] as? Bool) ?? false)
        } else if type == "toggleWander" {
            let enabled = (dict["enabled"] as? Bool) ?? !wanderEnabled
            wanderEnabled = enabled
            wanderItem?.state = wanderEnabled ? .on : .off
            defaults.set(wanderEnabled, forKey: "ro.wander")
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
