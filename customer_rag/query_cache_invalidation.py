from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable
from uuid import uuid4


def _source_key(source: str | Path) -> str:
    return str(Path(source).resolve()).replace("\\", "/").casefold()


def invalidate_query_cache(index_dir: Path, sources: Iterable[str | Path] | None = None) -> int:
    """Remove answers depending on changed documents after publishing the corpus."""
    changed_sources = None if sources is None else {_source_key(source) for source in sources}
    if changed_sources == set():
        return 0
    removed = 0
    for path in (index_dir / "query_cache").glob("*.json"):
        try:
            if changed_sources is not None:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeError):
                    payload = None
                dependencies = payload.get("source_paths") if isinstance(payload, dict) else None
                # Older cache formats cannot identify their source documents.
                if isinstance(dependencies, list) and dependencies:
                    if all(isinstance(source, str) for source in dependencies):
                        if not changed_sources.intersection(_source_key(source) for source in dependencies):
                            continue
            path.unlink()
            removed += 1
        except OSError:
            # The corpus signature also invalidates these entries if another
            # process has removed or locked one of the files in the meantime.
            continue
    return removed


def invalidate_corpus_caches(corpus_path: Path, *, empty: bool = False) -> None:
    index_dir = corpus_path.parent
    invalidate_query_cache(index_dir)
    # Publish a new empty generation so other processes drop loaded old vectors.
    generation = uuid4().hex
    manifest = index_dir / "vector_index_manifest.json"
    temporary = manifest.with_name(f"{manifest.name}.{generation}.tmp")
    temporary.write_text(json.dumps({
        "generation": generation, "index": "", "metadata": "", "chunks": 0, "empty": True,
    }), encoding="utf-8")
    os.replace(temporary, manifest)
    paths = [corpus_path.with_suffix(".parsed.pkl"), index_dir / "quick_search_cache.pkl"]
    paths.extend([index_dir / "faiss.index", index_dir / "chunks.jsonl"])
    paths.extend(index_dir.glob("faiss.*.index"))
    paths.extend(index_dir.glob("chunks.*.jsonl"))
    if empty:
        paths.extend([index_dir / "embedding_cache.npz", index_dir / "raw_parse_manifest.json"])
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Signatures and the new manifest also invalidate files locked by readers.
            continue
