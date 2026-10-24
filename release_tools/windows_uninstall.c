/* CRB-owned GUI launcher. No CRT, shell, scripting engine or network dependency. */
#define UNICODE
#define _UNICODE
#include <windows.h>

static WCHAR image[32768], command[32768], location[32768];

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

static BOOL registered(const WCHAR *key_name) {
    HKEY key; DWORD kind = 0, bytes = sizeof(location);
    if (RegOpenKeyExW(HKEY_CURRENT_USER, key_name, 0, KEY_QUERY_VALUE | KEY_WOW64_64KEY, &key) != ERROR_SUCCESS) return FALSE;
    LONG result = RegQueryValueExW(key, L"InstallLocation", NULL, &kind, (BYTE *)location, &bytes);
    RegCloseKey(key);
    if (result != ERROR_SUCCESS || kind != REG_SZ || bytes < sizeof(WCHAR) || bytes > sizeof(location) || location[bytes / sizeof(WCHAR) - 1]) return FALSE;
    DWORD length = lstrlenW(location);
    while (length > 3 && (location[length - 1] == L'\\' || location[length - 1] == L'/')) location[--length] = 0;
    return !lstrcmpiW(location, image);
}

void entry(void) {
    DWORD count = GetModuleFileNameW(NULL, image, 32768);
    if (!count || count >= 32768) fail(ERROR_BAD_PATHNAME);
    WCHAR *end = last_separator(image);
    if (!end) fail(ERROR_BAD_PATHNAME);
    *end = 0;
    BOOL client = registered(L"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\CodeRelayBridge.client_is1");
    BOOL server = registered(L"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\CodeRelayBridge.server_is1");
    if (client == server) fail(ERROR_BAD_PATHNAME);
    lstrcpyW(command, image);
    if (redirected(command)) fail(ERROR_BAD_PATHNAME);
    count = GetEnvironmentVariableW(L"LOCALAPPDATA", image, 32768);
    if (!count || count > 32700) fail(ERROR_BAD_PATHNAME);
    lstrcatW(image, client ? L"\\CodexRelayClient\\installer\\unins000.exe" : L"\\CodexRelayServer\\installer\\unins000.exe");
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
