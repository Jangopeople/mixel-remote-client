// C interface of the Mixel Quick Support host core (crates/quick_host/src/ffi.rs).
// Texts are UTF-8 with a closing zero; a text returned by the library is given back to mqs_free.
#ifndef MIXEL_QUICK_HOST_H
#define MIXEL_QUICK_HOST_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct MqsHost MqsHost;
/// Called for every event with its JSON text (valid only during the call) and the caller's pointer.
typedef void (*MqsListener)(const char *event, void *context);

MqsHost *mqs_create(const uint8_t *seed, MqsListener listener, void *context);
void mqs_seed(const MqsHost *host, uint8_t *out32);
char *mqs_enroll(const MqsHost *host, const char *code, const char *name);
/* This device's fixed Remote ID; the registration once a technician typed it (JSON, see ffi.rs). Blocks. */
char *mqs_register(const MqsHost *host, const char *name);
char *mqs_start(const MqsHost *host, const char *enrollment, uint32_t width, uint32_t height, bool control);
void mqs_accept_issuer(const MqsHost *host, bool on);
void mqs_decide(const MqsHost *host, bool accept);
void mqs_end_session(const MqsHost *host);
void mqs_stop(const MqsHost *host);
void mqs_push_frame(const MqsHost *host, const uint8_t *data, size_t length);
void mqs_destroy(MqsHost *host);
void mqs_free(char *text);
#endif
