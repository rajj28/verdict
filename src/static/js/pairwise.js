"use strict";
document.addEventListener("keydown", function (event) {
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey ||
      event.target.closest("input, textarea, select, [contenteditable=true]")) return;
  const choices = {a: "left", ArrowLeft: "left", d: "right", ArrowRight: "right", s: "skip", ArrowDown: "skip"};
  const choice = choices[event.key] || choices[event.key.toLowerCase()];
  if (!choice) return;
  const button = document.querySelector('[data-pairwise-key="' + choice + '"]');
  if (button && !button.disabled) {
    event.preventDefault();
    button.click();
  }
});
