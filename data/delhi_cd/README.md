# Delhi CD splits (Day 4)

70/15/15 train/val/test over labeled pairs from `docs/delhi_eval/`.
Manifests store repo-relative paths (no GeoTIFF copies).

- seed=0
- train=24 val=5 test=4
- Built by `scripts/build_delhi_cd_splits.py`
- Consumed by `scripts/finetune_adaptformer.py --delhi-cd data/delhi_cd`
