"""Classifica blocchi BIO e applica il miglior modello agli Input.

Flusso:
1. usa il golden manuale per creare documenti etichettati BIO;
2. confronta SVM, Naive Bayes e regressione logistica in leave-one-document-out;
3. sceglie il modello con uno score pesato;
4. riaddestra il modello scelto su tutti i golden;
5. applica il modello agli atti di Input gia' segmentati da 01.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support
from sklearn.naive_bayes import ComplementNB
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC
from sklearn.utils.class_weight import compute_sample_weight


SCRIPT_DIR = Path(__file__).resolve().parent
GOLDEN_MODULE_PATH = SCRIPT_DIR / "03_golden_segmentazione.py"
EXTRACTION_MODULE_PATH = SCRIPT_DIR / "01_estrazione_e_segmentazione.py"
SCRIPT_OUTPUT_ROOT = SCRIPT_DIR / Path(__file__).stem
DEFAULT_RAW_GOLDEN_EXTRACTIONS = SCRIPT_DIR / "03_golden_segmentazione" / "SEGMENTAZIONE_SU_GOLDEN"
LEGACY_RAW_GOLDEN_EXTRACTIONS = SCRIPT_DIR / "SEGMENTAZIONE_SU_GOLDEN"
DEFAULT_INPUT_SEGMENTATION_ROOT = SCRIPT_DIR / "01_estrazione_e_segmentazione"
DEFAULT_OUTPUT_ROOT = SCRIPT_OUTPUT_ROOT
MODEL_NAMES = ("svm", "naive_bayes", "regressione_logistica")
MODEL_SELECTION_WEIGHTS = (
    ("boundary_f1", 0.50),
    ("type_accuracy_su_confine_esatto", 0.25),
    ("token_macro_f1", 0.15),
    ("token_accuracy", 0.10),
)


def load_golden_module() -> Any:
    spec = importlib.util.spec_from_file_location("golden_segmentazione", GOLDEN_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Impossibile caricare {GOLDEN_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_extraction_module() -> Any:
    spec = importlib.util.spec_from_file_location("estrazione_segmentazione", EXTRACTION_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Impossibile caricare {EXTRACTION_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gs = load_golden_module()
extractor = load_extraction_module()
Document = gs.Document


def word_shape(value: str) -> str:
    if value.isupper():
        return "UPPER"
    if value[:1].isupper():
        return "TITLE"
    if value.isdigit():
        return "DIGIT"
    return "LOWER"


def context_features(document: Document, radius: int = 5) -> list[str]:
    words = [token.norm for token in document.tokens]
    features = []
    for index, token in enumerate(document.tokens):
        context = []
        for offset in range(-radius, radius + 1):
            position = index + offset
            value = words[position] if 0 <= position < len(words) else "<PAD>"
            context.append(f"w{offset:+d}={value}")
        position_bin = min(int(token.relative_position * 10), 9)
        context.extend((f"role={token.role}", f"pos={position_bin}", f"shape={word_shape(token.text)}"))
        features.append(" ".join(context))
    return features


def make_model(name: str) -> Pipeline:
    vectorizer = FeatureUnion([
        ("word", TfidfVectorizer(
            ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=60_000
        )),
        ("char", TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), min_df=2,
            sublinear_tf=True, max_features=60_000
        )),
    ])
    if name == "svm":
        classifier = LinearSVC(C=1.0, class_weight="balanced", max_iter=20_000)
    elif name == "naive_bayes":
        classifier = ComplementNB(alpha=0.5)
    elif name == "regressione_logistica":
        classifier = LogisticRegression(
            C=2.0, class_weight="balanced", max_iter=1500, solver="lbfgs"
        )
    else:
        raise ValueError(f"Modello sconosciuto: {name}")
    return Pipeline([("features", vectorizer), ("classifier", classifier)])


def fit_model(model: Pipeline, name: str, x: list[str], y: list[str]) -> Pipeline:
    if name == "naive_bayes":
        weights = compute_sample_weight(class_weight="balanced", y=y)
        model.fit(x, y, classifier__sample_weight=weights)
    else:
        model.fit(x, y)
    return model


def score_predictions(y_true: list[str], y_pred: list[str]) -> dict[str, float]:
    true_boundary = [label.startswith("B:") for label in y_true]
    pred_boundary = [label.startswith("B:") for label in y_pred]
    bp, br, bf, _ = precision_recall_fscore_support(
        true_boundary, pred_boundary, average="binary", zero_division=0
    )
    exact_true_types = []
    exact_pred_types = []
    for truth, prediction in zip(y_true, y_pred):
        if truth.startswith("B:") and prediction.startswith("B:"):
            exact_true_types.append(truth)
            exact_pred_types.append(prediction)
    type_accuracy = accuracy_score(exact_true_types, exact_pred_types) if exact_true_types else 0.0
    return {
        "token_accuracy": accuracy_score(y_true, y_pred),
        "token_macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "boundary_precision": bp,
        "boundary_recall": br,
        "boundary_f1": bf,
        "type_accuracy_su_confine_esatto": type_accuracy,
    }


def reconstruct_blocks(document: Document, predictions: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    current_type: str | None = None
    current_start: int | None = None

    def close(end_char: int) -> None:
        nonlocal current_type, current_start
        if current_type is None or current_start is None:
            return
        block_text = document.text[current_start:end_char].strip(" \n;,")
        if block_text:
            counts[current_type] += 1
            rows.append({
                "id_delibera": document.id_delibera,
                "ordine_globale": len(rows) + 1,
                "tipo_blocco": f"{current_type}_{counts[current_type]}",
                "testo_blocco": block_text,
            })
        current_type = None
        current_start = None

    for token, label in zip(document.tokens, predictions):
        if label.startswith("B:"):
            close(token.start)
            current_type = label.split(":", 1)[1]
            current_start = token.start
        elif label == "O":
            close(token.start)
    close(len(document.text))
    return rows


def final_model_path(output_dir: Path, tool: str, source_format: str, model_name: str) -> Path:
    return output_dir / f"modello_{tool.lower()}_{source_format.lower()}_{model_name}.joblib"


def train_condition(
    documents: list[Document],
    tool: str,
    source_format: str,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics: list[dict[str, Any]] = []
    predicted_blocks: list[dict[str, Any]] = []
    features = {document.id_delibera: context_features(document) for document in documents}

    for model_name in MODEL_NAMES:
        for test_document in documents:
            train_documents = [doc for doc in documents if doc.id_delibera != test_document.id_delibera]
            x_train = [item for doc in train_documents for item in features[doc.id_delibera]]
            y_train = [label for doc in train_documents for label in doc.labels]
            model = fit_model(make_model(model_name), model_name, x_train, y_train)
            prediction = list(model.predict(features[test_document.id_delibera]))
            scores = score_predictions(test_document.labels, prediction)
            metrics.append({
                "estrattore": tool,
                "formato": source_format,
                "modello": model_name,
                "fold_test": test_document.id_delibera,
                "n_token_test": len(prediction),
                **{key: round(value, 6) for key, value in scores.items()},
            })
            for row in reconstruct_blocks(test_document, prediction):
                row.update({"estrattore": tool, "formato": source_format, "modello": model_name})
                predicted_blocks.append(row)

        x_all = [item for doc in documents for item in features[doc.id_delibera]]
        y_all = [label for doc in documents for label in doc.labels]
        final_model = fit_model(make_model(model_name), model_name, x_all, y_all)
        joblib.dump(final_model, final_model_path(output_dir, tool, source_format, model_name))
    return metrics, predicted_blocks


def metric_value(row: dict[str, Any], field: str) -> float:
    try:
        return float(row.get(field) or 0)
    except (TypeError, ValueError):
        return 0.0


def model_selection_score(row: dict[str, Any]) -> float:
    return sum(metric_value(row, field) * weight for field, weight in MODEL_SELECTION_WEIGHTS)


def summarize(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    score_fields = (
        "token_accuracy", "token_macro_f1", "boundary_precision", "boundary_recall",
        "boundary_f1", "type_accuracy_su_confine_esatto",
    )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in metrics:
        groups.setdefault((row["estrattore"], row["formato"], row["modello"]), []).append(row)

    result = []
    for (tool, source_format, model), rows in sorted(groups.items()):
        summary: dict[str, Any] = {
            "estrattore": tool,
            "formato": source_format,
            "modello": model,
            "n_fold": len(rows),
        }
        for field in score_fields:
            values = [float(row[field]) for row in rows]
            summary[field] = round(float(np.mean(values)), 6)
            summary[f"{field}_std"] = round(float(np.std(values)), 6)
        summary["score_scelta_modello"] = round(model_selection_score(summary), 6)
        result.append(summary)
    return result


def select_best_model(summary: list[dict[str, Any]]) -> dict[str, Any]:
    if not summary:
        raise ValueError("Nessuna metrica disponibile per scegliere il modello migliore")
    return max(
        summary,
        key=lambda row: (
            metric_value(row, "score_scelta_modello"),
            metric_value(row, "boundary_f1"),
            metric_value(row, "type_accuracy_su_confine_esatto"),
        ),
    )


def print_model_ranking(summary: list[dict[str, Any]]) -> None:
    weights = " + ".join(f"{weight:.2f}*{field}" for field, weight in MODEL_SELECTION_WEIGHTS)
    print(f"Score scelta classificatore su golden = {weights}")
    print("Classifica classificatori:")
    for rank, row in enumerate(
        sorted(summary, key=lambda item: metric_value(item, "score_scelta_modello"), reverse=True),
        start=1,
    ):
        print(
            f"{rank}. {row['estrattore']}/{row['formato']} {row['modello']} "
            f"score={metric_value(row, 'score_scelta_modello'):.6f} "
            f"boundary_f1={metric_value(row, 'boundary_f1'):.6f} "
            f"type_acc={metric_value(row, 'type_accuracy_su_confine_esatto'):.6f} "
            f"macro_f1={metric_value(row, 'token_macro_f1'):.6f} "
            f"token_acc={metric_value(row, 'token_accuracy'):.6f}"
        )


def sigmoid(value: float) -> float:
    value = max(min(value, 50), -50)
    return 1.0 / (1.0 + math.exp(-value))


def predict_with_confidence(model: Pipeline, features: list[str]) -> tuple[list[str], list[float]]:
    predictions = list(model.predict(features))
    if hasattr(model, "predict_proba"):
        probabilities = np.asarray(model.predict_proba(features))
        confidences = [float(np.max(row)) for row in probabilities]
        return predictions, confidences

    if hasattr(model, "decision_function"):
        decision = np.asarray(model.decision_function(features))
        if decision.ndim == 1:
            confidences = [sigmoid(abs(float(value))) for value in decision]
        else:
            confidences = []
            for row in decision:
                ordered = np.sort(row)
                margin = float(ordered[-1] - ordered[-2]) if len(ordered) >= 2 else float(ordered[-1])
                confidences.append(sigmoid(margin))
        return predictions, confidences

    return predictions, [0.0 for _ in predictions]


def apply_model_to_documents(
    model: Pipeline,
    documents: list[Document],
    tool: str,
    source_format: str,
    model_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    bio_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    doc_scores: list[dict[str, Any]] = []
    all_confidences: list[float] = []
    total_tokens = 0

    for document in documents:
        prediction, confidences = predict_with_confidence(model, context_features(document))
        all_confidences.extend(confidences)
        total_tokens += len(prediction)
        for index, (token, label, confidence) in enumerate(
            zip(document.tokens, prediction, confidences), start=1
        ):
            bio_rows.append({
                "estrattore": tool,
                "formato": source_format,
                "modello": model_name,
                "id_delibera": document.id_delibera,
                "indice_token": index,
                "token": token.text,
                "etichetta_predetta": label,
                "confidenza_predizione": round(confidence, 6),
                "ruolo": token.role,
                "posizione_relativa": round(token.relative_position, 6),
            })

        blocks = reconstruct_blocks(document, prediction)
        for row in blocks:
            row.update({"estrattore": tool, "formato": source_format, "modello": model_name})
            block_rows.append(row)

        o_count = sum(1 for label in prediction if label == "O")
        doc_scores.append({
            "estrattore": tool,
            "formato": source_format,
            "modello": model_name,
            "id_delibera": document.id_delibera,
            "n_token": len(prediction),
            "n_blocchi_predetti": len(blocks),
            "quota_token_O": round(o_count / len(prediction), 6) if prediction else 0,
            "score_confidenza_input": round(float(np.mean(confidences)), 6) if confidences else 0,
            "confidenza_min": round(float(np.min(confidences)), 6) if confidences else 0,
            "confidenza_max": round(float(np.max(confidences)), 6) if confidences else 0,
            "nota_score": "Confidenza media delle predizioni; non e' accuratezza perche' gli Input non sono annotati.",
        })

    overall = {
        "estrattore": tool,
        "formato": source_format,
        "modello": model_name,
        "n_documenti_input": len(documents),
        "n_token_input": total_tokens,
        "n_blocchi_predetti": len(block_rows),
        "score_confidenza_input": round(float(np.mean(all_confidences)), 6) if all_confidences else 0,
        "confidenza_min": round(float(np.min(all_confidences)), 6) if all_confidences else 0,
        "confidenza_max": round(float(np.max(all_confidences)), 6) if all_confidences else 0,
        "nota_score": "Confidenza media delle predizioni; non e' accuratezza perche' gli Input non sono annotati.",
    }
    return bio_rows, block_rows, doc_scores, overall


def apply_best_model_to_input(
    output_dir: Path,
    best_model: dict[str, Any],
    input_segmentation_dir: Path,
) -> dict[str, Any]:
    tool = str(best_model["estrattore"])
    source_format = str(best_model["formato"])
    model_name = str(best_model["modello"])
    model_file = final_model_path(output_dir, tool, source_format, model_name)
    if not model_file.exists():
        raise FileNotFoundError(f"Modello finale non trovato: {model_file}")

    documents = gs.load_unlabeled_condition(input_segmentation_dir, tool, source_format)
    model = joblib.load(model_file)
    bio_rows, block_rows, doc_scores, overall_score = apply_model_to_documents(
        model, documents, tool, source_format, model_name
    )
    gs.write_csv(
        output_dir / "input_predizioni_BIO.csv",
        bio_rows,
        [
            "estrattore", "formato", "modello", "id_delibera", "indice_token",
            "token", "etichetta_predetta", "confidenza_predizione",
            "ruolo", "posizione_relativa",
        ],
    )
    gs.write_csv(
        output_dir / "input_blocchi_predetti.csv",
        block_rows,
        ["estrattore", "formato", "modello", "id_delibera", "ordine_globale", "tipo_blocco", "testo_blocco"],
    )
    gs.write_csv(
        output_dir / "score_input_per_delibera.csv",
        doc_scores,
        [
            "estrattore", "formato", "modello", "id_delibera", "n_token",
            "n_blocchi_predetti", "quota_token_O", "score_confidenza_input",
            "confidenza_min", "confidenza_max", "nota_score",
        ],
    )
    gs.write_csv(
        output_dir / "score_input_modello_scelto.csv",
        [overall_score],
        [
            "estrattore", "formato", "modello", "n_documenti_input",
            "n_token_input", "n_blocchi_predetti", "score_confidenza_input",
            "confidenza_min", "confidenza_max", "nota_score",
        ],
    )
    result = {
        **best_model,
        "segmentazione_input": str(input_segmentation_dir),
        "modello_file": str(model_file),
        "score_input": overall_score,
        "output_bio": str(output_dir / "input_predizioni_BIO.csv"),
        "output_blocchi": str(output_dir / "input_blocchi_predetti.csv"),
        "output_score_input": str(output_dir / "score_input_modello_scelto.csv"),
    }
    (output_dir / "miglior_modello.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def export_golden_bio_rows(
    loaded_conditions: dict[tuple[str, str], tuple[list[Document], list[dict[str, Any]]]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_bio_rows: list[dict[str, Any]] = []
    all_audit: list[dict[str, Any]] = []
    for (tool, source_format), (documents, audit) in loaded_conditions.items():
        all_bio_rows.extend(gs.export_bio_rows(documents, tool, source_format))
        all_audit.extend({**row, "estrattore": tool, "formato": source_format} for row in audit)
    return all_bio_rows, all_audit


def next_output_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in output_root.glob("CLASSIFICAZIONE_*"):
        match = re.fullmatch(r"CLASSIFICAZIONE_(\d+)", path.name, re.IGNORECASE)
        if path.is_dir() and match:
            numbers.append(int(match.group(1)))
    output = output_root / f"CLASSIFICAZIONE_{max(numbers, default=0) + 1}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def resolve_existing_golden_source(requested: Path | None) -> Path:
    if requested:
        source_dir = requested.expanduser().resolve()
    elif DEFAULT_RAW_GOLDEN_EXTRACTIONS.is_dir():
        source_dir = DEFAULT_RAW_GOLDEN_EXTRACTIONS.resolve()
    else:
        source_dir = LEGACY_RAW_GOLDEN_EXTRACTIONS.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(
            f"Cartella golden inesistente: {source_dir}. "
            "Il 04 non esegue estrazioni: legge solo file JSON/MD o CSV gia' presenti."
        )
    return source_dir


def has_normalized_csv(source_dir: Path) -> bool:
    return any(
        (source_dir / tool / source_format / "01_elementi_normalizzati.csv").exists()
        for tool, source_format in gs.CONDITIONS
    )


def missing_normalized_conditions(source_dir: Path) -> list[str]:
    return [
        f"{tool}/{source_format}"
        for tool, source_format in gs.CONDITIONS
        if not (source_dir / tool / source_format / "01_elementi_normalizzati.csv").exists()
    ]


def raw_formats(source_dir: Path) -> tuple[str, ...]:
    formats = []
    if any(source_dir.rglob("*.json")):
        formats.append("JSON")
    if any(source_dir.rglob("*.md")) or any(source_dir.rglob("*.markdown")):
        formats.append("MARKDOWN")
    return tuple(formats)


def load_raw_golden_condition(
    source_dir: Path,
    source_format: str,
    golden: dict[str, list[dict[str, str]]],
) -> tuple[list[Document], list[dict[str, Any]]]:
    extractor_format = "json" if source_format == "JSON" else "markdown"
    elements = extractor.read_all_outputs(source_dir, extractor_format)
    if elements.empty:
        raise FileNotFoundError(f"Nessun file {source_format} leggibile in {source_dir}")

    elements = elements.copy()
    elements["id_delibera_canonico"] = elements["id_atto"].apply(gs.canonical_id)
    elements = elements[elements["id_delibera_canonico"].isin(golden)].copy()
    if elements.empty:
        raise ValueError(
            f"I file {source_format} in {source_dir} non corrispondono agli ID del golden."
        )

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in elements.to_dict("records"):
        doc_id = gs.canonical_id(str(row.get("id_atto") or ""))
        row["id_atto"] = doc_id
        row["tool"] = "DOCLING"
        row["source_format"] = source_format
        grouped.setdefault(doc_id, []).append(row)

    missing = sorted(set(golden) - set(grouped), key=gs.natural_number)
    if missing:
        raise ValueError(
            f"Nei file {source_format} di {source_dir} mancano gli atti golden: "
            f"{gs.format_ids(set(missing), max_items=100)}"
        )

    documents = []
    audit = []
    for doc_id in sorted(golden, key=gs.natural_number):
        text, tokens = gs.build_document(grouped[doc_id], doc_id)
        document, rows = gs.label_document(doc_id, text, tokens, golden[doc_id])
        documents.append(document)
        audit.extend(rows)
    return documents, audit


def load_golden_conditions(
    source_dir: Path,
    golden: dict[str, list[dict[str, str]]],
) -> dict[tuple[str, str], tuple[list[Document], list[dict[str, Any]]]]:
    if has_normalized_csv(source_dir):
        missing_conditions = missing_normalized_conditions(source_dir)
        if missing_conditions:
            raise FileNotFoundError(
                "La sorgente golden non contiene tutte le condizioni attese: "
                + ", ".join(missing_conditions)
                + ". Riesegui 03_golden_segmentazione.py dopo aver configurato gli estrattori."
            )
        loaded: dict[tuple[str, str], tuple[list[Document], list[dict[str, Any]]]] = {}
        for tool, source_format in gs.CONDITIONS:
            loaded[(tool, source_format)] = gs.load_condition(source_dir, tool, source_format, golden)
        return loaded

    formats = raw_formats(source_dir)
    if not formats:
        raise FileNotFoundError(
            f"{source_dir} non contiene ne' 01_elementi_normalizzati.csv ne' file JSON/MD."
        )

    loaded = {}
    for source_format in formats:
        loaded[("DOCLING", source_format)] = load_raw_golden_condition(
            source_dir, source_format, golden
        )
    return loaded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Confronta classificatori sui golden e applica il migliore agli Input."
    )
    parser.add_argument(
        "--segmentazione-golden",
        type=Path,
        help=(
            f"Cartella golden gia' pronta; default: {DEFAULT_RAW_GOLDEN_EXTRACTIONS}. "
            "Puo' contenere direttamente GOLD_####.json/.md oppure i CSV normalizzati. "
            "Il 04 non esegue estrazioni."
        ),
    )
    parser.add_argument("--golden", type=Path, default=gs.DEFAULT_GOLDEN)
    parser.add_argument(
        "--segmentazione-input",
        type=Path,
        help="Cartella SEGMENTAZIONE_N degli atti Input; default: ultima non-golden compatibile.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Cartella in cui creare CLASSIFICAZIONE_N (default: {DEFAULT_OUTPUT_ROOT})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    golden_path = args.golden.expanduser().resolve()
    if not golden_path.is_file():
        raise FileNotFoundError(f"Golden CSV inesistente: {golden_path}")

    golden = gs.read_golden(golden_path)
    if len(golden) < 3:
        raise ValueError("Servono almeno tre delibere golden per la validazione per documento")

    golden_source_dir = resolve_existing_golden_source(args.segmentazione_golden)

    output_dir = next_output_dir(args.output.expanduser().resolve())
    print(f"Golden: {len(golden)} delibere, {sum(map(len, golden.values()))} blocchi")
    print(f"Sorgente golden: {golden_source_dir}")
    print(f"Output classificazione: {output_dir}")

    loaded_conditions = load_golden_conditions(golden_source_dir, golden)
    print(
        "Condizioni golden caricate: "
        + ", ".join(f"{tool}/{source_format}" for tool, source_format in loaded_conditions)
    )

    all_metrics: list[dict[str, Any]] = []
    all_predictions: list[dict[str, Any]] = []
    for tool, source_format in loaded_conditions:
        print(f"[{tool}/{source_format}] classificazione leave-one-document-out...")
        documents, _ = loaded_conditions[(tool, source_format)]
        metrics, predictions = train_condition(documents, tool, source_format, output_dir)
        all_metrics.extend(metrics)
        all_predictions.extend(predictions)

    metric_columns = [
        "estrattore", "formato", "modello", "fold_test", "n_token_test",
        "token_accuracy", "token_macro_f1", "boundary_precision", "boundary_recall",
        "boundary_f1", "type_accuracy_su_confine_esatto",
    ]
    gs.write_csv(output_dir / "metriche_golden_per_delibera.csv", all_metrics, metric_columns)
    summary = summarize(all_metrics)
    summary_columns = list(summary[0]) if summary else []
    gs.write_csv(output_dir / "score_classificatori_golden.csv", summary, summary_columns)
    print_model_ranking(summary)
    best_model = select_best_model(summary)
    print(
        "Miglior classificatore su golden: "
        f"{best_model['estrattore']}/{best_model['formato']} {best_model['modello']} "
        f"(score={best_model['score_scelta_modello']}, boundary_f1={best_model['boundary_f1']})"
    )

    golden_bio_rows, audit_rows = export_golden_bio_rows(loaded_conditions)
    gs.write_csv(
        output_dir / "golden_delibere_BIO.csv",
        golden_bio_rows,
        [
            "estrattore", "formato", "id_delibera", "indice_token",
            "token", "etichetta_BIO", "ruolo", "posizione_relativa",
        ],
    )
    gs.write_csv(
        output_dir / "audit_allineamento_golden.csv",
        audit_rows,
        [
            "estrattore", "formato", "id_delibera", "ordine_globale",
            "tipo_blocco", "similarita", "metodo", "stato", "token_start", "token_end",
        ],
    )
    gs.write_csv(
        output_dir / "predizioni_blocchi_cv_golden.csv",
        all_predictions,
        ["estrattore", "formato", "modello", "id_delibera", "ordine_globale", "tipo_blocco", "testo_blocco"],
    )

    if args.segmentazione_input:
        input_segmentation_dir = args.segmentazione_input.expanduser().resolve()
        if not input_segmentation_dir.is_dir():
            raise FileNotFoundError(f"Cartella segmentazione Input inesistente: {input_segmentation_dir}")
    else:
        input_segmentation_dir = gs.input_segmentation_or_none(
            DEFAULT_INPUT_SEGMENTATION_ROOT,
            set(golden),
            str(best_model["estrattore"]),
            str(best_model["formato"]),
        )
        if input_segmentation_dir is None:
            raise FileNotFoundError(
                "Nessuna segmentazione Input non-golden compatibile trovata. "
                "Esegui prima 01_estrazione_e_segmentazione.py sugli Input oppure passa --segmentazione-input."
            )

    print(f"Applico il miglior classificatore agli Input in: {input_segmentation_dir}")
    input_application = apply_best_model_to_input(output_dir, best_model, input_segmentation_dir)
    print(
        "Score Input modello scelto: "
        f"{input_application['score_input']['score_confidenza_input']} "
        "(confidenza media, non accuratezza supervisionata)"
    )

    (output_dir / "configurazione.json").write_text(
        json.dumps({
            "golden": str(golden_path),
            "sorgente_golden": str(golden_source_dir),
            "segmentazione_input": str(input_segmentation_dir),
            "schema_target": "B:TIPO / I / O",
            "validazione_golden": "leave-one-document-out",
            "classificatori": list(MODEL_NAMES),
            "pesi_score_scelta_classificatore": dict(MODEL_SELECTION_WEIGHTS),
            "miglior_classificatore": best_model,
            "applicazione_input": input_application,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Classificazione completata: {output_dir}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Errore: {exc}") from None
