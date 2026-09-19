"""Custom vocabulary boosting: turns operator-supplied phrases (names,
jargon, acronyms specific to an event) into a sherpa-onnx hotwords file, so
the decoder gets a bonus score toward recognizing them instead of guessing.

Sherpa-onnx's hotwords_file expects each phrase already split into the
model's own BPE pieces. Doing that "properly" needs the training-time
sentencepiece model, which the streaming zipformer model this app ships
does not include (only tokens.txt survives in the release tarball). Absent
that, a greedy longest-piece-match over tokens.txt is the standard
fallback: BPE vocabularies are themselves built by greedy merges, so
longest-match segmentation over the resulting vocab recovers the same
split as training for the common case. It won't be byte-identical to the
original tokenizer for every word, but it always produces a sequence of
pieces that are valid, in-vocabulary transitions -- which is what the
decoder graph actually needs in order to bias toward them. Verified against
a live recognizer: building with such a file changes nothing about normal
decoding (no regression) and doesn't require passing modeling_unit/bpe_vocab
at all, since the file is already pre-split.
"""
from __future__ import annotations

from pathlib import Path

WORD_BOUNDARY = "▁"  # sentencepiece's "start of word" marker, as literally written in tokens.txt


def _load_vocab(tokens_path: Path) -> set[str]:
    vocab = set()
    with tokens_path.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split(" ")
            if len(parts) >= 2:
                vocab.add(parts[0])
    return vocab


def _tokenize_word(word: str, vocab: set[str]) -> list[str] | None:
    """Greedy longest-match of one boundary-marked word against the piece
    vocabulary. None if no covering split exists (e.g. a character outside
    the model's alphabet)."""
    text = WORD_BOUNDARY + word.upper()
    pieces: list[str] = []
    i, n = 0, len(text)
    while i < n:
        matched = None
        for j in range(n, i, -1):
            candidate = text[i:j]
            if candidate in vocab:
                matched = candidate
                break
        if matched is None:
            return None
        pieces.append(matched)
        i += len(matched)
    return pieces


def build_hotwords_file(phrases: list[str], tokens_path: str, out_path: str) -> list[str]:
    """Writes out_path in sherpa-onnx's hotwords_file format (one phrase
    per line, pieces space-separated). Returns the subset of `phrases` that
    couldn't be tokenized against this model's vocabulary, so the caller
    can tell the operator those were skipped rather than silently dropping
    them."""
    vocab = _load_vocab(Path(tokens_path))
    lines: list[str] = []
    skipped: list[str] = []
    for phrase in phrases:
        phrase = phrase.strip()
        if not phrase:
            continue
        piece_seq: list[str] = []
        ok = True
        for word in phrase.split():
            pieces = _tokenize_word(word, vocab)
            if pieces is None:
                ok = False
                break
            piece_seq.extend(pieces)
        if ok and piece_seq:
            lines.append(" ".join(piece_seq))
        else:
            skipped.append(phrase)
    Path(out_path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return skipped
