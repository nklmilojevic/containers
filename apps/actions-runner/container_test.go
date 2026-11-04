package main

import (
	"testing"

	helpers "github.com/home-operations/containers/tests"
)

func Test(t *testing.T) {
	image := helpers.GetTestImage("quay.io/nklmilojevic/actions-runner:rolling")
	helpers.RequireFileExists(t, image, "/usr/local/bin/yq")
}
