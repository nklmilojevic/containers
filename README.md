<!---
NOTE: AUTO-GENERATED FILE
to edit this file, instead edit its template at: ./scripts/templates/README.md.j2
-->
<div align="center">


## Containers

_An opinionated collection of container images_

</div>

<div align="center">

![GitHub Repo stars](https://img.shields.io/github/stars/nklmilojevic/containers?style=for-the-badge)
![GitHub forks](https://img.shields.io/github/forks/nklmilojevic/containers?style=for-the-badge)
![GitHub Workflow Status (with event)](https://img.shields.io/github/actions/workflow/status/nklmilojevic/containers/release-scheduled.yaml?style=for-the-badge&label=Scheduled%20Release)

</div>

Welcome to my container images, if looking for a container start by [browsing the GitHub Packages page for this repo's packages](https://github.com/nklmilojevic?tab=packages&repo_name=containers).

## Mission statement

The goal of this project is to support [semantically versioned](https://semver.org/), [rootless](https://rootlesscontaine.rs/), and [multiple architecture](https://www.docker.com/blog/multi-arch-build-and-images-the-simple-way/) containers for various applications.

It also adheres to a [KISS principle](https://en.wikipedia.org/wiki/KISS_principle), logging to stdout, [one process per container](https://testdriven.io/tips/59de3279-4a2d-4556-9cd0-b444249ed31e/), no [s6-overlay](https://github.com/just-containers/s6-overlay) and all images are built on top of [Alpine](https://hub.docker.com/_/alpine) or [Ubuntu](https://hub.docker.com/_/ubuntu).

## Tag immutability

The containers built here do not use immutable tags, as least not in the more common way you have seen from [linuxserver.io](https://fleet.linuxserver.io/) or [Bitnami](https://bitnami.com/stacks/containers).

We do take a similar approach but instead of appending a `-ls69` or `-r420` prefix to the tag we instead insist on pinning to the sha256 digest of the image, while this is not as pretty it is just as functional in making the images immutable.

| Container                                          | Immutable |
|----------------------------------------------------|-----------|
| `docker.io/nklmilojevic/sonarr:rolling`                   | ❌         |
| `docker.io/nklmilojevic/sonarr:3.0.8.1507`                | ❌         |
| `docker.io/nklmilojevic/sonarr:rolling@sha256:8053...`    | ✅         |
| `docker.io/nklmilojevic/sonarr:3.0.8.1507@sha256:8053...` | ✅         |

_If pinning an image to the sha256 digest, tools like [Renovate](https://github.com/renovatebot/renovate) support updating the container on a digest or application version change._

## Rootless

To run these containers as non-root make sure you update your configuration to the user and group you want.

### Docker compose

```yaml
networks:
  sonarr:
    name: sonarr
    external: true
services:
  sonarr:
    image: docker.io/nklmilojevic/sonarr:3.0.8.1507
    container_name: sonarr
    user: 65534:65534
    # ...
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sonarr
# ...
spec:
  # ...
  template:
    # ...
    spec:
      # ...
      securityContext:
        runAsUser: 65534
        runAsGroup: 65534
        fsGroup: 65534
        fsGroupChangePolicy: OnRootMismatch
# ...
```

## Passing arguments to a application

Some applications do not support defining configuration via environment variables and instead only allow certain config to be set in the command line arguments for the app. To circumvent this, for applications that have an `entrypoint.sh` read below.

1. First read the Kubernetes docs on [defining command and arguments for a Container](https://kubernetes.io/docs/tasks/inject-data-application/define-command-argument-container/).
2. Look up the documentation for the application and find a argument you would like to set.
3. Set the extra arguments in the `args` section like below.

    ```yaml
    args:
      - --port
      - "8080"
    ```

## Configuration volume

For applications that need to have persistent configuration data the config volume is hardcoded to `/config` inside the container. This is not able to be changed in most cases.

## Available Images

Each Image will be built with a `rolling` tag, along with tags specific to it's version. Available Images Below

Container | Channel | Image
--- | --- | ---
[actions-runner](https://hub.docker.com/r/nklmilojevic/actions-runner) | stable | docker.io/nklmilojevic/actions-runner
[bazarr](https://hub.docker.com/r/nklmilojevic/bazarr) | stable | docker.io/nklmilojevic/bazarr
[esphome](https://hub.docker.com/r/nklmilojevic/esphome) | stable | docker.io/nklmilojevic/esphome
[home-assistant](https://hub.docker.com/r/nklmilojevic/home-assistant) | stable | docker.io/nklmilojevic/home-assistant
[jbops](https://hub.docker.com/r/nklmilojevic/jbops) | stable | docker.io/nklmilojevic/jbops
[lidarr](https://hub.docker.com/r/nklmilojevic/lidarr) | master | docker.io/nklmilojevic/lidarr
[lidarr-develop](https://hub.docker.com/r/nklmilojevic/lidarr-develop) | develop | docker.io/nklmilojevic/lidarr-develop
[lidarr-nightly](https://hub.docker.com/r/nklmilojevic/lidarr-nightly) | nightly | docker.io/nklmilojevic/lidarr-nightly
[plex](https://hub.docker.com/r/nklmilojevic/plex) | stable | docker.io/nklmilojevic/plex
[plex-beta](https://hub.docker.com/r/nklmilojevic/plex-beta) | beta | docker.io/nklmilojevic/plex-beta
[postgres-init](https://hub.docker.com/r/nklmilojevic/postgres-init) | stable | docker.io/nklmilojevic/postgres-init
[prowlarr](https://hub.docker.com/r/nklmilojevic/prowlarr) | master | docker.io/nklmilojevic/prowlarr
[prowlarr-develop](https://hub.docker.com/r/nklmilojevic/prowlarr-develop) | develop | docker.io/nklmilojevic/prowlarr-develop
[prowlarr-nightly](https://hub.docker.com/r/nklmilojevic/prowlarr-nightly) | nightly | docker.io/nklmilojevic/prowlarr-nightly
[qbittorrent](https://hub.docker.com/r/nklmilojevic/qbittorrent) | stable | docker.io/nklmilojevic/qbittorrent
[qbittorrent-beta](https://hub.docker.com/r/nklmilojevic/qbittorrent-beta) | beta | docker.io/nklmilojevic/qbittorrent-beta
[radarr](https://hub.docker.com/r/nklmilojevic/radarr) | master | docker.io/nklmilojevic/radarr
[radarr-develop](https://hub.docker.com/r/nklmilojevic/radarr-develop) | develop | docker.io/nklmilojevic/radarr-develop
[radarr-nightly](https://hub.docker.com/r/nklmilojevic/radarr-nightly) | nightly | docker.io/nklmilojevic/radarr-nightly
[readarr-develop](https://hub.docker.com/r/nklmilojevic/readarr-develop) | develop | docker.io/nklmilojevic/readarr-develop
[readarr-nightly](https://hub.docker.com/r/nklmilojevic/readarr-nightly) | nightly | docker.io/nklmilojevic/readarr-nightly
[sabnzbd](https://hub.docker.com/r/nklmilojevic/sabnzbd) | stable | docker.io/nklmilojevic/sabnzbd
[sonarr](https://hub.docker.com/r/nklmilojevic/sonarr) | main | docker.io/nklmilojevic/sonarr
[sonarr-develop](https://hub.docker.com/r/nklmilojevic/sonarr-develop) | develop | docker.io/nklmilojevic/sonarr-develop
[tautulli](https://hub.docker.com/r/nklmilojevic/tautulli) | master | docker.io/nklmilojevic/tautulli
[volsync](https://hub.docker.com/r/nklmilojevic/volsync) | stable | docker.io/nklmilojevic/volsync


## Deprecations

Containers here can be **deprecated** at any point, this could be for any reason described below.

1. The upstream application is **no longer actively developed**
2. The upstream application has an **official upstream container** that follows closely to the mission statement described here
3. The upstream application has been **replaced with a better alternative**
4. The **maintenance burden** of keeping the container here **is too bothersome**

**Note**: Deprecated containers will remained published to this repo for 6 months after which they will be pruned.

## Credits

A lot of inspiration and ideas are thanks to the hard work of [hotio.dev](https://hotio.dev/) and [linuxserver.io](https://www.linuxserver.io/) contributors.