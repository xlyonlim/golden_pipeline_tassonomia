"""Addestra baseline classiche per la segmentazione delle delibere.

Il golden viene allineato ai token degli elementi estratti. I target sono:
``B:TIPO`` (inizio blocco), ``I`` (continuazione) e ``O`` (fuori narrativa).
I progressivi _1, _2, ... e ordine_globale sono ricostruiti dopo la predizione.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

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
DEFAULT_GOLDEN = SCRIPT_DIR / "Delibere golden" / "golden_delibere.csv"
DEFAULT_OUTPUT_ROOT = SCRIPT_DIR / "JSON_VS_MD_BIO"
WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
PROGRESSIVE_RE = re.compile(r"_\d+$")
CONDITIONS = (
    ("DOCLING", "JSON"),
    ("DOCLING", "MARKDOWN"),
    ("OPENDATALOADER", "JSON"),
    ("OPENDATALOADER", "MARKDOWN"),
)


@dataclass
class Token:
    text: str
    norm: str
    start: int
    end: int
    role: str
    relative_position: float


@dataclass
class Document:
    id_delibera: str
    text: str
    tokens: list[Token]
    labels: list[str]


def natural_number(value: str) -> int:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else 10**9


def canonical_id(value: str) -> str:
    return f"ATTO_{natural_number(value):03d}"


def base_type(value: str) -> str:
    return PROGRESSIVE_RE.sub("", value.strip().upper())


def normalize_word(value: str) -> str:
    return value.lower().replace("’", "'")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        header = file.readline()
        file.seek(0)
        delimiter = ";" if ";" in header else ","
        return list(csv.DictReader(file, delimiter=delimiter))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_golden(path: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(path):
        doc_id = canonical_id(row["id_delibera"])
        grouped.setdefault(doc_id, []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("ordine_globale") or 0))
    return grouped


def build_document(elements: list[dict[str, str]], doc_id: str) -> tuple[str, list[Token]]:
    elements = sorted(elements, key=lambda row: float(row.get("order") or 0))
    parts: list[str] = []
    token_data: list[tuple[str, int, int, str]] = []
    cursor = 0
    for row in elements:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        if parts:
            parts.append("\n")
            cursor += 1
        parts.append(text)
        role = str(row.get("role_norm") or row.get("label_raw") or "unknown")
        for match in WORD_RE.finditer(text):
            token_data.append((match.group(), cursor + match.start(), cursor + match.end(), role))
        cursor += len(text)
    full_text = "".join(parts)
    denominator = max(len(token_data) - 1, 1)
    tokens = [
        Token(word, normalize_word(word), start, end, role, index / denominator)
        for index, (word, start, end, role) in enumerate(token_data)
    ]
    if not tokens:
        raise ValueError(f"Nessun token disponibile per {doc_id}")
    return full_text, tokens


def all_occurrences(haystack: list[str], needle: list[str], start: int, stop: int) -> Iterable[int]:
    if not needle:
        return
    first = needle[0]
    last_start = min(stop, len(haystack) - len(needle))
    for index in range(max(0, start), last_start + 1):
        if haystack[index] == first and haystack[index:index + len(needle)] == needle:
            yield index


def align_block(
    source: list[str], golden: list[str], cursor: int
) -> tuple[int, int, float, str]:
    """Allinea un blocco in avanti, privilegiando ancore iniziali/finali esatte."""
    search_stop = min(len(source), cursor + max(2500, len(golden) * 5))
    start: int | None = None
    method = "exact_anchors"
    for anchor_len in range(min(10, len(golden)), 2, -1):
        candidates = list(all_occurrences(source, golden[:anchor_len], cursor, search_stop))
        if candidates:
            start = candidates[0]
            break

    if start is not None:
        expected_end = min(len(source), start + max(len(golden) * 3, len(golden) + 80))
        end: int | None = None
        for anchor_len in range(min(10, len(golden)), 2, -1):
            tail = golden[-anchor_len:]
            candidates = list(all_occurrences(source, tail, start, expected_end))
            if candidates:
                end = min(candidates, key=lambda value: abs((value + anchor_len - start) - len(golden))) + anchor_len
                break
        if end is None:
            end = min(len(source), start + len(golden))
    else:
        method = "sequence_matcher"
        window = source[cursor:search_stop]
        matcher = SequenceMatcher(None, golden, window, autojunk=False)
        matches = [block for block in matcher.get_matching_blocks() if block.size]
        if not matches:
            raise ValueError("nessuna corrispondenza lessicale")
        start = cursor + min(block.b for block in matches)
        end = cursor + max(block.b + block.size for block in matches)

    candidate = source[start:end]
    similarity = SequenceMatcher(None, golden, candidate, autojunk=False).ratio()
    return start, max(start + 1, end), similarity, method


def label_document(
    doc_id: str,
    text: str,
    tokens: list[Token],
    golden_rows: list[dict[str, str]],
) -> tuple[Document, list[dict[str, Any]]]:
    labels = ["O"] * len(tokens)
    source_words = [token.norm for token in tokens]
    cursor = 0
    audit: list[dict[str, Any]] = []
    for row in golden_rows:
        golden_words = [normalize_word(match.group()) for match in WORD_RE.finditer(row["testo_blocco"])]
        if not golden_words:
            continue
        try:
            start, end, similarity, method = align_block(source_words, golden_words, cursor)
            block_type = base_type(row["tipo_blocco"])
            labels[start] = f"B:{block_type}"
            for index in range(start + 1, end):
                labels[index] = "I"
            cursor = end
            status = "OK" if similarity >= 0.65 else "BASSA_SIMILARITA"
            audit.append({
                "id_delibera": doc_id,
                "ordine_globale": row.get("ordine_globale"),
                "tipo_blocco": row.get("tipo_blocco"),
                "similarita": round(similarity, 4),
                "metodo": method,
                "stato": status,
                "token_start": start,
                "token_end": end,
            })
        except Exception as exc:
            audit.append({
                "id_delibera": doc_id,
                "ordine_globale": row.get("ordine_globale"),
                "tipo_blocco": row.get("tipo_blocco"),
                "similarita": 0,
                "metodo": "errore",
                "stato": f"NON_ALLINEATO: {exc}",
                "token_start": "",
                "token_end": "",
            })
    return Document(doc_id, text, tokens, labels), audit


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


def word_shape(value: str) -> str:
    if value.isupper():
        return "UPPER"
    if value[:1].isupper():
        return "TITLE"
    if value.isdigit():
        return "DIGIT"
    return "LOWER"


def make_model(name: str) -> Pipeline:
    vectorizer = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=60_000)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=60_000)),
    ])
    if name == "svm":
        classifier = LinearSVC(C=1.0, class_weight="balanced")
    elif name == "logistica":
        classifier = LogisticRegression(C=2.0, class_weight="balanced", max_iter=1500, solver="lbfgs")
    elif name == "naive_bayes":
        classifier = ComplementNB(alpha=0.5)
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

    for index, (token, label) in enumerate(zip(document.tokens, predictions)):
        if label.startswith("B:"):
            close(token.start)
            current_type = label.split(":", 1)[1]
            current_start = token.start
        elif label == "O":
            close(token.start)
    close(len(document.text))
    return rows


def next_output_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in output_root.glob("ALLENAMENTO_BIO_*"):
        match = re.fullmatch(r"ALLENAMENTO_BIO_(\d+)", path.name, re.IGNORECASE)
        if path.is_dir() and match:
            numbers.append(int(match.group(1)))
    output = output_root / f"ALLENAMENTO_BIO_{max(numbers, default=0) + 1}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def load_condition(
    segmentation_dir: Path,
    tool: str,
    source_format: str,
    golden: dict[str, list[dict[str, str]]],
) -> tuple[list[Document], list[dict[str, Any]]]:
    path = segmentation_dir / tool / source_format / "01_elementi_normalizzati.csv"
    if not path.exists():
        raise FileNotFoundError(f"File mancante: {path}")
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(path):
        grouped.setdefault(canonical_id(row["id_atto"]), []).append(row)
    missing = sorted(set(golden) - set(grouped), key=natural_number)
    if missing:
        raise ValueError(
            f"{tool}/{source_format}: mancano gli atti golden {', '.join(missing)}. "
            "Esegui prima 01_estrazione_e_segmentazione.py sui PDF golden "
            "con --preserva-nomi-pdf."
        )
    documents = []
    audit = []
    for doc_id in sorted(golden, key=natural_number):
        text, tokens = build_document(grouped[doc_id], doc_id)
        document, rows = label_document(doc_id, text, tokens, golden[doc_id])
        documents.append(document)
        audit.extend(rows)
    return documents, audit


def train_condition(
    documents: list[Document],
    tool: str,
    source_format: str,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics: list[dict[str, Any]] = []
    predicted_blocks: list[dict[str, Any]] = []
    model_names = ("svm", "logistica", "naive_bayes")
    features = {document.id_delibera: context_features(document) for document in documents}

    for model_name in model_names:
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
        joblib.dump(final_model, output_dir / f"modello_{tool.lower()}_{source_format.lower()}_{model_name}.joblib")
    return metrics, predicted_blocks


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
            "estrattore": tool, "formato": source_format, "modello": model, "n_fold": len(rows)
        }
        for field in score_fields:
            values = [float(row[field]) for row in rows]
            summary[field] = round(float(np.mean(values)), 6)
            summary[f"{field}_std"] = round(float(np.std(values)), 6)
        result.append(summary)
    return result


def export_bio_rows(
    documents: list[Document], tool: str, source_format: str
) -> list[dict[str, Any]]:
    """Esporta il golden derivato senza alterare golden_delibere.csv."""
    rows = []
    for document in documents:
        for index, (token, label) in enumerate(zip(document.tokens, document.labels), start=1):
            rows.append({
                "estrattore": tool,
                "formato": source_format,
                "id_delibera": document.id_delibera,
                "indice_token": index,
                "token": token.text,
                "etichetta_BIO": label,
                "ruolo": token.role,
                "posizione_relativa": round(token.relative_position, 6),
            })
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Confronta SVM, regressione logistica e Naive Bayes sulla segmentazione golden."
    )
    parser.add_argument("--segmentazione", required=True, type=Path, help="Cartella con le estrazioni golden")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Radice separata per dataset BIO, modelli e metriche (default: {DEFAULT_OUTPUT_ROOT})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    segmentation_dir = args.segmentazione.expanduser().resolve()
    golden_path = args.golden.expanduser().resolve()
    if not segmentation_dir.is_dir():
        raise FileNotFoundError(f"Cartella segmentazione inesistente: {segmentation_dir}")
    if not golden_path.is_file():
        raise FileNotFoundError(f"Golden CSV inesistente: {golden_path}")

    golden = read_golden(golden_path)
    if len(golden) < 3:
        raise ValueError("Servono almeno tre delibere golden per la validazione per documento")
    loaded_conditions: dict[tuple[str, str], tuple[list[Document], list[dict[str, Any]]]] = {}
    for tool, source_format in CONDITIONS:
        loaded_conditions[(tool, source_format)] = load_condition(
            segmentation_dir, tool, source_format, golden
        )

    output_dir = next_output_dir(args.output.expanduser().resolve())
    all_metrics: list[dict[str, Any]] = []
    all_predictions: list[dict[str, Any]] = []
    all_audit: list[dict[str, Any]] = []
    all_bio_rows: list[dict[str, Any]] = []

    print(f"Golden: {len(golden)} delibere, {sum(map(len, golden.values()))} blocchi")
    print(f"Output: {output_dir}")
    for tool, source_format in CONDITIONS:
        print(f"[{tool}/{source_format}] allineamento e addestramento...")
        documents, audit = loaded_conditions[(tool, source_format)]
        all_bio_rows.extend(export_bio_rows(documents, tool, source_format))
        low_quality = sum(row["stato"] != "OK" for row in audit)
        if low_quality:
            print(f"[{tool}/{source_format}] attenzione: {low_quality} allineamenti da controllare")
        metrics, predictions = train_condition(documents, tool, source_format, output_dir)
        all_audit.extend({**row, "estrattore": tool, "formato": source_format} for row in audit)
        all_metrics.extend(metrics)
        all_predictions.extend(predictions)

    metric_columns = [
        "estrattore", "formato", "modello", "fold_test", "n_token_test",
        "token_accuracy", "token_macro_f1", "boundary_precision", "boundary_recall",
        "boundary_f1", "type_accuracy_su_confine_esatto",
    ]
    write_csv(output_dir / "metriche_per_delibera.csv", all_metrics, metric_columns)
    summary = summarize(all_metrics)
    summary_columns = list(summary[0]) if summary else []
    write_csv(output_dir / "riepilogo_modelli.csv", summary, summary_columns)
    write_csv(
        output_dir / "predizioni_blocchi_cv.csv", all_predictions,
        ["estrattore", "formato", "modello", "id_delibera", "ordine_globale", "tipo_blocco", "testo_blocco"],
    )
    write_csv(
        output_dir / "audit_allineamento.csv", all_audit,
        ["estrattore", "formato", "id_delibera", "ordine_globale", "tipo_blocco", "similarita", "metodo", "stato", "token_start", "token_end"],
    )
    write_csv(
        output_dir / "golden_delibere_BIO.csv",
        all_bio_rows,
        [
            "estrattore", "formato", "id_delibera", "indice_token",
            "token", "etichetta_BIO", "ruolo", "posizione_relativa",
        ],
    )
    (output_dir / "configurazione.json").write_text(
        json.dumps({
            "golden": str(golden_path),
            "segmentazione": str(segmentation_dir),
            "schema_target": "B:TIPO / I / O",
            "validazione": "leave-one-document-out",
            "modelli": ["svm", "logistica", "naive_bayes"],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Addestramento completato: {output_dir}")


if __name__ == "__main__":
    main()
