// "No official firmware?" link on the start page: after the user starts the
// download, the hint turns into the next step and the drop zone is outlined.
(function () {
  const line = document.getElementById("fwGet");
  if (!line) return;
  const orig = line.innerHTML;
  // Delegated, because the line is rebuilt: after a click it only names the file the clicked link
  // gave (one of several versions, e.g. gr3_v220.zip or gr3_v210.zip), and switching series puts
  // the links back (window.resetFwGet).
  line.addEventListener("click", (e) => {
    const link = e.target.closest("a.fw-dl");
    if (!link) return;
    line.classList.add("ready");
    line.textContent = "下载好了？直接把 " + link.href.split("/").pop() + " 拖进来。";
    document.getElementById("drop").classList.add("await");
  });
  window.resetFwGet = () => {
    if (!line.classList.contains("ready")) return;
    line.classList.remove("ready");
    line.innerHTML = orig;
    document.getElementById("drop").classList.remove("await");
  };
  // Any import attempt ends the waiting state.
  const input = document.getElementById("fileInput");
  const clear = () => document.getElementById("drop").classList.remove("await");
  input.addEventListener("change", clear);
  document.getElementById("drop").addEventListener("drop", clear);
})();
