/* CRB-owned GUI launcher. No CRT, shell, scripting engine or network dependency. */
#define UNICODE
#define _UNICODE
#include <windows.h>

static WCHAR image[32768], command[32768];

static void fail(DWORD code) {
    BOOL chinese = PRIMARYLANGID(GetUserDefaultUILanguage()) == LANG_CHINESE;
    MessageBoxW(NULL,
        chinese ? L"无法启动卸载程序。请确认这是完整安装版，并从原安装目录打开；必要时重新运行安装包修复。"
                : L"Cannot start the uninstaller. Open it from the complete installed application, or run Setup again to repair the installation.",
        L"CodeRelayBridge", MB_OK | MB_ICONERROR);
    ExitProcess(code);
}

static WCHAR *last_separator(WCHAR *text) {
    WCHAR *last = NULL;
    for (; *text; ++text) if (*text == L'\\' || *text == L'/') last = text;
    return last;
}

static BOOL redirected(WCHAR *path) {
    WCHAR *end = path + lstrlenW(path);
    for (;;) {
        DWORD attributes = GetFileAttributesW(path);
        if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) return TRUE;
        if (end - path <= 3) return FALSE;
        end = last_separator(path);
        if (!end || end - path < 3) return FALSE;
        *end = 0;
    }
}

void entry(void) {
    DWORD count = GetModuleFileNameW(NULL, image, 32768);
    if (!count || count >= 32768) fail(ERROR_BAD_PATHNAME);
    WCHAR *end = last_separator(image);
    if (!end) fail(ERROR_BAD_PATHNAME);
    *end = 0;
    end = last_separator(image);
    if (!end || lstrcmpiW(end + 1, L"app")) fail(ERROR_BAD_PATHNAME);
    *end = 0;
    end = last_separator(image);
    if (!end || (lstrcmpiW(end + 1, L"CodexRelayClient") && lstrcmpiW(end + 1, L"CodexRelayServer"))) fail(ERROR_BAD_PATHNAME);
    if (lstrlenW(image) > 32720) fail(ERROR_BAD_PATHNAME);
    lstrcatW(image, L"\\installer\\unins000.exe");
    lstrcpyW(command, image);
    if (redirected(command)) fail(ERROR_BAD_PATHNAME);
    WCHAR *tail = GetCommandLineW();
    while (*tail == L' ' || *tail == L'\t') ++tail;
    if (*tail == L'"') { ++tail; while (*tail && *tail != L'"') ++tail; if (*tail) ++tail; }
    else { while (*tail && *tail != L' ' && *tail != L'\t') ++tail; }
    while (*tail == L' ' || *tail == L'\t') ++tail;
    if (lstrlenW(image) + lstrlenW(tail) + 5 >= 32768) fail(ERROR_BAD_ARGUMENTS);
    lstrcpyW(command, L"\""); lstrcatW(command, image); lstrcatW(command, L"\" "); lstrcatW(command, tail);
    STARTUPINFOW startup = {0}; PROCESS_INFORMATION process = {0}; startup.cb = sizeof(startup);
    if (!CreateProcessW(image, command, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &process)) fail(GetLastError());
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    ExitProcess(0);
}
