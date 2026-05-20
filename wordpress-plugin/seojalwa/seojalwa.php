<?php
/**
 * Plugin Name: SEO Jalwa
 * Plugin URI: https://seojalwa.com
 * Description: Connect your WordPress site to SEO Jalwa for automatic daily article publishing, SEO optimization, and AI-powered content generation.
 * Version: 1.0.1
 * Author: SEO Jalwa
 * Author URI: https://seojalwa.com
 * License: GPL v2 or later
 * License URI: https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain: seojalwa
 * Requires at least: 5.0
 * Requires PHP: 7.4
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

define( 'SEOJALWA_VERSION', '1.0.1' );
define( 'SEOJALWA_API_URL', 'https://api.seojalwa.com' );
define( 'SEOJALWA_PLUGIN_FILE', __FILE__ );
define( 'SEOJALWA_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
define( 'SEOJALWA_PLUGIN_URL', plugin_dir_url( __FILE__ ) );

// -----------------------------------------------------------------------------
// Load classes
// -----------------------------------------------------------------------------
require_once SEOJALWA_PLUGIN_DIR . 'includes/class-api.php';
require_once SEOJALWA_PLUGIN_DIR . 'includes/class-publisher.php';
require_once SEOJALWA_PLUGIN_DIR . 'includes/class-settings.php';
require_once SEOJALWA_PLUGIN_DIR . 'includes/class-tracker.php';

// -----------------------------------------------------------------------------
// Activation / deactivation
// -----------------------------------------------------------------------------
register_activation_hook( __FILE__, 'seojalwa_activate' );
register_deactivation_hook( __FILE__, 'seojalwa_deactivate' );

function seojalwa_activate() {
    add_option( 'seojalwa_api_key', '' );
    add_option( 'seojalwa_site_id', '' );
    add_option( 'seojalwa_connected', false );
    add_option( 'seojalwa_last_sync', 0 );
    add_option( 'seojalwa_version', SEOJALWA_VERSION );
    add_option( 'seojalwa_connected_since', 0 );
    add_option( 'seojalwa_articles_count', 0 );
    add_option( 'seojalwa_auto_publish', 1 );
    add_option( 'seojalwa_post_status', 'publish' );
    add_option( 'seojalwa_category', 1 );
    add_option( 'seojalwa_post_author', 1 );

    if ( ! wp_next_scheduled( 'seojalwa_poll' ) ) {
        wp_schedule_event( time(), 'seojalwa_15min', 'seojalwa_poll' );
    }
    if ( ! wp_next_scheduled( 'seojalwa_ping' ) ) {
        wp_schedule_event( time(), 'hourly', 'seojalwa_ping' );
    }
}

function seojalwa_deactivate() {
    wp_clear_scheduled_hook( 'seojalwa_poll' );
    wp_clear_scheduled_hook( 'seojalwa_ping' );
}

// -----------------------------------------------------------------------------
// Custom cron interval
// -----------------------------------------------------------------------------
add_filter( 'cron_schedules', function ( $schedules ) {
    $schedules['seojalwa_15min'] = array(
        'interval' => 900,
        'display'  => __( 'Every 15 Minutes', 'seojalwa' ),
    );
    return $schedules;
} );

// -----------------------------------------------------------------------------
// Boot
// -----------------------------------------------------------------------------
add_action( 'plugins_loaded', function () {
    new SEOJalwa_Settings();
    new SEOJalwa_Tracker();
} );

add_action( 'seojalwa_poll', array( 'SEOJalwa_Publisher', 'poll_articles' ) );
add_action( 'seojalwa_ping', array( 'SEOJalwa_API', 'ping' ) );

// -----------------------------------------------------------------------------
// REST API: status + verify endpoints
// -----------------------------------------------------------------------------
add_action( 'rest_api_init', function () {
    register_rest_route( 'seojalwa/v1', '/status', array(
        'methods'             => 'GET',
        'callback'            => 'seojalwa_rest_status',
        'permission_callback' => '__return_true',
    ) );
    register_rest_route( 'seojalwa/v1', '/verify', array(
        'methods'             => 'POST',
        'callback'            => 'seojalwa_rest_verify',
        'permission_callback' => '__return_true',
    ) );
} );

function seojalwa_rest_status() {
    return new WP_REST_Response( array(
        'connected'          => (bool) get_option( 'seojalwa_connected', false ),
        'version'            => SEOJALWA_VERSION,
        'site_url'           => get_site_url(),
        'articles_published' => (int) get_option( 'seojalwa_articles_count', 0 ),
        'last_sync'          => gmdate( 'c', (int) get_option( 'seojalwa_last_sync', 0 ) ),
    ), 200 );
}

function seojalwa_rest_verify( WP_REST_Request $request ) {
    $api_key  = (string) $request->get_param( 'api_key' );
    $stored   = (string) get_option( 'seojalwa_api_key', '' );
    if ( ! $stored || ! hash_equals( $stored, $api_key ) ) {
        return new WP_REST_Response( array( 'valid' => false ), 403 );
    }
    return new WP_REST_Response( array(
        'valid'       => true,
        'blog_name'   => get_bloginfo( 'name' ),
        'wp_version'  => get_bloginfo( 'version' ),
        'php_version' => PHP_VERSION,
    ), 200 );
}

// -----------------------------------------------------------------------------
// Update check (transient cached, runs on admin_init)
// -----------------------------------------------------------------------------
add_action( 'admin_init', 'seojalwa_check_for_updates' );

function seojalwa_check_for_updates() {
    $cached = get_transient( 'seojalwa_latest_version' );
    if ( false === $cached ) {
        $resp = wp_remote_get( SEOJALWA_API_URL . '/api/plugin/version', array( 'timeout' => 10 ) );
        if ( ! is_wp_error( $resp ) && 200 === wp_remote_retrieve_response_code( $resp ) ) {
            $body = json_decode( wp_remote_retrieve_body( $resp ), true );
            if ( ! empty( $body['data']['version'] ) ) {
                $cached = $body['data'];
                set_transient( 'seojalwa_latest_version', $cached, 12 * HOUR_IN_SECONDS );
            }
        }
    }
    if ( is_array( $cached ) && version_compare( $cached['version'], SEOJALWA_VERSION, '>' ) ) {
        add_action( 'admin_notices', function () use ( $cached ) {
            $changelog = isset( $cached['changelog'] ) ? esc_html( $cached['changelog'] ) : '';
            $url       = isset( $cached['download_url'] ) ? esc_url( $cached['download_url'] ) : '#';
            echo '<div class="notice notice-warning"><p><strong>SEO Jalwa:</strong> A new version (' .
                esc_html( $cached['version'] ) . ') is available. ' . $changelog .
                ' <a href="' . $url . '" class="button button-primary">Download Update</a></p></div>';
        } );
    }
}

// -----------------------------------------------------------------------------
// AJAX: verify API key from settings page
// -----------------------------------------------------------------------------
add_action( 'wp_ajax_seojalwa_verify_key', 'seojalwa_ajax_verify_key' );

function seojalwa_ajax_verify_key() {
    check_ajax_referer( 'seojalwa_admin', 'nonce' );
    if ( ! current_user_can( 'manage_options' ) ) {
        wp_send_json_error( array( 'message' => 'Permission denied' ), 403 );
    }
    $api_key = isset( $_POST['api_key'] ) ? sanitize_text_field( wp_unslash( $_POST['api_key'] ) ) : '';
    if ( empty( $api_key ) ) {
        wp_send_json_error( array( 'message' => 'API key required' ), 400 );
    }
    $result = SEOJalwa_API::verify_key( $api_key );
    if ( is_wp_error( $result ) ) {
        wp_send_json_error( array( 'message' => $result->get_error_message() ), 400 );
    }
    if ( empty( $result['success'] ) || empty( $result['valid'] ) ) {
        wp_send_json_error( array(
            'message' => isset( $result['error'] ) ? $result['error'] : 'Invalid API key',
            'code'    => isset( $result['code'] ) ? $result['code'] : 'UNKNOWN_ERROR',
        ), 401 );
    }
    $data = isset( $result['data'] ) ? $result['data'] : array();
    update_option( 'seojalwa_api_key', $api_key );
    update_option( 'seojalwa_site_id', isset( $data['userId'] ) ? $data['userId'] : '' );
    update_option( 'seojalwa_connected', true );
    update_option( 'seojalwa_connected_since', time() );
    update_option( 'seojalwa_last_sync', time() );
    wp_send_json_success( array(
        'message'   => 'Connected successfully',
        'site_name' => isset( $data['siteName'] ) ? $data['siteName'] : '',
    ) );
}

add_action( 'wp_ajax_seojalwa_test_connectivity', 'seojalwa_ajax_test_connectivity' );

function seojalwa_ajax_test_connectivity() {
    check_ajax_referer( 'seojalwa_admin', 'nonce' );
    if ( ! current_user_can( 'manage_options' ) ) {
        wp_send_json_error( array( 'message' => 'Permission denied' ), 403 );
    }
    $r = SEOJalwa_API::test_connectivity();
    if ( ! empty( $r['reachable'] ) ) {
        wp_send_json_success( array(
            'reachable' => true,
            'message'   => 'API reachable',
            'status'    => isset( $r['status'] ) ? $r['status'] : 200,
        ) );
    }
    wp_send_json_error( array(
        'reachable' => false,
        'message'   => isset( $r['error'] ) ? $r['error']
            : 'Cannot reach api.seojalwa.com — your hosting may be blocking outbound HTTPS requests. Contact your host or our support at hello@seojalwa.com.',
    ), 503 );
}

add_action( 'wp_ajax_seojalwa_disconnect', 'seojalwa_ajax_disconnect' );

function seojalwa_ajax_disconnect() {
    check_ajax_referer( 'seojalwa_admin', 'nonce' );
    if ( ! current_user_can( 'manage_options' ) ) {
        wp_send_json_error( array( 'message' => 'Permission denied' ), 403 );
    }
    update_option( 'seojalwa_api_key', '' );
    update_option( 'seojalwa_site_id', '' );
    update_option( 'seojalwa_connected', false );
    wp_send_json_success( array( 'message' => 'Disconnected' ) );
}
