//! Read the running app's real bundle identity, never a webview or CLI value.
pub fn identifier() -> Option<String> {
    objc2_foundation::NSBundle::mainBundle()
        .bundleIdentifier()
        .map(|value| value.to_string())
}
