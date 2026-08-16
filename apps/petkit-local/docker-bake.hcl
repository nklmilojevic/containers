target "docker-metadata-action" {}

variable "APP" {
  default = "petkit-local"
}

variable "VERSION" {
  // renovate: datasource=github-releases depName=alex-so-3/petkit-local
  default = "v2.1.0"
}

variable "SOURCE" {
  default = "https://github.com/alex-so-3/petkit-local"
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
