/**
 * TranscribeEditor — TipTap rich text editor pro Přepis.
 *
 * Funkce:
 * - Bold, italic, underline, strikethrough
 * - Font family, font size
 * - Barva textu, zvýraznění
 * - Zarovnání (levé, střed, pravé, do bloku)
 * - Find & Replace panel
 * - Vložit timestamp [HH:MM:SS] na pozici kurzoru
 * - Klik na timestamp → onTimestampClick(sekundy)
 * - Export: TXT, HTML, Markdown, DOCX, EPUB
 */
import { useEffect, useCallback, useState } from 'react'
import { useEditor, EditorContent, Extension } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Underline from '@tiptap/extension-underline'
import { TextStyle } from '@tiptap/extension-text-style'
import { Color } from '@tiptap/extension-color'
import FontFamily from '@tiptap/extension-font-family'
import Highlight from '@tiptap/extension-highlight'
import TextAlign from '@tiptap/extension-text-align'
import { Document, Paragraph, TextRun, Packer, AlignmentType } from 'docx'
import JSZip from 'jszip'

// Custom FontSize extension built on top of TextStyle
const FontSize = Extension.create({
  name: 'fontSize',
  addGlobalAttributes() {
    return [{
      types: ['textStyle'],
      attributes: {
        fontSize: {
          default: null,
          parseHTML: el => (el as HTMLElement).style.fontSize || null,
          renderHTML: attrs => {
            if (!attrs.fontSize) return {}
            return { style: `font-size: ${attrs.fontSize}` }
          },
        },
      },
    }]
  },
  addCommands() {
    return {
      setFontSize: (size: string) => ({ chain }: { chain: () => unknown }) => {
        return (chain() as ReturnType<typeof import('@tiptap/react').Editor.prototype.chain>)
          .setMark('textStyle', { fontSize: size })
          .run()
      },
    } as Record<string, unknown>
  },
})

const FONT_FAMILIES = ['Výchozí', 'Arial', 'Georgia', 'Times New Roman', 'Courier New', 'Verdana', 'Trebuchet MS']
const FONT_SIZES = ['12px', '14px', '16px', '18px', '20px', '24px', '28px', '32px']

const TIMESTAMP_RE = /\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g

interface Props {
  initialContent?: string
  currentAudioTime?: number
  onTimestampClick?: (seconds: number) => void
  onSave?: (html: string, plainText: string) => void
  saveStatus?: 'idle' | 'saving' | 'saved' | 'error'
}

// HTML → simple Markdown
function htmlToMarkdown(html: string): string {
  return html
    .replace(/<strong>(.*?)<\/strong>/g, '**$1**')
    .replace(/<em>(.*?)<\/em>/g, '_$1_')
    .replace(/<u>(.*?)<\/u>/g, '$1')
    .replace(/<s>(.*?)<\/s>/g, '~~$1~~')
    .replace(/<h1>(.*?)<\/h1>/g, '# $1\n')
    .replace(/<h2>(.*?)<\/h2>/g, '## $1\n')
    .replace(/<h3>(.*?)<\/h3>/g, '### $1\n')
    .replace(/<p>(.*?)<\/p>/g, '$1\n\n')
    .replace(/<br\s*\/?>/g, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .trim()
}

function secondsToTimestamp(s: number): string {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (h > 0) return `[${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}]`
  return `[${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}]`
}

function timestampToSeconds(ts: string): number {
  const clean = ts.replace(/[\[\]]/g, '')
  const parts = clean.split(':').map(Number)
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2]
  return parts[0] * 60 + parts[1]
}

async function buildDocx(html: string): Promise<Blob> {
  // Parse HTML paragraphs into docx paragraphs
  const div = document.createElement('div')
  div.innerHTML = html
  const paragraphs: Paragraph[] = []

  div.querySelectorAll('p, h1, h2, h3, li').forEach(el => {
    const runs: TextRun[] = []
    el.childNodes.forEach(node => {
      if (node.nodeType === Node.TEXT_NODE) {
        runs.push(new TextRun({ text: node.textContent || '' }))
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const span = node as HTMLElement
        const isBold = span.tagName === 'STRONG' || span.style.fontWeight === 'bold'
        const isItalic = span.tagName === 'EM' || span.style.fontStyle === 'italic'
        const isUnderline = span.tagName === 'U'
        runs.push(new TextRun({
          text: span.textContent || '',
          bold: isBold,
          italics: isItalic,
          underline: isUnderline ? {} : undefined,
        }))
      }
    })
    if (runs.length === 0) runs.push(new TextRun({ text: el.textContent || '' }))
    const tagName = el.tagName.toLowerCase()
    paragraphs.push(new Paragraph({
      children: runs,
      alignment: tagName === 'h1' || tagName === 'h2' ? AlignmentType.LEFT : undefined,
      heading: tagName === 'h1' ? 'Heading1' as never : tagName === 'h2' ? 'Heading2' as never : undefined,
    }))
  })

  if (paragraphs.length === 0) {
    paragraphs.push(new Paragraph({ children: [new TextRun({ text: div.textContent || '' })] }))
  }

  const doc = new Document({ sections: [{ children: paragraphs }] })
  return Packer.toBlob(doc)
}

async function buildEpub(html: string, title = 'Přepis'): Promise<Blob> {
  const zip = new JSZip()
  zip.file('mimetype', 'application/epub+zip')

  const meta = zip.folder('META-INF')!
  meta.file('container.xml', `<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>`)

  const oebps = zip.folder('OEBPS')!
  oebps.file('content.opf', `<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>${title}</dc:title>
    <dc:language>cs</dc:language>
    <dc:identifier id="uid">astt-prepis-${Date.now()}</dc:identifier>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="toc"><itemref idref="chapter"/></spine>
</package>`)

  oebps.file('toc.ncx', `<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="astt-prepis"/></head>
  <docTitle><text>${title}</text></docTitle>
  <navMap><navPoint id="c1" playOrder="1"><navLabel><text>${title}</text></navLabel><content src="chapter.xhtml"/></navPoint></navMap>
</ncx>`)

  oebps.file('chapter.xhtml', `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>${title}</title><meta charset="UTF-8"/></head>
<body>${html}</body>
</html>`)

  return zip.generateAsync({ type: 'blob', mimeType: 'application/epub+zip' })
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function TranscribeEditor({ initialContent = '', currentAudioTime = 0, onTimestampClick, onSave, saveStatus = 'idle' }: Props) {
  const [showFindReplace, setShowFindReplace] = useState(false)
  const [findText, setFindText] = useState('')
  const [replaceText, setReplaceText] = useState('')
  const [findMsg, setFindMsg] = useState('')
  const [fontSize, setFontSize] = useState('16px')
  const [textColor, setTextColor] = useState('#000000')
  const [highlightColor, setHighlightColor] = useState('#ffff00')

  const editor = useEditor({
    extensions: [
      StarterKit,
      Underline,
      TextStyle,
      FontSize,
      Color,
      FontFamily,
      Highlight.configure({ multicolor: true }),
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
    ],
    content: initialContent || '<p></p>',
    editorProps: {
      attributes: {
        class: 'outline-none min-h-64 p-3 prose prose-sm max-w-none',
      },
      handleClick(view, _pos, event) {
        // Detect click on timestamp [MM:SS] or [H:MM:SS]
        const target = event.target as HTMLElement
        const text = target.textContent || ''
        const match = text.match(/^\[(\d{1,2}:\d{2}(?::\d{2})?)\]$/)
        if (match && onTimestampClick) {
          onTimestampClick(timestampToSeconds(match[0]))
          return true
        }
        return false
      },
    },
  })

  // Sync content when it changes externally (live transcription updates or archive open)
  useEffect(() => {
    if (!editor || !initialContent) return
    if (editor.getHTML() !== initialContent) {
      editor.commands.setContent(initialContent)
    }
  }, [editor, initialContent])

  const insertTimestamp = useCallback(() => {
    if (!editor) return
    const ts = secondsToTimestamp(currentAudioTime)
    editor.chain().focus().insertContent(`<span style="color:#af1a1e;cursor:pointer" title="Klik=přejít na toto místo v audiu">${ts}</span> `).run()
  }, [editor, currentAudioTime])

  // Odstraní všechny timestamps [MM:SS] nebo [H:MM:SS] z textu
  const removeAllTimestamps = useCallback(() => {
    if (!editor) return
    const html = editor.getHTML()
    // Odstraní <span> tagy obsahující timestamp
    const cleaned = html.replace(/<span[^>]*>\[\d{1,2}:\d{2}(?::\d{2})?\]<\/span>\s*/g, '')
    // Odstraní také holé timestamps bez span
    const cleaned2 = cleaned.replace(/\[\d{1,2}:\d{2}(?::\d{2})?\]\s*/g, '')
    editor.commands.setContent(cleaned2)
  }, [editor])

  const handleFindReplace = useCallback((replaceAll: boolean) => {
    if (!editor || !findText) return
    const html = editor.getHTML()
    const escaped = findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const re = new RegExp(escaped, 'gi')
    const count = (html.match(re) || []).length
    if (count === 0) { setFindMsg('Nenalezeno'); return }
    if (replaceAll) {
      editor.commands.setContent(html.replace(re, replaceText))
      setFindMsg(`Nahrazeno: ${count}×`)
    } else {
      setFindMsg(`Nalezeno: ${count}×`)
    }
  }, [editor, findText, replaceText])

  const exportAs = useCallback(async (fmt: 'txt' | 'html' | 'md' | 'docx' | 'epub') => {
    if (!editor) return
    const html = editor.getHTML()
    const txt = editor.getText()
    const base = 'prepis'
    if (fmt === 'txt') {
      downloadBlob(new Blob([txt], { type: 'text/plain;charset=utf-8' }), `${base}.txt`)
    } else if (fmt === 'html') {
      const full = `<!DOCTYPE html><html lang="cs"><head><meta charset="UTF-8"><title>Přepis</title></head><body>${html}</body></html>`
      downloadBlob(new Blob([full], { type: 'text/html;charset=utf-8' }), `${base}.html`)
    } else if (fmt === 'md') {
      downloadBlob(new Blob([htmlToMarkdown(html)], { type: 'text/plain;charset=utf-8' }), `${base}.md`)
    } else if (fmt === 'docx') {
      const blob = await buildDocx(html)
      downloadBlob(blob, `${base}.docx`)
    } else if (fmt === 'epub') {
      const blob = await buildEpub(html)
      downloadBlob(blob, `${base}.epub`)
    }
  }, [editor])

  if (!editor) return null

  const btnClass = (active: boolean) =>
    `px-2 py-1 text-sm rounded border ${active ? 'bg-blue-600 border-blue-500 text-white' : 'bg-gray-700 border-gray-600 text-gray-200 hover:bg-gray-600'}`

  return (
    <div className="flex flex-col h-full bg-white border border-gray-200 rounded-lg overflow-hidden">
      {/* Toolbar */}
      <div className="flex flex-wrap gap-1 p-2 border-b border-gray-200 bg-gray-50">
        {/* Text formatting */}
        <button onClick={() => editor.chain().focus().toggleBold().run()} className={btnClass(editor.isActive('bold'))} title="Tučné (Ctrl+B)">B</button>
        <button onClick={() => editor.chain().focus().toggleItalic().run()} className={btnClass(editor.isActive('italic'))} title="Kurzíva (Ctrl+I)"><em>I</em></button>
        <button onClick={() => editor.chain().focus().toggleUnderline().run()} className={btnClass(editor.isActive('underline'))} title="Podtržení (Ctrl+U)"><u>U</u></button>
        <button onClick={() => editor.chain().focus().toggleStrike().run()} className={btnClass(editor.isActive('strike'))} title="Přeškrtnutí"><s>S</s></button>

        <div className="w-px bg-gray-300 mx-1" />

        {/* Color */}
        <label className="flex items-center gap-1 cursor-pointer" title="Barva textu">
          <span className="text-xs text-gray-600">A</span>
          <input type="color" value={textColor} onChange={e => { setTextColor(e.target.value); editor.chain().focus().setColor(e.target.value).run() }}
            className="w-6 h-6 rounded cursor-pointer border-0 p-0" />
        </label>

        {/* Highlight */}
        <label className="flex items-center gap-1 cursor-pointer" title="Zvýraznění">
          <span className="text-xs text-gray-600">Hlt</span>
          <input type="color" value={highlightColor} onChange={e => setHighlightColor(e.target.value)}
            className="w-6 h-6 rounded cursor-pointer border-0 p-0" />
          <button onClick={() => editor.chain().focus().toggleHighlight({ color: highlightColor }).run()}
            className={btnClass(editor.isActive('highlight'))} title="Aplikovat zvýraznění">
            ▌
          </button>
        </label>

        <div className="w-px bg-gray-300 mx-1" />

        {/* Font family */}
        <select
          value={editor.getAttributes('textStyle').fontFamily || ''}
          onChange={e => {
            if (e.target.value) editor.chain().focus().setFontFamily(e.target.value).run()
            else editor.chain().focus().unsetFontFamily().run()
          }}
          className="text-sm border border-gray-300 rounded px-1 bg-white"
          title="Písmo"
        >
          {FONT_FAMILIES.map(f => (
            <option key={f} value={f === 'Výchozí' ? '' : f}>{f}</option>
          ))}
        </select>

        {/* Font size */}
        <select
          value={fontSize}
          onChange={e => {
            setFontSize(e.target.value)
            ;(editor.chain().focus() as unknown as { setFontSize: (s: string) => { run: () => void } }).setFontSize(e.target.value).run()
          }}
          className="text-sm border border-gray-300 rounded px-1 bg-white w-20"
          title="Velikost písma"
        >
          {FONT_SIZES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>

        <div className="w-px bg-gray-300 mx-1" />

        {/* Alignment */}
        <button onClick={() => editor.chain().focus().setTextAlign('left').run()} className={btnClass(editor.isActive({ textAlign: 'left' }))} title="Vlevo">⬤ L</button>
        <button onClick={() => editor.chain().focus().setTextAlign('center').run()} className={btnClass(editor.isActive({ textAlign: 'center' }))} title="Střed">C</button>
        <button onClick={() => editor.chain().focus().setTextAlign('right').run()} className={btnClass(editor.isActive({ textAlign: 'right' }))} title="Vpravo">R</button>

        <div className="w-px bg-gray-300 mx-1" />

        {/* Timestamp */}
        <button
          onClick={insertTimestamp}
          title={`Vložit timestamp ${secondsToTimestamp(currentAudioTime)}`}
          className="px-2 py-1 text-sm rounded border bg-red-50 border-red-300 text-red-700 hover:bg-red-100 font-mono"
        >
          ⏱ {secondsToTimestamp(currentAudioTime)}
        </button>

        {/* Find/Replace toggle */}
        <button onClick={() => setShowFindReplace(v => !v)} className={btnClass(showFindReplace)} title="Najít / Nahradit">
          ⌕ Najít
        </button>

        {/* Timestamp management */}
        <button onClick={removeAllTimestamps}
          className="px-2 py-1 text-xs rounded border bg-red-50 border-red-200 text-red-600 hover:bg-red-100"
          title="Odstraní všechny timestamps [MM:SS] z textu">
          ✕⏱ Smazat ts
        </button>

        <div className="w-px bg-gray-300 mx-1" />

        {/* Export */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-gray-500">Export:</span>
          {(['txt', 'html', 'md', 'docx', 'epub'] as const).map(f => (
            <button key={f} onClick={() => exportAs(f)}
              className="px-2 py-1 text-xs rounded border bg-gray-700 border-gray-600 text-gray-200 hover:bg-gray-600 uppercase">
              {f}
            </button>
          ))}
        </div>

        <div className="w-px bg-gray-300 mx-1" />

        {/* Uložit */}
        {onSave && (
          <button
            onClick={() => editor && onSave(editor.getHTML(), editor.getText())}
            className="px-3 py-1 text-sm rounded border bg-green-600 border-green-500 text-white hover:bg-green-500"
            title="Uložit přepis do archivu"
          >
            {saveStatus === 'saving' ? '⏳' : saveStatus === 'saved' ? '✓ Uloženo' : saveStatus === 'error' ? '✗ Chyba' : '💾 Uložit'}
          </button>
        )}
      </div>

      {/* Find / Replace panel */}
      {showFindReplace && (
        <div className="flex items-center gap-2 px-3 py-2 bg-yellow-50 border-b border-yellow-200">
          <input
            placeholder="Hledat..."
            value={findText}
            onChange={e => setFindText(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm w-40"
          />
          <input
            placeholder="Nahradit za..."
            value={replaceText}
            onChange={e => setReplaceText(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm w-40"
          />
          <button onClick={() => handleFindReplace(false)} className="px-3 py-1 text-sm bg-blue-100 rounded border border-blue-300 hover:bg-blue-200">Najít</button>
          <button onClick={() => handleFindReplace(true)} className="px-3 py-1 text-sm bg-orange-100 rounded border border-orange-300 hover:bg-orange-200">Nahradit vše</button>
          {findMsg && <span className="text-sm text-gray-600">{findMsg}</span>}
          <button onClick={() => { setShowFindReplace(false); setFindMsg('') }} className="ml-auto text-gray-400 hover:text-gray-600 text-lg leading-none">×</button>
        </div>
      )}

      {/* Editor area */}
      <div className="flex-1 overflow-auto">
        <EditorContent editor={editor} className="h-full" />
      </div>
    </div>
  )
}
