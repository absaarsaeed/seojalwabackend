<?php
/**
 * SEO Jalwa — Page-view tracker.
 *
 * Injects a 1×1 tracking pixel on every SEO Jalwa-published post.
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEOJalwa_Tracker {

    public function __construct() {
        add_action( 'wp_enqueue_scripts', array( $this, 'enqueue_pixel' ) );
        add_filter( 'body_class', array( $this, 'body_class' ) );
    }

    /** Adds a data attribute to <body> on SEO Jalwa posts. */
    public function body_class( $classes ) {
        if ( is_singular( 'post' ) ) {
            $post_id = get_queried_object_id();
            if ( $post_id && get_post_meta( $post_id, '_seojalwa_published', true ) ) {
                $classes[] = 'seojalwa-content';
            }
        }
        return $classes;
    }

    /** Enqueue the pixel JS (loaded site-wide but only fires on SEO Jalwa pages). */
    public function enqueue_pixel() {
        if ( ! get_option( 'seojalwa_connected', false ) ) {
            return;
        }
        if ( ! is_singular( 'post' ) ) {
            return;
        }
        $post_id = get_queried_object_id();
        if ( ! $post_id || ! get_post_meta( $post_id, '_seojalwa_published', true ) ) {
            return;
        }
        wp_register_script( 'seojalwa-pixel', '', array(), SEOJALWA_VERSION, true );
        wp_localize_script( 'seojalwa-pixel', 'seojalwa_vars', array(
            'api_url' => SEOJALWA_API_URL,
            'api_key' => (string) get_option( 'seojalwa_api_key', '' ),
        ) );
        wp_enqueue_script( 'seojalwa-pixel' );
        wp_add_inline_script( 'seojalwa-pixel', "(function(){
            try {
                if (typeof seojalwa_vars === 'undefined' || !seojalwa_vars.api_key) return;
                var img = new Image();
                img.src = seojalwa_vars.api_url + '/api/plugin/track?k=' +
                    encodeURIComponent(seojalwa_vars.api_key) +
                    '&u=' + encodeURIComponent(window.location.href) +
                    '&t=' + Date.now();
            } catch (e) {}
        })();" );
    }
}
