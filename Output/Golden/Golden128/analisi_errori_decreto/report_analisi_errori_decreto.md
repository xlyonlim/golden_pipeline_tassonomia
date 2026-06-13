# Analisi locale degli errori legati alla classe Decreto

L analisi usa le predizioni in cross-validation e distingue due casi: decreti reali classificati come altro e atti di altre classi classificati come Decreto. Il secondo caso corrisponde all idea di assorbimento degli errori da parte della classe Decreto.

## Riepilogo per modello e pipeline

| modello     | pipeline   |   decreti_non_riconosciuti |   altri_atti_classificati_come_decreto |   totale_errori_legati_a_decreto |
|:------------|:-----------|---------------------------:|---------------------------------------:|---------------------------------:|
| gemma3_12b  | A          |                          4 |                                      9 |                               13 |
| gemma3_12b  | B          |                          8 |                                      9 |                               17 |
| gemma3_4b   | A          |                          5 |                                      7 |                               12 |
| gemma3_4b   | B          |                          4 |                                      6 |                               10 |
| llama3_1_8b | A          |                          4 |                                      7 |                               11 |
| llama3_1_8b | B          |                         11 |                                      9 |                               20 |


## Classi piu spesso assorbite in Decreto

| classe_reale_assorbita_in_decreto   |   conteggio |
|:------------------------------------|------------:|
| Determina                           |          21 |
| Ordinanza                           |          14 |
| DeliberaGiunta                      |           6 |
| AccordoSindacale                    |           6 |


## Assorbimenti per configurazione

| modello     | pipeline   | classe_reale_assorbita_in_decreto   |   conteggio |
|:------------|:-----------|:------------------------------------|------------:|
| gemma3_12b  | A          | AccordoSindacale                    |           3 |
| gemma3_12b  | A          | Determina                           |           3 |
| gemma3_12b  | A          | DeliberaGiunta                      |           2 |
| gemma3_12b  | A          | Ordinanza                           |           1 |
| gemma3_12b  | B          | Determina                           |           6 |
| gemma3_12b  | B          | Ordinanza                           |           3 |
| gemma3_4b   | A          | Determina                           |           3 |
| gemma3_4b   | A          | Ordinanza                           |           2 |
| gemma3_4b   | A          | AccordoSindacale                    |           1 |
| gemma3_4b   | A          | DeliberaGiunta                      |           1 |
| gemma3_4b   | B          | Determina                           |           3 |
| gemma3_4b   | B          | Ordinanza                           |           3 |
| llama3_1_8b | A          | Determina                           |           3 |
| llama3_1_8b | A          | AccordoSindacale                    |           2 |
| llama3_1_8b | A          | DeliberaGiunta                      |           1 |
| llama3_1_8b | A          | Ordinanza                           |           1 |
| llama3_1_8b | B          | Ordinanza                           |           4 |
| llama3_1_8b | B          | Determina                           |           3 |
| llama3_1_8b | B          | DeliberaGiunta                      |           2 |


## File prodotti

- riepilogo_errori_decreto_cv.csv
- classi_assorbite_in_decreto_cv.csv
- dettaglio_errori_decreto_confronto_A_B.csv
