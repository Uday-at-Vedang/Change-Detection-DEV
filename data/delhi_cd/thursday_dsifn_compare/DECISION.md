# DSIFN vs AdaptFormer — Thursday decision

**Decision: `KEEP_ADAPTFORMER`**

AdaptFormer **does fill building interiors** on Delhi (mean fill 0.832, hole 0.148).
DSIFN proxy leaves large interior holes (fill 0.356, hole 0.644) and collapses on
Delhi F1 (0.09). **Do not switch backbones.**

## Means (Delhi 5 pairs)

| Model | F1 | Fill ratio | Hole rate |
|---|---:|---:|---:|
| AdaptFormer | 0.624 | 0.832 | 0.148 |
| DSIFN (proxy) | 0.090 | 0.356 | 0.644 |

## Caveat

Official DSIFN pretrained checkpoint was not published in the current Drive zip
(dataset only: train/val/test). Comparison uses a proxy DSIFN (VGG16 ImageNet +
DDN trained on DSIFN val; best DSIFN-CD test F1≈0.485). Still enough to reject
a backbone switch for Delhi interior completeness.

Details: `data/delhi_cd/thursday_dsifn_compare/metrics.json`
