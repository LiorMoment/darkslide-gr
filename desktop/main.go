// Darkslide GR desktop: a MyGo window around the unchanged Python app.
//
// The app ships a standalone Python and the program files (launcher.py,
// darkslide/, web/, templates/, film_curves/) as resources. On launch it
// starts launcher.py on 127.0.0.1, waits until the page answers, and shows
// it. The page's 退出 button ends the server, which quits the app; quitting
// the app asks the server to stop the same way.
//
//	go tool mygo dev     # development, with the repository's program files
//	go tool mygo build   # packaged apps in build/
package main

import (
	"errors"
	"fmt"
	"html"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/egoist/mygo"
)

// The launcher's own port range: the same port each time keeps the page's
// origin, and with it what the page remembers in localStorage.
const (
	firstPort = 8787
	portTries = 20
)

const loadingPage = `<!doctype html><meta charset="utf-8">
<style>
:root{color-scheme:light dark;font:15px -apple-system,"Segoe UI","PingFang TC","Microsoft JhengHei",sans-serif}
body{margin:0;height:100vh;display:grid;place-items:center;background:light-dark(#f5f5f7,#1c1c1e);color:light-dark(#1d1d1f,#f5f5f7)}
p{opacity:.7}
</style><p>正在啟動 Darkslide GR…</p>`

// watchParent runs launcher.py (argv[1]) as python3 launcher.py would, and
// ends the process once stdin reaches its end: the app has gone.
const watchParent = `import os, runpy, sys, threading
def watch():
    sys.stdin.buffer.read()
    os._exit(0)
threading.Thread(target=watch, daemon=True).start()
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
code = 0
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as e:
    if e.code is None or isinstance(e.code, int):
        code = e.code or 0
    else:
        print(e.code, file=sys.stderr)
        code = 1
except BaseException:
    import traceback; traceback.print_exc(); code = 1
# Out at once: a normal shutdown would wait on the thread still reading stdin.
sys.stdout.flush(); sys.stderr.flush()
os._exit(code)
`

type server struct {
	mu       sync.Mutex
	cmd      *exec.Cmd
	parent   io.WriteCloser
	port     int
	quitting bool
	done     chan struct{}
	logPath  string
}

func main() {
	if !mygo.App.RequestSingleInstanceLock() {
		return
	}
	srv := &server{done: make(chan struct{})}
	// The port comes first: the page's origin is the one origin trusted to call Go.
	port, portErr := freePort()
	srv.port = port
	local = "http://127.0.0.1:" + strconv.Itoa(port)
	var win *mygo.Window
	preload := fetchBridge
	if bridgeDownloads() {
		preload += "\n" + blobBridge + "\n" + linkBridge
	}
	page := mygo.PageOptions{TrustedOrigins: []string{local}, PreloadScript: preload}
	mygo.Bind(&Downloads{}, &Local{})

	mygo.App.OnSecondInstance(func([]string, string) {
		if win != nil {
			win.Restore()
			win.Focus()
		}
	})
	mygo.App.WhenReady(func() {
		win = mygo.NewWindow(mygo.WindowOptions{
			Title:           "Darkslide GR",
			Width:           1280,
			Height:          860,
			MinWidth:        900,
			MinHeight:       600,
			StateKey:        "main",
			BackgroundColor: "light-dark(#f5f5f7, #1c1c1e)",
			Page:            page,
		})
		page := win.Page()
		page.LoadHTML(loadingPage, "")
		keepOnApp(page, srv)
		page.OnDownloadDone(func(d *mygo.Download) { downloadDone(d) })
		if portErr != nil {
			showError(page, portErr, "")
			return
		}
		go func() {
			addr, err := srv.start()
			if err != nil {
				showError(page, err, srv.logPath)
				return
			}
			page.LoadURL(addr)
		}()
	})
	// One window: closing it quits, on macOS too, so the server never runs
	// without a page to stop it from.
	mygo.App.OnWindowAllClosed(func() { mygo.App.Quit() })

	err := mygo.App.Run()
	srv.stop()
	if err != nil {
		log.Fatal(err)
	}
}

// keepOnApp sends links that leave the local server, such as Ricoh's firmware
// page, to the default browser.
func keepOnApp(page *mygo.Page, srv *server) {
	page.OnWillNavigate(func(e *mygo.NavigateEvent) {
		u, err := url.Parse(e.URL)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") {
			return
		}
		if u.Host == "127.0.0.1:"+strconv.Itoa(srv.port) {
			return
		}
		e.PreventDefault()
		if os.Getenv("DARKSLIDE_SELFTEST_PORT") != "" {
			return // selftest builds keep the browser closed
		}
		go mygo.Shell.OpenExternal(e.URL)
	})
}

func showError(page *mygo.Page, err error, logPath string) {
	tail := ""
	if b, rerr := os.ReadFile(logPath); rerr == nil {
		if len(b) > 4000 {
			b = b[len(b)-4000:]
		}
		tail = string(b)
	}
	page.LoadHTML(fmt.Sprintf(`<!doctype html><meta charset="utf-8">
<style>:root{color-scheme:light dark;font:14px -apple-system,"Segoe UI","PingFang TC","Microsoft JhengHei",sans-serif}
body{margin:32px;background:light-dark(#f5f5f7,#1c1c1e)}pre{white-space:pre-wrap;font-size:12px;opacity:.8}</style>
<h2>Darkslide GR 無法啟動</h2><p>%s</p><p>記錄檔：%s</p><pre>%s</pre>`,
		html.EscapeString(err.Error()), html.EscapeString(logPath), html.EscapeString(tail)), "")
}

// appRoot is the folder holding launcher.py: the app's resources when
// packaged, the repository under go run.
func appRoot(resources string) (string, error) {
	for _, dir := range []string{resources, filepath.Join(resources, "..", "..")} {
		if _, err := os.Stat(filepath.Join(dir, "launcher.py")); err == nil {
			return filepath.Abs(dir)
		}
	}
	return "", errors.New("找不到 launcher.py，程式檔不完整，請重新安裝")
}

// pythonPath is the bundled interpreter, or $DARKSLIDE_PYTHON for development.
func pythonPath(resources string) (string, error) {
	if p := os.Getenv("DARKSLIDE_PYTHON"); p != "" {
		return p, nil
	}
	p := filepath.Join(resources, "python", "bin", "python3")
	if runtime.GOOS == "windows" {
		p = filepath.Join(resources, "python", "python.exe")
	}
	if _, err := os.Stat(p); err != nil {
		return "", fmt.Errorf("找不到內建的 Python（%s），請先執行 scripts/fetch_python.py 或重新安裝", p)
	}
	return p, nil
}

func freePort() (int, error) {
	for port := firstPort; port < firstPort+portTries; port++ {
		l, err := net.Listen("tcp", "127.0.0.1:"+strconv.Itoa(port))
		if err == nil {
			l.Close()
			return port, nil
		}
	}
	return 0, errors.New("找不到可用的連接埠（8787–8806 都被佔用）")
}

// start runs launcher.py and returns the page's address once it answers.
func (s *server) start() (string, error) {
	resources, err := mygo.App.Path(mygo.PathResources)
	if err != nil {
		return "", err
	}
	logs, err := mygo.App.Path(mygo.PathLogs)
	if err != nil {
		return "", err
	}
	s.logPath = filepath.Join(logs, "server.log")
	root, err := appRoot(resources)
	if err != nil {
		return "", err
	}
	python, err := pythonPath(resources)
	if err != nil {
		return "", err
	}
	port := s.port
	logFile, err := os.Create(s.logPath)
	if err != nil {
		return "", err
	}

	// -E -s: ignore PYTHON* variables and the user's site-packages, so the
	// machine's own Python setup cannot change the app. -B: the app bundle
	// stays unwritten (and its signature valid).
	args := []string{"-E", "-s", "-B", "-X", "utf8"}
	if watchesStdin {
		args = append(args, "-c", watchParent)
	}
	cmd := exec.Command(python, append(args, filepath.Join(root, "launcher.py"), "--no-browser")...)
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "PORT="+strconv.Itoa(port), "PYTHONUNBUFFERED=1")
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	if watchesStdin {
		// The server's stdin is a pipe only this process writes to: it closes
		// however the app ends, even when killed, and watchParent then ends the server.
		parent, err := cmd.StdinPipe()
		if err != nil {
			logFile.Close()
			return "", err
		}
		s.parent = parent
	}
	hideConsole(cmd)
	if err := cmd.Start(); err != nil {
		logFile.Close()
		return "", fmt.Errorf("無法啟動 Python：%w", err)
	}
	bindToApp(cmd)
	s.mu.Lock()
	s.cmd = cmd
	s.mu.Unlock()

	go func() {
		err := cmd.Wait()
		logFile.Close()
		close(s.done)
		s.mu.Lock()
		quitting := s.quitting
		s.mu.Unlock()
		if !quitting {
			// The page's 退出 button, or a crash: either way there is no page left.
			log.Printf("server exited: %v", err)
			mygo.App.Quit()
		}
	}()

	addr := "http://127.0.0.1:" + strconv.Itoa(port) + "/"
	client := &http.Client{Timeout: time.Second}
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-s.done:
			return "", errors.New("伺服器啟動後立即結束")
		default:
		}
		if resp, err := client.Get(addr); err == nil {
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return addr, nil
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	return "", errors.New("伺服器 30 秒內沒有回應")
}

// stop asks the server to quit as the page's 退出 button does, and ends it
// if it has not within three seconds.
func (s *server) stop() {
	s.mu.Lock()
	s.quitting = true
	cmd, port := s.cmd, s.port
	s.mu.Unlock()
	if cmd == nil {
		return
	}
	client := &http.Client{Timeout: time.Second}
	if resp, err := client.Post("http://127.0.0.1:"+strconv.Itoa(port)+"/api/quit", "application/json", strings.NewReader("{}")); err == nil {
		resp.Body.Close()
	}
	select {
	case <-s.done:
	case <-time.After(3 * time.Second):
		cmd.Process.Kill()
		<-s.done
	}
}
