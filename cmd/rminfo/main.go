package main

import (
	"fmt"
	"os"
	"strings"
)

func read(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(b))
}

func main() {
	fmt.Println("model:   ", read("/sys/devices/soc0/machine"))
	fmt.Println("os:      ", strings.TrimPrefix(read("/usr/share/remarkable/update.conf"), "REMARKABLE_RELEASE_VERSION="))
	fmt.Println("battery: ", read("/sys/class/power_supply/max77818_battery/capacity")+"%")
	fmt.Println("status:  ", read("/sys/class/power_supply/max77818_battery/status"))
}
