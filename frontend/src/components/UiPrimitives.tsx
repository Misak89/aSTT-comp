import { isValidElement, type ReactNode } from 'react'

type Tone = 'light' | 'dark'
type ActionVariant = 'start' | 'stop' | 'danger' | 'secondary' | 'neutral'

export function HelpTip({ text, tone = 'light' }: { text: string; tone?: Tone }) {
  if (!text.trim()) return null
  const iconClass = tone === 'dark'
    ? 'border-slate-600 text-slate-300 hover:border-slate-400 hover:text-white'
    : 'border-gray-300 text-gray-500 hover:border-gray-400 hover:text-gray-800'
  return (
    <span className="relative inline-flex group/help align-middle">
      <span
        className={`inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border text-[10px] font-bold leading-none ${iconClass}`}
        title={text}
        aria-label={text}
      >
        ?
      </span>
      <span className="pointer-events-none absolute left-1/2 top-5 z-40 hidden w-72 -translate-x-1/2 whitespace-pre-line rounded bg-gray-950 px-2.5 py-2 text-xs leading-relaxed text-white shadow-xl group-hover/help:block">
        {text}
      </span>
    </span>
  )
}

export function FieldHintLabel({
  children,
  hint,
  tone = 'light',
  className = '',
}: {
  children: ReactNode
  hint: string
  tone?: Tone
  className?: string
}) {
  const textClass = tone === 'dark' ? 'text-slate-400' : 'text-gray-500'
  const label = reactNodeText(children)
  const helpText = settingHelpText(hint, label)
  return (
    <span className={`inline-flex items-center gap-1 ${textClass} ${className}`} title={helpText}>
      <span className={hint ? 'cursor-help underline decoration-dotted underline-offset-2' : ''}>
        {children}
      </span>
      <HelpTip text={helpText} tone={tone} />
    </span>
  )
}

function settingHelpText(hint: string, label: string): string {
  const base = hint.trim()
  if (!base || /\bCo to (je|dělá)\b/i.test(base)) return base
  return `${base}\n\nCo to je/dělá: ${settingEffect(label)}`
}

function reactNodeText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(reactNodeText).join(' ').trim()
  if (isValidElement<{ children?: ReactNode }>(node)) return reactNodeText(node.props.children)
  return ''
}

function settingEffect(label: string): string {
  const normalized = label.toLowerCase().replace(/\s+/g, ' ').trim()

  if (normalized.includes('model') && normalized.includes('parametr')) return 'vybírá model, u kterého se mění konkrétní technické nastavení.'
  if (normalized === 'model') return 'vybírá STT model, který bude převádět řeč na text.'
  if (normalized.includes('mikrofon') || normalized.includes('mic zařízení')) return 'vybírá fyzický zvukový vstup, ze kterého aplikace nahrává.'
  if (normalized.includes('vlákna')) return 'určuje, kolik částí procesoru smí model použít; víc může zrychlit, ale zatíží PC.'
  if (normalized.includes('beam')) return 'určuje, kolik možných přepisů model porovnává najednou; víc bývá přesnější, ale pomalejší.'
  if (normalized.includes('best of')) return 'určuje, z kolika pokusů si model vybere nejlepší text; víc může zlepšit kvalitu, ale zpomalí.'
  if (normalized.includes('fallback')) return 'povoluje nebo zakazuje záložní pokusy, když první dekódování nestačí.'
  if (normalized.includes('prompt') || normalized.includes('kontext')) return 'předá modelu nápovědu slov nebo tématu, aby lépe trefil očekávaný text.'
  if (normalized.includes('word timestamps')) return 'přidá čas ke slovům, aby šlo dohledat, kdy v audiu zazněla.'
  if (normalized.includes('jazyk')) return 'říká modelu, v jakém jazyce má řeč přepisovat.'

  if (normalized.includes('max doběh')) return 'nastaví, jak dlouho se čeká, než pomalý model po stopu doběhne.'
  if (normalized.includes('stop při mezeře')) return 'zastaví trial, když dlouho nepřichází nový přepis, aby test zbytečně nečekal.'
  if (normalized.includes('sweep')) return 'automaticky vytvoří několik blízkých variant nastavení pro porovnání.'
  if (normalized === 'režim') return 'volí, jestli se ladí úzce kolem aktuální hodnoty, nebo v širším rozsahu.'
  if (normalized.includes('velikost kroku') || normalized === 'krok (s)') return 'určuje velikost posunu testované hodnoty mezi jednotlivými variantami.'
  if (normalized.includes('vlastní rozsahy')) return 'zapne ruční volbu, které parametry a jaké hodnoty se mají zkoušet.'
  if (normalized.includes('max lag')) return 'nastaví nejvyšší přijatelné zpoždění přepisu proti zvuku.'

  if (normalized.includes('video') || normalized.includes('referenční')) return 'vybírá zdroj zvuku nebo textovou referenci, podle které se test porovnává.'
  if (normalized === 'od' || normalized.startsWith('od ')) return 'nastavuje čas, od kterého se má vybraný úsek začít používat.'
  if (normalized === 'do' || normalized.startsWith('do ')) return 'nastavuje čas, kde má vybraný úsek skončit.'
  if (normalized.includes('délka segmentu')) return 'určuje délku jedné uložené části mikrofonního audia.'
  if (normalized.includes('délka klipu') || normalized.includes('délka')) return 'nastavuje, jak dlouhá část audia se v testu použije.'
  if (normalized.includes('konec dříve')) return 'ukončí sběr před koncem audia, když chceš vynechat závěrečnou pauzu.'
  if (normalized.includes('pauza')) return 'nastavuje délku ticha nebo čekání mezi opakovanými částmi audia.'
  if (normalized.includes('start audia')) return 'posune časování testu, když zvuk reálně začne hrát později.'
  if (normalized.includes('opakování') || normalized.includes('repeat')) return 'nastavuje, kolikrát se stejná varianta zopakuje pro stabilnější porovnání.'
  if (normalized.includes('překryv')) return 'nechá konec předchozí části i v další části jako zvukový kontext.'
  if (normalized.includes('rezerva fronty')) return 'přidá časovou rezervu, aby se přepis nezpozdil kvůli čekání ve frontě.'

  if (normalized.includes('mód evaluace')) return 'volí, jestli se audio zpracuje jako stream, dávka nebo jiný testovací režim.'
  if (normalized.includes('vstupní režim')) return 'určuje, odkud se vezme audio pro daný test.'
  if (normalized.includes('seed')) return 'zajistí stejný náhodný výběr úseku při opakovaném spuštění.'
  if (normalized.includes('pevný start')) return 'ručně určí přesný začátek klipu místo náhodného výběru.'
  if (normalized.includes('max kandidát') || normalized.includes('max triál')) return 'omezuje počet variant, aby test netrval příliš dlouho.'
  if (normalized.includes('hw profil')) return 'popisuje výkonovou třídu počítače, pro kterou se nastavení hodnotí.'
  if (normalized.includes('zátěž')) return 'uměle zatíží CPU nebo RAM, aby se ověřilo chování na obsazeném PC.'
  if (normalized.includes('omezení výkonu')) return 'omezí výkon workeru nebo modelu pro simulaci slabšího počítače.'
  if (normalized.includes('popis') || normalized.includes('poznámka')) return 'uloží krátké vysvětlení testu, aby šel později správně poznat.'

  if (normalized.includes('vzdálenost')) return 'zaznamená, jak daleko byl reproduktor nebo mobil od mikrofonu.'
  if (normalized.includes('hlasitost')) return 'zaznamená hlasitost přehrávání, protože ovlivňuje kvalitu vstupu.'
  if (normalized.includes('input gain') || normalized.includes('mic gain')) return 'nastavuje zesílení mikrofonu; moc málo je tiché, moc hodně přebuzuje.'
  if (normalized.includes('prostředí')) return 'označí hluk v místnosti, aby šlo vysvětlit rozdíly výsledků.'
  if (normalized.includes('chunk') || normalized.includes('interval')) return 'nastavuje velikost nebo četnost audio bloků posílaných do modelu.'
  if (normalized.includes('příprava')) return 'přidá čas před startem, aby šlo připravit přehrávání nebo mikrofon.'
  if (normalized.includes('rms')) return 'ukazuje průměrnou hlasitost signálu, tedy jestli mikrofon slyší dost zvuku.'
  if (normalized.includes('clipping')) return 'ukazuje přebuzení zvuku, kdy je vstup moc hlasitý a zkreslený.'
  if (normalized.includes('noise floor')) return 'ukazuje úroveň šumu, kterou mikrofon slyší i bez řeči.'
  if (normalized.includes('backpressure')) return 'hlídá zahlcení zpracování, když model nestíhá přijímat audio.'
  if (normalized.includes('sample rate')) return 'určuje počet audio vzorků za sekundu, tedy technickou kvalitu vstupu.'

  return label ? `nastavuje položku „${label}“.` : 'vysvětluje účel tohoto nastavení.'
}

export function ActionButton({
  children,
  onClick,
  disabled = false,
  variant = 'secondary',
  title,
  className = '',
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  variant?: ActionVariant
  title?: string
  className?: string
  type?: 'button' | 'submit' | 'reset'
}) {
  const base = 'inline-flex items-center justify-center gap-1.5 rounded px-3 py-1.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-55'
  const variants: Record<ActionVariant, string> = {
    start: 'border border-emerald-500 bg-emerald-600 text-white hover:bg-emerald-500',
    stop: 'border border-red-500 bg-red-700 text-white hover:bg-red-600',
    danger: 'border border-red-500 bg-red-950/30 text-red-100 hover:bg-red-900/50',
    secondary: 'border border-blue-500 bg-blue-950/30 text-blue-100 hover:bg-blue-900/50',
    neutral: 'border border-gray-600 bg-gray-800 text-gray-200 hover:border-gray-400 hover:text-white',
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`${base} ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  )
}
