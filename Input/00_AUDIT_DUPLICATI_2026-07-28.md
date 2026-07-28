# Audit duplicati PDF non golden - 28 luglio 2026

## Esito

- PDF non golden controllati prima della scrematura: 29.
- PDF rimasti nel campione primario dopo la scrematura e il recupero degli
  esclusi golden compatibili: 20.
- PDF archiviati nel corpus secondario: 12.
- PDF illeggibili o non validi: 0.
- Gruppi di duplicati binari interni: 0.
- Coincidenze binarie tra non golden e golden: 0.
- Coppie semanticamente compatibili con lo stesso documento: 0.

Il controllo semantico e stato eseguito sul testo completo mediante similarita
TF-IDF a n-grammi di caratteri, oltre al confronto degli identificativi degli
atti segnalati.

## Coppie controllate manualmente

### INPUT_0006 e legacy ATTO_018

Non sono duplicati. Sono due deliberazioni del Comune di Moimacco che usano lo
stesso modello grafico e trattano temi simili, ma hanno elementi identificativi
diversi:

- `INPUT_0006`: deliberazione n. 9 del 30 gennaio 2026, relativa a CAFC S.p.A.
- Legacy `ATTO_018`: deliberazione n. 26 del 31 marzo 2026, relativa ad A&T 2000 S.p.A.

La loro similarita deriva dal comune, dal modello e dalla materia delle societa
partecipate.

### Legacy ATTO_015 e GOLD_0003

Non sono duplicati. Entrambi riguardano il PIAO e riportano ampi passaggi della
stessa normativa, ma sono atti distinti:

- Legacy `ATTO_015`: Comune di Carinaro, PIAO 2026-2028.
- `GOLD_0003`: Comune di Africo, PIAO 2022-2024.

## Nota sul vecchio CSV

`Archivio_identificativi_legacy/00_pdf_duplicati_input_LEGACY.csv` documenta tre
duplicati individuati ed eliminati durante una precedente preparazione del
dataset. I nomi numerici sono stati poi riassegnati ai PDF rimasti; il CSV non
descrive quindi duplicati ancora presenti nella cartella attuale.
