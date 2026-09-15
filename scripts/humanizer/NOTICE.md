# Vendored: blader/humanizer

`SKILL.md` and `LICENSE` in this folder come from
https://github.com/blader/humanizer (MIT License).

- Pinned commit: `9862685f575c65a8247f90369951df1b3416e3d6` (branch `main`)
- Vendored on: 2026-09-15

The generator (`scripts/generate_post.py`) injects `SKILL.md` into the model's
system prompt so every post is written and then re-checked against its 25
signs of AI writing. Keeping a copy in the repo means the daily job never
depends on upstream being reachable.

To refresh from upstream:

    bash scripts/update_humanizer.sh            # latest main
    bash scripts/update_humanizer.sh <commit>   # a specific commit
