(function () {
  "use strict";
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-print-page]");
    if (button) {
      window.print();
    }
  });
})();
