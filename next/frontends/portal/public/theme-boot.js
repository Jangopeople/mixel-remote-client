// Applies the saved appearance before the app renders (no flash of the wrong theme).
try {
  var theme = new URLSearchParams(location.search).get('theme') || localStorage.getItem('mixel-remote-portal-theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme);
} catch (e) { /* storage unavailable */ }
