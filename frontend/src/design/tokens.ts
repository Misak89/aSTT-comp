/**
 * Design tokeny — aSTT-comp
 * Centrální definice barev, použití a pravidel.
 * Importuj přímo: import { colors } from '../design/tokens'
 */

/**
 * PRAVIDLA PSANÍ (typography rules)
 * - Pomlčka: vždy `-` (spojovník/hyphen). NIKDY `—` (em dash).
 *   Důvod: konzistence, čitelnost v kódu, copy-paste bezpečnost.
 * - Zvýraznění klíčových slov: inline style color: colors.red (#af1a1e).
 */

export const colors = {
  /** Primární červená — akcent, nebezpečí, živý mikrofon, důraz v nadpisech */
  red: '#af1a1e',

  /** Stavové barvy pro trial status */
  status: {
    ok:         '#16a34a',  // green-600
    borderline: '#ca8a04',  // yellow-600
    too_slow:   '#ea580c',  // orange-600
    fail:       '#dc2626',  // red-600
  },
} as const
