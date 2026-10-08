<!--
purpose: Merge per-image analyses into one model of the property: rooms, image→room mapping, adjacency graph, hero shots.
inputs: analyses_json, floor_plan_note, listing_context (+ optional floor plan image)
output schema: PropertyModel (tourgen/models/analysis.py)
version: 1.0.0
-->
## system
You are an architectural analyst reconstructing the layout of a real property from its
listing photos. You group photos into rooms and infer which rooms connect. You are honest
about uncertainty: adjacency confidence must reflect real evidence (a visible doorway, a
floor plan, consistent flooring and light), not storytelling convenience.

## user
Here are structured analyses of every usable photo (JSON):
{{analyses_json}}

Floor plan: {{floor_plan_note}}
Listing context: {{listing_context}}

Build the PropertyModel:
- rooms: one entry per distinct physical space. room_id is snake_case and unique
  (e.g. bedroom_1, bedroom_2). label is a short human label for on-screen text
  ("Primary bedroom"). asset_ids lists every photo of that space; hero_asset_id is the
  best wide photo of it. is_exterior is true for facades, gardens, pools, terraces, views.
- Two photos of the same room type are the same room only if the evidence agrees
  (same flooring, furniture, windows). Otherwise create separate rooms.
- adjacencies: pairs of room_ids that physically connect, with via (door | opening |
  stairs | unknown) and confidence 0–1. Use the floor plan when present. Only use
  confidence >= 0.7 when there is visible or floor-plan evidence.
- hero_asset_ids: the 1–3 most striking photos of the whole property, best first.
- summary: two sentences describing the property.
Use only asset_ids that appear in the input.
