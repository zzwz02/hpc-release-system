#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  printf '用法：%s <SDK目录> <输出.tsv>\n' "$0" >&2
  exit 2
fi

sdk_root=$1
manifest=$2

if [[ ! -d "$sdk_root" ]]; then
  printf 'SDK 路径不是目录：%s\n' "$sdk_root" >&2
  exit 2
fi

sdk_root=$(realpath "$sdk_root")
mkdir -p "$(dirname "$manifest")"
manifest=$(realpath -m "$manifest")

case "$manifest" in
  "$sdk_root"|"$sdk_root"/*)
    printf '输出文件必须位于被扫描目录之外：%s\n' "$manifest" >&2
    exit 2
    ;;
esac

scratch=$(mktemp "${manifest}.tmp.XXXXXX")
trap 'rm -f "$scratch"' EXIT

printf 'path\ttype\tbytes\tsha256\tsymlink\tbuild_id\tsoname\tneeded\n' > "$scratch"

while IFS= read -r -d '' item; do
  relative=${item#"$sdk_root"/}
  kind=file
  bytes=
  digest=
  link=
  build=
  soname=
  needed=

  if [[ -L "$item" ]]; then
    kind=symlink
    bytes=$(stat -c '%s' "$item")
    link=$(readlink "$item")
  else
    bytes=$(stat -c '%s' "$item")
    digest=$(sha256sum "$item")
    digest=${digest%% *}

    if command -v readelf >/dev/null 2>&1 && readelf -h "$item" >/dev/null 2>&1; then
      kind=elf
      build=$(readelf -n "$item" 2>/dev/null | awk '/Build ID:/ && !seen++ { print $3 }' || true)
      soname=$(readelf -d "$item" 2>/dev/null | awk '/\(SONAME\)/ && !seen++ { value=$NF; gsub(/^\[|\]$/, "", value); print value }' || true)
      needed=$(readelf -d "$item" 2>/dev/null | awk '/\(NEEDED\)/ { value=$NF; gsub(/^\[|\]$/, "", value); values=(values ? values "," : "") value } END { print values }' || true)
    fi
  fi

  relative=${relative//$'\t'/ }
  relative=${relative//$'\n'/ }
  link=${link//$'\t'/ }
  link=${link//$'\n'/ }

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$relative" "$kind" "$bytes" "$digest" "$link" "$build" "$soname" "$needed" >> "$scratch"
done < <(find "$sdk_root" \( -type f -o -type l \) -print0 | sort -z)

mv "$scratch" "$manifest"
trap - EXIT
printf '已写入 %s\n' "$manifest" >&2
