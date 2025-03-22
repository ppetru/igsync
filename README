igsync fetches content from Instagram and posts a copy of it to a WordPress
blog. The WordPress posts are standalone and do not rely on embedding
Instagram.

## Features

- Fetches new posts via the Instagram Graph API
- Posts to WordPress with original timestamps, media, and tags
- Avoids duplicate media uploads to WordPress
- Pushes monitoring metrics to Prometheus

## Prerequisites

- **Python 3.6 or later**
- A **WordPress site** with the REST API enabled
- An **Instagram account** with access to the Instagram Graph API
- A **Prometheus push gateway** (optional, for metrics)

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ppetru/igsync.git
   ```
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
### Nix users
   ```bash
   nix develop
   ```
3. **Set configuration**:
   - Create a `.env` file in the project root.
   - Copy the example from `.env.example` and fill in your details.

## Usage

```bash
python igsync.py [options]
```

Use `--help` to see the available options.

## Configuration

The script uses environment variables stored in the `.env` file.

- `INSTAGRAM_ACCESS_TOKEN`: Instagram Graph API access token
- `WORDPRESS_SITE_URL`: WordPress site URL
- `WORDPRESS_USERNAME`: WordPress username
- `WORDPRESS_APPLICATION_PASSWORD`: WordPress application password
- `CATEGORY_ID`: The ID of the WordPress category to set on posts
- `PROMETHEUS_PUSH_GATEWAY`: Prometheus push gateway URL (optional)

You can also tweak the script’s behavior by editing `igsync.py` directly.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Found a bug or have a suggestion? Please open an issue or submit a pull request on GitHub. Contributions are welcome!
