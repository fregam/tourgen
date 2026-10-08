# 0005 — 9:16 via planned crop centre; render_scale for previews

**Context:** Image-to-video models output landscape clips; 9:16 must not be a blind centre crop.
**Decision:** Claude records `subject_center_x` per image; the planner copies it into
`Shot.reframe_center_x`; `media/compose.normalize_clip` crops around it. `Settings.render_scale`
(`TOURGEN_RENDER_SCALE`) shrinks every render for tests and quick previews.
**Consequences:** 9:16 upscales the centre of a 16:9 clip (~1.8× at 1080p); generating native
9:16 clips per shot is a future option that doubles generation cost.
