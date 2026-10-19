import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from segmenter import SentenceSegmenter, join_items  # noqa: E402


def words(text, stable_count=None):
    """'Hello world . How' -> items; tokens of . ? ! 。 are punctuation."""
    items = []
    for token in text.split():
        kind = 'punctuation' if token in ('.', '?', '!', '。', '？', '、', ',') else 'pronunciation'
        items.append([token, kind, True])
    if stable_count is not None:
        for item in items[stable_count:]:
            item[2] = False
    return [tuple(item) for item in items]


class JoinItemsTest(unittest.TestCase):
    def test_english(self):
        self.assertEqual(join_items(words("Hello , world . It's fine ?")), "Hello, world. It's fine?")

    def test_japanese_has_no_spaces(self):
        self.assertEqual(join_items(words('今日 は 晴れ です 。')), '今日は晴れです。')

    def test_mixed(self):
        self.assertEqual(join_items(words('AWS の 料金 is high .')), 'AWS の料金 is high.')


class SentenceSegmenterTest(unittest.TestCase):
    def setUp(self):
        self.seg = SentenceSegmenter()

    def test_short_utterance_waits_for_final(self):
        seg = SentenceSegmenter()
        self.assertEqual(seg.feed('r1', words('Hello there', 1), True, 'Hello there'), [])
        self.assertEqual(seg.feed('r1', words('Hello there .'), False, 'Hello there.'), ['Hello there.'])

    def test_long_speech_emits_stable_sentences_once(self):
        # first sentence stable, second still changing
        out = self.seg.feed('r1', words('We shipped it . Next we plan', 4), True, '')
        self.assertEqual(out, ['We shipped it.'])
        # same partial again: nothing new
        self.assertEqual(self.seg.feed('r1', words('We shipped it . Next we plan', 4), True, ''), [])
        out = self.seg.feed('r1', words('We shipped it . Next we plan to test . And', 10), True, '')
        self.assertEqual(out, ['Next we plan to test.'])
        final = self.seg.feed('r1', words('We shipped it . Next we plan to test . And then release .'), False,
                              'We shipped it. Next we plan to test. And then release.')
        self.assertEqual(final, ['And then release.'])

    def test_multiple_sentences_in_one_update(self):
        out = self.seg.feed('r1', words('One . Two ? Three ! four'), True, '')
        self.assertEqual(out, ['One.', 'Two?', 'Three!'])

    def test_unstable_punctuation_not_used(self):
        self.assertEqual(self.seg.feed('r1', words('Almost done .', 2), True, ''), [])

    def test_japanese_sentence(self):
        out = self.seg.feed('r1', words('資料 を 共有 します 。 次 に', 5), True, '')
        self.assertEqual(out, ['資料を共有します。'])
        self.assertEqual(self.seg.feed('r1', words('資料 を 共有 します 。 次 に 進み ます 。'), False, ''), ['次に進みます。'])

    def test_final_without_early_emit_uses_transcript(self):
        self.assertEqual(self.seg.feed('r1', [], False, ' Plain transcript '), ['Plain transcript'])

    def test_final_with_only_punctuation_left_is_dropped(self):
        self.seg.feed('r1', words('Done .'), True, '')
        self.assertEqual(self.seg.feed('r1', words('Done .'), False, 'Done.'), [])

    def test_results_are_tracked_independently(self):
        self.assertEqual(self.seg.feed('r1', words('A one .'), True, ''), ['A one.'])
        self.assertEqual(self.seg.feed('r2', words('B two .'), False, 'B two.'), ['B two.'])
        self.assertEqual(self.seg.feed('r1', words('A one . more'), False, 'A one. more'), ['more'])


if __name__ == '__main__':
    unittest.main()
