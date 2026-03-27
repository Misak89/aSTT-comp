/** Zkrátí název videa na max 24 znaků (celé slovo) a přidá _videoId.
 *  Příklad: "PlayStation VR2 Tech rozhovor_R3BsjbDtWrY"
 */
export function videoLabel(title: string, videoId: string): string {
  let short = title.trim()
  if (short.length > 24) {
    short = short.slice(0, 24).replace(/\s+\S*$/, '').trimEnd()
  }
  return `${short}_${videoId}`
}
