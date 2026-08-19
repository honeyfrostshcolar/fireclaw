from __future__ import annotations

import re
import unittest
from pathlib import Path
from html.parser import HTMLParser


WEB_CONSOLE_DIR = Path(__file__).resolve().parent.parent / "src" / "fireclaw_core" / "web_console"
INDEX_HTML_PATH = WEB_CONSOLE_DIR / "index.html"
STYLE_CSS_PATH = WEB_CONSOLE_DIR / "style.css"


class HTMLIDExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.tags: list[str] = []
        self.classes: set[str] = set()
        self.data_attrs: dict[str, set[str]] = {}

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for attr, val in attrs:
            if attr == "id" and val:
                self.ids.add(val)
            elif attr == "class" and val:
                for cls in val.split():
                    self.classes.add(cls)
            elif attr.startswith("data-") and val:
                self.data_attrs.setdefault(attr, set()).add(val)


class TestWebConsoleUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html_content = INDEX_HTML_PATH.read_text(encoding="utf-8")
        cls.css_content = STYLE_CSS_PATH.read_text(encoding="utf-8")

        parser = HTMLIDExtractor()
        parser.feed(cls.html_content)
        cls.extracted_ids = parser.ids
        cls.extracted_classes = parser.classes
        cls.extracted_data = parser.data_attrs

    def test_files_exist_and_non_empty(self):
        self.assertTrue(INDEX_HTML_PATH.is_file(), "index.html must exist")
        self.assertTrue(STYLE_CSS_PATH.is_file(), "style.css must exist")
        self.assertGreater(len(self.html_content), 500)
        self.assertGreater(len(self.css_content), 500)

    def test_top_header_elements(self):
        """Top Header must include title, robot id display, mode badge, safety pill, and 5 nav tabs."""
        self.assertIn("FireClaw", self.html_content)
        self.assertIn("Web Console", self.html_content)

        required_header_ids = {
            "robot-id-display",
            "mode-badge",
            "global-safety-pill",
            "nav-overview",
            "nav-dispatch",
            "nav-execution",
            "nav-recovery",
            "nav-settings",
        }
        for elem_id in required_header_ids:
            self.assertIn(elem_id, self.extracted_ids, f"Header element #{elem_id} missing in index.html")

    def test_five_tab_containers(self):
        """Must define the 5 tab container panes."""
        required_tabs = {
            "tab-overview",
            "tab-dispatch",
            "tab-execution",
            "tab-recovery",
            "tab-settings",
        }
        for tab_id in required_tabs:
            self.assertIn(tab_id, self.extracted_ids, f"Tab container #{tab_id} missing in index.html")

    def test_tab_overview_structure(self):
        """Overview tab must have robot status card, safety status card, recommended action container and primary action button."""
        required_overview_ids = {
            "robot-status-card",
            "safety-status-card",
            "recommended-action-container",
            "btn-primary-action",
        }
        for elem_id in required_overview_ids:
            self.assertIn(elem_id, self.extracted_ids, f"Overview element #{elem_id} missing in index.html")

    def test_tab_dispatch_structure(self):
        """Dispatch tab must have natural language input, parse button, and intent preview card with confirmation."""
        required_dispatch_ids = {
            "task-input-text",
            "btn-parse-task",
            "intent-preview-card",
            "preview-parsed-task",
            "preview-target-robot",
            "preview-estimated-steps",
            "preview-risk-level",
            "btn-confirm-start",
            "btn-cancel-preview",
        }
        for elem_id in required_dispatch_ids:
            self.assertIn(elem_id, self.extracted_ids, f"Dispatch element #{elem_id} missing in index.html")

    def test_tab_execution_structure(self):
        """Execution tab must have execution status badge, 3-state cancel indicator, current step card, timeline, and control buttons."""
        required_exec_ids = {
            "execution-status-badge",
            "cancel-status-indicator",
            "current-step-card",
            "execution-timeline",
            "btn-pause-task",
            "btn-resume-task",
            "btn-cancel-task",
        }
        for elem_id in required_exec_ids:
            self.assertIn(elem_id, self.extracted_ids, f"Execution element #{elem_id} missing in index.html")

        # Verify 3-state cancellation semantic presence
        self.assertTrue(
            "cancel_requested" in self.html_content or "cancel-requested" in self.html_content,
            "Cancellation state 1 (cancel_requested) indicator missing in index.html",
        )
        self.assertTrue(
            "stopping" in self.html_content,
            "Cancellation state 2 (stopping) indicator missing in index.html",
        )
        self.assertTrue(
            "stopped_confirmed" in self.html_content or "stopped-confirmed" in self.html_content,
            "Cancellation state 3 (stopped_confirmed) indicator missing in index.html",
        )

    def test_tab_recovery_structure(self):
        """Recovery tab must expose evidence and an explicitly limited request form."""
        required_recovery_ids = {
            "freeze-reason-display",
            "freeze-evidence-display",
            "recovery-blockers-list",
            "recovery-confirm-check",
            "btn-execute-recovery",
        }
        for elem_id in required_recovery_ids:
            self.assertIn(elem_id, self.extracted_ids, f"Recovery element #{elem_id} missing in index.html")
        self.assertIn("非正式两阶段恢复", self.html_content)
        self.assertNotIn("底盘防跌落传感器正常", self.html_content)
        self.assertNotIn("雷达防碰撞距离安全", self.html_content)
        self.assertNotIn("急停开关处于释放状态", self.html_content)

    def test_tab_settings_structure(self):
        """Settings tab must have profile displays, connectivity test buttons, and diagnostic log box."""
        required_settings_ids = {
            "profile-path-display",
            "profile-mode-display",
            "gateway-url-display",
            "btn-test-ros",
            "btn-test-gateway",
            "settings-log-box",
        }
        for elem_id in required_settings_ids:
            self.assertIn(elem_id, self.extracted_ids, f"Settings element #{elem_id} missing in index.html")

    def test_global_notifications_and_error_modal(self):
        """Must have toast container and 4-part structured error modal."""
        self.assertIn("toast-container", self.extracted_ids, "Global #toast-container missing")
        self.assertIn("error-modal", self.extracted_ids, "Four-part #error-modal missing")

        # 4-part error modal sections
        required_modal_parts = {
            "modal-what-happened",
            "modal-robot-safe-status",
            "modal-action-taken",
            "modal-next-steps",
        }
        for part_id in required_modal_parts:
            self.assertIn(part_id, self.extracted_ids, f"Error modal part #{part_id} missing in index.html")

    def test_css_rescue_dark_theme_tokens(self):
        """CSS must define dark slate background variables, status colors, and dark theme classes."""
        self.assertIn("theme-rescue-dark", self.css_content)

        # Check dark palette colors/variables
        # Either hex or hsl format present for dark slate and rescue colors
        color_patterns = [
            r"#0f172a|#0f141c|#1e293b|#18202c|#1e2837", # dark background
            r"#10b981|#2e7d32|green",                    # ready / success
            r"#f59e0b|#ed6c02|#ffa726|amber|orange",     # warning / caution / real robot
            r"#ef4444|#d32f2f|#dc2626|red",              # danger / blocked / estop
            r"#06b6d4|#0288d1|#38bdf8|cyan|blue",        # simulation / info
        ]
        for pattern in color_patterns:
            self.assertTrue(
                re.search(pattern, self.css_content, re.IGNORECASE),
                f"Expected color pattern {pattern} not found in style.css",
            )

    def test_css_responsive_breakpoints_and_animations(self):
        """CSS must include media queries for responsive layouts and smooth transitions."""
        self.assertIn("@media", self.css_content, "CSS must have responsive @media queries")
        self.assertIn("transition", self.css_content, "CSS must have transition rules for smooth interaction")
        self.assertIn(".timeline", self.css_content, "CSS must style timeline component")
        self.assertIn(".modal", self.css_content, "CSS must style modal component")
        self.assertIn(".toast", self.css_content, "CSS must style toast notifications")


if __name__ == "__main__":
    unittest.main()
