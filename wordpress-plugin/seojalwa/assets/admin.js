(function ($) {
    'use strict';

    function showMsg(text, type) {
        var $m = $('#seojalwa-message');
        $m.text(text).removeClass('is-error is-success');
        if (type === 'error') $m.addClass('is-error');
        if (type === 'success') $m.addClass('is-success');
    }

    function spin(on) {
        $('#seojalwa-spinner').toggleClass('is-active', !!on);
    }

    function runConnectivityTest() {
        var $box  = $('#seojalwa-connectivity');
        var $text = $('#seojalwa-connectivity-text');
        if (!$box.length) return;
        $.post(seojalwa_vars.ajax_url, {
            action: 'seojalwa_test_connectivity',
            nonce:  seojalwa_vars.nonce
        }).done(function (resp) {
            $box.show().removeClass('seojalwa-banner--ok seojalwa-banner--err');
            if (resp && resp.success) {
                $box.addClass('seojalwa-banner--ok');
                $text.text('Connection test: ✓ API reachable');
            } else {
                $box.addClass('seojalwa-banner--err');
                var m = (resp && resp.data && resp.data.message)
                    || 'Cannot reach api.seojalwa.com — your hosting may be blocking outbound HTTPS requests. Contact your host or our support at hello@seojalwa.com.';
                $text.html('Connection test: ✗ ' + m + ' <a href="mailto:hello@seojalwa.com">hello@seojalwa.com</a>');
            }
        }).fail(function () {
            $box.show().removeClass('seojalwa-banner--ok').addClass('seojalwa-banner--err');
            $text.html('Connection test: ✗ Cannot reach api.seojalwa.com — your hosting may be blocking outbound HTTPS requests. Contact your host or our support at <a href="mailto:hello@seojalwa.com">hello@seojalwa.com</a>.');
        });
    }

    $(runConnectivityTest);

    $(document).on('click', '#seojalwa-verify', function () {
        var key = $('#seojalwa_api_key').val().trim();
        if (!key) { showMsg('Enter your API key.', 'error'); return; }
        spin(true);
        showMsg('Verifying…');
        $.post(seojalwa_vars.ajax_url, {
            action:  'seojalwa_verify_key',
            nonce:   seojalwa_vars.nonce,
            api_key: key
        }).done(function (resp) {
            spin(false);
            if (resp && resp.success) {
                showMsg('Connected! Reloading…', 'success');
                setTimeout(function () { window.location.reload(); }, 700);
            } else {
                showMsg((resp && resp.data && resp.data.message) || 'Verification failed.', 'error');
            }
        }).fail(function (xhr) {
            spin(false);
            var msg = 'Network error.';
            if (xhr.responseJSON && xhr.responseJSON.data && xhr.responseJSON.data.message) {
                msg = xhr.responseJSON.data.message;
            }
            showMsg(msg, 'error');
        });
    });

    $(document).on('click', '#seojalwa-disconnect', function () {
        if (!confirm('Disconnect this site from SEO Jalwa?')) return;
        spin(true);
        $.post(seojalwa_vars.ajax_url, {
            action: 'seojalwa_disconnect',
            nonce:  seojalwa_vars.nonce
        }).always(function () {
            window.location.reload();
        });
    });
})(jQuery);
