(function () {
  var STORAGE_KEY = 'nabiswa-theme';
  var root = document.documentElement;

  function apply(theme) {
    root.setAttribute('data-theme', theme);
    var sun = document.getElementById('icon-sun');
    var moon = document.getElementById('icon-moon');
    if (sun && moon) {
      sun.style.display = theme === 'dark' ? 'none' : 'inline-block';
      moon.style.display = theme === 'dark' ? 'inline-block' : 'none';
    }
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