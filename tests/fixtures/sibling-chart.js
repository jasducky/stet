// A sibling asset referenced by sibling-script.html.
//
// It exists so A17 and U5 have a page that loads its own external script from
// beside itself. Under R1.5 the CSP must refuse this file while still serving
// the review layer's own assets, so it deliberately does something observable:
// it writes into an element the page already contains.
//
// Nothing here is real. Northwick Analytics is fictional.
(function () {
  var host = document.getElementById("chart-body");
  if (!host) return;
  host.textContent = "Rendered by the sibling script at " + new Date().toISOString();
  host.setAttribute("data-sibling-ran", "1");
})();
