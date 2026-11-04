package main

import (
	"context"
	"testing"

	helpers "github.com/home-operations/containers/tests"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
)

func Test(t *testing.T) {
	ctx := context.Background()
	image := helpers.GetTestImage("quay.io/nklmilojevic/openssh:rolling")

	// sshd must come up rootless and accept connections on its unprivileged port.
	c, err := testcontainers.Run(ctx, image,
		testcontainers.WithEnv(map[string]string{
			"PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestkeytestkeytestkeytestkeytestkeytest test",
		}),
		testcontainers.WithExposedPorts("2222/tcp"),
		testcontainers.WithWaitStrategy(wait.ForListeningPort("2222/tcp")),
	)
	testcontainers.CleanupContainer(t, c)
	require.NoError(t, err)
}
