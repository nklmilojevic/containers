target "docker-metadata-action" {}

variable "APP" {
  default = "petkit-local"
}

variable "VERSION" {
  // Pinned by hand — renovate intentionally does NOT manage this. The fork
  // carries upstream's `v2.1.0` tag AS IS, and semver ranks 2.1.0 ABOVE the
  // 2.1.0-nkl.N fork prereleases, so a renovate rule here "upgrades" us
  // straight back to unpatched upstream. Bump by hand when cutting a new
  // fork tag; drop the fork entirely once alex-so-3#21 lands.
  // nkl.3 = v2.1.0 + K3 HTTP piggyback (#28) + D4H food-low derivation (#27)
  //         + T4 Times Used derived from visit events (#32).
  default = "v2.1.0-nkl.3"
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
