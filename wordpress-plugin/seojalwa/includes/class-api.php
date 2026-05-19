<?php
/**
 * SEO Jalwa — API communication helper.
 *
 * All HTTP calls go through wp_remote_post / wp_remote_get with a 10 s timeout.
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEOJalwa_API {

    public static function api_url( $path ) {
        return rtrim( SEOJALWA_API_URL, '/' ) . '/' . ltrim( $path, '/' );
    }

    protected static function key() {
        return (string) get_option( 'seojalwa_api_key', '' );
    }

    protected static function headers( $api_key = null ) {
        return array(
            'X-Jalwa-API-Key' => $api_key ? $api_key : self::key(),
            'Content-Type'    => 'application/json',
            'Accept'          => 'application/json',
        );
    }

    protected static function handle_response( $response ) {
        if ( is_wp_error( $response ) ) {
            error_log( '[SEO Jalwa] HTTP error: ' . $response->get_error_message() );
            return $response;
        }
        $code = wp_remote_retrieve_response_code( $response );
        $body = json_decode( wp_remote_retrieve_body( $response ), true );
        if ( $code >= 200 && $code < 300 ) {
            return $body;
        }
        $msg = isset( $body['error'] ) ? $body['error'] : 'HTTP ' . $code;
        error_log( '[SEO Jalwa] API ' . $code . ': ' . $msg );
        return new WP_Error( 'seojalwa_api', $msg, array( 'status' => $code ) );
    }

    /** Verify the user's API key against /api/plugin/verify */
    public static function verify_key( $api_key ) {
        $response = wp_remote_post( self::api_url( '/api/plugin/verify' ), array(
            'timeout' => 10,
            'headers' => self::headers( $api_key ),
            'body'    => wp_json_encode( new stdClass() ),
        ) );
        return self::handle_response( $response );
    }

    /** Keep-alive ping — hourly cron */
    public static function ping() {
        if ( ! get_option( 'seojalwa_connected', false ) ) {
            return;
        }
        $response = wp_remote_post( self::api_url( '/api/plugin/ping' ), array(
            'timeout' => 10,
            'headers' => self::headers(),
            'body'    => wp_json_encode( new stdClass() ),
        ) );
        $body = self::handle_response( $response );
        if ( ! is_wp_error( $body ) ) {
            update_option( 'seojalwa_last_sync', time() );
        }
    }

    /** GET /api/plugin/articles/pending */
    public static function get_pending_articles() {
        $response = wp_remote_get( self::api_url( '/api/plugin/articles/pending' ), array(
            'timeout' => 15,
            'headers' => self::headers(),
        ) );
        $body = self::handle_response( $response );
        if ( is_wp_error( $body ) ) {
            return $body;
        }
        return isset( $body['data'] ) && is_array( $body['data'] ) ? $body['data'] : array();
    }

    /** POST /api/plugin/articles/{id}/confirm */
    public static function confirm_published( $article_id, $wp_post_id, $wp_url ) {
        $response = wp_remote_post(
            self::api_url( '/api/plugin/articles/' . rawurlencode( $article_id ) . '/confirm' ),
            array(
                'timeout' => 15,
                'headers' => self::headers(),
                'body'    => wp_json_encode( array(
                    'wordpressPostId' => (string) $wp_post_id,
                    'wordpressUrl'    => $wp_url,
                ) ),
            )
        );
        return self::handle_response( $response );
    }

    /** POST /api/plugin/track */
    public static function track_pageview( $page_url ) {
        $response = wp_remote_post( self::api_url( '/api/plugin/track' ), array(
            'timeout' => 5,
            'headers' => self::headers(),
            'body'    => wp_json_encode( array(
                'pageUrl' => $page_url,
                'event'   => 'pageview',
            ) ),
        ) );
        return self::handle_response( $response );
    }
}
