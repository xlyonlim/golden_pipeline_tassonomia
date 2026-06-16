Analisi locale degli errori legati alla classe Decreto
==========================================================

L'analisi usa le predizioni in cross-validation e distingue due casi: decreti reali classificati come altro e atti di altre classi classificati come Decreto. Il secondo caso corrisponde all'assorbimento degli errori da parte della classe Decreto.

Riepilogo per modello e pipeline
---------------------------------
    modello pipeline  decreti_non_riconosciuti  altri_atti_classificati_come_decreto  totale_errori_legati_a_decreto
 gemma3_12b        A                         4                                     9                              13
 gemma3_12b        B                         8                                     9                              17
  gemma3_4b        A                         5                                     7                              12
  gemma3_4b        B                         4                                     6                              10
llama3_1_8b        A                         4                                     7                              11
llama3_1_8b        B                        11                                     9                              20

Classi piu spesso assorbite in Decreto
---------------------------------------
classe_reale_assorbita_in_decreto  conteggio
                        Determina         21
                        Ordinanza         14
                   DeliberaGiunta          6
                 AccordoSindacale          6

Assorbimenti per configurazione
-------------------------------
    modello pipeline classe_reale_assorbita_in_decreto  conteggio
 gemma3_12b        A                  AccordoSindacale          3
 gemma3_12b        A                         Determina          3
 gemma3_12b        A                    DeliberaGiunta          2
 gemma3_12b        A                         Ordinanza          1
 gemma3_12b        B                         Determina          6
 gemma3_12b        B                         Ordinanza          3
  gemma3_4b        A                         Determina          3
  gemma3_4b        A                         Ordinanza          2
  gemma3_4b        A                  AccordoSindacale          1
  gemma3_4b        A                    DeliberaGiunta          1
  gemma3_4b        B                         Determina          3
  gemma3_4b        B                         Ordinanza          3
llama3_1_8b        A                         Determina          3
llama3_1_8b        A                  AccordoSindacale          2
llama3_1_8b        A                    DeliberaGiunta          1
llama3_1_8b        A                         Ordinanza          1
llama3_1_8b        B                         Ordinanza          4
llama3_1_8b        B                         Determina          3
llama3_1_8b        B                    DeliberaGiunta          2

Metriche CV globali per classe
-------------------------------
           classe  f1_medio  precision_media  recall_media   f1_min   f1_max  n_valutazioni
          Statuto  0.968791         0.944183      0.996528 0.914286 1.000000             18
 AccordoSindacale  0.954462         0.967116      0.944444 0.909091 1.000000             18
      Regolamento  0.952571         0.959896      0.951389 0.842105 1.000000             18
DeliberaConsiglio  0.946043         0.938909      0.954861 0.823529 1.000000             18
        Ordinanza  0.942976         0.941245      0.947917 0.875000 0.969697             18
   DeliberaGiunta  0.934998         0.951688      0.920139 0.903226 0.967742             18
        Determina  0.934326         0.974662      0.899306 0.827586 0.967742             18
          Decreto  0.858130         0.843215      0.875000 0.733333 0.937500             18

File prodotti
- riepilogo_errori_decreto_cv.csv
- classi_assorbite_in_decreto_cv.csv
- dettaglio_errori_decreto_confronto_A_B.csv
- metriche_cv_globali_per_classe.csv