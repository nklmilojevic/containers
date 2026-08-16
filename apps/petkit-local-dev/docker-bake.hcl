target "docker-metadata-action" {}

variable "APP" {
  default = "petkit-local-dev"
}

variable "VERSION" {
  // Bumped by hand every time addon/ changes — VERSION is the image tag, not
  // an upstream version. Renovate deliberately does not touch it; the source
  // is vendored, not fetched.
  default = "v2.1.0-nkl.1"
}

variable "SOURCE" {
  default = "https://github.com/nklmilojevic/containers/tree/main/apps/petkit-local-dev"
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
