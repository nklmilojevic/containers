package main

import (
	"context"
	"testing"

	"github.com/home-operations/containers/testhelpers"
)

func Test(t *testing.T) {
	ctx := context.Background()
	image := testhelpers.GetTestImage("quay.io/home-operations/irqbalance:rolling")
	testhelpers.TestFileExists(t, ctx, image, "/usr/sbin/irqbalance", nil)
}
