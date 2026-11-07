#ifndef MIXEL_CONSENT_DISPLAY_H_
#define MIXEL_CONSENT_DISPLAY_H_

#include <windows.h>

#include <cstring>

namespace mixel {
namespace consent_display {

// Session display status belongs to the interactive recipient. A service in
// session 0 does not receive it, and this must not keep the system awake.
inline constexpr GUID kSessionDisplayStatus = {
    0x2b84c20e, 0xad23, 0x4ddf, {0x93, 0xdb, 0x05, 0xff, 0xbd, 0x7e, 0xfc, 0xa5}};
inline constexpr wchar_t kPowerWindow[] = L"MixelConsentDisplayState";
inline constexpr DWORD kWakeBudgetMs = 1250;

inline LRESULT CALLBACK PowerWindowProc(HWND window, UINT message,
                                       WPARAM wparam, LPARAM lparam) {
  if (message == WM_NCCREATE) {
    const auto* create = reinterpret_cast<const CREATESTRUCTW*>(lparam);
    SetWindowLongPtrW(window, GWLP_USERDATA,
                      reinterpret_cast<LONG_PTR>(create->lpCreateParams));
  } else if (message == WM_POWERBROADCAST &&
             wparam == PBT_POWERSETTINGCHANGE && lparam) {
    auto* state = reinterpret_cast<bool*>(
        GetWindowLongPtrW(window, GWLP_USERDATA));
    const auto* setting =
        reinterpret_cast<const POWERBROADCAST_SETTING*>(lparam);
    if (state && IsEqualGUID(setting->PowerSetting, kSessionDisplayStatus) &&
        setting->DataLength == sizeof(DWORD)) {
      DWORD display = 0;
      std::memcpy(&display, setting->Data, sizeof(display));
      *state = display == 1 || display == 2;  // On or dimmed, not off.
    }
    return TRUE;
  }
  return DefWindowProcW(window, message, wparam, lparam);
}

inline void Prepare() {
  const HINSTANCE instance = GetModuleHandleW(nullptr);
  const WNDCLASSW klass{0, PowerWindowProc, 0, 0, instance, nullptr, nullptr,
                       nullptr, nullptr, kPowerWindow};
  const ATOM atom = RegisterClassW(&klass);
  bool display_ready = false;
  const HWND window = atom ? CreateWindowExW(
      0, kPowerWindow, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, instance,
      &display_ready) : nullptr;
  const HPOWERNOTIFY notification = window ? RegisterPowerSettingNotification(
      window, &kSessionDisplayStatus, DEVICE_NOTIFY_WINDOW_HANDLE) : nullptr;

  // A single display-only request resets the idle timer. It doesn't alter the
  // power plan, simulate input, unlock a desktop or keep the screen awake.
  const bool wake_requested = SetThreadExecutionState(ES_DISPLAY_REQUIRED) != 0;
  if (notification && wake_requested) {
    const ULONGLONG deadline = GetTickCount64() + kWakeBudgetMs;
    while (!display_ready && GetTickCount64() < deadline) {
      MSG message{};
      if (PeekMessageW(&message, window, WM_POWERBROADCAST, WM_POWERBROADCAST,
                       PM_REMOVE)) {
        if (message.message == WM_QUIT) {
          PostQuitMessage(static_cast<int>(message.wParam));
          break;
        }
        DispatchMessageW(&message);
        continue;
      }
      const ULONGLONG now = GetTickCount64();
      if (display_ready || now >= deadline) break;
      const DWORD outcome = MsgWaitForMultipleObjectsEx(
          0, nullptr, static_cast<DWORD>(deadline - now),
          QS_POSTMESSAGE | QS_SENDMESSAGE, 0);
      if (outcome == WAIT_FAILED || outcome == WAIT_TIMEOUT) break;
    }
  }

  if (notification) UnregisterPowerSettingNotification(notification);
  if (window) DestroyWindow(window);
  if (atom) UnregisterClassW(kPowerWindow, instance);
}

}  // namespace consent_display
}  // namespace mixel

#endif  // MIXEL_CONSENT_DISPLAY_H_
