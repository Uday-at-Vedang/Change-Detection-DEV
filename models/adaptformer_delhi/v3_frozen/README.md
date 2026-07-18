# AdaptFormer Delhi — v3 FROZEN baseline

Do not overwrite this directory. Retrain into `best_v4/` / `runs/finetune_v4/`.

| Metric | Value |
|--------|-------|
| Val F1 | 0.6771 |
| Test F1 | 0.5809 |
| Test P / R / IoU | 0.6782 / 0.5351 / 0.4117 |
| Threshold | 0.2 |
| Loss | tversky |
| Run | `runs/finetune_v3/20260717_185640` |
| Split seed | 30 (stratified) |

Load: `ADAPTFORMER_WEIGHTS=models/adaptformer_delhi/v3_frozen`
