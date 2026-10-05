// Streams an input device to stdout under an exclusive grab, so the frozen xochitl
// does not queue the events and replay them as strokes and taps after the thaw.
// An optional FIFO gets a copy for the tablet app; copies nobody reads are dropped.
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
	copyTo := -1
	if len(os.Args) > 2 {
		syscall.Mkfifo(os.Args[2], 0o600)
		copyTo, _ = syscall.Open(os.Args[2], syscall.O_RDWR|syscall.O_NONBLOCK, 0)
	}
	buf := make([]byte, 4096)
	for {
		n, err := f.Read(buf)
		if err != nil {
			return
		}
		if copyTo >= 0 {
			syscall.Write(copyTo, buf[:n])
		}
		if _, err := os.Stdout.Write(buf[:n]); err != nil {
			return
		}
	}
}
