/**
 * WerDiff — vizualizace rozdílů mezi referenčním textem a přepisem modelu.
 * Zobrazuje slova barevně: zelená = správně, červená = chybně/navíc, šedá = vynecháno.
 *
 * Algoritmus: word-level Levenshtein diff (insertions / deletions / substitutions).
 */

interface Props {
  reference: string
  transcript: string
  wer?: number | null
}

type DiffOp =
  | { type: 'match'; word: string }
  | { type: 'sub'; ref: string; hyp: string }
  | { type: 'del'; ref: string }   // v ref, chybí v hyp
  | { type: 'ins'; hyp: string }   // není v ref, navíc v hyp

export function WerDiff({ reference, transcript, wer }: Props) {
  const refWords = tokenize(reference)
  const hypWords = tokenize(transcript)
  const ops = computeDiff(refWords, hypWords)

  return (
    <div className="space-y-2">
      {wer != null && (
        <p className="text-xs text-gray-500">
          WER: <span className="font-mono font-medium">{(wer * 100).toFixed(1)}%</span>
          <span className="ml-3 text-gray-400">
            <span className="inline-block w-3 h-3 rounded bg-green-100 border border-green-300 mr-1 align-middle" />správně
            <span className="inline-block w-3 h-3 rounded bg-red-100 border border-red-300 mx-1 ml-3 align-middle" />chybně
            <span className="inline-block w-3 h-3 rounded bg-gray-100 border border-gray-300 mx-1 ml-3 align-middle" />vynecháno
          </span>
        </p>
      )}
      <div className="font-mono text-xs leading-6 flex flex-wrap gap-x-1 gap-y-0.5 bg-gray-50 rounded p-3 border border-gray-200">
        {ops.map((op, i) => {
          if (op.type === 'match') {
            return (
              <span key={i} className="bg-green-50 text-green-800 px-0.5 rounded">
                {op.word}
              </span>
            )
          }
          if (op.type === 'sub') {
            return (
              <span key={i} className="inline-flex flex-col items-center">
                <span className="bg-red-100 text-red-700 px-0.5 rounded line-through text-gray-400">{op.ref}</span>
                <span className="bg-red-100 text-red-800 px-0.5 rounded -mt-0.5">{op.hyp}</span>
              </span>
            )
          }
          if (op.type === 'del') {
            return (
              <span key={i} className="bg-gray-100 text-gray-400 px-0.5 rounded line-through">
                {op.ref}
              </span>
            )
          }
          // ins
          return (
            <span key={i} className="bg-orange-100 text-orange-700 px-0.5 rounded">
              +{op.hyp}
            </span>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function tokenize(text: string): string[] {
  return text.toLowerCase().replace(/[^\w\s]/g, '').split(/\s+/).filter(Boolean)
}

function computeDiff(ref: string[], hyp: string[]): DiffOp[] {
  const R = ref.length
  const H = hyp.length

  // DP tabulka edit distance
  const dp: number[][] = Array.from({ length: R + 1 }, (_, i) =>
    Array.from({ length: H + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0))
  )
  for (let i = 1; i <= R; i++) {
    for (let j = 1; j <= H; j++) {
      if (ref[i - 1] === hyp[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1]
      } else {
        dp[i][j] = 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
      }
    }
  }

  // Traceback
  const ops: DiffOp[] = []
  let i = R, j = H
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && ref[i - 1] === hyp[j - 1]) {
      ops.push({ type: 'match', word: ref[i - 1] })
      i--; j--
    } else if (i > 0 && j > 0 && dp[i][j] === dp[i - 1][j - 1] + 1) {
      ops.push({ type: 'sub', ref: ref[i - 1], hyp: hyp[j - 1] })
      i--; j--
    } else if (j > 0 && dp[i][j] === dp[i][j - 1] + 1) {
      ops.push({ type: 'ins', hyp: hyp[j - 1] })
      j--
    } else {
      ops.push({ type: 'del', ref: ref[i - 1] })
      i--
    }
  }
  return ops.reverse()
}
