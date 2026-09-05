(function () {
  var STORAGE_KEY = 'nabiswa-theme';
  var root = document.documentElement;

  function apply(theme) {
    root.setAttribute('data-theme', theme);
  }

  var saved = null;
  try { saved = window.localStorage.getItem(STORAGE_KEY); } catch (e) { /* private mode etc. */ }
  apply(saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

  window.toggleNabiswaTheme = function () {
    var current = root.getAttribute('data-theme') || 'light';
    var next = current === 'light' ? 'dark' : 'light';
    apply(next);
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch (e) {}
  };
})();
