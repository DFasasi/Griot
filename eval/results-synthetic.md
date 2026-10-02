# Bridge evaluation

Catalog: synthetic catalog (3000 tracks) — sanity check only. 50 random waypoint pairs, 8 bridge tracks.

| method | n | seam sim ↑ | worst seam ↑ | tempo jump % ↓ | key clashes ↓ | energy jump ↓ | progress ρ ↑ | artist repeat ↓ | judge cost ↓ |
|---|---|---|---|---|---|---|---|---|---|
| griot | 50 | 0.876 | 0.757 | 3.072 | 0.060 | 0.041 | 0.944 | 0.004 | 0.257 |
| griot (preview-only) | 50 | 0.868 | 0.742 | 3.004 | 0.060 | 0.045 | 0.935 | 0.004 | 0.264 |
| straight line (emb) | 50 | 0.884 | 0.808 | 7.096 | 0.571 | 0.097 | 0.999 | 0.078 | 0.645 |
| artist graph (BtF) | 50 | 0.860 | 0.723 | 8.707 | 0.609 | 0.053 | 0.923 | 0.062 | 0.664 |
| random | 50 | -0.001 | -0.721 | 18.451 | 0.687 | 0.198 | 0.283 | 0.010 | 1.616 |
