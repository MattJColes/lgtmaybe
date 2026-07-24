// GoatCounter — cookieless, privacy-first analytics (no cookies, no PII, no
// consent banner needed). Material for MkDocs uses instant navigation, so page
// changes don't reload the document; auto-count is disabled here and each page
// view (initial load and every instant navigation) is counted via `document$`,
// the theme's observable that fires on every page.
window.goatcounter = {
  endpoint: "https://colescodes.goatcounter.com/count",
  no_onload: true,
};

document$.subscribe(function () {
  if (window.goatcounter && typeof window.goatcounter.count === "function") {
    window.goatcounter.count({
      path: location.pathname + location.search + location.hash,
    });
  }
});
