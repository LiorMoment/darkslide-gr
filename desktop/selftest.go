//go:build selftest

// A control channel for testing the packaged app without a person at the
// window, built only with -tags selftest:
//
//	POST /eval       body: JavaScript, run in the page; replies with its value as JSON
//	GET  /file       ?path=, a local file the page can turn into a File
//	GET  /downloads  the downloads that finished, as JSON
//
// It listens on 127.0.0.1:$DARKSLIDE_SELFTEST_PORT and saves downloads in
// $DARKSLIDE_SELFTEST_DOWNLOADS instead of revealing them.
package main

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sync"

	"github.com/egoist/mygo"
)

func init() {
	port := os.Getenv("DARKSLIDE_SELFTEST_PORT")
	if port == "" {
		return
	}
	dir := os.Getenv("DARKSLIDE_SELFTEST_DOWNLOADS")
	var mu sync.Mutex
	var done []*mygo.Download
	// Every finished download, the webview's and Downloads.Save's, is recorded, not revealed.
	downloadDone = func(d *mygo.Download) {
		mu.Lock()
		done = append(done, d)
		mu.Unlock()
	}
	if dir != "" {
		downloadDir = func() (string, error) { return dir, nil }
	}
	mygo.App.OnWindowCreated(func(w *mygo.Window) {
		page := w.Page()
		if page == nil {
			return
		}
		page.OnWillDownload(func(e *mygo.DownloadEvent) {
			if dir != "" {
				e.Path = filepath.Join(dir, e.SuggestedName)
			}
		})
		mux := http.NewServeMux()
		mux.HandleFunc("POST /eval", func(rw http.ResponseWriter, r *http.Request) {
			code, _ := io.ReadAll(r.Body)
			v, err := page.Eval(string(code))
			out := map[string]any{"value": v}
			if err != nil {
				out["error"] = err.Error()
			}
			json.NewEncoder(rw).Encode(out)
		})
		// A local file for the page, which turns it into a File as a drop or
		// the file picker would: GET /file?path=...
		mux.HandleFunc("GET /file", func(rw http.ResponseWriter, r *http.Request) {
			rw.Header().Set("Access-Control-Allow-Origin", "*")
			http.ServeFile(rw, r, r.URL.Query().Get("path"))
		})
		mux.HandleFunc("GET /downloads", func(rw http.ResponseWriter, r *http.Request) {
			mu.Lock()
			defer mu.Unlock()
			type row struct{ URL, Path, Err string }
			rows := []row{}
			for _, d := range done {
				e := ""
				if d.Err != nil {
					e = d.Err.Error()
				}
				rows = append(rows, row{d.URL, d.Path, e})
			}
			json.NewEncoder(rw).Encode(rows)
		})
		go http.ListenAndServe("127.0.0.1:"+port, mux)
	})
}
