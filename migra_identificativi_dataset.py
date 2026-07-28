"""Migra il dataset legacy ATTO_N agli ID GOLD_#### e INPUT_####."""

from __future__ import annotations

import csv
import hashlib
import re
import uuid
from pathlib import Path

from document_ids import canonical_document_id


SCRIPT_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = SCRIPT_DIR / "Delibere golden"
INPUT_DIR = SCRIPT_DIR / "Input"
MAPPING_PATH = SCRIPT_DIR / "00_mappa_migrazione_id.csv"
MANIFEST_PATH = SCRIPT_DIR / "manifest_documenti.csv"
LEGACY_PDF_RE = re.compile(r"atto_(\d+)\.pdf", re.IGNORECASE)
NEW_PDF_RE = re.compile(r"(GOLD|INPUT)_(\d+)\.pdf", re.IGNORECASE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_delimited(path: Path) -> tuple[list[dict[str, str]], list[str], str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        header = file.readline()
        file.seek(0)
        delimiter = ";" if ";" in header else ","
        reader = csv.DictReader(file, delimiter=delimiter)
        return list(reader), list(reader.fieldnames or []), delimiter


def write_delimited(
    path: Path, rows: list[dict[str, str]], columns: list[str], delimiter: str = ";"
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file, fieldnames=columns, delimiter=delimiter, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def active_pdfs(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: int(re.search(r"(\d+)", path.stem).group(1)),
    )


def build_mapping(
    directory: Path, prefix: str
) -> list[dict[str, str]]:
    pdfs = active_pdfs(directory)
    records: list[dict[str, str]] = []
    for index, path in enumerate(pdfs, start=1):
        legacy = LEGACY_PDF_RE.fullmatch(path.name)
        current = NEW_PDF_RE.fullmatch(path.name)
        if legacy:
            old_number = int(legacy.group(1))
            old_id = f"ATTO_{old_number:03d}"
            old_name = path.name
        elif current and current.group(1).upper() == prefix:
            old_id = f"{prefix}_{int(current.group(2)):04d}"
            old_name = path.name
        else:
            raise ValueError(
                f"Nome PDF inatteso in {directory}: {path.name}. "
                "La migrazione accetta soltanto atto_N.pdf o gli ID nuovi."
            )

        new_id = f"{prefix}_{index:04d}"
        records.append({
            "insieme": "GOLDEN" if prefix == "GOLD" else "INPUT",
            "id_precedente": old_id,
            "nome_precedente": old_name,
            "id_documento": new_id,
            "nome_file": f"{new_id}.pdf",
            "percorso_relativo": str(
                (directory / f"{new_id}.pdf").relative_to(SCRIPT_DIR)
            ),
        })
    return records


def rename_two_phase(directory: Path, records: list[dict[str, str]]) -> None:
    pending: list[tuple[Path, Path]] = []
    for record in records:
        source = directory / record["nome_precedente"]
        destination = directory / record["nome_file"]
        if source == destination:
            continue
        if not source.exists():
            raise FileNotFoundError(f"PDF sorgente non trovato: {source}")
        if destination.exists():
            raise FileExistsError(f"Destinazione gia' esistente: {destination}")
        temporary = directory / f".__migrazione_{uuid.uuid4().hex}.pdf"
        source.rename(temporary)
        pending.append((temporary, destination))

    for temporary, destination in pending:
        temporary.rename(destination)


def migrate_golden_csv(
    path: Path, golden_mapping: list[dict[str, str]]
) -> None:
    rows, columns, delimiter = read_delimited(path)
    if "id_delibera" not in columns:
        raise ValueError(f"Colonna id_delibera mancante in {path}")

    by_old = {
        canonical_document_id(record["id_precedente"], default_prefix="GOLD"):
        record["id_documento"]
        for record in golden_mapping
    }
    migrated = []
    for row in rows:
        current = str(row.get("id_delibera") or "")
        canonical = canonical_document_id(current, default_prefix="GOLD")
        row["id_delibera"] = by_old.get(canonical, canonical)
        migrated.append(row)
    write_delimited(path, migrated, columns, delimiter)


def write_mapping(records: list[dict[str, str]]) -> None:
    columns = [
        "insieme",
        "id_precedente",
        "nome_precedente",
        "id_documento",
        "nome_file",
        "percorso_relativo",
    ]
    write_delimited(MAPPING_PATH, records, columns)


def write_manifest(records: list[dict[str, str]]) -> None:
    golden_rows, _, _ = read_delimited(GOLDEN_DIR / "golden_delibere.csv")
    annotated_ids = {
        canonical_document_id(row["id_delibera"], default_prefix="GOLD")
        for row in golden_rows
    }
    rows = []
    for record in records:
        path = SCRIPT_DIR / record["percorso_relativo"]
        rows.append({
            "id_documento": record["id_documento"],
            "insieme": record["insieme"],
            "annotato": (
                "1"
                if record["insieme"] == "GOLDEN"
                and record["id_documento"] in annotated_ids
                else "0"
            ),
            "regione": "",
            "comune": "",
            "numero_delibera": "",
            "data_delibera": "",
            "nome_file": path.name,
            "percorso_relativo": record["percorso_relativo"],
            "sha256": sha256(path),
            "id_precedente": record["id_precedente"],
            "nome_precedente": record["nome_precedente"],
        })
    columns = [
        "id_documento",
        "insieme",
        "annotato",
        "regione",
        "comune",
        "numero_delibera",
        "data_delibera",
        "nome_file",
        "percorso_relativo",
        "sha256",
        "id_precedente",
        "nome_precedente",
    ]
    write_delimited(MANIFEST_PATH, rows, columns)


def main() -> None:
    golden_mapping = build_mapping(GOLDEN_DIR, "GOLD")
    input_mapping = build_mapping(INPUT_DIR, "INPUT")
    if len(golden_mapping) != 50:
        raise ValueError(
            f"Attesi 50 PDF golden prima della migrazione, trovati {len(golden_mapping)}"
        )
    if len(input_mapping) != 20:
        raise ValueError(
            f"Attesi 20 PDF input prima della migrazione, trovati {len(input_mapping)}"
        )

    current_records = golden_mapping + input_mapping
    already_migrated = all(
        record["id_precedente"] == record["id_documento"]
        and record["nome_precedente"] == record["nome_file"]
        for record in current_records
    )
    if already_migrated and MAPPING_PATH.exists():
        historical_records, _, _ = read_delimited(MAPPING_PATH)
        current_ids = {record["id_documento"] for record in current_records}
        historical_ids = {record["id_documento"] for record in historical_records}
        if current_ids != historical_ids:
            raise ValueError(
                "La mappa di migrazione esistente non corrisponde ai PDF attuali."
            )
        write_manifest(historical_records)
        print("Dataset gia' migrato; mappa storica conservata e manifest aggiornato.")
        print(f"Mappa: {MAPPING_PATH}")
        print(f"Manifest: {MANIFEST_PATH}")
        return

    for csv_name in ("golden_delibere.csv", "golden_delibere1.csv"):
        migrate_golden_csv(GOLDEN_DIR / csv_name, golden_mapping)

    rename_two_phase(GOLDEN_DIR, golden_mapping)
    rename_two_phase(INPUT_DIR, input_mapping)
    write_mapping(current_records)
    write_manifest(current_records)

    print(f"Migrati {len(golden_mapping)} golden e {len(input_mapping)} input.")
    print(f"Mappa: {MAPPING_PATH}")
    print(f"Manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
