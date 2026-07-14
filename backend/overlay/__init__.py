"""Synthetic overlays and scenario generation (eval/scenario-side).

These modules construct labeled test data. Unlike runtime pipeline code, they
ARE permitted to read/write ground truth (fault service/type, inject_time,
innocent flags) — but ONLY into the label sidecars, never into event payloads.
"""
