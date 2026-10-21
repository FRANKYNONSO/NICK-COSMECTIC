// Global numeric input fix (Update Request item 1)
// - Prevents mouse-wheel scrolling over a focused number input from
//   incrementing/decrementing its value.
// - Prevents drag-select over a focused number input from changing it
//   (the browser's native drag-to-scrub behavior on some platforms).
// Loaded on every page (see <script src="js/numeric-input-fix.js">).
(function () {
  document.addEventListener(
    "wheel",
    function (e) {
      var el = document.activeElement;
      if (el && el.tagName === "INPUT" && el.type === "number") {
        el.blur();
      }
    },
    { passive: true }
  );
})();
