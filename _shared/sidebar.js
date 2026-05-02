/* Pied Piper sidebar mount script
 * Fetches /_shared/sidebar.html, injects into #sidebar-mount,
 * marks the active channel, and handles mobile drawer. */
(function () {
  'use strict';

  function normPath(p) {
    // Normalize to leading-slash, no trailing slash (except root)
    if (!p) return '/';
    p = p.replace(/\/+$/, '') || '/';
    return p;
  }

  function mount(html) {
    // 1. Inject sidebar HTML
    var mount = document.getElementById('sidebar-mount');
    if (!mount) return;
    mount.innerHTML = html;

    // 2. Mark active channel
    var path = normPath(window.location.pathname);
    var items = mount.querySelectorAll('.sb-item[data-path]');
    var bestMatch = null;
    var bestLen = 0;
    items.forEach(function (el) {
      var itemPath = normPath(el.getAttribute('data-path'));
      // Exact match or prefix match (longer = better)
      if (path === itemPath || (path.startsWith(itemPath) && itemPath !== '/' && itemPath.length > bestLen)) {
        if (itemPath.length > bestLen || (itemPath === path && itemPath.length >= bestLen)) {
          bestLen = itemPath.length;
          bestMatch = el;
        }
      }
    });
    // Exact root match
    if (path === '/') {
      items.forEach(function (el) {
        if (normPath(el.getAttribute('data-path')) === '/') bestMatch = el;
      });
    }
    if (bestMatch) bestMatch.classList.add('active');

    // 3. Apply body offset class
    document.body.classList.add('pp-has-sidebar');

    // 4. Insert hamburger button
    var hamburger = document.createElement('button');
    hamburger.id = 'pp-hamburger';
    hamburger.setAttribute('aria-label', 'Open navigation');
    hamburger.setAttribute('aria-expanded', 'false');
    hamburger.innerHTML = '&#9776;';
    document.body.insertBefore(hamburger, document.body.firstChild);

    // 5. Overlay for drawer close
    var overlay = document.createElement('div');
    overlay.id = 'pp-sb-overlay';
    document.body.insertBefore(overlay, document.body.firstChild);

    var sidebar = mount.querySelector('#pp-sidebar');

    function openDrawer() {
      sidebar && sidebar.classList.add('open');
      overlay.classList.add('visible');
      hamburger.setAttribute('aria-expanded', 'true');
      hamburger.innerHTML = '&#10005;';
      document.body.style.overflow = 'hidden';
    }
    function closeDrawer() {
      sidebar && sidebar.classList.remove('open');
      overlay.classList.remove('visible');
      hamburger.setAttribute('aria-expanded', 'false');
      hamburger.innerHTML = '&#9776;';
      document.body.style.overflow = '';
    }

    hamburger.addEventListener('click', function () {
      var isOpen = sidebar && sidebar.classList.contains('open');
      isOpen ? closeDrawer() : openDrawer();
    });
    overlay.addEventListener('click', closeDrawer);

    // Close drawer on nav (same-origin link click)
    if (sidebar) {
      sidebar.querySelectorAll('a').forEach(function (a) {
        if (!a.classList.contains('sb-external')) {
          a.addEventListener('click', function () {
            setTimeout(closeDrawer, 80);
          });
        }
      });
    }
  }

  // Fetch and inject
  fetch('/_shared/sidebar.html')
    .then(function (r) { return r.text(); })
    .then(function (html) { mount(html); })
    .catch(function (err) {
      console.warn('[sidebar] failed to load /_shared/sidebar.html', err);
    });
})();
