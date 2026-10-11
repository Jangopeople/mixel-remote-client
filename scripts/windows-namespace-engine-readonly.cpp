// Disposable source-only CDB extension. Configuration getters cannot qualify a hit.
#include <windows.h>
#include <dbgeng.h>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <type_traits>

namespace {
template<class T> struct owned_interface {
    T* value = nullptr;
    ~owned_interface() { if (value) value->Release(); }
    owned_interface() = default;
    owned_interface(const owned_interface&) = delete;
    owned_interface& operator=(const owned_interface&) = delete;
};
struct arguments {
    ULONG pid = 0, tid = 0, probe_rva = 0, image_size = 0;
    char image[513] = {}, folder[513] = {};
};
bool bounded_length(const char* text, size_t capacity, size_t* length) {
    if (!text) return false;
    for (size_t index = 0; index < capacity; ++index) {
        if (text[index] == 0) { *length = index; return true; }
    }
    return false;
}
bool decimal(const char* text, ULONG* result) {
    size_t length = 0;
    if (!bounded_length(text, 11, &length) || !length || length > 10 || text[0] == '0') return false;
    uint64_t value = 0;
    for (size_t index = 0; index < length; ++index) {
        if (text[index] < '0' || text[index] > '9') return false;
        value = value * 10 + static_cast<unsigned>(text[index] - '0');
    }
    if (value > UINT32_MAX) return false;
    *result = static_cast<ULONG>(value);
    return true;
}
bool path(const char* text, bool image) {
    size_t length = 0;
    if (!bounded_length(text, 513, &length) || length < 4 || text[0] < 'A' || text[0] > 'Z' || text[1] != ':' || text[2] != '\\' || std::strstr(text, "..")) return false;
    for (size_t index = 3; index < length; ++index) {
        const char c = text[index];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.' || c == '\\')) return false;
    }
    return !image || (length >= 4 && std::strcmp(text + length - 4, ".exe") == 0);
}
bool parse_arguments(const char* input, arguments* result) {
    size_t length = 0;
    if (!bounded_length(input, 1153, &length) || !length) return false;
    char copy[1153] = {}; std::memcpy(copy, input, length);
    char* words[6] = {}; size_t count = 0;
    char* cursor = copy;
    while (*cursor) {
        if (*cursor == ' ' || *cursor == '\t') { ++cursor; continue; }
        if (count == 6) return false;
        words[count++] = cursor;
        while (*cursor && *cursor != ' ' && *cursor != '\t') ++cursor;
        if (*cursor) *cursor++ = 0;
    }
    if (count != 6 || !decimal(words[0], &result->pid) || !decimal(words[1], &result->tid) ||
        !decimal(words[2], &result->probe_rva) || !decimal(words[3], &result->image_size) ||
        result->image_size > 268435456 || result->probe_rva >= result->image_size ||
        !path(words[4], true) || !path(words[5], false)) return false;
    std::memcpy(result->image, words[4], std::strlen(words[4]) + 1);
    std::memcpy(result->folder, words[5], std::strlen(words[5]) + 1);
    return true;
}
bool same_ascii_path(const char* left, const char* right) {
    size_t first = 0, second = 0;
    if (!bounded_length(left, 4096, &first) || !bounded_length(right, 513, &second) || first != second) return false;
    for (size_t index = 0; index < first; ++index) {
        const auto lower = [](char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c + ('a' - 'A')) : c; };
        if (lower(left[index]) != lower(right[index])) return false;
    }
    return true;
}
struct facts {
    unsigned arguments_valid = 0, context_available = 0, process_matched = 0, thread_matched = 0;
    unsigned mapping_available = 0, current_engine_matched = 0, module_available = 0;
    unsigned present = 0, id_matched = 0, flags_available = 0, enabled = 0, deferred = 0, one_shot = 0, adder_only = 0, go_only = 0, unknown_flags = 0;
    unsigned type_available = 0, data_amd64 = 0, data_available = 0, execute_byte = 0, pass_available = 0, pass_one = 0, remaining_available = 0, remaining_one = 0;
    unsigned match_available = 0, specific_thread = 0, any_thread = 0, expected_engine_matched = 0;
    unsigned command_available = 0, callback_matched = 0, offset_available = 0, expected_offset_available = 0, offset_matched = 0;
};
void output(IDebugControl* control, const facts& f) {
    control->Output(DEBUG_OUTPUT_NORMAL,
        "\nMIXEL_CDB_ENGINE_QUERY {\"argumentsValid\":%u,\"contextAvailable\":%u,\"processMatched\":%u,\"threadMatched\":%u,\"mappingAvailable\":%u,\"currentEngineMatched\":%u,\"moduleAvailable\":%u,\"presentMask\":%u,\"idMatchedMask\":%u,\"flagsAvailableMask\":%u,\"enabledMask\":%u,\"deferredMask\":%u,\"oneShotMask\":%u,\"adderOnlyMask\":%u,\"goOnlyMask\":%u,\"unknownFlagsMask\":%u,\"typeAvailableMask\":%u,\"dataAmd64Mask\":%u,\"dataAvailableMask\":%u,\"executeByteMask\":%u,\"passAvailableMask\":%u,\"passOneMask\":%u,\"currentPassAvailableMask\":%u,\"currentPassOneMask\":%u,\"matchAvailableMask\":%u,\"specificThreadMask\":%u,\"anyThreadMask\":%u,\"expectedEngineMatchedMask\":%u,\"commandAvailableMask\":%u,\"callbackMatchedMask\":%u,\"offsetAvailableMask\":%u,\"expectedOffsetAvailableMask\":%u,\"offsetMatchedMask\":%u}\n",
        f.arguments_valid, f.context_available, f.process_matched, f.thread_matched, f.mapping_available, f.current_engine_matched, f.module_available,
        f.present, f.id_matched, f.flags_available, f.enabled, f.deferred, f.one_shot, f.adder_only, f.go_only, f.unknown_flags,
        f.type_available, f.data_amd64, f.data_available, f.execute_byte, f.pass_available, f.pass_one, f.remaining_available, f.remaining_one,
        f.match_available, f.specific_thread, f.any_thread, f.expected_engine_matched, f.command_available, f.callback_matched,
        f.offset_available, f.expected_offset_available, f.offset_matched);
}
}
extern "C" __declspec(dllexport) HRESULT CALLBACK DebugExtensionInitialize(PULONG version, PULONG flags) {
    if (!version || !flags) return E_INVALIDARG;
    *version = DEBUG_EXTENSION_VERSION(1, 0); *flags = 0; return S_OK;
}
extern "C" __declspec(dllexport) HRESULT CALLBACK inspect(PDEBUG_CLIENT client, PCSTR input) {
    // Borrow client only for this synchronous callback; every QI reference is owned.
    if (!client) return E_INVALIDARG;
    owned_interface<IDebugControl> control;
    if (client->QueryInterface(__uuidof(IDebugControl), reinterpret_cast<void**>(&control.value)) != S_OK || !control.value) return E_NOINTERFACE;
    facts f; arguments args;
    if (!parse_arguments(input, &args)) { output(control.value, f); return S_OK; }
    f.arguments_valid = 1;
    owned_interface<IDebugSystemObjects> system;
    if (client->QueryInterface(__uuidof(IDebugSystemObjects), reinterpret_cast<void**>(&system.value)) != S_OK || !system.value) { output(control.value, f); return S_OK; }
    ULONG current_pid = 0, current_tid = 0, current_engine = 0, expected_engine = 0;
    if (system.value->GetCurrentProcessSystemId(&current_pid) == S_OK && system.value->GetCurrentThreadSystemId(&current_tid) == S_OK) {
        f.context_available = 1; f.process_matched = current_pid == args.pid; f.thread_matched = current_tid == args.tid;
    }
    if (f.process_matched && f.thread_matched && system.value->GetThreadIdBySystemId(args.tid, &expected_engine) == S_OK && system.value->GetCurrentThreadId(&current_engine) == S_OK) {
        f.mapping_available = 1; f.current_engine_matched = current_engine == expected_engine;
    }
    if (!f.mapping_available || !f.current_engine_matched) { output(control.value, f); return S_OK; }
    owned_interface<IDebugSymbols> symbols;
    ULONG64 expected_offsets[4] = {};
    if (client->QueryInterface(__uuidof(IDebugSymbols), reinterpret_cast<void**>(&symbols.value)) == S_OK && symbols.value) {
        const char* names[3] = { "ntdll!NtOpenDirectoryObject", "ntdll!NtOpenEvent", "KERNELBASE!OpenEventW" };
        for (unsigned slot = 0; slot < 3; ++slot) if (symbols.value->GetOffsetByName(names[slot], &expected_offsets[slot]) == S_OK && expected_offsets[slot]) f.expected_offset_available |= 1u << slot;
        ULONG loaded = 0, unloaded = 0, matches = 0; ULONG64 image_base = 0;
        bool module_scan_valid = symbols.value->GetNumberModules(&loaded, &unloaded) == S_OK && loaded > 0 && loaded <= 128;
        if (module_scan_valid) for (ULONG index = 0; index < loaded; ++index) {
            char image[4096]; std::memset(image, 0x7f, sizeof(image)); ULONG64 base = 0; size_t length = 0;
            if (symbols.value->GetModuleByIndex(index, &base) != S_OK || !base ||
                symbols.value->GetModuleNames(index, base, image, static_cast<ULONG>(sizeof(image)), nullptr, nullptr, 0, nullptr, nullptr, 0, nullptr) != S_OK ||
                !bounded_length(image, sizeof(image), &length)) { module_scan_valid = false; break; }
            if (same_ascii_path(image, args.image)) { ++matches; image_base = base; }
        }
        if (module_scan_valid && matches == 1 && image_base <= UINT64_MAX - args.image_size) {
            f.module_available = 1; expected_offsets[3] = image_base + args.probe_rva; f.expected_offset_available |= 8;
        }
    }
    char expected[4][1024] = {};
    const int lengths[4] = {
        std::snprintf(expected[0], sizeof(expected[0]), "r $t1 = 1; $$><%s\\entry.txt", args.folder),
        std::snprintf(expected[1], sizeof(expected[1]), "r $t1 = 2; $$><%s\\entry.txt", args.folder),
        std::snprintf(expected[2], sizeof(expected[2]), "$$><%s\\winentry.txt", args.folder),
        std::snprintf(expected[3], sizeof(expected[3]), "$$><%s\\hwcontrol.txt", args.folder)
    };
    for (unsigned slot = 0; slot < 4; ++slot) {
        const unsigned bit = 1u << slot;
        owned_interface<IDebugBreakpoint> breakpoint;
        if (control.value->GetBreakpointById(slot, &breakpoint.value) != S_OK || !breakpoint.value) continue;
        f.present |= bit;
        ULONG id = UINT32_MAX, flags = 0, type = 0, machine = 0, size = 0, access = 0, count = 0, match = 0;
        if (breakpoint.value->GetId(&id) != S_OK || id != slot) continue;
        f.id_matched |= bit;
        if (breakpoint.value->GetFlags(&flags) == S_OK) {
            f.flags_available |= bit;
            if (flags & DEBUG_BREAKPOINT_ENABLED) f.enabled |= bit;
            if (flags & DEBUG_BREAKPOINT_DEFERRED) f.deferred |= bit;
            if (flags & DEBUG_BREAKPOINT_ONE_SHOT) f.one_shot |= bit;
            if (flags & DEBUG_BREAKPOINT_ADDER_ONLY) f.adder_only |= bit;
            if (flags & DEBUG_BREAKPOINT_GO_ONLY) f.go_only |= bit;
            if (flags & ~(DEBUG_BREAKPOINT_ENABLED | DEBUG_BREAKPOINT_DEFERRED | DEBUG_BREAKPOINT_ONE_SHOT | DEBUG_BREAKPOINT_ADDER_ONLY | DEBUG_BREAKPOINT_GO_ONLY)) f.unknown_flags |= bit;
        }
        if (breakpoint.value->GetType(&type, &machine) == S_OK) { f.type_available |= bit; if (type == DEBUG_BREAKPOINT_DATA && machine == IMAGE_FILE_MACHINE_AMD64) f.data_amd64 |= bit; }
        if (breakpoint.value->GetDataParameters(&size, &access) == S_OK) { f.data_available |= bit; if (size == 1 && access == DEBUG_BREAK_EXECUTE) f.execute_byte |= bit; }
        if (breakpoint.value->GetPassCount(&count) == S_OK) { f.pass_available |= bit; if (count == 1) f.pass_one |= bit; }
        if (breakpoint.value->GetCurrentPassCount(&count) == S_OK) { f.remaining_available |= bit; if (count == 1) f.remaining_one |= bit; }
        const HRESULT match_result = breakpoint.value->GetMatchThreadId(&match);
        if (match_result == S_OK) { f.match_available |= bit; f.specific_thread |= bit; if (match == expected_engine) f.expected_engine_matched |= bit; }
        else if (match_result == E_NOINTERFACE) { f.match_available |= bit; f.any_thread |= bit; }
        char command[4096]; std::memset(command, 0x7f, sizeof(command)); size_t command_length = 0;
        if (breakpoint.value->GetCommand(command, static_cast<ULONG>(sizeof(command)), nullptr) == S_OK && bounded_length(command, sizeof(command), &command_length)) {
            f.command_available |= bit;
            if (lengths[slot] > 0 && static_cast<size_t>(lengths[slot]) < sizeof(expected[slot]) && std::strcmp(command, expected[slot]) == 0) f.callback_matched |= bit;
        }
        ULONG64 offset = 0;
        if (breakpoint.value->GetOffset(&offset) == S_OK && offset) { f.offset_available |= bit; if ((f.expected_offset_available & bit) && offset == expected_offsets[slot]) f.offset_matched |= bit; }
    }
    output(control.value, f); return S_OK;
}
static_assert(std::is_same<decltype(&inspect), PDEBUG_EXTENSION_CALL>::value, "SDK callback ABI differs");
static_assert(std::is_same<decltype(&DebugExtensionInitialize), PDEBUG_EXTENSION_INITIALIZE>::value, "SDK initialization ABI differs");
