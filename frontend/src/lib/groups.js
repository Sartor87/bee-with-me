/** `groups` must be an array: the map calls `.find` on it. An older backend (and any snapshot
 *  saved from one) delivered the json_agg column as a JSON string; one such row used to blank
 *  every marker (B50). Anything that is not an array after decoding becomes []. */
export function normaliseGroups(pos) {
  if (!pos || typeof pos !== 'object' || Array.isArray(pos.groups)) return pos
  let groups = pos.groups
  if (typeof groups === 'string') {
    try { groups = JSON.parse(groups) } catch { groups = [] }
  }
  return { ...pos, groups: Array.isArray(groups) ? groups : [] }
}
