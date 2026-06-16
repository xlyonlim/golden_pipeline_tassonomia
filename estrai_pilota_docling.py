from __future__ import annotations

import csv
from pathlib import Path

from pilota_testo_utils import pdf_files, pulisci_testo_atto, qualita_testo, salva_testo


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "Input"
OUTPUT_DIR = BASE_DIR / "dataset_pilota" / "output_docling"
TXT_DIR = BASE_DIR / "dataset_pilota" / "txt_docling"
RAW_MD_DIR = OUTPUT_DIR / "markdown_raw"
REPORT_CSV = OUTPUT_DIR / "report_docling.csv"


def converti_con_docling(converter, pdf_path: Path) -> tuple[str, str]:
    result = converter.convert(str(pdf_path))
    markdown = result.document.export_to_markdown()
    testo_pulito = pulisci_testo_atto(markdown)
    return markdown, testo_pulito


def main() -> None:
    if not INPUT_DIR.exists():
        raise FileNotFoundError(f"Cartella Input non trovata: {INPUT_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_MD_DIR.mkdir(parents=True, exist_ok=True)

    files = pdf_files(INPUT_DIR)
    if not files:
        print(f"Nessun PDF trovato in {INPUT_DIR}")
        return

    print(f"Trovati {len(files)} PDF in {INPUT_DIR}")
    records = []

    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            "Docling non e installato. Installa con: python -m pip install docling"
        ) from exc

    converter = DocumentConverter()

    for indice, pdf_path in enumerate(files, 1):
        print(f"\n[{indice}/{len(files)}] Docling: {pdf_path.name}")
        raw_path = RAW_MD_DIR / f"{pdf_path.stem}.md"
        txt_path = TXT_DIR / f"{pdf_path.stem}.txt"

        if raw_path.exists():
            markdown_raw = raw_path.read_text(encoding="utf-8", errors="ignore")
            testo_pulito = pulisci_testo_atto(markdown_raw)
            salva_testo(txt_path, testo_pulito)
            print("  markdown gia presente, rigenero solo il TXT pulito.")
        else:
            markdown_raw, testo_pulito = converti_con_docling(converter, pdf_path)
            salva_testo(raw_path, markdown_raw)
            salva_testo(txt_path, testo_pulito)

        records.append(
            {
                "file": pdf_path.name,
                "markdown_raw": str(raw_path),
                "txt_pulito": str(txt_path),
                "caratteri_raw": len(markdown_raw),
                "caratteri_puliti": len(testo_pulito),
                "score_raw": qualita_testo(markdown_raw),
                "score_pulito": qualita_testo(testo_pulito),
            }
        )

    with REPORT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(records)

    print(f"\nCompletato. TXT puliti: {TXT_DIR}")
    print(f"Markdown originali Docling: {RAW_MD_DIR}")
    print(f"Report: {REPORT_CSV}")


if __name__ == "__main__":
    main()
