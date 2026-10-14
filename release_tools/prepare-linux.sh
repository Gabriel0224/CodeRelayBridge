#!/bin/bash
# Only distribution system libraries: no Python/pip, extra repos or App configs.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
role=${RELAY_PRODUCT:-}
if [[ -z "$role" ]]; then
    if [[ -f "$root/RelayClient/RelayClient" ]]; then role=RelayClient; else role=RelayServer; fi
fi
case "$role" in RelayClient|RelayServer) ;; *) echo '未知程序类型。' >&2; exit 1;; esac
if [[ $(uname -m) != x86_64 ]]; then
    echo '此包仅支持 Linux x86_64；不能在 ARM64 上安装。' >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]]; then echo '无法确认发行版，未修改系统。' >&2; exit 1; fi
source /etc/os-release
case " $ID ${ID_LIKE:-} " in
    *ubuntu*) ;;
    *) echo '补齐脚本仅适用于 Ubuntu 及基于 Ubuntu 的 Mint。' >&2; exit 1;;
esac
if ! command -v ldd >/dev/null; then echo '系统缺少 ldd，无法安全核验依赖。' >&2; exit 1; fi
export LD_LIBRARY_PATH="$root/$role/_internal:$root/$role/_internal/PySide6/Qt/lib"
missing=0
for file in "$root/$role/$role" \
    "$root/$role"/_internal/libpython*.so* \
    "$root/$role/_internal/PySide6/Qt/lib/libQt6Gui.so.6" \
    "$root/$role/_internal/PySide6/Qt/plugins/platforms/libqxcb.so" \
    "$root/$role"/_internal/PySide6/Qt/plugins/platforms/libqwayland*.so; do
    [[ -f "$file" ]] || continue
    result=$(ldd "$file" 2>&1 || true)
    if [[ "$result" == *'not found'* || "$result" == *'version '*'not found'* ]]; then
        printf '%s\n%s\n' "依赖检查：$(basename -- "$file")" "$result"
        missing=1
    fi
done
if [[ "$missing" == 0 ]]; then echo '系统依赖检查通过；Python 和 Qt 已随包提供。'; exit 0; fi
if [[ ${1:-} != --install ]]; then
    echo '缺少系统库。可执行：bash prepare-linux.sh --install（联网，需要 sudo）。' >&2
    exit 2
fi
if [[ ${EUID:-$(id -u)} == 0 ]]; then
    echo '请用普通用户运行此入口；脚本会仅在安装系统库时请求 sudo。' >&2
    exit 1
fi
command -v sudo >/dev/null || { echo '系统没有 sudo，请联系系统管理员。' >&2; exit 1; }
command -v apt-get >/dev/null || { echo '没有 apt-get，未修改系统。' >&2; exit 1; }
# Do not expose bundled library search paths to apt/sudo or desktop programs.
unset LD_LIBRARY_PATH
sudo apt-get update
sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 \
    libxcb-xkb1 libxkbcommon-x11-0 libgl1 libegl1 libfontconfig1 libfreetype6 \
    libdbus-1-3 libwayland-client0 libwayland-cursor0 libwayland-egl1 \
    ca-certificates fonts-noto-cjk
exec /bin/bash "$root/prepare-linux.sh"
