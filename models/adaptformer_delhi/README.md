# AdaptFormer Delhi fine-tune (Day 6+)

Export the best checkpoint from a finetune run:

```powershell
python scripts/export_adaptformer_delhi.py
# or
python scripts/export_adaptformer_delhi.py --src runs/day5_full/<run_id>/best
```

This creates:

- `best/` — HuggingFace `save_pretrained` layout (used by the app)
- `best.pt` — state_dict (plan deliverable name; optional)

Point the app at the weights:

```
ADAPTFORMER_WEIGHTS=models/adaptformer_delhi/best
```

Weights are gitignored (large). Re-export after each training run.
