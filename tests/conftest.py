import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))


@pytest.fixture(scope="session")
def corpus():
    with open(ROOT / "tests" / "corpus.jsonl") as fh:
        return [json.loads(line) for line in fh]
