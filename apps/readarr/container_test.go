package main

import (
	"testing"

	helpers "github.com/home-operations/containers/tests"
)

func Test(t *testing.T) {
	image := helpers.GetTestImage("quay.io/nklmilojevic/readarr:rolling")
	helpers.RequireHTTPEndpoint(t, image, helpers.HTTPTestConfig{Port: "8787"}, nil)
}
