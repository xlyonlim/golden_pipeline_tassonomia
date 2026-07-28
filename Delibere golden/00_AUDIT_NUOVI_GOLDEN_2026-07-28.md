# Audit nuovi golden - 28 luglio 2026

## Esito sintetico

- Golden gia annotati nel CSV: 22 (`GOLD_0001`-`GOLD_0022`).
- Candidati gia presenti e mantenuti: 5 (`GOLD_0037`, `GOLD_0042`,
  `GOLD_0043`, `GOLD_0044`, `GOLD_0046`).
- Nuovi PDF verificati e trasferiti: 23.
- Totale attuale: 50 PDF golden.
- La quota e completa: `GOLD_0033` copre il Lazio e `GOLD_0047` la terza Sicilia.
- Tutti i 23 PDF trasferiti sono deliberazioni di Giunta comunale o municipale.
- Nessun duplicato binario e nessun PDF illeggibile.

## PDF trasferiti

| ID | Comune | Regione | Pagine | Esito |
|---|---|---|---:|---|
| GOLD_0023 | Arnad | Valle d'Aosta | 4 | Conforme |
| GOLD_0024 | Arenzano | Liguria | 7 | Conforme |
| GOLD_0025 | Bogliasco | Liguria | 7 | Conforme |
| GOLD_0026 | Tribano | Veneto | 3 | Conforme |
| GOLD_0027 | Vicenza | Veneto | 5 | Conforme |
| GOLD_0028 | Arco | Trentino-Alto Adige | 10 | Conforme |
| GOLD_0029 | Borgo Chiese | Trentino-Alto Adige | 7 | Conforme |
| GOLD_0030 | Cadelbosco di Sopra | Emilia-Romagna | 6 | Conforme |
| GOLD_0031 | Carpi | Emilia-Romagna | 3 | Conforme |
| GOLD_0032 | Monterotondo Marittimo | Toscana | 5 | Conforme |
| GOLD_0033 | Anagni | Lazio | 8 | Conforme |
| GOLD_0034 | Baschi | Umbria | 7 | Conforme |
| GOLD_0035 | San Gemini | Umbria | 6 | Conforme |
| GOLD_0036 | Giulianova | Abruzzo | 9 | Conforme |
| GOLD_0038 | Filignano | Molise | 4 | Conforme con avvertenza OCR |
| GOLD_0039 | Stornarella | Puglia | 4 | Conforme |
| GOLD_0040 | Ginestra | Basilicata | 5 | Conforme |
| GOLD_0041 | Gualtieri Sicamino | Sicilia | 6 | Conforme, documento scansionato |
| GOLD_0045 | Francofonte | Sicilia | 7 | Conforme |
| GOLD_0047 | Alimena | Sicilia | 2 | Conforme, impaginazione a due colonne |
| GOLD_0048 | Calasetta | Sardegna | 6 | Conforme |
| GOLD_0049 | Guspini | Sardegna | 5 | Conforme |
| GOLD_0050 | Irgoli | Sardegna | 5 | Conforme |

## Avvertenze

`GOLD_0038` (Filignano) e visivamente integro e leggibile, ma il livello testuale
incorporato separa o altera numerose lettere. Va mantenuto come caso difficile
per OCR, controllando l'annotazione sul documento visuale e non soltanto sul
testo estratto automaticamente.

`GOLD_0041` (Gualtieri Sicamino) e prevalentemente scansionato. Docling riesce a
riconoscere comune, Giunta, proposta e dispositivo, quindi il documento e
utilizzabile come secondo caso OCR.

## PDF esclusi

- `abbadia san salvatorepdf.pdf`: delibera valida, ma seconda unita toscana
  rispetto alla quota richiesta; e stato preferito Monterotondo Marittimo per
  la maggiore diversita tematica.
- `lentini.pdf`: delibera valida, ma composta da 281 pagine per la presenza di
  allegati contabili; avrebbe un peso sproporzionato nell'annotazione e nella
  valutazione.
- `villaurbana.PDF`: delibera valida, ma appartenente alla Sardegna; avrebbe
  creato una quarta unita sarda invece di coprire la terza Sicilia.

I tre PDF esclusi sono conservati nella sottocartella `_ESCLUSI` della cartella
di provenienza.
