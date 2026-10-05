// Streams an input device to stdout under an exclusive grab, so the frozen xochitl
// does not queue the events and replay them as strokes and taps after the thaw.
package main

import (
	"fmt"
	"os"
	"syscall"
)

func main() {
	f, err := os.Open(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, "evgrab:", err)
		os.Exit(1)
	}
	const eviocgrab = 0x40044590
	if _, _, e := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), eviocgrab, 1); e != 0 {
		fmt.Fprintln(os.Stderr, "evgrab: grab:", e)
		os.Exit(1)
	}
	buf := make([]byte, 4096)
	for {
		n, err := f.Read(buf)
		if err != nil {
			return
		}
		if _, err := os.Stdout.Write(buf[:n]); err != nil {
			return
		}
	}
}
