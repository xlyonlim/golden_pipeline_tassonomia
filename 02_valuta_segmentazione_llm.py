"""Valuta i dataset di segmentazione con Nemotron 3 Super su Ollama Cloud."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "nemotron-3-super:cloud"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
MAX_REFERENCE_CHARS = 80_000
MAX_CANDIDATE_CHARS = 25_000
_reference_lock = threading.Lock()
_reference_cache: dict[str, tuple[str, str]] = {}


def natural_number(value: str) -> int:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else 10**9


def latest_segmentation(root: Path) -> Path:
    candidates = []
    for path in root.glob("SEGMENTAZIONE_*"):
        match = re.fullmatch(r"SEGMENTAZIONE_(\d+)", path.name, re.IGNORECASE)
        if path.is_dir() and match:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        raise FileNotFoundError("Nessuna cartella SEGMENTAZIONE_N trovata")
    incomplete = [item for item in candidates if not (item[1] / "00_COMPLETATA.txt").exists()]
    return max(incomplete or candidates, key=lambda item: item[0])[1]


def read_dataset(path: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        header = file.readline()
        file.seek(0)
        # Il testo delle delibere contiene molte virgole: contare i separatori
        # sull'intero campione può quindi scambiare un CSV ';' per un CSV ','.
        # L'intestazione non contiene testo libero ed è una sorgente affidabile.
        delimiter = ";" if ";" in header else ","
        for row in csv.DictReader(file, delimiter=delimiter):
            grouped.setdefault(row["id_delibera"], []).append(row)
    return grouped


def pdf_reference(input_dir: Path, segmentation_dir: Path, id_delibera: str) -> tuple[str, str]:
    with _reference_lock:
        if id_delibera in _reference_cache:
            return _reference_cache[id_delibera]

    number = natural_number(id_delibera)
    pdf_path = input_dir / f"atto_{number}.pdf"
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        winget_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
        matches = sorted(winget_root.glob("oschwartz10612.Poppler_*/*/Library/bin/pdftotext.exe"))
        pdftotext = str(matches[-1]) if matches else None

    text = ""
    source = "pdf_text_layer"
    if pdftotext is not None:
        process = subprocess.run(
            [pdftotext, "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
            capture_output=True,
            check=False,
        )
        text = process.stdout.decode("utf-8", errors="replace")

    if len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", text)) < 500:
        fallback = segmentation_dir / "DOCLING" / "_estrazioni" / f"atto_{number}.md"
        if fallback.exists():
            text = fallback.read_text(encoding="utf-8", errors="replace")
            source = "docling_ocr_fallback"

    result = (text[:MAX_REFERENCE_CHARS], source)
    with _reference_lock:
        _reference_cache[id_delibera] = result
    return result


def blocks_as_text(rows: list[dict[str, str]]) -> str:
    ordered = sorted(rows, key=lambda row: int(row.get("ordine_globale") or 0))
    return "\n".join(
        f"[{row.get('ordine_globale')}] {row.get('tipo_blocco')}: {row.get('testo_blocco')}"
        for row in ordered
    )[:MAX_CANDIDATE_CHARS]


def ollama_json(model: str, prompt: str, retries: int = 2) -> dict[str, Any]:
    payload = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0},
    }).encode("utf-8")

    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                OLLAMA_CHAT_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=900) as response:
                body = json.loads(response.read().decode("utf-8"))
            return json.loads(body["message"]["content"])
        except Exception as exc:
            if attempt >= retries:
                raise
            print(
                f"[OLLAMA] tentativo {attempt + 1} fallito: {exc}; nuovo tentativo...",
                flush=True,
            )
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("Risposta Ollama non disponibile")


def evaluate_document(
    model: str,
    input_dir: Path,
    segmentation_dir: Path,
    tool: str,
    id_delibera: str,
    json_rows: list[dict[str, str]],
    markdown_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    print(f"[{tool}] invio {id_delibera} a Ollama...", flush=True)
    reference, reference_source = pdf_reference(input_dir, segmentation_dir, id_delibera)
    prompt = f"""
Sei un valutatore rigoroso di segmentazioni di deliberazioni amministrative italiane.
Confronta i due dataset candidati con il testo di riferimento del PDF.

Valuta separatamente JSON e MARKDOWN da 0 a 100 su:
- marker_precision: correttezza delle etichette PREMESSO, VISTO, RITENUTO ecc.;
- marker_recall: marker narrativi presenti nel riferimento e recuperati;
- boundary_accuracy: inizio/fine dei blocchi e stop prima di votazione, tabelle e dispositivo;
- text_fidelity: fedeltà del testo, ordine e assenza di omissioni/allucinazioni;
- noise_exclusion: esclusione di certificati, firme, pubblicazione e dispositivo;
- overall_score: giudizio complessivo coerente con le metriche precedenti.

Vincoli metodologici obbligatori:
- si valuta esclusivamente la NARRATIVA precedente al dispositivo;
- DELIBERA, PROPONE/PROPONE DI DELIBERARE, votazioni, punti dispositivi,
  firme, certificati, pubblicazione, intestazioni e liste presenze devono essere
  esclusi: non considerarli omissioni e non ridurre recall o text_fidelity;
- VISTO è un'etichetta canonica che comprende VISTO/VISTA/VISTI/VISTE: non
  penalizzare genere o numero grammaticale;
- i suffissi progressivi _1, _2, ... sono previsti dal dataset e non sono errori;
- VISTI_PARERI e ACQUISITI_PARERI sono etichette semantiche ammesse e distinte
  dai richiami normativi generici;
- le sezioni CANDIDATO JSON e CANDIDATO MARKDOWN sono rappresentazioni testuali
  dei blocchi estratti: non valutarne la sintassi come JSON o Markdown;
- concentra issues e punteggi su marker realmente mancanti/spuri, confini dei
  blocchi, ordine, omissioni interne alla narrativa e rumore effettivo.

Restituisci esclusivamente JSON valido con questa struttura:
{{"evaluations":[{{"format":"JSON","marker_precision":0,"marker_recall":0,
"boundary_accuracy":0,"text_fidelity":0,"noise_exclusion":0,"overall_score":0,
"issues":["..."],"notes":"..."}},{{"format":"MARKDOWN", ...}}]}}

ATTO: {id_delibera}
ESTRATTORE: {tool}
SORGENTE RIFERIMENTO: {reference_source}

TESTO DI RIFERIMENTO:
{reference}

CANDIDATO JSON:
{blocks_as_text(json_rows)}

CANDIDATO MARKDOWN:
{blocks_as_text(markdown_rows)}
"""
    response = ollama_json(model, prompt)
    results = []
    for evaluation in response.get("evaluations", []):
        evaluation.update({
            "id_delibera": id_delibera,
            "estrattore": tool,
            "reference_source": reference_source,
        })
        results.append(evaluation)
    return results


def wait_for_tool(segmentation_dir: Path, tool: str, timeout: int) -> tuple[Path, Path]:
    json_path = segmentation_dir / tool / "JSON" / "02_blocchi_narrativi.csv"
    markdown_path = segmentation_dir / tool / "MARKDOWN" / "02_blocchi_narrativi.csv"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if json_path.exists() and markdown_path.exists():
            return json_path, markdown_path
        time.sleep(5)
    raise TimeoutError(f"Dataset {tool} non disponibili entro {timeout} secondi")


def evaluate_tool(
    model: str,
    input_dir: Path,
    segmentation_dir: Path,
    tool: str,
    workers: int,
    timeout: int,
) -> list[dict[str, Any]]:
    json_path, markdown_path = wait_for_tool(segmentation_dir, tool, timeout)
    json_data = read_dataset(json_path)
    markdown_data = read_dataset(markdown_path)
    ids = sorted(set(json_data) | set(markdown_data), key=natural_number)
    results: list[dict[str, Any]] = []
    print(
        f"[{tool}] avvio valutazione di {len(ids)} atti con {workers} richieste concorrenti.",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                evaluate_document,
                model,
                input_dir,
                segmentation_dir,
                tool,
                id_delibera,
                json_data.get(id_delibera, []),
                markdown_data.get(id_delibera, []),
            ): id_delibera
            for id_delibera in ids
        }
        for future in as_completed(futures):
            id_delibera = futures[future]
            try:
                results.extend(future.result())
                print(f"[{tool}] valutato {id_delibera}", flush=True)
            except Exception as exc:
                results.append({
                    "id_delibera": id_delibera,
                    "estrattore": tool,
                    "format": "ERRORE",
                    "notes": str(exc),
                })
    return results


def next_evaluation_dir(segmentation_dir: Path) -> Path:
    """Crea una nuova cartella numerata senza sovrascrivere valutazioni precedenti."""
    numbers = []
    for path in segmentation_dir.glob("VALUTAZIONE_LLM_*"):
        match = re.fullmatch(r"VALUTAZIONE_LLM_(\d+)", path.name, re.IGNORECASE)
        if path.is_dir() and match:
            numbers.append(int(match.group(1)))
    output_dir = segmentation_dir / f"VALUTAZIONE_LLM_{max(numbers, default=0) + 1}"
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


def save_results(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    json_path = output_dir / "valutazione_dettaglio.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = [
        "id_delibera", "estrattore", "format", "reference_source",
        "marker_precision", "marker_recall", "boundary_accuracy",
        "text_fidelity", "noise_exclusion", "overall_score", "issues", "notes",
    ]
    with (output_dir / "valutazione_dettaglio.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(file, fieldnames=columns, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (natural_number(str(item.get("id_delibera", ""))), str(item.get("estrattore", "")), str(item.get("format", "")))):
            normalized = dict(row)
            normalized["issues"] = " | ".join(row.get("issues", [])) if isinstance(row.get("issues"), list) else row.get("issues", "")
            writer.writerow(normalized)

    score_fields = [
        "marker_precision", "marker_recall", "boundary_accuracy",
        "text_fidelity", "noise_exclusion", "overall_score",
    ]
    summary = []
    groups = sorted({(row.get("estrattore"), row.get("format")) for row in rows if row.get("format") != "ERRORE"})
    for tool, fmt in groups:
        group = [row for row in rows if row.get("estrattore") == tool and row.get("format") == fmt]
        summary_row: dict[str, Any] = {"estrattore": tool, "format": fmt, "n_atti": len(group)}
        for field in score_fields:
            values = [float(row[field]) for row in group if row.get(field) is not None]
            summary_row[field] = round(sum(values) / len(values), 2) if values else None
        summary.append(summary_row)
    (output_dir / "valutazione_riepilogo.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary_columns = ["estrattore", "format", "n_atti", *score_fields]
    with (output_dir / "valutazione_riepilogo.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(
            file, fieldnames=summary_columns, delimiter=";", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(summary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Valuta la segmentazione tramite Ollama Cloud")
    parser.add_argument("--segmentazione", type=Path, help="Cartella SEGMENTAZIONE_N; default: ultima incompleta o più recente")
    parser.add_argument("--input", type=Path, default=SCRIPT_DIR / "Input")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--concorrenza", type=int, default=2)
    parser.add_argument("--timeout-attesa", type=int, default=7200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    segmentation_dir = (
        args.segmentazione.resolve() if args.segmentazione else latest_segmentation(SCRIPT_DIR)
    )
    if not 1 <= args.concorrenza <= 4:
        raise ValueError("--concorrenza deve essere compresa tra 1 e 4")

    output_dir = next_evaluation_dir(segmentation_dir)
    print(f"Risultati destinati a: {output_dir}", flush=True)
    all_results: list[dict[str, Any]] = []
    # Docling viene valutato appena pronto; intanto OpenDataLoader può continuare localmente.
    for tool in ("DOCLING", "OPENDATALOADER"):
        all_results.extend(
            evaluate_tool(
                args.model,
                args.input.resolve(),
                segmentation_dir,
                tool,
                args.concorrenza,
                args.timeout_attesa,
            )
        )
        save_results(output_dir, all_results)

    print(f"Valutazione completata: {output_dir}")


if __name__ == "__main__":
    main()
