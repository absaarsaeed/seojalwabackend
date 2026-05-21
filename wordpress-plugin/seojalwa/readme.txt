=== SEO Jalwa ===
Contributors: seojalwa
Tags: seo, content, ai, automatic publishing
Requires at least: 5.0
Tested up to: 6.7
Requires PHP: 7.4
Stable tag: 1.0.2
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Automatically publish AI-written SEO articles to your WordPress site daily.

== Description ==

SEO Jalwa connects your WordPress site to the SEO Jalwa platform for automatic daily article publishing. Once connected:

* 1 SEO-optimized article published daily (or your chosen frequency)
* AI-generated hero images included
* Meta titles and descriptions set automatically
* Yoast SEO compatible
* Track article performance from your SEO Jalwa dashboard

== Installation ==

1. Upload the plugin files to `/wp-content/plugins/seojalwa/`, or install the plugin through the WordPress plugins screen directly.
2. Activate the plugin through the **Plugins** menu in WordPress.
3. Go to **Settings → SEO Jalwa**.
4. Enter your API key from your SEO Jalwa dashboard.
5. Click **Verify & Connect**.

== Frequently Asked Questions ==

= Where do I get my API key? =
Log in to seojalwa.com, go to **Dashboard → Connect Site → WordPress**. Your API key is shown there.

= How often are articles published? =
By default once per day. You can change the frequency in your SEO Jalwa dashboard under **Article Settings**.

= Will this affect my existing content? =
No. SEO Jalwa only creates new posts. It never modifies your existing content.

= Is Yoast SEO supported? =
Yes — meta title and meta description are written into Yoast's standard meta keys (`_yoast_wpseo_title`, `_yoast_wpseo_metadesc`).

== Changelog ==

= 1.0.2 =
* Intelligent category selection — uses the WordPress category chosen by
  SEO Jalwa's site analyser (per article) instead of always assigning the
  default category. Falls back to the default if the backend hasn't picked
  one yet.
* User-Agent bumped to `SEO Jalwa Plugin v1.0.2`.

= 1.0.1 =
* Robust connectivity diagnostics on the settings screen (green/red badge).
* Verbose `error_log` traces for verify (URL, key prefix, HTTP status, body).
* Verify call now sends `site_url`, `site_name`, `wp_version`, `php_version`,
  and `plugin_version` to the server so connection issues surface explicit
  error codes (`INVALID_API_KEY`, `SITE_URL_MISMATCH`, `CONNECTION_FAILED`,
  `PARSE_ERROR`).
* `User-Agent: SEO Jalwa Plugin v1.0.1` on every request.
* Bumped HTTP timeout from 10s to 30s for slower shared hosting.

= 1.0.0 =
* Initial release.
* Auto-publishing every 15 minutes (cron).
* Featured image sideloading from Cloudflare R2.
* Yoast SEO meta integration.
* Page-view tracking pixel for SEO Jalwa-published posts.
* In-app update notifications.
