#!/bin/sh
set -eu
scripts=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd)
cache=${RTRANSLATE_RUNTIME_CACHE:-"$HOME/Library/Caches/RTranslate/pdf"}
for runner in "$cache"/engine-*/bin/python; do
    if [ -x "$runner" ] && [ -f "$(dirname "$(dirname "$runner")")/.installed" ]; then
        exec "$runner" -I -B -X utf8 "$scripts/rtranslate.py" "$@"
    fi
done
# /usr/bin/python3 is an installer stub on a stock Mac without Command Line Tools.
runner=$(command -v python3 || true)
if [ "$runner" = /usr/bin/python3 ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then runner=; fi
if [ "${RTRANSLATE_MANAGED_BOOTSTRAP:-0}" != 1 ] && [ -n "$runner" ] && "$runner" -I -c 'import sys;sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    exec "$runner" -I -B -X utf8 "$scripts/rtranslate.py" "$@"
fi
case $(uname -sm) in
    'Darwin arm64')
        wheel=uv-0.13.9-py3-none-macosx_11_0_arm64.whl
        directory=f3/a7/a8d7b074798c7fb3b26b37584b8fe2e29df2ccdc24ce7902caab2a53edbf
        sha=66c512266d633bef48384a1f53003f41f2a8e7b36fe6e7fbfd3e0fd52d96fa63 ;;
    'Darwin x86_64')
        wheel=uv-0.13.9-py3-none-macosx_10_12_x86_64.whl
        directory=1e/db/08e9ef4879bd667e6d7b4705e594e5bd3e4585f10fc63cb16b9fa2fac63e
        sha=c16ed11e013557536b3e60065f1b912f6407bd6518e041b961e23ef9e6b4fbf5 ;;
    *) echo 'RTranslate supports macOS and Windows x64.' >&2; exit 1 ;;
esac
umask 077
mkdir -p "$cache/assets"
uv="$cache/bootstrap-uv-$sha"
if [ ! -x "$uv" ]; then
    pending=$(mktemp -d "$cache/.rtranslate-start.XXXXXX")
    trap 'rm -rf -- "$pending"' EXIT
    /usr/bin/curl -fLsS --retry 2 --proto '=https' "https://files.pythonhosted.org/packages/$directory/$wheel" -o "$pending/$wheel"
    actual=$(/usr/bin/shasum -a 256 "$pending/$wheel" | cut -c1-64)
    [ "$sha" = "$actual" ] || { echo 'RTranslate bootstrap checksum mismatch.' >&2; exit 1; }
    /usr/bin/unzip -p "$pending/$wheel" '*/scripts/uv' > "$pending/uv"
    chmod 700 "$pending/uv"
    mv -f -- "$pending/$wheel" "$cache/assets/$wheel"
    mv -f -- "$pending/uv" "$uv"
    rmdir "$pending"
    trap - EXIT
fi
export UV_PYTHON_INSTALL_DIR="$cache/python" UV_CACHE_DIR="$cache/uv-cache" UV_NO_CONFIG=1 UV_SYSTEM_CERTS=true
"$uv" python install --no-bin --no-registry 3.12.16 >&2
runner=$("$uv" python find --managed-python --no-python-downloads 3.12.16)
exec "$runner" -I -B -X utf8 "$scripts/rtranslate.py" "$@"
