<?php
/**
 * SEO Jalwa — Settings page under Settings → SEO Jalwa.
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class SEOJalwa_Settings {

    public function __construct() {
        add_action( 'admin_menu', array( $this, 'add_menu_page' ) );
        add_action( 'admin_init', array( $this, 'register_settings' ) );
        add_action( 'admin_enqueue_scripts', array( $this, 'enqueue_assets' ) );
    }

    public function add_menu_page() {
        add_options_page(
            __( 'SEO Jalwa', 'seojalwa' ),
            __( 'SEO Jalwa', 'seojalwa' ),
            'manage_options',
            'seojalwa',
            array( $this, 'render_page' )
        );
    }

    public function register_settings() {
        register_setting( 'seojalwa_settings', 'seojalwa_auto_publish', array( 'type' => 'boolean', 'default' => 1 ) );
        register_setting( 'seojalwa_settings', 'seojalwa_post_status', array( 'type' => 'string', 'default' => 'publish' ) );
        register_setting( 'seojalwa_settings', 'seojalwa_category', array( 'type' => 'integer', 'default' => 1 ) );
        register_setting( 'seojalwa_settings', 'seojalwa_post_author', array( 'type' => 'integer', 'default' => 1 ) );
    }

    public function enqueue_assets( $hook ) {
        if ( 'settings_page_seojalwa' !== $hook ) {
            return;
        }
        wp_enqueue_style( 'seojalwa-admin', SEOJALWA_PLUGIN_URL . 'assets/admin.css', array(), SEOJALWA_VERSION );
        wp_enqueue_script( 'seojalwa-admin', SEOJALWA_PLUGIN_URL . 'assets/admin.js', array( 'jquery' ), SEOJALWA_VERSION, true );
        wp_localize_script( 'seojalwa-admin', 'seojalwa_vars', array(
            'ajax_url' => admin_url( 'admin-ajax.php' ),
            'nonce'    => wp_create_nonce( 'seojalwa_admin' ),
            'api_key'  => (string) get_option( 'seojalwa_api_key', '' ),
        ) );
    }

    public function render_page() {
        if ( ! current_user_can( 'manage_options' ) ) {
            return;
        }
        $connected      = (bool) get_option( 'seojalwa_connected', false );
        $api_key        = (string) get_option( 'seojalwa_api_key', '' );
        $masked         = $api_key ? '••••••••' . substr( $api_key, -4 ) : '';
        $last_sync      = (int) get_option( 'seojalwa_last_sync', 0 );
        $count          = (int) get_option( 'seojalwa_articles_count', 0 );
        $connected_iso  = (int) get_option( 'seojalwa_connected_since', 0 );
        ?>
        <div class="wrap seojalwa-wrap">
            <h1><?php esc_html_e( 'SEO Jalwa', 'seojalwa' ); ?></h1>
            <p class="description">
                <?php esc_html_e( 'Connect your site to SEO Jalwa for automatic SEO-optimised article publishing.', 'seojalwa' ); ?>
            </p>

            <?php if ( $connected ) : ?>
                <div class="seojalwa-banner seojalwa-banner--ok">
                    <strong>✓ <?php esc_html_e( 'Connected to SEO Jalwa', 'seojalwa' ); ?></strong>
                    <p>
                        <?php
                        printf(
                            /* translators: 1: human-readable time, 2: masked key. */
                            esc_html__( 'Last sync: %1$s · API Key: %2$s', 'seojalwa' ),
                            $last_sync ? esc_html( human_time_diff( $last_sync, time() ) ) . ' ago' : esc_html__( 'never', 'seojalwa' ),
                            esc_html( $masked )
                        );
                        ?>
                    </p>
                    <button type="button" class="button" id="seojalwa-disconnect">
                        <?php esc_html_e( 'Disconnect', 'seojalwa' ); ?>
                    </button>
                </div>
            <?php else : ?>
                <div class="seojalwa-banner seojalwa-banner--info">
                    <p>
                        <?php esc_html_e( 'Enter your API key from your SEO Jalwa dashboard to connect this site.', 'seojalwa' ); ?>
                        <a href="https://seojalwa.com/dashboard/connections" target="_blank" rel="noopener">
                            <?php esc_html_e( 'Get your API key →', 'seojalwa' ); ?>
                        </a>
                    </p>
                </div>
            <?php endif; ?>

            <div id="seojalwa-connectivity" class="seojalwa-banner" style="display:none">
                <strong id="seojalwa-connectivity-text"></strong>
            </div>

            <h2><?php esc_html_e( 'API Key', 'seojalwa' ); ?></h2>
            <table class="form-table" role="presentation">
                <tr>
                    <th scope="row"><label for="seojalwa_api_key"><?php esc_html_e( 'SEO Jalwa API Key', 'seojalwa' ); ?></label></th>
                    <td>
                        <input type="password" id="seojalwa_api_key" class="regular-text"
                               placeholder="jalwa_live_..." value="<?php echo esc_attr( $api_key ); ?>"
                               <?php disabled( $connected, true ); ?> />
                        <button type="button" id="seojalwa-verify" class="button button-primary" <?php disabled( $connected, true ); ?>>
                            <?php esc_html_e( 'Verify & Connect', 'seojalwa' ); ?>
                        </button>
                        <span class="spinner" id="seojalwa-spinner" style="float:none;"></span>
                        <p id="seojalwa-message" class="description" aria-live="polite"></p>
                    </td>
                </tr>
            </table>

            <?php if ( $connected ) : ?>
            <form method="post" action="options.php">
                <?php settings_fields( 'seojalwa_settings' ); ?>
                <h2><?php esc_html_e( 'Article Publishing', 'seojalwa' ); ?></h2>
                <table class="form-table" role="presentation">
                    <tr>
                        <th scope="row"><label for="seojalwa_auto_publish"><?php esc_html_e( 'Auto-publish articles', 'seojalwa' ); ?></label></th>
                        <td>
                            <input type="checkbox" id="seojalwa_auto_publish" name="seojalwa_auto_publish" value="1"
                                   <?php checked( (int) get_option( 'seojalwa_auto_publish', 1 ), 1 ); ?> />
                            <span class="description"><?php esc_html_e( 'Automatically publish articles delivered by SEO Jalwa.', 'seojalwa' ); ?></span>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="seojalwa_category"><?php esc_html_e( 'Article category', 'seojalwa' ); ?></label></th>
                        <td>
                            <?php wp_dropdown_categories( array(
                                'name'             => 'seojalwa_category',
                                'id'               => 'seojalwa_category',
                                'selected'         => (int) get_option( 'seojalwa_category', 1 ),
                                'show_option_none' => false,
                                'hide_empty'       => 0,
                            ) ); ?>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="seojalwa_post_author"><?php esc_html_e( 'Article author', 'seojalwa' ); ?></label></th>
                        <td>
                            <?php wp_dropdown_users( array(
                                'name'     => 'seojalwa_post_author',
                                'id'       => 'seojalwa_post_author',
                                'selected' => (int) get_option( 'seojalwa_post_author', 1 ),
                                'who'      => 'authors',
                            ) ); ?>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="seojalwa_post_status"><?php esc_html_e( 'Post status', 'seojalwa' ); ?></label></th>
                        <td>
                            <select id="seojalwa_post_status" name="seojalwa_post_status">
                                <option value="publish" <?php selected( get_option( 'seojalwa_post_status', 'publish' ), 'publish' ); ?>>
                                    <?php esc_html_e( 'Published', 'seojalwa' ); ?>
                                </option>
                                <option value="draft" <?php selected( get_option( 'seojalwa_post_status', 'publish' ), 'draft' ); ?>>
                                    <?php esc_html_e( 'Draft', 'seojalwa' ); ?>
                                </option>
                            </select>
                        </td>
                    </tr>
                </table>
                <?php submit_button( __( 'Save Settings', 'seojalwa' ) ); ?>
            </form>

            <h2><?php esc_html_e( 'Plugin Info', 'seojalwa' ); ?></h2>
            <table class="widefat striped">
                <tbody>
                    <tr><th><?php esc_html_e( 'Plugin version', 'seojalwa' ); ?></th><td><?php echo esc_html( SEOJALWA_VERSION ); ?></td></tr>
                    <tr><th><?php esc_html_e( 'Connected since', 'seojalwa' ); ?></th>
                        <td><?php echo $connected_iso ? esc_html( gmdate( 'Y-m-d H:i', $connected_iso ) ) . ' UTC' : '—'; ?></td>
                    </tr>
                    <tr><th><?php esc_html_e( 'Articles published by SEO Jalwa', 'seojalwa' ); ?></th>
                        <td>
                            <?php echo esc_html( $count ); ?>
                            <a href="<?php echo esc_url( admin_url( 'edit.php?meta_key=_seojalwa_published&meta_value=1' ) ); ?>">
                                — <?php esc_html_e( 'View articles', 'seojalwa' ); ?>
                            </a>
                        </td>
                    </tr>
                </tbody>
            </table>
            <?php endif; ?>

            <h2><?php esc_html_e( 'Support', 'seojalwa' ); ?></h2>
            <p>
                <a href="https://seojalwa.com/contact" target="_blank" rel="noopener" class="button"><?php esc_html_e( 'Need help?', 'seojalwa' ); ?></a>
                <a href="https://seojalwa.com/docs/wordpress" target="_blank" rel="noopener" class="button"><?php esc_html_e( 'View documentation', 'seojalwa' ); ?></a>
            </p>
        </div>
        <?php
    }
}
