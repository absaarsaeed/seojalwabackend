<?php
/**
 * SEO Jalwa — Article publishing.
 *
 * Polled every 15 minutes via wp_cron. Inserts WordPress posts for every
 * pending article returned by the backend, sets featured image from R2,
 * applies Yoast meta, and confirms publication back to the backend.
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEOJalwa_Publisher {

    /** Cron entry point — wp_cron calls this every 15 minutes. */
    public static function poll_articles() {
        if ( ! get_option( 'seojalwa_connected', false ) ) {
            return;
        }
        if ( ! get_option( 'seojalwa_auto_publish', 1 ) ) {
            return;
        }
        $articles = SEOJalwa_API::get_pending_articles();
        if ( is_wp_error( $articles ) || ! is_array( $articles ) ) {
            return;
        }
        foreach ( $articles as $article ) {
            try {
                self::publish_article( $article );
            } catch ( Exception $e ) {
                error_log( '[SEO Jalwa] Publish failed for ' .
                    ( isset( $article['id'] ) ? $article['id'] : 'unknown' ) . ': ' . $e->getMessage() );
            }
        }
        update_option( 'seojalwa_last_sync', time() );
    }

    /** Publish a single article. */
    public static function publish_article( $article ) {
        if ( empty( $article['id'] ) || empty( $article['title'] ) ) {
            return new WP_Error( 'seojalwa_invalid_article', 'Article missing id/title' );
        }

        // 0. Skip if we've already published this article ID
        $existing = get_posts( array(
            'meta_key'   => '_seojalwa_article_id',
            'meta_value' => $article['id'],
            'post_type'  => 'post',
            'post_status'=> 'any',
            'numberposts'=> 1,
        ) );
        if ( ! empty( $existing ) ) {
            return $existing[0]->ID;
        }

        // 1. Insert the post
        $post_id = wp_insert_post( array(
            'post_title'    => wp_strip_all_tags( $article['title'] ),
            'post_content'  => isset( $article['content'] ) ? $article['content'] : '',
            'post_excerpt'  => isset( $article['excerpt'] ) ? $article['excerpt'] : '',
            'post_status'   => get_option( 'seojalwa_post_status', 'publish' ),
            'post_author'   => (int) get_option( 'seojalwa_post_author', 1 ),
            'post_category' => array( (int) get_option( 'seojalwa_category', 1 ) ),
            'tags_input'    => isset( $article['suggestedTags'] ) && is_array( $article['suggestedTags'] )
                ? $article['suggestedTags'] : array(),
            'meta_input'    => array(
                '_yoast_wpseo_title'    => isset( $article['metaTitle'] ) ? $article['metaTitle'] : '',
                '_yoast_wpseo_metadesc' => isset( $article['metaDescription'] ) ? $article['metaDescription'] : '',
                '_seojalwa_article_id'  => $article['id'],
                '_seojalwa_published'   => '1',
                '_seojalwa_seo_score'   => isset( $article['seoScore'] ) ? (int) $article['seoScore'] : 0,
            ),
        ), true );

        if ( is_wp_error( $post_id ) ) {
            error_log( '[SEO Jalwa] wp_insert_post failed: ' . $post_id->get_error_message() );
            return $post_id;
        }

        // 2. Featured image
        if ( ! empty( $article['featuredImageUrl'] ) ) {
            $attachment_id = self::sideload_image( $article['featuredImageUrl'], $post_id, $article['title'] );
            if ( $attachment_id && ! is_wp_error( $attachment_id ) ) {
                set_post_thumbnail( $post_id, $attachment_id );
            }
        }

        // 3. Confirm back to the backend
        SEOJalwa_API::confirm_published( $article['id'], $post_id, get_permalink( $post_id ) );

        // 4. Increment counter + log
        update_option( 'seojalwa_articles_count',
            (int) get_option( 'seojalwa_articles_count', 0 ) + 1 );
        error_log( '[SEO Jalwa] Published "' . $article['title'] . '" as WP post #' . $post_id );
        return $post_id;
    }

    /** Download image from an arbitrary URL and attach to a post. */
    protected static function sideload_image( $url, $post_id, $title ) {
        if ( ! function_exists( 'media_handle_sideload' ) ) {
            require_once ABSPATH . 'wp-admin/includes/media.php';
            require_once ABSPATH . 'wp-admin/includes/file.php';
            require_once ABSPATH . 'wp-admin/includes/image.php';
        }
        $tmp = download_url( $url, 60 );
        if ( is_wp_error( $tmp ) ) {
            return $tmp;
        }
        $file_array = array(
            'name'     => sanitize_file_name( wp_basename( parse_url( $url, PHP_URL_PATH ) ) ?: 'hero.jpg' ),
            'tmp_name' => $tmp,
        );
        $attachment_id = media_handle_sideload( $file_array, $post_id, $title );
        if ( is_wp_error( $attachment_id ) ) {
            @unlink( $tmp );
        }
        return $attachment_id;
    }
}
