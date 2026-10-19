/* =========================================================
   Nabiswa Logic WiFi — Link Path loader
   File: static/js/connect-loader.js

   One loader for the whole purchase modal, from the moment the
   customer taps "Select Plan" until they are online:

     0  Sending payment request     "Talking to M-Pesa, one moment."
     1  Confirm on your phone       "Enter your M-Pesa PIN to complete the payment."
     2  Activating your access      (payment received)
     3  Opening the connection
     4  You're online

   Usage:
     var loader = NabiswaConnect.mount(document.getElementById('connect-loader'), {
       onCancel:  function () { cancelPayment(); closeModal(); },
       onRetry:   function () { startPayment(); },
       onSuccess: function () { window.location.href = redirectUrl; }
     });

     // 1. STK push request has been sent
     //    (the loader opens in this state, nothing to call)
     // 2. STK push accepted by M-Pesa, the PIN prompt is on the phone
     loader.awaitPin();
     // 3. Payment confirmed by your backend / callback
     loader.paid();            // auto-moves to "Opening the connection" after a few seconds
     // 4. Hotspot login finished
     loader.success();
     // Any failure or timeout
     loader.fail('Payment was cancelled.', 'Payment not completed');  // heading optional
     // opts.onClose (optional) adds a Close button to the failure screen
   ========================================================= */
(function (global) {
  'use strict';

  var STEPS = [
    'Sending payment request',
    'Confirm on your phone',
    'Activating your access',
    'Opening the connection',
    "You're online"
  ];

  var COPY = [
    { title: 'Sending payment request',   sub: 'Talking to M-Pesa, one moment.' },
    { title: 'Check your phone',          sub: 'Enter your M-Pesa PIN to complete the payment.' },
    { title: 'Payment received',          sub: 'Your payment went through. We are activating your access.' },
    { title: 'Setting up your connection', sub: 'Opening your connection to the internet.' },
    { title: "You're online",             sub: 'Enjoy browsing. You can close this window.' }
  ];

  var TEMPLATE =
    '<div class="link-loader" role="status" aria-live="polite">' +
      '<div class="ll-scene" aria-hidden="true">' +
        '<svg viewBox="0 0 300 110" fill="none">' +
          '<g class="ll-link-a">' +
            '<line class="ll-link-base" x1="54" y1="68" x2="122" y2="68"/>' +
            '<line class="ll-link-flow" x1="54" y1="68" x2="122" y2="68"/>' +
            '<circle class="ll-packet ll-packet-a p1" cx="54" cy="68" r="3"/>' +
            '<circle class="ll-packet ll-packet-a p2" cx="54" cy="68" r="3"/>' +
          '</g>' +
          '<g class="ll-link-b">' +
            '<line class="ll-link-base" x1="178" y1="68" x2="240" y2="68"/>' +
            '<line class="ll-link-flow" x1="178" y1="68" x2="240" y2="68"/>' +
            '<circle class="ll-packet ll-packet-b p1" cx="178" cy="68" r="3"/>' +
            '<circle class="ll-packet ll-packet-b p2" cx="178" cy="68" r="3"/>' +
          '</g>' +
          '<g class="ll-device-phone">' +
            '<circle class="ll-ping p1" cx="36" cy="68" r="14"/>' +
            '<circle class="ll-ping p2" cx="36" cy="68" r="14"/>' +
            '<rect class="ll-node" x="24" y="48" width="24" height="40" rx="5"/>' +
            '<line class="ll-glyph ll-phone-top" x1="32" y1="53" x2="40" y2="53"/>' +
            '<line class="ll-glyph ll-phone-home" x1="33" y1="82" x2="39" y2="82"/>' +
            '<g class="ll-pin-dots">' +
              '<circle cx="28.5" cy="68" r="1.7"/>' +
              '<circle cx="33.5" cy="68" r="1.7"/>' +
              '<circle cx="38.5" cy="68" r="1.7"/>' +
              '<circle cx="43.5" cy="68" r="1.7"/>' +
            '</g>' +
          '</g>' +
          '<g class="ll-device-router">' +
            '<circle class="ll-burst" cx="150" cy="62" r="6"/>' +
            '<path class="ll-arc ll-arc-3" d="M130.2 42.2 A28 28 0 0 1 169.8 42.2"/>' +
            '<path class="ll-arc ll-arc-2" d="M136.57 48.57 A19 19 0 0 1 163.43 48.57"/>' +
            '<path class="ll-arc ll-arc-1" d="M142.93 54.93 A10 10 0 0 1 157.07 54.93"/>' +
            '<circle class="ll-core" cx="150" cy="62" r="2.8"/>' +
            '<rect class="ll-node" x="128" y="68" width="44" height="18" rx="5"/>' +
            '<circle class="ll-led" cx="138" cy="77" r="2"/>' +
            '<circle class="ll-led" cx="146" cy="77" r="2" style="opacity:.5"/>' +
            '<line class="ll-glyph" x1="156" y1="77" x2="164" y2="77"/>' +
          '</g>' +
          '<g class="ll-device-globe">' +
            '<circle class="ll-node" cx="264" cy="68" r="19"/>' +
            '<ellipse class="ll-glyph" cx="264" cy="68" rx="8" ry="19"/>' +
            '<line class="ll-glyph" x1="245" y1="68" x2="283" y2="68"/>' +
            '<path class="ll-glyph" d="M248 58 Q264 62 280 58"/>' +
            '<path class="ll-glyph" d="M248 78 Q264 74 280 78"/>' +
            '<g class="ll-badge">' +
              '<circle class="ll-badge-bg" cx="281" cy="52" r="8"/>' +
              '<path class="ll-badge-tick" d="M277.2 52.2 L280 55 L285 49.6"/>' +
            '</g>' +
          '</g>' +
        '</svg>' +
      '</div>' +
      '<h3 class="ll-title"></h3>' +
      '<p class="ll-sub"></p>' +
      '<ul class="ll-steps"></ul>' +
      '<div class="connection-track ll-track" aria-hidden="true"><span class="connection-track-progress ll-track-progress"></span></div>' +
      '<p class="ll-note"></p>' +
      '<div class="ll-cancel-wrap"><button type="button" class="btn btn-secondary ll-cancel">Cancel</button></div>' +
      '<div class="ll-actions">' +
        '<button type="button" class="btn ll-retry">Try again</button>' +
        '<button type="button" class="btn btn-secondary ll-close" hidden>Close</button>' +
        '<a class="btn btn-secondary ll-support" href="tel:+254792390205">Call support</a>' +
      '</div>' +
    '</div>';

  function mount(container, options) {
    if (!container) return null;
    var opts = options || {};
    var advanceAfter   = opts.advanceAfter   == null ? 3500  : opts.advanceAfter;
    var pinNoteAfter   = opts.pinNoteAfter   == null ? 45000 : opts.pinNoteAfter;
    var delayNoteAfter = opts.delayNoteAfter == null ? 25000 : opts.delayNoteAfter;
    var successDelay   = opts.successDelay   == null ? 1800  : opts.successDelay;

    container.innerHTML = TEMPLATE;
    var root = container.querySelector('.link-loader');
    var title = root.querySelector('.ll-title');
    var sub = root.querySelector('.ll-sub');
    var stepsEl = root.querySelector('.ll-steps');
    var note = root.querySelector('.ll-note');
    var modalCard = container.closest ? container.closest('.modal-card') : null;
    if (modalCard) modalCard.classList.add('is-connecting');

    var timers = [];
    var state = { step: 0, finished: false };

    STEPS.forEach(function (label) {
      var li = document.createElement('li');
      li.className = 'll-step';
      li.innerHTML = '<span class="ll-step-icon"></span><span class="ll-step-label"></span>';
      li.querySelector('.ll-step-label').textContent = label;
      stepsEl.appendChild(li);
    });
    var stepEls = stepsEl.querySelectorAll('.ll-step');

    function later(fn, ms) { var id = setTimeout(fn, ms); timers.push(id); return id; }
    function clearTimers() { timers.forEach(clearTimeout); timers = []; }
    function showNote(text) { note.textContent = text; note.classList.add('show'); }
    function hideNote() { note.classList.remove('show'); }

    function render(step) {
      state.step = step;
      root.setAttribute('data-step', step);

      root.classList.toggle('phase-request', step === 0);
      root.classList.toggle('phase-pin', step === 1);

      var reach = Math.max(0, Math.min(3, step - 1));
      for (var n = 1; n <= 3; n++) root.classList.toggle('reach-' + n, n <= reach);

      for (var i = 0; i < stepEls.length; i++) {
        var s = i < step ? 'done' : (i === step ? 'active' : 'pending');
        if (step === 4) s = 'done';
        stepEls[i].setAttribute('data-state', s);
      }
      title.textContent = COPY[step].title;
      sub.textContent = COPY[step].sub;
    }

    function go(step) {
      if (state.finished || step <= state.step && step !== 0) return;
      clearTimers();
      hideNote();
      render(step);
      if (step === 1 && pinNoteAfter > 0) {
        later(function () {
          showNote('No prompt yet? Check that your phone is on and has network, then keep this page open.');
        }, pinNoteAfter);
      }
      if (step === 2) {
        if (advanceAfter > 0) later(function () { go(3); }, advanceAfter);
      }
      if (step >= 2 && delayNoteAfter > 0) {
        later(function () {
          showNote('This is taking a little longer than usual. Keep this page open and we will connect you automatically.');
        }, delayNoteAfter);
      }
    }

    function success() {
      if (state.finished) return;
      state.finished = true;
      clearTimers();
      hideNote();
      root.classList.remove('is-failed');
      root.classList.add('is-success');
      render(4);
      if (typeof opts.onSuccess === 'function') later(opts.onSuccess, successDelay);
    }

    function fail(message, heading) {
      if (state.finished) return;
      state.finished = true;
      clearTimers();
      hideNote();
      root.classList.remove('phase-request', 'phase-pin');
      root.classList.add('is-failed');
      title.textContent = heading || 'Connection not completed';
      sub.textContent = message || 'We could not finish connecting you. Try again, or call support.';
      var active = stepEls[state.step];
      if (active) active.setAttribute('data-state', 'failed');
    }

    function restart() {
      clearTimers();
      state.finished = false;
      root.classList.remove('is-success', 'is-failed', 'reach-1', 'reach-2', 'reach-3');
      hideNote();
      state.step = 0;
      render(0);
    }

    root.querySelector('.ll-retry').addEventListener('click', function () {
      restart();
      if (typeof opts.onRetry === 'function') opts.onRetry();
    });

    var closeBtn = root.querySelector('.ll-close');
    if (typeof opts.onClose === 'function') {
      closeBtn.hidden = false;
      closeBtn.addEventListener('click', opts.onClose);
    }

    root.querySelector('.ll-cancel').addEventListener('click', function () {
      if (typeof opts.onCancel === 'function') opts.onCancel();
    });

    function destroy() {
      clearTimers();
      if (modalCard) modalCard.classList.remove('is-connecting');
      container.innerHTML = '';
    }

    render(0);

    return {
      request:    restart,
      awaitPin:   function () { go(1); },
      paid:       function () { go(2); },
      connecting: function () { go(3); },
      success:    success,
      fail:       fail,
      restart:    restart,
      destroy:    destroy
    };
  }

  global.NabiswaConnect = { mount: mount };
})(window);