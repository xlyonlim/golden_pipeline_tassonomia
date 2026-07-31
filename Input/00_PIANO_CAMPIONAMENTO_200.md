# Piano di campionamento degli input - 200 delibere

## Obiettivo

Costruire un campione stratificato regionale di 200 deliberazioni di Giunta
comunale o municipale:

- 20 regioni;
- 10 delibere per regione;
- 200 comuni distinti nel campione principale;
- nessun PDF presente nel golden set;
- nessun duplicato binario o ricodificato.

Il Trentino-Alto Adige e considerato come una sola regione. Non sono previste
quote separate per le province autonome di Trento e Bolzano.

## Situazione attuale - 31 luglio 2026

La cartella `Input` contiene 159 PDF. `Trivento.pdf` e una deliberazione del
Consiglio comunale e non puo essere conteggiata. Inoltre,
`delibera copia conforme (8).pdf` e un duplicato binario di
`Fara Filiorum Petri.pdf`. Restano quindi 157 deliberazioni di Giunta uniche e
potenzialmente valide.

Valle d'Aosta ha un documento eccedente, il Piemonte ne ha due, la Liguria ne
ha uno e il Lazio ne ha uno. Gli eccedenti non sono necessariamente da
eliminare: vanno conservati fuori dal campione principale dopo il controllo
qualitativo finale. Ai fini delle quote regionali risultano 152 documenti
conteggiabili e occorre raccoglierne altri 48.

`Casalbordino.pdf` contiene in realta una deliberazione del Comune di Casoli.
Il documento e valido e appartiene comunque all'Abruzzo, ma il nome del file va
corretto prima della rinumerazione definitiva.

| Regione | PDF presenti | Non validi | Duplicati | Conteggiabili | Eccedenti | Da raccogliere | Totale finale |
|---|---:|---:|---:|---:|---:|---:|---:|
| Valle d'Aosta | 11 | 0 | 0 | 10 | 1 | 0 | 10 |
| Piemonte | 12 | 0 | 0 | 10 | 2 | 0 | 10 |
| Liguria | 11 | 0 | 0 | 10 | 1 | 0 | 10 |
| Lombardia | 10 | 0 | 0 | 10 | 0 | 0 | 10 |
| Trentino-Alto Adige | 10 | 0 | 0 | 10 | 0 | 0 | 10 |
| Veneto | 10 | 0 | 0 | 10 | 0 | 0 | 10 |
| Friuli-Venezia Giulia | 10 | 0 | 0 | 10 | 0 | 0 | 10 |
| Emilia-Romagna | 10 | 0 | 0 | 10 | 0 | 0 | 10 |
| Toscana | 10 | 0 | 0 | 10 | 0 | 0 | 10 |
| Umbria | 10 | 0 | 0 | 10 | 0 | 0 | 10 |
| Marche | 9 | 0 | 0 | 9 | 0 | 1 | 10 |
| Lazio | 11 | 0 | 0 | 10 | 1 | 0 | 10 |
| Abruzzo | 10 | 0 | 1 | 9 | 0 | 1 | 10 |
| Molise | 8 | 1 | 0 | 7 | 0 | 3 | 10 |
| Campania | 3 | 0 | 0 | 3 | 0 | 7 | 10 |
| Puglia | 1 | 0 | 0 | 1 | 0 | 9 | 10 |
| Basilicata | 0 | 0 | 0 | 0 | 0 | 10 | 10 |
| Calabria | 10 | 0 | 0 | 10 | 0 | 0 | 10 |
| Sicilia | 1 | 0 | 0 | 1 | 0 | 9 | 10 |
| Sardegna | 2 | 0 | 0 | 2 | 0 | 8 | 10 |
| **Totale** | **159** | **1** | **1** | **152** | **5** | **48** | **200** |

## Input mantenuti

| Atto | Comune | Regione |
|---|---|---|
| INPUT_0001 | Ausonia | Lazio |
| INPUT_0002 | Bitti | Sardegna |
| INPUT_0003 | Carbonara di Nola | Campania |
| INPUT_0004 | Carinaro | Campania |
| INPUT_0005 | Dervio | Lombardia |
| INPUT_0006 | Moimacco | Friuli-Venezia Giulia |
| INPUT_0007 | Mombello Monferrato | Piemonte |
| INPUT_0008 | Molise | Molise |
| INPUT_0009 | Mompantero | Piemonte |
| INPUT_0010 | Caltanissetta | Sicilia |
| INPUT_0011 | Trani | Puglia |
| INPUT_0012 | Campobasso | Molise |
| INPUT_0013 | Pistoia | Toscana |
| INPUT_0014 | Jelsi | Molise |
| INPUT_0015 | Poggibonsi | Toscana |
| INPUT_0016 | Solofra | Campania |
| INPUT_0017 | Selvino | Lombardia |
| INPUT_0018 | Abbadia San Salvatore | Toscana |
| INPUT_0019 | Villaurbana | Sardegna |
| INPUT_0020 | Varallo Pombia | Piemonte |

## Regole di raccolta

Per ogni nuovo PDF:

1. Deve essere una deliberazione completa della Giunta comunale o municipale.
2. Il comune non deve essere gia presente negli input primari o nei golden.
3. Deve essere identificabile almeno da comune, numero, data e oggetto.
4. Non deve essere un fascicolo composto da molte deliberazioni differenti.
5. Non deve superare 30 pagine, salvo casi difficili inseriti deliberatamente.
6. Deve contenere il dispositivo finale e non soltanto la proposta o un allegato.
7. Deve essere conservato il PDF originale, senza stampa o riconversione.

Per ciascuna regione:

- includere almeno un capoluogo, quando disponibile;
- includere almeno tre comuni con meno di 10.000 abitanti;
- variare gli altri comuni per dimensione e provincia;
- raccogliere 4 documenti brevi (1-5 pagine);
- raccogliere 4 documenti medi (6-10 pagine);
- raccogliere 2 documenti lunghi (11-30 pagine);
- distribuire i temi tra finanza/personale, lavori pubblici/urbanistica,
  scuola/sociale/cultura e atti istituzionali/legali.

Nel campione complessivo sono utili 20-30 PDF scansionati o con OCR difficile,
distribuiti tra regioni diverse. Questi casi devono restare una minoranza
controllata.

## Input archiviati

Gli atti ripetuti dello stesso comune non sono stati eliminati: sono conservati
in `Archivio_input_campionamento_200/STESSO_COMUNE` come corpus secondario per
verificare la coerenza del modello sullo stesso modello amministrativo.

Il legacy `ATTO_027`, deliberazione del Consiglio comunale, e conservato separatamente in
`Archivio_input_campionamento_200/ORGANO_NON_GIUNTA`.

I PDF del corpus secondario non devono essere conteggiati nei 200 input primari.

## Esclusi golden riutilizzati

Sono stati recuperati come input `INPUT_0018`, `INPUT_0019` e `INPUT_0020`, poiche
provengono da comuni non presenti nel golden definitivo e rispettano le quote
regionali.

Gli altri vecchi candidati non sono stati inseriti perche appartengono a comuni
gia presenti nei golden:

- Vanzago;
- Premariacco;
- Frasso Telesino;
- Numana.

Degli otto documenti disponibili di Varallo Pombia e stato scelto un solo atto,
relativo all'asilo nido comunale. Gli altri restano recuperabili dalla cronologia
Git, ma non devono entrare nel campione primario.

Lentini rimane escluso dal campione principale per la presenza di 281 pagine di
allegati contabili.
