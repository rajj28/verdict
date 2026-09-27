(function () {
  "use strict";
  var script = document.currentScript;
  if (!script) {
    return;
  }
  var match = script.src.match(/^(.*)\/embed\/([^/]+)\.js(?:[?#].*)?$/);
  if (!match) {
    return;
  }
  var base = match[1];
  var slug = decodeURIComponent(match[2]);
  if (window.parent !== window) {
    var sendHeight = function () {
      window.parent.postMessage({
        type: "verdict-embed-height",
        height: document.documentElement.scrollHeight
      }, base);
    };
    if (window.ResizeObserver) {
      new ResizeObserver(sendHeight).observe(document.documentElement);
    }
    window.addEventListener("load", sendHeight);
    sendHeight();
    return;
  }
  var frame = document.createElement("iframe");
  frame.src = base + "/embed/" + encodeURIComponent(slug);
  frame.title = "VERDICT project gallery";
  frame.loading = "lazy";
  frame.style.width = "100%";
  frame.style.border = "0";
  frame.style.minHeight = "240px";
  frame.setAttribute("scrolling", "no");
  window.addEventListener("message", function (event) {
    if (event.origin !== base || event.source !== frame.contentWindow) {
      return;
    }
    if (event.data && event.data.type === "verdict-embed-height") {
      frame.style.height = Math.max(120, Number(event.data.height) || 240) + "px";
    }
  });
  script.parentNode.insertBefore(frame, script.nextSibling);
})();
