final class MockEffects: RelativeMouseEffects {
    var associations: [Bool] = []
    var associationResults: [Bool] = []
    var monitorAllowed = true
    var monitors: [(Double, Double) -> Void] = []
    var removed = 0
    var retries: [() -> Void] = []
    var deltas: [(AnyObject, Int, Int, String, Int)] = []
    func associateCursor(_ value: Bool) -> Bool {
        associations.append(value)
        return associationResults.isEmpty ? true : associationResults.removeFirst()
    }
    func beginMonitor(_ delta: @escaping (Double, Double) -> Void) -> Any? {
        if !monitorAllowed { return nil }
        monitors.append(delta)
        return monitors.count
    }
    func removeMonitor(_ monitor: Any) { removed += 1 }
    func retryLater(_ operation: @escaping () -> Void) { retries.append(operation) }
    func sendDelta(_ lease: RelativeMouseLease, dx: Int, dy: Int) { deltas.append((lease.channel, dx, dy, lease.owner, lease.generation)) }
    func report(_ message: String) {}
}
final class MockWindowEffects: RelativeMouseWindowEffects {
    final class Token: NSObject {
        let window: AnyObject
        let operation: () -> Void
        var active = true
        init(_ window: AnyObject, _ operation: @escaping () -> Void) { self.window = window; self.operation = operation }
    }
    var tokens: [Token] = []
    var removed = 0
    var allowed = true
    var closeDuringRegistration = false
    func observeClose(_ window: AnyObject, operation: @escaping () -> Void) -> AnyObject? {
        if !allowed { return nil }
        let token = Token(window, operation); tokens.append(token)
        if closeDuringRegistration { operation() }
        return token
    }
    func removeCloseObserver(_ token: AnyObject) {
        guard let token = token as? Token, token.active else { return }
        token.active = false; removed += 1
    }
    func close(_ window: AnyObject) {
        for token in tokens where token.active && token.window === window { token.operation() }
    }
}

var failures = 0
var count = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String = "assertion") {
    if !condition() { failures += 1; print("FAIL: \(message)") }
}
func test(_ name: String, _ operation: () -> Void) {
    count += 1
    let before = failures
    operation()
    print("\(before == failures ? "PASS" : "FAIL"): \(name)")
}
func lease(_ channel: AnyObject, _ owner: String = "session-A", _ generation: Int = 1) -> RelativeMouseLease {
    return RelativeMouseLease(channel: channel, owner: owner, generation: generation)
}

test("valid strict method arguments bind channel owner and generation") {
    let a = NSObject(), b = NSObject()
    let value = RelativeMouseLease.decode(["owner": "session-A", "generation": 1], channel: a)
    check(value?.matches(lease(a)) == true)
    check(value?.matches(lease(b)) == false)
    check(value?.matches(lease(a, "session-B")) == false)
    check(value?.matches(lease(a, "session-A", 2)) == false)
}
test("malformed arguments never become an unowned request") {
    let a = NSObject()
    for raw: Any? in [nil, [:], ["owner": "A"], ["generation": 1], ["owner": "", "generation": 1],
        ["owner": "A", "generation": true], ["owner": "A", "generation": 1.0],
        ["owner": "A", "generation": "1"], ["owner": "A", "generation": 0],
        ["owner": "A", "generation": -1], ["owner": "A", "generation": 9_007_199_254_740_992],
        ["owner": "A", "generation": 1, "force": true], ["owner": "A\n", "generation": 1],
        ["owner": String(repeating: "a", count: 129), "generation": 1]] {
        check(RelativeMouseLease.decode(raw, channel: a) == nil)
    }
}
test("identical active enable is idempotent without replacing monitor") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a))); check(state.enable(lease(a)))
    check(f.associations == [false]); check(f.monitors.count == 1)
}
test("different channel cannot steal active delta ownership") {
    let f = MockEffects(), a = NSObject(), b = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a))); check(!state.enable(lease(b)))
    f.monitors[0](2, 3)
    check(f.deltas.count == 1 && f.deltas[0].0 === a)
    check(f.associations == [false])
}
test("different owner and newer generation cannot steal active lease") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a)))
    check(!state.enable(lease(a, "session-B"))); check(!state.enable(lease(a, "session-A", 2)))
    check(state.enabled && f.removed == 0)
}
test("foreign disable has zero effects") {
    let f = MockEffects(), a = NSObject(), b = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a))); check(!state.disable(lease(b)))
    check(!state.disable(lease(a, "other"))); check(!state.disable(lease(a, "session-A", 2)))
    check(state.enabled && f.removed == 0 && f.associations == [false])
}
test("matching release and duplicate release are bounded and idempotent") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a))); check(state.disable(lease(a))); check(state.disable(lease(a)))
    check(!state.enabled && f.removed == 1 && f.associations == [false, true])
}
test("old release cannot unlock newer generation") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a))); check(state.disable(lease(a))); check(state.enable(lease(a, "session-A", 2)))
    check(!state.disable(lease(a))); check(state.enabled && f.removed == 1)
    check(f.associations == [false, true, false])
}
test("old retry cannot reassociate after a newer enabled owner") {
    let f = MockEffects(), a = NSObject(), b = NSObject(); let state = RelativeMouseState(effects: f)
    f.associationResults = [true, false, true]
    check(state.enable(lease(a))); check(!state.disable(lease(a))); check(f.retries.count == 1)
    check(!state.enable(lease(b)))
    check(state.disable(lease(a)))
    check(state.enable(lease(b))); let count = f.associations.count
    f.retries[0](); check(f.associations.count == count && state.enabled)
}
test("owned retry restores only its still-disabled epoch") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    f.associationResults = [true, false, true]
    check(state.enable(lease(a))); check(!state.disable(lease(a))); check(state.associationPending)
    f.retries[0](); check(!state.associationPending && !state.enabled)
    check(f.associations == [false, true, true])
}
test("failed immediate restore permits exact release retry") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    f.associationResults = [true, false, true]
    check(state.enable(lease(a))); check(!state.disable(lease(a))); check(state.disable(lease(a)))
    let count = f.associations.count; f.retries[0](); check(f.associations.count == count)
}
test("failed dissociation creates no monitor or active lease") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    f.associationResults = [false]
    check(!state.enable(lease(a))); check(!state.enabled && f.monitors.isEmpty)
    check(state.disable(lease(a)) && f.associations == [false])
}
test("monitor failure rolls back cursor and refuses enable") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    f.monitorAllowed = false
    check(!state.enable(lease(a))); check(!state.enabled && state.activeLease == nil)
    check(f.associations == [false, true])
}
test("failed monitor rollback retry cannot affect newer lock") {
    let f = MockEffects(), a = NSObject(), b = NSObject(); let state = RelativeMouseState(effects: f)
    f.monitorAllowed = false; f.associationResults = [true, false, true, true]
    check(!state.enable(lease(a))); f.monitorAllowed = true
    check(!state.enable(lease(b))); check(state.disable(lease(a)))
    check(state.enable(lease(b))); let count = f.associations.count
    f.retries[0](); check(f.associations.count == count && state.enabled)
}
test("removed monitor callback cannot leak into a newer channel") {
    let f = MockEffects(), a = NSObject(), b = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a))); let stale = f.monitors[0]
    check(state.disable(lease(a))); check(state.enable(lease(b)))
    stale(5, 6); check(f.deltas.isEmpty)
    f.monitors[1](7, 8); check(f.deltas.count == 1 && f.deltas[0].0 === b)
}
test("fractional movement accumulates and resets per lease") {
    let f = MockEffects(), a = NSObject(), b = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a))); f.monitors[0](0.6, -0.7); check(f.deltas.isEmpty)
    f.monitors[0](0.7, -0.6); check(f.deltas[0].1 == 1 && f.deltas[0].2 == -1)
    check(state.disable(lease(a))); check(state.enable(lease(b))); f.monitors[1](0.8, 0.8)
    check(f.deltas.count == 1)
}
test("force cleanup is explicit and old owned cleanup cannot follow newer enable") {
    let f = MockEffects(), a = NSObject(), b = NSObject(); let state = RelativeMouseState(effects: f)
    f.associationResults = [true, false, true]
    check(state.enable(lease(a))); state.finishApplication(); check(!state.enabled)
    check(!state.enable(lease(b))); check(state.disable(lease(a)))
    check(state.enable(lease(b))); let count = f.associations.count
    f.retries[0](); check(f.associations.count == count && state.enabled)
}
test("failed monitor and both restorations retain explicit cleanupRequired ownership") {
    let f = MockEffects(), a = NSObject(), b = NSObject(); let state = RelativeMouseState(effects: f)
    f.monitorAllowed = false; f.associationResults = [true, false, false, true]
    let reply = state.enableResponse(lease(a))
    check(reply["status"] as? String == "cleanupRequired")
    check(reply["owner"] as? String == "session-A" && reply["generation"] as? Int == 1)
    f.retries[0](); check(state.associationPending)
    check(state.enableResponse(lease(b))["status"] as? String == "refused")
    check(!state.disable(lease(b)))
    check(state.disable(lease(a))); check(!state.associationPending)
    f.monitorAllowed = true; check(state.enable(lease(b)))
}
test("monitor failure with successful restoration reports inactive not enabled") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    f.monitorAllowed = false
    check(state.enableResponse(lease(a))["status"] as? String == "inactive")
    check(!state.enabled && !state.associationPending && state.disable(lease(a)))
}
test("empty exact disable resolves a transport failure before acquisition") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.disable(lease(a)))
    check(f.associations.isEmpty && f.removed == 0)
    check(state.enableResponse(lease(a))["status"] as? String == "enabled")
}
test("release failure then scheduled success can be acknowledged before next enable") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    f.associationResults = [true, false, true, true]
    check(state.enable(lease(a))); check(!state.disable(lease(a)))
    check(!state.enable(lease(a, "session-A", 2)))
    f.retries[0](); let count = f.associations.count
    check(state.disable(lease(a)) && f.associations.count == count)
    check(state.enable(lease(a, "session-A", 2)))
}
test("delta transport carries immutable owner and generation for queued callback filtering") {
    let f = MockEffects(), a = NSObject(); let state = RelativeMouseState(effects: f)
    check(state.enable(lease(a, "session-A", 7))); f.monitors[0](1, 2)
    check(state.disable(lease(a, "session-A", 7)))
    check(state.enable(lease(a, "session-A", 8))); f.monitors[1](3, 4)
    check(f.deltas[0].3 == "session-A" && f.deltas[0].4 == 7)
    check(f.deltas[1].3 == "session-A" && f.deltas[1].4 == 8)
}
test("unattached window is refused before any native acquisition") {
    let f = MockEffects(), w = MockWindowEffects(), a = NSObject()
    let state = RelativeMouseState(effects: f)
    let lifetime = RelativeMouseWindowLifetimes(state: state, effects: w)
    check(!lifetime.admit(channel: a, window: nil)); check(f.associations.isEmpty && !state.enabled)
}
test("definitive owning window close releases exactly its channel and removes observer") {
    let f = MockEffects(), w = MockWindowEffects(), a = NSObject(), window = NSObject()
    let state = RelativeMouseState(effects: f); let lifetime = RelativeMouseWindowLifetimes(state: state, effects: w)
    check(lifetime.admit(channel: a, window: window)); check(state.enable(lease(a)))
    w.close(window)
    check(!state.enabled && f.removed == 1 && f.associations == [false, true])
    check(w.removed == 1 && !lifetime.admit(channel: a, window: window))
    let count = f.associations.count; w.close(window); check(f.associations.count == count)
}
test("foreign window close cannot release active owner") {
    let f = MockEffects(), w = MockWindowEffects(), a = NSObject(), b = NSObject(), wa = NSObject(), wb = NSObject()
    let state = RelativeMouseState(effects: f); let lifetime = RelativeMouseWindowLifetimes(state: state, effects: w)
    check(lifetime.admit(channel: a, window: wa)); check(lifetime.admit(channel: b, window: wb))
    check(state.enable(lease(a))); w.close(wb)
    check(state.enabled && f.removed == 0 && f.associations == [false])
    w.close(wa); check(!state.enabled)
}
test("old close after new channel ownership has zero cursor effects") {
    let f = MockEffects(), w = MockWindowEffects(), a = NSObject(), b = NSObject(), wa = NSObject(), wb = NSObject()
    let state = RelativeMouseState(effects: f); let lifetime = RelativeMouseWindowLifetimes(state: state, effects: w)
    check(lifetime.admit(channel: a, window: wa)); check(state.enable(lease(a))); check(state.disable(lease(a)))
    check(lifetime.admit(channel: b, window: wb)); check(state.enable(lease(b)))
    let count = f.associations.count; w.close(wa)
    check(f.associations.count == count && state.enabled && f.removed == 1)
    check(!lifetime.admit(channel: a, window: wa))
}
test("closed cleanupRequired owner can be reconciled before a new explicit enable") {
    let f = MockEffects(), w = MockWindowEffects(), a = NSObject(), b = NSObject(), wa = NSObject(), wb = NSObject()
    let state = RelativeMouseState(effects: f); let lifetime = RelativeMouseWindowLifetimes(state: state, effects: w)
    check(lifetime.admit(channel: a, window: wa))
    f.monitorAllowed = false; f.associationResults = [true, false, false, true, true]
    check(state.enableResponse(lease(a))["status"] as? String == "cleanupRequired")
    w.close(wa); check(state.associationPending && !lifetime.admit(channel: a, window: wa))
    f.monitorAllowed = true; check(lifetime.admit(channel: b, window: wb))
    check(state.enableResponse(lease(b))["status"] as? String == "enabled")
    let count = f.associations.count; for retry in f.retries { retry() }
    check(state.enabled && f.associations.count == count)
}
test("failed closed-owner reconciliation refuses new enable without stealing reservation") {
    let f = MockEffects(), w = MockWindowEffects(), a = NSObject(), b = NSObject(), wa = NSObject()
    let state = RelativeMouseState(effects: f); let lifetime = RelativeMouseWindowLifetimes(state: state, effects: w)
    check(lifetime.admit(channel: a, window: wa)); f.associationResults = [true, false, false, false]
    check(state.enable(lease(a))); check(!state.disable(lease(a))); w.close(wa)
    check(state.enableResponse(lease(b))["status"] as? String == "refused")
    check(state.associationPending && !state.enabled)
    check(state.enableResponse(lease(b))["status"] as? String == "enabled")
}
test("observer refusal and registration-time close cannot admit a lease") {
    let f = MockEffects(), w = MockWindowEffects(), a = NSObject(), wa = NSObject()
    let state = RelativeMouseState(effects: f); let lifetime = RelativeMouseWindowLifetimes(state: state, effects: w)
    w.allowed = false; check(!lifetime.admit(channel: a, window: wa))
    w.allowed = true; w.closeDuringRegistration = true
    check(!lifetime.admit(channel: a, window: wa))
    check(f.associations.isEmpty && w.removed == 1)
}
test("channel cannot rebind its lease to a different window") {
    let f = MockEffects(), w = MockWindowEffects(), a = NSObject(), wa = NSObject(), wb = NSObject()
    let state = RelativeMouseState(effects: f); let lifetime = RelativeMouseWindowLifetimes(state: state, effects: w)
    check(lifetime.admit(channel: a, window: wa)); check(!lifetime.admit(channel: a, window: wb))
    check(state.enable(lease(a))); w.close(wb); check(state.enabled)
}
print("Ran \(count) native production-state tests; \(failures) failures; all effects mocked")
exit(failures == 0 ? 0 : 1)
