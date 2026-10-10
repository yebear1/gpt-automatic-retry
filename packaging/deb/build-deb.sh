#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/../.." && pwd)
version=$(sed -n 's/^Version: //p' "$script_dir/control")
package_name="gpt-automatic-retry_${version}_all.deb"
build_dir=$(mktemp -d)
package_root="$build_dir/package"

cleanup() {
  rm -rf -- "$build_dir"
}
trap cleanup EXIT INT TERM

install -Dm644 "$script_dir/control" "$package_root/DEBIAN/control"
install -Dm755 "$script_dir/gpt-automatic-retry" "$package_root/usr/bin/gpt-automatic-retry"
install -Dm755 "$script_dir/gpt-automatic-retry-gui" "$package_root/usr/bin/gpt-automatic-retry-gui"
install -Dm755 "$project_dir/gpt_keepalive.py" "$package_root/usr/lib/gpt-automatic-retry/gpt_keepalive.py"
install -Dm755 "$project_dir/gpt_keepalive_gui.py" "$package_root/usr/lib/gpt-automatic-retry/gpt_keepalive_gui.py"
install -Dm644 "$script_dir/gpt-automatic-retry.service" \
  "$package_root/usr/lib/systemd/user/gpt-automatic-retry.service"
install -Dm644 "$script_dir/gpt-automatic-retry.desktop" \
  "$package_root/usr/share/applications/gpt-automatic-retry.desktop"
install -Dm644 "$script_dir/gpt-automatic-retry.svg" \
  "$package_root/usr/share/icons/hicolor/scalable/apps/gpt-automatic-retry.svg"
install -Dm644 "$project_dir/README.md" \
  "$package_root/usr/share/doc/gpt-automatic-retry/README.md"

mkdir -p "$project_dir/dist"
dpkg-deb --build --root-owner-group "$package_root" "$project_dir/dist/$package_name"
(cd "$project_dir/dist" && sha256sum "$package_name" > SHA256SUMS)
printf '%s\n' "$project_dir/dist/$package_name"
