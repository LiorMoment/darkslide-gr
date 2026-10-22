package main

import (
	"os/exec"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

// watchesStdin is false: the job object below ends the server with the app, and
// a stdin pipe blocked in a read stalls every child that inherits it, such as the
// server's emulation worker, until its 60-second limit (Windows serializes I/O on
// one pipe).
const watchesStdin = false

// hideConsole keeps python.exe, a console program, from opening a console window.
func hideConsole(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
}

// job ends the server with the app, even when the app is ended from Task
// Manager or crashes: Windows closes the job's handle with the process.
var job windows.Handle

func bindToApp(cmd *exec.Cmd) {
	if job == 0 {
		h, err := windows.CreateJobObject(nil, nil)
		if err != nil {
			return
		}
		info := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{}
		info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
		if _, err := windows.SetInformationJobObject(h, windows.JobObjectExtendedLimitInformation,
			uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info))); err != nil {
			windows.CloseHandle(h)
			return
		}
		job = h
	}
	p, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(cmd.Process.Pid))
	if err != nil {
		return
	}
	defer windows.CloseHandle(p)
	windows.AssignProcessToJobObject(job, p)
}
