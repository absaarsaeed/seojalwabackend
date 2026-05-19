"""Catalogue of all third-party services manageable from the admin panel.

This is metadata only — the encrypted values live in `api_configs` collection.
Each entry describes:
  - key:         unique slug used in the URL and as the api_configs.key
  - label:       human label for the admin UI
  - section:     UI grouping
  - description: one-line "what it powers"
  - fields:      list of field specs (name, label, type, placeholder, required)
  - env_map:     dict mapping field-name -> environment variable name fallback
  - instructions:{title, steps[], url, note}
  - status_note: optional badge override (e.g. "pending_review")
"""

CATALOG: list[dict] = [
    # ------------------------------------------------------------ AI Models
    {
        "key": "openai",
        "label": "OpenAI (GPT-4o + DALL-E 3)",
        "section": "AI Models",
        "description": ("Powers AI Writer, article generation, and "
                        "hero image creation via DALL-E 3."),
        "fields": [
            {"name": "api_key", "label": "API Key",
             "type": "password", "placeholder": "sk-proj-...",
             "required": True},
        ],
        "env_map": {"api_key": "OPENAI_API_KEY"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to platform.openai.com",
                "Sign in → API Keys",
                "Click Create new secret key",
                "Copy the key immediately — OpenAI only shows it once",
                "Paste it above and click Save",
            ],
            "url": "https://platform.openai.com/api-keys",
            "note": ("Uses the same key for GPT-4o text and DALL-E 3 images. "
                     "Set billing limits at platform.openai.com/account/limits "
                     "to avoid unexpected charges."),
        },
    },
    {
        "key": "anthropic",
        "label": "Anthropic (Claude)",
        "section": "AI Models",
        "description": ("Used by AI Visibility to scan what Claude says about "
                        "your users' brands."),
        "fields": [
            {"name": "api_key", "label": "API Key",
             "type": "password", "placeholder": "sk-ant-...",
             "required": True},
        ],
        "env_map": {"api_key": "ANTHROPIC_API_KEY"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to console.anthropic.com",
                "Sign up or sign in",
                "Open API Keys → Create Key",
                "Copy and paste above",
            ],
            "url": "https://console.anthropic.com",
            "note": "",
        },
    },
    {
        "key": "gemini",
        "label": "Google Gemini",
        "section": "AI Models",
        "description": ("Used by AI Visibility to monitor brand mentions "
                        "on Gemini."),
        "fields": [
            {"name": "api_key", "label": "API Key",
             "type": "password", "placeholder": "AIza...",
             "required": True},
        ],
        "env_map": {"api_key": "GEMINI_API_KEY"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to aistudio.google.com",
                "Click Get API Key",
                "Create API key in new project",
                "Copy and paste above",
            ],
            "url": "https://aistudio.google.com/app/apikey",
            "note": "Free tier available with generous limits.",
        },
    },
    {
        "key": "perplexity",
        "label": "Perplexity",
        "section": "AI Models",
        "description": ("Used by AI Visibility to check brand visibility on "
                        "Perplexity search."),
        "fields": [
            {"name": "api_key", "label": "API Key",
             "type": "password", "placeholder": "pplx-...",
             "required": True},
        ],
        "env_map": {"api_key": "PERPLEXITY_API_KEY"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to perplexity.ai",
                "Sign in → Settings → API",
                "Generate API key",
                "Copy and paste above",
            ],
            "url": "https://www.perplexity.ai/settings/api",
            "note": "",
        },
    },

    # -------------------------------------------------------------- Email
    {
        "key": "sendgrid",
        "label": "SendGrid",
        "section": "Email",
        "description": ("Sends all transactional emails — welcome, password "
                        "resets, weekly reports, article notifications."),
        "fields": [
            {"name": "api_key", "label": "API Key",
             "type": "password", "placeholder": "SG....", "required": True},
            {"name": "from_email", "label": "From Email Address",
             "type": "email", "placeholder": "hello@yourdomain.com",
             "required": True},
        ],
        "env_map": {"api_key": "SENDGRID_API_KEY",
                     "from_email": "SENDGRID_FROM_EMAIL"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to sendgrid.com and sign up free",
                "Open Settings → API Keys → Create API Key",
                "Choose Full Access or Restricted (Mail Send only)",
                "Copy the key (shown once)",
                ("Verify your sending domain in "
                 "Settings → Sender Authentication"),
            ],
            "url": "https://app.sendgrid.com/settings/api_keys",
            "note": ("Free plan allows 100 emails/day. Verify your domain "
                     "for best deliverability."),
        },
    },
    {
        "key": "resend",
        "label": "Resend",
        "section": "Email",
        "description": ("Alternative email provider. Used if SendGrid is not "
                        "configured. Sends welcome emails, password resets, "
                        "weekly reports."),
        "fields": [
            {"name": "api_key", "label": "API Key",
             "type": "password", "placeholder": "re_...", "required": True},
            {"name": "from_email", "label": "From Email Address",
             "type": "email", "placeholder": "hello@seojalwa.com",
             "required": True},
        ],
        "env_map": {"api_key": "RESEND_API_KEY",
                     "from_email": "RESEND_FROM_EMAIL"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to resend.com and sign up free",
                "Go to API Keys → Create API Key",
                "Copy the key starting with re_",
                "Verify your domain in Domains section",
                "Paste the key above and Save",
            ],
            "url": "https://resend.com/api-keys",
            "note": ("Free plan allows 3,000 emails/month. Verify your "
                     "domain for best deliverability."),
        },
    },

    # -------------------------------------------------- SEO & Keywords
    {
        "key": "dataforseo",
        "label": "DataForSEO",
        "section": "SEO & Keywords",
        "description": ("Powers keyword research inside Auto Publish. Finds "
                        "the best topics with real search-volume data."),
        "fields": [
            {"name": "login", "label": "Login (Email)",
             "type": "email", "placeholder": "your@email.com",
             "required": True},
            {"name": "password", "label": "Password",
             "type": "password", "placeholder": "Your DataForSEO password",
             "required": True},
        ],
        "env_map": {"login": "DATAFORSEO_LOGIN",
                     "password": "DATAFORSEO_PASSWORD"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to dataforseo.com",
                "Click Sign Up (free to start)",
                ("After signup, your login credentials are your API "
                 "credentials"),
                "Add credit to your account (starts from $5, pay as you go)",
                "Enter your email and password above",
            ],
            "url": "https://dataforseo.com",
            "note": ("Pay-as-you-go pricing — approximately $0.001-0.01 "
                     "per keyword lookup."),
        },
    },

    # -------------------------------------------------- File Storage
    {
        "key": "cloudflare_r2",
        "label": "Cloudflare R2",
        "section": "File Storage",
        "description": ("Stores all generated images — article hero images "
                        "and social media post images."),
        "fields": [
            {"name": "account_id", "label": "Account ID",
             "type": "text", "placeholder": "abc123...", "required": True},
            {"name": "access_key_id", "label": "Access Key ID",
             "type": "text", "placeholder": "...", "required": True},
            {"name": "secret_access_key", "label": "Secret Access Key",
             "type": "password", "placeholder": "...", "required": True},
            {"name": "bucket_name", "label": "Bucket Name",
             "type": "text", "placeholder": "seojalwa-assets",
             "required": True},
            {"name": "public_url", "label": "Public Bucket URL",
             "type": "url", "placeholder": "https://pub-xxxxx.r2.dev",
             "required": True},
        ],
        "env_map": {
            "account_id": "R2_ACCOUNT_ID",
            "access_key_id": "R2_ACCESS_KEY_ID",
            "secret_access_key": "R2_SECRET_ACCESS_KEY",
            "bucket_name": "R2_BUCKET_NAME",
            "public_url": "R2_PUBLIC_URL",
        },
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to dash.cloudflare.com",
                "Open R2 Object Storage",
                "Create a bucket named seojalwa-assets",
                "Open Manage R2 API Tokens",
                ("Create new token with Object Read and Write permissions"),
                ("Copy Account ID, Access Key ID, and Secret Access Key"),
                ("For Public URL: bucket → Settings → enable Public Access "
                 "→ copy the public URL"),
            ],
            "url": "https://dash.cloudflare.com/r2",
            "note": ("R2 has no egress fees. 10 GB free storage per month. "
                     "Best choice for image storage."),
        },
    },

    # ------------------------------------------------- Google Services
    {
        "key": "google_oauth",
        "label": "Google OAuth (Search Console + YouTube)",
        "section": "Google Services",
        "description": ("Allows users to connect Google Search Console for "
                        "analytics; also used for YouTube posting."),
        "fields": [
            {"name": "client_id", "label": "Client ID",
             "type": "text",
             "placeholder": "xxxxx.apps.googleusercontent.com",
             "required": True},
            {"name": "client_secret", "label": "Client Secret",
             "type": "password", "placeholder": "GOCSPX-...",
             "required": True},
        ],
        "env_map": {"client_id": "GOOGLE_CLIENT_ID",
                     "client_secret": "GOOGLE_CLIENT_SECRET"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to console.cloud.google.com",
                "Create a new project named SEO Jalwa",
                "Open APIs & Services → Enable APIs",
                ("Enable: Google Search Console API and "
                 "YouTube Data API v3"),
                ("Go to Credentials → Create Credentials → "
                 "OAuth 2.0 Client ID"),
                "Application type: Web application",
                ("Add authorized redirect URI: "
                 "https://api.seojalwa.com/api/analytics/gsc/callback"),
                "Copy Client ID and Client Secret",
            ],
            "url": "https://console.cloud.google.com/apis/credentials",
            "note": ("Add your domain to OAuth consent screen and submit "
                     "for verification if you exceed 100 users."),
        },
    },

    # ------------------------------------------ Social Media OAuth Apps
    {
        "key": "meta",
        "label": "Meta (Instagram + Facebook)",
        "section": "Social Media OAuth Apps",
        "description": ("Allows users to connect Instagram and Facebook "
                        "accounts for automated posting."),
        "fields": [
            {"name": "app_id", "label": "App ID",
             "type": "text", "placeholder": "123456789...",
             "required": True},
            {"name": "app_secret", "label": "App Secret",
             "type": "password", "placeholder": "abc123...",
             "required": True},
        ],
        "env_map": {"app_id": "META_APP_ID",
                     "app_secret": "META_APP_SECRET"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to developers.facebook.com",
                "Click My Apps → Create App",
                "Select Business as app type",
                "Add Instagram Graph API and Pages API products",
                "Open App Review → Permissions",
                ("Request: instagram_content_publish and "
                 "pages_manage_posts"),
                "Copy App ID and App Secret from Basic Settings",
            ],
            "url": "https://developers.facebook.com/apps",
            "note": ("⚠️ App review takes 1-4 weeks — start immediately. "
                     "This is the longest approval of all platforms."),
        },
        "status_note": "pending_review",
    },
    {
        "key": "linkedin",
        "label": "LinkedIn",
        "section": "Social Media OAuth Apps",
        "description": ("Allows users to connect LinkedIn profiles and "
                        "company pages for automated posting."),
        "fields": [
            {"name": "client_id", "label": "Client ID",
             "type": "text", "placeholder": "86abc...", "required": True},
            {"name": "client_secret", "label": "Client Secret",
             "type": "password", "placeholder": "...", "required": True},
        ],
        "env_map": {"client_id": "LINKEDIN_CLIENT_ID",
                     "client_secret": "LINKEDIN_CLIENT_SECRET"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to linkedin.com/developers",
                "Click Create App",
                "Fill in app name and company page",
                ("Under Products, request Share on LinkedIn and "
                 "Sign In with LinkedIn"),
                "Copy Client ID and Client Secret from Auth tab",
            ],
            "url": "https://www.linkedin.com/developers/apps/new",
            "note": "Review usually takes 1-3 days.",
        },
    },
    {
        "key": "twitter",
        "label": "X / Twitter",
        "section": "Social Media OAuth Apps",
        "description": ("Allows users to connect X/Twitter accounts for "
                        "automated posting."),
        "fields": [
            {"name": "client_id", "label": "Client ID",
             "type": "text", "placeholder": "...", "required": True},
            {"name": "client_secret", "label": "Client Secret",
             "type": "password", "placeholder": "...", "required": True},
        ],
        "env_map": {"client_id": "TWITTER_CLIENT_ID",
                     "client_secret": "TWITTER_CLIENT_SECRET"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to developer.twitter.com",
                "Apply for developer account if not already approved",
                "Create a new Project and App",
                "Enable OAuth 2.0 in app settings",
                ("Add callback URL: "
                 "https://api.seojalwa.com/api/social/callback/twitter"),
                "Copy Client ID and Client Secret",
            ],
            "url": "https://developer.twitter.com/en/portal/dashboard",
            "note": ("⚠️ Posting via API requires the Basic plan at "
                     "$100/month. Free tier is read-only."),
        },
    },
    {
        "key": "pinterest",
        "label": "Pinterest",
        "section": "Social Media OAuth Apps",
        "description": ("Allows users to connect Pinterest accounts for "
                        "automated pin posting."),
        "fields": [
            {"name": "app_id", "label": "App ID",
             "type": "text", "placeholder": "...", "required": True},
            {"name": "app_secret", "label": "App Secret",
             "type": "password", "placeholder": "...", "required": True},
        ],
        "env_map": {"app_id": "PINTEREST_APP_ID",
                     "app_secret": "PINTEREST_APP_SECRET"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to developers.pinterest.com",
                "Click My Apps → Create App",
                "Fill in app details",
                "Request ads:read and pins:write permissions",
                "Submit for review",
                "Copy App ID and App Secret",
            ],
            "url": "https://developers.pinterest.com/apps",
            "note": "Review takes 3-7 days.",
        },
    },

    # ------------------------------------------------------------- Payments
    {
        "key": "lemonsqueezy",
        "label": "LemonSqueezy",
        "section": "Payments",
        "description": ("Handles all subscription billing — plan purchases, "
                        "upgrades, downgrades, and payment processing."),
        "fields": [
            {"name": "api_key", "label": "API Key",
             "type": "password", "placeholder": "eyJ0eXAi...",
             "required": True},
            {"name": "store_id", "label": "Store ID",
             "type": "text", "placeholder": "12345", "required": True},
            {"name": "webhook_secret", "label": "Webhook Secret",
             "type": "password", "placeholder": "...", "required": True},
        ],
        "env_map": {"api_key": "LEMONSQUEEZY_API_KEY",
                     "store_id": "LEMONSQUEEZY_STORE_ID",
                     "webhook_secret": "LEMONSQUEEZY_WEBHOOK_SECRET"},
        "instructions": {
            "title": "How to get this key",
            "steps": [
                "Go to app.lemonsqueezy.com",
                ("Complete business verification (required before "
                 "accepting payments)"),
                "Open Settings → API → Create API Key",
                "Open Settings → Store → copy your Store ID",
                ("Open Settings → Webhooks → Add webhook URL: "
                 "https://api.seojalwa.com/api/billing/webhook"),
                "Copy the webhook signing secret",
            ],
            "url": "https://app.lemonsqueezy.com/settings/api",
            "note": ("Business verification can take 1-2 weeks — apply "
                     "early."),
        },
    },
]


CATALOG_BY_KEY: dict[str, dict] = {entry["key"]: entry for entry in CATALOG}


def get_entry(key: str) -> dict | None:
    return CATALOG_BY_KEY.get(key.lower())


def all_keys() -> list[str]:
    return [e["key"] for e in CATALOG]
