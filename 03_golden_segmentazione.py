"""Prepara la segmentazione golden e il dataset BIO.

Questo script non addestra classificatori. Si limita a:
- trovare o generare la segmentazione dei soli PDF annotati nel golden;
- allineare i blocchi manuali ai token estratti;
- esportare le etichette BIO e l'audit di allineamento.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from document_ids import (
    canonical_document_id,
    document_filename,
    document_sort_key,
)


SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_OUTPUT_ROOT = SCRIPT_DIR / Path(__file__).stem
DEFAULT_INPUT_SEGMENTATION_ROOT = SCRIPT_DIR / "01_estrazione_e_segmentazione"
DEFAULT_GOLDEN = SCRIPT_DIR / "Delibere golden" / "golden_delibere.csv"
DEFAULT_GOLDEN_INPUT = SCRIPT_DIR / "Delibere golden"
DEFAULT_GOLDEN_SEGMENTATION = SCRIPT_OUTPUT_ROOT / "SEGMENTAZIONE_SU_GOLDEN"
LEGACY_GOLDEN_SEGMENTATION = SCRIPT_DIR / "SEGMENTAZIONE_SU_GOLDEN"
DEFAULT_OUTPUT_ROOT = SCRIPT_OUTPUT_ROOT
WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
PROGRESSIVE_RE = re.compile(r"_\d+$")
CONDITIONS = (
    ("DOCLING", "JSON"),
    ("DOCLING", "MARKDOWN"),
    ("OPENDATALOADER", "JSON"),
    ("OPENDATALOADER", "MARKDOWN"),
    ("MARKER", "JSON"),
    ("MARKER", "MARKDOWN"),
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


def canonical_id(value: str, default_prefix: str = "GOLD") -> str:
    return canonical_document_id(value, default_prefix=default_prefix)


def base_type(value: str) -> str:
    return PROGRESSIVE_RE.sub("", value.strip().upper())


def normalize_word(value: str) -> str:
    return value.lower().replace("â€™", "'").replace("’", "'")


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


def all_occurrences(
    haystack: list[str], needle: list[str], start: int, stop: int
) -> Iterable[int]:
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
                end = min(
                    candidates,
                    key=lambda value: abs((value + anchor_len - start) - len(golden)),
                ) + anchor_len
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
        golden_words = [
            normalize_word(match.group()) for match in WORD_RE.finditer(row["testo_blocco"])
        ]
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


def format_ids(ids: Iterable[str], max_items: int = 10) -> str:
    ordered = sorted(ids, key=document_sort_key)
    if len(ordered) <= max_items:
        return ", ".join(ordered)
    shown = ", ".join(ordered[:max_items])
    return f"{shown}, ... (+{len(ordered) - max_items} altri)"


def segmentation_ids(segmentation_dir: Path, tool: str, source_format: str) -> set[str]:
    path = segmentation_dir / tool / source_format / "01_elementi_normalizzati.csv"
    if not path.exists():
        return set()
    return {canonical_id(row["id_atto"]) for row in read_csv(path)}


def all_segmentation_ids(segmentation_dir: Path) -> set[str]:
    ids: set[str] = set()
    for tool, source_format in CONDITIONS:
        ids.update(segmentation_ids(segmentation_dir, tool, source_format))
    return ids


def manifest_mentions_path(segmentation_dir: Path, path: Path) -> bool:
    target = str(path.resolve()).lower()
    target_name = path.name.lower()
    for tool, _ in CONDITIONS:
        manifest = segmentation_dir / tool / "00_manifest_pdf_input.csv"
        if not manifest.exists():
            continue
        for row in read_csv(manifest):
            original = str(row.get("percorso_originale") or "").lower()
            if target in original or target_name in original:
                return True
    return False


def is_golden_segmentation(segmentation_dir: Path, golden_ids: set[str]) -> bool:
    if (segmentation_dir / "00_SEGMENTAZIONE_SU_GOLDEN.txt").exists():
        return True
    if manifest_mentions_path(segmentation_dir, DEFAULT_GOLDEN_INPUT):
        return True
    ids = all_segmentation_ids(segmentation_dir)
    return bool(ids & golden_ids)


def input_segmentation_or_none(
    root: Path, golden_ids: set[str], tool: str, source_format: str
) -> Path | None:
    search_roots = [root]
    if root.resolve() != SCRIPT_DIR.resolve():
        search_roots.append(SCRIPT_DIR)
    seen: set[Path] = set()
    for search_root in search_roots:
        for _, _, path in numbered_segmentation_dirs(search_root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if is_golden_segmentation(path, golden_ids):
                continue
            if segmentation_ids(path, tool, source_format):
                return path
    return None


def missing_golden_by_condition(
    segmentation_dir: Path, golden_ids: set[str]
) -> dict[str, set[str]]:
    missing = {}
    for tool, source_format in CONDITIONS:
        ids = segmentation_ids(segmentation_dir, tool, source_format)
        condition = f"{tool}/{source_format}"
        if not ids:
            missing[condition] = set(golden_ids)
            continue
        condition_missing = golden_ids - ids
        if condition_missing:
            missing[condition] = condition_missing
    return missing


def numbered_segmentation_dirs(root: Path) -> list[tuple[int, float, Path]]:
    candidates: list[tuple[int, float, Path]] = []
    for path in root.glob("SEGMENTAZIONE_*"):
        match = re.fullmatch(r"SEGMENTAZIONE_(\d+)", path.name, re.IGNORECASE)
        if path.is_dir() and match:
            candidates.append((int(match.group(1)), path.stat().st_mtime, path))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates


def archive_existing_path(path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    archived = path.with_name(f"{path.name}_PRECEDENTE_{timestamp}")
    counter = 2
    while archived.exists():
        archived = path.with_name(f"{path.name}_PRECEDENTE_{timestamp}_{counter}")
        counter += 1
    shutil.move(str(path), str(archived))
    return archived


def make_temp_dir(parent: Path, prefix: str) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = parent / f"{prefix}_{timestamp}"
    counter = 2
    while path.exists():
        path = parent / f"{prefix}_{timestamp}_{counter}"
        counter += 1
    path.mkdir(parents=True, exist_ok=False)
    return path


def find_golden_pdf(doc_id: str) -> Path:
    expected = document_filename(doc_id, default_prefix="GOLD")
    direct = DEFAULT_GOLDEN_INPUT / expected
    if direct.exists():
        return direct
    matches = [
        path for path in DEFAULT_GOLDEN_INPUT.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".pdf"
        and canonical_id(path.stem, "GOLD") == canonical_id(doc_id, "GOLD")
    ]
    if matches:
        return matches[0]
    raise FileNotFoundError(f"PDF golden mancante per {doc_id}: {expected}")


def prepare_annotated_golden_input(root: Path, golden_ids: set[str]) -> Path:
    temp_input = make_temp_dir(root, "_tmp_golden_annotati")
    for doc_id in sorted(golden_ids, key=natural_number):
        source = find_golden_pdf(doc_id)
        shutil.copy2(
            source,
            temp_input / document_filename(doc_id, default_prefix="GOLD"),
        )
    return temp_input


def write_golden_segmentation_marker(
    segmentation_dir: Path, golden_ids: set[str] | None = None
) -> None:
    marker = segmentation_dir / "00_SEGMENTAZIONE_SU_GOLDEN.txt"
    ids_line = ""
    if golden_ids:
        ids_line = f"Atti annotati nel golden CSV: {format_ids(golden_ids, max_items=100)}\n"
    marker.write_text(
        "Segmentazione usata da 03_golden_segmentazione.py sui soli PDF annotati.\n"
        f"Cartella PDF golden: {DEFAULT_GOLDEN_INPUT}\n"
        f"{ids_line}",
        encoding="utf-8",
    )


def promote_golden_segmentation(
    segmentation_dir: Path, golden_ids: set[str] | None = None
) -> Path:
    DEFAULT_GOLDEN_SEGMENTATION.parent.mkdir(parents=True, exist_ok=True)
    if segmentation_dir.resolve() == DEFAULT_GOLDEN_SEGMENTATION.resolve():
        write_golden_segmentation_marker(DEFAULT_GOLDEN_SEGMENTATION, golden_ids)
        return DEFAULT_GOLDEN_SEGMENTATION

    if DEFAULT_GOLDEN_SEGMENTATION.exists():
        archived = archive_existing_path(DEFAULT_GOLDEN_SEGMENTATION)
        print(f"SEGMENTAZIONE_SU_GOLDEN precedente spostata in: {archived.name}")

    shutil.move(str(segmentation_dir), str(DEFAULT_GOLDEN_SEGMENTATION))
    write_golden_segmentation_marker(DEFAULT_GOLDEN_SEGMENTATION, golden_ids)
    print(f"Segmentazione golden salvata come: {DEFAULT_GOLDEN_SEGMENTATION}")
    return DEFAULT_GOLDEN_SEGMENTATION


def generate_golden_segmentation(
    root: Path, golden_ids: set[str], marker_disable_ocr: bool = False
) -> Path:
    if not DEFAULT_GOLDEN_INPUT.is_dir():
        raise FileNotFoundError(f"Cartella PDF golden inesistente: {DEFAULT_GOLDEN_INPUT}")

    temp_input = prepare_annotated_golden_input(root, golden_ids)
    temp_output_root = make_temp_dir(root, "_tmp_segmentazione_su_golden")
    command = [
        sys.executable,
        str(SCRIPT_DIR / "01_estrazione_e_segmentazione.py"),
        "--input",
        str(temp_input),
        "--output",
        str(temp_output_root),
        "--preserva-nomi-pdf",
        "--prefisso-id",
        "GOLD",
    ]
    if marker_disable_ocr:
        command.append("--marker-disable-ocr")

    print(
        "Genero la segmentazione dei soli PDF annotati nel golden "
        f"({format_ids(golden_ids, max_items=100)})..."
    )
    try:
        subprocess.run(command, cwd=str(root), check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Generazione della segmentazione golden fallita (codice {exc.returncode})."
        ) from exc
    finally:
        shutil.rmtree(temp_input, ignore_errors=True)

    diagnostics = []
    for _, _, path in numbered_segmentation_dirs(temp_output_root):
        missing = missing_golden_by_condition(path, golden_ids)
        if not missing:
            promoted = promote_golden_segmentation(path, golden_ids)
            shutil.rmtree(temp_output_root, ignore_errors=True)
            return promoted
        first_condition, first_missing = next(iter(missing.items()))
        diagnostics.append(
            f"- {path.name}: {first_condition} non contiene {format_ids(first_missing)}"
        )

    raise FileNotFoundError(
        "Ho eseguito 01_estrazione_e_segmentazione.py sui PDF golden annotati, "
        "ma non ho trovato una segmentazione compatibile con golden_delibere.csv.\n"
        "Controlli effettuati:\n"
        + "\n".join(diagnostics)
    )


def golden_segmentation_or_generate(
    root: Path, golden_ids: set[str], marker_disable_ocr: bool = False
) -> Path:
    for candidate in (DEFAULT_GOLDEN_SEGMENTATION, LEGACY_GOLDEN_SEGMENTATION):
        if not candidate.is_dir():
            continue
        missing = missing_golden_by_condition(candidate, golden_ids)
        if not missing:
            if (
                candidate.resolve() == LEGACY_GOLDEN_SEGMENTATION.resolve()
                and not DEFAULT_GOLDEN_SEGMENTATION.exists()
            ):
                DEFAULT_GOLDEN_SEGMENTATION.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(candidate, DEFAULT_GOLDEN_SEGMENTATION)
                write_golden_segmentation_marker(DEFAULT_GOLDEN_SEGMENTATION, golden_ids)
                print(
                    "SEGMENTAZIONE_SU_GOLDEN legacy copiata in: "
                    f"{DEFAULT_GOLDEN_SEGMENTATION}"
                )
                return DEFAULT_GOLDEN_SEGMENTATION
            write_golden_segmentation_marker(candidate, golden_ids)
            return candidate

    diagnostics = []
    for _, _, path in numbered_segmentation_dirs(root):
        missing = missing_golden_by_condition(path, golden_ids)
        if not missing:
            return promote_golden_segmentation(path, golden_ids)
        first_condition, first_missing = next(iter(missing.items()))
        diagnostics.append(
            f"- {path.name}: {first_condition} non contiene {format_ids(first_missing)}"
        )

    if diagnostics:
        print("Le segmentazioni presenti non corrispondono al golden:")
        print("\n".join(diagnostics))
    return generate_golden_segmentation(
        root, golden_ids, marker_disable_ocr=marker_disable_ocr
    )


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
        grouped.setdefault(canonical_id(row["id_atto"], "GOLD"), []).append(row)
    missing = sorted(set(golden) - set(grouped), key=natural_number)
    if missing:
        present = sorted(set(grouped), key=natural_number)
        raise ValueError(
            f"{tool}/{source_format}: la segmentazione {segmentation_dir} non contiene "
            f"gli atti del golden ({format_ids(set(missing))}). "
            f"Atti presenti: {format_ids(set(present))}."
        )

    documents = []
    audit = []
    for doc_id in sorted(golden, key=natural_number):
        text, tokens = build_document(grouped[doc_id], doc_id)
        document, rows = label_document(doc_id, text, tokens, golden[doc_id])
        documents.append(document)
        audit.extend(rows)
    return documents, audit


def load_unlabeled_condition(
    segmentation_dir: Path, tool: str, source_format: str
) -> list[Document]:
    path = segmentation_dir / tool / source_format / "01_elementi_normalizzati.csv"
    if not path.exists():
        raise FileNotFoundError(f"File input mancante: {path}")
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(path):
        grouped.setdefault(canonical_id(row["id_atto"], "INPUT"), []).append(row)
    documents = []
    for doc_id in sorted(grouped, key=natural_number):
        text, tokens = build_document(grouped[doc_id], doc_id)
        documents.append(Document(doc_id, text, tokens, []))
    if not documents:
        raise ValueError(f"Nessun documento disponibile in {path}")
    return documents


def export_bio_rows(
    documents: list[Document], tool: str, source_format: str
) -> list[dict[str, Any]]:
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


def next_output_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in output_root.glob("GOLDEN_BIO_*"):
        match = re.fullmatch(r"GOLDEN_BIO_(\d+)", path.name, re.IGNORECASE)
        if path.is_dir() and match:
            numbers.append(int(match.group(1)))
    output = output_root / f"GOLDEN_BIO_{max(numbers, default=0) + 1}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepara segmentazione golden e dataset BIO senza addestrare modelli."
    )
    parser.add_argument(
        "--segmentazione",
        type=Path,
        help=(
            "Cartella con estrazioni golden; default: SEGMENTAZIONE_SU_GOLDEN "
            f"in {SCRIPT_OUTPUT_ROOT} se valida, altrimenti viene generata dai soli PDF "
            "annotati nel CSV golden"
        ),
    )
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Cartella in cui creare GOLDEN_BIO_N (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--marker-disable-ocr",
        action="store_true",
        help=(
            "Quando genera SEGMENTAZIONE_SU_GOLDEN, passa a Marker l'opzione "
            "--disable_ocr per evitare il backend OCR/VLM locale."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    golden_path = args.golden.expanduser().resolve()
    if not golden_path.is_file():
        raise FileNotFoundError(f"Golden CSV inesistente: {golden_path}")

    golden = read_golden(golden_path)
    if not golden:
        raise ValueError("Golden CSV vuoto")

    segmentation_dir = (
        args.segmentazione.expanduser().resolve()
        if args.segmentazione
        else golden_segmentation_or_generate(
            SCRIPT_OUTPUT_ROOT,
            set(golden),
            marker_disable_ocr=args.marker_disable_ocr,
        ).resolve()
    )
    if not segmentation_dir.is_dir():
        raise FileNotFoundError(f"Cartella segmentazione inesistente: {segmentation_dir}")

    output_dir = next_output_dir(args.output.expanduser().resolve())
    all_bio_rows: list[dict[str, Any]] = []
    all_audit: list[dict[str, Any]] = []
    print(f"Golden: {len(golden)} delibere, {sum(map(len, golden.values()))} blocchi")
    print(f"Segmentazione golden: {segmentation_dir}")
    print(f"Output BIO: {output_dir}")

    for tool, source_format in CONDITIONS:
        documents, audit = load_condition(segmentation_dir, tool, source_format, golden)
        all_bio_rows.extend(export_bio_rows(documents, tool, source_format))
        all_audit.extend({**row, "estrattore": tool, "formato": source_format} for row in audit)
        low_quality = sum(row["stato"] != "OK" for row in audit)
        print(
            f"[{tool}/{source_format}] documenti={len(documents)}, "
            f"allineamenti_da_controllare={low_quality}"
        )

    write_csv(
        output_dir / "golden_delibere_BIO.csv",
        all_bio_rows,
        [
            "estrattore", "formato", "id_delibera", "indice_token",
            "token", "etichetta_BIO", "ruolo", "posizione_relativa",
        ],
    )
    write_csv(
        output_dir / "audit_allineamento.csv",
        all_audit,
        [
            "estrattore", "formato", "id_delibera", "ordine_globale",
            "tipo_blocco", "similarita", "metodo", "stato", "token_start", "token_end",
        ],
    )
    (output_dir / "configurazione.json").write_text(
        json.dumps({
            "golden": str(golden_path),
            "segmentazione": str(segmentation_dir),
            "schema_target": "B:TIPO / I / O",
            "condizioni": [f"{tool}/{fmt}" for tool, fmt in CONDITIONS],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Golden BIO completato: {output_dir}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Errore: {exc}") from None
