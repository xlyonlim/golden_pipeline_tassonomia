"""Valuta i dataset di segmentazione con Nemotron 3 Super tramite API NVIDIA."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
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

from document_ids import canonical_document_id, document_filename


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
SCRIPT_OUTPUT_ROOT = SCRIPT_DIR / Path(__file__).stem
DEFAULT_SEGMENTATION_ROOT = SCRIPT_DIR / "01_estrazione_e_segmentazione"
DEFAULT_OUTPUT_ROOT = SCRIPT_OUTPUT_ROOT
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_API_KEY_ENV = "NVIDIA_API_KEY"
EXTRACTOR_TOOLS = ("DOCLING", "OPENDATALOADER", "MARKER")
# I modelli cloud tollerano contesti ampi, ma richieste molto grandi e parallele
# aumentano sensibilmente 502 e risposte JSON troncate.
MAX_REFERENCE_CHARS = 45_000
MAX_CANDIDATE_CHARS = 15_000
_reference_lock = threading.Lock()
_reference_cache: dict[str, tuple[str, str]] = {}


def natural_number(value: str) -> int:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else 10**9


def segmentation_candidates(root: Path) -> list[tuple[int, Path]]:
    candidates = []
    for path in root.glob("SEGMENTAZIONE_*"):
        match = re.fullmatch(r"SEGMENTAZIONE_(\d+)", path.name, re.IGNORECASE)
        if path.is_dir() and match:
            candidates.append((int(match.group(1)), path))
    return candidates


def latest_segmentation(root: Path) -> Path:
    candidates = segmentation_candidates(root)
    if not candidates and root.resolve() != SCRIPT_DIR.resolve():
        candidates = segmentation_candidates(SCRIPT_DIR)
    if not candidates:
        raise FileNotFoundError(
            f"Nessuna cartella SEGMENTAZIONE_N trovata in {root} o nelle vecchie cartelle radice"
        )
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
    document_id = canonical_document_id(id_delibera, default_prefix="INPUT")
    with _reference_lock:
        if document_id in _reference_cache:
            return _reference_cache[document_id]

    pdf_path = input_dir / document_filename(document_id)
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
        fallback = (
            segmentation_dir
            / "DOCLING"
            / "_estrazioni"
            / f"{document_id}.md"
        )
        if fallback.exists():
            text = fallback.read_text(encoding="utf-8", errors="replace")
            source = "docling_ocr_fallback"

    result = (text[:MAX_REFERENCE_CHARS], source)
    with _reference_lock:
        _reference_cache[document_id] = result
    return result


def blocks_as_text(rows: list[dict[str, str]]) -> str:
    ordered = sorted(rows, key=lambda row: int(row.get("ordine_globale") or 0))
    return "\n".join(
        f"[{row.get('ordine_globale')}] {row.get('tipo_blocco')}: {row.get('testo_blocco')}"
        for row in ordered
    )[:MAX_CANDIDATE_CHARS]


def parse_model_json(content: str) -> dict[str, Any]:
    """Legge anche JSON in code fence o con newline non escapati nelle stringhe."""
    content = content.strip().lstrip("\ufeff")
    if not content:
        raise ValueError("L'API NVIDIA ha restituito un contenuto vuoto")

    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    if fenced:
        content = fenced.group(1).strip()

    candidates = [content]
    first_brace = content.find("{")
    last_brace = content.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        extracted = content[first_brace:last_brace + 1]
        if extracted != content:
            candidates.append(extracted)

    errors = []
    for candidate in candidates:
        try:
            result = json.loads(candidate, strict=False)
            if not isinstance(result, dict):
                raise ValueError("la radice JSON non è un oggetto")
            evaluations = result.get("evaluations")
            if not isinstance(evaluations, list) or not evaluations:
                raise ValueError("campo 'evaluations' assente o vuoto")
            return result
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(str(exc))
    raise ValueError(f"JSON del modello non valido: {'; '.join(errors)}")


def nvidia_json(
    model: str,
    prompt: str,
    api_key: str,
    retries: int = 4,
) -> dict[str, Any]:
    payload = json.dumps({
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 2_500,
        "seed": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")

    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                NVIDIA_CHAT_URL,
                data=payload,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=900) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
            if not raw_body.strip():
                raise ValueError("risposta HTTP 200 vuota dall'API NVIDIA")
            body = json.loads(raw_body, strict=False)
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("risposta NVIDIA priva del campo 'choices'")
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict)
                )
            content = str(content)
            return parse_model_json(content)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            detail = re.sub(r"\s+", " ", detail)[:300]
            error = f"HTTP {exc.code} {exc.reason}"
            if detail:
                error += f": {detail}"
            # Credenziali mancanti o richiesta non valida non migliorano con retry.
            if exc.code in {400, 401, 403, 404, 422}:
                raise RuntimeError(error) from exc
            if attempt >= retries:
                raise RuntimeError(error) from exc
            retry_after = (
                exc.headers.get("Retry-After") if exc.headers is not None else None
            )
            delay = (
                float(retry_after)
                if retry_after and retry_after.replace(".", "", 1).isdigit()
                else min(60, 5 * (2 ** attempt)) + random.uniform(0, 2)
            )
            print(
                f"[NVIDIA API] tentativo {attempt + 1}/{retries + 1} fallito: "
                f"{error}; riprovo tra {delay:.1f}s...",
                flush=True,
            )
            time.sleep(delay)
        except Exception as exc:
            if attempt >= retries:
                raise
            delay = min(60, 5 * (2 ** attempt)) + random.uniform(0, 2)
            print(
                f"[NVIDIA API] tentativo {attempt + 1}/{retries + 1} fallito: "
                f"{exc}; riprovo tra {delay:.1f}s...",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError("Risposta NVIDIA non disponibile")


def evaluate_document(
    model: str,
    api_key: str,
    input_dir: Path,
    segmentation_dir: Path,
    tool: str,
    id_delibera: str,
    json_rows: list[dict[str, str]],
    markdown_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    print(f"[{tool}] invio {id_delibera} all'API NVIDIA...", flush=True)
    reference, reference_source = pdf_reference(input_dir, segmentation_dir, id_delibera)
    prompt = f"""
Sei un valutatore rigoroso di segmentazioni di deliberazioni amministrative italiane.
Confronta i due dataset candidati con il testo di riferimento del PDF.

Valuta separatamente JSON e MARKDOWN da 0 a 100 su:
- marker_precision: correttezza delle etichette narrative e dispositive;
- marker_recall: marker narrativi e dispositivi presenti nel riferimento e recuperati;
- boundary_accuracy: inizio/fine dei blocchi, separazione di dispositivi autonomi
  e arresto prima di firme, pareri, certificazioni e testo a pie' di pagina;
- text_fidelity: fedeltà del testo, ordine e assenza di omissioni/allucinazioni;
- noise_exclusion: esclusione di certificati, firme, pubblicazione, pareri e
  allegati successivi al dispositivo e testo a pie' di pagina;
- overall_score: giudizio complessivo coerente con le metriche precedenti.

Vincoli metodologici obbligatori:
- si valutano sia la NARRATIVA sia i DISPOSITIVI PROPONE, DELIBERA e ATTESTA;
- più proposte o deliberazioni autonome devono restare in blocchi distinti
  con suffissi progressivi _1, _2, ...;
- una nuova votazione apre un secondo dispositivo soltanto quando introduce
  una decisione autonoma; le formule procedurali non costituiscono etichette;
- i punti numerati, alfabetici o puntati appartenenti al dispositivo devono
  essere conservati fino alla conclusione dell'elenco;
- firme, certificati, pubblicazione, pareri e allegati successivi,
  intestazioni e liste presenze devono essere esclusi: non considerarli
  omissioni e non ridurre recall o text_fidelity;
- VISTO/VISTA/VISTI/VISTE e le varianti di RICHIAMATO, se collocati dopo
  un dispositivo, ne delimitano la fine e non devono esservi accodati;
- DELIBERA, ATTESTA, PROPONE e DICHIARA possono presentarsi con lettere
  separate da spazi per effetto dell'OCR e devono essere considerati equivalenti;
- VISTO è un'etichetta canonica che comprende VISTO/VISTA/VISTI/VISTE: non
  penalizzare genere o numero grammaticale;
- i suffissi progressivi _1, _2, ... sono previsti dal dataset e non sono errori;
- VISTI_PARERI e ACQUISITI_PARERI sono etichette semantiche ammesse e distinte
  dai richiami normativi generici;
- SU_INVITO, TENUTO_PRESENTE, ADEMPIUTO, VISTO_E_PRESO_ATTO e
  RITENUTO_OPPORTUNO sono marker narrativi validi;
- le sezioni CANDIDATO JSON e CANDIDATO MARKDOWN sono rappresentazioni testuali
  dei blocchi estratti: non valutarne la sintassi come JSON o Markdown;
- concentra issues e punteggi su marker realmente mancanti/spuri, confini dei
  blocchi, ordine, omissioni interne alla narrativa e rumore effettivo.

Restituisci esclusivamente JSON valido con questa struttura:
{{"evaluations":[{{"format":"JSON","marker_precision":0,"marker_recall":0,
"boundary_accuracy":0,"text_fidelity":0,"noise_exclusion":0,"overall_score":0,
"issues":["..."],"notes":"..."}},{{"format":"MARKDOWN", ...}}]}}

DOCUMENTO: {id_delibera}
ESTRATTORE: {tool}
SORGENTE RIFERIMENTO: {reference_source}

TESTO DI RIFERIMENTO:
{reference}

CANDIDATO JSON:
{blocks_as_text(json_rows)}

CANDIDATO MARKDOWN:
{blocks_as_text(markdown_rows)}
"""
    response = nvidia_json(model, prompt, api_key)
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
        if (segmentation_dir / "00_COMPLETATA.txt").exists():
            raise FileNotFoundError(
                f"Dataset {tool} non presenti in {segmentation_dir}. "
                "Riesegui 01_estrazione_e_segmentazione.py dopo aver configurato l'estrattore."
            )
        time.sleep(5)
    raise TimeoutError(f"Dataset {tool} non disponibili entro {timeout} secondi")


def evaluate_tool(
    model: str,
    api_key: str,
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
                api_key,
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


def next_evaluation_dir(output_root: Path) -> Path:
    """Crea una nuova cartella numerata senza sovrascrivere valutazioni precedenti."""
    output_root.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in output_root.glob("VALUTAZIONE_LLM_*"):
        match = re.fullmatch(r"VALUTAZIONE_LLM_(\d+)", path.name, re.IGNORECASE)
        if path.is_dir() and match:
            numbers.append(int(match.group(1)))
    output_dir = output_root / f"VALUTAZIONE_LLM_{max(numbers, default=0) + 1}"
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
    parser = argparse.ArgumentParser(
        description="Valuta la segmentazione tramite API NVIDIA"
    )
    parser.add_argument(
        "--segmentazione",
        type=Path,
        help=(
            "Cartella SEGMENTAZIONE_N; default: ultima in "
            f"{DEFAULT_SEGMENTATION_ROOT}, con fallback sulle vecchie cartelle radice"
        ),
    )
    parser.add_argument("--input", type=Path, default=SCRIPT_DIR / "Input")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Radice separata per le valutazioni (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--api-key-env",
        default=DEFAULT_API_KEY_ENV,
        help=(
            "Variabile d'ambiente contenente la chiave API NVIDIA "
            f"(default: {DEFAULT_API_KEY_ENV})"
        ),
    )
    parser.add_argument("--concorrenza", type=int, default=1)
    parser.add_argument("--timeout-attesa", type=int, default=7200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"Chiave API NVIDIA mancante. Imposta la variabile "
            f"{args.api_key_env}, ad esempio in PowerShell:\n"
            f'  $env:{args.api_key_env}="<CHIAVE_NVIDIA>"'
        )
    segmentation_dir = (
        args.segmentazione.resolve()
        if args.segmentazione
        else latest_segmentation(DEFAULT_SEGMENTATION_ROOT)
    )
    if not 1 <= args.concorrenza <= 4:
        raise ValueError("--concorrenza deve essere compresa tra 1 e 4")

    output_dir = next_evaluation_dir(args.output.expanduser().resolve())
    print(f"Risultati destinati a: {output_dir}", flush=True)
    all_results: list[dict[str, Any]] = []
    # Ogni estrattore viene valutato appena i suoi JSON/MARKDOWN sono disponibili.
    for tool in EXTRACTOR_TOOLS:
        try:
            all_results.extend(
                evaluate_tool(
                    args.model,
                    api_key,
                    args.input.resolve(),
                    segmentation_dir,
                    tool,
                    args.concorrenza,
                    args.timeout_attesa,
                )
            )
        except (FileNotFoundError, TimeoutError) as exc:
            print(f"[{tool}] saltato: {exc}", flush=True)
            all_results.append({
                "id_delibera": "",
                "estrattore": tool,
                "format": "ERRORE",
                "notes": str(exc),
            })
        save_results(output_dir, all_results)

    print(f"Valutazione completata: {output_dir}")


if __name__ == "__main__":
    main()
