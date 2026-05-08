# ABOUTME: Tests Prometheus textfile metric rendering for IGSync.
# ABOUTME: Imports the script with dummy boundary env vars and no network access.

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
