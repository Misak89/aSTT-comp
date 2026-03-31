# Design - aSTT-comp

Centrální design pravidla. Zdrojový kód tokenů: `tokens.ts`.

## Barvy

| Token | Hex | Použití |
| --- | --- | --- |
| `colors.red` | `#af1a1e` | Primární červená - akcent, živý mikrofon, důraz v nadpisech a textu |
| `colors.status.ok` | `#16a34a` | Trial status OK |
| `colors.status.borderline` | `#ca8a04` | Trial status hraniční |
| `colors.status.too_slow` | `#ea580c` | Trial status příliš pomalý |
| `colors.status.fail` | `#dc2626` | Trial status selhání |

## Zvýraznění textu

Klíčová slova v UI textech zvýrazňuj inline stylem:

```tsx
<span style={{color:'#af1a1e'}}>důležité slovo</span>
```

## Pomlčka

- Vždy používej `-` (spojovník, hyphen, ASCII 45)
- NIKDY nepoužívej `—` (em dash, Unicode U+2014)
- Platí všude: UI texty, nadpisy, dokumentace, komentáře v kódu

## Ikony

Projekt nepoužívá icon knihovnu. Ikony jsou inline SVG (viewBox 0 0 24 24, strokeWidth 2).
Mikrofon ikona s červeným prvkem: červený kruh `cx="18" cy="6" r="3" fill="#af1a1e"` jako indikátor živého záznamu.
