target "docker-metadata-action" {}

variable "APP" {
  default = "hms-cpap"
}

variable "VERSION" {
  // Pinned by hand — renovate intentionally does NOT manage this. The fork
  // still carries upstream's plain `v4.9.9` tag, and semver ranks 4.9.9 ABOVE
  // the 4.9.9-nkl.N fork prereleases, so a renovate rule here "upgrades" us
  // straight back to unpatched upstream. Bump this by hand when cutting a new
  // fork tag; drop the fork entirely once the fixes land upstream.
  default = "v4.9.9-nkl.2"
}

variable "SOURCE" {
  default = "https://github.com/nklmilojevic/hms-cpap"
}

group "default" {
  targets = ["image-local"]
}

target "image" {
  inherits = ["docker-metadata-action"]
  args = {
    VERSION = "${VERSION}"
  }
  labels = {
    "org.opencontainers.image.source" = "${SOURCE}"
  }
}

target "image-local" {
  inherits = ["image"]
  output = ["type=docker"]
  tags = ["${APP}:${VERSION}"]
}

target "image-all" {
  inherits = ["image"]
  platforms = [
    "linux/amd64",
    "linux/arm64"
  ]
}
