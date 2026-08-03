target "docker-metadata-action" {}

variable "APP" {
  default = "petkit-local"
}

variable "VERSION" {
  // Pinned by hand — renovate intentionally does NOT manage this. The fork
  // still carries upstream's plain `v1.6.0` tag, and semver ranks 1.6.0 ABOVE
  // the 1.6.0-nkl.N fork prereleases, so a renovate rule here "upgrades" us
  // straight back to unpatched upstream (it did, once). Bump this by hand when
  // cutting a new fork tag; drop the fork entirely once the fixes land upstream.
  default = "v1.6.0-nkl.3"
}

variable "SOURCE" {
  default = "https://github.com/nklmilojevic/petkit-local"
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
