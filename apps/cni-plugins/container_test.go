package main

import (
	"context"
	"testing"

	"github.com/home-operations/containers/testhelpers"
)

func Test(t *testing.T) {
	ctx := context.Background()
	image := testhelpers.GetTestImage("quay.io/home-operations/cni-plugins:rolling")
	testhelpers.TestCommandSucceeds(t, ctx, image, nil, "/plugins/macvlan")
}
