#!/usr/bin/env python3

import argparse
import logging
import os
import re
import requests
import sqlite3
from dateutil import parser
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
from requests.auth import HTTPBasicAuth
from slugify import slugify

logging.basicConfig(format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

INSTAGRAM_ACCESS_TOKEN = os.environ["INSTAGRAM_ACCESS_TOKEN"]
WORDPRESS_SITE_URL = os.environ["WORDPRESS_SITE_URL"]
WORDPRESS_USERNAME = os.environ["WORDPRESS_USERNAME"]
WORDPRESS_APPLICATION_PASSWORD = os.environ["WORDPRESS_APPLICATION_PASSWORD"]
CATEGORY_ID = os.environ["CATEGORY_ID"]
PROMETHEUS_PUSH_GATEWAY = os.environ["PROMETHEUS_PUSH_GATEWAY"]
DB_PATH = "instagram_posts.db"

Path("media").mkdir(exist_ok=True)


def init_db(db_path):
    """Initialize SQLite database with posts and media tables."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS posts
                 (id TEXT PRIMARY KEY, caption TEXT, media_type TEXT, permalink TEXT, timestamp TEXT, posted_to_wp INTEGER DEFAULT 0)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS media
                 (media_id TEXT PRIMARY KEY, post_id TEXT, media_type TEXT, media_url TEXT, local_path TEXT,
                  wp_media_id INTEGER, wp_url TEXT,
                  FOREIGN KEY(post_id) REFERENCES posts(id))"""
    )
    conn.commit()
    return conn


def fetch_instagram_posts(access_token, conn):
    """Fetch new Instagram posts with pagination."""
    c = conn.cursor()
    c.execute("SELECT id FROM posts")
    existing_ids = set(row[0] for row in c.fetchall())
    posts = []
    url = f"https://graph.instagram.com/me/media?fields=id,caption,media_type,media_url,permalink,timestamp&access_token={access_token}"
    page = 1
    while url:
        logger.debug(f"Fetching page {page}...")
        response = requests.get(url)
        if response.status_code != 200:
            logger.debug(f"Error fetching posts: {response.status_code}")
            break
        data = response.json()
        page_posts = data["data"]
        new_posts = [post for post in page_posts if post["id"] not in existing_ids]
        posts.extend(new_posts)
        if not new_posts or "next" not in data["paging"]:
            break
        url = data["paging"]["next"]
        page += 1
    logger.info(f"Fetched {len(posts)} new posts from {page} pages")
    return posts


def fetch_children(post_id, access_token):
    """Fetch media children for carousel posts."""
    url = f"https://graph.instagram.com/{post_id}/children?fields=id,media_type,media_url&access_token={access_token}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()["data"]
    logger.debug(f"Error fetching children for post {post_id}: {response.status_code}")
    return []


def download_media(media_url, local_path):
    """Download media from Instagram."""
    if Path(local_path).exists():
        logger.debug(f"Media {local_path} already exists, skipping download")
        return
    logger.debug(f"Downloading {media_url} to {local_path}")
    response = requests.get(media_url, stream=True)
    if response.status_code == 200:
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        logger.debug(f"Downloaded {local_path}")
    else:
        logger.error(f"Error downloading {media_url}: {response.status_code}")


def insert_post(conn, post):
    """Insert a post into the database."""
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO posts (id, caption, media_type, permalink, timestamp, posted_to_wp) VALUES (?, ?, ?, ?, ?, 0)",
        (
            post["id"],
            post.get("caption", ""),
            post["media_type"],
            post["permalink"],
            post.get("timestamp", ""),
        ),
    )
    conn.commit()


def insert_media(conn, media_id, post_id, media_type, media_url):
    """Insert media metadata into the database."""
    local_path = get_local_path(media_id, media_type)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO media (media_id, post_id, media_type, media_url, local_path) VALUES (?, ?, ?, ?, ?)",
        (media_id, post_id, media_type, media_url, local_path),
    )
    conn.commit()


def get_local_path(media_id, media_type):
    """Generate local file path for media."""
    ext = ".jpg" if media_type == "IMAGE" else ".mp4"
    return f"media/{media_id}{ext}"


def extract_tags(caption):
    """Extract hashtags from the caption."""
    return re.findall(r"#\w+", caption)


def remove_tags(caption):
    """Remove hashtags from the caption."""
    return re.sub(r"#\w+", "", caption).strip()


def get_or_create_tag(tag_name, auth, wordpress_url):
    """Get tag ID if it exists, or create it and return the new ID."""
    tag_name = tag_name.lstrip("#")
    response = requests.get(
        f"{wordpress_url}/wp-json/wp/v2/tags", params={"search": tag_name}, auth=auth
    )
    if response.status_code == 200:
        tags = response.json()
        for tag in tags:
            if tag["name"].lower() == tag_name.lower():
                return tag["id"]
    # Tag doesn't exist, create it
    response = requests.post(
        f"{wordpress_url}/wp-json/wp/v2/tags", json={"name": tag_name}, auth=auth
    )
    if response.status_code == 201:
        return response.json()["id"]
    logger.debug(f"Error creating tag {tag_name}: {response.status_code}")
    return None


def handle_media(conn, media_list):
    """Handle media uploads and return a mapping of media IDs to WordPress IDs and URLs."""
    wp_media_map = {}
    c = conn.cursor()
    for media in media_list:
        media_id, media_type, local_path, wp_media_id, wp_url = media
        if wp_media_id:
            logger.debug(f"Using existing media {media_id} with ID {wp_media_id}")
            wp_media_map[media_id] = (wp_media_id, wp_url)
        else:
            wp_media_id, wp_url = upload_media_to_wordpress(local_path, media_type)
            if wp_media_id:
                c.execute(
                    "UPDATE media SET wp_media_id = ?, wp_url = ? WHERE media_id = ?",
                    (wp_media_id, wp_url, media_id),
                )
                conn.commit()
                logger.debug(f"Uploaded media {media_id} with ID {wp_media_id}")
                wp_media_map[media_id] = (wp_media_id, wp_url)
    return wp_media_map


def format_caption(caption):
    """Format caption by removing tags and adding paragraph blocks."""
    caption = remove_tags(caption)
    lines = caption.split("\n")
    formatted = ""
    for line in lines:
        if line.strip():
            formatted += (
                f"<!-- wp:paragraph --><p>{line.strip()}</p><!-- /wp:paragraph -->"
            )
    return formatted


def build_content(media_list, wp_media_map, caption, first_image_id):
    """Build the post content using block markup, excluding the featured image."""
    content = ""
    for media in media_list:
        media_id, media_type, _, _, _ = media
        if media_id in wp_media_map and media_id != first_image_id:
            wp_media_id, wp_url = wp_media_map[media_id]
            if media_type == "IMAGE":
                content += f'<!-- wp:image {{"id":{wp_media_id}}} --><figure class="wp-block-image"><img src="{wp_url}" alt="" class="wp-image-{wp_media_id}"/></figure><!-- /wp:image -->'
            elif media_type == "VIDEO":
                content += f'<!-- wp:video {{"id":{wp_media_id}}} --><figure class="wp-block-video"><video controls src="{wp_url}"></video></figure><!-- /wp:video -->'
    content += format_caption(caption)
    return content


def get_pending_posts(conn):
    """Retrieve posts not yet posted to WordPress."""
    c = conn.cursor()
    c.execute(
        "SELECT id, caption, media_type, timestamp FROM posts WHERE posted_to_wp = 0"
    )
    return c.fetchall()


def get_media_for_post(conn, post_id):
    """Get media items for a post, including wp_media_id and wp_url."""
    c = conn.cursor()
    c.execute(
        "SELECT media_id, media_type, local_path, wp_media_id, wp_url FROM media WHERE post_id = ?",
        (post_id,),
    )
    return c.fetchall()


def upload_media_to_wordpress(local_path, media_type):
    """Upload media to WordPress."""
    content_type = "image/jpeg" if media_type == "IMAGE" else "video/mp4"
    filename = "instagram_image.jpg" if media_type == "IMAGE" else "instagram_video.mp4"
    headers = {
        "Content-Type": content_type,
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    logger.debug(f"Uploading {local_path} to WordPress")
    with open(local_path, "rb") as f:
        response = requests.post(
            f"{WORDPRESS_SITE_URL}/wp-json/wp/v2/media",
            headers=headers,
            data=f,
            auth=HTTPBasicAuth(WORDPRESS_USERNAME, WORDPRESS_APPLICATION_PASSWORD),
        )
    if response.status_code == 201:
        data = response.json()
        logger.debug(f"Uploaded media ID {data['id']}")
        return data["id"], data["source_url"]
    logger.error(f"Error uploading {local_path}: {response.status_code}")
    return None, None


def create_wordpress_post(title, content, slug, featured_media, tag_ids, timestamp):
    """Create a post on WordPress."""
    if timestamp:
        dt = parser.parse(timestamp)
        formatted_timestamp = dt.isoformat()
    else:
        formatted_timestamp = datetime.now(timezone.utc).isoformat()
    post_data = {
        "title": title,
        "content": content,
        "slug": slug,
        "status": "publish",
        "categories": [CATEGORY_ID],
        "tags": tag_ids,
        "date": formatted_timestamp,
    }
    if featured_media:
        post_data["featured_media"] = featured_media
    logger.debug(f"Creating post with title '{title}' and date '{formatted_timestamp}'")
    response = requests.post(
        f"{WORDPRESS_SITE_URL}/wp-json/wp/v2/posts",
        headers={"Content-Type": "application/json"},
        json=post_data,
        auth=HTTPBasicAuth(WORDPRESS_USERNAME, WORDPRESS_APPLICATION_PASSWORD),
    )
    if response.status_code == 201:
        logger.debug("Post created successfully")
        return True
    logger.error(f"Error creating post: {response.status_code} {response.text}")
    return False


def reset_media_uploads(conn):
    """Reset media upload records by setting wp_media_id and wp_url to NULL."""
    c = conn.cursor()
    c.execute("UPDATE media SET wp_media_id = NULL, wp_url = NULL")
    conn.commit()
    logger.info("Reset all media upload records.")


def mark_post_as_posted(conn, post_id):
    """Mark a post as posted to WordPress."""
    c = conn.cursor()
    c.execute("UPDATE posts SET posted_to_wp = 1 WHERE id = ?", (post_id,))
    conn.commit()


def fetch_and_store_instagram_posts(conn):
    """Fetch and store new Instagram posts, returning the count."""
    posts = fetch_instagram_posts(INSTAGRAM_ACCESS_TOKEN, conn)
    for post in posts:
        logger.debug(f"Processing post {post['id']}")
        insert_post(conn, post)
        if post["media_type"] == "CAROUSEL_ALBUM":
            children = fetch_children(post["id"], INSTAGRAM_ACCESS_TOKEN)
            for child in children:
                insert_media(
                    conn,
                    child["id"],
                    post["id"],
                    child["media_type"],
                    child["media_url"],
                )
                download_media(
                    child["media_url"], get_local_path(child["id"], child["media_type"])
                )
        else:
            insert_media(
                conn, post["id"], post["id"], post["media_type"], post["media_url"]
            )
            download_media(
                post["media_url"], get_local_path(post["id"], post["media_type"])
            )
    logger.debug(f"Stored {len(posts)} new posts")
    return len(posts)


def post_pending_to_wordpress(conn, test_mode=False):
    """Post pending Instagram posts to WordPress, returning the count."""
    pending_posts = get_pending_posts(conn)
    if pending_posts:
        logger.info(f"Found {len(pending_posts)} pending posts to process")
    if test_mode:
        pending_posts = pending_posts[:1]

    auth = HTTPBasicAuth(WORDPRESS_USERNAME, WORDPRESS_APPLICATION_PASSWORD)
    posted_count = 0
    for post in pending_posts:
        post_id, caption, media_type, timestamp = post
        caption = caption or ""
        title = caption.split("\n", 1)[0] if "\n" in caption else caption
        if not title:
            title = "Untitled"
        slug = slugify("Photo " + title)
        logger.debug(f"Posting post {post_id} to WordPress")

        media_list = get_media_for_post(conn, post_id)
        wp_media_map = handle_media(conn, media_list)

        first_image_id = next((m[0] for m in media_list if m[1] == "IMAGE"), None)
        featured_media = (
            wp_media_map.get(first_image_id, (None, None))[0]
            if first_image_id
            else None
        )

        content = build_content(media_list, wp_media_map, caption, first_image_id)
        tags = extract_tags(caption)
        tag_ids = [
            tag_id
            for tag in tags
            if (tag_id := get_or_create_tag(tag, auth, WORDPRESS_SITE_URL))
        ]

        if create_wordpress_post(
            title, content, slug, featured_media, tag_ids, timestamp
        ):
            if not test_mode:
                mark_post_as_posted(conn, post_id)
            else:
                logger.info(
                    f"Test post created for post_id: {post_id}. Not marking as posted."
                )
            posted_count += 1
            logger.debug(f"Successfully posted post {post_id}")
    return posted_count


def main():
    parser = argparse.ArgumentParser(description="Sync Instagram posts to WordPress")
    parser.add_argument(
        "--fetch-only", action="store_true", help="Only fetch from Instagram"
    )
    parser.add_argument(
        "--post-only", action="store_true", help="Only post to WordPress"
    )
    parser.add_argument("--verbose", action="store_true", help="Show detailed progress")
    parser.add_argument(
        "--test-post",
        action="store_true",
        help="Post one pending post to WordPress without marking it as posted",
    )
    parser.add_argument(
        "--reset-media", action="store_true", help="Reset media upload records"
    )
    parser.add_argument(
        "--no-prometheus",
        action="store_true",
        help="Disable Prometheus metrics pushing",
    )
    args = parser.parse_args()

    fetch = not args.post_only
    post = not args.fetch_only
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    conn = init_db(DB_PATH)
    new_instagram_posts = 0
    posted_to_wordpress = 0
    try:
        if fetch:
            logger.info("Fetching new posts from Instagram...")
            new_instagram_posts = fetch_and_store_instagram_posts(conn)
        if post:
            logger.info("Posting pending posts to WordPress...")
            if args.reset_media:
                reset_media_uploads(conn)
            posted_to_wordpress = post_pending_to_wordpress(conn, args.test_post)
    finally:
        conn.close()

    if not args.no_prometheus:
        registry = CollectorRegistry()
        last_success = Gauge(
            "last_success",
            "Last time the script successfully completed",
            registry=registry,
        )
        last_success.set_to_current_time()
        instagram_gauge = Gauge(
            "new_instagram_posts",
            "Number of new posts fetched from Instagram",
            registry=registry,
        )
        instagram_gauge.set(new_instagram_posts)
        wordpress_gauge = Gauge(
            "posted_to_wordpress",
            "Number of posts successfully posted to WordPress",
            registry=registry,
        )
        wordpress_gauge.set(posted_to_wordpress)
        push_to_gateway(
            PROMETHEUS_PUSH_GATEWAY, job="instagram_sync", registry=registry
        )
        logger.debug(f"Pushed metrics to {PROMETHEUS_PUSH_GATEWAY}")


if __name__ == "__main__":
    main()
