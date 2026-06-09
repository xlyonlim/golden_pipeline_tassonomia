import csv
import json
import time
import re
import shutil
import hashlib
import subprocess
import os
from pathlib import Path

import requests
import opendataloader_pdf
from pypdf import PdfReader, PdfWriter

# ============================================================
# DIRECTORY
# ============================================================

progetto_dir = Path(__file__).resolve().parent
base_dir = progetto_dir

input_dir = base_dir / "Input"
output_dir = base_dir / "Output" / "Golden" / "Golden128" / Path(__file__).stem

txt_dir = output_dir / "txt_opendataloader"
txt_ocr_dir = output_dir / "txt_opendataloader_ocr"
temp_pdf_dir = output_dir / "pdf_testo_completo"
testi_llm_dir = output_dir / "testi_completi_llm"

file_dataset_csv = None
file_dataset_jsonl = None
file_audit_csv = None
JAVA_OK_PER_OPENDATALOADER = None
JAVA_CMD_OPENDATALOADER = None

CSV_PRECEDENTI_DA_LEGGERE = [
    output_dir / "dataset_atti.csv",
    output_dir / "dataset_gemma3_4b.csv",
]

# ============================================================
# PARAMETRI
# ============================================================

NUM_PAGINE_DA_USARE = None
USA_FINESTRA_HEAD_TAIL = True
PAGINE_INIZIALI_DA_USARE = 5
PAGINE_FINALI_DA_USARE = 3
FORZA_OCR_SEMPRE = False
BLOCCA_CLASSIFICAZIONE_SE_OCR_FALLISCE = True

OCR_ENGINE = "rapidocr"      # auto | rapidocr | paddleocr
DISABILITA_PADDLE_ONEDNN = True

MAX_PAGINE_OCR = None        # None = tutte le pagine
OCR_DPI = 220
RIGENERA_CACHE_OCR = False
CONFRONTA_TESTO_NORMALE_CON_OCR = False
FORZA_OCR_SE_TESTO_NORMALE_SCADENTE = True
SOGLIA_TESTO_MINIMO_SENZA_OCR = 500
SOGLIA_QUALITA_TESTO_NORMALE = 75
SCEGLI_OCR_SE_MIGLIORA_QUALITA_DI = 5

CONTROLLO_DUPLICATI_VISIVO = True
DPI_DUPLICATI_VISIVO = 55

DELIMITATORE_CSV = ";"

CAMPI_CSV = [
    "id_atto",
    "file_name",
    "ocr",
    "testo_completo_llm",
    "golden_label",
]

CAMPI_AUDIT = [
    "id_atto",
    "file_name",
    "pdf_path_locale",
    "pdf_link",
    "txt_path_locale",
    "txt_link",
    "testo_llm_path_locale",
    "testo_llm_link",
    "ocr",
    "errore_tecnico",
    "fallback_locale",
    "modello",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "costo_input",
    "costo_output",
    "costo_totale",
    "avg_input_tokens_per_chunk",
    "avg_output_tokens_per_chunk",
    "avg_total_tokens_per_chunk",
    "num_chunk_llm",
    "timestamp",
]

PAROLE_CHIAVE_CLASSIFICAZIONE = [
    "delibera",
    "deliberazione",
    "determina",
    "determinazione",
    "decreto",
    "ordinanza",
    "regolamento",
    "statuto",
    "accordo",
    "convenzione",
    "oggetto",
    "visto",
    "considerato",
    "ritenuto",
]

PROVIDER_LLM = "ollama"
OLLAMA_BASE_URL = "http://localhost:11434"
MODELLO_OLLAMA_DEFAULT = "gemma3:4b"
MODELLO_OLLAMA_ATTIVO = None
MAX_TENTATIVI_OLLAMA = 1
MAX_TENTATIVI_TIMEOUT_OLLAMA = 1
TIMEOUT_OLLAMA = 180
MAX_TOKENS_RISPOSTA = 350
PAUSA_TRA_RICHIESTE = 0
OLLAMA_NUM_CTX = 2048

CARATTERI_PER_CHUNK_LLM = 1000
SOVRAPPOSIZIONE_CHUNK = 0
MAX_CARATTERI_INPUT_LLM = 1000
MAX_CARATTERI_TESTO_COMPLETO_LLM = 2500
MAX_CHUNK_LLM = 1
USA_FALLBACK_LOCALE_SE_LLM_FALLISCE = True

COSTO_INPUT_PER_1K = 0.0
COSTO_OUTPUT_PER_1K = 0.0

RIELABORA_TUTTI = False
MAX_PDF_DA_PROCESSARE = 40  # None = tutti i PDF; per i test lasciare un campione
ID_ATTI_DA_RIELABORARE = set()
FILE_DA_RIELABORARE = set()

# ============================================================
# PROMPT
# ============================================================

PROMPT_SISTEMA_PULIZIA = """
Produci un riassunto breve e informativo di un atto della Pubblica Amministrazione italiana.
Non classificare e non assegnare etichette.
Mantieni forma dell'atto, ente, numero/data, oggetto, premesse essenziali e dispositivo.
Elimina firme, formule tecniche, protocolli, dettagli contabili non essenziali e ripetizioni.
Massimo 6 frasi. Solo testo italiano, senza JSON, markdown, commenti o prefissi.
"""# ============================================================
# UTILITÃ€
# ============================================================

def crea_file_link(path_file):
    try:
        return Path(path_file).resolve().as_uri()
    except Exception:
        return str(Path(path_file).resolve())

def slug_modello_per_nome_file(nome_modello):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(nome_modello or "modello_non_selezionato"))
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "modello_non_selezionato"

def configura_file_output_per_modello(nome_modello):
    global file_dataset_csv, file_dataset_jsonl, file_audit_csv
    slug = slug_modello_per_nome_file(nome_modello)
    file_dataset_csv = output_dir / f"dataset_atti_{slug}_test_prompt_riassunto_head5_tail3.csv"
    file_dataset_jsonl = output_dir / f"dataset_atti_{slug}_test_prompt_riassunto_head5_tail3.jsonl"
    file_audit_csv = output_dir / f"audit_log_{slug}_test_prompt_riassunto_head5_tail3.csv"

def normalizza_ocr_valore(valore):
    testo = str(valore or "").strip().lower()
    return "1" if testo in {"1", "si", "sÃ¬", "yes", "true", "ocr", "usato"} else "0"

def normalizza_testo_per_cella(testo):
    if not testo:
        return ""
    testo = str(testo).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", testo).strip()

def pre_pulisci_testo_per_llm(testo):
    if not testo:
        return ""
    testo = str(testo).replace("\r", "\n").replace("\t", " ")
    righe = []
    for riga in testo.splitlines():
        riga = re.sub(r"\s+", " ", riga).strip()
        if not riga:
            continue
        lettere = len(re.findall(r"[^\W\d_]", riga))
        simboli = len(re.findall(r"[^\w\s]", riga))
        if lettere < 3 and simboli > lettere:
            continue
        riga = re.sub(r"[|_]{3,}", " ", riga)
        riga = re.sub(r"\.{5,}", " ", riga)
        riga = re.sub(r"[-=]{5,}", " ", riga)
        riga = re.sub(r"\s+", " ", riga).strip()
        if riga:
            righe.append(riga)
    return "\n".join(righe).strip()

def spezza_testo_in_chunk(testo, max_caratteri=CARATTERI_PER_CHUNK_LLM, overlap=SOVRAPPOSIZIONE_CHUNK):
    testo = testo.strip()
    if len(testo) <= max_caratteri:
        return [testo]
    chunks = []
    start = 0
    n = len(testo)
    while start < n:
        end = min(start + max_caratteri, n)
        if end < n:
            finestra = testo[start:end]
            taglio = max(finestra.rfind("\n"), finestra.rfind(". "), finestra.rfind("; "))
            if taglio > max_caratteri * 0.65:
                end = start + taglio + 1
        chunks.append(testo[start:end].strip())
        if end >= n:
            break
        start = max(0, end - overlap)
    return [c for c in chunks if c]

def tronca_a_unita_logica(testo, max_caratteri):
    if not testo or len(testo) <= max_caratteri:
        return testo
    finestra = testo[:max_caratteri]
    candidati = [
        finestra.rfind(". "), finestra.rfind(";\n"), finestra.rfind(".\n"),
        finestra.rfind("! "), finestra.rfind("? "), finestra.rfind("\n\n"),
        finestra.rfind("\n"), finestra.rfind("; "),
    ]
    taglio = max(candidati)
    if taglio < int(max_caratteri * 0.75):
        taglio = finestra.rfind(" ")
    if taglio <= 0:
        taglio = max_caratteri
    testo_troncato = testo[:taglio + 1].strip()
    return re.sub(r"[,;:\-\s]+$", ".", testo_troncato).strip()

# ============================================================
# LLM / TOKEN / COSTI
# ============================================================

def conta_token_stimati(testo):
    if not testo:
        return 0
    return max(1, int(len(str(testo)) / 4))

def chiama_ollama(prompt_sistema, prompt_utente):
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": MODELLO_OLLAMA_ATTIVO,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_utente},
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": MAX_TOKENS_RISPOSTA,
            "num_ctx": OLLAMA_NUM_CTX,
        },
    }
    response = requests.post(url, json=payload, timeout=TIMEOUT_OLLAMA)
    response.raise_for_status()
    data = response.json()
    contenuto = (data.get("message") or {}).get("content", "")
    input_tokens = data.get("prompt_eval_count")
    output_tokens = data.get("eval_count")
    total_tokens = (
        int(input_tokens) + int(output_tokens)
        if input_tokens is not None and output_tokens is not None
        else None
    )
    return ripulisci_output_llm(contenuto), input_tokens, output_tokens, total_tokens

def ripulisci_output_llm(testo):
    if not testo:
        return ""
    testo = str(testo).strip()
    testo = re.sub(r"^```(?:text|txt)?", "", testo, flags=re.IGNORECASE).strip()
    testo = re.sub(r"```$", "", testo).strip()
    testo = testo.replace("\r", "\n")
    testo = re.sub(r"\n{3,}", "\n\n", testo)
    testo = re.sub(r"[ \t]+", " ", testo)
    return testo.strip()

def stats_llm_parziali(num_chunk, tot_in=0, tot_out=0, tot_total=0):
    return {
        "num_chunk_llm": num_chunk,
        "fallback_locale": 0,
        "input_tokens": tot_in,
        "output_tokens": tot_out,
        "total_tokens": tot_total,
        "costo_input": round(tot_in / 1000 * COSTO_INPUT_PER_1K, 6),
        "costo_output": round(tot_out / 1000 * COSTO_OUTPUT_PER_1K, 6),
        "costo_totale": round((tot_in / 1000 * COSTO_INPUT_PER_1K) + (tot_out / 1000 * COSTO_OUTPUT_PER_1K), 6),
        "avg_input_tokens_per_chunk": round(tot_in / max(num_chunk, 1), 2),
        "avg_output_tokens_per_chunk": round(tot_out / max(num_chunk, 1), 2),
        "avg_total_tokens_per_chunk": round(tot_total / max(num_chunk, 1), 2),
    }

def genera_testo_completo_pulito_con_ollama(testo_atto):
    testo_atto = pre_pulisci_testo_per_llm(testo_atto)
    testo_atto = seleziona_blocchi_utili_per_llm(testo_atto, MAX_CARATTERI_INPUT_LLM)

    if not testo_atto.strip():
        return "", "Testo vuoto dopo pre-pulizia/selezione", stats_llm_parziali(0)

    chunks = spezza_testo_in_chunk(testo_atto)
    if MAX_CHUNK_LLM is not None:
        chunks = chunks[:MAX_CHUNK_LLM]
    testi_puliti = []
    tot_in = tot_out = tot_total = 0

    for idx, chunk in enumerate(chunks, 1):
        print(f"Pulizia LLM chunk {idx}/{len(chunks)} con Ollama...")
        user_prompt = f"""
Pulisci conservativamente il testo OCR qui sotto: rimuovi solo rumore, duplicazioni, firme e parti tecniche superflue.
Non riassumere, non classificare, non riscrivere piÃ¹ del necessario.

BLOCCO {idx}/{len(chunks)}:
{chunk}
"""
        ultimo_errore = ""
        for tentativo in range(1, MAX_TENTATIVI_OLLAMA + 1):
            try:
                contenuto, input_tokens, output_tokens, total_tokens = chiama_ollama(PROMPT_SISTEMA_PULIZIA, user_prompt)
                if not contenuto:
                    ultimo_errore = "Risposta vuota da Ollama"
                    if tentativo < MAX_TENTATIVI_OLLAMA:
                        time.sleep(5 * tentativo)
                        continue
                    return "", ultimo_errore, stats_llm_parziali(len(chunks), tot_in, tot_out, tot_total)

                if contenuto.strip() == "ERRORE_LINGUA_NON_ITALIANA":
                    return "", "ERRORE_LINGUA_NON_ITALIANA", stats_llm_parziali(len(chunks), tot_in, tot_out, tot_total)

                testi_puliti.append(contenuto)
                input_tokens_chunk = int(input_tokens or conta_token_stimati(PROMPT_SISTEMA_PULIZIA + user_prompt))
                output_tokens_chunk = int(output_tokens or conta_token_stimati(contenuto))
                total_tokens_chunk = int(total_tokens or (input_tokens_chunk + output_tokens_chunk))
                tot_in += input_tokens_chunk
                tot_out += output_tokens_chunk
                tot_total += total_tokens_chunk
                break
            except requests.exceptions.Timeout:
                ultimo_errore = f"Timeout Ollama nel chunk {idx} dopo {TIMEOUT_OLLAMA} secondi"
                print(ultimo_errore)
                if tentativo < min(MAX_TENTATIVI_OLLAMA, MAX_TENTATIVI_TIMEOUT_OLLAMA):
                    time.sleep(5 * tentativo)
                    continue
                if USA_FALLBACK_LOCALE_SE_LLM_FALLISCE:
                    print("Uso fallback locale: testo pre-pulito senza LLM.")
                    testo_fallback = tronca_a_unita_logica(testo_atto, MAX_CARATTERI_TESTO_COMPLETO_LLM)
                    stats = stats_llm_parziali(len(chunks), tot_in, tot_out, tot_total)
                    stats["fallback_locale"] = 1
                    return testo_fallback, "", stats
                return "", ultimo_errore, stats_llm_parziali(len(chunks), tot_in, tot_out, tot_total)
            except KeyboardInterrupt:
                print("\nInterruzione manuale durante la chiamata a Ollama.")
                raise
            except Exception as e:
                ultimo_errore = f"Errore Ollama nel chunk {idx}: {e}"
                print(ultimo_errore)
                if tentativo < MAX_TENTATIVI_OLLAMA:
                    time.sleep(5 * tentativo)
                    continue
                if USA_FALLBACK_LOCALE_SE_LLM_FALLISCE:
                    print("Uso fallback locale: testo pre-pulito senza LLM.")
                    testo_fallback = tronca_a_unita_logica(testo_atto, MAX_CARATTERI_TESTO_COMPLETO_LLM)
                    stats = stats_llm_parziali(len(chunks), tot_in, tot_out, tot_total)
                    stats["fallback_locale"] = 1
                    return testo_fallback, "", stats
                return "", ultimo_errore, stats_llm_parziali(len(chunks), tot_in, tot_out, tot_total)

    testo_finale = ripulisci_output_llm("\n\n".join(testi_puliti))
    if MAX_CARATTERI_TESTO_COMPLETO_LLM is not None and len(testo_finale) > MAX_CARATTERI_TESTO_COMPLETO_LLM:
        testo_finale = tronca_a_unita_logica(testo_finale, MAX_CARATTERI_TESTO_COMPLETO_LLM)

    num_chunk = len(chunks)
    return testo_finale, "", stats_llm_parziali(num_chunk, tot_in, tot_out, tot_total)

def salva_testo_llm_txt(id_atto, file_name, testo_llm):
    stem = Path(file_name).stem.replace(" ", "_")
    path = testi_llm_dir / f"{id_atto}_{stem}_testo_completo_llm.txt"
    path.write_text(testo_llm, encoding="utf-8")
    return path

# ============================================================
# AUDIT
# ============================================================

def crea_record_audit(record, txt_path=None, errore_tecnico="", stats=None):
    stats = stats or {}
    if txt_path:
        txt_path_locale = str(Path(txt_path).resolve())
        txt_link = crea_file_link(txt_path)
    else:
        txt_path_locale = ""
        txt_link = ""

    return {
        "id_atto": record.get("id_atto", ""),
        "file_name": record.get("file_name", ""),
        "pdf_path_locale": record.get("pdf_path_locale", ""),
        "pdf_link": record.get("pdf_link", ""),
        "txt_path_locale": txt_path_locale,
        "txt_link": txt_link,
        "testo_llm_path_locale": record.get("testo_llm_path_locale", ""),
        "testo_llm_link": record.get("testo_llm_link", ""),
        "ocr": normalizza_ocr_valore(record.get("ocr", "")),
        "errore_tecnico": errore_tecnico,
        "fallback_locale": int(stats.get("fallback_locale", 0) or 0),
        "modello": MODELLO_OLLAMA_ATTIVO or "OLLAMA_NON_SELEZIONATO",
        "input_tokens": stats.get("input_tokens", 0),
        "output_tokens": stats.get("output_tokens", 0),
        "total_tokens": stats.get("total_tokens", 0),
        "costo_input": stats.get("costo_input", 0.0),
        "costo_output": stats.get("costo_output", 0.0),
        "costo_totale": stats.get("costo_totale", 0.0),
        "avg_input_tokens_per_chunk": stats.get("avg_input_tokens_per_chunk", 0.0),
        "avg_output_tokens_per_chunk": stats.get("avg_output_tokens_per_chunk", 0.0),
        "avg_total_tokens_per_chunk": stats.get("avg_total_tokens_per_chunk", 0.0),
        "num_chunk_llm": stats.get("num_chunk_llm", 0),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def scegli_modello_ollama():
    print("\nUso modello Ollama fisso:")
    print(f"- {MODELLO_OLLAMA_DEFAULT}\n")
    return MODELLO_OLLAMA_DEFAULT


def prepara_cartelle():
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)
    txt_ocr_dir.mkdir(parents=True, exist_ok=True)
    temp_pdf_dir.mkdir(parents=True, exist_ok=True)
    testi_llm_dir.mkdir(parents=True, exist_ok=True)


def estrai_numero_da_nome(nome_file):
    nome = Path(nome_file).stem.lower().replace(" ", "")
    match = re.search(r"(\d+)", nome)
    return int(match.group(1)) if match else None


def natural_sort_key(path_or_string):
    if isinstance(path_or_string, Path):
        numero = estrai_numero_da_nome(path_or_string.name)
        testo = path_or_string.stem.lower().replace(" ", "")
    else:
        numero = estrai_numero_da_nome(str(path_or_string))
        testo = str(path_or_string).lower().replace(" ", "")
    return (0, numero, testo) if numero is not None else (1, testo)


def trova_pdf():
    return sorted(input_dir.glob("*.pdf"), key=natural_sort_key)


def numero_atto_da_nome_file(pdf_path):
    match = re.match(r"^atto\s*[_\-\s]*([0-9]+)\s*$", Path(pdf_path).stem.strip().lower())
    return int(match.group(1)) if match else None


def verifica_numerazione_pdf(pdf_files):
    numeri = {}
    senza_numero = []
    for pdf_path in pdf_files:
        numero = numero_atto_da_nome_file(pdf_path)
        if numero is None:
            senza_numero.append(pdf_path.name)
            continue
        numeri.setdefault(numero, []).append(pdf_path.name)

    duplicati = {n: nomi for n, nomi in numeri.items() if len(nomi) > 1}
    if duplicati:
        dettagli = ["atto_{}: {}".format(n, ", ".join(nomi)) for n, nomi in sorted(duplicati.items())]
        raise RuntimeError("Controllo numerazione fallito: numeri duplicati.\n" + "\n".join(dettagli))

    if numeri:
        massimo = max(numeri)
        mancanti = [n for n in range(1, massimo + 1) if n not in numeri]
        if mancanti:
            raise RuntimeError("Controllo numerazione fallito: mancano questi file:\n" + ", ".join(f"atto_{n}" for n in mancanti))
        print(f"Controllo numerazione OK: presenti atto_1 ... atto_{massimo}.")

    if senza_numero:
        print("Nota: questi PDF sono esclusi dal controllo sequenziale:")
        for nome in senza_numero:
            print(f"- {nome}")


def calcola_sha256_file(path_file, blocco=1024 * 1024):
    sha = hashlib.sha256()
    with Path(path_file).open("rb") as f:
        while True:
            dati = f.read(blocco)
            if not dati:
                break
            sha.update(dati)
    return sha.hexdigest()


def verifica_duplicati_contenuto_pdf(pdf_files):
    gruppi_hash = {}
    for pdf_path in pdf_files:
        gruppi_hash.setdefault(calcola_sha256_file(pdf_path), []).append(pdf_path.name)
    duplicati = [nomi for nomi in gruppi_hash.values() if len(nomi) > 1]
    if duplicati:
        dettagli = ["- " + ", ".join(sorted(gruppo)) for gruppo in duplicati]
        raise RuntimeError("Controllo duplicati fallito: PDF identici.\n" + "\n".join(dettagli))
    print("Controllo duplicati OK.")


def crea_id_atto(pdf_path, indice_progressivo, id_usati):
    numero = estrai_numero_da_nome(pdf_path.name) or indice_progressivo
    id_base = f"{numero:04d}"
    id_finale = id_base
    contatore = 2
    while id_finale in id_usati:
        id_finale = f"{id_base}_{contatore}"
        contatore += 1
    id_usati.add(id_finale)
    return id_finale


def crea_mappa_pdf_id(pdf_files):
    id_usati = set()
    return {pdf_path: crea_id_atto(pdf_path, i, id_usati) for i, pdf_path in enumerate(pdf_files, 1)}


def normalizza_id_da_csv(row):
    id_atto = str(row.get("id_atto", "")).strip()
    if id_atto:
        match = re.search(r"(\d+)", id_atto)
        if match:
            return f"{int(match.group(1)):04d}"
    file_name = str(row.get("file_name", "") or row.get("file_origine", "")).strip()
    numero = estrai_numero_da_nome(file_name) if file_name else None
    return f"{numero:04d}" if numero is not None else ""


def carica_dataset_csv():
    records = {}
    possibili_csv = []
    if file_dataset_csv is not None:
        possibili_csv.append(file_dataset_csv)
    possibili_csv.extend(CSV_PRECEDENTI_DA_LEGGERE)
    csv_da_leggere = next((p for p in possibili_csv if p and p.exists()), None)
    if csv_da_leggere is None:
        return records

    try:
        with csv_da_leggere.open("r", encoding="utf-8-sig", newline="") as csvfile:
            reader = csv.DictReader(csvfile, delimiter=DELIMITATORE_CSV)
            for row in reader:
                id_atto = normalizza_id_da_csv(row)
                if not id_atto:
                    continue
                records[id_atto] = {
                    "id_atto": id_atto,
                    "file_name": row.get("file_name", "").strip() or row.get("file_origine", "").strip(),
                    "ocr": normalizza_ocr_valore(row.get("ocr", "")),
                    "testo_completo_llm": row.get("testo_completo_llm", "").strip(),
                    "golden_label": row.get("golden_label", "").strip() or row.get("tipo", "").strip(),
                    "errore": row.get("errore", "").strip(),
                }
    except Exception as e:
        print(f"Errore nella lettura del CSV esistente: {e}")
    return records


def ordina_ids(records, ordine_ids):
    ids_ordinati = [id_atto for id_atto in ordine_ids if id_atto in records]
    ids_extra = [id_atto for id_atto in records if id_atto not in ids_ordinati]

    def key_id(x):
        match = re.search(r"\d+", x)
        return int(match.group()) if match else 999999

    return ids_ordinati + sorted(ids_extra, key=key_id)


def salva_dataset_csv(records, ordine_ids):
    ids_finali = ordina_ids(records, ordine_ids)
    with file_dataset_csv.open("w", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CAMPI_CSV, delimiter=DELIMITATORE_CSV, extrasaction="ignore")
        writer.writeheader()
        for id_atto in ids_finali:
            record = {campo: records[id_atto].get(campo, "") for campo in CAMPI_CSV}
            record["testo_completo_llm"] = normalizza_testo_per_cella(record.get("testo_completo_llm", ""))
            writer.writerow(record)


def salva_dataset_jsonl(records, ordine_ids):
    ids_finali = ordina_ids(records, ordine_ids)
    with file_dataset_jsonl.open("w", encoding="utf-8") as jsonlfile:
        for id_atto in ids_finali:
            record = {campo: records[id_atto].get(campo, "") for campo in CAMPI_CSV}
            record["testo_completo_llm"] = normalizza_testo_per_cella(record.get("testo_completo_llm", ""))
            jsonlfile.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_audit_log(record):
    file_esiste = file_audit_csv.exists()
    with file_audit_csv.open("a", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CAMPI_AUDIT, delimiter=DELIMITATORE_CSV, extrasaction="ignore")
        if not file_esiste:
            writer.writeheader()
        writer.writerow(record)


def record_valido(record):
    return bool(record and str(record.get("testo_completo_llm", "")).strip())


def crea_metadati_file(pdf_path):
    return {"pdf_path_locale": str(pdf_path.resolve()), "pdf_link": crea_file_link(pdf_path)}


def crea_pdf_prime_pagine(pdf_path, num_pagine):
    if not USA_FINESTRA_HEAD_TAIL:
        if num_pagine is None:
            return pdf_path
        temp_pdf_dir.mkdir(parents=True, exist_ok=True)
        temp_pdf_path = temp_pdf_dir / f"{pdf_path.stem}_prime_{num_pagine}_pagine.pdf"
        if temp_pdf_path.exists():
            return temp_pdf_path
        try:
            reader = PdfReader(str(pdf_path))
            writer = PdfWriter()
            for i in range(min(num_pagine, len(reader.pages))):
                writer.add_page(reader.pages[i])
            with temp_pdf_path.open("wb") as f:
                writer.write(f)
            return temp_pdf_path
        except Exception as e:
            print(f"Errore nella creazione del PDF temporaneo per {pdf_path.name}: {e}")
            return None

    try:
        reader = PdfReader(str(pdf_path))
        totale_pagine = len(reader.pages)
        pagine_iniziali = max(0, int(PAGINE_INIZIALI_DA_USARE or 0))
        pagine_finali = max(0, int(PAGINE_FINALI_DA_USARE or 0))
        soglia_documento_intero = pagine_iniziali + pagine_finali

        if totale_pagine <= soglia_documento_intero:
            return pdf_path

        indici_iniziali = list(range(min(pagine_iniziali, totale_pagine)))
        inizio_finali = max(totale_pagine - pagine_finali, len(indici_iniziali))
        indici_finali = list(range(inizio_finali, totale_pagine))
        indici_da_usare = indici_iniziali + indici_finali

        temp_pdf_dir.mkdir(parents=True, exist_ok=True)
        temp_pdf_path = temp_pdf_dir / (
            f"{pdf_path.stem}_head_{pagine_iniziali}_tail_{pagine_finali}_pagine.pdf"
        )
        if temp_pdf_path.exists():
            return temp_pdf_path

        writer = PdfWriter()
        for indice in indici_da_usare:
            writer.add_page(reader.pages[indice])
        with temp_pdf_path.open("wb") as f:
            writer.write(f)
        print(
            f"Uso finestra head-tail per {pdf_path.name}: "
            f"prime {pagine_iniziali} + ultime {pagine_finali} "
            f"({len(indici_da_usare)}/{totale_pagine} pagine)."
        )
        return temp_pdf_path
    except Exception as e:
        print(f"Errore nella creazione del PDF head-tail per {pdf_path.name}: {e}")
        return None
def trova_txt_generato(pdf_path, cartella_output):
    stem = pdf_path.stem
    possibile = list(cartella_output.rglob(f"{stem}.txt"))
    if possibile:
        return possibile[0]
    stem_pulito = stem.lower().replace(" ", "")
    for txt_file in cartella_output.rglob("*.txt"):
        txt_stem_pulito = txt_file.stem.lower().replace(" ", "")
        if stem_pulito == txt_stem_pulito or stem_pulito in txt_stem_pulito:
            return txt_file
    return None


def candidati_java_windows():
    candidati = []
    java_path = shutil.which("java")
    if java_path:
        candidati.append(Path(java_path))

    cartelle_base = [
        Path(r"C:\Program Files\Eclipse Adoptium"),
        Path(r"C:\Program Files\Java"),
        Path(r"C:\Program Files\Microsoft"),
        Path(r"C:\Program Files\Amazon Corretto"),
    ]
    for cartella in cartelle_base:
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


def versione_java_principale(java_cmd=None):
    comando = str(java_cmd or shutil.which("java") or "java")
    if java_cmd is None and shutil.which("java") is None:
        return None, "java non trovato nel PATH"
    try:
        result = subprocess.run(
            [comando, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as e:
        return None, f"impossibile leggere la versione Java: {e}"

    testo_versione = f"{result.stdout}\n{result.stderr}"
    match = re.search(r'version "([^"]+)"', testo_versione)
    if not match:
        return None, "versione Java non riconosciuta"

    versione = match.group(1)
    if versione.startswith("1."):
        parti = versione.split(".")
        if len(parti) > 1 and parti[1].isdigit():
            return int(parti[1]), versione
        return None, versione

    match_major = re.match(r"(\d+)", versione)
    if match_major:
        return int(match_major.group(1)), versione
    return None, versione


def configura_java_per_opendataloader(java_cmd):
    java_cmd = Path(java_cmd).resolve()
    java_bin = java_cmd.parent
    java_home = java_bin.parent
    os.environ["JAVA_HOME"] = str(java_home)

    path_attuale = os.environ.get("PATH", "")
    parti_path = [p for p in path_attuale.split(os.pathsep) if p]
    parti_path = [p for p in parti_path if Path(p).resolve() != java_bin]
    os.environ["PATH"] = os.pathsep.join([str(java_bin), *parti_path])


def java_compatibile_con_opendataloader():
    global JAVA_OK_PER_OPENDATALOADER, JAVA_CMD_OPENDATALOADER
    if JAVA_OK_PER_OPENDATALOADER is not None:
        return JAVA_OK_PER_OPENDATALOADER

    ultimo_dettaglio = "java non trovato"
    for candidato in candidati_java_windows():
        major, dettaglio = versione_java_principale(candidato)
        ultimo_dettaglio = dettaglio
        if major is not None and major >= 11:
            configura_java_per_opendataloader(candidato)
            JAVA_CMD_OPENDATALOADER = str(Path(candidato).resolve())
            print(f"OpenDataLoader usera Java: {JAVA_CMD_OPENDATALOADER} ({dettaglio})")
            JAVA_OK_PER_OPENDATALOADER = True
            return True

    print(
        "OpenDataLoader saltato: serve Java 11 o superiore "
        f"(versione rilevata: {ultimo_dettaglio})."
    )
    JAVA_OK_PER_OPENDATALOADER = False
    return False


def converti_pdf_opendataloader_normale(pdf_prime_pagine):
    if not java_compatibile_con_opendataloader():
        return None

    txt_esistente = trova_txt_generato(pdf_prime_pagine, txt_dir)
    if txt_esistente is not None:
        return txt_esistente
    try:
        opendataloader_pdf.convert(input_path=[str(pdf_prime_pagine)], output_dir=str(txt_dir), format="text", quiet=True)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"Errore OpenDataLoader normale su {pdf_prime_pagine.name}: {e}")
        return None
    return trova_txt_generato(pdf_prime_pagine, txt_dir)


def leggi_txt(txt_path):
    try:
        return Path(txt_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"Errore lettura TXT {txt_path}: {e}")
        return ""


def punteggio_qualita_testo(testo):
    if not testo:
        return 0

    testo_pulito = re.sub(r"\s+", " ", str(testo)).strip()
    if not testo_pulito:
        return 0

    lunghezza = len(testo_pulito)
    lettere = len(re.findall(r"[^\W\d_]", testo_pulito))
    cifre = len(re.findall(r"\d", testo_pulito))
    simboli = len(re.findall(r"[^\w\s]", testo_pulito))

    rapporto_lettere = lettere / max(len(testo_pulito), 1)
    rapporto_simboli = simboli / max(len(testo_pulito), 1)

    parole_chiave_presenti = sum(
        1 for parola in PAROLE_CHIAVE_CLASSIFICAZIONE
        if parola in testo_pulito.lower()
    )

    score = 0
    score += min(40, lunghezza // 80)
    score += min(25, int(rapporto_lettere * 100 / 2.5))
    score -= min(20, int(rapporto_simboli * 100))
    score += min(20, parole_chiave_presenti * 4)
    if cifre > 0:
        score += 5

    return max(0, min(100, score))


def testo_sufficiente_senza_ocr(testo):
    if not testo:
        return False
    testo_pulito = re.sub(r"\s+", " ", testo).strip()
    if len(testo_pulito) < SOGLIA_TESTO_MINIMO_SENZA_OCR:
        return False
    return punteggio_qualita_testo(testo_pulito) >= SOGLIA_QUALITA_TESTO_NORMALE


def blocco_sicuramente_rumoroso(blocco):
    b = blocco.lower().strip()
    if not b:
        return True

    pattern_rumore = [
        r"firma digitale",
        r"documento firmato digitalmente",
        r"certificat[oa] di pubblicazione",
        r"relata di pubblicazione",
        r"attestazione di esecutivit",
        r"copia conforme",
        r"hash",
        r"impronta",
        r"segnatura",
        r"protocollo informatico",
        r"codice verific",
        r"pagina \d+ di \d+",
    ]

    if any(re.search(p, b) for p in pattern_rumore):
        return True

    lettere = len(re.findall(r"[^\W\d_]", b))
    simboli = len(re.findall(r"[^\w\s]", b))
    return lettere < 8 and simboli > lettere


def seleziona_blocchi_utili_per_llm(testo, max_caratteri=MAX_CARATTERI_INPUT_LLM):
    if not testo:
        return ""

    testo = str(testo).replace("\r", "\n")
    blocchi = [b.strip() for b in re.split(r"\n\s*\n+", testo) if b.strip()]
    utili = []

    pattern_utili = [
        r"\boggetto\b",
        r"\bpremess[oa]\b",
        r"\bvisto\b",
        r"\bconsiderato\b",
        r"\britenuto\b",
        r"\bdelibera\b",
        r"\bdeliberazione\b",
        r"\bdetermina\b",
        r"\bdeterminazione\b",
        r"\bdecreto\b",
        r"\bordinanza\b",
        r"\bdecreta\b",
        r"\bordina\b",
        r"\bart\.\b",
        r"\barticolo\b",
        r"\bgiunta comunale\b",
        r"\bconsiglio comunale\b",
        r"\bregolamento\b",
        r"\bstatuto\b",
        r"\bente\b",
        r"\bnumero\b",
        r"\bdata\b",
    ]

    for i, blocco in enumerate(blocchi):
        b = re.sub(r"\s+", " ", blocco).strip()
        if not b or blocco_sicuramente_rumoroso(b):
            continue

        keep = False

        if i < 4:
            keep = True

        if any(re.search(p, b.lower()) for p in pattern_utili):
            keep = True

        if len(b) > 350 and punteggio_qualita_testo(b) >= SOGLIA_QUALITA_TESTO_NORMALE:
            keep = True

        if keep:
            utili.append(blocco)

    if not utili:
        utili = blocchi[:8]

    testo_selezionato = "\n\n".join(utili).strip()
    if max_caratteri is not None and len(testo_selezionato) > max_caratteri:
        testo_selezionato = tronca_a_unita_logica(testo_selezionato, max_caratteri)

    return testo_selezionato


def renderizza_pdf_in_immagini(pdf_path):
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


def converti_pdf_ocr_rapidocr(pdf_prime_pagine):
    txt_output = txt_ocr_dir / f"{pdf_prime_pagine.stem}_rapidocr.txt"
    if txt_output.exists() and not RIGENERA_CACHE_OCR:
        return txt_output

    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        print(f"RapidOCR non disponibile: {e}")
        return None

    try:
        print(f"OCR RapidOCR: {pdf_prime_pagine.name}")
        ocr = RapidOCR()
        testi = []

        for indice, immagine in enumerate(renderizza_pdf_in_immagini(pdf_prime_pagine), 1):
            print(f"  RapidOCR pagina {indice}...")
            result, _ = ocr(np.array(immagine))
            if result:
                righe = []
                for r in result:
                    if len(r) > 1 and str(r[1]).strip():
                        righe.append(str(r[1]).strip())
                if righe:
                    testi.append("\n".join(righe))

        testo_finale = "\n\n".join(t for t in testi if t).strip()
        if not testo_finale:
            return None

        txt_ocr_dir.mkdir(parents=True, exist_ok=True)
        txt_output.write_text(testo_finale, encoding="utf-8")
        return txt_output

    except Exception as e:
        print(f"Errore OCR RapidOCR su {pdf_prime_pagine.name}: {e}")
        return None


def converti_pdf_ocr_paddleocr(pdf_prime_pagine):
    txt_output = txt_ocr_dir / f"{pdf_prime_pagine.stem}_paddleocr.txt"
    if txt_output.exists() and not RIGENERA_CACHE_OCR:
        return txt_output

    try:
        if DISABILITA_PADDLE_ONEDNN:
            os.environ["FLAGS_use_mkldnn"] = "0"
            os.environ["FLAGS_use_onednn"] = "0"
            os.environ["FLAGS_tracer_onednn_ops_on"] = ""
            os.environ["FLAGS_tracer_onednn_ops_off"] = "all"

        import numpy as np
        from paddleocr import PaddleOCR
    except ImportError as e:
        print(f"PaddleOCR non disponibile: {e}")
        return None

    try:
        print(f"OCR PaddleOCR: {pdf_prime_pagine.name}")
        ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
            lang="it",
        )
        testi = []

        for indice, immagine in enumerate(renderizza_pdf_in_immagini(pdf_prime_pagine), 1):
            print(f"  PaddleOCR pagina {indice}...")
            if hasattr(ocr, "predict"):
                result = ocr.predict(np.array(immagine))
            else:
                result = ocr.ocr(np.array(immagine))
            righe = []

            for pagina in result or []:
                if hasattr(pagina, "get"):
                    for testo in pagina.get("rec_texts", []) or []:
                        testo = str(testo).strip()
                        if testo:
                            righe.append(testo)
                    continue

                for item in pagina or []:
                    if len(item) >= 2 and item[1] and str(item[1][0]).strip():
                        righe.append(str(item[1][0]).strip())

            if righe:
                testi.append("\n".join(righe))

        testo_finale = "\n\n".join(t for t in testi if t).strip()
        if not testo_finale:
            return None

        txt_ocr_dir.mkdir(parents=True, exist_ok=True)
        txt_output.write_text(testo_finale, encoding="utf-8")
        return txt_output

    except Exception as e:
        print(f"Errore OCR PaddleOCR su {pdf_prime_pagine.name}: {e}")
        if DISABILITA_PADDLE_ONEDNN:
            print("PaddleOCR eseguito con oneDNN/MKLDNN disabilitato.")
        return None


def converti_pdf_opendataloader_ocr(pdf_prime_pagine):
    engine = (OCR_ENGINE or "").lower().strip()

    if engine == "rapidocr":
        return converti_pdf_ocr_rapidocr(pdf_prime_pagine)

    if engine == "paddleocr":
        return converti_pdf_ocr_paddleocr(pdf_prime_pagine)

    if engine == "auto":
        txt_rapid = converti_pdf_ocr_rapidocr(pdf_prime_pagine)
        if txt_rapid is not None and not Path(txt_rapid).exists():
            txt_rapid = None
        testo_rapid = leggi_txt(txt_rapid) if txt_rapid else ""
        score_rapid = punteggio_qualita_testo(testo_rapid)

        if score_rapid >= SOGLIA_QUALITA_TESTO_NORMALE:
            return txt_rapid

        txt_paddle = converti_pdf_ocr_paddleocr(pdf_prime_pagine)
        if txt_paddle is not None and not Path(txt_paddle).exists():
            txt_paddle = None
        testo_paddle = leggi_txt(txt_paddle) if txt_paddle else ""
        score_paddle = punteggio_qualita_testo(testo_paddle)

        if txt_paddle and score_paddle >= score_rapid + SCEGLI_OCR_SE_MIGLIORA_QUALITA_DI:
            return txt_paddle

        return txt_rapid or txt_paddle

    print(f"OCR_ENGINE non supportato: {OCR_ENGINE}")
    return None


def converti_pdf_in_txt(pdf_path):
    pdf_da_convertire = crea_pdf_prime_pagine(pdf_path, NUM_PAGINE_DA_USARE)
    if pdf_da_convertire is None:
        return None, "0"

    if FORZA_OCR_SEMPRE:
        txt_ocr = converti_pdf_opendataloader_ocr(pdf_da_convertire)
        return (txt_ocr, "1") if txt_ocr is not None else (None, "1")

    print(f"Conversione normale OpenDataLoader: {pdf_path.name}")
    txt_normale = converti_pdf_opendataloader_normale(pdf_da_convertire)
    testo_normale = leggi_txt(txt_normale) if txt_normale is not None else ""
    score_normale = punteggio_qualita_testo(testo_normale)

    if txt_normale is not None and testo_sufficiente_senza_ocr(testo_normale):
        return txt_normale, "0"

    if FORZA_OCR_SE_TESTO_NORMALE_SCADENTE or txt_normale is None:
        print("Testo normale assente o troppo povero: provo OCR.")
        txt_ocr = converti_pdf_opendataloader_ocr(pdf_da_convertire)
        testo_ocr = leggi_txt(txt_ocr) if txt_ocr is not None else ""
        score_ocr = punteggio_qualita_testo(testo_ocr)

        if txt_ocr is not None and (
            txt_normale is None or score_ocr >= score_normale + SCEGLI_OCR_SE_MIGLIORA_QUALITA_DI
        ):
            return txt_ocr, "1"

    if txt_normale is not None:
        return txt_normale, "0"

    if BLOCCA_CLASSIFICAZIONE_SE_OCR_FALLISCE:
        return None, "1"

    return txt_normale, "0"


def avvia_automazione():
    global MODELLO_OLLAMA_ATTIVO

    prepara_cartelle()
    MODELLO_OLLAMA_ATTIVO = scegli_modello_ollama()
    configura_file_output_per_modello(MODELLO_OLLAMA_ATTIVO)

    pdf_files = trova_pdf()
    if not pdf_files:
        print("Nessun PDF trovato nella cartella Input:")
        print(input_dir)
        return

    if MAX_PDF_DA_PROCESSARE is not None:
        limite = max(0, int(MAX_PDF_DA_PROCESSARE))
        pdf_files = pdf_files[:limite]
        print(f"Campione test attivo: elaboro {len(pdf_files)} PDF su massimo {limite}.")
    verifica_numerazione_pdf(pdf_files)
    verifica_duplicati_contenuto_pdf(pdf_files)

    mappa_pdf_id = crea_mappa_pdf_id(pdf_files)
    ordine_ids = [mappa_pdf_id[pdf_path] for pdf_path in pdf_files]
    records = carica_dataset_csv()

    print(f"Trovati {len(pdf_files)} PDF nella cartella Input.")
    print(f"Righe gia presenti/importate nel dataset: {len(records)}")
    print("Provider LLM: Ollama")
    print(f"Modello Ollama attivo: {MODELLO_OLLAMA_ATTIVO}")
    print("\nOrdine rilevato:")
    for pdf_path in pdf_files:
        print(f"- {mappa_pdf_id[pdf_path]} <= {pdf_path.name}")
    print("\nInizio elaborazione...\n")

    for i, pdf_path in enumerate(pdf_files, 1):
        id_atto = mappa_pdf_id[pdf_path]
        file_name = pdf_path.name
        metadati_pdf = crea_metadati_file(pdf_path)
        record_esistente = records.get(id_atto, {})

        forza_rielaborazione = (
            RIELABORA_TUTTI
            or id_atto in ID_ATTI_DA_RIELABORARE
            or file_name in FILE_DA_RIELABORARE
        )

        if record_esistente and record_valido(record_esistente) and not forza_rielaborazione:
            print(f"[{i}/{len(pdf_files)}] Gia valido, salto: {id_atto} ({file_name})")
            records[id_atto] = {
                **record_esistente,
                "id_atto": id_atto,
                "file_name": file_name,
                "pdf_path_locale": metadati_pdf["pdf_path_locale"],
                "pdf_link": metadati_pdf["pdf_link"],
            }
            continue

        if forza_rielaborazione:
            print(f"[{i}/{len(pdf_files)}] Rielaborazione forzata: {id_atto} ({file_name})")
        elif record_esistente:
            print(f"[{i}/{len(pdf_files)}] Rielaboro incompleto: {id_atto} ({file_name})")
        else:
            print(f"[{i}/{len(pdf_files)}] Nuovo file, elaboro: {id_atto} ({file_name})")

        txt_path, ocr_usato = converti_pdf_in_txt(pdf_path)

        record_base = {
            "id_atto": id_atto,
            "file_name": file_name,
            "ocr": normalizza_ocr_valore(ocr_usato),
            "testo_completo_llm": "",
            "golden_label": record_esistente.get("golden_label", "") if record_esistente else "",
        }

        if txt_path is None:
            record_base["errore"] = "TXT/OCR non generato"
            records[id_atto] = {
                **record_esistente,
                **record_base,
                "pdf_path_locale": metadati_pdf["pdf_path_locale"],
                "pdf_link": metadati_pdf["pdf_link"],
                "testo_llm_path_locale": "",
                "testo_llm_link": "",
            }
            salva_dataset_csv(records, ordine_ids)
            salva_dataset_jsonl(records, ordine_ids)
            append_audit_log(crea_record_audit(
                {
                    **record_base,
                    "pdf_path_locale": metadati_pdf["pdf_path_locale"],
                    "pdf_link": metadati_pdf["pdf_link"],
                    "testo_llm_path_locale": "",
                    "testo_llm_link": "",
                },
                None,
                errore_tecnico="TXT/OCR non generato",
                stats={
                    "num_chunk_llm": 0,
                    "fallback_locale": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "costo_input": 0.0,
                    "costo_output": 0.0,
                    "costo_totale": 0.0,
                    "avg_input_tokens_per_chunk": 0.0,
                    "avg_output_tokens_per_chunk": 0.0,
                    "avg_total_tokens_per_chunk": 0.0,
                }
            ))
            continue

        testo_estratto = leggi_txt(txt_path)
        if not testo_estratto.strip():
            record_base["errore"] = "TXT vuoto"
            records[id_atto] = {
                **record_esistente,
                **record_base,
                "pdf_path_locale": metadati_pdf["pdf_path_locale"],
                "pdf_link": metadati_pdf["pdf_link"],
                "testo_llm_path_locale": "",
                "testo_llm_link": "",
            }
            salva_dataset_csv(records, ordine_ids)
            salva_dataset_jsonl(records, ordine_ids)
            append_audit_log(crea_record_audit(
                {
                    **record_base,
                    "pdf_path_locale": metadati_pdf["pdf_path_locale"],
                    "pdf_link": metadati_pdf["pdf_link"],
                    "testo_llm_path_locale": "",
                    "testo_llm_link": "",
                },
                txt_path,
                errore_tecnico="TXT vuoto",
                stats={
                    "num_chunk_llm": 0,
                    "fallback_locale": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "costo_input": 0.0,
                    "costo_output": 0.0,
                    "costo_totale": 0.0,
                    "avg_input_tokens_per_chunk": 0.0,
                    "avg_output_tokens_per_chunk": 0.0,
                    "avg_total_tokens_per_chunk": 0.0,
                }
            ))
            continue

        testo_llm, errore_llm, stats = genera_testo_completo_pulito_con_ollama(testo_estratto)

        if errore_llm:
            record_base["errore"] = errore_llm
            records[id_atto] = {
                **record_esistente,
                **record_base,
                "pdf_path_locale": metadati_pdf["pdf_path_locale"],
                "pdf_link": metadati_pdf["pdf_link"],
                "testo_llm_path_locale": "",
                "testo_llm_link": "",
            }
            salva_dataset_csv(records, ordine_ids)
            salva_dataset_jsonl(records, ordine_ids)
            append_audit_log(crea_record_audit(
                {
                    **record_base,
                    "pdf_path_locale": metadati_pdf["pdf_path_locale"],
                    "pdf_link": metadati_pdf["pdf_link"],
                    "testo_llm_path_locale": "",
                    "testo_llm_link": "",
                },
                txt_path,
                errore_tecnico=errore_llm,
                stats=stats
            ))
            print(f"Errore LLM: {errore_llm}\n")
            continue

        testo_llm_path = salva_testo_llm_txt(id_atto, file_name, testo_llm)

        record_finale = {
            **record_esistente,
            "id_atto": id_atto,
            "file_name": file_name,
            "ocr": normalizza_ocr_valore(ocr_usato),
            "testo_completo_llm": testo_llm,
            "golden_label": record_esistente.get("golden_label", "") if record_esistente else "",
            "pdf_path_locale": metadati_pdf["pdf_path_locale"],
            "pdf_link": metadati_pdf["pdf_link"],
            "testo_llm_path_locale": str(testo_llm_path.resolve()),
            "testo_llm_link": crea_file_link(testo_llm_path),
            "errore": record_esistente.get("errore", "") if record_esistente else "",
        }

        records[id_atto] = record_finale
        salva_dataset_csv(records, ordine_ids)
        salva_dataset_jsonl(records, ordine_ids)
        append_audit_log(crea_record_audit(
            record_finale,
            txt_path,
            errore_tecnico="",
            stats=stats
        ))

        print(f"ID atto: {record_finale['id_atto']}")
        print(f"File name: {record_finale['file_name']}")
        print(f"OCR: {record_finale['ocr']}")
        print(f"Testo LLM TXT: {record_finale['testo_llm_path_locale']}")
        print(f"Testo completo LLM: {normalizza_testo_per_cella(record_finale['testo_completo_llm'])[:500]}...")
        print()

        if PAUSA_TRA_RICHIESTE > 0:
            time.sleep(PAUSA_TRA_RICHIESTE)

    salva_dataset_csv(records, ordine_ids)
    salva_dataset_jsonl(records, ordine_ids)
    print("\nProcesso completato.")
    print("CSV salvato in:")
    print(file_dataset_csv)
    print("JSONL salvato in:")
    print(file_dataset_jsonl)
    print("Audit log salvato in:")
    print(file_audit_csv)


if __name__ == "__main__":
    avvia_automazione()




