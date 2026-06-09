# ============================================================
# classificazione_atti_ml_testo_completo.py
#
# Obiettivo:
# - usare il dataset Golden128 aggiornato
# - usare testo_completo_llm come X
# - usare golden_label come Y
# - allenare e valutare solo:
#   1. Naive Bayes
#   2. Logistic Regression
#   3. Linear SVM
# - nessun confronto tra LLM e golden_label
# ============================================================

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from sklearn.model_selection import (
    train_test_split,
    StratifiedKFold,
    cross_val_score,
    cross_val_predict
)

from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.feature_extraction.text import TfidfVectorizer

from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    accuracy_score,
    precision_score,
    recall_score,
    f1_score
)

from sklearn.naive_bayes import MultinomialNB
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC


# ============================================================
# 1. CONFIGURAZIONE
BASE_DATASET_DIR = Path(
    r"C:\Users\Ciro\OneDrive - Università di Napoli Federico II\Desktop\Tirocinio\Output\Golden\Golden128"
)
COLONNA_TESTO = "testo_completo_llm"
COLONNA_LABEL = "golden_label"

TEST_SIZE = 0.25
RANDOM_STATE = 1234


# ============================================================
# 2. RISOLUZIONE PATH
# ============================================================

def risolvi_dataset_path(dataset_dir, dataset_file):
    dataset_dir = Path(dataset_dir)

    if not dataset_dir.exists():
        raise FileNotFoundError(f"Cartella dataset non trovata:\n{dataset_dir}")

    path = dataset_dir / dataset_file

    if path.exists():
        return path

    if not dataset_file.lower().endswith(".csv"):
        path_csv = dataset_dir / f"{dataset_file}.csv"
        if path_csv.exists():
            return path_csv

    possibili = list(dataset_dir.glob(f"{Path(dataset_file).stem}*.csv"))

    if len(possibili) == 1:
        return possibili[0]

    if len(possibili) > 1:
        print("\nHo trovato piÃ¹ file possibili:")
        for p in possibili:
            print(p)
        raise ValueError("Specifica meglio DATASET_FILE.")

    raise FileNotFoundError(
        f"Dataset non trovato.\nCartella: {dataset_dir}\nFile richiesto: {dataset_file}"
    )


def csv_allenabile(path):
    try:
        df_check = pd.read_csv(
            path,
            sep=";",
            encoding="utf-8-sig",
            engine="python",
            on_bad_lines="warn",
            dtype=str,
        )
    except Exception:
        return False

    if COLONNA_TESTO not in df_check.columns or COLONNA_LABEL not in df_check.columns:
        return False

    testo = (
        df_check[COLONNA_TESTO]
        .fillna("")
        .astype(str)
        .map(lambda valore: re.sub(r"\s+", " ", str(valore)).strip())
    )
    label = df_check[COLONNA_LABEL].fillna("").astype(str).str.strip()
    righe_valide = (testo.str.strip() != "") & (label.str.strip() != "")
    label_valide = label[righe_valide]
    label_valide = label_valide[
        ~label_valide.str.lower().isin([
            "nan",
            "none",
            "errore",
            "da controllare",
            "ambigua",
            "ambiguo"
        ])
    ]

    return len(label_valide) >= 2 and label_valide.nunique() >= 2


def scegli_da_lista(titolo, opzioni):
    print(f"\n{titolo}")
    print("=" * len(titolo))

    for indice, opzione in enumerate(opzioni, start=1):
        print(f"{indice}. {opzione}")

    while True:
        scelta = input("\nInserisci il numero scelto: ").strip()

        if scelta.isdigit():
            indice = int(scelta)
            if 1 <= indice <= len(opzioni):
                return opzioni[indice - 1]

        print(f"Scelta non valida. Inserisci un numero da 1 a {len(opzioni)}.")


def seleziona_dataset_interattivo(base_dataset_dir):
    base_dataset_dir = Path(base_dataset_dir)

    if not base_dataset_dir.exists():
        raise FileNotFoundError(f"Cartella Golden128 non trovata:\n{base_dataset_dir}")

    cartelle = [
        cartella
        for cartella in sorted(base_dataset_dir.iterdir())
        if cartella.is_dir() and any(csv_allenabile(csv) for csv in cartella.glob("*.csv"))
    ]

    if not cartelle:
        raise FileNotFoundError(
            "Non ho trovato cartelle con CSV allenabili "
            f"contenenti le colonne {COLONNA_TESTO} e {COLONNA_LABEL} in:\n{base_dataset_dir}"
        )

    cartella_scelta = scegli_da_lista(
        "Scegli la cartella del modello/output da allenare",
        [cartella.name for cartella in cartelle]
    )

    dataset_dir = cartelle[[cartella.name for cartella in cartelle].index(cartella_scelta)]
    csv_validi = [csv for csv in sorted(dataset_dir.glob("*.csv")) if csv_allenabile(csv)]

    if len(csv_validi) == 1:
        return csv_validi[0]

    csv_scelto = scegli_da_lista(
        f"Scegli il CSV dataset in {dataset_dir.name}",
        [csv.name for csv in csv_validi]
    )

    return dataset_dir / csv_scelto


DATASET_PATH = seleziona_dataset_interattivo(BASE_DATASET_DIR)
DATASET_DIR = DATASET_PATH.parent
DATASET_STEM = DATASET_PATH.stem

def nome_cartella_risultati(dataset_dir, dataset_stem):
    nome_dataset = Path(dataset_dir).name
    match = re.match(r"^(?P<modello>.+)_pipeline_(?P<pipeline>[AB])$", nome_dataset)
    if match:
        nome = f"risultati_training_{match.group('modello')}_{match.group('pipeline')}"
    else:
        nome = f"risultati_training_{dataset_stem}"

    nome = re.sub(r"[^A-Za-z0-9_-]+", "_", nome)
    nome = re.sub(r"_+", "_", nome).strip("_")
    return nome or "risultati_training"


OUTPUT_DIR = DATASET_DIR / nome_cartella_risultati(DATASET_DIR, DATASET_STEM)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_STEM = OUTPUT_DIR.name


# ============================================================
# 3. FUNZIONI BASE
# ============================================================

def carica_dataset(path):
    return pd.read_csv(
        path,
        sep=";",
        encoding="utf-8-sig",
        engine="python",
        on_bad_lines="warn"
    )


def pulisci_testo(testo):
    if pd.isna(testo):
        return ""

    testo = str(testo).lower()
    testo = re.sub(r"[^a-zÃ Ã¨Ã©Ã¬Ã²Ã¹0-9\s]", " ", testo)
    testo = re.sub(r"\s+", " ", testo).strip()

    return testo


def normalizza_label(label):
    if pd.isna(label):
        return ""

    label = str(label).strip()

    mapping = {
        "determinazione": "Determina",
        "determinazione dirigenziale": "Determina",
        "determina dirigenziale": "Determina",
        "determina": "Determina",

        "delibera giunta": "DeliberaGiunta",
        "delibera di giunta": "DeliberaGiunta",
        "deliberazione giunta": "DeliberaGiunta",
        "deliberazione di giunta": "DeliberaGiunta",
        "deliberazione della giunta": "DeliberaGiunta",

        "delibera consiglio": "DeliberaConsiglio",
        "delibera di consiglio": "DeliberaConsiglio",
        "deliberazione consiglio": "DeliberaConsiglio",
        "deliberazione di consiglio": "DeliberaConsiglio",
        "deliberazione del consiglio": "DeliberaConsiglio",

        "decreto sindacale": "Decreto",
        "decreto": "Decreto",

        "ordinanza sindacale": "Ordinanza",
        "ordinanza": "Ordinanza",

        "regolamento comunale": "Regolamento",
        "regolamento": "Regolamento",

        "accordo sindacale": "AccordoSindacale",
        "accordosindacale": "AccordoSindacale",

        "statuto": "Statuto",
    }

    return mapping.get(label.lower().strip(), label)


# ============================================================
# 4. FEATURE TF-IDF
# ============================================================

def crea_features_tfidf():
    return FeatureUnion([
        (
            "word_tfidf",
            TfidfVectorizer(
                analyzer="word",
                ngram_range=(1, 3),
                max_features=20000,
                min_df=1,
                sublinear_tf=True
            )
        ),
        (
            "char_tfidf",
            TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(3, 5),
                max_features=12000,
                min_df=1,
                sublinear_tf=True
            )
        )
    ])


# ============================================================
# 5. MODELLI
# ============================================================

def crea_modelli():
    return {
        "naive_bayes": Pipeline([
            ("features", crea_features_tfidf()),
            ("clf", MultinomialNB())
        ]),

        "logistic_regression": Pipeline([
            ("features", crea_features_tfidf()),
            ("clf", LogisticRegression(
                max_iter=5000,
                class_weight="balanced",
                random_state=RANDOM_STATE
            ))
        ]),

        "linear_svm": Pipeline([
            ("features", crea_features_tfidf()),
            ("clf", LinearSVC(
                class_weight="balanced",
                random_state=RANDOM_STATE,
                max_iter=10000
            ))
        ])
    }


# ============================================================
# 6. METRICHE E SALVATAGGI
# ============================================================

def calcola_metriche(y_true, y_pred):
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision_macro": precision_score(y_true, y_pred, average="macro", zero_division=0),
        "recall_macro": recall_score(y_true, y_pred, average="macro", zero_division=0),
        "f1_macro": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "f1_weighted": f1_score(y_true, y_pred, average="weighted", zero_division=0)
    }


def salva_classification_report(y_true, y_pred, labels, path):
    report_dict = classification_report(
        y_true,
        y_pred,
        labels=labels,
        output_dict=True,
        zero_division=0
    )

    pd.DataFrame(report_dict).transpose().to_csv(
        path,
        sep=";",
        encoding="utf-8-sig"
    )


def salva_confusion_matrix(y_true, y_pred, labels, path):
    cm = confusion_matrix(y_true, y_pred, labels=labels)

    cm_df = pd.DataFrame(
        cm,
        index=[f"vero_{label}" for label in labels],
        columns=[f"pred_{label}" for label in labels]
    )

    cm_df.to_csv(path, sep=";", encoding="utf-8-sig")

    return cm_df


def salva_txt_risultati(path, dataset_path, output_dir, labels, distribuzione, tabella_test, cv_summary):
    righe = [
        f"Generato il: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Dataset: {dataset_path}",
        f"Cartella risultati: {output_dir}",
        f"X usata: {COLONNA_TESTO}",
        f"Y usata: {COLONNA_LABEL}",
        "",
        "Classi:",
        ", ".join(labels),
        "",
        "Distribuzione golden_label:",
        distribuzione.to_string(),
        "",
        "Metriche su test set:",
        tabella_test.to_string(index=False),
    ]

    if cv_summary:
        cv_df = pd.DataFrame(cv_summary).copy()
        colonne_percentuali = [
            colonna
            for colonna in cv_df.columns
            if colonna not in ["modello", "cv_n_splits"]
        ]

        for colonna in colonne_percentuali:
            cv_df[colonna] = cv_df[colonna] * 100

        cv_df = cv_df.round(2)

        righe.extend([
            "",
            "Metriche cross-validation:",
            cv_df.to_string(index=False),
        ])
    else:
        righe.extend([
            "",
            "Cross-validation: non eseguita.",
        ])

    righe.extend([
        "",
        "File generati nella cartella risultati:",
        "- training_set.csv",
        "- test_set.csv",
        "- risultati_test_ml.csv",
        "- sintesi_metriche_test_set.csv",
        "- tabella_riassuntiva_metriche_modelli.csv",
        "- confusion_matrix_*",
        "- classification_report_*",
    ])

    testo = "\n".join(["Risultati training modelli ML", "=" * 29, ""] + righe)
    path.write_text(testo, encoding="utf-8")


def valuta_modello(nome, modello, X_train, X_test, y_train, y_test, labels):
    print("\n=================================================")
    print(f"MODELLO: {nome}")
    print("=================================================")

    modello.fit(X_train, y_train)
    y_pred = modello.predict(X_test)

    metriche = calcola_metriche(y_test, y_pred)

    print(f"\nAccuracy: {metriche['accuracy']:.4f}")
    print(f"Precision macro: {metriche['precision_macro']:.4f}")
    print(f"Recall macro: {metriche['recall_macro']:.4f}")
    print(f"F1 macro: {metriche['f1_macro']:.4f}")
    print(f"F1 weighted: {metriche['f1_weighted']:.4f}")

    print("\nClassification report:")
    print(classification_report(y_test, y_pred, labels=labels, zero_division=0))

    cm_path = OUTPUT_DIR / f"confusion_matrix_{nome}.csv"
    report_path = OUTPUT_DIR / f"classification_report_{nome}.csv"

    cm_df = salva_confusion_matrix(y_test, y_pred, labels, cm_path)
    salva_classification_report(y_test, y_pred, labels, report_path)

    print("\nConfusion matrix:")
    print(cm_df)

    return {
        "modello": nome,
        **metriche,
        "predizioni": y_pred
    }


# ============================================================
# 7. CARICAMENTO DATASET
# ============================================================

print("\n=================================================")
print("PATH")
print("=================================================")
print(f"Dataset usato:\n{DATASET_PATH}")
print(f"Cartella output:\n{OUTPUT_DIR}")

df = carica_dataset(DATASET_PATH)

print("\nColonne trovate:")
print(list(df.columns))

if COLONNA_TESTO not in df.columns:
    raise ValueError(f"Manca la colonna testo richiesta: {COLONNA_TESTO}")

if COLONNA_LABEL not in df.columns:
    raise ValueError(f"Manca la colonna label richiesta: {COLONNA_LABEL}")


# ============================================================
# 8. PREPARAZIONE DATI
# ============================================================

df = df.copy()

df[COLONNA_TESTO] = df[COLONNA_TESTO].fillna("").astype(str)
df[COLONNA_LABEL] = df[COLONNA_LABEL].apply(normalizza_label)

df["testo_ml"] = df[COLONNA_TESTO].apply(pulisci_testo)

df = df[df["testo_ml"].str.strip() != ""].copy()
df = df[df[COLONNA_LABEL].astype(str).str.strip() != ""].copy()

df = df[
    ~df[COLONNA_LABEL].str.lower().isin([
        "nan",
        "none",
        "errore",
        "da controllare",
        "ambigua",
        "ambiguo"
    ])
].copy()

print("\n=================================================")
print("DISTRIBUZIONE GOLDEN_LABEL")
print("=================================================")
distribuzione_label = df[COLONNA_LABEL].value_counts()
print(distribuzione_label)

conteggi = distribuzione_label
classi_troppo_piccole = conteggi[conteggi < 2]

if len(classi_troppo_piccole) > 0:
    print("\nATTENZIONE:")
    print("Queste classi hanno meno di 2 esempi e vengono rimosse per lo split stratificato:")
    print(classi_troppo_piccole)

    classi_valide = conteggi[conteggi >= 2].index
    df = df[df[COLONNA_LABEL].isin(classi_valide)].copy()
    distribuzione_label = df[COLONNA_LABEL].value_counts()


# ============================================================
# 9. X E y
# ============================================================

X = df["testo_ml"]
y = df[COLONNA_LABEL]

labels = sorted(y.unique())

numero_classi = y.nunique()
numero_osservazioni = len(df)

print("\n=================================================")
print("INFO DATASET FINALE")
print("=================================================")
print(f"Numero osservazioni: {numero_osservazioni}")
print(f"Numero classi: {numero_classi}")
print(f"Classi: {labels}")
print(f"X usata: {COLONNA_TESTO}")
print(f"Y usata: {COLONNA_LABEL}")

if numero_osservazioni == 0 or numero_classi == 0:
    raise ValueError(
        "Dataset non allenabile dopo la preparazione: non resta nessuna riga con "
        f"{COLONNA_TESTO} e {COLONNA_LABEL} valorizzate.\n"
        f"CSV selezionato: {DATASET_PATH}"
    )

if numero_classi < 2:
    raise ValueError(
        "Dataset non allenabile: serve almeno 2 classi distinte dopo la preparazione.\n"
        f"CSV selezionato: {DATASET_PATH}"
    )


# ============================================================
# 10. TRAIN / TEST SPLIT
# ============================================================

n_test_minimo = numero_classi
n_test_attuale = int(round(numero_osservazioni * TEST_SIZE))

if n_test_attuale < n_test_minimo:
    test_size_effettivo = n_test_minimo / numero_osservazioni
    print("\nATTENZIONE:")
    print(f"TEST_SIZE={TEST_SIZE} produrrebbe solo {n_test_attuale} casi di test.")
    print(f"Lo aumento a {test_size_effettivo:.3f}.")
else:
    test_size_effettivo = TEST_SIZE

X_train, X_test, y_train, y_test, train_index, test_index = train_test_split(
    X,
    y,
    df.index,
    test_size=test_size_effettivo,
    random_state=RANDOM_STATE,
    stratify=y
)

print("\n=================================================")
print("TRAIN / TEST SPLIT")
print("=================================================")
print(f"Training set: {len(X_train)} osservazioni")
print(f"Test set: {len(X_test)} osservazioni")

print("\nDistribuzione classi nel training set:")
print(y_train.value_counts())

print("\nDistribuzione classi nel test set:")
print(y_test.value_counts())


# ============================================================
# 11. SALVATAGGIO TRAINING SET E TEST SET
# ============================================================

df_train = df.loc[train_index].copy()
df_test = df.loc[test_index].copy()

train_path = OUTPUT_DIR / "training_set.csv"
test_path = OUTPUT_DIR / "test_set.csv"

df_train.to_csv(train_path, index=False, sep=";", encoding="utf-8-sig")
df_test.to_csv(test_path, index=False, sep=";", encoding="utf-8-sig")

print("\nFile training/test salvati:")
print(train_path)
print(test_path)


# ============================================================
# 12. VALUTAZIONE MODELLI SU TEST SET
# ============================================================

modelli = crea_modelli()
risultati_sintesi = []
risultati_test = df_test.copy()

for nome, modello in modelli.items():
    risultato = valuta_modello(
        nome=nome,
        modello=modello,
        X_train=X_train,
        X_test=X_test,
        y_train=y_train,
        y_test=y_test,
        labels=labels
    )

    risultati_test[f"pred_{nome}"] = risultato["predizioni"]
    risultati_test[f"correct_{nome}"] = (
        risultati_test[f"pred_{nome}"] == risultati_test[COLONNA_LABEL]
    )

    risultati_sintesi.append({
        "modello": nome,
        "accuracy": risultato["accuracy"],
        "precision_macro": risultato["precision_macro"],
        "recall_macro": risultato["recall_macro"],
        "f1_macro": risultato["f1_macro"],
        "f1_weighted": risultato["f1_weighted"]
    })

risultati_test_path = OUTPUT_DIR / "risultati_test_ml.csv"

risultati_test.to_csv(
    risultati_test_path,
    index=False,
    sep=";",
    encoding="utf-8-sig"
)


# ============================================================
# 13. CROSS-VALIDATION STRATIFICATA
# ============================================================

print("\n=================================================")
print("CROSS-VALIDATION STRATIFICATA")
print("=================================================")

min_class_count = y.value_counts().min()
cv_summary = []

if min_class_count >= 2:
    n_splits = min(5, min_class_count)

    cv = StratifiedKFold(
        n_splits=n_splits,
        shuffle=True,
        random_state=RANDOM_STATE
    )

    cv_predictions = df.copy()

    for nome, modello in modelli.items():
        print(f"\nCross-validation modello: {nome}")

        acc_scores = cross_val_score(modello, X, y, cv=cv, scoring="accuracy")
        precision_scores = cross_val_score(modello, X, y, cv=cv, scoring="precision_macro")
        recall_scores = cross_val_score(modello, X, y, cv=cv, scoring="recall_macro")
        f1_macro_scores = cross_val_score(modello, X, y, cv=cv, scoring="f1_macro")
        f1_weighted_scores = cross_val_score(modello, X, y, cv=cv, scoring="f1_weighted")

        y_pred_cv = cross_val_predict(modello, X, y, cv=cv)

        cv_predictions[f"pred_cv_{nome}"] = y_pred_cv
        cv_predictions[f"correct_cv_{nome}"] = y_pred_cv == y

        cv_summary.append({
            "modello": nome,
            "cv_n_splits": n_splits,
            "accuracy_mean": acc_scores.mean(),
            "accuracy_std": acc_scores.std(),
            "precision_macro_mean": precision_scores.mean(),
            "precision_macro_std": precision_scores.std(),
            "recall_macro_mean": recall_scores.mean(),
            "recall_macro_std": recall_scores.std(),
            "f1_macro_mean": f1_macro_scores.mean(),
            "f1_macro_std": f1_macro_scores.std(),
            "f1_weighted_mean": f1_weighted_scores.mean(),
            "f1_weighted_std": f1_weighted_scores.std()
        })

        print(f"Accuracy media: {acc_scores.mean():.4f} Â± {acc_scores.std():.4f}")
        print(f"Precision macro media: {precision_scores.mean():.4f} Â± {precision_scores.std():.4f}")
        print(f"Recall macro media: {recall_scores.mean():.4f} Â± {recall_scores.std():.4f}")
        print(f"F1 macro media: {f1_macro_scores.mean():.4f} Â± {f1_macro_scores.std():.4f}")
        print(f"F1 weighted media: {f1_weighted_scores.mean():.4f} Â± {f1_weighted_scores.std():.4f}")

        salva_confusion_matrix(
            y,
            y_pred_cv,
            labels,
            OUTPUT_DIR / f"confusion_matrix_cv_{nome}.csv"
        )

        salva_classification_report(
            y,
            y_pred_cv,
            labels,
            OUTPUT_DIR / f"classification_report_cv_{nome}.csv"
        )

    pd.DataFrame(cv_summary).to_csv(
        OUTPUT_DIR / "cross_validation_summary.csv",
        index=False,
        sep=";",
        encoding="utf-8-sig"
    )

    cv_predictions.to_csv(
        OUTPUT_DIR / "predizioni_cross_validation.csv",
        index=False,
        sep=";",
        encoding="utf-8-sig"
    )

else:
    print("Cross-validation non eseguita: almeno una classe ha meno di 2 esempi.")


# ============================================================
# 14. TABELLA RIASSUNTIVA FINALE
# ============================================================

sintesi_df = pd.DataFrame(risultati_sintesi)

sintesi_df = sintesi_df.sort_values(
    by="f1_macro",
    ascending=False
)

sintesi_path = OUTPUT_DIR / "sintesi_metriche_test_set.csv"

sintesi_df.to_csv(
    sintesi_path,
    index=False,
    sep=";",
    encoding="utf-8-sig"
)

tabella_finale = sintesi_df.copy()

for col in [
    "accuracy",
    "precision_macro",
    "recall_macro",
    "f1_macro",
    "f1_weighted"
]:
    tabella_finale[col] = tabella_finale[col] * 100

tabella_finale = tabella_finale.round(2)

tabella_finale_path = OUTPUT_DIR / "tabella_riassuntiva_metriche_modelli.csv"

tabella_finale.to_csv(
    tabella_finale_path,
    index=False,
    sep=";",
    encoding="utf-8-sig"
)

txt_risultati_path = OUTPUT_DIR / f"{REPORT_STEM}.txt"

salva_txt_risultati(
    path=txt_risultati_path,
    dataset_path=DATASET_PATH,
    output_dir=OUTPUT_DIR,
    labels=labels,
    distribuzione=distribuzione_label,
    tabella_test=tabella_finale,
    cv_summary=cv_summary
)

print("\n=================================================")
print("TABELLA RIASSUNTIVA FINALE MODELLI")
print("=================================================")
print(tabella_finale.to_string(index=False))

migliore = tabella_finale.iloc[0]

print("\nMiglior modello secondo F1 macro:")
print(
    f"{migliore['modello']} "
    f"- F1 macro: {migliore['f1_macro']:.2f}% "
    f"- Accuracy: {migliore['accuracy']:.2f}%"
)


# ============================================================
# 15. FILE FINALI
# ============================================================

print("\n=================================================")
print("FILE FINALI SALVATI IN:")
print("=================================================")
print(OUTPUT_DIR)

print("\nFile principali:")
print(train_path)
print(test_path)
print(risultati_test_path)
print(sintesi_path)
print(tabella_finale_path)
print(txt_risultati_path)

print("\nAltri file salvati:")
print("- confusion_matrix_*")
print("- classification_report_*")
print("- cross_validation_summary.csv")
print("- predizioni_cross_validation.csv")
