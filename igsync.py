#!/usr/bin/env python3

import argparse
import os
import requests
import sqlite3
from dotenv import load_dotenv
from pathlib import Path
from requests.auth import HTTPBasicAuth
from slugify import slugify

load_dotenv()

INSTAGRAM_ACCESS_TOKEN = os.environ['INSTAGRAM_ACCESS_TOKEN']
WORDPRESS_SITE_URL = os.environ['WORDPRESS_SITE_URL']
WORDPRESS_USERNAME = os.environ['WORDPRESS_USERNAME']
WORDPRESS_APPLICATION_PASSWORD = os.environ['WORDPRESS_APPLICATION_PASSWORD']
DB_PATH = 'instagram_posts.db'

# Ensure media directory exists
Path('media').mkdir(exist_ok=True)

### Database Setup

def init_db(db_path):
    """Initialize SQLite database with posts and media tables."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS posts
                 (id TEXT PRIMARY KEY, caption TEXT, media_type TEXT, permalink TEXT, posted_to_wp INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS media
                 (media_id TEXT PRIMARY KEY, post_id TEXT, media_type TEXT, media_url TEXT, local_path TEXT,
                  FOREIGN KEY(post_id) REFERENCES posts(id))''')
    conn.commit()
    return conn

### Instagram Functions

def fetch_instagram_posts(access_token, conn, verbose=False):
    """Fetch new Instagram posts with pagination."""
    c = conn.cursor()
    c.execute("SELECT id FROM posts")
    existing_ids = set(row[0] for row in c.fetchall())
    posts = []
    url = f'https://graph.instagram.com/me/media?fields=id,caption,media_type,media_url,permalink&access_token={access_token}'
    page = 1
    while url:
        if verbose:
            print(f"Fetching page {page}...")
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Error fetching posts: {response.status_code}")
            break
        data = response.json()
        page_posts = data['data']
        new_posts = [post for post in page_posts if post['id'] not in existing_ids]
        posts.extend(new_posts)
        if not new_posts or 'next' not in data['paging']:
            break
        url = data['paging']['next']
        page += 1
    if verbose:
        print(f"Fetched {len(posts)} new posts from {page} pages")
    elif posts:
        print(f"Found {len(posts)} new posts")
    return posts

def fetch_children(post_id, access_token):
    """Fetch media children for carousel posts."""
    url = f'https://graph.instagram.com/{post_id}/children?fields=id,media_type,media_url&access_token={access_token}'
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()['data']
    print(f"Error fetching children for post {post_id}: {response.status_code}")
    return []

def download_media(media_url, local_path, verbose=False):
    """Download media from Instagram."""
    if Path(local_path).exists():
        if verbose:
            print(f"Media {local_path} already exists, skipping download")
        return
    if verbose:
        print(f"Downloading {media_url} to {local_path}")
    response = requests.get(media_url, stream=True)
    if response.status_code == 200:
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        if verbose:
            print(f"Downloaded {local_path}")
    else:
        print(f"Error downloading {media_url}: {response.status_code}")

### Database Helpers

def insert_post(conn, post):
    """Insert a post into the database."""
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO posts (id, caption, media_type, permalink, posted_to_wp) VALUES (?, ?, ?, ?, 0)",
              (post['id'], post.get('caption', ''), post['media_type'], post['permalink']))
    conn.commit()

def insert_media(conn, media_id, post_id, media_type, media_url):
    """Insert media metadata into the database."""
    local_path = get_local_path(media_id, media_type)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO media (media_id, post_id, media_type, media_url, local_path) VALUES (?, ?, ?, ?, ?)",
              (media_id, post_id, media_type, media_url, local_path))
    conn.commit()

def get_local_path(media_id, media_type):
    """Generate local file path for media."""
    ext = '.jpg' if media_type == 'IMAGE' else '.mp4'
    return f'media/{media_id}{ext}'

### WordPress Functions

def get_pending_posts(conn):
    """Retrieve posts not yet posted to WordPress."""
    c = conn.cursor()
    c.execute("SELECT id, caption, media_type FROM posts WHERE posted_to_wp = 0")
    return c.fetchall()

def get_media_for_post(conn, post_id):
    """Get media items for a specific post."""
    c = conn.cursor()
    c.execute("SELECT media_id, media_type, local_path FROM media WHERE post_id = ?", (post_id,))
    return c.fetchall()

def upload_media_to_wordpress(local_path, media_type, verbose=False):
    """Upload media to WordPress."""
    content_type = 'image/jpeg' if media_type == 'IMAGE' else 'video/mp4'
    filename = 'instagram_image.jpg' if media_type == 'IMAGE' else 'instagram_video.mp4'
    headers = {
        'Content-Type': content_type,
        'Content-Disposition': f'attachment; filename="{filename}"'
    }
    if verbose:
        print(f"Uploading {local_path} to WordPress")
    with open(local_path, 'rb') as f:
        response = requests.post(
            f'{WORDPRESS_SITE_URL}/wp-json/wp/v2/media',
            headers=headers,
            data=f,
            auth=HTTPBasicAuth(WORDPRESS_USERNAME, WORDPRESS_APPLICATION_PASSWORD)
        )
    if response.status_code == 201:
        data = response.json()
        if verbose:
            print(f"Uploaded media ID {data['id']}")
        return data['id'], data['source_url']
    print(f"Error uploading {local_path}: {response.status_code}")
    return None, None

def create_wordpress_post(title, content, slug, featured_media, verbose=False):
    """Create a post on WordPress."""
    post_data = {'title': title, 'content': content, 'slug': slug, 'status': 'publish'}
    if featured_media:
        post_data['featured_media'] = featured_media
    if verbose:
        print(f"Creating post with title '{title}'")
    response = requests.post(
        f'{WORDPRESS_SITE_URL}/wp-json/wp/v2/posts',
        headers={'Content-Type': 'application/json'},
        json=post_data,
        auth=HTTPBasicAuth(WORDPRESS_USERNAME, WORDPRESS_APPLICATION_PASSWORD)
    )
    if response.status_code == 201:
        if verbose:
            print("Post created successfully")
        return True
    print(f"Error creating post: {response.status_code} {response.text}")
    return False

def mark_post_as_posted(conn, post_id):
    """Mark a post as posted to WordPress."""
    c = conn.cursor()
    c.execute("UPDATE posts SET posted_to_wp = 1 WHERE id = ?", (post_id,))
    conn.commit()

### Main Workflow

def fetch_and_store_instagram_posts(conn, verbose=False):
    """Fetch and store new Instagram posts."""
    posts = fetch_instagram_posts(INSTAGRAM_ACCESS_TOKEN, conn, verbose)
    for post in posts:
        if verbose:
            print(f"Processing post {post['id']}")
        insert_post(conn, post)
        if post['media_type'] == 'CAROUSEL_ALBUM':
            children = fetch_children(post['id'], INSTAGRAM_ACCESS_TOKEN)
            for child in children:
                insert_media(conn, child['id'], post['id'], child['media_type'], child['media_url'])
                download_media(child['media_url'], get_local_path(child['id'], child['media_type']), verbose)
        else:
            insert_media(conn, post['id'], post['id'], post['media_type'], post['media_url'])
            download_media(post['media_url'], get_local_path(post['id'], post['media_type']), verbose)
    if verbose:
        print(f"Stored {len(posts)} new posts")

def post_pending_to_wordpress(conn, verbose=False, test_mode=False):
    """Post pending Instagram posts to WordPress."""
    pending_posts = get_pending_posts(conn)
    if not verbose and pending_posts:
        print(f"Found {len(pending_posts)} pending posts to process")
    if test_mode:
        pending_posts = pending_posts[:1]
    for post in pending_posts:
        post_id, caption, media_type = post
        # Extract title from caption
        caption = caption or ''  # Handle None captions
        title = caption.split('\n', 1)[0] if '\n' in caption else caption
        if not title:  # Handle empty captions
            title = 'Untitled'
        slug = slugify("Photo " + title)
        if verbose:
            print(f"Posting post {post_id} to WordPress")
        media_list = get_media_for_post(conn, post_id)
        wp_media_map = {}
        for media in media_list:
            media_id, media_type, local_path = media
            wp_media_id, wp_url = upload_media_to_wordpress(local_path, media_type, verbose)
            if wp_media_id:
                wp_media_map[media_id] = (wp_media_id, wp_url)
        content = ''
        for media in media_list:
            media_id, media_type, _ = media
            if media_id in wp_media_map:
                wp_media_id, wp_url = wp_media_map[media_id]
                content += f'<img src="{wp_url}"><br>' if media_type == 'IMAGE' else f'[video id="{wp_media_id}"]<br>'
        content += caption
        first_image_id = next((m[0] for m in media_list if m[1] == 'IMAGE'), None)
        featured_media = wp_media_map.get(first_image_id, (None, None))[0] if first_image_id else None
        if create_wordpress_post(title, content, slug, featured_media, verbose):
            if not test_mode:
                mark_post_as_posted(conn, post_id)
            else:
                print(f"Test post created for post_id: {post_id}. Not marking as posted.")
            if verbose:
                print(f"Successfully posted post {post_id}")

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Sync Instagram posts to WordPress")
    parser.add_argument('--fetch-only', action='store_true', help="Only fetch from Instagram")
    parser.add_argument('--post-only', action='store_true', help="Only post to WordPress")
    parser.add_argument('--verbose', action='store_true', help="Show detailed progress")
    parser.add_argument('--test-post', action='store_true', help="Post one pending post to WordPress without marking it as posted")
    args = parser.parse_args()

    # Determine actions
    fetch = not args.post_only
    post = not args.fetch_only
    verbose = args.verbose

    # Initialize database connection
    conn = init_db(DB_PATH)
    try:
        if fetch:
            print("Fetching new posts from Instagram...")
            fetch_and_store_instagram_posts(conn, verbose)
        if post:
            print("Posting pending posts to WordPress...")
            post_pending_to_wordpress(conn, verbose, args.test_post)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
