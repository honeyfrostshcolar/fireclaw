from __future__ import annotations

import re
import unittest
from pathlib import Path
from html.parser import HTMLParser


WEB_CONSOLE_DIR = Path(__file__).resolve().parent.parent / "src" / "fireclaw_core" / "web_console"
INDEX_HTML_PATH = WEB_CONSOLE_DIR / "index.html"
STYLE_CSS_PATH = WEB_CONSOLE_DIR / "style.css"
APP_JS_PATH = WEB_CONSOLE_DIR / "app.js"


class HTMLIDExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.tags: list[str] = []
        self.classes: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for attr, val in attrs:
            if attr == "id" and val:
                self.ids.add(val)
            elif attr == "class" and val:
                for cls in val.split():
                    self.classes.add(cls)


class TestFriendlyWebIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html_content = INDEX_HTML_PATH.read_text(encoding="utf-8")
        cls.css_content = STYLE_CSS_PATH.read_text(encoding="utf-8")
        cls.js_content = APP_JS_PATH.read_text(encoding="utf-8")

        parser = HTMLIDExtractor()
        parser.feed(cls.html_content)
        cls.extracted_ids = parser.ids
        cls.extracted_classes = parser.classes

    def test_html_modal_enhanced_elements(self):
        """Verify index.html error modal contains suggested-actions and technical-details panels."""
        self.assertIn("error-modal", self.extracted_ids, "#error-modal must exist")
        self.assertIn("modal-suggested-actions", self.extracted_ids, "#modal-suggested-actions container must exist")
        self.assertIn("modal-technical-details", self.extracted_ids, "#modal-technical-details collapsible container must exist")
        self.assertIn("modal-tech-content", self.extracted_ids, "#modal-tech-content pre element must exist")

        # Verify details/summary structure
        self.assertIn("<details", self.html_content, "<details> element must be used for collapsible panel")
        self.assertIn("<summary", self.html_content, "<summary> element must be used for collapsible panel header")
        self.assertIn("查看技术详情", self.html_content, "Summary text must mention 查看技术详情")

    def test_css_friendly_error_styles(self):
        """Verify style.css contains styles for enhanced toast, suggested actions, and technical details."""
        self.assertIn(".toast-enhanced", self.css_content, ".toast-enhanced class must be styled")
        self.assertTrue(
            "#modal-suggested-actions" in self.css_content or ".modal-suggested-actions" in self.css_content,
            "modal suggested actions container must be styled in style.css"
        )
        self.assertTrue(
            "#modal-technical-details" in self.css_content or ".modal-technical-details" in self.css_content,
            "modal technical details panel must be styled in style.css"
        )
        self.assertIn(".btn-suggested-action", self.css_content, ".btn-suggested-action button must be styled")

    def test_js_methods_defined(self):
        """Verify app.js implements showFriendlyError, showEnhancedToast, and handleApiError."""
        self.assertIn("showFriendlyError", self.js_content, "showFriendlyError method must be implemented in app.js")
        self.assertIn("showEnhancedToast", self.js_content, "showEnhancedToast method must be implemented in app.js")
        self.assertIn("handleApiError", self.js_content, "handleApiError method must be implemented in app.js")

    def test_js_show_friendly_error_severity_routing(self):
        """Verify showFriendlyError handles critical (modal), warning (enhanced toast), and info (toast)."""
        self.assertRegex(
            self.js_content,
            r"showFriendlyError\s*\([^)]*\)\s*\{",
            "showFriendlyError must be a method in WebConsoleApp"
        )
        # Should check severity: critical, warning, info
        self.assertIn("critical", self.js_content)
        self.assertIn("warning", self.js_content)
        self.assertIn("showEnhancedToast", self.js_content)

    def test_js_enhanced_toast_features(self):
        """Verify showEnhancedToast builds a rich toast with detail link and 8s duration."""
        self.assertRegex(
            self.js_content,
            r"showEnhancedToast\s*\(",
            "showEnhancedToast must be defined with parameters"
        )
        self.assertIn("查看详情", self.js_content, "Enhanced toast must provide a 查看详情 link/button")
        self.assertIn("8000", self.js_content, "Enhanced toast should have an extended duration (8s)")

    def test_js_error_modal_handles_structured_data(self):
        """Verify showErrorModal handles suggested_actions and technical_details."""
        self.assertIn("modal-suggested-actions", self.js_content)
        self.assertIn("modal-tech-content", self.js_content)
        self.assertIn("btn-suggested-action", self.js_content)


if __name__ == "__main__":
    unittest.main()
