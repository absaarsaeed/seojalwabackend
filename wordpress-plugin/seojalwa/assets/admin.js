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
