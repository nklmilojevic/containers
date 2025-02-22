#!/usr/bin/env python3
import importlib.util
import json
import logging
import os
import sys
from os.path import isfile
from subprocess import check_output

import requests
import yaml

# Configure logging to write to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    stream=sys.stderr
)

repo_owner = os.environ.get('REPO_OWNER')
TESTABLE_PLATFORMS = ["linux/amd64"]

def load_metadata_file_yaml(file_path):
    logging.info(f"Loading YAML metadata from: {file_path}")
    with open(file_path, "r") as f:
        return yaml.safe_load(f)

def load_metadata_file_json(file_path):
    logging.info(f"Loading JSON metadata from: {file_path}")
    with open(file_path, "r") as f:
        return json.load(f)

def get_latest_version_py(latest_py_path, channel_name):
    logging.info(f"Getting latest version from Python script: {latest_py_path}")
    spec = importlib.util.spec_from_file_location("latest", latest_py_path)
    latest = importlib.util.module_from_spec(spec)
    sys.modules["latest"] = latest
    spec.loader.exec_module(latest)
    version = latest.get_latest(channel_name)
    logging.info(f"Latest version from Python script: {version}")
    return version

def get_latest_version_sh(latest_sh_path, channel_name):
    logging.info(f"Getting latest version from shell script: {latest_sh_path}")
    out = check_output([latest_sh_path, channel_name])
    version = out.decode("utf-8").strip()
    logging.info(f"Latest version from shell script: {version}")
    return version

def get_latest_version(subdir, channel_name):
    logging.info(f"\nGetting latest version for channel: {channel_name}")
    ci_dir = os.path.join(subdir, "ci")

    if os.path.isfile(os.path.join(ci_dir, "latest.py")):
        return get_latest_version_py(os.path.join(ci_dir, "latest.py"), channel_name)
    elif os.path.isfile(os.path.join(ci_dir, "latest.sh")):
        return get_latest_version_sh(os.path.join(ci_dir, "latest.sh"), channel_name)
    elif os.path.isfile(os.path.join(subdir, channel_name, "latest.py")):
        return get_latest_version_py(os.path.join(subdir, channel_name, "latest.py"), channel_name)
    elif os.path.isfile(os.path.join(subdir, channel_name, "latest.sh")):
        return get_latest_version_sh(os.path.join(subdir, channel_name, "latest.sh"), channel_name)

    logging.info("No version script found")
    return None

def get_published_version(image_name):
    logging.info(f"\nChecking published version for image: {image_name}")
    try:
        r = requests.get(
            f"https://quay.io/api/v1/repository/{repo_owner}/{image_name}/tag/",
            params={"limit": 100},
            headers={"Content-Type": "application/json"}
        )

        if r.status_code != 200:
            logging.error(f"Failed to get tags, status code: {r.status_code}")
            return None

        data = r.json()
        # Get all non-rolling tags
        tags = [tag['name'] for tag in data.get('tags', []) if tag['name'] != 'rolling']
        logging.info(f"Found tags: {tags}")

        # Get the longest matching version (to avoid partial matches like '5.0' when '5.0.4' exists)
        matching_versions = sorted(tags, key=len, reverse=True)

        if matching_versions:
            result = matching_versions[0]
            logging.info(f"Selected published version: {result}")
            return result

        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Request error: {str(e)}")
        return None



def get_image_metadata(subdir, meta, forRelease=False, force=False, channels=None):
    logging.info(f"\nProcessing metadata:")
    logging.info(f"Subdir: {subdir}")
    logging.info(f"Force build: {force}")
    logging.info(f"For release: {forRelease}")

    imagesToBuild = {
        "images": [],
        "imagePlatforms": []
    }

    if channels is None:
        channels = meta["channels"]
    else:
        channels = [channel for channel in meta["channels"] if channel["name"] in channels]

    logging.info(f"Processing channels: {[c['name'] for c in channels]}")

    for channel in channels:
        logging.info(f"\nProcessing channel: {channel['name']}")

        version = get_latest_version(subdir, channel["name"])
        if version is None:
            logging.info(f"Skipping channel {channel['name']}: No version found")
            continue

        # Image Name
        toBuild = {}
        if channel.get("stable", False):
            toBuild["name"] = meta["app"]
        else:
            toBuild["name"] = "-".join([meta["app"], channel["name"]])

        logging.info(f"Processing image: {toBuild['name']}")
        logging.info(f"Latest version: {version}")

        # Skip if latest version already published
        if not force:
            published = get_published_version(toBuild["name"])
            logging.info(f"Version comparison:")
            logging.info(f"- Published version: {published}")
            logging.info(f"- Latest version: {version}")

            if published is not None and published == version:
                logging.info(f"Skipping build - version {version} already published")
                continue

            logging.info(f"Will build - versions don't match or no published version found")
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

        logging.info(f"Adding image to build list: {toBuild['name']}")
        imagesToBuild["images"].append(toBuild)

    logging.info("\nFinal build list:")
    logging.info(f"Images: {len(imagesToBuild['images'])}")
    logging.info(f"Platforms: {len(imagesToBuild['imagePlatforms'])}")
    return imagesToBuild

if __name__ == "__main__":
    logging.info("Script started")
    logging.info(f"Arguments: {sys.argv}")

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

        logging.info(f"Processing specific apps: {apps}")
        logging.info(f"Channels: {channels}")

        for app in apps:
            if not os.path.exists(os.path.join("./apps", app)):
                logging.error(f"App \"{app}\" not found")
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
        logging.info("Processing all apps")
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

    # Only print the JSON output to stdout
    print(json.dumps(imagesToBuild))
