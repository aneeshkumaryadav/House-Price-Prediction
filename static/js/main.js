document.addEventListener('DOMContentLoaded', function () {
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Navbar solid-on-scroll ---------- */
  var navbar = document.getElementById('mainNavbar');
  function updateNavbar() {
    if (window.scrollY > 40) navbar.classList.add('scrolled');
    else navbar.classList.remove('scrolled');
  }
  if (navbar) { updateNavbar(); window.addEventListener('scroll', updateNavbar); }

  /* ---------- Smooth scroll for in-page anchors ---------- */
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener('click', function (e) {
      var target = document.querySelector(this.getAttribute('href'));
      if (target) { e.preventDefault(); target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    });
  });

  /* ---------- Scroll reveal ---------- */
  var revealEls = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window && revealEls.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { entry.target.classList.add('is-visible'); io.unobserve(entry.target); }
      });
    }, { threshold: 0.15 });
    revealEls.forEach(function (el) { io.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.classList.add('is-visible'); });
  }

  /* ---------- Hero blueprint draw-in + price ticker ---------- */
  var paths = document.querySelectorAll('.blueprint-path[data-draw]');
  if (paths.length) {
    if (reduceMotion) {
      paths.forEach(function (p) { p.style.strokeDasharray = 'none'; });
    } else {
      paths.forEach(function (path, i) {
        var length = path.getTotalLength ? path.getTotalLength() : 400;
        path.style.strokeDasharray = length;
        path.style.strokeDashoffset = length;
        path.style.transition = 'stroke-dashoffset 1.1s ease ' + (i * 0.12) + 's';
        requestAnimationFrame(function () {
          requestAnimationFrame(function () { path.style.strokeDashoffset = 0; });
        });
      });
    }

    var tickerEl = document.getElementById('tickerValue');
    var target = window.HERO_TICKER_TARGET || 0;
    if (tickerEl && target) {
      var startDelay = reduceMotion ? 0 : (paths.length * 120 + 300);
      setTimeout(function () {
        var start = null, duration = 900;
        function formatINR(n) {
          n = Math.floor(n);
          var s = String(Math.abs(n));
          if (s.length <= 3) return (n < 0 ? '-' : '') + s;
          var head = s.slice(0, -3), tail = s.slice(-3), parts = [];
          while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2); }
          if (head) parts.unshift(head);
          return (n < 0 ? '-' : '') + parts.join(',') + ',' + tail;
        }
        function step(ts) {
          if (!start) start = ts;
          var progress = Math.min((ts - start) / duration, 1);
          var eased = 1 - Math.pow(1 - progress, 3);
          var value = Math.floor(eased * target);
          tickerEl.textContent = '\u20B9' + formatINR(value);
          if (progress < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      }, startDelay);
    }
  }

  /* ---------- Confidence ruler (predict results) ---------- */
  var ruler = document.querySelector('.confidence-ruler');
  if (ruler) {
    var low = parseFloat(ruler.dataset.low);
    var high = parseFloat(ruler.dataset.high);
    var price = parseFloat(ruler.dataset.price);
    var pad = (high - low) * 0.35;
    var rangeMin = low - pad, rangeMax = high + pad;

    for (var i = 0; i <= 8; i++) {
      var tick = document.createElement('div');
      tick.className = 'tick';
      tick.style.left = (i / 8 * 100) + '%';
      ruler.appendChild(tick);
    }

    var band = document.createElement('div');
    band.className = 'band';
    var bandLeft = (low - rangeMin) / (rangeMax - rangeMin) * 100;
    var bandWidth = (high - low) / (rangeMax - rangeMin) * 100;
    band.style.left = bandLeft + '%';
    band.style.width = bandWidth + '%';
    ruler.appendChild(band);

    var marker = document.createElement('div');
    marker.className = 'marker';
    marker.style.left = ((price - rangeMin) / (rangeMax - rangeMin) * 100) + '%';
    ruler.appendChild(marker);
  }
});