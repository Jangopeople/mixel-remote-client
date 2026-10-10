import Cocoa
import FlutterMacOS


let kMessagesChannel = "uni_links/messages"
let kEventsChannel = "uni_links/events"

public class UniLinksDesktopPlugin: NSObject, FlutterPlugin, FlutterStreamHandler, FlutterAppLifecycleDelegate {
    private static var _instance: UniLinksDesktopPlugin?
    
    private var _eventSink: FlutterEventSink?;
    private var _initialUrl: String?
    // Only the latest validated support intent waits for a temporarily absent
    // primary Dart listener. Ordinary URI behavior remains unchanged.
    private var _pendingSupportUrl: String?
    private var _initialSupportHandled = false

    private static func isSupportInvite(_ raw: String) -> Bool {
        // A printable 4096-byte API key may be percent-encoded to 12288 bytes.
        // Bound the retained URI before parsing it; never log bearer contents.
        guard raw.utf8.count <= 16384,
              let uri = URLComponents(string: raw),
              uri.scheme?.lowercased() == "mixel-remote",
              uri.host?.lowercased() == "support",
              uri.user == nil, uri.password == nil, uri.port == nil,
              uri.fragment == nil, uri.path.isEmpty || uri.path == "/"
        else { return false }
        var params = [String: String]()
        var queryOrder = [String]()
        for pair in (uri.percentEncodedQuery ?? "").components(separatedBy: "&") {
            let split = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            if split[0].isEmpty { continue }
            // Dart queryParameters form-decodes raw '+', keeps exact-key
            // insertion order and replaces its value on later duplicates.
            guard let name = String(split[0]).replacingOccurrences(of: "+", with: " ").removingPercentEncoding,
                  let value = (split.count == 2 ? String(split[1]) : "").replacingOccurrences(of: "+", with: " ").removingPercentEncoding
            else { return false }
            if params[name] == nil { queryOrder.append(name) }
            params[name] = value
        }
        var normalized = [String: String]()
        for name in queryOrder {
            normalized[name.lowercased()] = params[name]
        }
        guard let token = normalized["invite"], let apiKey = normalized["apikey"],
              token.range(of: "^inv_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                options: [.regularExpression, .caseInsensitive]) == token.startIndex..<token.endIndex,
              (20...4096).contains(apiKey.utf8.count),
              apiKey.unicodeScalars.allSatisfy({ (33...126).contains($0.value) })
        else { return false }
        return true
    }
    
    public static var instance: UniLinksDesktopPlugin {
        get {
            return _instance!
        }
    }
    
    public static func register(with registrar: FlutterPluginRegistrar) {
        let instance = UniLinksDesktopPlugin()
        
        let channel = FlutterMethodChannel(name: kMessagesChannel, binaryMessenger: registrar.messenger)
        registrar.addMethodCallDelegate(instance, channel: channel)
        
        let chargingChannel = FlutterEventChannel(name: kEventsChannel, binaryMessenger: registrar.messenger);
        chargingChannel.setStreamHandler(instance);
        
        registrar.addApplicationDelegate(instance);
        
        _instance = instance
    }
    
    public func handleOpen(_ urls: [URL]) -> Bool {
        for url in urls {
            let urlString = url.absoluteString;
            if (_initialUrl == nil) {
                _initialUrl = urlString
            }
            if (_eventSink != nil) {
                if Self.isSupportInvite(urlString), let initial = _initialUrl,
                   Self.isSupportInvite(initial) { _initialSupportHandled = true }
                _eventSink!(urlString)
            } else if Self.isSupportInvite(urlString) {
                _pendingSupportUrl = urlString
            }
        }
        
        // mark all urls as consumed
        return true;
    }
    
    public func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
        switch call.method {
        case "getInitialLink":
            if let initial = _initialUrl, Self.isSupportInvite(initial) {
                if _initialSupportHandled { result(""); break }
                // Getter and listener can attach in either order. Deliver the
                // latest pending support intent once, never a stale cold URL.
                let current = _pendingSupportUrl ?? initial
                _pendingSupportUrl = nil
                _initialSupportHandled = true
                result(current)
                break
            }
            result(self._initialUrl ?? "");
            break;
        default:
            result(FlutterMethodNotImplemented)
        }
    }
    
    public func onListen(withArguments arguments: Any?, eventSink events: @escaping FlutterEventSink) -> FlutterError? {
        self._eventSink = events;
        if let pending = _pendingSupportUrl {
            _pendingSupportUrl = nil
            if let initial = _initialUrl, Self.isSupportInvite(initial) {
                _initialSupportHandled = true
            }
            events(pending)
        }
        return nil;
    }
    
    public func onCancel(withArguments arguments: Any?) -> FlutterError? {
        self._eventSink = nil;
        return nil;
    }
}
