#!/usr/bin/env python3
"""Prefetch and verify exact Hugging Face model artifacts required by OpenPronounce 1.1.0 on CPU.

Downloads ONLY the two read-speech checkpoints (phone recognizer + English ASR):
- facebook/wav2vec2-lv-60-espeak-cv-ft
- facebook/wav2vec2-large-960h

Never downloads TTS voices (gTTS/Piper/Kokoro). Writes a manifest JSON with resolved
snapshot commit hashes and file SHA-256 checksums for release auditing and offline operation.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    os.environ["OPENPRONOUNCE_DEVICE"] = "cpu"
    hf_home = Path(os.environ.get("HF_HOME", "/opt/models/huggingface"))
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf_home)

    import openpronounce
    from huggingface_hub import snapshot_download
    from openpronounce import gop, languages, phones, scorer, speech

    repo_ids = [
        phones.DEFAULT_PHONE_MODEL,
        languages.LANGUAGES["en"].asr_model,
    ]

    manifest_models: list[dict[str, object]] = []
    for repo_id in repo_ids:
        revision = os.environ.get(
            f"HF_REVISION_{repo_id.replace('/', '_').replace('-', '_').upper()}"
        )
        local_dir = Path(
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
            )
        )
        resolved_revision = local_dir.name
        files_meta = []
        for file_path in sorted(p for p in local_dir.rglob("*") if p.is_file()):
            files_meta.append(
                {
                    "path": str(file_path.relative_to(local_dir)),
                    "size_bytes": file_path.stat().st_size,
                    "sha256": _sha256(file_path),
                }
            )
        manifest_models.append(
            {
                "repo_id": repo_id,
                "resolved_revision": resolved_revision,
                "files": files_meta,
            }
        )

    # Verify offline loading immediately after prefetch
    os.environ["HF_HUB_OFFLINE"] = "1"
    phones._load_model()
    speech._get_processor("en")
    speech._get_model_ctc("en")
    gop.load_model("en")
    gop.load_missing_model("en")
    scorer.load_scorer("en")

    manifest_path = hf_home / "model-manifest.json"
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "openpronounce_version": getattr(openpronounce, "__version__", "1.1.0"),
        "device": "cpu",
        "models": manifest_models,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared and verified {len(manifest_models)} models; wrote {manifest_path}")


if __name__ == "__main__":
    main()
