# ABOUTME: Tests Prometheus textfile metric rendering for IGSync.
# ABOUTME: Imports the script with dummy boundary env vars and no network access.

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


IGSYNC_SOURCE = Path(
    os.environ.get("IGSYNC_SOURCE", Path(__file__).resolve().parents[1] / "igsync.py")
)


class MetricsTextfileTest(unittest.TestCase):
    def load_module(self):
        env = {
            "INSTAGRAM_ACCESS_TOKEN": "dummy",
            "WORDPRESS_SITE_URL": "https://example.com",
            "WORDPRESS_USERNAME": "dummy",
            "WORDPRESS_APPLICATION_PASSWORD": "dummy",
            "CATEGORY_ID": "1",
            "PROMETHEUS_PUSH_GATEWAY": "http://127.0.0.1:1",
        }
        old_env = os.environ.copy()
        old_cwd = os.getcwd()
        try:
            os.environ.update(env)
            with tempfile.TemporaryDirectory() as workdir:
                os.chdir(workdir)
                spec = importlib.util.spec_from_file_location("igsync", IGSYNC_SOURCE)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)

    def test_writes_prefixed_metrics_to_textfile_atomically(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "igsync.prom"
            module.write_textfile_metrics(path, 2, 1, 3)

            content = path.read_text()

        self.assertIn("# HELP igsync_last_success_timestamp", content)
        self.assertIn("# TYPE igsync_last_success_timestamp gauge", content)
        self.assertIn("igsync_new_instagram_posts 2.0\n", content)
        self.assertIn("igsync_posted_to_wordpress 1.0\n", content)
        self.assertIn("igsync_wordpress_pending_posts 3.0\n", content)
        self.assertNotRegex(content, r"(?m)^new_instagram_posts ")
        self.assertNotRegex(content, r"(?m)^posted_to_wordpress ")

    def test_includes_featured_image_in_post_content(self):
        module = self.load_module()

        content = module.build_content(
            [("media-1", "IMAGE", "media/image.jpg", None, None)],
            {"media-1": (42, "https://example.com/image.jpg")},
            "Caption",
        )

        self.assertIn('class="wp-image-42"', content)
        self.assertIn("Caption", content)

    def test_keeps_post_pending_when_expected_media_upload_fails(self):
        module = self.load_module()
        conn = module.init_db(":memory:")
        module.insert_post(
            conn,
            {
                "id": "post-1",
                "caption": "Caption",
                "media_type": "IMAGE",
                "permalink": "https://instagram.example/post-1",
                "timestamp": "2026-07-16T20:00:00Z",
            },
        )
        module.insert_media(
            conn,
            "media-1",
            "post-1",
            "IMAGE",
            "https://instagram.example/media-1.jpg",
        )

        with patch.object(
            module, "upload_media_to_wordpress", return_value=(None, None)
        ), patch.object(module, "create_wordpress_post") as create_post:
            posted_count = module.post_pending_to_wordpress(conn)

        self.assertEqual(posted_count, 0)
        self.assertEqual(module.get_pending_posts(conn)[0][0], "post-1")
        create_post.assert_not_called()
        conn.close()


if __name__ == "__main__":
    unittest.main()
