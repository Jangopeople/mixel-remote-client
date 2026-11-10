#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hidsystem/IOHIDUserDevice.h>
#include <Security/SecTask.h>
#include <dispatch/dispatch.h>
#include <mach/mach_time.h>

// Keyboard page, eight modifier bits, then a bitmap of usages 0x04..0xdf.
static const uint8_t descriptor[] = {
    0x05,0x01,0x09,0x06,0xa1,0x01,0x05,0x07,0x19,0xe0,0x29,0xe7,
    0x15,0x00,0x25,0x01,0x75,0x01,0x95,0x08,0x81,0x02,
    0x19,0x04,0x29,0xdf,0x95,0xdc,0x81,0x02,0x95,0x04,0x81,0x01,0xc0,
};

bool mixel_login_keyboard_entitled(void) {
    SecTaskRef task = SecTaskCreateFromSelf(NULL);
    if (!task) return false;
    CFTypeRef value = SecTaskCopyValueForEntitlement(
        task, CFSTR("com.apple.developer.hid.virtual.device"), NULL
    );
    bool allowed = value && CFEqual(value, kCFBooleanTrue);
    if (value) CFRelease(value);
    CFRelease(task);
    return allowed;
}

IOHIDUserDeviceRef mixel_login_keyboard_open(void) {
    CFDataRef report = CFDataCreate(NULL, descriptor, sizeof(descriptor));
    if (!report) return NULL;
    const void *keys[] = { CFSTR("ReportDescriptor"), CFSTR("Product"), CFSTR("Transport") };
    const void *values[] = { report, CFSTR("Mixel Remote Keyboard"), CFSTR("Virtual") };
    CFDictionaryRef config = CFDictionaryCreate(NULL, keys, values, 3,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFRelease(report);
    if (!config) return NULL;
    IOHIDUserDeviceRef device = IOHIDUserDeviceCreateWithProperties(NULL, config, 0);
    CFRelease(config);
    if (!device) return NULL;
    dispatch_queue_t queue = dispatch_queue_create("ch.mixel.remote.login-keyboard", DISPATCH_QUEUE_SERIAL);
    IOHIDUserDeviceSetDispatchQueue(device, queue);
    IOHIDUserDeviceActivate(device);
    dispatch_release(queue);
    return device;
}

int32_t mixel_login_keyboard_report(IOHIDUserDeviceRef device, const uint8_t report[29]) {
    return IOHIDUserDeviceHandleReportWithTimeStamp(device, mach_absolute_time(), report, 29);
}

void mixel_login_keyboard_close(IOHIDUserDeviceRef device) {
    IOHIDUserDeviceCancel(device);
    CFRelease(device);
}
