/**
 * Twin constants and phrasing, mirrored from the backend.
 *
 * The thresholds are real and fixed (backend/rank/constants.py:28-30) but are
 * not served by any endpoint, so they are copied here to label the dial.
 */
export const TWIN_MATCH_THETA = 0.8
export const TWIN_PARTIAL_THETA = 0.5

/**
 * `Twin.missing_evidence` is a list of bare COMPONENT IDS, not sentences — a
 * component the twin simulated a symptom on that the real system never
 * instrumented (compare.py:78-79). The backend does compose a sentence for each,
 * `f"instrument {c} to verify the simulated symptom"` (compare.py:80), but it
 * lands in `recommendations`, which twin() drops on the floor and never returns.
 * So the sentence is mirrored here rather than invented.
 */
export function instrumentRecommendation(component: string): string {
  return `instrument ${component} to verify the simulated symptom`
}
