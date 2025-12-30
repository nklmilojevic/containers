package main

import (
	"context"
	"testing"

	"github.com/home-operations/containers/testhelpers"
)

func Test(t *testing.T) {
	ctx := context.Background()
	image := testhelpers.GetTestImage("quay.io/home-operations/tqm:rolling")
	testhelpers.TestCommandSucceeds(t, ctx, image, nil, "/app/bin/tqm", "version")
}
