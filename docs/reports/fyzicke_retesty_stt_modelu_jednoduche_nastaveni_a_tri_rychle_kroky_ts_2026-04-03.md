# Fyzicke Retesty STT Modelu, Jednoduche Nastaveni a Tri Rychle Kroky (T.S.)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: fyzicke_retesty_stt_modelu_jednoduche_nastaveni_a_tri_rychle_kroky_ts_2026-04-03.md
- last_updated_utc: 2026-04-03T17:44:53Z
- review_due_utc: 2026-04-15T00:00:00Z

## Kontekst
Tento report uklada posledni fyzicke retesty modelu pro transcript rezim, jednoduchy per-model profil pouzity pri testu a tri nejzasadnejsi kroky s nejvyssim dopadem na rychle dovedeni projektu k cilum.

## Fyzicke testy (provedene)
1. 13s, start 0s, all-installed + simple-cz-v1 profil.
2. 13s, start 60s, all-installed + simple-cz-v1 profil.
3. Predchozi fyzicky delsi probe 30s, start 0s (pro doplneni kvality).

Reference reporty:
- `runtime/logs/smoke_transcribe_13s_20260403_143640.md`
- `runtime/logs/smoke_transcribe_13s_20260403_143846.md`
- `runtime/logs/smoke_transcribe_13s_20260403_143211.md`

## Jednoduche nastaveni (simple-cz-v1)
Nastaveni bylo volene schvalne jednoduse kvuli rychlemu a reprodukovatelnemu fyzickemu overeni:
- `whisper_cpp_*`: `language=cs, threads=4, beam_size=2, best_of=2, no_fallback=true`
- `faster_whisper_small/medium`: `language=cs, threads=4, beam_size=1, best_of=1, compute_type=int8, device=cpu`
- `vosk_small_cs_0_4`: `sample_rate=16000, chunk_seconds=0.2, set_words=false`
- `sherpa_onnx_small`: `num_threads=2, decoding_method=greedy_search, provider=cpu, sample_rate=16000`
- `qwen3_asr_0_6b`: `language=Czech, dtype=float32, device_map=cpu, max_new_tokens=128`

Profil soubor:
- `runtime/logs/smoke_profile_simple_cz_v1.json`

## Stav modelu (aktualni)
### Prepisuji dobre
1. `whisper_cpp_large_v3_turbo`
2. `whisper_cpp_large_v3`
3. `faster_whisper_medium_cs_int8`

### Prepisuji jakkoliv (slabsi kvalita/kolisani)
1. `faster_whisper_small_cs_int8`
2. `whisper_cpp_small`
3. `whisper_cpp_base`
4. `vosk_small_cs_0_4`
5. `sherpa_onnx_small` (technicky bezi, ale kvalita casto nevyhovujici pro CZ)

### Blokovane / nefunkcni
1. `qwen3_asr_0_6b` (`No module named 'qwen_asr'`)

## Tri nejzasadnejsi kroky s nejrychlejsim dopadem
1. Zamknout default model gate na kvalitni trio (`turbo`, `large_v3`, `faster_medium`) a ostatni explicitne jako `experimental/blocked`.
2. Dokoncit V6-S4 long-run gate (30/120/240 min + recovery/restart) jako tvrdy release gate.
3. Zavest runtime readiness gate pred jobem (dependency + bundle kompatibilita) s cistym `blocked` vysledkem misto padu.

## Zaver
Aktualni fyzicke testy potvrzuji provozne funkcni zaklad (8 modelu pass ve smoke), ale produktove kvalitni default by mel zustat omezen na modely s konzistentne dobrym CZ vystupem. Rychly posun k cili zajisti kombinace default gate, long-run gate a readiness gate.
