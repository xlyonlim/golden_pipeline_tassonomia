"""Worker isolato: converte un solo PDF con Docling in JSON e Markdown."""

from pathlib import Path
import json
import re
import sys


def needs_full_page_ocr(document_dict: dict) -> bool:
    """Rileva pagine con livello testuale assente o ingannevolmente scarso."""
    page_parts: dict[int, list[str]] = {}
    page_has_picture: dict[int, bool] = {}

    for item in document_dict.get("texts", []):
        text = str(item.get("text") or "")
        for provenance in item.get("prov") or []:
            page = provenance.get("page_no")
            if page is not None:
                page_parts.setdefault(int(page), []).append(text)

    for item in document_dict.get("pictures", []):
        for provenance in item.get("prov") or []:
            page = provenance.get("page_no")
            if page is not None:
                page_has_picture[int(page)] = True

    declared_pages = {
        int(page_number)
        for page_number in document_dict.get("pages", {})
        if str(page_number).isdigit()
    }
    for page in declared_pages | set(page_parts) | set(page_has_picture):
        combined = " ".join(page_parts.get(page, []))
        letters = len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", combined))
        digits = len(re.findall(r"\d", combined))
        # Una pagina dichiarata dal PDF ma completamente assente dall'output è
        # tipicamente una scansione non rilevata come picture (atti 12/14).
        if letters == 0 or (
            letters < 20 and (digits >= 2 or page_has_picture.get(page, False))
        ):
            return True

    return False

def main() -> None:
    if len(sys.argv) != 3:
        print(
            "Questo è un worker interno e non va eseguito direttamente.\n"
            "Avvia invece: python 01_estrazione_e_segmentazione.py"
        )
        return

    from docling.document_converter import DocumentConverter
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import PdfFormatOption

    pdf_path = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Primo passaggio veloce: OCR automatico, senza forzarlo sulle pagine
    # che possiedono già un livello testuale affidabile.
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    document = result.document
    document_dict = document.export_to_dict()

    # Secondo passaggio soltanto se il primo rileva pagine sospette.
    if needs_full_page_ocr(document_dict):
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.ocr_options.force_full_page_ocr = True
        converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
        )
        result = converter.convert(str(pdf_path))
        document = result.document
        document_dict = document.export_to_dict()

    with (output_dir / f"{pdf_path.stem}.json").open("w", encoding="utf-8") as file:
        json.dump(document_dict, file, ensure_ascii=False, indent=2)

    (output_dir / f"{pdf_path.stem}.md").write_text(
        document.export_to_markdown(), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
