target "docker-metadata-action" {}

variable "APP" {
  default = "headphones"
}

variable "VERSION" {
  // renovate: datasource=github-releases depName=rembo10/headphones
  default = "v0.6.4"
}

variable "SOURCE" {
  default = "https://github.com/rembo10/headphones"
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
