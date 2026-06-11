import csv
from datetime import datetime
from pathlib import Path

import pandas as pd


BASE_DATASET_DIR = Path(__file__).resolve().parent / "Output" / "Golden" / "Golden128"

AUDIT_SUMMARY_CSV = "audit_pipeline_summary.csv"
AUDIT_SECTION_START = "<!-- AUDIT_PIPELINE_START -->"
AUDIT_SECTION_END = "<!-- AUDIT_PIPELINE_END -->"
CONFUSION_SECTION_START = "<!-- CONFUSION_MATRICES_START -->"
CONFUSION_SECTION_END = "<!-- CONFUSION_MATRICES_END -->"


def scegli_da_lista(titolo, opzioni):
    print(f"\n{titolo}")
    print("=" * len(titolo))

    for indice, opzione in enumerate(opzioni, start=1):
        print(f"{indice}. {opzione}")

    while True:
        scelta = input("\nInserisci il numero scelto: ").strip()
        if scelta.isdigit():
            indice = int(scelta)
            if 1 <= indice <= len(opzioni):
                return opzioni[indice - 1]
        print(f"Scelta non valida. Inserisci un numero da 1 a {len(opzioni)}.")


def trova_txt_report(output_dir):
    output_dir = Path(output_dir)
    candidati = [
        path
        for path in sorted(output_dir.glob("risultati_training*.txt"))
        if ".tmp" not in path.name
    ]
    if candidati:
        return candidati[0]

    vecchio = output_dir / "risultati_training_modelli.txt"
    if vecchio.exists():
        return vecchio

    return None


def trova_cartelle_risultati(base_dir):
    return sorted(
        path
        for path in Path(base_dir).rglob("*")
        if path.is_dir() and trova_txt_report(path) is not None
    )


def trova_audit_per_dataset(dataset_dir):
    audit = sorted(Path(dataset_dir).glob("audit_log*.csv"))
    if not audit:
        raise FileNotFoundError(f"Nessun audit_log*.csv trovato in:\n{dataset_dir}")
    if len(audit) == 1:
        return audit[0]

    scelto = scegli_da_lista(
        f"Scegli audit CSV in {dataset_dir.name}",
        [path.name for path in audit]
    )
    return dataset_dir / scelto


def numero(valore):
    if valore is None:
        return 0.0
    testo = str(valore).strip().replace(",", ".")
    if not testo:
        return 0.0
    try:
        return float(testo)
    except ValueError:
        return 0.0


def intero(valore):
    return int(numero(valore))


def leggi_audit(path):
    return pd.read_csv(
        path,
        sep=";",
        encoding="utf-8-sig",
        engine="python",
        on_bad_lines="warn",
        dtype=str,
    )


def parse_timestamp(serie):
    valori = pd.to_datetime(serie, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    valori = valori.dropna()
    if valori.empty:
        return None, None, None
    inizio = valori.min()
    fine = valori.max()
    durata = fine - inizio
    return inizio, fine, durata


def formato_durata(delta):
    if delta is None:
        return "n/d"
    secondi = int(delta.total_seconds())
    ore, resto = divmod(secondi, 3600)
    minuti, secondi = divmod(resto, 60)
    return f"{ore:02d}:{minuti:02d}:{secondi:02d}"


def inferisci_pipeline(dataset_dir):
    nome = Path(dataset_dir).name
    if nome.endswith("_pipeline_A"):
        return "pipeline_A", "pulizia"
    if nome.endswith("_pipeline_B"):
        return "pipeline_B", "riassunto"
    return "n/d", "n/d"


def calcola_metriche_audit(df, audit_path, dataset_dir):
    pipeline, tipo_output = inferisci_pipeline(dataset_dir)

    for colonna in [
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "costo_input",
        "costo_output",
        "costo_totale",
        "num_chunk_llm",
    ]:
        if colonna not in df.columns:
            df[colonna] = "0"

    n = len(df)
    ocr_usati = df.get("ocr", pd.Series([], dtype=str)).map(intero).sum()
    fallback = df.get("fallback_locale", pd.Series([], dtype=str)).map(intero).sum()
    errore_tecnico = (
        df.get("errore_tecnico", pd.Series([""] * n, dtype=str))
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )

    input_tokens = df["input_tokens"].map(intero).sum()
    output_tokens = df["output_tokens"].map(intero).sum()
    total_tokens = df["total_tokens"].map(intero).sum()
    costo_input = df["costo_input"].map(numero).sum()
    costo_output = df["costo_output"].map(numero).sum()
    costo_totale = df["costo_totale"].map(numero).sum()
    chunk_llm = df["num_chunk_llm"].map(intero).sum()

    inizio, fine, durata = parse_timestamp(df.get("timestamp", pd.Series([], dtype=str)))
    durata_secondi = int(durata.total_seconds()) if durata is not None else 0

    modello = "n/d"
    if "modello" in df.columns:
        modelli = [m for m in df["modello"].dropna().astype(str).str.strip().unique() if m]
        modello = ", ".join(modelli) if modelli else "n/d"

    return {
        "dataset_dir": str(Path(dataset_dir).resolve()),
        "audit_csv": str(Path(audit_path).resolve()),
        "modello": modello,
        "pipeline": pipeline,
        "tipo_output": tipo_output,
        "record_audit": n,
        "ocr_usati": int(ocr_usati),
        "ocr_percento": round(ocr_usati / max(n, 1) * 100, 2),
        "fallback_locale": int(fallback),
        "fallback_percento": round(fallback / max(n, 1) * 100, 2),
        "errori_tecnici": int(errore_tecnico),
        "input_tokens_totali": int(input_tokens),
        "output_tokens_totali": int(output_tokens),
        "total_tokens_totali": int(total_tokens),
        "input_tokens_medi": round(input_tokens / max(n, 1), 2),
        "output_tokens_medi": round(output_tokens / max(n, 1), 2),
        "total_tokens_medi": round(total_tokens / max(n, 1), 2),
        "chunk_llm_totali": int(chunk_llm),
        "costo_input_totale": round(costo_input, 6),
        "costo_output_totale": round(costo_output, 6),
        "costo_totale": round(costo_totale, 6),
        "timestamp_inizio": inizio.strftime("%Y-%m-%d %H:%M:%S") if inizio is not None else "n/d",
        "timestamp_fine": fine.strftime("%Y-%m-%d %H:%M:%S") if fine is not None else "n/d",
        "durata_osservata": formato_durata(durata),
        "durata_osservata_secondi": durata_secondi,
        "secondi_medi_per_record": round(durata_secondi / max(n, 1), 2),
    }


def salva_summary_csv(metriche, path):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(metriche.keys()), delimiter=";")
        writer.writeheader()
        writer.writerow(metriche)


def trova_confusion_matrices(output_dir):
    output_dir = Path(output_dir)
    files = sorted(output_dir.glob("confusion_matrix*.csv"))

    def key(path):
        nome = path.stem
        tipo = 1 if "_cv_" in nome else 0
        return (tipo, nome)

    return sorted(files, key=key)


def nome_modello_da_confusion(path):
    stem = Path(path).stem
    if stem.startswith("confusion_matrix_cv_"):
        return stem.replace("confusion_matrix_cv_", ""), "cross-validation"
    if stem.startswith("confusion_matrix_"):
        return stem.replace("confusion_matrix_", ""), "test set"
    return stem, "n/d"


def leggi_confusion_matrix(path):
    return pd.read_csv(
        path,
        sep=";",
        encoding="utf-8-sig",
        engine="python",
        index_col=0,
    )


def sezione_confusion_matrices_txt(output_dir):
    matrices = trova_confusion_matrices(output_dir)
    righe = [
        CONFUSION_SECTION_START,
        "",
        "Confusion matrix modelli ML",
        "===========================",
        "",
        f"Generato il: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Cartella risultati: {Path(output_dir).resolve()}",
        "",
    ]

    if not matrices:
        righe.extend([
            "Nessuna confusion_matrix*.csv trovata nella cartella risultati.",
            "",
            CONFUSION_SECTION_END,
            "",
        ])
        return "\n".join(righe)

    righe.extend([
        "Le matrici senza prefisso CV sono calcolate sul test set.",
        "Le matrici con prefisso CV sono calcolate tramite cross-validation stratificata.",
        "",
    ])

    for matrix_path in matrices:
        modello, tipo = nome_modello_da_confusion(matrix_path)
        try:
            matrix = leggi_confusion_matrix(matrix_path)
            matrix_txt = matrix.to_string()
        except Exception as e:
            matrix_txt = f"Errore lettura matrice: {e}"

        righe.extend([
            f"{tipo} - {modello}",
            "-" * (len(tipo) + len(modello) + 3),
            f"File: {matrix_path.name}",
            "",
            matrix_txt,
            "",
        ])

    righe.extend([CONFUSION_SECTION_END, ""])
    return "\n".join(righe)


def righe_metriche(metriche):
    return [
        f"Generato il: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Dataset/pipeline: {metriche['dataset_dir']}",
        f"Audit CSV: {metriche['audit_csv']}",
        f"Modello: {metriche['modello']}",
        f"Pipeline: {metriche['pipeline']} ({metriche['tipo_output']})",
        "",
        "Volumi:",
        f"- Record audit: {metriche['record_audit']}",
        f"- OCR usati: {metriche['ocr_usati']} ({metriche['ocr_percento']}%)",
        f"- Fallback locale: {metriche['fallback_locale']} ({metriche['fallback_percento']}%)",
        f"- Errori tecnici: {metriche['errori_tecnici']}",
        f"- Chunk LLM totali: {metriche['chunk_llm_totali']}",
        "",
        "Token:",
        f"- Input token totali: {metriche['input_tokens_totali']}",
        f"- Output token totali: {metriche['output_tokens_totali']}",
        f"- Total token totali: {metriche['total_tokens_totali']}",
        f"- Input token medi per record: {metriche['input_tokens_medi']}",
        f"- Output token medi per record: {metriche['output_tokens_medi']}",
        f"- Total token medi per record: {metriche['total_tokens_medi']}",
        "",
        "Costi registrati:",
        f"- Costo input totale: {metriche['costo_input_totale']}",
        f"- Costo output totale: {metriche['costo_output_totale']}",
        f"- Costo totale: {metriche['costo_totale']}",
        "",
        "Timestamp:",
        f"- Inizio osservato: {metriche['timestamp_inizio']}",
        f"- Fine osservata: {metriche['timestamp_fine']}",
        f"- Durata osservata: {metriche['durata_osservata']}",
        f"- Secondi medi per record: {metriche['secondi_medi_per_record']}",
        "",
        "Nota:",
        "La durata osservata deriva dai timestamp dell'audit, quindi misura l'intervallo tra",
        "primo e ultimo record scritto. Non e' una misura precisa della singola chiamata LLM/OCR.",
    ]


def sezione_audit_txt(metriche):
    righe = [
        AUDIT_SECTION_START,
        "",
        "Audit pipeline LLM",
        "==================",
        "",
    ]
    righe.extend(righe_metriche(metriche))
    righe.extend(["", AUDIT_SECTION_END, ""])
    return "\n".join(righe)


def sostituisci_o_aggiungi_sezione(testo, start_marker, end_marker, sezione):
    if start_marker in testo and end_marker in testo:
        prima = testo.split(start_marker, 1)[0].rstrip()
        dopo = testo.split(end_marker, 1)[1].lstrip()
        return f"{prima}\n\n{sezione}{dopo}"
    return f"{testo.rstrip()}\n\n{sezione}"


def aggiorna_txt(output_dir, metriche):
    output_dir = Path(output_dir)
    report_txt = trova_txt_report(output_dir)
    if report_txt is None:
        raise FileNotFoundError(f"TXT training non trovato in:\n{output_dir}")

    testo = report_txt.read_text(encoding="utf-8")
    sezione_audit = sezione_audit_txt(metriche)
    sezione_confusion = sezione_confusion_matrices_txt(output_dir)

    nuovo = sostituisci_o_aggiungi_sezione(
        testo,
        AUDIT_SECTION_START,
        AUDIT_SECTION_END,
        sezione_audit,
    )
    nuovo = sostituisci_o_aggiungi_sezione(
        nuovo,
        CONFUSION_SECTION_START,
        CONFUSION_SECTION_END,
        sezione_confusion,
    )

    report_txt.write_text(nuovo, encoding="utf-8")
    return report_txt


def main():
    cartelle_risultati = trova_cartelle_risultati(BASE_DATASET_DIR)
    if not cartelle_risultati:
        raise FileNotFoundError(f"Nessun TXT risultati training trovato in:\n{BASE_DATASET_DIR}")

    scelta = scegli_da_lista(
        "Scegli la cartella risultati da aggiornare",
        [str(path.relative_to(BASE_DATASET_DIR)) for path in cartelle_risultati],
    )

    output_dir = BASE_DATASET_DIR / scelta
    dataset_dir = output_dir.parent
    audit_path = trova_audit_per_dataset(dataset_dir)

    df = leggi_audit(audit_path)
    metriche = calcola_metriche_audit(df, audit_path, dataset_dir)

    summary_csv = output_dir / AUDIT_SUMMARY_CSV
    salva_summary_csv(metriche, summary_csv)
    txt_path = aggiorna_txt(output_dir, metriche)

    print("\nReport aggiornati:")
    print(summary_csv)
    print(txt_path)


if __name__ == "__main__":
    main()
