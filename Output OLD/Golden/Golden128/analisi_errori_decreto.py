from pathlib import Path
import re

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = BASE_DIR / "Output" / "Golden" / "Golden128"
OUTPUT_DIR = GOLDEN_DIR / "analisi_errori_decreto"

CONFIGS = [
    (
        "gemma3_4b",
        "A",
        GOLDEN_DIR / "gemma3_4b_pipeline_A",
        "dataset_atti_gemma3_4b_pulizia.csv",
        "risultati_training_gemma3_4b_A",
    ),
    (
        "gemma3_4b",
        "B",
        GOLDEN_DIR / "gemma3_4b_pipeline_B",
        "dataset_atti_gemma3_4b_riassunto.csv",
        "risultati_training_gemma3_4b_B",
    ),
    (
        "llama3_1_8b",
        "A",
        GOLDEN_DIR / "llama3_1_8b_pipeline_A",
        "dataset_atti_llama3_1_8b_pulizia.csv",
        "risultati_training_llama3_1_8b_A",
    ),
    (
        "llama3_1_8b",
        "B",
        GOLDEN_DIR / "llama3_1_8b_pipeline_B",
        "dataset_atti_llama3_1_8b_riassunto.csv",
        "risultati_training_llama3_1_8b_B",
    ),
    (
        "gemma3_12b",
        "A",
        GOLDEN_DIR / "gemma3_12b_pipeline_A",
        "dataset_atti_gemma3_12b_pulizia.csv",
        "risultati_training_gemma3_12b_A",
    ),
    (
        "gemma3_12b",
        "B",
        GOLDEN_DIR / "gemma3_12b_pipeline_B",
        "dataset_atti_gemma3_12b_riassunto.csv",
        "risultati_training_gemma3_12b_B",
    ),
]

CLASSIFIERS = {
    "naive_bayes": "pred_cv_naive_bayes",
    "logistic_regression": "pred_cv_logistic_regression",
    "linear_svm": "pred_cv_linear_svm",
}


def normalize_id(value):
    text = str(value).strip()
    text = re.sub(r"\.0$", "", text)
    return text.zfill(4) if text.isdigit() else text


def clean_text(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def read_csv(path):
    return pd.read_csv(
        path,
        sep=";",
        encoding="utf-8-sig",
        engine="python",
        dtype=str,
    )


def text_indicators(text):
    lower = clean_text(text).lower()
    terms = [
        "decreto",
        "decreto sindacale",
        "sindaco",
        "determina",
        "determinazione",
        "ordinanza",
        "ordina",
        "dirigente",
        "responsabile",
        "delibera",
        "giunta",
        "consiglio",
    ]
    return ", ".join(term for term in terms if term in lower)


def load_pipeline_texts():
    texts = {}

    for model, pipeline, folder, dataset_file, _ in CONFIGS:
        dataset_path = folder / dataset_file
        if not dataset_path.exists():
            raise FileNotFoundError(f"Dataset non trovato: {dataset_path}")

        df = read_csv(dataset_path)
        df["id_norm"] = df["id_atto"].map(normalize_id)
        texts[(model, pipeline)] = df.set_index("id_norm")

    return texts


def analyze_decreto_errors(texts):
    detail_rows = []
    summary_rows = []
    absorbed_rows = []

    for model, pipeline, folder, _, results_dir in CONFIGS:
        predictions_path = folder / results_dir / "predizioni_cross_validation.csv"
        if not predictions_path.exists():
            raise FileNotFoundError(f"Predizioni CV non trovate: {predictions_path}")

        predictions = read_csv(predictions_path)
        predictions["id_norm"] = predictions["id_atto"].map(normalize_id)
        predictions["golden_label"] = predictions["golden_label"].astype(str).str.strip()

        for classifier, prediction_col in CLASSIFIERS.items():
            predictions[prediction_col] = predictions[prediction_col].astype(str).str.strip()

            missed_decreto = (
                (predictions["golden_label"] == "Decreto")
                & (predictions[prediction_col] != "Decreto")
            )
            absorbed_in_decreto = (
                (predictions["golden_label"] != "Decreto")
                & (predictions[prediction_col] == "Decreto")
            )
            involved = predictions[missed_decreto | absorbed_in_decreto].copy()

            summary_rows.append(
                {
                    "modello": model,
                    "pipeline": pipeline,
                    "classificatore": classifier,
                    "decreti_non_riconosciuti": int(missed_decreto.sum()),
                    "altri_atti_classificati_come_decreto": int(absorbed_in_decreto.sum()),
                    "totale_errori_legati_a_decreto": int(
                        (missed_decreto | absorbed_in_decreto).sum()
                    ),
                }
            )

            for label, count in predictions.loc[
                absorbed_in_decreto, "golden_label"
            ].value_counts().items():
                absorbed_rows.append(
                    {
                        "modello": model,
                        "pipeline": pipeline,
                        "classificatore": classifier,
                        "classe_reale_assorbita_in_decreto": label,
                        "conteggio": int(count),
                    }
                )

            for _, row in involved.iterrows():
                id_atto = row["id_norm"]
                text_a = ""
                text_b = ""

                if id_atto in texts[(model, "A")].index:
                    text_a = clean_text(texts[(model, "A")].loc[id_atto, "testo_completo_llm"])
                if id_atto in texts[(model, "B")].index:
                    text_b = clean_text(texts[(model, "B")].loc[id_atto, "testo_completo_llm"])

                detail_rows.append(
                    {
                        "modello": model,
                        "pipeline_errore": pipeline,
                        "classificatore": classifier,
                        "id_atto": id_atto,
                        "file_name": row.get("file_name", ""),
                        "golden_label": row["golden_label"],
                        "predetto": row[prediction_col],
                        "tipo_errore": (
                            "decreto_non_riconosciuto"
                            if row["golden_label"] == "Decreto"
                            else "assorbito_in_decreto"
                        ),
                        "len_testo_A": len(text_a),
                        "len_testo_B": len(text_b),
                        "indicatori_A": text_indicators(text_a),
                        "indicatori_B": text_indicators(text_b),
                        "snippet_A": text_a[:700],
                        "snippet_B": text_b[:700],
                    }
                )

    summary = pd.DataFrame(summary_rows)
    absorbed = pd.DataFrame(absorbed_rows)
    detail = pd.DataFrame(detail_rows).sort_values(
        ["modello", "pipeline_errore", "classificatore", "tipo_errore", "id_atto"]
    )

    return summary, absorbed, detail


def analyze_global_cv_metrics():
    rows = []

    for model, pipeline, folder, _, results_dir in CONFIGS:
        results_folder = folder / results_dir

        for classifier in CLASSIFIERS:
            report_path = results_folder / f"classification_report_cv_{classifier}.csv"
            if not report_path.exists():
                raise FileNotFoundError(f"Classification report CV non trovato: {report_path}")

            report = pd.read_csv(report_path, sep=";", encoding="utf-8-sig")
            label_col = report.columns[0]

            for _, row in report.iterrows():
                label = str(row[label_col]).strip()
                if label in {"", "accuracy", "macro avg", "weighted avg"}:
                    continue

                rows.append(
                    {
                        "modello": model,
                        "pipeline": pipeline,
                        "classificatore": classifier,
                        "classe": label,
                        "precision": float(str(row["precision"]).replace(",", ".")),
                        "recall": float(str(row["recall"]).replace(",", ".")),
                        "f1": float(str(row["f1-score"]).replace(",", ".")),
                        "support": float(str(row["support"]).replace(",", ".")),
                    }
                )

    metrics = pd.DataFrame(rows)
    return (
        metrics.groupby("classe")
        .agg(
            f1_medio=("f1", "mean"),
            precision_media=("precision", "mean"),
            recall_media=("recall", "mean"),
            f1_min=("f1", "min"),
            f1_max=("f1", "max"),
            n_valutazioni=("f1", "count"),
        )
        .reset_index()
        .sort_values(["f1_medio", "precision_media", "recall_media"], ascending=False)
    )


def write_reports(summary, absorbed, detail, global_metrics):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary.to_csv(
        OUTPUT_DIR / "riepilogo_errori_decreto_cv.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )
    absorbed.to_csv(
        OUTPUT_DIR / "classi_assorbite_in_decreto_cv.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )
    detail.to_csv(
        OUTPUT_DIR / "dettaglio_errori_decreto_confronto_A_B.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )
    global_metrics.to_csv(
        OUTPUT_DIR / "metriche_cv_globali_per_classe.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )

    summary_by_pipeline = summary.groupby(["modello", "pipeline"], as_index=False)[
        [
            "decreti_non_riconosciuti",
            "altri_atti_classificati_come_decreto",
            "totale_errori_legati_a_decreto",
        ]
    ].sum()

    absorbed_by_class = (
        absorbed.groupby("classe_reale_assorbita_in_decreto", as_index=False)["conteggio"]
        .sum()
        .sort_values("conteggio", ascending=False)
        if not absorbed.empty
        else absorbed
    )

    absorbed_by_config = (
        absorbed.groupby(
            ["modello", "pipeline", "classe_reale_assorbita_in_decreto"],
            as_index=False,
        )["conteggio"]
        .sum()
        .sort_values(["modello", "pipeline", "conteggio"], ascending=[True, True, False])
        if not absorbed.empty
        else absorbed
    )

    report = []
    report.append("Analisi locale degli errori legati alla classe Decreto")
    report.append("=" * 58)
    report.append("")
    report.append(
        "L'analisi usa le predizioni in cross-validation e distingue due casi: "
        "decreti reali classificati come altro e atti di altre classi classificati "
        "come Decreto. Il secondo caso corrisponde all'assorbimento degli errori "
        "da parte della classe Decreto."
    )
    report.append("")
    report.append("Riepilogo per modello e pipeline")
    report.append("-" * 33)
    report.append(summary_by_pipeline.to_string(index=False))
    report.append("")
    report.append("Classi piu spesso assorbite in Decreto")
    report.append("-" * 39)
    report.append(
        absorbed_by_class.to_string(index=False)
        if not absorbed_by_class.empty
        else "Nessuna classe assorbita in Decreto."
    )
    report.append("")
    report.append("Assorbimenti per configurazione")
    report.append("-" * 31)
    report.append(
        absorbed_by_config.to_string(index=False)
        if not absorbed_by_config.empty
        else "Nessun assorbimento rilevato."
    )
    report.append("")
    report.append("Metriche CV globali per classe")
    report.append("-" * 31)
    report.append(global_metrics.to_string(index=False))
    report.append("")
    report.append("File prodotti")
    report.append("- riepilogo_errori_decreto_cv.csv")
    report.append("- classi_assorbite_in_decreto_cv.csv")
    report.append("- dettaglio_errori_decreto_confronto_A_B.csv")
    report.append("- metriche_cv_globali_per_classe.csv")

    report_text = "\n".join(report)
    (OUTPUT_DIR / "report_analisi_errori_decreto.txt").write_text(
        report_text,
        encoding="utf-8",
    )
    (OUTPUT_DIR / "report_analisi_errori_decreto.md").write_text(
        report_text,
        encoding="utf-8",
    )


def main():
    texts = load_pipeline_texts()
    summary, absorbed, detail = analyze_decreto_errors(texts)
    global_metrics = analyze_global_cv_metrics()
    write_reports(summary, absorbed, detail, global_metrics)

    print(f"Analisi completata. File creati in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
