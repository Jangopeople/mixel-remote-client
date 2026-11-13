// Protections a technician can switch on for the Mac they help, and the Mac's sound:
// - privacy screen: dark borderless windows over every display. They are left out of the
//   technician's picture (the capture filter excludes them, see macos.rs) and let clicks through.
// - block input: an event tap that drops keyboard and mouse events which did not come from
//   Mixel Remote. Needs the Accessibility permission. Control+Option+Command+Delete gives the
//   person at the Mac their input back.
// - pointer: a small ring the person at the Mac sees where the technician points.
// - sound: ScreenCaptureKit records what the Mac plays (macOS 13 or later).
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <CoreMedia/CoreMedia.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>

// Marks events Mixel Remote posts for the technician (kCGEventSourceUserData).
const int64_t MIXEL_INPUT_MARK = 0x4D58524D54;

static void on_main(dispatch_block_t block) {
    if ([NSThread isMainThread]) {
        block();
        return;
    }
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    dispatch_async(dispatch_get_main_queue(), ^{
        block();
        dispatch_semaphore_signal(done);
    });
    dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
}

// ---------------------------------------------------------------- privacy screen
static NSMutableArray<NSWindow *> *privacy_windows;

static int32_t privacy_ids(uint32_t *ids, int32_t capacity) {
    int32_t count = 0;
    for (NSWindow *window in privacy_windows) {
        if (count < capacity) ids[count] = (uint32_t)window.windowNumber;
        count++;
    }
    return count;
}

// Opens the cover windows, still invisible; returns their window numbers (or -1).
int32_t mixel_privacy_create(const char *text, uint32_t *ids, int32_t capacity) {
    __block int32_t count = -1;
    NSString *message = text ? [NSString stringWithUTF8String:text] : @"";
    on_main(^{
        [NSApplication sharedApplication];
        if (privacy_windows) {
            count = privacy_ids(ids, capacity);
            return;
        }
        privacy_windows = [NSMutableArray array];
        for (NSScreen *screen in [NSScreen screens]) {
            NSWindow *window = [[NSWindow alloc] initWithContentRect:screen.frame
                                                           styleMask:NSWindowStyleMaskBorderless
                                                             backing:NSBackingStoreBuffered
                                                               defer:NO];
            window.releasedWhenClosed = NO;
            window.level = NSScreenSaverWindowLevel;
            window.backgroundColor = [NSColor colorWithCalibratedRed:0.078 green:0.078 blue:0.078 alpha:1.0];
            window.opaque = YES;
            window.hasShadow = NO;
            window.ignoresMouseEvents = YES;
            window.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                                        NSWindowCollectionBehaviorStationary |
                                        NSWindowCollectionBehaviorFullScreenAuxiliary |
                                        NSWindowCollectionBehaviorIgnoresCycle;
            window.sharingType = NSWindowSharingNone;
            window.alphaValue = 0.0;
            NSTextField *label = [NSTextField labelWithString:message];
            label.textColor = [NSColor colorWithCalibratedWhite:0.94 alpha:1.0];
            label.font = [NSFont systemFontOfSize:28 weight:NSFontWeightSemibold];
            label.alignment = NSTextAlignmentCenter;
            label.translatesAutoresizingMaskIntoConstraints = NO;
            [window.contentView addSubview:label];
            [NSLayoutConstraint activateConstraints:@[
                [label.centerXAnchor constraintEqualToAnchor:window.contentView.centerXAnchor],
                [label.centerYAnchor constraintEqualToAnchor:window.contentView.centerYAnchor],
            ]];
            [window setFrame:screen.frame display:NO];
            [window orderFrontRegardless];
            [privacy_windows addObject:window];
        }
        count = privacy_ids(ids, capacity);
    });
    return count;
}

// Shows the cover (after the capture filter left it out).
void mixel_privacy_reveal(void) {
    on_main(^{
        for (NSWindow *window in privacy_windows) {
            window.alphaValue = 1.0;
            [window orderFrontRegardless];
        }
    });
}

void mixel_privacy_close(void) {
    on_main(^{
        for (NSWindow *window in privacy_windows) {
            [window orderOut:nil];
            [window close];
        }
        privacy_windows = nil;
    });
}

// ---------------------------------------------------------------- pointer ring
@interface MixelRingView : NSView
@end
@implementation MixelRingView
- (void)drawRect:(NSRect)rect {
    [[NSColor clearColor] set];
    NSRectFill(rect);
    [[NSColor colorWithCalibratedRed:0xBA / 255.0 green:0x20 / 255.0 blue:0x34 / 255.0 alpha:0.92] setFill];
    [[NSBezierPath bezierPathWithOvalInRect:NSInsetRect(self.bounds, 1, 1)] fill];
    [[NSColor whiteColor] setFill];
    [[NSBezierPath bezierPathWithOvalInRect:NSInsetRect(self.bounds, 12, 12)] fill];
}
@end

static NSWindow *ring;
static const CGFloat RING = 34;

// Moves the ring to (x, y) in global display coordinates (origin top left of the main display).
void mixel_pointer_move(double x, double y) {
    dispatch_async(dispatch_get_main_queue(), ^{
        [NSApplication sharedApplication];
        if (!ring) {
            ring = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, RING, RING)
                                               styleMask:NSWindowStyleMaskBorderless
                                                 backing:NSBackingStoreBuffered
                                                   defer:NO];
            ring.releasedWhenClosed = NO;
            ring.opaque = NO;
            ring.backgroundColor = [NSColor clearColor];
            ring.hasShadow = NO;
            ring.ignoresMouseEvents = YES;
            ring.level = NSScreenSaverWindowLevel + 1;
            ring.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                                      NSWindowCollectionBehaviorStationary |
                                      NSWindowCollectionBehaviorFullScreenAuxiliary |
                                      NSWindowCollectionBehaviorIgnoresCycle;
            ring.contentView = [[MixelRingView alloc] initWithFrame:NSMakeRect(0, 0, RING, RING)];
        }
        NSScreen *primary = [NSScreen screens].firstObject;
        CGFloat height = primary ? primary.frame.size.height : 0;
        [ring setFrameOrigin:NSMakePoint(x - RING / 2, height - y - RING / 2)];
        [ring orderFrontRegardless];
    });
}

void mixel_pointer_hide(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        [ring orderOut:nil];
    });
}

// ---------------------------------------------------------------- drawings
// The technician's strokes over the shared display. Shapes arrive as doubles: kind (0 line,
// 1 filled polygon, 2 dot), red, green, blue, alpha (0..1), width, point count, then x, y pairs
// in points from the top left of the display.
@interface MixelDrawView : NSView
@property(nonatomic, copy) NSData *shapes;
@end
@implementation MixelDrawView
- (BOOL)isFlipped {
    return YES;
}
- (void)drawRect:(NSRect)rect {
    [[NSColor clearColor] set];
    NSRectFill(rect);
    const double *value = self.shapes.bytes;
    size_t total = self.shapes.length / sizeof(double), at = 0;
    while (value && at + 7 <= total) {
        int kind = (int)value[at];
        NSColor *colour = [NSColor colorWithSRGBRed:value[at + 1] green:value[at + 2] blue:value[at + 3] alpha:value[at + 4]];
        double width = value[at + 5];
        size_t count = (size_t)value[at + 6];
        at += 7;
        if (count == 0 || count > (total - at) / 2) break;
        const double *points = value + at;
        at += count * 2;
        if (kind == 2) {
            [colour setFill];
            [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(points[0] - width / 2, points[1] - width / 2, width, width)] fill];
            continue;
        }
        NSBezierPath *path = [NSBezierPath bezierPath];
        [path moveToPoint:NSMakePoint(points[0], points[1])];
        for (size_t index = 1; index < count; index++) [path lineToPoint:NSMakePoint(points[index * 2], points[index * 2 + 1])];
        if (kind == 1) {
            [path closePath];
            [colour setFill];
            [path fill];
        } else {
            path.lineWidth = width;
            path.lineCapStyle = NSLineCapStyleRound;
            path.lineJoinStyle = NSLineJoinStyleRound;
            [colour setStroke];
            [path stroke];
        }
    }
}
@end

static NSWindow *drawing;

// Opens (or moves) the overlay over a display rectangle in global coordinates (origin top left
// of the main display), still invisible; returns its window number (or -1).
int32_t mixel_draw_open(double x, double y, double width, double height) {
    __block int32_t number = -1;
    on_main(^{
        [NSApplication sharedApplication];
        NSScreen *primary = [NSScreen screens].firstObject;
        CGFloat top = primary ? primary.frame.size.height : 0;
        NSRect frame = NSMakeRect(x, top - y - height, width, height);
        if (!drawing) {
            drawing = [[NSWindow alloc] initWithContentRect:frame
                                                  styleMask:NSWindowStyleMaskBorderless
                                                    backing:NSBackingStoreBuffered
                                                      defer:NO];
            drawing.releasedWhenClosed = NO;
            drawing.opaque = NO;
            drawing.backgroundColor = [NSColor clearColor];
            drawing.hasShadow = NO;
            drawing.ignoresMouseEvents = YES;
            drawing.level = NSScreenSaverWindowLevel + 1;
            drawing.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                                         NSWindowCollectionBehaviorStationary |
                                         NSWindowCollectionBehaviorFullScreenAuxiliary |
                                         NSWindowCollectionBehaviorIgnoresCycle;
            drawing.sharingType = NSWindowSharingNone;
            // Shown (still empty and so invisible): ScreenCaptureKit lists, and can leave out,
            // only windows that are on screen.
            drawing.alphaValue = 1.0;
            drawing.contentView = [[MixelDrawView alloc] initWithFrame:NSMakeRect(0, 0, width, height)];
        }
        [drawing setFrame:frame display:NO];
        [drawing orderFrontRegardless];
        number = (int32_t)drawing.windowNumber;
    });
    return number;
}

// Shows these shapes at this opacity (kept above 0 so the window stays left out of the capture).
void mixel_draw_show(const double *shapes, size_t count, double alpha) {
    NSData *data = shapes && count ? [NSData dataWithBytes:shapes length:count * sizeof(double)] : [NSData data];
    dispatch_async(dispatch_get_main_queue(), ^{
        if (!drawing) return;
        MixelDrawView *view = (MixelDrawView *)drawing.contentView;
        view.shapes = data;
        view.needsDisplay = YES;
        drawing.alphaValue = alpha < 0.01 ? 0.01 : alpha;
        [drawing orderFrontRegardless];
    });
}

void mixel_draw_close(void) {
    on_main(^{
        [drawing orderOut:nil];
        [drawing close];
        drawing = nil;
    });
}

// ---------------------------------------------------------------- block input
static CFMachPortRef tap;
static CFRunLoopRef tap_loop;
static pthread_t tap_thread;
static pthread_mutex_t tap_lock = PTHREAD_MUTEX_INITIALIZER;
static volatile int tap_running;

static CGEventRef tap_event(CGEventTapProxy proxy, CGEventType type, CGEventRef event, void *context) {
    (void)proxy;
    (void)context;
    if (type == kCGEventTapDisabledByTimeout || type == kCGEventTapDisabledByUserInput) {
        if (tap) CGEventTapEnable(tap, true);
        return event;
    }
    if (CGEventGetIntegerValueField(event, kCGEventSourceUserData) == MIXEL_INPUT_MARK ||
        CGEventGetIntegerValueField(event, kCGEventSourceUnixProcessID) == getpid()) {
        return event;
    }
    if (type == kCGEventKeyDown && CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode) == 51) {
        CGEventFlags flags = CGEventGetFlags(event);
        if ((flags & kCGEventFlagMaskControl) && (flags & kCGEventFlagMaskAlternate) && (flags & kCGEventFlagMaskCommand)) {
            // The person at the Mac takes their keyboard and mouse back.
            tap_running = 0;
            CFRunLoopStop(CFRunLoopGetCurrent());
            return NULL;
        }
    }
    return NULL;
}

static void *tap_main(void *argument) {
    dispatch_semaphore_t ready = (__bridge dispatch_semaphore_t)argument;
    CFRunLoopSourceRef source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0);
    tap_loop = (CFRunLoopRef)CFRetain(CFRunLoopGetCurrent());
    CFRunLoopAddSource(tap_loop, source, kCFRunLoopCommonModes);
    CGEventTapEnable(tap, true);
    dispatch_semaphore_signal(ready);
    CFRunLoopRun();
    CGEventTapEnable(tap, false);
    CFRunLoopRemoveSource(tap_loop, source, kCFRunLoopCommonModes);
    CFRelease(source);
    tap_running = 0;
    return NULL;
}

bool mixel_accessibility_trusted(void) {
    return AXIsProcessTrusted();
}

// 0: blocking, 1: the Accessibility permission is missing, 2: macOS refused the event tap.
int32_t mixel_block_start(void) {
    pthread_mutex_lock(&tap_lock);
    if (tap_running) {
        pthread_mutex_unlock(&tap_lock);
        return 0;
    }
    if (tap) {
        // A previous tap ended (the person pressed the escape keys): release it first.
        pthread_join(tap_thread, NULL);
        CFMachPortInvalidate(tap);
        CFRelease(tap);
        tap = NULL;
        if (tap_loop) CFRelease(tap_loop);
        tap_loop = NULL;
    }
    if (!AXIsProcessTrusted()) {
        pthread_mutex_unlock(&tap_lock);
        return 1;
    }
    CGEventMask mask = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp) | CGEventMaskBit(kCGEventFlagsChanged) |
                       CGEventMaskBit(kCGEventLeftMouseDown) | CGEventMaskBit(kCGEventLeftMouseUp) |
                       CGEventMaskBit(kCGEventRightMouseDown) | CGEventMaskBit(kCGEventRightMouseUp) |
                       CGEventMaskBit(kCGEventOtherMouseDown) | CGEventMaskBit(kCGEventOtherMouseUp) |
                       CGEventMaskBit(kCGEventMouseMoved) | CGEventMaskBit(kCGEventLeftMouseDragged) |
                       CGEventMaskBit(kCGEventRightMouseDragged) | CGEventMaskBit(kCGEventOtherMouseDragged) |
                       CGEventMaskBit(kCGEventScrollWheel);
    // At the hardware level the local pointer does not move either; the session level is the fallback.
    tap = CGEventTapCreate(kCGHIDEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault, mask, tap_event, NULL);
    if (!tap) tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault, mask, tap_event, NULL);
    if (!tap) {
        pthread_mutex_unlock(&tap_lock);
        return AXIsProcessTrusted() ? 2 : 1;
    }
    dispatch_semaphore_t ready = dispatch_semaphore_create(0);
    tap_running = 1;
    if (pthread_create(&tap_thread, NULL, tap_main, (__bridge void *)ready) != 0) {
        tap_running = 0;
        CFMachPortInvalidate(tap);
        CFRelease(tap);
        tap = NULL;
        pthread_mutex_unlock(&tap_lock);
        return 2;
    }
    dispatch_semaphore_wait(ready, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC));
    pthread_mutex_unlock(&tap_lock);
    return 0;
}

void mixel_block_stop(void) {
    pthread_mutex_lock(&tap_lock);
    if (tap) {
        if (tap_loop) CFRunLoopStop(tap_loop);
        pthread_join(tap_thread, NULL);
        CFMachPortInvalidate(tap);
        CFRelease(tap);
        tap = NULL;
        if (tap_loop) CFRelease(tap_loop);
        tap_loop = NULL;
    }
    tap_running = 0;
    pthread_mutex_unlock(&tap_lock);
}

bool mixel_block_active(void) {
    return tap_running != 0;
}

// ---------------------------------------------------------------- sound
typedef void (*mixel_audio_callback)(void *context, const float *stereo, size_t frames);

API_AVAILABLE(macos(13.0))
@interface MixelAudio : NSObject <SCStreamOutput, SCStreamDelegate>
@property(nonatomic, strong) SCStream *stream;
@property(nonatomic, strong) dispatch_queue_t queue;
@property(nonatomic) mixel_audio_callback callback;
@property(nonatomic) void *context;
@end

@implementation MixelAudio
- (void)stream:(SCStream *)stream didOutputSampleBuffer:(CMSampleBufferRef)sample ofType:(SCStreamOutputType)type {
    (void)stream;
    if (type != SCStreamOutputTypeAudio || !CMSampleBufferIsValid(sample) || !self.callback) return;
    CMFormatDescriptionRef format = CMSampleBufferGetFormatDescription(sample);
    const AudioStreamBasicDescription *description = format ? CMAudioFormatDescriptionGetStreamBasicDescription(format) : NULL;
    if (!description || description->mFormatID != kAudioFormatLinearPCM || !(description->mFormatFlags & kAudioFormatFlagIsFloat) ||
        description->mBitsPerChannel != 32 || description->mChannelsPerFrame == 0) {
        return;
    }
    size_t needed = 0;
    if (CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(sample, &needed, NULL, 0, NULL, NULL, 0, NULL) != noErr || needed == 0) return;
    AudioBufferList *list = malloc(needed);
    if (!list) return;
    CMBlockBufferRef block = NULL;
    OSStatus status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sample, NULL, list, needed, NULL, NULL, kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, &block);
    size_t frames = (size_t)CMSampleBufferGetNumSamples(sample);
    if (status == noErr && frames > 0 && frames < 48000 * 4) {
        UInt32 channels = description->mChannelsPerFrame;
        bool planar = (description->mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0;
        float *stereo = malloc(frames * 2 * sizeof(float));
        if (stereo) {
            bool complete = true;
            for (UInt32 channel = 0; channel < 2 && complete; channel++) {
                UInt32 from = channel < channels ? channel : channels - 1;
                if (planar) {
                    if (from >= list->mNumberBuffers || list->mBuffers[from].mDataByteSize < frames * sizeof(float)) complete = false;
                } else if (list->mNumberBuffers < 1 || list->mBuffers[0].mDataByteSize < frames * channels * sizeof(float)) {
                    complete = false;
                }
            }
            if (complete) {
                for (size_t index = 0; index < frames; index++) {
                    for (UInt32 channel = 0; channel < 2; channel++) {
                        UInt32 from = channel < channels ? channel : channels - 1;
                        stereo[index * 2 + channel] = planar ? ((const float *)list->mBuffers[from].mData)[index]
                                                             : ((const float *)list->mBuffers[0].mData)[index * channels + from];
                    }
                }
                self.callback(self.context, stereo, frames);
            }
            free(stereo);
        }
    }
    if (block) CFRelease(block);
    free(list);
}
- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error {
    (void)stream;
    (void)error;
    self.callback = NULL;
}
@end

// Starts recording what the Mac plays; NULL with *error = 1 (no screen-recording permission),
// 2 (no display or ScreenCaptureKit refused), 3 (macOS older than 13).
void *mixel_audio_start(mixel_audio_callback callback, void *context, int32_t *error) {
    *error = 0;
    if (@available(macOS 13.0, *)) {
        if (!CGPreflightScreenCaptureAccess()) {
            *error = 1;
            return NULL;
        }
        __block SCShareableContent *content = nil;
        dispatch_semaphore_t listed = dispatch_semaphore_create(0);
        [SCShareableContent getShareableContentExcludingDesktopWindows:NO
                                                   onScreenWindowsOnly:YES
                                                     completionHandler:^(SCShareableContent *value, NSError *problem) {
                                                         if (!problem) content = value;
                                                         dispatch_semaphore_signal(listed);
                                                     }];
        if (dispatch_semaphore_wait(listed, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)) != 0 || !content) {
            *error = 2;
            return NULL;
        }
        SCDisplay *display = nil;
        for (SCDisplay *candidate in content.displays) {
            if (candidate.displayID == CGMainDisplayID()) display = candidate;
        }
        if (!display) display = content.displays.firstObject;
        if (!display) {
            *error = 2;
            return NULL;
        }
        SCContentFilter *filter = [[SCContentFilter alloc] initWithDisplay:display excludingWindows:@[]];
        SCStreamConfiguration *configuration = [SCStreamConfiguration new];
        configuration.capturesAudio = YES;
        configuration.excludesCurrentProcessAudio = YES;
        configuration.sampleRate = 48000;
        configuration.channelCount = 2;
        // Sound only: the smallest picture, once a second.
        configuration.width = 2;
        configuration.height = 2;
        configuration.minimumFrameInterval = CMTimeMake(1, 1);
        MixelAudio *audio = [MixelAudio new];
        audio.callback = callback;
        audio.context = context;
        audio.queue = dispatch_queue_create("ch.mixel.remote.sound", DISPATCH_QUEUE_SERIAL);
        audio.stream = [[SCStream alloc] initWithFilter:filter configuration:configuration delegate:audio];
        NSError *problem = nil;
        if (![audio.stream addStreamOutput:audio type:SCStreamOutputTypeAudio sampleHandlerQueue:audio.queue error:&problem] ||
            ![audio.stream addStreamOutput:audio type:SCStreamOutputTypeScreen sampleHandlerQueue:audio.queue error:&problem]) {
            *error = 2;
            return NULL;
        }
        __block bool started = false;
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        [audio.stream startCaptureWithCompletionHandler:^(NSError *failure) {
            started = failure == nil;
            dispatch_semaphore_signal(done);
        }];
        if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)) != 0 || !started) {
            audio.callback = NULL;
            *error = 2;
            return NULL;
        }
        return (__bridge_retained void *)audio;
    }
    *error = 3;
    return NULL;
}

void mixel_audio_stop(void *handle) {
    if (!handle) return;
    if (@available(macOS 13.0, *)) {
        MixelAudio *audio = (__bridge_transfer MixelAudio *)handle;
        audio.callback = NULL;
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        [audio.stream stopCaptureWithCompletionHandler:^(NSError *failure) {
            (void)failure;
            dispatch_semaphore_signal(done);
        }];
        dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
        // Late sample callbacks run on the serial queue: wait for it before the context goes away.
        dispatch_sync(audio.queue, ^{
                      });
    }
}

// Whether the system's own "Lock Screen" call exists (looked up, not called).
bool mixel_lock_available(void) {
    void *library = dlopen("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login", RTLD_LAZY);
    bool found = library && dlsym(library, "SACLockScreenImmediate") != NULL;
    return found;
}
