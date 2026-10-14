#!/bin/bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -f "$root/RelayClient/RelayClient" ]]; then
    product=RelayClient
else
    product=RelayServer
fi
if [[ ${EUID:-$(id -u)} == 0 ]]; then
    echo '请使用登录桌面的普通用户打开 App，不要用 sudo 运行 App。' >&2
    exit 1
fi
code=0
/bin/bash "$root/prepare-linux.sh" || code=$?
if [[ "$code" == 2 ]]; then
    read -r -p '是否联网从本机发行版软件源补齐系统库？[y/N] ' answer
    case "$answer" in y|Y) /bin/bash "$root/prepare-linux.sh" --install;; *) exit 2;; esac
elif [[ "$code" != 0 ]]; then
    exit "$code"
fi
exec "$root/$product/$product"
