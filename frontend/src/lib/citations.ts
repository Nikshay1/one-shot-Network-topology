/**
 * Narration citations.
 *
 * The narrator cites LEDGER FACT IDS and nothing else. Its regex is
 * `\[(fact-[A-Za-z0-9_.]+-[0-9]{4})\]` (narrator.py:28) and its only tool is
 * `query_evidence_ledger`, so the ledger is its entire world — it cannot cite an
 * event id because it never sees one. Mirrored exactly here.
 *
 * "Citation-bound" means claim-level stripping (narrator.py:58-69): a LINE whose
 * citation does not resolve to a live fact id is DELETED from the report, not
 * flagged. Two consequences worth knowing while reading a report:
 *  - a line with no citation at all is kept unconditionally, so uncited prose
 *    passes through freely — the mechanism only polices citations that are
 *    present and wrong;
 *  - if the model fails validation twice, the stripped text ships anyway with
 *    citations_valid=false and holes where the claims were.
 */
export const CITATION_RE = /\[(fact-[A-Za-z0-9_.]+-[0-9]{4})\]/g

export type CitationToken =
  | { kind: 'text'; value: string }
  | { kind: 'citation'; factId: string }

/** Splits a run of markdown text into plain text and citation tokens. */
export function tokenizeCitations(text: string): CitationToken[] {
  const tokens: CitationToken[] = []
  let last = 0

  // A fresh regex per call: /g regexes carry lastIndex across calls.
  const re = new RegExp(CITATION_RE.source, 'g')
  let match: RegExpExecArray | null
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) tokens.push({ kind: 'text', value: text.slice(last, match.index) })
    tokens.push({ kind: 'citation', factId: match[1]! })
    last = match.index + match[0].length
  }
  if (last < text.length) tokens.push({ kind: 'text', value: text.slice(last) })
  return tokens
}

export function citationsIn(text: string): string[] {
  return [...text.matchAll(new RegExp(CITATION_RE.source, 'g'))].map((m) => m[1]!)
}
