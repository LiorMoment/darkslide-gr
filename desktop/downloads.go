package main

import (
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/egoist/mygo"
)

// WebView2 starts no download for a link to a blob: URL, which is how the page
// saves what it makes itself (导出 .cube, the recipe card, the photo recipe
// zip). On Windows blobBridge hands such a link's content to Downloads.Save
// instead; links to the server download as they do everywhere.
const blobBridge = `(() => {
  const save = (a) => {
    const href = a.href, name = a.getAttribute("download") || "download";
    fetch(href).then((r) => r.blob()).then((b) => new Promise((done, fail) => {
      const r = new FileReader();
      r.onload = () => done(String(r.result).split(",")[1] || "");
      r.onerror = () => fail(r.error);
      r.readAsDataURL(b);
    })).then((data) => window.mygo.call("Downloads.Save", name, data, href))
      .catch((e) => console.error("download failed", e));
  };
  const wanted = (a) => a && a.hasAttribute("download") && a.href.startsWith("blob:") && window.mygo;
  // Links the page clicks without adding them to the document.
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (!this.isConnected && wanted(this)) return save(this);
    return click.call(this);
  };
  // Links in the document, clicked by the user or by the page.
  document.addEventListener("click", (e) => {
    const a = e.target && e.target.closest ? e.target.closest("a") : null;
    if (wanted(a)) { e.preventDefault(); save(a); }
  }, true);
})();`

// bridgeDownloads says whether pages get blobBridge and linkBridge, which save
// downloads through Go: on Windows, and anywhere with DARKSLIDE_DOWNLOAD_BRIDGE=1,
// to test them.
func bridgeDownloads() bool {
	return runtime.GOOS == "windows" || os.Getenv("DARKSLIDE_DOWNLOAD_BRIDGE") == "1"
}

// downloadDir is where Save writes; selftest builds point it elsewhere.
var downloadDir = func() (string, error) { return mygo.App.Path(mygo.PathDownloads) }

// downloadDone hears of every finished download, the webview's and Save's.
var downloadDone = func(d *mygo.Download) {
	if d.Err == nil {
		mygo.Shell.ShowItemInFolder(d.Path)
	}
}

// Downloads is bound for the page.
type Downloads struct{}

// Save writes data, base64, to the Downloads folder under name, numbered when
// a file has that name, and returns the path. url is the blob's, for reports.
func (Downloads) Save(name, data, url string) (string, error) {
	raw, err := base64.StdEncoding.DecodeString(data)
	if err != nil {
		return "", err
	}
	dir, err := downloadDir()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	path := numbered(filepath.Join(dir, cleanName(name)))
	err = os.WriteFile(path, raw, 0o644)
	if err != nil {
		path = ""
	}
	mygo.RunOnMain(func() { downloadDone(&mygo.Download{URL: url, Path: path, Err: err}) })
	return path, err
}

// cleanName makes a name the page suggests safe as a file name in one folder.
func cleanName(name string) string {
	name = filepath.Base(filepath.Clean("/" + strings.ReplaceAll(name, `\`, "/")))
	name = strings.Map(func(r rune) rune {
		if r < 0x20 || strings.ContainsRune(`<>:"|?*`, r) {
			return '_'
		}
		return r
	}, name)
	if name == "/" || name == "." || name == "" {
		return "download"
	}
	return name
}

// numbered gives path a number, "name (2).ext", when a file has its name.
func numbered(path string) string {
	if _, err := os.Lstat(path); errors.Is(err, os.ErrNotExist) {
		return path
	}
	ext := filepath.Ext(path)
	base := strings.TrimSuffix(path, ext)
	for i := 2; ; i++ {
		p := base + " (" + strconv.Itoa(i) + ")" + ext
		if _, err := os.Lstat(p); errors.Is(err, os.ErrNotExist) {
			return p
		}
	}
}
