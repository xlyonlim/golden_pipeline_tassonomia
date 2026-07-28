# Convenzione degli identificativi

## Spazi separati

- `GOLD_0001`, `GOLD_0002`, ... identificano esclusivamente i documenti golden.
- `INPUT_0001`, `INPUT_0002`, ... identificano esclusivamente i documenti
  del campione sul quale applicare il modello.
- Il nome del PDF coincide sempre con l'identificativo, per esempio
  `GOLD_0001.pdf` o `INPUT_0001.pdf`.

Regione, comune, data e argomento non sono codificati nel nome: sono metadati e
possono cambiare o essere corretti senza modificare l'identificativo.

## Regole di stabilita

1. Un identificativo assegnato non deve essere cambiato o riutilizzato.
2. I nuovi PDF ricevono il progressivo successivo del proprio insieme.
3. Un documento spostato fuori dal campione mantiene il proprio ID nel manifest.
4. Le due sequenze sono indipendenti: `GOLD_0001` e `INPUT_0001` sono documenti
   diversi e non entrano mai in conflitto perche il prefisso fa parte dell'ID.
5. I nomi `ATTO_n` sono legacy e possono comparire soltanto negli archivi e nelle
   colonne di tracciabilita della migrazione.

## File di controllo

- `manifest_documenti.csv` censisce i documenti primari e i relativi hash.
- `00_mappa_migrazione_id.csv` conserva la corrispondenza tra ID vecchi e nuovi.
- `Archivio_identificativi_legacy` conserva i report storici non piu validi per
  il contenuto attuale delle cartelle.

Lo script `01_estrazione_e_segmentazione.py` riconosce gli ID gia assegnati e
non li rinumera. Eventuali PDF con nomi non normalizzati ricevono il primo
progressivo successivo disponibile.
