from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd


DEFAULT_SOURCE_RESULTS = (
    Path("Output")
    / "Golden"
    / "Golden128"
    / "gemma3_4b_pipeline_A"
    / "risultati_training_gemma3_4b_A"
)
DEFAULT_COMPARE_RESULTS = (
    Path("Output")
    / "Golden"
    / "Golden128"
    / "gemma3_4b_pipeline_B"
    / "risultati_training_gemma3_4b_B"
)
DEFAULT_OUTPUT_DIR = Path("Output") / "Golden" / "Golden128" / Path(__file__).stem
DEFAULT_INPUT_DIR = Path("Input")
DEFAULT_PER_CLASS = 5
DEFAULT_RANDOM_STATE = 2026

ID_COL = "id_atto"
FILE_COL = "file_name"
LABEL_COL = "golden_label"


def leggi_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str).fillna("")


def normalizza_id_serie(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.strip().str.extract(r"(\d+)", expand=False).fillna("")


def id_sort_key(valore: str) -> tuple[int, int | str]:
    valore = str(valore).strip()
    return (0, int(valore)) if valore.isdigit() else (1, valore)


def carica_training_e_test(source_results: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    training_path = source_results / "training_set.csv"
    test_path = source_results / "test_set.csv"

    if not training_path.exists():
        raise FileNotFoundError(f"Training set non trovato: {training_path}")

    if not test_path.exists():
        raise FileNotFoundError(f"Test set non trovato: {test_path}")

    training = leggi_csv(training_path)
    test = leggi_csv(test_path)

    colonne_mancanti = [
        colonna
        for colonna in [ID_COL, FILE_COL, LABEL_COL]
        if colonna not in training.columns
    ]
    if colonne_mancanti:
        raise ValueError(
            "Nel training set mancano colonne richieste: "
            + ", ".join(colonne_mancanti)
        )

    return training, test


def seleziona_diagnostico(
    training: pd.DataFrame,
    test: pd.DataFrame,
    per_class: int,
    random_state: int,
) -> pd.DataFrame:
    training = training.copy()
    test = test.copy()

    training[ID_COL] = normalizza_id_serie(training[ID_COL])
    training[LABEL_COL] = training[LABEL_COL].astype(str).str.strip()
    test[ID_COL] = normalizza_id_serie(test[ID_COL])

    test_ids = set(test[ID_COL])
    overlap = set(training[ID_COL]) & test_ids
    if overlap:
        raise ValueError(
            "Il training set contiene ID presenti nel test set: "
            + ", ".join(sorted(overlap, key=lambda x: int(x) if x.isdigit() else x))
        )

    selezionati = []
    conteggi = training[LABEL_COL].value_counts().sort_index()
    classi_insufficienti = conteggi[conteggi < per_class]

    if not classi_insufficienti.empty:
        dettagli = ", ".join(
            f"{label}={count}" for label, count in classi_insufficienti.items()
        )
        raise ValueError(
            f"Non ci sono almeno {per_class} atti per classe nel training set: {dettagli}"
        )

    for label in sorted(training[LABEL_COL].unique()):
        gruppo = training[training[LABEL_COL] == label]
        campione = gruppo.sample(n=per_class, random_state=random_state)
        selezionati.append(campione)

    diagnostico = pd.concat(selezionati, ignore_index=True)
    diagnostico["_id_sort"] = diagnostico[ID_COL].map(
        lambda valore: int(valore) if str(valore).isdigit() else str(valore)
    )
    diagnostico = diagnostico.sort_values([LABEL_COL, "_id_sort"]).drop(
        columns=["_id_sort"]
    )
    return diagnostico.reset_index(drop=True)


def copia_pdf(diagnostico: pd.DataFrame, input_dir: Path, pdf_output_dir: Path) -> list[str]:
    pdf_output_dir.mkdir(parents=True, exist_ok=True)

    mancanti = []
    for _, row in diagnostico.iterrows():
        file_name = str(row[FILE_COL]).strip()
        if not file_name:
            mancanti.append(f"{row[ID_COL]}: file_name vuoto")
            continue

        sorgente = input_dir / file_name
        if not sorgente.exists():
            mancanti.append(f"{row[ID_COL]}: {sorgente}")
            continue

        destinazione = pdf_output_dir / file_name
        shutil.copy2(sorgente, destinazione)

    return mancanti


def salva_output(
    training: pd.DataFrame,
    diagnostico: pd.DataFrame,
    test: pd.DataFrame,
    output_dir: Path,
    source_results: Path,
    compare_results: Path | None,
    input_dir: Path,
    per_class: int,
    random_state: int,
    missing_pdfs: list[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    diagnostico_path = output_dir / "diagnostic_set.csv"
    training_copy_path = output_dir / "training_set_gemma3_4b.csv"
    ids_path = output_dir / "diagnostic_ids.txt"
    summary_path = output_dir / "diagnostic_summary.csv"
    split_membership_path = output_dir / "split_membership_gemma3_4b.csv"
    split_audit_path = output_dir / "split_audit_gemma3_4b.csv"
    test_copy_path = output_dir / "test_set_escluso.csv"
    manifest_path = output_dir / "manifest_diagnostico_40.json"

    training.to_csv(training_copy_path, index=False, sep=";", encoding="utf-8-sig")
    diagnostico.to_csv(diagnostico_path, index=False, sep=";", encoding="utf-8-sig")
    test.to_csv(test_copy_path, index=False, sep=";", encoding="utf-8-sig")

    ids = normalizza_id_serie(diagnostico[ID_COL]).tolist()
    ids_path.write_text("\n".join(ids) + "\n", encoding="utf-8")

    training_ids = set(normalizza_id_serie(training[ID_COL]))
    test_ids = set(normalizza_id_serie(test[ID_COL]))
    diagnostic_ids = set(ids)
    diagnostic_subset_train = diagnostic_ids <= training_ids
    diagnostic_overlap_test = sorted(diagnostic_ids & test_ids, key=id_sort_key)

    membership = pd.concat(
        [
            training.assign(split="training"),
            test.assign(split="test"),
            diagnostico.assign(split="diagnostic"),
        ],
        ignore_index=True,
    )
    membership["_id_norm"] = normalizza_id_serie(membership[ID_COL])
    membership["_id_sort"] = membership["_id_norm"].map(id_sort_key)
    membership["in_training_set"] = membership["_id_norm"].isin(training_ids)
    membership["in_test_set"] = membership["_id_norm"].isin(test_ids)
    membership["in_diagnostic_set"] = membership["_id_norm"].isin(diagnostic_ids)
    membership = membership.sort_values(["_id_sort", "split"]).drop(columns=["_id_sort"])
    membership.to_csv(
        split_membership_path,
        index=False,
        sep=";",
        encoding="utf-8-sig",
    )

    compare_train_same = ""
    compare_test_same = ""
    compare_train_size = ""
    compare_test_size = ""
    if compare_results is not None and compare_results.exists():
        compare_training_path = compare_results / "training_set.csv"
        compare_test_path = compare_results / "test_set.csv"
        if compare_training_path.exists() and compare_test_path.exists():
            compare_training = leggi_csv(compare_training_path)
            compare_test = leggi_csv(compare_test_path)
            compare_train_ids = set(normalizza_id_serie(compare_training[ID_COL]))
            compare_test_ids = set(normalizza_id_serie(compare_test[ID_COL]))
            compare_train_same = training_ids == compare_train_ids
            compare_test_same = test_ids == compare_test_ids
            compare_train_size = len(compare_train_ids)
            compare_test_size = len(compare_test_ids)

    split_audit = pd.DataFrame(
        [
            {
                "modello_llm": "gemma3:4b",
                "source_results": str(source_results.resolve()),
                "compare_results": str(compare_results.resolve()) if compare_results else "",
                "training_rows": len(training),
                "test_rows": len(test),
                "diagnostic_rows": len(diagnostico),
                "training_classes": training[LABEL_COL].nunique(),
                "test_classes": test[LABEL_COL].nunique(),
                "diagnostic_classes": diagnostico[LABEL_COL].nunique(),
                "diagnostic_subset_train": diagnostic_subset_train,
                "diagnostic_overlap_test_count": len(diagnostic_overlap_test),
                "diagnostic_overlap_test_ids": ", ".join(diagnostic_overlap_test),
                "compare_train_same": compare_train_same,
                "compare_test_same": compare_test_same,
                "compare_train_size": compare_train_size,
                "compare_test_size": compare_test_size,
                "per_class_diagnostic": per_class,
                "random_state_diagnostic": random_state,
            }
        ]
    )
    split_audit.to_csv(split_audit_path, index=False, sep=";", encoding="utf-8-sig")

    summary = (
        diagnostico.groupby(LABEL_COL, as_index=False)
        .agg(numero_atti=(ID_COL, "count"), id_atti=(ID_COL, lambda valori: ", ".join(valori)))
        .sort_values(LABEL_COL)
    )
    summary.to_csv(summary_path, index=False, sep=";", encoding="utf-8-sig")

    manifest = {
        "generato_il": datetime.now().isoformat(timespec="seconds"),
        "modello_llm_riferimento": "gemma3:4b",
        "source_results": str(source_results.resolve()),
        "compare_results": str(compare_results.resolve()) if compare_results else "",
        "input_dir": str(input_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "per_class": per_class,
        "random_state": random_state,
        "training_rows": int(len(training)),
        "test_rows": int(len(test)),
        "totale_atti": int(len(diagnostico)),
        "classi": sorted(diagnostico[LABEL_COL].unique().tolist()),
        "diagnostic_ids": ids,
        "training_set_ids": sorted(training_ids, key=id_sort_key),
        "test_set_escluso_ids": sorted(test_ids, key=id_sort_key),
        "diagnostic_subset_train": diagnostic_subset_train,
        "diagnostic_overlap_test_ids": diagnostic_overlap_test,
        "compare_train_same": compare_train_same,
        "compare_test_same": compare_test_same,
        "missing_pdfs": missing_pdfs,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nDataset diagnostico creato.")
    print(f"Cartella output: {output_dir}")
    print(f"Training set gemma3:4b: {training_copy_path}")
    print(f"CSV diagnostico: {diagnostico_path}")
    print(f"ID selezionati: {ids_path}")
    print(f"Riepilogo classi: {summary_path}")
    print(f"Membership split: {split_membership_path}")
    print(f"Audit split: {split_audit_path}")
    print(f"Test set escluso: {test_copy_path}")
    print(f"Manifest: {manifest_path}")

    if missing_pdfs:
        print("\nATTENZIONE: alcuni PDF non sono stati copiati:")
        for item in missing_pdfs:
            print(f"- {item}")
    else:
        print(f"PDF copiati in: {output_dir / 'Input_diagnostico_40'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Crea un campione diagnostico bilanciato dal training set ufficiale, "
            "escludendo il test set."
        )
    )
    parser.add_argument(
        "--source-results",
        type=Path,
        default=DEFAULT_SOURCE_RESULTS,
        help="Cartella che contiene training_set.csv e test_set.csv.",
    )
    parser.add_argument(
        "--compare-results",
        type=Path,
        default=DEFAULT_COMPARE_RESULTS,
        help="Cartella opzionale con split da confrontare, di default gemma3:4b pipeline B.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Cartella dei PDF originali.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Cartella in cui salvare il dataset diagnostico.",
    )
    parser.add_argument(
        "--per-class",
        type=int,
        default=DEFAULT_PER_CLASS,
        help="Numero di atti da selezionare per ogni classe.",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=DEFAULT_RANDOM_STATE,
        help="Seed per rendere riproducibile la selezione.",
    )
    parser.add_argument(
        "--no-copy-pdf",
        action="store_true",
        help="Non copia i PDF selezionati nella cartella diagnostica.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent

    source_results = (base_dir / args.source_results).resolve()
    compare_results = (base_dir / args.compare_results).resolve() if args.compare_results else None
    input_dir = (base_dir / args.input_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()

    training, test = carica_training_e_test(source_results)
    diagnostico = seleziona_diagnostico(
        training=training,
        test=test,
        per_class=args.per_class,
        random_state=args.random_state,
    )

    missing_pdfs = []
    if not args.no_copy_pdf:
        missing_pdfs = copia_pdf(
            diagnostico=diagnostico,
            input_dir=input_dir,
            pdf_output_dir=output_dir / "Input_diagnostico_40",
        )

    salva_output(
        training=training,
        diagnostico=diagnostico,
        test=test,
        output_dir=output_dir,
        source_results=source_results,
        compare_results=compare_results,
        input_dir=input_dir,
        per_class=args.per_class,
        random_state=args.random_state,
        missing_pdfs=missing_pdfs,
    )


if __name__ == "__main__":
    main()
