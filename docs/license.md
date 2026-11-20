# Licensing & Publication Compliance Gate

> **Notice:** This document provides engineering compliance guidance and is not legal advice. Complete a legal/licensing review before publishing public container images or operating a closed-source network service.

## Upstream Licensing Summary (`openpronounce==1.1.0`)

Per upstream documentation (<https://openpronounce.github.io/docs/licensing.html>):

- **OpenPronounce `1.1.0` code and shipped scoring models** (`scorer_en.json`, `word_mdd_en.json`, `word_missing_generic.json`) are dual-licensed under **AGPL-3.0-or-later** or a **commercial license** from the OpenPronounce authors.
- Wrapping OpenPronounce in a separate FastAPI HTTP service does **not** automatically eliminate AGPL-3.0 network-interaction source-sharing obligations.
- **Bundled Hugging Face checkpoints** loaded at runtime / prefetched into the Docker image:
  - `facebook/wav2vec2-lv-60-espeak-cv-ft` (verify upstream model card & license metadata before redistributing)
  - `facebook/wav2vec2-large-960h` (Apache-2.0 on Hugging Face; verify model card metadata on each release)
- **Bundled lexicon:** CMU Pronouncing Dictionary (`cmudict.dict.gz`, BSD-2-Clause; `cmudict.LICENSE` shipped inside the `openpronounce` wheel).
- **Excluded copyleft extras:** This service does **not** install `espeak-ng`, `tts-piper` (GPL-3 voices), or `tts-kokoro` (`acoustic=False` and English-only `cmudict` G2P).

## Required Release Strategy (Choose One Before Publishing)

1. **AGPL-3.0-or-later Open-Source Distribution**
   - Distribute this repository and any modified source under AGPL-3.0-or-later (compatible with upstream OpenPronounce 1.1.0).
   - Provide clear corresponding source links (`org.opencontainers.image.source`) and upstream copyright/license notices in the repository and container image.
   - Verify redistribution rights for every cached file listed in `/opt/models/huggingface/model-manifest.json`.
2. **Commercial License from OpenPronounce Authors**
   - Obtain an active commercial license covering the product, code, and shipped models prior to closed-source deployment or private registry distribution.
   - Keep commercial license documentation on file and disable public GHCR redistribution of proprietary/commercial builds unless explicitly permitted.

## Pre-Publication Checklist

Before publishing images to GHCR via `.github/workflows/docker-publish.yml`, complete the checklist below:

- [ ] License strategy selected (AGPL-3.0-or-later vs commercial license) and repository `LICENSE` file aligned with the chosen strategy.
- [ ] Upstream OpenPronounce copyright and license notices preserved.
- [ ] Every artifact in `model-manifest.json` audited for redistribution rights if bundled in the published image.
- [ ] OCI `org.opencontainers.image.licenses` label matches the approved distribution license.
