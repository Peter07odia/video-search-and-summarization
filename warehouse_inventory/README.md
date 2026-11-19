# Drone lane inventory (pallet shuttle racking)

Fly a drone into each shuttle lane, then do the counting in the office. This tool turns one video
per lane into a per-lane exception report against the WMS:

| Signal | Source | Trust |
|---|---|---|
| Pallet count | VSS CV pipeline (Grounding DINO `pallet` + NvDCF tracker), unique track IDs | Authoritative count |
| Pallet identity | Barcode reads of LPN labels (`7700593756`), GS1 case labels | Exact when read |
| Location check | Upright location barcode, or the location text read by the VLM | Exact when read |
| Condition / compliance | VSS VLM: torn wrap, leaning load, broken pallet, mixed product, rack damage | Description, needs a human to confirm |
| VLM pallet estimate | VSS VLM | Cross-check only, never used alone |

Lane statuses, in priority order: `NOT_FLOWN`, `LOCATION_MISMATCH`, `MISPLACED_PALLET`,
`COUNT_MISMATCH`, `MIXED_SKU`, `SKU_MISMATCH`, `UNKNOWN_PALLET`, `NOT_IN_WMS`, `NEEDS_REVIEW`,
`CONDITION`, `MATCH`.

## Try the demo (no GPU)

```bash
pip install -r requirements.txt
python -m warehouse_inventory.demo      # -> demo/out/report.html
```

The demo renders lane videos with real barcodes, so label decoding is real. The VSS and CV
outputs are simulated, and the report is marked DEMO DATA.

## Run on real flights

1. **Fly.** Record one clip per lane-level, named by location, e.g. `videos/H-033-0054-10.mp4`.
   - Hover 2–3 s square to the upright location label and the front pallet's LPN label, about 1 m away.
   - Fly in at a constant height and slow speed with lights on. Tilt the camera down about 30° so
     the LPN label of each pallet shows through the gaps.
   - Record 4K/30 if the drone supports it.
2. **WMS export.** Put a CSV with location + LPN (one row per pallet), or location + pallet
   qty, next to the config. Map its headers in `config.yaml`
   (`cp config.example.yaml config.yaml`).
3. **VSS.** Deploy VSS (see `../deploy/docker`). Then run:
   ```bash
   python -m warehouse_inventory labels     # barcode reads, no VSS needed
   python -m warehouse_inventory vss        # condition + VLM cross-check, starts CV pipeline
   # copy CV tracker output out of the VSS container, one file per request_id:
   #   docker exec <vss> find / -name "*_fused.json" 2>/dev/null
   #   docker cp <vss>:<path>/<request_id>_fused.json out/cv/
   python -m warehouse_inventory cv         # pallet count from tracker IDs
   python -m warehouse_inventory reconcile  # -> out/reconciliation.csv, issues.csv, report.html
   ```
   Each lane also stays indexed in VSS, so you can ask questions in the VSS UI
   ("show damaged pallets in H-033-0054-10").

## Tuning on first real footage

- `counting.min_track_frames`: raise it if counts run high (tracker ID switches), lower it if
  they run low.
- `vss.cv_prompt`: try `pallet . pallet load;0.35` if pallets are missed.
- `labels.patterns`: regexes that classify location vs LPN barcodes.
- Validate on 10–15 lanes with a hand count before you quote an accuracy number.
