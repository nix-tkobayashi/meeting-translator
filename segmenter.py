"""Split Amazon Transcribe streaming results into sentences as soon as their words are stable,
so long uninterrupted speech is translated sentence by sentence instead of waiting for a pause."""

SENTENCE_END = set('.?!。？！')
CLAUSE_END = set(',、，')
# Run-on speech may contain no sentence end for a long time; translate it in clauses instead.
CLAUSE_FLUSH_WORDS = 20  # flush up to the last stable comma once this many stable words wait
HARD_FLUSH_WORDS = 40  # flush all stable words even without any punctuation


def _is_cjk(char):
    return ord(char) >= 0x2E80


def join_items(items):
    """items: [(content, item_type, stable)] -> text; no space between two CJK words or before punctuation."""
    text = ''
    for content, item_type, _ in items:
        if not content:
            continue
        if text and item_type != 'punctuation' and not (_is_cjk(text[-1]) and _is_cjk(content[0])):
            text += ' '
        text += content
    return text


def _word_count(items):
    return sum(1 for _, item_type, _ in items if item_type != 'punctuation')


def _has_words(text):
    return any(char.isalnum() for char in text)


class SentenceSegmenter:
    def __init__(self):
        self.emitted = {}  # result_id -> number of items already sent for translation

    def feed(self, result_id, items, is_partial, transcript):
        """Return the sentences of this result that are ready to translate, each exactly once.
        items: [(content, item_type, stable)] from the first alternative."""
        done = self.emitted.get(result_id, 0)
        sentences = []
        if is_partial:
            stable = 0
            while stable < len(items) and items[stable][2]:
                stable += 1
            start = done
            for index in range(done, stable):
                content, item_type, _ = items[index]
                if item_type == 'punctuation' and content in SENTENCE_END:
                    sentences.append(join_items(items[start:index + 1]))
                    start = index + 1
            pending = _word_count(items[start:stable])
            if pending >= CLAUSE_FLUSH_WORDS:
                commas = [i for i in range(start, stable) if items[i][1] == 'punctuation' and items[i][0] in CLAUSE_END]
                end = commas[-1] + 1 if commas else stable if pending >= HARD_FLUSH_WORDS else None
                if end:
                    sentences.append(join_items(items[start:end]))
                    start = end
            if start != done:
                self.emitted[result_id] = start
        else:
            self.emitted.pop(result_id, None)
            # Nothing sent early: keep Transcribe's own formatting of the whole segment.
            sentences.append(transcript.strip() if done == 0 else join_items(items[done:]))
        return [sentence for sentence in sentences if _has_words(sentence)]
