# Kompletní data — tuning v2

**Job:** `tune_20260327_025443_6200bb`
**Label:** `TestTurbo_FullDoubleCZ-NEW`
**Dokončen:** 2026-03-27, ~13:00 CET
**Trvání:** ~9 h reálného času (start 03:54 CET), 4.34 h výpočetního času

## Konfigurace

- **Modely:** `whisper_cpp_small`, `whisper_cpp_large_v3_turbo`
- **Videa:** `s9F5qXVK4uU` (548 s, médium obtížnost), `ls5MvHhjYGg`
- **Sample:** 156 s / video, clip_seed=41
- **initial_prompt:** `''` (prázdný — žádný prompt ve všech 160 triálech!)
- **Parametrický prostor:** beam_size × best_of × threads × no_fallback, chunk=30 s
- **Triálů:** 160 (80 per model), všechny úspěšně dokončeny, 0 chyb

## Souhrn per model

| Model | WER min | WER avg | WER max | RTF min | RTF avg | RTF max | Viable |
|---|---|---|---|---|---|---|---|
| whisper_cpp_small | 0.2758 | 0.2938 | 0.3318 | 0.206 | 0.360 | 0.589 | 80/80 |
| whisper_cpp_large_v3_turbo | **0.1757** | **0.1778** | **0.1827** | 0.691 | 1.125 | 1.997 | 41/80 |

## Pareto fronta (WER × RTF, oba minimalizovány)

| Trial | Model | beam | threads | WER | RTF | Viable |
|---|---|---|---|---|---|---|
| #102 | large_v3_turbo | 2 | 8 | **0.1757** | **0.691** | ✓ |
| #55 | small | 5 | 8 | 0.2758 | 0.282 | ✓ |
| #30 | small | 3 | 8 | 0.2865 | 0.256 | ✓ |
| #6 | small | 1 | 8 | 0.3120 | 0.212 | ✓ |

## Vliv threads na large_v3_turbo (beam=2)

| threads | WER | RTF | Viable | Latence |
|---|---|---|---|---|
| 2 | 0.1757 | 1.52 | ✗ | 237 ms |
| 4 | 0.1757 | 0.96–0.98 | ✓ ⚠ | 149–153 ms |
| 6 | 0.1757 | 0.77–0.78 | ✓ | 121–122 ms |
| 8 | **0.1757** | **0.69–0.69** | ✓ | **108 ms** |

## Kompletní tabulka výsledků (CSV)

```csv
trial_idx,model_id,beam_size,best_of,threads,no_fallback,chunk_s,wer,wer_soft,cer,rtf,rtf_viable,latency_ms,perceived_delay_s,elapsed_s
0,whisper_cpp_small,1,1,2,False,30,0.3120,0.2345,0.1691,0.4203,True,65564,221.6,65.6
1,whisper_cpp_small,1,1,2,True,30,0.3120,0.2345,0.1691,0.4315,True,67302,223.3,67.3
2,whisper_cpp_small,1,1,4,False,30,0.3120,0.2345,0.1691,0.2834,True,44210,200.2,44.2
3,whisper_cpp_small,1,1,4,True,30,0.3120,0.2345,0.1691,0.2747,True,42857,198.9,42.9
4,whisper_cpp_small,1,1,6,False,30,0.3120,0.2345,0.1691,0.2226,True,34723,190.7,34.7
5,whisper_cpp_small,1,1,6,True,30,0.3120,0.2345,0.1691,0.2334,True,36398,192.4,36.4
6,whisper_cpp_small,1,1,8,False,30,0.3120,0.2345,0.1691,0.2123,True,33120,189.1,33.1
7,whisper_cpp_small,1,1,8,True,30,0.3120,0.2345,0.1691,0.2058,True,32104,188.1,32.1
8,whisper_cpp_small,2,1,2,False,30,0.3318,0.2348,0.1731,0.5148,True,80306,236.3,80.3
9,whisper_cpp_small,2,1,2,True,30,0.3318,0.2348,0.1731,0.5122,True,79911,235.9,79.9
10,whisper_cpp_small,2,1,4,False,30,0.3318,0.2348,0.1731,0.3196,True,49845,205.9,49.8
11,whisper_cpp_small,2,1,4,True,30,0.3318,0.2348,0.1731,0.3261,True,50873,206.9,50.9
12,whisper_cpp_small,2,1,6,False,30,0.3318,0.2348,0.1731,0.2719,True,42418,198.4,42.4
13,whisper_cpp_small,2,1,6,True,30,0.3318,0.2348,0.1731,0.2733,True,42634,198.6,42.6
14,whisper_cpp_small,2,1,8,False,30,0.3318,0.2348,0.1731,0.2441,True,38084,194.1,38.1
15,whisper_cpp_small,2,1,8,True,30,0.3318,0.2348,0.1731,0.2430,True,37919,193.9,37.9
16,whisper_cpp_small,2,2,2,False,30,0.3318,0.2348,0.1731,0.5101,True,79565,235.6,79.6
17,whisper_cpp_small,2,2,2,True,30,0.3318,0.2348,0.1731,0.5037,True,78586,234.6,78.6
18,whisper_cpp_small,2,2,4,False,30,0.3318,0.2348,0.1731,0.3200,True,49911,205.9,49.9
19,whisper_cpp_small,2,2,4,True,30,0.3318,0.2348,0.1731,0.3118,True,48640,204.6,48.6
20,whisper_cpp_small,2,2,6,False,30,0.3318,0.2348,0.1731,0.2620,True,40866,196.9,40.9
21,whisper_cpp_small,2,2,6,True,30,0.3318,0.2348,0.1731,0.3033,True,47309,203.3,47.3
22,whisper_cpp_small,2,2,8,False,30,0.3318,0.2348,0.1731,0.2366,True,36910,192.9,36.9
23,whisper_cpp_small,2,2,8,True,30,0.3318,0.2348,0.1731,0.2375,True,37058,193.1,37.1
24,whisper_cpp_small,3,1,2,False,30,0.2865,0.2052,0.1580,0.5280,True,82380,238.4,82.4
25,whisper_cpp_small,3,1,2,True,30,0.2865,0.2052,0.1580,0.5378,True,83898,239.9,83.9
26,whisper_cpp_small,3,1,4,False,30,0.2865,0.2052,0.1580,0.3479,True,54272,210.3,54.3
27,whisper_cpp_small,3,1,4,True,30,0.2865,0.2052,0.1580,0.3507,True,54704,210.7,54.7
28,whisper_cpp_small,3,1,6,False,30,0.2865,0.2052,0.1580,0.2879,True,44925,200.9,44.9
29,whisper_cpp_small,3,1,6,True,30,0.2865,0.2052,0.1580,0.2897,True,45204,201.2,45.2
30,whisper_cpp_small,3,1,8,False,30,0.2865,0.2052,0.1580,0.2563,True,39984,196.0,40.0
31,whisper_cpp_small,3,1,8,True,30,0.2865,0.2052,0.1580,0.2567,True,40042,196.0,40.0
32,whisper_cpp_small,3,2,2,False,30,0.2865,0.2052,0.1580,0.5375,True,83855,239.8,83.9
33,whisper_cpp_small,3,2,2,True,30,0.2865,0.2052,0.1580,0.5415,True,84470,240.5,84.5
34,whisper_cpp_small,3,2,4,False,30,0.2865,0.2052,0.1580,0.3487,True,54400,210.4,54.4
35,whisper_cpp_small,3,2,4,True,30,0.2865,0.2052,0.1580,0.3474,True,54190,210.2,54.2
36,whisper_cpp_small,3,2,6,False,30,0.2865,0.2052,0.1580,0.2856,True,44560,200.6,44.6
37,whisper_cpp_small,3,2,6,True,30,0.2865,0.2052,0.1580,0.2881,True,44948,200.9,44.9
38,whisper_cpp_small,3,2,8,False,30,0.2865,0.2052,0.1580,0.2596,True,40497,196.5,40.5
39,whisper_cpp_small,3,2,8,True,30,0.2865,0.2052,0.1580,0.2552,True,39820,195.8,39.8
40,whisper_cpp_small,3,3,2,False,30,0.2865,0.2052,0.1580,0.5378,True,83898,239.9,83.9
41,whisper_cpp_small,3,3,2,True,30,0.2865,0.2052,0.1580,0.5379,True,83915,239.9,83.9
42,whisper_cpp_small,3,3,4,False,30,0.2865,0.2052,0.1580,0.3483,True,54348,210.3,54.3
43,whisper_cpp_small,3,3,4,True,30,0.2865,0.2052,0.1580,0.3529,True,55057,211.1,55.1
44,whisper_cpp_small,3,3,6,False,30,0.2865,0.2052,0.1580,0.2902,True,45268,201.3,45.3
45,whisper_cpp_small,3,3,6,True,30,0.2865,0.2052,0.1580,0.2898,True,45217,201.2,45.2
46,whisper_cpp_small,3,3,8,False,30,0.2865,0.2052,0.1580,0.2586,True,40334,196.3,40.3
47,whisper_cpp_small,3,3,8,True,30,0.2865,0.2052,0.1580,0.2569,True,40070,196.1,40.1
48,whisper_cpp_small,5,1,2,False,30,0.2758,0.2016,0.1544,0.5847,True,91226,247.2,91.2
49,whisper_cpp_small,5,1,2,True,30,0.2758,0.2016,0.1544,0.5845,True,91172,247.2,91.2
50,whisper_cpp_small,5,1,4,False,30,0.2758,0.2016,0.1544,0.3861,True,60230,216.2,60.2
51,whisper_cpp_small,5,1,4,True,30,0.2758,0.2016,0.1544,0.3856,True,60160,216.2,60.2
52,whisper_cpp_small,5,1,6,False,30,0.2758,0.2016,0.1544,0.3122,True,48693,204.7,48.7
53,whisper_cpp_small,5,1,6,True,30,0.2758,0.2016,0.1544,0.3132,True,48859,204.9,48.9
54,whisper_cpp_small,5,1,8,False,30,0.2758,0.2016,0.1544,0.2819,True,43975,200.0,44.0
55,whisper_cpp_small,5,1,8,True,30,0.2758,0.2016,0.1544,0.2817,True,43952,199.9,44.0
56,whisper_cpp_small,5,2,2,False,30,0.2758,0.2016,0.1544,0.5874,True,91634,247.6,91.6
57,whisper_cpp_small,5,2,2,True,30,0.2758,0.2016,0.1544,0.5862,True,91452,247.4,91.5
58,whisper_cpp_small,5,2,4,False,30,0.2758,0.2016,0.1544,0.3882,True,60564,216.6,60.6
59,whisper_cpp_small,5,2,4,True,30,0.2758,0.2016,0.1544,0.3884,True,60598,216.6,60.6
60,whisper_cpp_small,5,2,6,False,30,0.2758,0.2016,0.1544,0.3120,True,48670,204.7,48.7
61,whisper_cpp_small,5,2,6,True,30,0.2758,0.2016,0.1544,0.3118,True,48642,204.6,48.6
62,whisper_cpp_small,5,2,8,False,30,0.2758,0.2016,0.1544,0.2832,True,44172,200.2,44.2
63,whisper_cpp_small,5,2,8,True,30,0.2758,0.2016,0.1544,0.2867,True,44719,200.7,44.7
64,whisper_cpp_small,5,3,2,False,30,0.2758,0.2016,0.1544,0.5833,True,90986,247.0,91.0
65,whisper_cpp_small,5,3,2,True,30,0.2758,0.2016,0.1544,0.5873,True,91620,247.6,91.6
66,whisper_cpp_small,5,3,4,False,30,0.2758,0.2016,0.1544,0.3872,True,60406,216.4,60.4
67,whisper_cpp_small,5,3,4,True,30,0.2758,0.2016,0.1544,0.3901,True,60867,216.9,60.9
68,whisper_cpp_small,5,3,6,False,30,0.2758,0.2016,0.1544,0.3116,True,48606,204.6,48.6
69,whisper_cpp_small,5,3,6,True,30,0.2758,0.2016,0.1544,0.3124,True,48737,204.7,48.7
70,whisper_cpp_small,5,3,8,False,30,0.2758,0.2016,0.1544,0.2844,True,44373,200.4,44.4
71,whisper_cpp_small,5,3,8,True,30,0.2758,0.2016,0.1544,0.2839,True,44284,200.3,44.3
72,whisper_cpp_small,5,5,2,False,30,0.2758,0.2016,0.1544,0.5882,True,91761,247.8,91.8
73,whisper_cpp_small,5,5,2,True,30,0.2758,0.2016,0.1544,0.5887,True,91834,247.8,91.8
74,whisper_cpp_small,5,5,4,False,30,0.2758,0.2016,0.1544,0.3908,True,60959,217.0,61.0
75,whisper_cpp_small,5,5,4,True,30,0.2758,0.2016,0.1544,0.3879,True,60510,216.5,60.5
76,whisper_cpp_small,5,5,6,False,30,0.2758,0.2016,0.1544,0.3111,True,48534,204.5,48.5
77,whisper_cpp_small,5,5,6,True,30,0.2758,0.2016,0.1544,0.3123,True,48714,204.7,48.7
78,whisper_cpp_small,5,5,8,False,30,0.2758,0.2016,0.1544,0.2847,True,44410,200.4,44.4
79,whisper_cpp_small,5,5,8,True,30,0.2758,0.2016,0.1544,0.2848,True,44426,200.4,44.4
80,whisper_cpp_large_v3_turbo,1,1,2,False,30,0.1827,0.1512,0.1271,1.6242,False,253377,409.4,253.4
81,whisper_cpp_large_v3_turbo,1,1,2,True,30,0.1827,0.1512,0.1271,1.6030,False,250068,406.1,250.1
82,whisper_cpp_large_v3_turbo,1,1,4,False,30,0.1827,0.1512,0.1271,0.9961,True,155388,311.4,155.4
83,whisper_cpp_large_v3_turbo,1,1,4,True,30,0.1827,0.1512,0.1271,1.0014,False,156225,312.2,156.2
84,whisper_cpp_large_v3_turbo,1,1,6,False,30,0.1827,0.1512,0.1271,0.8078,True,126022,282.0,126.0
85,whisper_cpp_large_v3_turbo,1,1,6,True,30,0.1827,0.1512,0.1271,0.8109,True,126496,282.5,126.5
86,whisper_cpp_large_v3_turbo,1,1,8,False,30,0.1827,0.1512,0.1271,0.7163,True,111736,267.7,111.7
87,whisper_cpp_large_v3_turbo,1,1,8,True,30,0.1827,0.1512,0.1271,0.7222,True,112662,268.7,112.7
88,whisper_cpp_large_v3_turbo,2,1,2,False,30,0.1757,0.1372,0.1252,1.5185,False,236882,392.9,236.9
89,whisper_cpp_large_v3_turbo,2,1,2,True,30,0.1757,0.1372,0.1252,1.5237,False,237702,393.7,237.7
90,whisper_cpp_large_v3_turbo,2,1,4,False,30,0.1757,0.1372,0.1252,0.9573,True,149332,305.3,149.3
91,whisper_cpp_large_v3_turbo,2,1,4,True,30,0.1757,0.1372,0.1252,0.9601,True,149776,305.8,149.8
92,whisper_cpp_large_v3_turbo,2,1,6,False,30,0.1757,0.1372,0.1252,0.7761,True,121070,277.1,121.1
93,whisper_cpp_large_v3_turbo,2,1,6,True,30,0.1757,0.1372,0.1252,0.7782,True,121388,277.4,121.4
94,whisper_cpp_large_v3_turbo,2,1,8,False,30,0.1757,0.1372,0.1252,0.6957,True,108533,264.5,108.5
95,whisper_cpp_large_v3_turbo,2,1,8,True,30,0.1757,0.1372,0.1252,0.6969,True,108718,264.7,108.7
96,whisper_cpp_large_v3_turbo,2,2,2,False,30,0.1757,0.1372,0.1252,1.5267,False,238164,394.2,238.2
97,whisper_cpp_large_v3_turbo,2,2,2,True,30,0.1757,0.1372,0.1252,1.5650,False,244149,400.1,244.1
98,whisper_cpp_large_v3_turbo,2,2,4,False,30,0.1757,0.1372,0.1252,0.9798,True,152852,308.8,152.9
99,whisper_cpp_large_v3_turbo,2,2,4,True,30,0.1757,0.1372,0.1252,0.9843,True,153546,309.6,153.5
100,whisper_cpp_large_v3_turbo,2,2,6,False,30,0.1757,0.1372,0.1252,0.7843,True,122344,278.4,122.3
101,whisper_cpp_large_v3_turbo,2,2,6,True,30,0.1757,0.1372,0.1252,0.7692,True,119998,276.0,120.0
102,whisper_cpp_large_v3_turbo,2,2,8,False,30,0.1757,0.1372,0.1252,0.6906,True,107740,263.7,107.7
103,whisper_cpp_large_v3_turbo,2,2,8,True,30,0.1757,0.1372,0.1252,0.6916,True,107891,263.9,107.9
104,whisper_cpp_large_v3_turbo,3,1,2,False,30,0.1771,0.1472,0.1255,1.7269,False,269404,425.4,269.4
105,whisper_cpp_large_v3_turbo,3,1,2,True,30,0.1771,0.1472,0.1255,1.7289,False,269715,425.7,269.7
106,whisper_cpp_large_v3_turbo,3,1,4,False,30,0.1771,0.1472,0.1255,1.0792,False,168353,324.4,168.4
107,whisper_cpp_large_v3_turbo,3,1,4,True,30,0.1771,0.1472,0.1255,1.0785,False,168254,324.2,168.3
108,whisper_cpp_large_v3_turbo,3,1,6,False,30,0.1771,0.1472,0.1255,0.8542,True,133264,289.3,133.3
109,whisper_cpp_large_v3_turbo,3,1,6,True,30,0.1771,0.1472,0.1255,0.8542,True,133262,289.3,133.3
110,whisper_cpp_large_v3_turbo,3,1,8,False,30,0.1771,0.1472,0.1255,0.7690,True,119960,276.0,120.0
111,whisper_cpp_large_v3_turbo,3,1,8,True,30,0.1771,0.1472,0.1255,0.7675,True,119738,275.7,119.7
112,whisper_cpp_large_v3_turbo,3,2,2,False,30,0.1771,0.1472,0.1255,1.6995,False,265116,421.1,265.1
113,whisper_cpp_large_v3_turbo,3,2,2,True,30,0.1771,0.1472,0.1255,1.6807,False,262193,418.2,262.2
114,whisper_cpp_large_v3_turbo,3,2,4,False,30,0.1771,0.1472,0.1255,1.0790,False,168317,324.3,168.3
115,whisper_cpp_large_v3_turbo,3,2,4,True,30,0.1771,0.1472,0.1255,1.0787,False,168272,324.3,168.3
116,whisper_cpp_large_v3_turbo,3,2,6,False,30,0.1771,0.1472,0.1255,0.8762,True,136694,292.7,136.7
117,whisper_cpp_large_v3_turbo,3,2,6,True,30,0.1771,0.1472,0.1255,0.8688,True,135522,291.5,135.5
118,whisper_cpp_large_v3_turbo,3,2,8,False,30,0.1771,0.1472,0.1255,0.7756,True,120996,277.0,121.0
119,whisper_cpp_large_v3_turbo,3,2,8,True,30,0.1771,0.1472,0.1255,0.7742,True,120780,276.8,120.8
120,whisper_cpp_large_v3_turbo,3,3,2,False,30,0.1771,0.1472,0.1255,1.6786,False,261865,417.9,261.9
121,whisper_cpp_large_v3_turbo,3,3,2,True,30,0.1771,0.1472,0.1255,1.6873,False,263224,419.2,263.2
122,whisper_cpp_large_v3_turbo,3,3,4,False,30,0.1771,0.1472,0.1255,1.0850,False,169255,325.3,169.3
123,whisper_cpp_large_v3_turbo,3,3,4,True,30,0.1771,0.1472,0.1255,1.0837,False,169060,325.1,169.1
124,whisper_cpp_large_v3_turbo,3,3,6,False,30,0.1771,0.1472,0.1255,0.8681,True,135426,291.4,135.4
125,whisper_cpp_large_v3_turbo,3,3,6,True,30,0.1771,0.1472,0.1255,0.8695,True,135636,291.6,135.6
126,whisper_cpp_large_v3_turbo,3,3,8,False,30,0.1771,0.1472,0.1255,0.7777,True,121330,277.3,121.3
127,whisper_cpp_large_v3_turbo,3,3,8,True,30,0.1771,0.1472,0.1255,0.7929,True,123688,279.7,123.7
128,whisper_cpp_large_v3_turbo,5,1,2,False,30,0.1782,0.1443,0.1245,1.7567,False,274040,430.0,274.0
129,whisper_cpp_large_v3_turbo,5,1,2,True,30,0.1782,0.1443,0.1245,1.7634,False,275101,431.1,275.1
130,whisper_cpp_large_v3_turbo,5,1,4,False,30,0.1782,0.1443,0.1245,1.1473,False,178970,335.0,179.0
131,whisper_cpp_large_v3_turbo,5,1,4,True,30,0.1782,0.1443,0.1245,1.1262,False,175700,331.7,175.7
132,whisper_cpp_large_v3_turbo,5,1,6,False,30,0.1782,0.1443,0.1245,0.9077,True,141602,297.6,141.6
133,whisper_cpp_large_v3_turbo,5,1,6,True,30,0.1782,0.1443,0.1245,0.9099,True,141937,297.9,141.9
134,whisper_cpp_large_v3_turbo,5,1,8,False,30,0.1782,0.1443,0.1245,0.8108,True,126488,282.5,126.5
135,whisper_cpp_large_v3_turbo,5,1,8,True,30,0.1782,0.1443,0.1245,0.8143,True,127029,283.0,127.0
136,whisper_cpp_large_v3_turbo,5,2,2,False,30,0.1782,0.1443,0.1245,1.7602,False,274592,430.6,274.6
137,whisper_cpp_large_v3_turbo,5,2,2,True,30,0.1782,0.1443,0.1245,1.7620,False,274868,430.9,274.9
138,whisper_cpp_large_v3_turbo,5,2,4,False,30,0.1782,0.1443,0.1245,1.1264,False,175720,331.7,175.7
139,whisper_cpp_large_v3_turbo,5,2,4,True,30,0.1782,0.1443,0.1245,1.1315,False,176510,332.5,176.5
140,whisper_cpp_large_v3_turbo,5,2,6,False,30,0.1782,0.1443,0.1245,0.9293,True,144968,301.0,145.0
141,whisper_cpp_large_v3_turbo,5,2,6,True,30,0.1782,0.1443,0.1245,0.9122,True,142304,298.3,142.3
142,whisper_cpp_large_v3_turbo,5,2,8,False,30,0.1782,0.1443,0.1245,0.8530,True,133064,289.1,133.1
143,whisper_cpp_large_v3_turbo,5,2,8,True,30,0.1782,0.1443,0.1245,0.8682,True,135448,291.4,135.4
144,whisper_cpp_large_v3_turbo,5,3,2,False,30,0.1782,0.1443,0.1245,1.9417,False,302906,458.9,302.9
145,whisper_cpp_large_v3_turbo,5,3,2,True,30,0.1782,0.1443,0.1245,1.9970,False,311534,467.5,311.5
146,whisper_cpp_large_v3_turbo,5,3,4,False,30,0.1782,0.1443,0.1245,1.2460,False,194380,350.4,194.4
147,whisper_cpp_large_v3_turbo,5,3,4,True,30,0.1782,0.1443,0.1245,1.2178,False,189974,346.0,190.0
148,whisper_cpp_large_v3_turbo,5,3,6,False,30,0.1782,0.1443,0.1245,1.0663,False,166346,322.3,166.3
149,whisper_cpp_large_v3_turbo,5,3,6,True,30,0.1782,0.1443,0.1245,1.0163,False,158538,314.5,158.5
150,whisper_cpp_large_v3_turbo,5,3,8,False,30,0.1782,0.1443,0.1245,0.9358,True,145978,302.0,146.0
151,whisper_cpp_large_v3_turbo,5,3,8,True,30,0.1782,0.1443,0.1245,0.9113,True,142162,298.2,142.2
152,whisper_cpp_large_v3_turbo,5,5,2,False,30,0.1782,0.1443,0.1245,1.9892,False,310314,466.3,310.3
153,whisper_cpp_large_v3_turbo,5,5,2,True,30,0.1782,0.1443,0.1245,1.9676,False,306952,462.9,307.0
154,whisper_cpp_large_v3_turbo,5,5,4,False,30,0.1782,0.1443,0.1245,1.2427,False,193854,349.9,193.9
155,whisper_cpp_large_v3_turbo,5,5,4,True,30,0.1782,0.1443,0.1245,1.2674,False,197724,353.7,197.7
156,whisper_cpp_large_v3_turbo,5,5,6,False,30,0.1782,0.1443,0.1245,1.0137,False,158134,314.1,158.1
157,whisper_cpp_large_v3_turbo,5,5,6,True,30,0.1782,0.1443,0.1245,1.0202,False,159153,315.2,159.2
158,whisper_cpp_large_v3_turbo,5,5,8,False,30,0.1782,0.1443,0.1245,0.9304,True,145130,301.1,145.1
159,whisper_cpp_large_v3_turbo,5,5,8,True,30,0.1782,0.1443,0.1245,0.9206,True,143624,299.6,143.6
```

## Statistická signifikance (95% CI, n=763 slov/trial)

| Srovnání | Rozdíl WER | z-skóre | Signifikantní? |
|---|---|---|---|
| large vs small (best) | 0.1000 | **4.70** | **ANO** |
| beam=2 vs beam=3 (large) | 0.0014 | 0.07 | **NE — šum** |
| beam=1 vs beam=2 (large) | 0.0070 | 0.37 | NE |

95% CI: large WER=0.176 ±0.027 = [0.149; 0.203]

## Klíčové kontextové poznámky

1. **Bez promptu** — všechny triály s `initial_prompt=''`. Předchozí test ukázal prompt snižuje WER z 1.84→0.97 (47%). Výsledky nepředstavují produkční podmínky.
2. **elapsed_s = 49 % reálného času** — model loading (large: +177 s/trial) není zachycen. Odhad délky jobu je 2× příliš krátký.
3. **2 videa** — per-video WER rozdíl 45% (0.143 vs 0.208). Výsledky nejsou statisticky robustní pro beam-level rozhodnutí.
4. **RAM**: není měřena. Aproximace: small ~600 MB, large_v3_turbo ~1 600 MB.

## Zdroje

- Surová data: `runtime/tuning/tune_20260327_025443_6200bb/status.json` (může být smazáno cleanup po 10+ dnech)
- Analýza: `docs/tuning_analyza_2026-03-27.md`
- Kritika analýzy: konverzace ze dne 2026-03-27 (uložena v session_log.md)
