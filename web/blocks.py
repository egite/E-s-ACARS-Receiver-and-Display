"""Joining long airline reports that arrive as several blocks.

Shared by the web server, which does this live, and tools/sweep_unread.py, which replays
the archive - the archive keeps every block separately, so judging what is unread needs
the same joining the screen does.
"""
import re

from translate import message_type, translate

BLOCK_WINDOW_SECS = 600
MSGNO_BLOCK = re.compile(r"([A-Z]\d{2})([A-Z])$")


def file_as_block(msg, summary):
    """Re-file a message as a partial block (hidden by default) instead of a report in its own right."""
    msg["kind"] = "partial"
    msg["english"] = {"category": "link", "summary": summary, "details": []}
    msg["type"] = f"{msg['source']}:partial"


class BlockAssembler:
    """Long airline reports are sent as several messages sharing a message number with a letter suffix
    (D26A, D26B, …). Often a later one repeats the whole report; otherwise the pieces only make sense
    joined together. Either way the flight card should show one report, not a trail of fragments."""

    def __init__(self):
        self.groups = {}  # (aircraft, source, label, number) -> {"head", "letter", "own", "parts", "ts"}

    def observe(self, msg):
        """Call before the message is stored. May re-file msg itself as a block; returns other messages
        (already stored) whose translation changed and need re-broadcasting."""
        m = MSGNO_BLOCK.match(msg.get("msgno") or "")
        if not m or msg["kind"] != "acars" or not msg["text"]:
            return []
        now, letter = msg["ts"], m.group(2)
        for k in [k for k, g in self.groups.items() if now - g["ts"] > BLOCK_WINDOW_SECS]:
            del self.groups[k]
        key = (msg["reg"] or msg["flight"] or msg["icao"], msg["source"], msg["label"], m.group(1))
        group = self.groups.get(key)
        if group is None:
            self.groups[key] = self._new_group(msg, letter)
            return []
        group["ts"] = now
        head, text = group["head"], msg["text"]

        # A longer copy of the same report replaces everything received so far.
        if text.startswith(group["own"][:40]) and len(text) >= len(group["own"]):
            replaced = [head, *group["parts"].values()]
            for old in replaced:
                file_as_block(old, "Earlier copy of a report that was later received complete")
            self.groups[key] = self._new_group(msg, letter)
            return replaced

        # A fragment without a recognisable header belongs to the report it follows.
        if msg["english"].get("generic") and letter > group["letter"]:
            if letter not in group["parts"]:
                group["parts"][letter] = msg
                file_as_block(msg, f"Block {letter} of: {head['english']['summary']}")
                self._join(group)
                return [head]
            return []

        # The fragment came first and now the report's own first block has arrived.
        if head["english"].get("generic") and letter < group["letter"] and not msg["english"].get("generic"):
            old_head = head
            self.groups[key] = new = self._new_group(msg, letter)
            new["parts"] = {**group["parts"], group["letter"]: old_head}
            for part_letter, part in new["parts"].items():
                file_as_block(part, f"Block {part_letter} of: {msg['english']['summary']}")
            self._join(new)
            return [old_head, *group["parts"].values()]

        # Otherwise it's a different report reusing the number.
        self.groups[key] = self._new_group(msg, letter)
        return []

    @staticmethod
    def _new_group(msg, letter):
        return {"head": msg, "letter": letter, "own": msg["text"], "parts": {}, "ts": msg["ts"]}

    @staticmethod
    def _join(group):
        head = group["head"]
        head["text"] = group["own"] + "".join(group["parts"][k]["text"] for k in sorted(group["parts"]))
        english = translate(head, None)
        english["details"] = english["details"] + [f"Assembled from {len(group['parts']) + 1} blocks"]
        head["english"] = english
        head["type"] = message_type(head)
