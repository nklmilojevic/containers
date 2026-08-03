target "docker-metadata-action" {}

variable "APP" {
  default = "petkit-local"
}

variable "VERSION" {
  // renovate: datasource=github-tags depName=nklmilojevic/petkit-local
  default = "v1.6.0-nkl.2"
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
