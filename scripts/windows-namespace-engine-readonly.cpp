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
struct shapes {
    unsigned command_available = 0, lengths[4] = {};
    unsigned outer_quote = 0, edge_whitespace = 0, control_character = 0;
    unsigned quoted_expected = 0, trimmed_expected = 0, expected_body = 0, expected_script = 0, backslash_deleted_expected = 0;
    unsigned module_enumeration_available = 0, module_count = 0, scanned_count = 0;
    unsigned module_names_success = 0, module_names_bounded = 0, module_exact_path = 0, module_case_path = 0, module_basename_only = 0;
    unsigned module_scan_completed = 0;
};
bool ascii_whitespace(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\v' || c == '\f'; }
void command_shapes(const char* command, size_t length, const char* expected, const char* script, unsigned bit, unsigned slot, shapes* s) {
    s->command_available |= bit; s->lengths[slot] = static_cast<unsigned>(length);
    if (length >= 2 && command[0] == '"' && command[length - 1] == '"') {
        s->outer_quote |= bit;
        const size_t expected_length = std::strlen(expected);
        if (length == expected_length + 2 && std::memcmp(command + 1, expected, expected_length) == 0) s->quoted_expected |= bit;
    }
    if (length && (ascii_whitespace(command[0]) || ascii_whitespace(command[length - 1]))) s->edge_whitespace |= bit;
    size_t first = 0, last = length;
    while (first < last && ascii_whitespace(command[first])) ++first;
    while (last > first && ascii_whitespace(command[last - 1])) --last;
    if (last - first == std::strlen(expected) && std::memcmp(command + first, expected, last - first) == 0) s->trimmed_expected |= bit;
    for (size_t index = 0; index < length; ++index) if (static_cast<unsigned char>(command[index]) < 32 || command[index] == 127) s->control_character |= bit;
    if (std::strstr(command, expected)) s->expected_body |= bit;
    if (script && std::strstr(command, script)) s->expected_script |= bit;
    // Compare a private expected variant only; never normalize the actual callback.
    char deleted[1024] = {}; size_t used = 0, expected_length = 0;
    if (!bounded_length(expected, sizeof(deleted), &expected_length)) return;
    for (size_t index = 0; index < expected_length; ++index) if (expected[index] != '\\') deleted[used++] = expected[index];
    if (std::strcmp(command, deleted) == 0) s->backslash_deleted_expected |= bit;
}
void output_shapes(IDebugControl* control, const shapes& s) {
    control->Output(DEBUG_OUTPUT_NORMAL,
        "\nMIXEL_CDB_ENGINE_SHAPES {\"commandAvailableMask\":%u,\"commandLength0\":%u,\"commandLength1\":%u,\"commandLength2\":%u,\"commandLength3\":%u,\"outerQuoteMask\":%u,\"edgeWhitespaceMask\":%u,\"controlCharacterMask\":%u,\"quotedExpectedMask\":%u,\"trimmedExpectedMask\":%u,\"expectedBodyOccurrenceMask\":%u,\"expectedScriptOccurrenceMask\":%u,\"backslashDeletedExpectedMask\":%u,\"moduleEnumerationAvailable\":%u,\"moduleCount\":%u,\"scannedCount\":%u,\"moduleNamesSuccessCount\":%u,\"moduleNamesBoundedCount\":%u,\"moduleExactPathCount\":%u,\"moduleCasePathCount\":%u,\"moduleBasenameOnlyCount\":%u,\"moduleScanCompleted\":%u}\n",
        s.command_available, s.lengths[0], s.lengths[1], s.lengths[2], s.lengths[3],
        s.outer_quote, s.edge_whitespace, s.control_character, s.quoted_expected, s.trimmed_expected, s.expected_body, s.expected_script, s.backslash_deleted_expected,
        s.module_enumeration_available, s.module_count, s.scanned_count, s.module_names_success, s.module_names_bounded,
        s.module_exact_path, s.module_case_path, s.module_basename_only, s.module_scan_completed);
}
void output(IDebugControl* control, const facts& f) {
    control->Output(DEBUG_OUTPUT_NORMAL,
        "\nMIXEL_CDB_ENGINE_QUERY {\"argumentsValid\":%u,\"contextAvailable\":%u,\"processMatched\":%u,\"threadMatched\":%u,\"mappingAvailable\":%u,\"currentEngineMatched\":%u,\"moduleAvailable\":%u,\"presentMask\":%u,\"idMatchedMask\":%u,\"flagsAvailableMask\":%u,\"enabledMask\":%u,\"deferredMask\":%u,\"oneShotMask\":%u,\"adderOnlyMask\":%u,\"goOnlyMask\":%u,\"unknownFlagsMask\":%u,\"typeAvailableMask\":%u,\"dataAmd64Mask\":%u,\"dataAvailableMask\":%u,\"executeByteMask\":%u,\"passAvailableMask\":%u,\"passOneMask\":%u,\"currentPassAvailableMask\":%u,\"currentPassOneMask\":%u,\"matchAvailableMask\":%u,\"specificThreadMask\":%u,\"anyThreadMask\":%u,\"expectedEngineMatchedMask\":%u,\"commandAvailableMask\":%u,\"callbackMatchedMask\":%u,\"offsetAvailableMask\":%u,\"expectedOffsetAvailableMask\":%u,\"offsetMatchedMask\":%u}\n",
        f.arguments_valid, f.context_available, f.process_matched, f.thread_matched, f.mapping_available, f.current_engine_matched, f.module_available,
        f.present, f.id_matched, f.flags_available, f.enabled, f.deferred, f.one_shot, f.adder_only, f.go_only, f.unknown_flags,
        f.type_available, f.data_amd64, f.data_available, f.execute_byte, f.pass_available, f.pass_one, f.remaining_available, f.remaining_one,
        f.match_available, f.specific_thread, f.any_thread, f.expected_engine_matched, f.command_available, f.callback_matched,
        f.offset_available, f.expected_offset_available, f.offset_matched);
}

struct loaded_facts {
    unsigned interface_available = 0, cache_available = 0, module_count = 0, scanned_count = 0;
    unsigned names_success = 0, names_bounded = 0, exact_path = 0, case_path = 0, basename_only = 0;
    unsigned scan_completed = 0, unique_full_path_matched = 0, probe_offset_available = 0, probe_offset_matched = 0;
};
void output_loaded(IDebugControl* control, const loaded_facts& l) {
    control->Output(DEBUG_OUTPUT_NORMAL,
        "\nMIXEL_CDB_ENGINE_LOAD {\"interfaceAvailable\":%u,\"cacheAvailable\":%u,\"moduleCount\":%u,\"scannedCount\":%u,\"namesSuccessCount\":%u,\"namesBoundedCount\":%u,\"exactPathCount\":%u,\"casePathCount\":%u,\"basenameOnlyCount\":%u,\"scanCompleted\":%u,\"uniqueFullPathMatched\":%u,\"probeOffsetAvailable\":%u,\"probeOffsetMatched\":%u}\n",
        l.interface_available, l.cache_available, l.module_count, l.scanned_count, l.names_success, l.names_bounded,
        l.exact_path, l.case_path, l.basename_only, l.scan_completed, l.unique_full_path_matched, l.probe_offset_available, l.probe_offset_matched);
}
void inspect_loaded(PDEBUG_CLIENT client, const arguments& args, const ULONG64* bases, ULONG count,
                    bool cache_available, ULONG64 actual_probe_offset, loaded_facts* l) {
    if (!cache_available || !count || count > 128) return;
    for (ULONG index = 0; index < count; ++index) if (!bases[index]) return;
    l->cache_available = 1; l->module_count = count;
    owned_interface<IDebugSymbols2> symbols;
    if (client->QueryInterface(__uuidof(IDebugSymbols2), reinterpret_cast<void**>(&symbols.value)) != S_OK || !symbols.value) return;
    l->interface_available = 1; bool complete = true; ULONG64 matched_base = 0;
    for (ULONG index = 0; index < count; ++index) {
        char name[4096]; std::memset(name, 0x7f, sizeof(name)); size_t length = 0; ++l->scanned_count;
        // DEBUG_ANY_ID makes the getter name this same cached base, not an index.
        if (symbols.value->GetModuleNameString(DEBUG_MODNAME_LOADED_IMAGE, DEBUG_ANY_ID, bases[index], name, static_cast<ULONG>(sizeof(name)), nullptr) != S_OK) { complete = false; break; }
        ++l->names_success;
        if (!bounded_length(name, sizeof(name), &length) || !length) { complete = false; break; }
        ++l->names_bounded;
        if (std::strcmp(name, args.image) == 0) ++l->exact_path;
        if (same_ascii_path(name, args.image)) { ++l->case_path; matched_base = bases[index]; }
        const char* leaf = std::strrchr(args.image, '\\');
        if (leaf && !std::strchr(name, '\\') && !std::strchr(name, '/') && same_ascii_path(name, leaf + 1)) ++l->basename_only;
    }
    l->scan_completed = complete;
    if (!complete || l->case_path != 1) return;
    l->unique_full_path_matched = 1;
    if (matched_base > UINT64_MAX - args.image_size) return;
    const ULONG64 end = matched_base + args.image_size, probe = matched_base + args.probe_rva;
    if (probe <= matched_base || probe >= end) return;
    l->probe_offset_available = 1; l->probe_offset_matched = actual_probe_offset && actual_probe_offset == probe;
}
// Optional direct address metadata. GetModuleByOffset names the first containing
// module; cached_base_unique means equal-base multiplicity in our saved cache.
// Neither this projection nor the engine status snapshot proves a hardware hit.
struct probe_facts {
    unsigned cache_available = 0, actual_offset_available = 0, interface_available = 0;
    unsigned lookup_available = 0, cached_base_matched = 0, cached_base_unique = 0;
    unsigned loaded_name_available = 0, loaded_path_matched = 0, image_range_matched = 0, probe_rva_matched = 0;
};
void output_probe(IDebugControl* control, const probe_facts& p) {
    control->Output(DEBUG_OUTPUT_NORMAL,
        "\nMIXEL_CDB_ENGINE_PROBE {\"cacheAvailable\":%u,\"actualProbeOffsetAvailable\":%u,\"interfaceAvailable\":%u,\"moduleLookupAvailable\":%u,\"cachedBaseMatched\":%u,\"cachedBaseUnique\":%u,\"loadedNameAvailable\":%u,\"loadedPathMatched\":%u,\"imageRangeMatched\":%u,\"probeRvaMatched\":%u}\n",
        p.cache_available, p.actual_offset_available, p.interface_available, p.lookup_available,
        p.cached_base_matched, p.cached_base_unique, p.loaded_name_available, p.loaded_path_matched,
        p.image_range_matched, p.probe_rva_matched);
}
void inspect_probe(PDEBUG_CLIENT client, IDebugSymbols* original_symbols, const arguments& args,
                   const ULONG64* bases, ULONG count, bool cache_available, ULONG64 actual_probe_offset, probe_facts* p) {
    if (!cache_available || !bases || !count || count > 128) return;
    for (ULONG index = 0; index < count; ++index) if (!bases[index]) return;
    p->cache_available = 1;
    if (!actual_probe_offset) return;
    p->actual_offset_available = 1;
    if (!original_symbols) return;
    ULONG index = DEBUG_ANY_ID; ULONG64 base = 0;
    if (original_symbols->GetModuleByOffset(actual_probe_offset, 0, &index, &base) != S_OK ||
        index >= count || !base) return;
    p->lookup_available = 1;
    if (bases[index] != base) return;
    p->cached_base_matched = 1;
    unsigned occurrences = 0;
    for (ULONG item = 0; item < count; ++item) if (bases[item] == base) ++occurrences;
    if (occurrences != 1) return;
    p->cached_base_unique = 1;
    owned_interface<IDebugSymbols2> symbols;
    if (client->QueryInterface(__uuidof(IDebugSymbols2), reinterpret_cast<void**>(&symbols.value)) != S_OK || !symbols.value) return;
    p->interface_available = 1;
    char name[4096]; std::memset(name, 0x7f, sizeof(name)); size_t length = 0;
    if (symbols.value->GetModuleNameString(DEBUG_MODNAME_LOADED_IMAGE, DEBUG_ANY_ID, base, name, static_cast<ULONG>(sizeof(name)), nullptr) != S_OK ||
        !bounded_length(name, sizeof(name), &length) || !length) return;
    p->loaded_name_available = 1;
    if (!same_ascii_path(name, args.image)) return;
    p->loaded_path_matched = 1;
    if (base > UINT64_MAX - args.image_size || actual_probe_offset <= base || actual_probe_offset >= base + args.image_size) return;
    p->image_range_matched = 1;
    p->probe_rva_matched = actual_probe_offset - base == args.probe_rva;
}
struct execution_facts { unsigned available = 0, kind = 0; };
void output_execution(IDebugControl* control, const execution_facts& e) {
    control->Output(DEBUG_OUTPUT_NORMAL,
        "\nMIXEL_CDB_ENGINE_EXECUTION {\"statusAvailable\":%u,\"statusKind\":%u}\n", e.available, e.kind);
}
void inspect_execution(IDebugControl* control, execution_facts* e) {
    ULONG status = 0;
    if (control->GetExecutionStatus(&status) != S_OK) return;
    e->available = 1;
    switch (status) {
    case DEBUG_STATUS_BREAK: e->kind = 1; break;
    case DEBUG_STATUS_GO: e->kind = 2; break;
    case DEBUG_STATUS_NO_DEBUGGEE: e->kind = 3; break;
    case DEBUG_STATUS_STEP_OVER: e->kind = 4; break;
    case DEBUG_STATUS_STEP_INTO: e->kind = 5; break;
    case DEBUG_STATUS_STEP_BRANCH: e->kind = 6; break;
    default: e->kind = 7; break;
    }
}
struct event_facts {
    unsigned arguments_valid = 0, status_available = 0, engine_stopped = 0;
    unsigned context_available = 0, process_matched = 0, mapping_available = 0;
    unsigned event_available = 0, event_process_matched = 0, event_thread_matched = 0, event_is_exception = 0;
    unsigned exception_available = 0, exception_kind = 0, first_chance = 0;
    unsigned location_available = 0, location_kind = 0;
};
void output_event(IDebugControl* control, const event_facts& e) {
    control->Output(DEBUG_OUTPUT_NORMAL,
        "\nMIXEL_CDB_ENGINE_EVENT {\"argumentsValid\":%u,\"statusAvailable\":%u,\"engineStopped\":%u,\"contextAvailable\":%u,\"processMatched\":%u,\"mappingAvailable\":%u,\"eventAvailable\":%u,\"eventProcessMatched\":%u,\"eventThreadMatched\":%u,\"eventIsException\":%u,\"exceptionAvailable\":%u,\"exceptionKind\":%u,\"firstChance\":%u,\"locationAvailableMask\":%u,\"locationKind\":%u}\n",
        e.arguments_valid, e.status_available, e.engine_stopped, e.context_available, e.process_matched, e.mapping_available,
        e.event_available, e.event_process_matched, e.event_thread_matched, e.event_is_exception,
        e.exception_available, e.exception_kind, e.first_chance, e.location_available, e.location_kind);
}
struct stack_facts {
    unsigned current_thread_available = 0, current_event_thread_matched = 0;
    unsigned stack_available = 0, frame_count = 0, frame0_instruction_matched = 0;
    unsigned symbol_available = 0, displacement_zero = 0, dbg_break_point = 0, remote_breakin = 0, loader_break = 0;
};
void output_stack(IDebugControl* control, const stack_facts& s) {
    control->Output(DEBUG_OUTPUT_NORMAL,
        "\nMIXEL_CDB_ENGINE_STACK {\"currentThreadAvailable\":%u,\"currentEventThreadMatched\":%u,\"stackAvailable\":%u,\"frameCount\":%u,\"frame0InstructionMatched\":%u,\"symbolAvailableMask\":%u,\"displacementZeroMask\":%u,\"dbgBreakPointMask\":%u,\"remoteBreakinMask\":%u,\"loaderBreakMask\":%u}\n",
        s.current_thread_available, s.current_event_thread_matched, s.stack_available, s.frame_count, s.frame0_instruction_matched,
        s.symbol_available, s.displacement_zero, s.dbg_break_point, s.remote_breakin, s.loader_break);
}
void output_event_stack(IDebugControl* control, const event_facts& e, const stack_facts& s) {
    output_event(control, e); output_stack(control, s);
}
void inspect_stack(IDebugControl* control, IDebugSystemObjects* system, IDebugSymbols* symbols,
                   ULONG event_thread, ULONG64 exception_address, stack_facts* s) {
    ULONG current_thread = DEBUG_ANY_ID;
    if (system->GetCurrentThreadId(&current_thread) != S_OK || current_thread == DEBUG_ANY_ID) return;
    s->current_thread_available = 1; s->current_event_thread_matched = current_thread == event_thread;
    if (!s->current_event_thread_matched) return; // Current context only; no selection.
    DEBUG_STACK_FRAME frames[8] = {}; ULONG filled = UINT32_MAX;
    if (control->GetStackTrace(0, 0, 0, frames, 8, &filled) != S_OK || filled == 0 || filled > 8) return;
    s->stack_available = 1; s->frame_count = filled;
    s->frame0_instruction_matched = exception_address && frames[0].InstructionOffset == exception_address;
    if (!s->frame0_instruction_matched || !symbols) return;
    const char* known[3] = { "ntdll!DbgBreakPoint", "ntdll!DbgUiRemoteBreakin", "ntdll!LdrpDoDebuggerBreak" };
    for (ULONG index = 0; index < filled; ++index) {
        if (!frames[index].InstructionOffset) continue;
        char name[4096]; std::memset(name, 0x7f, sizeof(name)); size_t length = 0; ULONG64 displacement = UINT64_MAX;
        if (symbols->GetNameByOffset(frames[index].InstructionOffset, name, static_cast<ULONG>(sizeof(name)), nullptr, &displacement) != S_OK ||
            !bounded_length(name, sizeof(name), &length) || !length || displacement >= 268435456) continue;
        const unsigned bit = 1u << index; s->symbol_available |= bit;
        if (!displacement) s->displacement_zero |= bit;
        if (std::strcmp(name, known[0]) == 0) s->dbg_break_point |= bit;
        else if (std::strcmp(name, known[1]) == 0) s->remote_breakin |= bit;
        else if (std::strcmp(name, known[2]) == 0) s->loader_break |= bit;
    }
}
HRESULT inspect_event(PDEBUG_CLIENT client, PCSTR input) {
    owned_interface<IDebugControl> control;
    if (client->QueryInterface(__uuidof(IDebugControl), reinterpret_cast<void**>(&control.value)) != S_OK || !control.value) return E_NOINTERFACE;
    event_facts e; stack_facts s; arguments args;
    if (!parse_arguments(input, &args)) { output_event_stack(control.value, e, s); return S_OK; }
    e.arguments_valid = 1;
    ULONG status = DEBUG_STATUS_NO_DEBUGGEE;
    if (control.value->GetExecutionStatus(&status) != S_OK) { output_event_stack(control.value, e, s); return S_OK; }
    e.status_available = 1; e.engine_stopped = status == DEBUG_STATUS_BREAK;
    if (!e.engine_stopped) { output_event_stack(control.value, e, s); return S_OK; }
    owned_interface<IDebugSystemObjects> system;
    if (client->QueryInterface(__uuidof(IDebugSystemObjects), reinterpret_cast<void**>(&system.value)) != S_OK || !system.value) { output_event_stack(control.value, e, s); return S_OK; }
    ULONG current_pid = 0, current_engine_process = DEBUG_ANY_ID, expected_engine_thread = DEBUG_ANY_ID;
    if (system.value->GetCurrentProcessSystemId(&current_pid) == S_OK && system.value->GetCurrentProcessId(&current_engine_process) == S_OK && current_engine_process != DEBUG_ANY_ID) {
        e.context_available = 1; e.process_matched = current_pid == args.pid;
    }
    if (!e.process_matched || system.value->GetThreadIdBySystemId(args.tid, &expected_engine_thread) != S_OK || expected_engine_thread == DEBUG_ANY_ID) { output_event_stack(control.value, e, s); return S_OK; }
    e.mapping_available = 1;
    ULONG type = 0, last_process = DEBUG_ANY_ID, last_thread_index = DEBUG_ANY_ID, used = 0;
    DEBUG_LAST_EVENT_INFO_EXCEPTION info = {};
    if (control.value->GetLastEventInformation(&type, &last_process, &last_thread_index, &info, static_cast<ULONG>(sizeof(info)), &used, nullptr, 0, nullptr) != S_OK || last_process == DEBUG_ANY_ID || last_thread_index == DEBUG_ANY_ID) { output_event_stack(control.value, e, s); return S_OK; }
    // LastEventInformation exposes a thread INDEX. Correlate only the explicit
    // event engine IDs; never reinterpret that index as a mapped engine ID.
    ULONG event_process = DEBUG_ANY_ID, event_thread = DEBUG_ANY_ID;
    if (system.value->GetEventProcess(&event_process) != S_OK || system.value->GetEventThread(&event_thread) != S_OK || event_process == DEBUG_ANY_ID || event_thread == DEBUG_ANY_ID) { output_event_stack(control.value, e, s); return S_OK; }
    e.event_available = 1; e.event_process_matched = event_process == current_engine_process; e.event_thread_matched = event_thread == expected_engine_thread;
    if (!e.event_process_matched) { e.event_thread_matched = 0; output_event_stack(control.value, e, s); return S_OK; }
    e.event_is_exception = type == DEBUG_EVENT_EXCEPTION;
    if (!e.event_is_exception || used != sizeof(info) || info.FirstChance > 1 || info.ExceptionRecord.NumberParameters > EXCEPTION_MAXIMUM_PARAMETERS) { output_event_stack(control.value, e, s); return S_OK; }
    e.exception_available = 1; e.first_chance = info.FirstChance;
    const ULONG code = info.ExceptionRecord.ExceptionCode;
    e.exception_kind = code == 0x80000003u ? 1u : code == 0x80000004u ? 2u : code == 0xc0000005u ? 3u : 4u;
    owned_interface<IDebugSymbols> symbols;
    if (client->QueryInterface(__uuidof(IDebugSymbols), reinterpret_cast<void**>(&symbols.value)) == S_OK && symbols.value) {
        const char* names[3] = { "ntdll!DbgBreakPoint", "ntdll!DbgUiRemoteBreakin", "ntdll!LdrpDoDebuggerBreak" };
        unsigned matches = 0, kind = 0;
        for (unsigned index = 0; index < 3; ++index) {
            ULONG64 address = 0;
            if (symbols.value->GetOffsetByName(names[index], &address) == S_OK && address) {
                e.location_available |= 1u << index;
                if (address == info.ExceptionRecord.ExceptionAddress) { ++matches; kind = index + 1; }
            }
        }
        if (matches == 1) e.location_kind = kind;
    }
    inspect_stack(control.value, system.value, symbols.value, event_thread, info.ExceptionRecord.ExceptionAddress, &s);
    output_event_stack(control.value, e, s); return S_OK;
}
}
extern "C" __declspec(dllexport) HRESULT CALLBACK DebugExtensionInitialize(PULONG version, PULONG flags) {
    if (!version || !flags) return E_INVALIDARG;
    *version = DEBUG_EXTENSION_VERSION(1, 0); *flags = 0; return S_OK;
}
extern "C" __declspec(dllexport) HRESULT CALLBACK inspect(PDEBUG_CLIENT client, PCSTR input) {
    // Borrow client only for this synchronous callback; every QI reference is owned.
    if (!client) return E_INVALIDARG;
    if (input && std::strncmp(input, "event ", 6) == 0) return inspect_event(client, input + 6);
    owned_interface<IDebugControl> control;
    if (client->QueryInterface(__uuidof(IDebugControl), reinterpret_cast<void**>(&control.value)) != S_OK || !control.value) return E_NOINTERFACE;
    facts f; shapes s; loaded_facts l; probe_facts p; execution_facts e; arguments args;
    if (!parse_arguments(input, &args)) { output(control.value, f); output_shapes(control.value, s); output_loaded(control.value, l); output_probe(control.value, p); output_execution(control.value, e); return S_OK; }
    f.arguments_valid = 1;
    owned_interface<IDebugSystemObjects> system;
    if (client->QueryInterface(__uuidof(IDebugSystemObjects), reinterpret_cast<void**>(&system.value)) != S_OK || !system.value) { output(control.value, f); output_shapes(control.value, s); output_loaded(control.value, l); output_probe(control.value, p); output_execution(control.value, e); return S_OK; }
    ULONG current_pid = 0, current_tid = 0, current_engine = 0, expected_engine = 0;
    if (system.value->GetCurrentProcessSystemId(&current_pid) == S_OK && system.value->GetCurrentThreadSystemId(&current_tid) == S_OK) {
        f.context_available = 1; f.process_matched = current_pid == args.pid; f.thread_matched = current_tid == args.tid;
    }
    if (f.process_matched && f.thread_matched && system.value->GetThreadIdBySystemId(args.tid, &expected_engine) == S_OK && system.value->GetCurrentThreadId(&current_engine) == S_OK) {
        f.mapping_available = 1; f.current_engine_matched = current_engine == expected_engine;
    }
    if (!f.mapping_available || !f.current_engine_matched) { output(control.value, f); output_shapes(control.value, s); output_loaded(control.value, l); output_probe(control.value, p); output_execution(control.value, e); return S_OK; }
    inspect_execution(control.value, &e);
    owned_interface<IDebugSymbols> symbols;
    ULONG64 expected_offsets[4] = {}, module_bases[128] = {}, actual_probe_offset = 0;
    ULONG cached_module_count = 0; bool cached_modules_available = false;
    if (client->QueryInterface(__uuidof(IDebugSymbols), reinterpret_cast<void**>(&symbols.value)) == S_OK && symbols.value) {
        const char* names[3] = { "ntdll!NtOpenDirectoryObject", "ntdll!NtOpenEvent", "KERNELBASE!OpenEventW" };
        for (unsigned slot = 0; slot < 3; ++slot) if (symbols.value->GetOffsetByName(names[slot], &expected_offsets[slot]) == S_OK && expected_offsets[slot]) f.expected_offset_available |= 1u << slot;
        ULONG loaded = 0, unloaded = 0, matches = 0; ULONG64 image_base = 0;
        bool module_scan_valid = symbols.value->GetNumberModules(&loaded, &unloaded) == S_OK && loaded > 0 && loaded <= 128;
        if (module_scan_valid) { s.module_enumeration_available = 1; s.module_count = loaded; }
        if (module_scan_valid) for (ULONG index = 0; index < loaded; ++index) {
            char image[4096]; std::memset(image, 0x7f, sizeof(image)); ULONG64 base = 0; size_t length = 0;
            ++s.scanned_count;
            if (symbols.value->GetModuleByIndex(index, &base) != S_OK || !base) { module_scan_valid = false; break; }
            module_bases[index] = base;
            if (symbols.value->GetModuleNames(index, base, image, static_cast<ULONG>(sizeof(image)), nullptr, nullptr, 0, nullptr, nullptr, 0, nullptr) != S_OK) { module_scan_valid = false; break; }
            ++s.module_names_success;
            if (!bounded_length(image, sizeof(image), &length)) { module_scan_valid = false; break; }
            ++s.module_names_bounded;
            if (std::strcmp(image, args.image) == 0) ++s.module_exact_path;
            if (same_ascii_path(image, args.image)) ++s.module_case_path;
            const char* leaf = std::strrchr(args.image, '\\');
            if (leaf && !std::strchr(image, '\\') && !std::strchr(image, '/') && same_ascii_path(image, leaf + 1)) ++s.module_basename_only;
            if (same_ascii_path(image, args.image)) { ++matches; image_base = base; }
        }
        s.module_scan_completed = module_scan_valid;
        cached_module_count = loaded; cached_modules_available = module_scan_valid;
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
            if (lengths[slot] > 0 && static_cast<size_t>(lengths[slot]) < sizeof(expected[slot])) {
                if (std::strcmp(command, expected[slot]) == 0) f.callback_matched |= bit;
                const char* script = std::strstr(expected[slot], "$$><");
                command_shapes(command, command_length, expected[slot], script, bit, slot, &s);
            }
        }
        ULONG64 offset = 0;
        if (breakpoint.value->GetOffset(&offset) == S_OK && offset) { if (slot == 3) actual_probe_offset = offset; f.offset_available |= bit; if ((f.expected_offset_available & bit) && offset == expected_offsets[slot]) f.offset_matched |= bit; }
    }
    inspect_loaded(client, args, module_bases, cached_module_count, cached_modules_available, actual_probe_offset, &l);
    inspect_probe(client, symbols.value, args, module_bases, cached_module_count, cached_modules_available, actual_probe_offset, &p);
    output(control.value, f); output_shapes(control.value, s); output_loaded(control.value, l); output_probe(control.value, p); output_execution(control.value, e); return S_OK;
}
static_assert(std::is_same<decltype(&inspect), PDEBUG_EXTENSION_CALL>::value, "SDK callback ABI differs");
static_assert(std::is_same<decltype(&DebugExtensionInitialize), PDEBUG_EXTENSION_INITIALIZE>::value, "SDK initialization ABI differs");
