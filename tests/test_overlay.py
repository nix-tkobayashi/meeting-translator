import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import tkinter as tk
    tk.Tk  # noqa: B018 (stubbed module in test_app lacks Tk)
except (ImportError, AttributeError):
    tk = None

THEM, ME = '相手（PC音声）', '自分（マイク）'


@unittest.skipIf(tk is None or not hasattr(tk, 'Tk'), 'tkinter not available')
class OverlayTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest('no display for Tk')
        self.addCleanup(self.root.destroy)
        import overlay
        self.overlay_module = overlay
        self.on_stop, self.on_settings, self.on_toggle = mock.Mock(), mock.Mock(), mock.Mock()
        self.ov = overlay.CaptionOverlay(self.root, 'TkDefaultFont', self.on_stop, self.on_settings, self.on_toggle)
        self.ov.show()
        self.root.update()

    def line_counts(self):
        self.root.update()
        return [self.ov._display_lines(text, '1.0') for text in (self.ov.left, self.ov.right)]

    def test_columns_stay_aligned(self):
        long_text = 'This is a fairly long English sentence that should wrap over several lines in the panel. ' * 3
        self.ov.add_caption(THEM, long_text, '短い訳。')
        self.ov.add_caption(ME, '短い発言です。', 'A short remark that is translated into a somewhat longer English line.')
        self.ov.render()
        left, right = self.line_counts()
        self.assertEqual(left, right)
        self.assertGreater(left, 4)

    def test_partial_shown_then_replaced(self):
        self.ov.set_partial(THEM, 'we are still talk')
        self.ov.render()
        self.assertIn('we are still talk', self.ov.left.get('1.0', 'end'))
        self.ov.add_caption(THEM, 'We are still talking.', '話し中です。')
        self.ov.render()
        self.assertNotIn('still talk\n', self.ov.left.get('1.0', 'end').replace('talking.', ''))
        self.assertIsNone(self.ov.partial)

    def test_clearing_partial_keeps_other_source(self):
        self.ov.set_partial(THEM, 'still talking')
        self.ov.set_partial(ME, '')
        self.assertEqual(self.ov.partial, (THEM, 'still talking'))
        self.ov.set_partial(THEM, '')
        self.assertIsNone(self.ov.partial)

    def test_drag_to_negative_coordinates(self):
        # Monitors left of / above the primary one have negative coordinates.
        self.ov._drag = (0, 0)
        self.ov._drag_move(mock.Mock(x_root=-800, y_root=300))
        self.root.update()
        self.assertEqual(self.ov.panel.winfo_x(), -800)

    def test_pill_follows_panel_and_flips_below_at_desktop_top(self):
        top = self.overlay_module.virtual_screen_top()
        self.ov.panel.geometry(f'+100+{top + 400}')
        self.ov._place_pill()
        self.root.update()
        self.assertLess(self.ov.pill.winfo_y(), self.ov.panel.winfo_y())
        self.ov.panel.geometry(f'+100+{top}')
        self.root.update()
        self.ov._place_pill()
        self.root.update()
        self.assertGreater(self.ov.pill.winfo_y(), self.ov.panel.winfo_y())

    def test_older_captions_fade(self):
        for i in range(6):
            self.ov.add_caption(THEM, f'line {i}', f'行 {i}')
        self.ov.render()
        first = self.ov.left.tag_names('1.0')
        last_index = self.ov.left.search('line 5', '1.0')
        self.assertIn(f'{THEM}2', first)
        self.assertIn(f'{THEM}0', self.ov.left.tag_names(last_index))

    def test_caption_history_is_capped(self):
        for i in range(self.overlay_module.MAX_CAPTIONS + 5):
            self.ov.add_caption(THEM, f'{i}', f'{i}')
        self.assertEqual(len(self.ov.captions), self.overlay_module.MAX_CAPTIONS)

    def test_source_toggle_and_reset(self):
        self.ov.toggle_source(ME)
        self.on_toggle.assert_called_once_with(ME, False)
        self.assertFalse(self.ov.enabled[ME])
        self.ov.reset()
        self.assertTrue(self.ov.enabled[ME])
        self.assertEqual(self.ov.captions, [])

    def test_windows_are_borderless_topmost_and_hide(self):
        for window in (self.ov.panel, self.ov.pill):
            self.assertTrue(window.overrideredirect())
            self.assertTrue(window.attributes('-topmost'))
        self.assertLess(self.ov.pill.winfo_y(), self.ov.panel.winfo_y())  # pill sits above the panel
        self.ov.hide()
        self.root.update()
        self.assertEqual(self.ov.panel.state(), 'withdrawn')
        self.assertEqual(self.ov.pill.state(), 'withdrawn')


if __name__ == '__main__':
    unittest.main()
