#!/bin/sh
# Install v1.0.0 from the official RTranslate release; accepts a local archive for offline installs.
set -eu
version=v1.0.0
archive= sha= project= host=shared
while [ "$#" -gt 0 ]; do
    case "$1" in
        --host) host=$2; shift 2 ;;
        --project) project=$2; shift 2 ;;
        --archive) archive=$2; shift 2 ;;
        --sha256) sha=$2; shift 2 ;;
        *) echo "Unknown RTranslate install option: $1" >&2; exit 2 ;;
    esac
done
case "$host" in shared|workbuddy) ;; *) echo 'Use --host shared or --host workbuddy.' >&2; exit 2 ;; esac
umask 077
scratch=$(mktemp -d "${TMPDIR:-/tmp}/rtranslate-install.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
if [ -z "$archive" ]; then
    release="https://github.com/irave7/RTranslate/releases/download/$version"
    archive="$scratch/rtranslate.zip"
    curl -fsSL --retry 2 --proto '=https' "$release/rtranslate.zip" -o "$archive"
    curl -fsSL --retry 2 --proto '=https' "$release/SHA256SUMS.txt" -o "$scratch/SHA256SUMS.txt"
    sha=$(awk '$2 == "rtranslate.zip" {print $1}' "$scratch/SHA256SUMS.txt")
fi
actual=$(shasum -a 256 "$archive" | cut -c1-64)
[ "$actual" = "$sha" ] || { echo 'RTranslate ZIP checksum mismatch.' >&2; exit 1; }
mkdir "$scratch/skill"
unzip -q "$archive" -d "$scratch/skill"
set -- --install-skill --host "$host"
if [ -n "$project" ]; then set -- "$@" --project "$project"; fi
/bin/sh "$scratch/skill/scripts/rtranslate.sh" "$@"
