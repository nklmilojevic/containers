target "docker-metadata-action" {}

variable "APP" {
  default = "cobalt-web"
}

variable "VERSION" {
  // renovate: datasource=docker depName=ghcr.io/imputnet/cobalt
  default = "11.7.1"
}

variable "SOURCE" {
  default = "https://github.com/imputnet/cobalt"
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
