from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
from pathlib import Path

import opendataloader_pdf

from pilota_testo_utils import pdf_files, pulisci_testo_atto, qualita_testo, salva_testo, testo_sufficiente


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "Input"
OUTPUT_DIR = BASE_DIR / "dataset_pilota" / "output_odl"
TXT_DIR = BASE_DIR / "dataset_pilota" / "txt_open-rapid-paddle"
RAW_DIR = OUTPUT_DIR / "raw"
REPORT_CSV = OUTPUT_DIR / "report_open_rapid_paddle.csv"

OCR_ENGINE = "auto"  # auto | rapidocr | paddleocr
FORZA_OCR_SEMPRE = False
RIGENERA_CACHE = False
OCR_DPI = 220
MAX_PAGINE_OCR = None
SOGLIA_QUALITA_TESTO_NORMALE = 70
SCEGLI_OCR_SE_MIGLIORA_QUALITA_DI = 5
DISABILITA_PADDLE_ONEDNN = True

JAVA_OK = None


def trova_txt_generato(pdf_path: Path, cartella_output: Path) -> Path | None:
    possibile = list(cartella_output.rglob(f"{pdf_path.stem}.txt"))
    if possibile:
        return possibile[0]
    stem_pulito = pdf_path.stem.lower().replace(" ", "")
    for txt_file in cartella_output.rglob("*.txt"):
        txt_stem_pulito = txt_file.stem.lower().replace(" ", "")
        if stem_pulito == txt_stem_pulito or stem_pulito in txt_stem_pulito:
            return txt_file
    return None


def leggi_txt(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def candidati_java_windows() -> list[Path]:
    candidati = []
    java_path = shutil.which("java")
    if java_path:
        candidati.append(Path(java_path))

    for cartella in [
        Path(r"C:\Program Files\Eclipse Adoptium"),
        Path(r"C:\Program Files\Java"),
        Path(r"C:\Program Files\Microsoft"),
        Path(r"C:\Program Files\Amazon Corretto"),
    ]:
        if cartella.exists():
            candidati.extend(cartella.glob(r"**\bin\java.exe"))

    unici = []
    visti = set()
    for candidato in candidati:
        chiave = str(candidato).lower()
        if candidato.exists() and chiave not in visti:
            visti.add(chiave)
            unici.append(candidato)
    return unici


def versione_java_principale(java_cmd: Path) -> tuple[int | None, str]:
    try:
        result = subprocess.run(
            [str(java_cmd), "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return None, f"errore Java: {exc}"

    testo_versione = f"{result.stdout}\n{result.stderr}"
    match = re.search(r'version "([^"]+)"', testo_versione)
    if not match:
        return None, "versione Java non riconosciuta"

    versione = match.group(1)
    if versione.startswith("1."):
        parti = versione.split(".")
        return (int(parti[1]), versione) if len(parti) > 1 and parti[1].isdigit() else (None, versione)

    match_major = re.match(r"(\d+)", versione)
    return (int(match_major.group(1)), versione) if match_major else (None, versione)


def configura_java_per_opendataloader(java_cmd: Path) -> None:
    java_bin = java_cmd.resolve().parent
    java_home = java_bin.parent
    os.environ["JAVA_HOME"] = str(java_home)
    path_attuale = os.environ.get("PATH", "")
    parti_path = [p for p in path_attuale.split(os.pathsep) if p]
    parti_path = [p for p in parti_path if Path(p).resolve() != java_bin]
    os.environ["PATH"] = os.pathsep.join([str(java_bin), *parti_path])


def java_compatibile_con_opendataloader() -> bool:
    global JAVA_OK
    if JAVA_OK is not None:
        return JAVA_OK

    ultimo_dettaglio = "java non trovato"
    for candidato in candidati_java_windows():
        major, dettaglio = versione_java_principale(candidato)
        ultimo_dettaglio = dettaglio
        if major is not None and major >= 11:
            configura_java_per_opendataloader(candidato)
            print(f"OpenDataLoader usera Java: {candidato.resolve()} ({dettaglio})")
            JAVA_OK = True
            return True

    print(f"OpenDataLoader saltato: serve Java 11 o superiore ({ultimo_dettaglio}).")
    JAVA_OK = False
    return False


def converti_opendataloader(pdf_path: Path) -> Path | None:
    txt_esistente = trova_txt_generato(pdf_path, RAW_DIR / "opendataloader")
    if txt_esistente and not RIGENERA_CACHE:
        return txt_esistente
    if not java_compatibile_con_opendataloader():
        return None

    out_dir = RAW_DIR / "opendataloader"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        opendataloader_pdf.convert(input_path=[str(pdf_path)], output_dir=str(out_dir), format="text", quiet=True)
    except Exception as exc:
        print(f"Errore OpenDataLoader su {pdf_path.name}: {exc}")
        return None
    return trova_txt_generato(pdf_path, out_dir)


def renderizza_pdf(pdf_path: Path):
    import fitz
    from PIL import Image

    immagini = []
    doc = fitz.open(str(pdf_path))
    zoom = OCR_DPI / 72
    matrice = fitz.Matrix(zoom, zoom)
    limite = len(doc) if MAX_PAGINE_OCR is None else min(len(doc), MAX_PAGINE_OCR)
    for indice_pagina in range(limite):
        pix = doc[indice_pagina].get_pixmap(matrix=matrice, alpha=False)
        immagini.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
    return immagini


def ordina_righe_ocr(elementi: list[tuple[float, float, str]]) -> str:
    elementi = sorted(elementi, key=lambda item: (item[1], item[0]))
    righe: list[list[tuple[float, float, str]]] = []

    for x, y, testo in elementi:
        if not righe or abs(y - righe[-1][0][1]) > 12:
            righe.append([(x, y, testo)])
        else:
            righe[-1].append((x, y, testo))

    testo_pagine = []
    for riga in righe:
        riga_ordinata = sorted(riga, key=lambda item: item[0])
        testo_pagine.append(" ".join(item[2] for item in riga_ordinata if item[2].strip()))
    return "\n".join(testo_pagine)


def ocr_rapidocr(pdf_path: Path) -> Path | None:
    out = RAW_DIR / "ocr" / f"{pdf_path.stem}_rapidocr.txt"
    if out.exists() and not RIGENERA_CACHE:
        return out
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        print(f"RapidOCR non disponibile: {exc}")
        return None

    print(f"OCR RapidOCR: {pdf_path.name}")
    ocr = RapidOCR()
    pagine = []
    for indice, immagine in enumerate(renderizza_pdf(pdf_path), 1):
        print(f"  RapidOCR pagina {indice}...")
        result, _ = ocr(np.array(immagine))
        elementi = []
        for item in result or []:
            if len(item) < 2 or not str(item[1]).strip():
                continue
            box = item[0]
            x = min(p[0] for p in box)
            y = min(p[1] for p in box)
            elementi.append((x, y, str(item[1]).strip()))
        if elementi:
            pagine.append(ordina_righe_ocr(elementi))

    testo = "\n\n".join(pagine).strip()
    if not testo:
        return None
    salva_testo(out, testo)
    return out


def ocr_paddleocr(pdf_path: Path) -> Path | None:
    out = RAW_DIR / "ocr" / f"{pdf_path.stem}_paddleocr.txt"
    if out.exists() and not RIGENERA_CACHE:
        return out
    try:
        if DISABILITA_PADDLE_ONEDNN:
            os.environ["FLAGS_use_mkldnn"] = "0"
            os.environ["FLAGS_use_onednn"] = "0"
            os.environ["FLAGS_tracer_onednn_ops_off"] = "all"
        import numpy as np
        from paddleocr import PaddleOCR
    except ImportError as exc:
        print(f"PaddleOCR non disponibile: {exc}")
        return None

    print(f"OCR PaddleOCR: {pdf_path.name}")
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
        lang="it",
    )
    pagine = []
    for indice, immagine in enumerate(renderizza_pdf(pdf_path), 1):
        print(f"  PaddleOCR pagina {indice}...")
        result = ocr.predict(np.array(immagine)) if hasattr(ocr, "predict") else ocr.ocr(np.array(immagine))
        elementi = []
        for pagina in result or []:
            if hasattr(pagina, "get"):
                testi = pagina.get("rec_texts", []) or []
                boxes = pagina.get("rec_boxes", []) or pagina.get("dt_polys", []) or []
                for i, testo in enumerate(testi):
                    if not str(testo).strip():
                        continue
                    box = boxes[i] if i < len(boxes) else None
                    x, y = estrai_xy_box(box)
                    elementi.append((x, y, str(testo).strip()))
                continue

            for item in pagina or []:
                if len(item) >= 2 and item[1] and str(item[1][0]).strip():
                    box = item[0]
                    x = min(p[0] for p in box)
                    y = min(p[1] for p in box)
                    elementi.append((x, y, str(item[1][0]).strip()))
        if elementi:
            pagine.append(ordina_righe_ocr(elementi))

    testo = "\n\n".join(pagine).strip()
    if not testo:
        return None
    salva_testo(out, testo)
    return out


def estrai_xy_box(box) -> tuple[float, float]:
    if box is None:
        return 0.0, 0.0
    try:
        if len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            return float(box[0]), float(box[1])
        return min(float(p[0]) for p in box), min(float(p[1]) for p in box)
    except Exception:
        return 0.0, 0.0


def converti_ocr_auto(pdf_path: Path) -> tuple[Path | None, str]:
    engine = OCR_ENGINE.lower().strip()
    if engine == "rapidocr":
        return ocr_rapidocr(pdf_path), "rapidocr"
    if engine == "paddleocr":
        return ocr_paddleocr(pdf_path), "paddleocr"

    txt_rapid = ocr_rapidocr(pdf_path)
    testo_rapid = leggi_txt(txt_rapid)
    score_rapid = qualita_testo(testo_rapid)
    if txt_rapid and score_rapid >= SOGLIA_QUALITA_TESTO_NORMALE:
        return txt_rapid, "rapidocr"

    txt_paddle = ocr_paddleocr(pdf_path)
    testo_paddle = leggi_txt(txt_paddle)
    score_paddle = qualita_testo(testo_paddle)
    if txt_paddle and score_paddle >= score_rapid + SCEGLI_OCR_SE_MIGLIORA_QUALITA_DI:
        return txt_paddle, "paddleocr"
    return (txt_rapid, "rapidocr") if txt_rapid else (txt_paddle, "paddleocr")


def estrai_pdf(pdf_path: Path) -> dict[str, str | int]:
    txt_normale = None if FORZA_OCR_SEMPRE else converti_opendataloader(pdf_path)
    testo_normale = leggi_txt(txt_normale)
    score_normale = qualita_testo(testo_normale)

    sorgente = "opendataloader"
    txt_scelto = txt_normale
    testo_scelto = testo_normale

    if FORZA_OCR_SEMPRE or not testo_sufficiente(testo_normale):
        txt_ocr, engine = converti_ocr_auto(pdf_path)
        testo_ocr = leggi_txt(txt_ocr)
        score_ocr = qualita_testo(testo_ocr)
        if txt_ocr and (txt_normale is None or score_ocr >= score_normale + SCEGLI_OCR_SE_MIGLIORA_QUALITA_DI):
            sorgente = engine
            txt_scelto = txt_ocr
            testo_scelto = testo_ocr

    testo_pulito = pulisci_testo_atto(testo_scelto)
    out_txt = TXT_DIR / f"{pdf_path.stem}.txt"
    salva_testo(out_txt, testo_pulito)

    return {
        "file": pdf_path.name,
        "output": str(out_txt),
        "sorgente": sorgente,
        "raw_path": str(txt_scelto or ""),
        "caratteri_raw": len(testo_scelto),
        "caratteri_puliti": len(testo_pulito),
        "score_raw": qualita_testo(testo_scelto),
        "score_pulito": qualita_testo(testo_pulito),
    }


def main() -> None:
    if not INPUT_DIR.exists():
        raise FileNotFoundError(f"Cartella Input non trovata: {INPUT_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    files = pdf_files(INPUT_DIR)
    if not files:
        print(f"Nessun PDF trovato in {INPUT_DIR}")
        return

    print(f"Trovati {len(files)} PDF in {INPUT_DIR}")
    print(f"OCR_ENGINE = {OCR_ENGINE}")
    records = []
    for indice, pdf_path in enumerate(files, 1):
        print(f"\n[{indice}/{len(files)}] {pdf_path.name}")
        records.append(estrai_pdf(pdf_path))

    with REPORT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(records)
    print(f"\nCompletato. TXT puliti: {TXT_DIR}")
    print(f"Report: {REPORT_CSV}")


if __name__ == "__main__":
    main()
