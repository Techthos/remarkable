// Runs a command with a private /tmp. The e-paper library takes its single instance
// locks there, so the frame app can drive the screen while the frozen xochitl holds them.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"syscall"
)

func check(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "privtmp:", err)
		os.Exit(1)
	}
}

func main() {
	runtime.LockOSThread()
	check(syscall.Unshare(syscall.CLONE_NEWNS))
	check(syscall.Mount("", "/", "", syscall.MS_REC|syscall.MS_PRIVATE, ""))
	check(syscall.Mount("tmpfs", "/tmp", "tmpfs", 0, ""))
	path, err := exec.LookPath(os.Args[1])
	check(err)
	check(syscall.Exec(path, os.Args[1:], os.Environ()))
}
