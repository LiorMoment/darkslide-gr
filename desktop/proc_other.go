//go:build !windows

package main

import "os/exec"

// watchesStdin: without job objects, the server learns that the app has gone
// from its stdin pipe closing (see watchParent).
const watchesStdin = true

func hideConsole(*exec.Cmd) {}

func bindToApp(*exec.Cmd) {}
