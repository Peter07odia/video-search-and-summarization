"""VSS prompts for deep-lane (pallet shuttle) inspection flights.

The VLM is used for condition / compliance / anomaly description and a *cross-check* count.
The authoritative count comes from the CV tracker (cv_count.py).
"""

CAPTION_PROMPT = """\
You are inspecting a deep-lane pallet shuttle racking channel. The camera is on a drone \
flying from the lane entrance toward the back wall. Pallets sit on two rails with visible \
gaps between loads.

If a rack location label is visible (format like H-033-0054-10), read it exactly.

For this clip, describe each pallet load you can see, in order from nearest to farthest:
- wrap condition (intact, torn, loose, missing)
- carton condition (square, crushed, leaning, fallen cartons)
- pallet condition (broken or missing boards, pallet not square on the rails)
- overhang past the rails, leaning load, load touching the rack above
- any load that looks like a DIFFERENT product from the others (different print, colour, \
carton size, wrap, or height)
- the item number and date printed on the white pallet placard, if legible
- leaks, spills, debris, or obstructions in the channel
- damage to the rack itself (bent rails, uprights, missing stops)
Also note empty gaps where a pallet would be expected. State only what is visible. \
If something is unclear, say so instead of guessing.\
"""

CAPTION_SUMMARIZATION_PROMPT = """\
Combine the clip descriptions into one front-to-back account of the lane. Merge \
descriptions of the same pallet that appear in consecutive clips; do not list a pallet \
twice. Keep every condition or compliance problem with its approximate time in seconds.\
"""

SUMMARY_AGGREGATION_PROMPT = """\
Return ONLY a JSON object, no prose, with exactly these keys:
{
  "location_label_text": "<rack location label exactly as read, or null>",
  "vlm_pallet_estimate": <integer or null>,
  "lane_full_to_back": <true|false|null>,
  "mixed_product_suspected": <true|false>,
  "issues": [
    {
      "type": "torn_wrap|crushed_cartons|leaning_load|broken_pallet|not_seated|overhang|\
leak_or_spill|obstruction|rack_damage|mixed_product|other",
      "severity": "low|medium|high",
      "approx_time_s": <number or null>,
      "pallet_position_from_front": <integer or null>,
      "description": "<short factual description>"
    }
  ],
  "placards": [{"pallet_position_from_front": <integer>, "item": "<item no or null>", \
"date": "<date as printed or null>"}],
  "summary": "<two sentences on overall lane condition>"
}
Severity: high = safety risk or product loss (leaning/falling load, leak, rack damage); \
medium = needs rework (torn wrap, crushed cartons, broken pallet); low = cosmetic.
If there are no issues return an empty list.\
"""
