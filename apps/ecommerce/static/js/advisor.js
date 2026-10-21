/* Skincare advisor wizard. Plain JS, no build step. Server text is only ever
   inserted with textContent (never innerHTML). */
(function () {
  'use strict';

  var NAIRA = '\u20A6';

  // Pure helper (also unit-tested): quiz answers -> skin type guess.
  function skinFromQuiz(feel, react) {
    if (react === 'yes') { return 'sensitive'; }
    if (feel === 'tight') { return 'dry'; }
    if (feel === 'shiny_all') { return 'oily'; }
    if (feel === 'shiny_t') { return 'combination'; }
    if (feel === 'comfy') { return 'normal'; }
    return null;
  }

  function formatNaira(n) {
    var v = Math.round(Number(n) || 0);
    return NAIRA + String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  var root = document.getElementById('adv-root');
  if (!root) {
    if (typeof module !== 'undefined') { module.exports = { skinFromQuiz: skinFromQuiz, formatNaira: formatNaira }; }
    return;
  }

  var TOTAL = parseInt(root.getAttribute('data-total'), 10) || 7;
  var steps = root.querySelectorAll('.adv-step');
  var bar = document.getElementById('adv-bar');
  var label = document.getElementById('adv-step-label');
  var backBtn = document.getElementById('adv-back');
  var skipBtn = document.getElementById('adv-skip');
  var nextBtn = document.getElementById('adv-next');
  var card = document.getElementById('adv-card');
  var resultsBox = document.getElementById('adv-results');
  var current = 1;
  var answers = {};
  var quiz = { feel: null, react: null };
  var geo = null; // device location, kept only in memory for this page

  function show(n) {
    current = n;
    Array.prototype.forEach.call(steps, function (s) {
      s.hidden = String(n) !== s.getAttribute('data-step');
    });
    bar.style.width = Math.round((n / TOTAL) * 100) + '%';
    label.textContent = 'Step ' + n + ' of ' + TOTAL;
    backBtn.style.visibility = n === 1 ? 'hidden' : 'visible';
    nextBtn.textContent = n === TOTAL ? 'See suggestions' : 'Next';
  }

  function selectedValues(group) {
    var out = [];
    Array.prototype.forEach.call(group.querySelectorAll('.adv-opt.is-on'), function (b) {
      out.push(b.getAttribute('data-value'));
    });
    return out;
  }

  function collect() {
    Array.prototype.forEach.call(root.querySelectorAll('.adv-options[data-name]'), function (g) {
      var name = g.getAttribute('data-name');
      var vals = selectedValues(g);
      answers[name] = g.getAttribute('data-multi') === '1' ? vals : (vals[0] || null);
    });
    answers.state = document.getElementById('adv-state').value.trim();
    answers.city = document.getElementById('adv-city').value.trim();
    if (geo) { answers.lat = geo.lat; answers.lon = geo.lon; } else { delete answers.lat; delete answers.lon; }
  }

  root.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('.adv-opt') : null;
    if (!btn) { return; }
    var group = btn.parentNode;
    var quizKey = group.getAttribute('data-quiz');
    var multi = group.getAttribute('data-multi') === '1';
    var wasOn = btn.classList.contains('is-on');
    if (!multi) {
      Array.prototype.forEach.call(group.querySelectorAll('.adv-opt'), function (b) { b.classList.remove('is-on'); });
    }
    if (multi) { btn.classList.toggle('is-on'); } else { btn.classList.add('is-on'); }

    if (quizKey) {
      quiz[quizKey] = btn.getAttribute('data-value');
      var guess = skinFromQuiz(quiz.feel, quiz.react);
      var out = document.getElementById('adv-quiz-result');
      if (guess && quiz.feel && quiz.react) {
        out.textContent = 'This suggests ' + guess + ' skin. We will use that, and you can change it above.';
        out.hidden = false;
        var skinGroup = root.querySelector('.adv-options[data-name="skin_type"]');
        Array.prototype.forEach.call(skinGroup.querySelectorAll('.adv-opt'), function (b) {
          b.classList.toggle('is-on', b.getAttribute('data-value') === guess);
        });
      }
    }
    if (group.getAttribute('data-name') === 'skin_type') {
      var notSure = document.getElementById('adv-notsure');
      document.getElementById('adv-quiz').hidden = !(notSure.classList.contains('is-on'));
    }
  });

  document.getElementById('adv-geo').addEventListener('click', function () {
    var note = document.getElementById('adv-geo-note');
    note.hidden = false;
    if (!navigator.geolocation) {
      note.textContent = 'Your device does not share location. Please type your city instead.';
      return;
    }
    note.textContent = 'Checking...';
    navigator.geolocation.getCurrentPosition(function (pos) {
      geo = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      note.textContent = 'Location received. It is used once for the weather and not saved.';
    }, function () {
      geo = null;
      note.textContent = 'We could not get your location. Please type your city instead.';
    }, { timeout: 8000, maximumAge: 600000 });
  });

  backBtn.addEventListener('click', function () { if (current > 1) { show(current - 1); } });
  skipBtn.addEventListener('click', function () { advance(true); });
  nextBtn.addEventListener('click', function () { advance(false); });

  function advance(skipped) {
    if (skipped) {
      var s = root.querySelector('.adv-step[data-step="' + current + '"]');
      Array.prototype.forEach.call(s.querySelectorAll('.adv-opt.is-on'), function (b) { b.classList.remove('is-on'); });
      if (current === 1) {
        document.getElementById('adv-state').value = '';
        document.getElementById('adv-city').value = '';
        geo = null;
      }
    }
    if (current < TOTAL) { show(current + 1); return; }
    submit();
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = text; }
    return n;
  }

  function setNote(id, text) {
    var n = document.getElementById(id);
    if (text) { n.textContent = text; n.hidden = false; } else { n.textContent = ''; n.hidden = true; }
  }

  function showResults(data) {
    card.hidden = true;
    resultsBox.hidden = false;
    var list = document.getElementById('adv-list');
    list.textContent = '';
    setNote('adv-weather', data.weather ? 'Today the weather where you are looks ' + data.weather + ', so we kept that in mind.' : '');
    setNote('adv-msg', data.message || '');
    (data.items || []).forEach(function (it) {
      var c = el('article', 'adv-item');
      if (it.image_url) {
        var img = el('img');
        img.src = it.image_url;
        img.alt = it.name;
        img.loading = 'lazy';
        c.appendChild(img);
      }
      var body = el('div', 'adv-item-body');
      body.appendChild(el('h3', null, it.name + (it.size ? ' (' + it.size + ')' : '')));
      body.appendChild(el('p', 'price', formatNaira(it.price_naira)));
      var ul = el('ul', 'adv-reasons');
      (it.reasons || []).forEach(function (r) { ul.appendChild(el('li', null, r)); });
      body.appendChild(ul);
      var a = el('a', 'btn-primary adv-view', 'View product');
      a.href = it.url;
      body.appendChild(a);
      c.appendChild(body);
      list.appendChild(c);
    });
    resultsBox.scrollIntoView ? resultsBox.scrollIntoView({ behavior: 'smooth', block: 'start' }) : null;
  }

  function submit() {
    collect();
    nextBtn.disabled = true;
    nextBtn.textContent = 'Finding products...';
    fetch('/advisor/recommend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(answers),
      credentials: 'same-origin'
    }).then(function (r) {
      return r.json().then(function (d) { return { status: r.status, data: d }; });
    }).then(function (res) {
      nextBtn.disabled = false;
      nextBtn.textContent = 'See suggestions';
      if (res.data && res.data.ok) { showResults(res.data); return; }
      card.hidden = true;
      resultsBox.hidden = false;
      document.getElementById('adv-list').textContent = '';
      setNote('adv-weather', '');
      setNote('adv-msg', (res.data && res.data.message) || 'Something went wrong. Please try again.');
    }).catch(function () {
      nextBtn.disabled = false;
      nextBtn.textContent = 'See suggestions';
      card.hidden = true;
      resultsBox.hidden = false;
      document.getElementById('adv-list').textContent = '';
      setNote('adv-weather', '');
      setNote('adv-msg', 'We could not reach the shop right now. Please try again shortly, or browse the shop.');
    });
  }

  document.getElementById('adv-restart').addEventListener('click', function () {
    Array.prototype.forEach.call(root.querySelectorAll('.adv-opt.is-on'), function (b) { b.classList.remove('is-on'); });
    document.getElementById('adv-state').value = '';
    document.getElementById('adv-city').value = '';
    document.getElementById('adv-quiz').hidden = true;
    document.getElementById('adv-quiz-result').hidden = true;
    quiz = { feel: null, react: null };
    geo = null;
    answers = {};
    resultsBox.hidden = true;
    card.hidden = false;
    show(1);
  });

  show(1);
})();
