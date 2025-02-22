#!/usr/bin/env bash
version=$(curl -sL "https://github.com/userdocs/qbittorrent-nox-static/releases/latest/download/dependency-version.json" | jq -r '. | "release-\(.qbittorrent)_v\(.libtorrent_2_0)"' 2>/dev/null)
version="${version#*release-}"
version="${version%%_*}"

# Check if version file exists and compare versions
if [ -f ".version" ] && [ "$(cat .version)" = "${version}" ]; then
    exit 0
fi

# Update version file and output new version
echo "${version}" > .version
printf "%s" "${version}"