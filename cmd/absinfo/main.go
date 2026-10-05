package main

import (
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

type absInfo struct{ Value, Min, Max, Fuzz, Flat, Res int32 }

var names = map[uintptr]string{
	0x00: "ABS_X", 0x01: "ABS_Y", 0x18: "ABS_PRESSURE", 0x19: "ABS_DISTANCE",
	0x2f: "ABS_MT_SLOT", 0x30: "ABS_MT_TOUCH_MAJOR", 0x31: "ABS_MT_TOUCH_MINOR",
	0x34: "ABS_MT_ORIENTATION", 0x35: "ABS_MT_POSITION_X", 0x36: "ABS_MT_POSITION_Y",
	0x37: "ABS_MT_TOOL_TYPE", 0x39: "ABS_MT_TRACKING_ID", 0x3a: "ABS_MT_PRESSURE",
}

func main() {
	f, err := os.Open(os.Args[1])
	if err != nil {
		fmt.Println(err)
		os.Exit(1)
	}
	const eviocgrab = 0x40044590
	if _, _, e := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), eviocgrab, 1); e != 0 {
		fmt.Println("grab:", e)
	} else {
		syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), eviocgrab, 0)
		fmt.Println("grab: free")
	}
	for code := range uintptr(0x40) {
		var a absInfo
		req := uintptr(0x80184540) + code // EVIOCGABS(code)
		if _, _, e := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), req, uintptr(unsafe.Pointer(&a))); e == 0 && a.Max != 0 {
			fmt.Printf("%-20s min %d max %d res %d\n", names[code], a.Min, a.Max, a.Res)
		}
	}
}
