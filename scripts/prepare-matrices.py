#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
from os.path import isfile
from subprocess import check_output

import requests
import yaml

repo_owner = os.environ.get('REPO_OWNER')

TESTABLE_PLATFORMS = ["linux/amd64"]

def load_metadata_file_yaml(file_path):
    print(f"Loading YAML metadata from: {file_path}")
    with open(file_path, "r") as f:
        return yaml.safe_load(f)

def load_metadata_file_json(file_path):
    print(f"Loading JSON metadata from: {file_path}")
    with open(file_path, "r") as f:
        return json.load(f)

def get_latest_version_py(latest_py_path, channel_name):
    print(f"Getting latest version from Python script: {latest_py_path}")
    spec = importlib.util.spec_from_file_location("latest", latest_py_path)
    latest = importlib.util.module_from_spec(spec)
    sys.modules["latest"] = latest
    spec.loader.exec_module(latest)
    version = latest.get_latest(channel_name)
    print(f"Latest version from Python script: {version}")
    return version

def get_latest_version_sh(latest_sh_path, channel_name):
    print(f"Getting latest version from shell script: {latest_sh_path}")
    out = check_output([latest_sh_path, channel_name])
    version = out.decode("utf-8").strip()
    print(f"Latest version from shell script: {version}")
    return version

def get_latest_version(subdir, channel_name):
    print(f"\nGetting latest version for channel: {channel_name}")
    ci_dir = os.path.join(subdir, "ci")

    # Try each possible location for version scripts
    if os.path.isfile(os.path.join(ci_dir, "latest.py")):
        return get_latest_version_py(os.path.join(ci_dir, "latest.py"), channel_name)
    elif os.path.isfile(os.path.join(ci_dir, "latest.sh")):
        return get_latest_version_sh(os.path.join(ci_dir, "latest.sh"), channel_name)
    elif os.path.isfile(os.path.join(subdir, channel_name, "latest.py")):
        return get_latest_version_py(os.path.join(subdir, channel_name, "latest.py"), channel_name)
    elif os.path.isfile(os.path.join(subdir, channel_name, "latest.sh")):
        return get_latest_version_sh(os.path.join(subdir, channel_name, "latest.sh"), channel_name)

    print("No version script found")
    return None

def get_published_version(image_name):
    print(f"\nChecking published version for image: {image_name}")
    try:
        r = requests.get(
            f"https://quay.io/api/v1/repository/{repo_owner}/{image_name}/tag/",
            params={"limit": 100},
            headers={"Content-Type": "application/json"}
        )

        print(f"Quay.io API Status Code: {r.status_code}")

        if r.status_code != 200:
            print(f"Failed to get tags, status code: {r.status_code}")
            return None

        data = r.json()
        # Get unique tags
        tags = list(set(tag['name'] for tag in data.get('tags', [])))
        print(f"Found unique tags: {tags}")

        # Filter for version tags and remove 'rolling'
        version_tags = [tag for tag in tags
                       if tag != "rolling"
                       and all(part.isdigit() for part in tag.split("."))]
        print(f"Filtered version tags: {version_tags}")

        if version_tags:
            # Use semantic versioning comparison
            result = max(version_tags, key=lambda x: [int(i) for i in x.split(".")])
            print(f"Selected published version: {result}")
            return result

        print("No valid version tags found")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Request error: {str(e)}")
        return None

def get_image_metadata(subdir, meta, forRelease=False, force=False, channels=None):
    print(f"\nProcessing metadata:")
    print(f"Subdir: {subdir}")
    print(f"Force build: {force}")
    print(f"For release: {forRelease}")

    imagesToBuild = {
        "images": [],
        "imagePlatforms": []
    }

    if channels is None:
        channels = meta["channels"]
    else:
        channels = [channel for channel in meta["channels"] if channel["name"] in channels]

    print(f"Processing channels: {[c['name'] for c in channels]}")

    for channel in channels:
        print(f"\nProcessing channel: {channel['name']}")

        version = get_latest_version(subdir, channel["name"])
        if version is None:
            print(f"Skipping channel {channel['name']}: No version found")
            continue

        # Image Name
        toBuild = {}
        if channel.get("stable", False):
            toBuild["name"] = meta["app"]
        else:
            toBuild["name"] = "-".join([meta["app"], channel["name"]])

        print(f"Processing image: {toBuild['name']}")
        print(f"Latest version: {version}")

        # Skip if latest version already published
        if not force:
            published = get_published_version(toBuild["name"])
            print(f"Version comparison:")
            print(f"- Published version: {published}")
            print(f"- Latest version: {version}")

            if published is not None and published == version:
                print(f"Skipping build - version {version} already published")
                continue

            print(f"Will build - versions don't match or no published version found")
            toBuild["published_version"] = published

        toBuild["version"] = version

        # Image Tags
        toBuild["tags"] = ["rolling", version]
        if meta.get("semver", False):
            parts = version.split(".")[:-1]
            while len(parts) > 0:
                toBuild["tags"].append(".".join(parts))
                parts = parts[:-1]

        # Platform Metadata
        for platform in channel["platforms"]:
            if platform not in TESTABLE_PLATFORMS and not forRelease:
                continue

            toBuild.setdefault("platforms", []).append(platform)

            target_os = platform.split("/")[0]
            target_arch = platform.split("/")[1]

            platformToBuild = {}
            platformToBuild["name"] = toBuild["name"]
            platformToBuild["platform"] = platform
            platformToBuild["target_os"] = target_os
            platformToBuild["target_arch"] = target_arch
            platformToBuild["version"] = version
            platformToBuild["channel"] = channel["name"]
            platformToBuild["label_type"] = "org.opencontainers.image"

            if isfile(os.path.join(subdir, channel["name"], "Dockerfile")):
                platformToBuild["dockerfile"] = os.path.join(subdir, channel["name"], "Dockerfile")
                platformToBuild["context"] = os.path.join(subdir, channel["name"])
                platformToBuild["goss_config"] = os.path.join(subdir, channel["name"], "goss.yaml")
            else:
                platformToBuild["dockerfile"] = os.path.join(subdir, "Dockerfile")
                platformToBuild["context"] = subdir
                platformToBuild["goss_config"] = os.path.join(subdir, "ci", "goss.yaml")

            platformToBuild["goss_args"] = "tail -f /dev/null" if channel["tests"].get("type", "web") == "cli" else ""

            platformToBuild["tests_enabled"] = channel["tests"]["enabled"] and platform in TESTABLE_PLATFORMS

            imagesToBuild["imagePlatforms"].append(platformToBuild)

        print(f"Adding image to build list: {toBuild['name']}")
        imagesToBuild["images"].append(toBuild)

    print("\nFinal build list:")
    print(f"Images: {len(imagesToBuild['images'])}")
    print(f"Platforms: {len(imagesToBuild['imagePlatforms'])}")
    return imagesToBuild

if __name__ == "__main__":
    print("\nScript started")
    print(f"Arguments: {sys.argv}")

    apps = sys.argv[1]
    forRelease = sys.argv[2] == "true"
    force = sys.argv[3] == "true"
    imagesToBuild = {
        "images": [],
        "imagePlatforms": []
    }

    if apps != "all":
        channels = None
        apps = apps.split(",")
        if len(sys.argv) == 5:
            channels = sys.argv[4].split(",")

        print(f"Processing specific apps: {apps}")
        print(f"Channels: {channels}")

        for app in apps:
            if not os.path.exists(os.path.join("./apps", app)):
                print(f"App \"{app}\" not found")
                exit(1)

            meta = None
            if os.path.isfile(os.path.join("./apps", app, "metadata.yaml")):
                meta = load_metadata_file_yaml(os.path.join("./apps", app, "metadata.yaml"))
            elif os.path.isfile(os.path.join("./apps", app, "metadata.json")):
                meta = load_metadata_file_json(os.path.join("./apps", app, "metadata.json"))

            imageToBuild = get_image_metadata(os.path.join("./apps", app), meta, forRelease, force=force, channels=channels)
            if imageToBuild is not None:
                imagesToBuild["images"].extend(imageToBuild["images"])
                imagesToBuild["imagePlatforms"].extend(imageToBuild["imagePlatforms"])
    else:
        print("Processing all apps")
        for subdir, dirs, files in os.walk("./apps"):
            for file in files:
                meta = None
                if file == "metadata.yaml":
                    meta = load_metadata_file_yaml(os.path.join(subdir, file))
                elif file == "metadata.json":
                    meta = load_metadata_file_json(os.path.join(subdir, file))
                else:
                    continue
                if meta is not None:
                    imageToBuild = get_image_metadata(subdir, meta, forRelease, force=force)
                    if imageToBuild is not None:
                        imagesToBuild["images"].extend(imageToBuild["images"])
                        imagesToBuild["imagePlatforms"].extend(imageToBuild["imagePlatforms"])

    print("\nFinal output:")
    print(json.dumps(imagesToBuild))
