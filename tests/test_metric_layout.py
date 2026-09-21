from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.app import SplitrailApp


class MetricLayoutTests(unittest.TestCase):
    def test_exact_metrics_fit_at_normal_launch_and_after_resize(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "splitrail_desktop.portable.data_dir", return_value=Path(directory)
        ), patch(
            "splitrail_desktop.sync.data_dir", return_value=Path(directory)
        ):
            try:
                app = SplitrailApp(auto_refresh=False, codex_usage=True)
            except tk.TclError as error:
                if "no display name" in str(error) or "couldn't connect to display" in str(error):
                    self.skipTest(f"Tk display unavailable: {error}")
                raise
            try:
                app.metric_cards[2].label_var.set("REQUESTS")
                values = ("$23,456.78", "35,123,456,789", "301,234", "1,234", "Not collected")
                for card, value in zip(app.metric_cards, values):
                    card.value_var.set(value)
                app.update()
                self.assertEqual(app.winfo_width(), 1240)
                for width in (1240, 1800, 1240):
                    with self.subTest(width=width):
                        app.geometry(f"{width}x880")
                        app.update()
                        for card, value in zip(app.metric_cards, values):
                            self.assertEqual(card.value_var.get(), value)
                            for label in card.winfo_children():
                                self.assertGreaterEqual(label.winfo_width(), label.winfo_reqwidth())
                                self.assertLessEqual(label.winfo_x() + label.winfo_width(), card.winfo_width())
                            self.assertLessEqual(card.winfo_x() + card.winfo_width(), card.master.winfo_width())
            finally:
                app._close()


if __name__ == "__main__":
    unittest.main()
