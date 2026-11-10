#include <Security/Security.h>
#include <string.h>

static CFMutableDictionaryRef controller_query(const char *account) {
    CFMutableDictionaryRef query = CFDictionaryCreateMutable(
        kCFAllocatorDefault, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    if (query) {
        CFStringRef account_name = CFStringCreateWithCString(kCFAllocatorDefault, account, kCFStringEncodingUTF8);
        if (!account_name) {
            CFRelease(query);
            return NULL;
        }
        CFDictionarySetValue(query, kSecClass, kSecClassGenericPassword);
        CFDictionarySetValue(query, kSecAttrService, CFSTR("ch.mixel.remote.controller.device-identity.v1"));
        CFDictionarySetValue(query, kSecAttrAccount, account_name);
        CFRelease(account_name);
        CFDictionarySetValue(query, kSecUseDataProtectionKeychain, kCFBooleanTrue);
        CFDictionarySetValue(query, kSecAttrSynchronizable, kCFBooleanFalse);
    }
    return query;
}

OSStatus mixel_controller_keychain_read(const char *account, unsigned char *output, CFIndex size) {
    CFMutableDictionaryRef query = controller_query(account);
    if (!query) return errSecAllocate;
    CFDictionarySetValue(query, kSecReturnData, kCFBooleanTrue);
    CFTypeRef result = NULL;
    OSStatus status = SecItemCopyMatching(query, &result);
    if (status == errSecSuccess) {
        if (!result || CFGetTypeID(result) != CFDataGetTypeID() || CFDataGetLength(result) != size) {
            status = errSecDecode;
        } else {
            memcpy(output, CFDataGetBytePtr(result), (size_t)size);
        }
    }
    if (result) CFRelease(result);
    CFRelease(query);
    return status;
}

OSStatus mixel_controller_keychain_create(const char *account, const unsigned char *record, CFIndex size) {
    CFMutableDictionaryRef query = controller_query(account);
    if (!query) return errSecAllocate;
    CFDataRef data = CFDataCreateWithBytesNoCopy(kCFAllocatorDefault, record, size, kCFAllocatorNull);
    if (!data) {
        CFRelease(query);
        return errSecAllocate;
    }
    CFDictionarySetValue(query, kSecValueData, data);
    CFDictionarySetValue(query, kSecAttrAccessible, kSecAttrAccessibleWhenUnlockedThisDeviceOnly);
    OSStatus status = SecItemAdd(query, NULL);
    CFRelease(data);
    CFRelease(query);
    return status;
}
