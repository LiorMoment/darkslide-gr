package main

import (
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/egoist/mygo"
)

// longRequests are the page's calls that can outlast a webview's patience: a fit,
// 生成 and the read-back that follows. WebKit gives up on a request after 60 s of
// silence ("Load failed"), which a slower Mac reaches. fetchBridge sends these
// through Local.Request instead, which waits as long as the server works.
var longRequests = regexp.MustCompile(`^/api/(projects/[^/]+/(generate|iiix-candidate|film-luts/[^/]+/fit|film-bakes|film-prepare)|fitting-setup)$`)

// fetchBridge hands the page's long requests, those with a JSON or no body, to
// Local.Request. Its pattern is longRequests' own.
var fetchBridge = `(() => {
  const long = new RegExp(` + strconv.Quote(longRequests.String()) + `);
  const native = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const u = new URL(typeof input === "string" ? input : input.url, location.href);
    const body = init && init.body;
    if (!window.mygo || u.origin !== location.origin || !long.test(u.pathname) || (body !== undefined && typeof body !== "string"))
      return native(input, init);
    return window.mygo.call("Local.Request", (init && init.method) || "GET", u.pathname + u.search, (init && init.headers) || {}, body || "")
      .then((r) => new Response(r.body, { status: r.status, headers: { "Content-Type": r.type } }));
  };
})();`

// linkBridge hands links to the server's own files (下载固件, the manifest) to
// Downloads.Fetch on Windows, where WebView2 in testing started no 下载固件 once
// the page had downloaded something else.
const linkBridge = `(() => {
  // A blob: link shares the page's origin too; blobBridge has those.
  const wanted = (a) => a && a.hasAttribute("download") && a.protocol === location.protocol && a.origin === location.origin && window.mygo;
  const fetch = (a) => window.mygo.call("Downloads.Fetch", a.pathname + a.search, a.getAttribute("download") || "")
    .catch((e) => console.error("download failed", e));
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (!this.isConnected && wanted(this)) return fetch(this);
    return click.call(this);
  };
  document.addEventListener("click", (e) => {
    const a = e.target && e.target.closest ? e.target.closest("a") : null;
    if (wanted(a)) { e.preventDefault(); fetch(a); }
  }, true);
})();`

// local is the server's address, set once its port is chosen.
var local string

// Local is bound for the page.
type Local struct{}

// Reply is a server's answer to Local.Request.
type Reply struct {
	Status int    `json:"status"`
	Type   string `json:"type"`
	Body   string `json:"body"`
}

// Request sends one of the page's long requests to the server and waits for
// the answer however long it takes.
func (Local) Request(method, path string, headers map[string]string, body string) (Reply, error) {
	u, err := url.Parse(path)
	if err != nil || u.Host != "" || !longRequests.MatchString(u.Path) {
		return Reply{}, errors.New("not a request this bridge carries")
	}
	req, err := http.NewRequest(method, local+u.RequestURI(), strings.NewReader(body))
	if err != nil {
		return Reply{}, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return Reply{}, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	return Reply{Status: resp.StatusCode, Type: resp.Header.Get("Content-Type"), Body: string(data)}, err
}

// Fetch saves one of the server's files to the Downloads folder, as a link
// with the download attribute would, and returns the path.
func (Downloads) Fetch(path, name string) (string, error) {
	u, err := url.Parse(path)
	if err != nil || u.Host != "" || !strings.HasPrefix(u.Path, "/api/") {
		return "", errors.New("not a file of the server")
	}
	resp, err := http.Get(local + u.RequestURI())
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", errors.New("the server answered " + resp.Status)
	}
	if name == "" {
		name = attachmentName(resp.Header.Get("Content-Disposition"))
	}
	dir, err := downloadDir()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	path = numbered(filepath.Join(dir, cleanName(name)))
	f, err := os.Create(path)
	if err == nil {
		_, err = io.Copy(f, resp.Body)
		if cerr := f.Close(); err == nil {
			err = cerr
		}
	}
	if err != nil {
		os.Remove(path)
		path = ""
	}
	full := local + u.RequestURI()
	mygo.RunOnMain(func() { downloadDone(&mygo.Download{URL: full, Path: path, Err: err}) })
	return path, err
}

func attachmentName(disposition string) string {
	for _, part := range strings.Split(disposition, ";") {
		if k, v, ok := strings.Cut(strings.TrimSpace(part), "="); ok && strings.EqualFold(k, "filename") {
			return strings.Trim(v, `"`)
		}
	}
	return ""
}
