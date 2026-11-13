/* Picks the right download for the visitor. The page works without this file:
   the Windows button and the full platform list are plain HTML. */
(function () {
  var BASE = "https://github.com/ethanwheatthin/All-In-One-Snapchat-Downloader/releases/latest/download/";
  var API = "https://api.github.com/repos/ethanwheatthin/All-In-One-Snapchat-Downloader/releases/latest";

  function detectOS(ua, platform, maxTouchPoints) {
    ua = ua || ""; platform = platform || "";
    if (/Android|iPhone|iPad|iPod/i.test(ua)) return "mobile";
    // iPadOS reports itself as a Mac but has a touch screen.
    if (/Mac/i.test(platform) && maxTouchPoints > 1) return "mobile";
    if (/Win/i.test(platform) || /Windows/i.test(ua)) return "windows";
    if (/Mac/i.test(platform) || /Mac OS X/i.test(ua)) return "mac";
    if (/Linux|X11/i.test(platform + " " + ua)) return "linux";
    return "windows";
  }

  window.SiteDownload = { detectOS: detectOS };
  if (typeof document === "undefined") return;

  function init() {
    var btn = document.getElementById("download-primary");
    var note = document.getElementById("download-note");
    var winNote = document.getElementById("windows-note");
    var os = detectOS(navigator.userAgent, navigator.platform, navigator.maxTouchPoints || 0);

    if (btn) {
      if (os === "mac") {
        btn.textContent = "Download for Mac";
        btn.href = "#download";
        if (note) note.innerHTML = "Choose Apple Silicon or Intel. Check your chip in the Apple menu under About This Mac.";
      } else if (os === "linux") {
        btn.textContent = "Download for Linux";
        btn.href = BASE + "AllInOneSnapchatDownloader-linux.tar.gz";
      } else if (os === "mobile") {
        btn.textContent = "See downloads";
        btn.href = "#download";
        if (note) note.innerHTML = "This app runs on a computer, not a phone or tablet. Open this page on your computer to download it.";
      }
    }
    if (winNote && os !== "windows" && os !== "mobile") winNote.hidden = true;

    // Show the version if GitHub answers. Any failure leaves it hidden.
    var v = document.getElementById("version");
    if (v && window.fetch) {
      fetch(API, { headers: { Accept: "application/vnd.github+json" } })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (d) {
          if (d && d.tag_name) { v.textContent = "Version " + String(d.tag_name).replace(/^[vV]/, "") + "."; v.hidden = false; }
        })
        .catch(function () {});
    }

    var copyBtns = document.querySelectorAll("[data-copy]");
    Array.prototype.forEach.call(copyBtns, function (b) {
      b.addEventListener("click", function () {
        var text = b.getAttribute("data-copy");
        var done = function () { var old = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = old; }, 1500); };
        var fallback = function () {
          var code = b.parentNode.querySelector("code");
          if (code && window.getSelection) {
            var range = document.createRange(); range.selectNodeContents(code);
            var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
          }
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, fallback);
        } else { fallback(); }
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
